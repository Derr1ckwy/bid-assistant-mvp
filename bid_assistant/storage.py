from __future__ import annotations

import hashlib
import io
import json
import re
import shutil
import sqlite3
import stat
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Lock
from uuid import uuid4

from PIL import Image, UnidentifiedImageError


ARCHIVE_FORMAT = "bid-assistant-project"
ARCHIVE_VERSION = 1
MAX_ARCHIVE_BYTES = 100 * 1024 * 1024
MAX_ARCHIVE_FILES = 500
MAX_ARCHIVE_UNCOMPRESSED_BYTES = 512 * 1024 * 1024
MAX_MANIFEST_BYTES = 1024 * 1024
MAX_DRAFT_VERSIONS = 50
MAX_EXPORT_VERSIONS = 50
MAX_PACKAGE_VERSIONS = 20
KNOWLEDGE_CATEGORIES = {"company", "product", "history"}
ATTACHMENT_CATEGORIES = {"qualification", "business", "technical", "pricing", "signature", "other"}
QUALIFICATION_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg"}
_COPY_CHUNK_SIZE = 1024 * 1024
_PROJECT_ID_PATTERN = re.compile(r"[a-zA-Z0-9_-]+")
_DRAFT_VERSION_PATTERN = re.compile(r"draftv_\d{8}T\d{12}Z_[0-9a-f]{8}")
_EXPORT_VERSION_PATTERN = re.compile(r"exportv_\d{8}T\d{12}Z_[0-9a-f]{8}")
_PACKAGE_VERSION_PATTERN = re.compile(r"packagev_\d{8}T\d{12}Z_[0-9a-f]{8}")
_WINDOWS_RESERVED_NAME = re.compile(r"^(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)", re.IGNORECASE)
BEIJING_TIMEZONE = timezone(timedelta(hours=8))
_VERSION_STAMP_LOCK = Lock()
_LAST_VERSION_TIME: datetime | None = None


