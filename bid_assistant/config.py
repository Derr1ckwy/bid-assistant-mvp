from __future__ import annotations

import os
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
    llm_model: str = os.getenv("LLM_MODEL", "qwen3.5:9b")
    llm_timeout_seconds: int = int(os.getenv("LLM_TIMEOUT_SECONDS", "180"))
    ragflow_base_url: str = os.getenv("RAGFLOW_BASE_URL", "http://localhost:9380").rstrip("/")
    ragflow_api_key: str = os.getenv("RAGFLOW_API_KEY", "")


settings = Settings()
