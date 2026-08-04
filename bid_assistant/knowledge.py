from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

from bid_assistant.models import KnowledgeChunk
from bid_assistant.parsers import DocumentParseError, parse_document


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


def search_knowledge(
    query: str,
    files_by_category: dict[str, list[Path]],
    *,
    top_k: int = 6,
) -> list[KnowledgeChunk]:
    terms = _query_terms(query)
    candidates: list[KnowledgeChunk] = []
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
                if score:
                    candidates.append(
                        KnowledgeChunk(
                            category=category,
                            source_file=path.name,
                            text=text,
                            score=float(score),
                        )
                    )
    candidates.sort(key=lambda item: item.score, reverse=True)
    return candidates[:top_k]
