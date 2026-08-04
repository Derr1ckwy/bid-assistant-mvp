from pathlib import Path

import pytest

from bid_assistant.storage import ProjectStore, safe_filename


def test_project_store_round_trip(tmp_path: Path) -> None:
    store = ProjectStore(tmp_path / "data")
    project = store.create_project("测试项目")

    source = store.save_source(project["id"], "招标?.txt", "内容".encode("utf-8"))
    store.save_json(project["id"], "analysis", {"ok": True})
    knowledge = store.save_knowledge_file(project["id"], "company", "企业资料.txt", b"company")

    assert source.exists()
    assert source.name == "招标_.txt"
    assert store.source_path(project["id"]) == source
    assert store.load_json(project["id"], "analysis") == {"ok": True}
    assert knowledge in store.list_knowledge_files(project["id"])["company"]
    assert store.get_project(project["id"])["status"] == "knowledge_ready"


def test_store_rejects_invalid_identifiers(tmp_path: Path) -> None:
    store = ProjectStore(tmp_path / "data")

    with pytest.raises(ValueError):
        store.project_dir("../escape")
    with pytest.raises(ValueError):
        store.save_knowledge_file("project", "unknown", "a.txt", b"x")


def test_safe_filename_removes_path_and_windows_characters() -> None:
    assert safe_filename("..\\folder\\bad?.txt") == "bad_.txt"
    assert safe_filename("../folder/bad?.txt") == "bad_.txt"
