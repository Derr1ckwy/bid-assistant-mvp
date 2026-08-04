from pathlib import Path

from bid_assistant.knowledge import search_knowledge


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
