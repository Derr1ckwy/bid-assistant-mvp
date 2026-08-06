from __future__ import annotations

import argparse
import json
import math
import re
import shutil
import sys
import time
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from bid_assistant.embeddings import OpenAICompatibleEmbeddingClient
from bid_assistant.knowledge import clear_knowledge_cache, search_knowledge


CATEGORIES = ("company", "product", "history")
QUERY_INSTRUCTION = (
    "Retrieve the most relevant supporting passage for a Chinese construction tender question."
)


@dataclass(frozen=True)
class QueryCase:
    question: str
    expected_source: str


QUERY_CASES = (
    QueryCase("公司具备哪些防水施工资质，证书有效期到什么时候？", "02_资质证书与合规清单.md"),
    QueryCase("项目经理、技术负责人和安全员需要提供哪些证明材料？", "04_核心团队与人员履历.md"),
    QueryCase("屋面渗漏维修后怎样安排淋水或闭水试验和质量验收？", "02_屋面防水维修改造技术方案.md"),
    QueryCase("SBS 改性沥青卷材和其他屋面材料应该怎样选型？", "01_屋面防水材料与选型.md"),
    QueryCase("有没有机关办公楼屋面防水维修的类似项目业绩？", "01_机关办公楼屋面防水案例.md"),
    QueryCase("有没有高校图书馆屋面修缮经验，施工难点是什么？", "02_高校图书馆屋面修缮案例.md"),
    QueryCase("医院不停诊条件下如何组织屋面维修？", "04_医院门诊楼屋面修缮案例.md"),
    QueryCase("质保期内再次渗漏时多久响应，售后如何闭环？", "06_质保与售后服务体系.md"),
    QueryCase("高处动火、临边防护和成品保护有哪些措施？", "03_安全文明施工与成品保护.md"),
    QueryCase("公司的质量管理体系和三级检查制度是怎样的？", "05_项目管理与质量体系.md"),
    QueryCase("近三年营业收入、资产负债和现金流情况如何？", "03_近三年财务状况.md"),
    QueryCase("发生雨水倒灌后，抢修人员多快到场并完成回访？", "06_质保与售后服务体系.md"),
)


class KeywordOnlyClient:
    configured = False
    cache_key = "keyword-only"


class CountingEmbeddingClient:
    def __init__(self, inner: OpenAICompatibleEmbeddingClient) -> None:
        self.inner = inner
        self.document_requests = 0
        self.document_texts = 0
        self.query_requests = 0

    @property
    def configured(self) -> bool:
        return self.inner.configured

    @property
    def cache_key(self) -> str:
        return self.inner.cache_key

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.document_texts += len(texts)
        self.document_requests += math.ceil(len(texts) / max(1, self.inner.batch_size))
        return self.inner.embed(texts)

    def embed_query(self, query: str) -> list[float]:
        self.query_requests += 1
        return self.inner.embed_query(query)


def _safe_fragment(value: str) -> str:
    cleaned = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff_-]+", "_", value).strip("_")
    return cleaned[:70] or "section"


def _split_markdown(text: str, max_chars: int = 760) -> list[str]:
    sections: list[str] = []
    current: list[str] = []
    for line in text.splitlines():
        if re.match(r"^#{1,6}\s+", line) and current:
            value = "\n".join(current).strip()
            if value:
                sections.append(value)
            current = [line]
        else:
            current.append(line)
    value = "\n".join(current).strip()
    if value:
        sections.append(value)

    result: list[str] = []
    for section in sections:
        if len(section) <= max_chars:
            result.append(section)
            continue
        paragraphs = [part.strip() for part in re.split(r"\n\s*\n", section) if part.strip()]
        buffer = ""
        for paragraph in paragraphs:
            candidate = f"{buffer}\n\n{paragraph}".strip()
            if buffer and len(candidate) > max_chars:
                result.append(buffer)
                buffer = paragraph
            else:
                buffer = candidate
        if buffer:
            result.append(buffer)
    return [item for item in result if len(item) >= 40]