class ProjectArchiveError(ValueError):
    """Raised when a project backup cannot be safely exported or imported."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _version_stamp() -> str:
    global _LAST_VERSION_TIME
    with _VERSION_STAMP_LOCK:
        current = datetime.now(timezone.utc)
        if _LAST_VERSION_TIME is not None and current <= _LAST_VERSION_TIME:
            current = _LAST_VERSION_TIME + timedelta(microseconds=1)
        _LAST_VERSION_TIME = current
    return current.strftime("%Y%m%dT%H%M%S%fZ")


def _beijing_now() -> str:
    return datetime.now(BEIJING_TIMEZONE).isoformat(timespec="milliseconds")


def format_beijing_time(value: str) -> str:
    if not value:
        return ""
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return value
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(BEIJING_TIMEZONE).strftime("%Y-%m-%d %H:%M:%S")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(_COPY_CHUNK_SIZE):
            digest.update(chunk)
    return digest.hexdigest()


def _is_safe_archive_path(value: str) -> bool:
    if not value or "\\" in value or value.startswith("/") or re.match(r"^[a-zA-Z]:", value):
        return False
    parts = value.split("/")
    return not any(
        part in {"", ".", ".."}
        or part.rstrip(" .") != part
        or re.search(r'[<>:"|?*\x00-\x1f]', part)
        or _WINDOWS_RESERVED_NAME.match(part)
        for part in parts
    )


def safe_filename(value: str) -> str:
    name = re.split(r"[\\/]", value or "file")[-1]
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
                    updated_at TEXT NOT NULL,
                    archived INTEGER NOT NULL DEFAULT 0
                )
                """
            )
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(projects)")}
            if "archived" not in columns:
                conn.execute("ALTER TABLE projects ADD COLUMN archived INTEGER NOT NULL DEFAULT 0")

    def _project_exists(self, project_id: str) -> bool:
        with self._connect() as conn:
            row = conn.execute("SELECT 1 FROM projects WHERE id=?", (project_id,)).fetchone()
        return row is not None

    def _new_project_id(self) -> str:
        while True:
            project_id = f"proj_{uuid4().hex[:12]}"
            if not self._project_exists(project_id) and not (self.projects_dir / project_id).exists():
                return project_id

    def project_dir(self, project_id: str) -> Path:
        if not _PROJECT_ID_PATTERN.fullmatch(project_id):
            raise ValueError("Invalid project id")
        path = self.projects_dir / project_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def create_project(self, name: str) -> dict:
        project_id = self._new_project_id()
        timestamp = _now()
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO projects (id, name, status, created_at, updated_at) VALUES (?, ?, 'new', ?, ?)",
                (project_id, name.strip() or "未命名投标项目", timestamp, timestamp),
            )
        self.project_dir(project_id)
        return self.get_project(project_id)

    def list_projects(self, *, include_archived: bool = False) -> list[dict]:
        query = "SELECT * FROM projects"
        if not include_archived:
            query += " WHERE archived=0"
        query += " ORDER BY archived ASC, updated_at DESC"
        with self._connect() as conn:
            rows = conn.execute(query).fetchall()
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
            cursor = conn.execute(
                f"UPDATE projects SET {assignments} WHERE id=?",
                (*updates.values(), project_id),
            )
        if cursor.rowcount == 0:
            raise KeyError(f"Project not found: {project_id}")

    def set_project_archived(self, project_id: str, archived: bool = True) -> None:
        with self._connect() as conn:
            cursor = conn.execute(
                "UPDATE projects SET archived=?, updated_at=? WHERE id=?",
                (int(archived), _now(), project_id),
            )
        if cursor.rowcount == 0:
            raise KeyError(f"Project not found: {project_id}")

    def duplicate_project(self, project_id: str, name: str | None = None) -> dict:
        project = self.get_project(project_id)
        source = self.project_dir(project_id)
        new_id = self._new_project_id()
        temporary = self.projects_dir / f".copy_{uuid4().hex}"
        target = self.projects_dir / new_id
        temporary.mkdir()
        try:
            for path in source.rglob("*"):
                relative = path.relative_to(source)
                if relative.parts[0] in {"output", "versions", "attachments", "packages"}:
                    continue
                if relative.as_posix() == "submission_checklist.json":
                    continue
                if path.is_symlink():
                    raise ValueError("项目目录包含符号链接，无法安全复制")
                destination = temporary / relative
                if path.is_dir():
                    destination.mkdir(parents=True, exist_ok=True)
                elif path.is_file() and not path.name.endswith(".tmp"):
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(path, destination)

            status = project["status"]
            if status in {"exported", "packaged"}:
                if (temporary / "review.json").is_file():
                    status = "review_generated"
                elif (temporary / "drafts.json").is_file():
                    status = "draft_generated"
                else:
                    status = "analysis_confirmed"
            source_filename = project.get("source_filename")
            if source_filename and not (temporary / "source" / source_filename).is_file():
                source_filename = None
            timestamp = _now()
            default_name = f"{project['name']}（副本）"
            duplicate_name = (name or default_name).strip()[:200] or default_name

            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO projects
                        (id, name, status, source_filename, created_at, updated_at, archived)
                    VALUES (?, ?, ?, ?, ?, ?, 0)
                    """,
                    (new_id, duplicate_name, status, source_filename, timestamp, timestamp),
                )
                temporary.replace(target)
        except Exception:
            if temporary.exists():
                shutil.rmtree(temporary)
            if target.exists() and not self._project_exists(new_id):
                shutil.rmtree(target)
            raise
        return self.get_project(new_id)

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

    def delete_json(self, project_id: str, name: str) -> bool:
        if not re.fullmatch(r"[a-zA-Z0-9_-]+", name):
            raise ValueError("Invalid JSON document name")
        target = self.project_dir(project_id) / f"{name}.json"
        if not target.exists():
            return False
        target.unlink()
        return True

    def save_draft_version(self, project_id: str, drafts: list[dict], reason: str) -> dict:
        if not isinstance(drafts, list) or not drafts:
            raise ValueError("Draft version requires at least one draft")
        version_id = f"draftv_{_version_stamp()}_{uuid4().hex[:8]}"
        created_at = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
        payload = {
            "id": version_id,
            "created_at": created_at,
            "reason": reason.strip()[:100] or "草稿保存",
            "draft_count": len(drafts),
            "drafts": drafts,
        }
        versions_dir = self.project_dir(project_id) / "versions" / "drafts"
        versions_dir.mkdir(parents=True, exist_ok=True)
        target = versions_dir / f"{version_id}.json"
        temporary = target.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(target)

        paths = sorted(versions_dir.glob("draftv_*.json"), reverse=True)
        for expired in paths[MAX_DRAFT_VERSIONS:]:
            expired.unlink()
        return {key: payload[key] for key in ("id", "created_at", "reason", "draft_count")}

    def list_draft_versions(self, project_id: str) -> list[dict]:
        versions_dir = self.project_dir(project_id) / "versions" / "drafts"
        if not versions_dir.exists():
            return []
        result: list[dict] = []
        for path in sorted(versions_dir.glob("draftv_*.json"), reverse=True):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                if payload.get("id") != path.stem or not isinstance(payload.get("drafts"), list):
                    continue
                result.append(
                    {
                        "id": payload["id"],
                        "created_at": payload.get("created_at", ""),
                        "reason": payload.get("reason", "草稿保存"),
                        "draft_count": len(payload["drafts"]),
                    }
                )
            except (OSError, json.JSONDecodeError):
                continue
        return result

    def load_draft_version(self, project_id: str, version_id: str) -> dict:
        if not _DRAFT_VERSION_PATTERN.fullmatch(version_id):
            raise ValueError("Invalid draft version id")
        target = self.project_dir(project_id) / "versions" / "drafts" / f"{version_id}.json"
        if not target.is_file():
            raise KeyError(f"Draft version not found: {version_id}")
        payload = json.loads(target.read_text(encoding="utf-8"))
        if payload.get("id") != version_id or not isinstance(payload.get("drafts"), list):
            raise ValueError("Invalid draft version payload")
        return payload

    def restore_draft_version(self, project_id: str, version_id: str) -> list[dict]:
        version = self.load_draft_version(project_id, version_id)
        current = self.load_json(project_id, "drafts", [])
        if current:
            self.save_draft_version(project_id, current, "恢复版本前自动快照")
        drafts = version["drafts"]
        self.save_json(project_id, "drafts", drafts)
        self.delete_json(project_id, "review")
        self.update_project(project_id, status="draft_generated")
        return drafts

    def save_knowledge_file(self, project_id: str, category: str, filename: str, content: bytes) -> Path:
        if category not in KNOWLEDGE_CATEGORIES:
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

    def knowledge_path(self, project_id: str, reference: str) -> Path | None:
        parts = reference.split("/")
        if len(parts) != 2 or parts[0] not in KNOWLEDGE_CATEGORIES:
            return None
        filename = parts[1]
        if filename != safe_filename(filename):
            return None
        path = self.project_dir(project_id) / "knowledge" / parts[0] / filename
        return path if path.is_file() else None

    def delete_knowledge_file(self, project_id: str, reference: str) -> bool:
        path = self.knowledge_path(project_id, reference)
        if path is None:
            return False
        path.unlink()
        try:
            path.parent.rmdir()
        except OSError:
            pass
        return True

    def save_word_template(self, project_id: str, filename: str, content: bytes) -> Path:
        sanitized = safe_filename(filename)
        if Path(sanitized).suffix.lower() != ".docx":
            raise ValueError("Word 模板仅支持 DOCX 文件")
        try:
            with zipfile.ZipFile(io.BytesIO(content)) as archive:
                if "word/document.xml" not in archive.namelist():
                    raise ValueError("上传的文件不是有效的 DOCX 模板")
        except zipfile.BadZipFile as exc:
            raise ValueError("上传的文件不是有效的 DOCX 模板") from exc
        template_dir = self.project_dir(project_id) / "template"
        template_dir.mkdir(exist_ok=True)
        for existing in template_dir.iterdir():
            if existing.is_file():
                existing.unlink()
        target = template_dir / sanitized
        target.write_bytes(content)
        return target

    def word_template_path(self, project_id: str) -> Path | None:
        template_dir = self.project_dir(project_id) / "template"
        if not template_dir.exists():
            return None
        templates = sorted(path for path in template_dir.iterdir() if path.is_file() and path.suffix.lower() == ".docx")
        return templates[0] if templates else None

    def delete_word_template(self, project_id: str) -> bool:
        path = self.word_template_path(project_id)
        if path is None:
            return False
        path.unlink()
        try:
            path.parent.rmdir()
        except OSError:
            pass
        return True

    def save_qualification_image(self, project_id: str, filename: str, content: bytes) -> Path:
        sanitized = safe_filename(filename)
        suffix = Path(sanitized).suffix.lower()
        if suffix not in QUALIFICATION_IMAGE_EXTENSIONS:
            raise ValueError("资质图片仅支持 PNG、JPG、JPEG")
        try:
            with Image.open(io.BytesIO(content)) as image:
                image.verify()
        except (UnidentifiedImageError, OSError) as exc:
            raise ValueError("上传的文件不是有效图片") from exc
        target_dir = self.project_dir(project_id) / "qualification_images"
        target_dir.mkdir(exist_ok=True)
        target = target_dir / sanitized
        stem = Path(sanitized).stem[:150] or "qualification"
        counter = 2
        while target.exists():
            target = target_dir / safe_filename(f"{stem} ({counter}){suffix}")
            counter += 1
        target.write_bytes(content)
        return target

    def list_qualification_images(self, project_id: str) -> list[Path]:
        target_dir = self.project_dir(project_id) / "qualification_images"
        if not target_dir.exists():
            return []
        return sorted(
            path for path in target_dir.iterdir()
            if path.is_file() and path.suffix.lower() in QUALIFICATION_IMAGE_EXTENSIONS
        )

    def qualification_image_path(self, project_id: str, filename: str) -> Path | None:
        if filename != safe_filename(filename):
            return None
        path = self.project_dir(project_id) / "qualification_images" / filename
        return path if path.is_file() and path.suffix.lower() in QUALIFICATION_IMAGE_EXTENSIONS else None

    def delete_qualification_image(self, project_id: str, filename: str) -> bool:
        path = self.qualification_image_path(project_id, filename)
        if path is None:
            return False
        path.unlink()
        return True

    def save_attachment_file(self, project_id: str, category: str, filename: str, content: bytes) -> Path:
        if category not in ATTACHMENT_CATEGORIES:
            raise ValueError("Invalid attachment category")
        target_dir = self.project_dir(project_id) / "attachments" / category
        target_dir.mkdir(parents=True, exist_ok=True)
        sanitized = safe_filename(filename)
        target = target_dir / sanitized
        stem = Path(sanitized).stem[:150] or "attachment"
        suffix = Path(sanitized).suffix
        counter = 2
        while target.exists():
            target = target_dir / safe_filename(f"{stem} ({counter}){suffix}")
            counter += 1
        target.write_bytes(content)
        return target

    def list_attachment_files(self, project_id: str) -> dict[str, list[Path]]:
        result = {category: [] for category in sorted(ATTACHMENT_CATEGORIES)}
        base = self.project_dir(project_id) / "attachments"
        for category in result:
            category_dir = base / category
            if category_dir.exists():
                result[category] = sorted(path for path in category_dir.iterdir() if path.is_file())
        return result

    def attachment_path(self, project_id: str, reference: str) -> Path | None:
        parts = reference.split("/")
        if len(parts) != 2 or parts[0] not in ATTACHMENT_CATEGORIES:
            return None
        filename = parts[1]
        if filename != safe_filename(filename):
            return None
        path = self.project_dir(project_id) / "attachments" / parts[0] / filename
        return path if path.is_file() else None

    def delete_attachment_file(self, project_id: str, reference: str) -> bool:
        path = self.attachment_path(project_id, reference)
        if path is None:
            return False
        path.unlink()
        return True

    def output_path(self, project_id: str, filename: str) -> Path:
        output_dir = self.project_dir(project_id) / "output"
        output_dir.mkdir(exist_ok=True)
        return output_dir / safe_filename(filename)

    def next_output_version(self, project_id: str, base_filename: str) -> dict:
        versions = self.list_export_versions(project_id)
        version = max((item["version"] for item in versions), default=0) + 1
        sanitized = safe_filename(base_filename)
        stem = Path(sanitized).stem or "投标文件初稿"
        while True:
            filename = safe_filename(f"{stem}_V{version:03d}.docx")
            path = self.output_path(project_id, filename)
            if not path.exists():
                return {"version": version, "filename": filename, "path": path}
            version += 1

    def record_export_version(
        self,
        project_id: str,
        output_path: str | Path,
        *,
        version: int,
        chapter_count: int,
        review_summary: dict[str, int],
        warning_count: int = 0,
        note: str = "",
        template_filename: str = "",
        qualification_image_count: int = 0,
    ) -> dict:
        if version < 1 or chapter_count < 1:
            raise ValueError("Invalid export version metadata")
        output_dir = (self.project_dir(project_id) / "output").resolve()
        target = Path(output_path).resolve()
        if target.parent != output_dir or target.suffix.lower() != ".docx" or not target.is_file():
            raise ValueError("Export file must be an existing project Word output")

        version_id = f"exportv_{_version_stamp()}_{uuid4().hex[:8]}"
        payload = {
            "id": version_id,
            "version": version,
            "created_at": _beijing_now(),
            "filename": target.name,
            "size": target.stat().st_size,
            "sha256": _sha256(target),
            "chapter_count": chapter_count,
            "review_summary": {
                key: max(0, int(review_summary.get(key, 0)))
                for key in ("pending", "high", "medium", "low")
            },
            "warning_count": max(0, int(warning_count)),
            "note": note.strip()[:200],
            "template_filename": safe_filename(template_filename) if template_filename else "",
            "qualification_image_count": max(0, int(qualification_image_count)),
        }
        versions_dir = self.project_dir(project_id) / "versions" / "exports"
        versions_dir.mkdir(parents=True, exist_ok=True)
        manifest = versions_dir / f"{version_id}.json"
        temporary = manifest.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(manifest)

        manifests = sorted(versions_dir.glob("exportv_*.json"), reverse=True)
        for expired in manifests[MAX_EXPORT_VERSIONS:]:
            try:
                expired_payload = json.loads(expired.read_text(encoding="utf-8"))
                expired_name = expired_payload.get("filename")
                if isinstance(expired_name, str) and expired_name == safe_filename(expired_name):
                    expired_output = output_dir / expired_name
                    if expired_output.is_file():
                        expired_output.unlink()
            except (OSError, json.JSONDecodeError):
                pass
            expired.unlink(missing_ok=True)
        return {**payload, "path": target}

    def list_export_versions(self, project_id: str) -> list[dict]:
        versions_dir = self.project_dir(project_id) / "versions" / "exports"
        if not versions_dir.exists():
            return []
        output_dir = self.project_dir(project_id) / "output"
        result: list[dict] = []
        for manifest in sorted(versions_dir.glob("exportv_*.json"), reverse=True):
            try:
                payload = json.loads(manifest.read_text(encoding="utf-8"))
                filename = payload.get("filename")
                version = payload.get("version")
                if (
                    payload.get("id") != manifest.stem
                    or not _EXPORT_VERSION_PATTERN.fullmatch(manifest.stem)
                    or not isinstance(filename, str)
                    or filename != safe_filename(filename)
                    or not isinstance(version, int)
                    or version < 1
                ):
                    continue
                path = output_dir / filename
                if not path.is_file():
                    continue
                review_summary = payload.get("review_summary")
                if not isinstance(review_summary, dict):
                    review_summary = {}
                result.append(
                    {
                        "id": payload["id"],
                        "version": version,
                        "created_at": payload.get("created_at", ""),
                        "filename": filename,
                        "size": path.stat().st_size,
                        "sha256": payload.get("sha256", ""),
                        "chapter_count": int(payload.get("chapter_count", 0)),
                        "review_summary": review_summary,
                        "warning_count": int(payload.get("warning_count", 0)),
                        "note": str(payload.get("note", "")),
                        "template_filename": str(payload.get("template_filename", "")),
                        "qualification_image_count": int(payload.get("qualification_image_count", 0)),
                        "path": path,
                    }
                )
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                continue
        return result

    def package_path(self, project_id: str, filename: str) -> Path:
        packages_dir = self.project_dir(project_id) / "packages"
        packages_dir.mkdir(exist_ok=True)
        return packages_dir / safe_filename(filename)

    def next_package_version(self, project_id: str, base_filename: str) -> dict:
        versions = self.list_package_versions(project_id)
        version = max((item["version"] for item in versions), default=0) + 1
        sanitized = safe_filename(base_filename)
        stem = Path(sanitized).stem or "提交包"
        while True:
            filename = safe_filename(f"{stem}_P{version:03d}.zip")
            path = self.package_path(project_id, filename)
            if not path.exists():
                return {"version": version, "filename": filename, "path": path}
            version += 1

    def record_package_version(
        self,
        project_id: str,
        package_path: str | Path,
        *,
        version: int,
        word_version: dict,
        checklist_summary: dict,
        attachment_count: int,
        warning_count: int = 0,
        internal_review_only: bool = False,
        note: str = "",
    ) -> dict:
        word_number = word_version.get("version")
        word_filename = word_version.get("filename")
        if (
            version < 1
            or not isinstance(word_number, int)
            or word_number < 1
            or not isinstance(word_filename, str)
            or word_filename != safe_filename(word_filename)
            or attachment_count < 0
        ):
            raise ValueError("Invalid package version metadata")

        packages_dir = (self.project_dir(project_id) / "packages").resolve()
        target = Path(package_path).resolve()
        if target.parent != packages_dir or target.suffix.lower() != ".zip" or not target.is_file():
            raise ValueError("Package file must be an existing project ZIP package")

        summary_keys = (
            "total",
            "required",
            "ready",
            "not_applicable",
            "pending_required",
            "linked",
            "broken_links",
        )
        normalized_summary = {
            key: max(0, int(checklist_summary.get(key, 0)))
            for key in summary_keys
        }
        normalized_summary["complete"] = bool(checklist_summary.get("complete", False))

        version_id = f"packagev_{_version_stamp()}_{uuid4().hex[:8]}"
        payload = {
            "id": version_id,
            "version": version,
            "created_at": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "filename": target.name,
            "size": target.stat().st_size,
            "sha256": _sha256(target),
            "word_version": word_number,
            "word_filename": word_filename,
            "checklist_summary": normalized_summary,
            "attachment_count": int(attachment_count),
            "warning_count": max(0, int(warning_count)),
            "internal_review_only": bool(internal_review_only),
            "note": note.strip()[:200],
        }
        versions_dir = self.project_dir(project_id) / "versions" / "packages"
        versions_dir.mkdir(parents=True, exist_ok=True)
        manifest = versions_dir / f"{version_id}.json"
        temporary = manifest.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(manifest)

        manifests = sorted(versions_dir.glob("packagev_*.json"), reverse=True)
        for expired in manifests[MAX_PACKAGE_VERSIONS:]:
            try:
                expired_payload = json.loads(expired.read_text(encoding="utf-8"))
                expired_name = expired_payload.get("filename")
                if isinstance(expired_name, str) and expired_name == safe_filename(expired_name):
                    expired_package = packages_dir / expired_name
                    if expired_package.is_file():
                        expired_package.unlink()
            except (OSError, json.JSONDecodeError):
                pass
            expired.unlink(missing_ok=True)
        return {**payload, "path": target}

    def list_package_versions(self, project_id: str) -> list[dict]:
        versions_dir = self.project_dir(project_id) / "versions" / "packages"
        if not versions_dir.exists():
            return []
        packages_dir = self.project_dir(project_id) / "packages"
        result: list[dict] = []
        for manifest in sorted(versions_dir.glob("packagev_*.json"), reverse=True):
            try:
                payload = json.loads(manifest.read_text(encoding="utf-8"))
                filename = payload.get("filename")
                version = payload.get("version")
                if (
                    payload.get("id") != manifest.stem
                    or not _PACKAGE_VERSION_PATTERN.fullmatch(manifest.stem)
                    or not isinstance(filename, str)
                    or filename != safe_filename(filename)
                    or not isinstance(version, int)
                    or version < 1
                ):
                    continue
                path = packages_dir / filename
                if not path.is_file():
                    continue
                checklist_summary = payload.get("checklist_summary")
                if not isinstance(checklist_summary, dict):
                    checklist_summary = {}
                result.append(
                    {
                        "id": payload["id"],
                        "version": version,
                        "created_at": payload.get("created_at", ""),
                        "filename": filename,
                        "size": path.stat().st_size,
                        "sha256": payload.get("sha256", ""),
                        "word_version": int(payload.get("word_version", 0)),
                        "word_filename": str(payload.get("word_filename", "")),
                        "checklist_summary": checklist_summary,
                        "attachment_count": int(payload.get("attachment_count", 0)),
                        "warning_count": int(payload.get("warning_count", 0)),
                        "internal_review_only": bool(payload.get("internal_review_only", False)),
                        "note": str(payload.get("note", "")),
                        "path": path,
                    }
                )
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                continue
        return result

    def project_progress(self, project_id: str) -> dict:
        project_path = self.project_dir(project_id)
        source_ready = self.source_path(project_id) is not None
        drafts = self.load_json(project_id, "drafts", [])
        checklist_payload = self.load_json(project_id, "submission_checklist", [])
        checklist_items = [
            item
            for item in checklist_payload
            if isinstance(item, dict) and str(item.get("name", "")).strip()
        ]
        checklist_complete = bool(checklist_items) and all(
            not bool(item.get("required", True)) or item.get("status") in {"已备妥", "不适用"}
            for item in checklist_items
        )
        output_dir = project_path / "output"
        steps = [
            {"key": "source", "label": "招标文件", "complete": source_ready},
            {"key": "parsed", "label": "文档解析", "complete": (project_path / "parsed.json").is_file()},
            {"key": "analysis", "label": "需求分析", "complete": (project_path / "analysis.json").is_file()},
            {"key": "drafts", "label": "章节草稿", "complete": bool(drafts)},
            {"key": "review", "label": "复核检查", "complete": (project_path / "review.json").is_file()},
            {
                "key": "output",
                "label": "Word 导出",
                "complete": output_dir.is_dir()
                and any(path.is_file() and path.suffix.lower() == ".docx" for path in output_dir.iterdir()),
            },
            {"key": "submission", "label": "提交清单", "complete": checklist_complete},
            {"key": "package", "label": "提交打包", "complete": bool(self.list_package_versions(project_id))},
        ]
        completed = sum(bool(step["complete"]) for step in steps)
        knowledge_count = sum(len(paths) for paths in self.list_knowledge_files(project_id).values())
        attachment_count = sum(len(paths) for paths in self.list_attachment_files(project_id).values())
        return {
            "steps": steps,
            "completed": completed,
            "total": len(steps),
            "percent": round(completed / len(steps) * 100),
            "knowledge_files": knowledge_count,
            "attachment_files": attachment_count,
        }

    def export_project_archive(self, project_id: str) -> bytes:
        project = self.get_project(project_id)
        base = self.project_dir(project_id)
        paths = sorted(path for path in base.rglob("*") if path.is_file())
        if len(paths) > MAX_ARCHIVE_FILES:
            raise ProjectArchiveError(f"项目文件数超过备份上限（{MAX_ARCHIVE_FILES} 个）")
        if any(path.is_symlink() for path in paths):
            raise ProjectArchiveError("项目目录包含符号链接，无法安全备份")

        total_size = sum(path.stat().st_size for path in paths)
        if total_size > MAX_ARCHIVE_UNCOMPRESSED_BYTES:
            raise ProjectArchiveError("项目文件总大小超过备份上限（512 MB）")

        files = [
            {
                "path": path.relative_to(base).as_posix(),
                "size": path.stat().st_size,
                "sha256": _sha256(path),
            }
            for path in paths
        ]
        manifest = {
            "format": ARCHIVE_FORMAT,
            "version": ARCHIVE_VERSION,
            "exported_at": _now(),
            "project": {
                key: project.get(key)
                for key in ("id", "name", "status", "source_filename", "created_at", "updated_at", "archived")
            },
            "files": files,
        }

        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
            archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
            for path, record in zip(paths, files, strict=True):
                archive.write(path, f"project/{record['path']}")
        result = buffer.getvalue()
        if len(result) > MAX_ARCHIVE_BYTES:
            raise ProjectArchiveError("压缩后的项目备份超过上传上限（100 MB）")
        return result

    def import_project_archive(self, content: bytes) -> dict:
        if not content:
            raise ProjectArchiveError("备份文件为空")
        if len(content) > MAX_ARCHIVE_BYTES:
            raise ProjectArchiveError("项目备份超过上传上限（100 MB）")

        try:
            archive = zipfile.ZipFile(io.BytesIO(content), "r")
        except zipfile.BadZipFile as exc:
            raise ProjectArchiveError("文件不是有效的项目 ZIP 备份") from exc

        with archive:
            infos = archive.infolist()
            file_infos = [info for info in infos if not info.is_dir()]
            if len(file_infos) > MAX_ARCHIVE_FILES + 1:
                raise ProjectArchiveError(f"备份文件数量超过上限（{MAX_ARCHIVE_FILES} 个项目文件）")
            if sum(info.file_size for info in file_infos) > MAX_ARCHIVE_UNCOMPRESSED_BYTES:
                raise ProjectArchiveError("备份解压后的大小超过上限（512 MB）")

            entries: dict[str, zipfile.ZipInfo] = {}
            for info in infos:
                name = info.filename.rstrip("/")
                if not _is_safe_archive_path(name):
                    raise ProjectArchiveError(f"备份包含不安全路径：{info.filename}")
                project_directory = name == "project" and info.is_dir()
                if name != "manifest.json" and not project_directory and not name.startswith("project/"):
                    raise ProjectArchiveError(f"备份包含未知目录：{info.filename}")
                if name in entries:
                    raise ProjectArchiveError(f"备份包含重复路径：{info.filename}")
                if info.flag_bits & 0x1:
                    raise ProjectArchiveError("不支持加密 ZIP 备份")
                file_type = (info.external_attr >> 16) & 0o170000
                if file_type == stat.S_IFLNK:
                    raise ProjectArchiveError("备份包含符号链接")
                entries[name] = info

            manifest_info = entries.get("manifest.json")
            if manifest_info is None or manifest_info.is_dir():
                raise ProjectArchiveError("备份缺少 manifest.json")
            if manifest_info.file_size > MAX_MANIFEST_BYTES:
                raise ProjectArchiveError("manifest.json 超过大小上限（1 MB）")
            try:
                manifest = json.loads(archive.read(manifest_info).decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ProjectArchiveError("manifest.json 无法解析") from exc
            if manifest.get("format") != ARCHIVE_FORMAT or manifest.get("version") != ARCHIVE_VERSION:
                raise ProjectArchiveError("备份格式或版本不受支持")

            metadata = manifest.get("project")
            records = manifest.get("files")
            if not isinstance(metadata, dict) or not isinstance(records, list):
                raise ProjectArchiveError("备份清单结构不完整")

            expected: dict[str, dict] = {}
            for record in records:
                if not isinstance(record, dict):
                    raise ProjectArchiveError("备份文件清单格式错误")
                relative = record.get("path")
                size = record.get("size")
                checksum = record.get("sha256")
                if (
                    not isinstance(relative, str)
                    or not _is_safe_archive_path(relative)
                    or not isinstance(size, int)
                    or size < 0
                    or not isinstance(checksum, str)
                    or not re.fullmatch(r"[0-9a-f]{64}", checksum)
                ):
                    raise ProjectArchiveError("备份文件清单包含无效记录")
                if relative in expected:
                    raise ProjectArchiveError(f"备份清单包含重复文件：{relative}")
                expected[relative] = record

            actual = {
                name.removeprefix("project/"): info
                for name, info in entries.items()
                if name.startswith("project/") and not info.is_dir()
            }
            if set(actual) != set(expected):
                raise ProjectArchiveError("备份文件与清单不一致")

            original_id = metadata.get("id")
            if (
                isinstance(original_id, str)
                and _PROJECT_ID_PATTERN.fullmatch(original_id)
                and not self._project_exists(original_id)
                and not (self.projects_dir / original_id).exists()
            ):
                project_id = original_id
            else:
                project_id = self._new_project_id()

            temporary = self.projects_dir / f".import_{uuid4().hex}"
            final_path = self.projects_dir / project_id
            temporary.mkdir()
            try:
                for relative, record in expected.items():
                    target = temporary.joinpath(*relative.split("/"))
                    target.parent.mkdir(parents=True, exist_ok=True)
                    digest = hashlib.sha256()
                    written = 0
                    with archive.open(actual[relative], "r") as source, target.open("wb") as destination:
                        while chunk := source.read(_COPY_CHUNK_SIZE):
                            written += len(chunk)
                            if written > record["size"]:
                                raise ProjectArchiveError(f"文件大小校验失败：{relative}")
                            digest.update(chunk)
                            destination.write(chunk)
                    if written != record["size"] or digest.hexdigest() != record["sha256"]:
                        raise ProjectArchiveError(f"文件完整性校验失败：{relative}")

                imported_name = str(metadata.get("name") or "导入的投标项目").strip()[:200]
                name = imported_name or "导入的投标项目"
                status_value = metadata.get("status")
                valid_status = isinstance(status_value, str) and re.fullmatch(r"[a-z0-9_]{1,64}", status_value)
                status = status_value if valid_status else "new"
                source_value = metadata.get("source_filename")
                source_filename = safe_filename(source_value) if isinstance(source_value, str) else None
                if source_filename and not (temporary / "source" / source_filename).is_file():
                    source_filename = None
                created_value = metadata.get("created_at")
                created_at = created_value if isinstance(created_value, str) and created_value else _now()
                imported_at = _now()

                with self._connect() as conn:
                    conn.execute(
                        """
                        INSERT INTO projects
                            (id, name, status, source_filename, created_at, updated_at, archived)
                        VALUES (?, ?, ?, ?, ?, ?, 0)
                        """,
                        (project_id, name, status, source_filename, created_at, imported_at),
                    )
                    temporary.replace(final_path)
            except Exception:
                if temporary.exists():
                    shutil.rmtree(temporary)
                if final_path.exists() and not self._project_exists(project_id):
                    shutil.rmtree(final_path)
                raise

        return self.get_project(project_id)
