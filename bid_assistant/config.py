from __future__ import annotations

import os
import json
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")


def _resolve_data_dir(value: str) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path.resolve()


@dataclass(frozen=True)
class Settings:
    data_dir: Path = _resolve_data_dir(os.getenv("BID_ASSISTANT_DATA_DIR", "./data"))
    llm_base_url: str = os.getenv("LLM_BASE_URL", "http://localhost:11434/v1").rstrip("/")
    llm_api_key: str = os.getenv("LLM_API_KEY", "ollama")
    llm_model: str = os.getenv("LLM_MODEL", "qwen3:4b")
    llm_timeout_seconds: int = int(os.getenv("LLM_TIMEOUT_SECONDS", "180"))
    llm_chunk_chars: int = int(os.getenv("LLM_CHUNK_CHARS", "12000"))
    llm_max_chunks: int = int(os.getenv("LLM_MAX_CHUNKS", "12"))
    ragflow_base_url: str = os.getenv("RAGFLOW_BASE_URL", "http://localhost:9380").rstrip("/")
    ragflow_api_key: str = os.getenv("RAGFLOW_API_KEY", "")
    mineru_cli: str = os.getenv("MINERU_CLI", "mineru")
    mineru_backend: str = os.getenv("MINERU_BACKEND", "pipeline")
    mineru_timeout_seconds: int = int(os.getenv("MINERU_TIMEOUT_SECONDS", "1800"))
    embedding_base_url: str = os.getenv("EMBEDDING_BASE_URL", "").rstrip("/")
    embedding_api_key: str = os.getenv("EMBEDDING_API_KEY", "")
    embedding_model: str = os.getenv("EMBEDDING_MODEL", "")
    embedding_timeout_seconds: int = int(os.getenv("EMBEDDING_TIMEOUT_SECONDS", "120"))
    vector_min_files: int = int(os.getenv("VECTOR_MIN_FILES", "40"))


def save_llm_settings(value: Settings) -> Path:
    env_path = PROJECT_ROOT / ".env"
    existing_lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []
    updates = {
        "LLM_BASE_URL": value.llm_base_url,
        "LLM_API_KEY": value.llm_api_key,
        "LLM_MODEL": value.llm_model,
        "LLM_TIMEOUT_SECONDS": str(value.llm_timeout_seconds),
        "LLM_CHUNK_CHARS": str(value.llm_chunk_chars),
        "LLM_MAX_CHUNKS": str(value.llm_max_chunks),
    }
    output: list[str] = []
    written: set[str] = set()
    for line in existing_lines:
        key = line.split("=", 1)[0].strip() if "=" in line and not line.lstrip().startswith("#") else ""
        if key in updates:
            output.append(f"{key}={json.dumps(updates[key], ensure_ascii=False)}")
            written.add(key)
        else:
            output.append(line)
    if output and output[-1].strip():
        output.append("")
    for key, item in updates.items():
        if key not in written:
            output.append(f"{key}={json.dumps(item, ensure_ascii=False)}")
    temporary_path = env_path.with_name(".env.tmp")
    temporary_path.write_text("\n".join(output).rstrip() + "\n", encoding="utf-8")
    temporary_path.replace(env_path)
    return env_path


settings = Settings()