def build_corpus(source_root: Path, corpus_root: Path) -> tuple[dict[str, list[Path]], dict[str, str]]:
    if corpus_root.exists():
        marker = corpus_root / ".benchmark-corpus"
        if not marker.is_file():
            raise RuntimeError(f"Refusing to replace unmarked directory: {corpus_root}")
        shutil.rmtree(corpus_root)
    corpus_root.mkdir(parents=True, exist_ok=True)
    (corpus_root / ".benchmark-corpus").write_text("generated\n", encoding="ascii")

    files_by_category: dict[str, list[Path]] = {category: [] for category in CATEGORIES}
    source_by_generated: dict[str, str] = {}
    serial = 0
    for category in CATEGORIES:
        source_dir = source_root / category
        if not source_dir.is_dir():
            raise FileNotFoundError(f"Missing knowledge category: {source_dir}")
        target_dir = corpus_root / category
        target_dir.mkdir(parents=True, exist_ok=True)
        for source in sorted(source_dir.glob("*.md")):
            sections = _split_markdown(source.read_text(encoding="utf-8"))
            for section_index, section in enumerate(sections, start=1):
                serial += 1
                generated = target_dir / (
                    f"{serial:03d}_{_safe_fragment(source.stem)}_{section_index:02d}.md"
                )
                generated.write_text(
                    f"来源文档：{source.name}\n资料类别：{category}\n\n{section}\n",
                    encoding="utf-8",
                )
                files_by_category[category].append(generated)
                source_by_generated[generated.name] = source.name

    file_count = sum(len(files) for files in files_by_category.values())
    if file_count < 40:
        raise RuntimeError(f"Benchmark corpus only contains {file_count} files; at least 40 are required.")
    return files_by_category, source_by_generated


def _evaluate(
    cases: tuple[QueryCase, ...],
    files_by_category: dict[str, list[Path]],
    source_by_generated: dict[str, str],
    *,
    client,
    vector_min_files: int,
) -> dict:
    details = []
    reciprocal_ranks: list[float] = []
    top1_hits = 0
    top3_hits = 0
    latencies: list[float] = []
    for case in cases:
        started = time.perf_counter()
        results = search_knowledge(
            case.question,
            files_by_category,
            top_k=3,
            embedding_client=client,
            vector_min_files=vector_min_files,
        )
        elapsed = time.perf_counter() - started
        latencies.append(elapsed)
        ranked_sources = [source_by_generated.get(item.source_file, item.source_file) for item in results]
        try:
            rank = ranked_sources.index(case.expected_source) + 1
        except ValueError:
            rank = None
        top1_hits += int(rank == 1)
        top3_hits += int(rank is not None and rank <= 3)
        reciprocal_ranks.append(1.0 / rank if rank else 0.0)
        details.append(
            {
                "question": case.question,
                "expected_source": case.expected_source,
                "rank": rank,
                "latency_seconds": round(elapsed, 4),
                "results": [
                    {
                        "source": source_by_generated.get(item.source_file, item.source_file),
                        "generated_file": item.source_file,
                        "score": item.score,
                    }
                    for item in results
                ],
            }
        )
    count = len(cases)
    return {
        "query_count": count,
        "top1_hit_rate": round(top1_hits / count, 4),
        "top3_hit_rate": round(top3_hits / count, 4),
        "mrr": round(sum(reciprocal_ranks) / count, 4),
        "average_latency_seconds": round(sum(latencies) / count, 4),
        "details": details,
    }


