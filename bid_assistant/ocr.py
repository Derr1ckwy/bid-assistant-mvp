from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory

from bid_assistant.config import Settings
from bid_assistant.models import ParsedDocument, ParsedPage


class MinerUError(RuntimeError):
    pass


class MinerUClient:
    def __init__(self, settings: Settings):
        self.cli = settings.mineru_cli
        self.backend = settings.mineru_backend
        self.timeout = settings.mineru_timeout_seconds

    def executable(self) -> str | None:
        configured = Path(self.cli)
        if configured.is_file():
            return str(configured.resolve())
        return shutil.which(self.cli)

    def is_available(self) -> bool:
        return self.executable() is not None

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
            text = item.get("text") or item.get("content") or item.get("markdown") or ""
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
        executable = self.executable()
        if executable is None:
            raise MinerUError("未检测到 MinerU。请运行 setup_mineru.ps1 完成独立环境安装。")
        with TemporaryDirectory(prefix="bid-assistant-mineru-") as temporary_dir:
            output_dir = Path(temporary_dir) / "output"
            command = [executable, "-p", str(source_path.resolve()), "-o", str(output_dir)]
            if self.backend:
                command.extend(["-b", self.backend])
            try:
                completed = subprocess.run(
                    command,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=self.timeout,
                    check=False,
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
