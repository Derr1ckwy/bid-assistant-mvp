from pathlib import Path

import bid_assistant.knowledge as knowledge
from bid_assistant.knowledge import clear_knowledge_cache, search_knowledge


def test_search_knowledge_returns_relevant_source(tmp_path: Path) -> None:
    company = tmp_path / "company.txt"
    product = tmp_path / "product.txt"
    company.write_text("公司建立项目质量检查制度和实施保障流程。", encoding="utf-8")
    product.write_text("产品包含资产管理和工单管理功能。", encoding="utf-8")

    results = search_knowledge(
        "项目实施质量保障",
        {"company": [company], "product": [product], "history": []},
        top_k=3,
    )

    assert results
    assert results[0].source_file == "company.txt"
    assert results[0].score > 0


def test_search_knowledge_caches_unchanged_files(tmp_path: Path, monkeypatch) -> None:
    clear_knowledge_cache()
    source = tmp_path / "company.txt"
    source.write_text("项目实施质量保障流程。", encoding="utf-8")
    real_parse = knowledge.parse_document
    calls = 0

    def counted_parse(path):
        nonlocal calls
        calls += 1
        return real_parse(path)

    monkeypatch.setattr(knowledge, "parse_document", counted_parse)
    files = {"company": [source], "product": [], "history": []}

    search_knowledge("实施保障", files)
    search_knowledge("质量保障", files)
    assert calls == 1

    source.write_text("项目实施质量保障流程和验收制度。", encoding="utf-8")
    search_knowledge("验收制度", files)
    assert calls == 2
