import json
from pathlib import Path

from bid_assistant.config import Settings
from bid_assistant.ocr import MinerUClient


def test_content_list_includes_table_body_and_skips_page_number(tmp_path: Path) -> None:
    source = tmp_path / "sample_content_list.json"
    source.write_text(
        json.dumps(
            [
                {"type": "text", "text": "投标人须知", "page_idx": 0},
                {
                    "type": "table",
                    "table_body": (
                        "<table><tr><th>条款号</th><th>编列内容</th></tr>"
                        "<tr><td>1.3.4</td><td>无安全事故</td></tr></table>"
                    ),
                    "page_idx": 0,
                },
                {"type": "page_number", "text": "8", "page_idx": 0},
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    pages = MinerUClient._pages_from_content_list(source)

    assert len(pages) == 1
    assert "投标人须知" in pages[0].text
    assert "条款号 | 编列内容" in pages[0].text
    assert "1.3.4 | 无安全事故" in pages[0].text
    assert pages[0].text.strip() != "8"


def test_mineru_prefers_python_module_entrypoint(tmp_path: Path) -> None:
    python_path = tmp_path / "python.exe"
    python_path.write_bytes(b"")
    client = MinerUClient(Settings(mineru_python=str(python_path), mineru_cli="missing-mineru"))

    assert client._command_prefix() == [
        str(python_path.resolve()),
        "-m",
        "mineru.cli.client",
    ]
