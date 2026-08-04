import io
import json
import sqlite3
import zipfile
from pathlib import Path

import pytest

from bid_assistant.storage import ProjectArchiveError, ProjectStore, safe_filename


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