def _write_markdown(result: dict, path: Path) -> None:
    keyword = result["keyword"]
    hybrid = result["hybrid"]
    lines = [
        "# 知识检索基准结果",
        "",
        f"- 语料文件数：{result['corpus_file_count']}",
        f"- 原始知识文档数：{result['source_document_count']}",
        f"- 向量模型：{result['embedding_model']}",
        f"- 向量维度：{result['embedding_dimensions']}",
        "",
        "| 模式 | Top-1 | Top-3 | MRR | 平均耗时 |",
        "| --- | ---: | ---: | ---: | ---: |",
        (
            f"| 关键词 | {keyword['top1_hit_rate']:.1%} | {keyword['top3_hit_rate']:.1%} | "
            f"{keyword['mrr']:.3f} | {keyword['average_latency_seconds']:.3f} 秒 |"
        ),
        (
            f"| 混合检索 | {hybrid['top1_hit_rate']:.1%} | {hybrid['top3_hit_rate']:.1%} | "
            f"{hybrid['mrr']:.3f} | {hybrid['average_latency_seconds']:.3f} 秒 |"
        ),
        "",
        f"首次向量建库及首问耗时：{result['hybrid_first_query_seconds']:.3f} 秒。",
        f"向量缓存后的重复问句耗时：{result['hybrid_cached_query_seconds']:.3f} 秒。",
        (
            f"文档向量请求 {result['embedding_document_requests']} 次，"
            f"共处理 {result['embedding_document_texts']} 个文本块。"
        ),
        "",
        "## 问句明细",
        "",
        "| 问句 | 关键词排名 | 混合排名 | 目标资料 |",
        "| --- | ---: | ---: | --- |",
    ]
    for keyword_item, hybrid_item in zip(keyword["details"], hybrid["details"], strict=True):
        keyword_rank = keyword_item["rank"] or "未命中"
        hybrid_rank = hybrid_item["rank"] or "未命中"
        lines.append(
            f"| {keyword_item['question']} | {keyword_rank} | {hybrid_rank} | "
            f"{keyword_item['expected_source']} |"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    default_source = Path(r"D:\灵坤\投标文件\湖北灵坤建筑修缮_模拟RAG知识库")
    parser = argparse.ArgumentParser(description="Benchmark keyword and hybrid knowledge retrieval.")
    parser.add_argument("--source-root", type=Path, default=default_source)
    parser.add_argument("--work-dir", type=Path, default=Path("tmp/retrieval_benchmark"))
    parser.add_argument("--base-url", default="http://127.0.0.1:11435/v1")
    parser.add_argument("--api-key", default="embedding")
    parser.add_argument("--model", default="qwen3-embedding:0.6b")
    parser.add_argument("--batch-size", type=int, default=16)
    args = parser.parse_args()

    args.work_dir.mkdir(parents=True, exist_ok=True)
    files_by_category, source_by_generated = build_corpus(
        args.source_root.resolve(),
        (args.work_dir / "corpus").resolve(),
    )
    source_document_count = sum(len(list((args.source_root / category).glob("*.md"))) for category in CATEGORIES)
    corpus_file_count = sum(len(files) for files in files_by_category.values())

    clear_knowledge_cache()
    search_knowledge(
        "benchmark warmup",
        files_by_category,
        top_k=1,
        embedding_client=KeywordOnlyClient(),
        vector_min_files=10**9,
    )
    keyword = _evaluate(
        QUERY_CASES,
        files_by_category,
        source_by_generated,
        client=KeywordOnlyClient(),
        vector_min_files=10**9,
    )

    inner = OpenAICompatibleEmbeddingClient(
        args.base_url,
        args.api_key,
        args.model,
        timeout=180,
        batch_size=args.batch_size,
        query_instruction=QUERY_INSTRUCTION,
    )
    client = CountingEmbeddingClient(inner)
    hybrid = _evaluate(
        QUERY_CASES,
        files_by_category,
        source_by_generated,
        client=client,
        vector_min_files=40,
    )
    cached_started = time.perf_counter()
    search_knowledge(
        QUERY_CASES[0].question,
        files_by_category,
        top_k=3,
        embedding_client=client,
        vector_min_files=40,
    )
    cached_elapsed = time.perf_counter() - cached_started

    probe = inner.embed(["dimension probe"])[0]
    result = {
        "source_root": str(args.source_root.resolve()),
        "source_document_count": source_document_count,
        "corpus_file_count": corpus_file_count,
        "embedding_model": args.model,
        "embedding_dimensions": len(probe),
        "embedding_document_requests": client.document_requests,
        "embedding_document_texts": client.document_texts,
        "embedding_query_requests": client.query_requests,
        "hybrid_first_query_seconds": hybrid["details"][0]["latency_seconds"],
        "hybrid_cached_query_seconds": round(cached_elapsed, 4),
        "keyword": keyword,
        "hybrid": hybrid,
    }
    json_path = args.work_dir / "retrieval_benchmark.json"
    markdown_path = args.work_dir / "retrieval_benchmark.md"
    json_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_markdown(result, markdown_path)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
