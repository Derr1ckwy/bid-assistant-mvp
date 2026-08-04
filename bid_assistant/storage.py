from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def safe_filename(value: str) -> str:
    name = Path(value or "file").name
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .")
    return name[:180] or "file"


class ProjectStore:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.projects_dir = self.root / "projects"
        self.projects_dir.mkdir(exist_ok=True)
        self.db_path = self.root / "app.db"
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS projects (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'new',
                    source_filename TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )

    def project_dir(self, project_id: str) -> Path:
        if not re.fullmatch(r"[a-zA-Z0-9_-]+", project_id):
            raise ValueError("Invalid project id")
        path = self.projects_dir / project_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def create_project(self, name: str) -> dict:
        project_id = f"proj_{uuid4().hex[:12]}"
        timestamp = _now()
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO projects (id, name, status, created_at, updated_at) VALUES (?, ?, 'new', ?, ?)",
                (project_id, name.strip() or "未命名投标项目", timestamp, timestamp),
            )
        self.project_dir(project_id)
        return self.get_project(project_id)

    def list_projects(self) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM projects ORDER BY updated_at DESC").fetchall()
        return [dict(row) for row in rows]

    def get_project(self, project_id: str) -> dict:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
        if not row:
            raise KeyError(f"Project not found: {project_id}")
        return dict(row)

    def update_project(self, project_id: str, **fields) -> None:
        allowed = {"name", "status", "source_filename"}
        updates = {key: value for key, value in fields.items() if key in allowed}
        if not updates:
            return
        updates["updated_at"] = _now()
        assignments = ", ".join(f"{key}=?" for key in updates)
        with self._connect() as conn:
            conn.execute(
                f"UPDATE projects SET {assignments} WHERE id=?",
                (*updates.values(), project_id),
            )

    def save_source(self, project_id: str, filename: str, content: bytes) -> Path:
        source_dir = self.project_dir(project_id) / "source"
        source_dir.mkdir(exist_ok=True)
        target = source_dir / safe_filename(filename)
        target.write_bytes(content)
        self.update_project(project_id, source_filename=target.name, status="uploaded")
        return target

    def source_path(self, project_id: str) -> Path | None:
        project = self.get_project(project_id)
        filename = project.get("source_filename")
        if not filename:
            return None
        path = self.project_dir(project_id) / "source" / filename
        return path if path.exists() else None

    def save_json(self, project_id: str, name: str, value) -> Path:
        if not re.fullmatch(r"[a-zA-Z0-9_-]+", name):
            raise ValueError("Invalid JSON document name")
        target = self.project_dir(project_id) / f"{name}.json"
        temporary = target.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(target)
        return target

    def load_json(self, project_id: str, name: str, default=None):
        target = self.project_dir(project_id) / f"{name}.json"
        if not target.exists():
            return default
        return json.loads(target.read_text(encoding="utf-8"))

    def save_knowledge_file(self, project_id: str, category: str, filename: str, content: bytes) -> Path:
        if category not in {"company", "product", "history"}:
            raise ValueError("Invalid knowledge category")
        target_dir = self.project_dir(project_id) / "knowledge" / category
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / safe_filename(filename)
        target.write_bytes(content)
        self.update_project(project_id, status="knowledge_ready")
        return target

    def list_knowledge_files(self, project_id: str) -> dict[str, list[Path]]:
        result: dict[str, list[Path]] = {"company": [], "product": [], "history": []}
        base = self.project_dir(project_id) / "knowledge"
        for category in result:
            category_dir = base / category
            if category_dir.exists():
                result[category] = sorted(path for path in category_dir.iterdir() if path.is_file())
        return result

    def output_path(self, project_id: str, filename: str) -> Path:
        output_dir = self.project_dir(project_id) / "output"
        output_dir.mkdir(exist_ok=True)
        return output_dir / safe_filename(filename)
