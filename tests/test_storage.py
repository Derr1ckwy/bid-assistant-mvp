import io
import json
import sqlite3
import zipfile
from pathlib import Path

import pytest

from bid_assistant.storage import MAX_DRAFT_VERSIONS, ProjectArchiveError, ProjectStore, safe_filename


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


def test_existing_database_is_migrated_for_archiving(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    with sqlite3.connect(data_dir / "app.db") as conn:
        conn.execute(
            """
            CREATE TABLE projects (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'new',
                source_filename TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "INSERT INTO projects VALUES ('proj_old', '旧项目', 'new', NULL, '2026-01-01', '2026-01-01')"
        )

    store = ProjectStore(data_dir)

    assert store.get_project("proj_old")["archived"] == 0


def test_archive_and_restore_project(tmp_path: Path) -> None:
    store = ProjectStore(tmp_path / "data")
    project = store.create_project("归档测试")

    store.set_project_archived(project["id"], True)
    assert store.list_projects() == []
    assert store.list_projects(include_archived=True)[0]["archived"] == 1

    store.set_project_archived(project["id"], False)
    assert store.list_projects()[0]["id"] == project["id"]


def test_duplicate_project_copies_business_data_without_outputs_or_versions(tmp_path: Path) -> None:
    store = ProjectStore(tmp_path / "data")
    project = store.create_project("原项目")
    project_id = project["id"]
    store.save_source(project_id, "招标文件.txt", b"tender")
    store.save_json(project_id, "analysis", {"ok": True})
    store.save_json(project_id, "drafts", [{"title": "第一版"}])
    store.save_json(project_id, "review", {"issues": []})
    store.save_knowledge_file(project_id, "company", "企业资料.txt", b"company")
    store.save_draft_version(project_id, [{"title": "第一版"}], "首次保存")
    store.output_path(project_id, "旧初稿.docx").write_bytes(b"docx")
    store.update_project(project_id, status="exported")

    duplicate = store.duplicate_project(project_id)

    assert duplicate["id"] != project_id
    assert duplicate["name"] == "原项目（副本）"
    assert duplicate["status"] == "review_generated"
    assert duplicate["archived"] == 0
    assert store.load_json(duplicate["id"], "analysis") == {"ok": True}
    assert store.load_json(duplicate["id"], "drafts") == [{"title": "第一版"}]
    assert store.list_knowledge_files(duplicate["id"])["company"][0].read_bytes() == b"company"
    assert not (store.project_dir(duplicate["id"]) / "output").exists()
    assert store.list_draft_versions(duplicate["id"]) == []


def test_draft_versions_can_be_listed_and_restored(tmp_path: Path) -> None:
    store = ProjectStore(tmp_path / "data")
    project = store.create_project("版本测试")
    project_id = project["id"]
    first_drafts = [{"chapter_id": "a", "title": "第一章", "markdown": "第一版"}]
    second_drafts = [{"chapter_id": "a", "title": "第一章", "markdown": "第二版"}]

    store.save_json(project_id, "drafts", first_drafts)
    first_version = store.save_draft_version(project_id, first_drafts, "首次生成")
    store.save_json(project_id, "drafts", second_drafts)
    store.save_draft_version(project_id, second_drafts, "人工修改")
    store.save_json(project_id, "review", {"issues": [{"message": "旧复核"}]})

    versions = store.list_draft_versions(project_id)
    restored = store.restore_draft_version(project_id, first_version["id"])

    assert len(versions) == 2
    assert versions[0]["reason"] == "人工修改"
    assert restored == first_drafts
    assert store.load_json(project_id, "drafts") == first_drafts
    assert store.load_json(project_id, "review") is None
    assert len(store.list_draft_versions(project_id)) == 3
    assert any(item["reason"] == "恢复版本前自动快照" for item in store.list_draft_versions(project_id))

    with pytest.raises(ValueError):
        store.load_draft_version(project_id, "../bad")


def test_draft_version_retention_keeps_latest_versions(tmp_path: Path) -> None:
    store = ProjectStore(tmp_path / "data")
    project = store.create_project("版本上限测试")
    drafts = [{"chapter_id": "a", "title": "第一章", "markdown": "正文"}]

    for index in range(MAX_DRAFT_VERSIONS + 2):
        store.save_draft_version(project["id"], drafts, f"版本 {index}")

    versions = store.list_draft_versions(project["id"])

    assert len(versions) == MAX_DRAFT_VERSIONS
    assert versions[0]["reason"] == f"版本 {MAX_DRAFT_VERSIONS + 1}"
    assert all(item["reason"] != "版本 0" for item in versions)


def test_project_progress_uses_persisted_artifacts(tmp_path: Path) -> None:
    store = ProjectStore(tmp_path / "data")
    project = store.create_project("进度测试")
    project_id = project["id"]

    store.save_source(project_id, "招标文件.txt", b"tender")
    store.save_json(project_id, "parsed", {"full_text": "tender"})
    store.save_json(project_id, "analysis", {"project_info": {}})
    store.save_json(project_id, "drafts", [{"title": "draft"}])
    store.save_knowledge_file(project_id, "company", "企业资料.txt", b"company")

    progress = store.project_progress(project_id)

    assert progress["completed"] == 4
    assert progress["percent"] == 67
    assert progress["knowledge_files"] == 1


def test_project_archive_round_trip_and_id_conflict(tmp_path: Path) -> None:
    source_store = ProjectStore(tmp_path / "source")
    source = source_store.create_project("迁移项目")
    source_store.save_source(source["id"], "招标文件.txt", "招标内容".encode())
    source_store.save_json(source["id"], "analysis", {"ok": True})
    source_store.save_knowledge_file(source["id"], "company", "企业.txt", b"company")
    source_store.output_path(source["id"], "初稿.docx").write_bytes(b"docx")
    backup = source_store.export_project_archive(source["id"])

    target_store = ProjectStore(tmp_path / "target")
    imported = target_store.import_project_archive(backup)

    assert imported["id"] == source["id"]
    assert imported["name"] == "迁移项目"
    assert imported["archived"] == 0
    assert target_store.load_json(imported["id"], "analysis") == {"ok": True}
    assert target_store.source_path(imported["id"]).read_text(encoding="utf-8") == "招标内容"
    assert target_store.output_path(imported["id"], "初稿.docx").read_bytes() == b"docx"

    duplicate = target_store.import_project_archive(backup)
    assert duplicate["id"] != source["id"]
    assert duplicate["name"] == "迁移项目"


def test_project_archive_rejects_path_traversal(tmp_path: Path) -> None:
    manifest = {
        "format": "bid-assistant-project",
        "version": 1,
        "project": {"id": "proj_bad", "name": "坏备份"},
        "files": [],
    }
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("manifest.json", json.dumps(manifest))
        archive.writestr("project/../../outside.txt", "bad")

    store = ProjectStore(tmp_path / "data")
    with pytest.raises(ProjectArchiveError, match="不安全路径"):
        store.import_project_archive(buffer.getvalue())
    assert not (tmp_path / "outside.txt").exists()


@pytest.mark.parametrize("unsafe_path", ["project/C:/secret.txt", "project/CON.txt", "project/bad:name.txt"])
def test_project_archive_rejects_windows_unsafe_paths(tmp_path: Path, unsafe_path: str) -> None:
    manifest = {
        "format": "bid-assistant-project",
        "version": 1,
        "project": {"id": "proj_bad", "name": "坏备份"},
        "files": [],
    }
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("manifest.json", json.dumps(manifest))
        archive.writestr(unsafe_path, "bad")

    store = ProjectStore(tmp_path / "data")
    with pytest.raises(ProjectArchiveError, match="不安全路径"):
        store.import_project_archive(buffer.getvalue())


def test_project_archive_rejects_tampered_file(tmp_path: Path) -> None:
    manifest = {
        "format": "bid-assistant-project",
        "version": 1,
        "project": {"id": "proj_bad", "name": "坏备份"},
        "files": [{"path": "analysis.json", "size": 2, "sha256": "0" * 64}],
    }
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("manifest.json", json.dumps(manifest))
        archive.writestr("project/analysis.json", "{}")

    store = ProjectStore(tmp_path / "data")
    with pytest.raises(ProjectArchiveError, match="完整性校验失败"):
        store.import_project_archive(buffer.getvalue())
