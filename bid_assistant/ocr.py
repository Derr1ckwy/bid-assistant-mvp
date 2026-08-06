from __future__ import annotations

import html
import json
import os
import shutil
import subprocess
from html.parser import HTMLParser
from pathlib import Path
from tempfile import TemporaryDirectory

from bid_assistant.config import PROJECT_ROOT, Settings
from bid_assistant.models import ParsedDocument, ParsedPage


class MinerUError(RuntimeError):
    pass


class _TableTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        value = " ".join(data.split())
        if value:
            self.parts.append(value)

    def handle_endtag(self, tag: str) -> None:
        if tag in {"td", "th"}:
            self.parts.append(" | ")
        elif tag == "tr":
            self.parts.append("\n")

    def get_text(self) -> str:
        lines = [line.strip(" |") for line in "".join(self.parts).splitlines()]
        return "\n".join(line for line in lines if line).strip()


def _table_html_to_text(value: str) -> str:
    parser = _TableTextParser()
    try:
        parser.feed(value)
        parser.close()
    except (ValueError, TypeError):
        return html.unescape(value).strip()
    return html.unescape(parser.get_text())


class MinerUClient:
    def __init__(self, settings: Settings):
        self.cli = settings.mineru_cli
        self.python = settings.mineru_python
        self.backend = settings.mineru_backend
        self.method = settings.mineru_method
        self.language = settings.mineru_language
        self.timeout = settings.mineru_timeout_seconds

    def _command_prefix(self) -> list[str] | None:
        configured_python = Path(self.python) if self.python else None
        if configured_python and configured_python.is_file():
            return [os.path.abspath(str(configured_python)), "-m", "mineru.cli.client"]
        configured = Path(self.cli)
        if configured.is_file():
            return [str(configured.resolve())]
        executable = shutil.which(self.cli)
        return [executable] if executable else None

    def is_available(self) -> bool:
        return self._command_prefix() is not None

    @staticmethod
    def _pages_from_content_list(path: Path) -> list[ParsedPage]:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise MinerUError(f"MinerU 结果无法读取：{exc}") from exc
        if isinstance(payload, dict):
            payload = payload.get("content_list") or payload.get("content") or []
        if not isinstance(payload, list):
            return []
        by_page: dict[int | None, list[str]] = {}
        for item in payload:
            if not isinstance(item, dict):
                continue
            if item.get("type") in {"page_number", "header", "footer"}:
                continue
            text = item.get("text") or item.get("content") or item.get("markdown") or ""
            if not text and isinstance(item.get("table_body"), str):
                text = _table_html_to_text(item["table_body"])
            if not isinstance(text, str) or not text.strip():
                continue
            raw_page = item.get("page_idx", item.get("page_index", item.get("page_no")))
            try:
                page_number = int(raw_page) + 1 if raw_page is not None else None
            except (TypeError, ValueError):
                page_number = None
            by_page.setdefault(page_number, []).append(text.strip())
        return [
            ParsedPage(page_number=page_number, text="\n\n".join(blocks))
            for page_number, blocks in sorted(by_page.items(), key=lambda pair: pair[0] or 0)
        ]

    def parse(self, source: str | Path) -> ParsedDocument:
        source_path = Path(source)
        command_prefix = self._command_prefix()
        if command_prefix is None:
            raise MinerUError("未检测到 MinerU。请运行 setup_mineru.ps1 完成独立环境安装。")
        with TemporaryDirectory(prefix="bid-assistant-mineru-") as temporary_dir:
            output_dir = Path(temporary_dir) / "output"
            command = [*command_prefix, "-p", str(source_path.resolve()), "-o", str(output_dir)]
            if self.backend:
                command.extend(["-b", self.backend])
            if self.method:
                command.extend(["-m", self.method])
            if self.language:
                command.extend(["-l", self.language])
            environment = os.environ.copy()
            model_root = PROJECT_ROOT / ".mineru-models"
            model_config = model_root / "mineru.json"
            if model_config.is_file():
                environment.setdefault("MINERU_TOOLS_CONFIG_JSON", str(model_config.resolve()))
                environment.setdefault("MINERU_MODEL_SOURCE", "modelscope")
                environment.setdefault("MODELSCOPE_CACHE", str((model_root / "modelscope").resolve()))
            try:
                completed = subprocess.run(
                    command,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=self.timeout,
                    check=False,
                    env=environment,
                )
            except subprocess.TimeoutExpired as exc:
                raise MinerUError(f"MinerU 解析超过 {self.timeout} 秒，已停止本次增强解析。") from exc
            except OSError as exc:
                raise MinerUError(f"MinerU 无法启动：{exc}") from exc
            if completed.returncode != 0:
                detail = (completed.stderr or completed.stdout).strip()[-600:]
                raise MinerUError(f"MinerU 解析失败：{detail or '未返回错误详情'}")

            content_lists = sorted(output_dir.rglob("*_content_list.json"))
            pages = self._pages_from_content_list(content_lists[0]) if content_lists else []
            if not pages:
                markdown_files = sorted(
                    output_dir.rglob("*.md"),
                    key=lambda path: path.stat().st_size,
                    reverse=True,
                )
                if markdown_files:
                    text = markdown_files[0].read_text(encoding="utf-8", errors="replace").strip()
                    pages = [ParsedPage(page_number=None, text=text)] if text else []
            if not pages:
                raise MinerUError("MinerU 已运行，但没有生成可用文本。")
            full_text = "\n\n".join(
                f"[第 {page.page_number} 页]\n{page.text}" if page.page_number else page.text
                for page in pages
                if page.text
            ).strip()
            return ParsedDocument(
                filename=source_path.name,
                file_type=source_path.suffix.lower().lstrip("."),
                pages=pages,
                full_text=full_text,
                char_count=sum(len(page.text) for page in pages),
                possible_scanned_document=False,
                parser_engine="mineru",
                warnings=[],
            )
