from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import requests

from bid_assistant.config import Settings


class EmbeddingError(RuntimeError):
    pass


@dataclass(frozen=True)
class OpenAICompatibleEmbeddingClient:
    base_url: str
    api_key: str
    model: str
    timeout: int = 120
    batch_size: int = 16
    query_instruction: str = ""

    @classmethod
    def from_settings(cls, settings: Settings) -> "OpenAICompatibleEmbeddingClient":
        return cls(
            settings.embedding_base_url,
            settings.embedding_api_key,
            settings.embedding_model,
            settings.embedding_timeout_seconds,
            settings.embedding_batch_size,
            settings.embedding_query_instruction,
        )

    @property
    def configured(self) -> bool:
        return bool(self.base_url and self.model)

    @property
    def cache_key(self) -> str:
        return f"{self.base_url}|{self.model}"

    def _embed_batch(self, texts: list[str]) -> list[list[float]]:
        payload: dict[str, Any] = {"model": self.model, "input": texts}
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        try:
            response = requests.post(
                f"{self.base_url}/embeddings",
                headers=headers,
                json=payload,
                timeout=self.timeout,
            )
            response.raise_for_status()
            data = response.json().get("data", [])
            ordered = sorted(data, key=lambda item: item.get("index", 0))
            vectors = [item.get("embedding") for item in ordered]
            if len(vectors) != len(texts) or not all(isinstance(item, list) and item for item in vectors):
                raise EmbeddingError("向量接口返回数量或格式不正确。")
            return [[float(value) for value in vector] for vector in vectors]
        except requests.RequestException as exc:
            raise EmbeddingError(f"向量接口请求失败：{exc}") from exc
        except (ValueError, TypeError, AttributeError) as exc:
            raise EmbeddingError("向量接口返回格式不正确。") from exc

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not self.configured:
            raise EmbeddingError("未配置向量模型。")
        if not texts:
            return []
        batch_size = max(1, self.batch_size)
        vectors: list[list[float]] = []
        for index in range(0, len(texts), batch_size):
            vectors.extend(self._embed_batch(texts[index : index + batch_size]))
        return vectors

    def embed_query(self, query: str) -> list[float]:
        value = query
        if self.query_instruction:
            value = f"Instruct: {self.query_instruction}\nQuery: {query}"
        return self.embed([value])[0]
