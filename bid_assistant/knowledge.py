from __future__ import annotations

import re
import math
from functools import lru_cache
from pathlib import Path

from bid_assistant.models import KnowledgeChunk
from bid_assistant.parsers import DocumentParseError, parse_document
from bid_assistant.config import settings
from bid_assistant.embeddings import EmbeddingError, OpenAICompatibleEmbeddingClient


_VECTOR_CACHE: dict[tuple[str, int, int, str], tuple[tuple[float, ...], ...]] = {}


def _chunks(text: str, max_chars: int = 900) -> list[str]:
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", text) if part.strip()]
    result: list[str] = []
    buffer = ""
    for paragraph in paragraphs:
        if len(buffer) + len(paragraph) + 2 <= max_chars:
            buffer = f"{buffer}\n\n{paragraph}".strip()
            continue
        if buffer:
            result.append(buffer)
        if len(paragraph) <= max_chars:
            buffer = paragraph
        else:
            result.extend(paragraph[index : index + max_chars] for index in range(0, len(paragraph), max_chars))
            buffer = ""
    if buffer:
        result.append(buffer)
    return result


def _query_terms(query: str) -> list[str]:
    terms = set(re.findall(r"[A-Za-z0-9][A-Za-z0-9_.-]{1,}|[\u4e00-\u9fff]{2,}", query))
    expanded: set[str] = set()
    for term in terms:
        expanded.add(term.lower())
        if re.fullmatch(r"[\u4e00-\u9fff]+", term) and len(term) > 4:
            expanded.update(term[index : index + 2] for index in range(len(term) - 1))
            expanded.update(term[index : index + 3] for index in range(len(term) - 2))
    return sorted(expanded, key=len, reverse=True)[:40]


@lru_cache(maxsize=256)
def _cached_file_chunks(path_value: str, modified_ns: int, size: int) -> tuple[str, ...]:
    del modified_ns, size
    parsed = parse_document(Path(path_value))
    return tuple(_chunks(parsed.full_text))


def clear_knowledge_cache() -> None:
    _cached_file_chunks.cache_clear()
    _VECTOR_CACHE.clear()


def _cosine(left: list[float] | tuple[float, ...], right: list[float] | tuple[float, ...]) -> float:
    if len(left) != len(right) or not left:
        return 0.0
    numerator = sum(a * b for a, b in zip(left, right, strict=True))
    denominator = math.sqrt(sum(value * value for value in left)) * math.sqrt(sum(value * value for value in right))
    return numerator / denominator if denominator else 0.0


def search_knowledge(
    query: str,
    files_by_category: dict[str, list[Path]],
    *,
    top_k: int = 6,
    embedding_client=None,
    vector_min_files: int | None = None,
) -> list[KnowledgeChunk]:
    terms = _query_terms(query)
    records: list[tuple[str, str, str, float, tuple[str, int, int, str]]] = []
    file_count = sum(len(files) for files in files_by_category.values())
    threshold = settings.vector_min_files if vector_min_files is None else max(1, vector_min_files)
    client = embedding_client or OpenAICompatibleEmbeddingClient.from_settings(settings)
    vector_enabled = file_count >= threshold and bool(getattr(client, "configured", False))
    cache_key = str(getattr(client, "cache_key", "custom"))
    for category, files in files_by_category.items():
        for path in files:
            try:
                stat = path.stat()
                chunks = _cached_file_chunks(str(path.resolve()), stat.st_mtime_ns, stat.st_size)
            except DocumentParseError:
                continue
            except OSError:
                continue
            for text in chunks:
                normalized = text.lower()
                score = sum(normalized.count(term) * min(len(term), 6) for term in terms)
                file_key = (str(path.resolve()), stat.st_mtime_ns, stat.st_size, cache_key)
                records.append((category, path.name, text, float(score), file_key))
    if vector_enabled and records:
        try:
            query_vector = client.embed([query])[0]
            missing_by_file: dict[tuple[str, int, int, str], list[str]] = {}
            for _, _, text, _, file_key in records:
                if file_key not in _VECTOR_CACHE:
                    missing_by_file.setdefault(file_key, []).append(text)
            for file_key, texts in missing_by_file.items():
                _VECTOR_CACHE[file_key] = tuple(tuple(vector) for vector in client.embed(texts))
            offsets: dict[tuple[str, int, int, str], int] = {}
            max_keyword = max((record[3] for record in records), default=0.0)
            candidates: list[KnowledgeChunk] = []
            for category, filename, text, keyword_score, file_key in records:
                index = offsets.get(file_key, 0)
                offsets[file_key] = index + 1
                dense = max(0.0, _cosine(query_vector, _VECTOR_CACHE[file_key][index]))
                keyword = keyword_score / max_keyword if max_keyword else 0.0
                candidates.append(KnowledgeChunk(
                    category=category,
                    source_file=filename,
                    text=text,
                    score=round(dense * 0.7 + keyword * 0.3, 6),
                ))
            candidates.sort(key=lambda item: item.score, reverse=True)
            return candidates[:top_k]
        except (EmbeddingError, OSError, ValueError, TypeError, IndexError):
            pass

    candidates = [
        KnowledgeChunk(category=category, source_file=filename, text=text, score=keyword_score)
        for category, filename, text, keyword_score, _ in records
        if keyword_score
    ]
    candidates.sort(key=lambda item: item.score, reverse=True)
    return candidates[:top_k]
