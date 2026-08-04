from bid_assistant.analyzer import analyze_document
from bid_assistant.models import ParsedDocument, ParsedPage


def test_rule_analysis_extracts_key_items() -> None:
    text = """项目名称：智慧园区平台项目
采购人：测试采购单位
采购代理机构：测试代理机构
预算金额：180 万元
投标截止时间：2026 年 9 月 15 日 09:30
投标人必须完整响应实质性条款，不得重大偏离。
项目经理应具有信息系统项目管理师证书。
须提供营业执照复印件和授权书证明材料。
技术方案评分标准：满分 20 分。
未提交证明材料的投标文件将被否决。
"""
    document = ParsedDocument(
        filename="tender.txt",
        file_type="txt",
        pages=[ParsedPage(page_number=1, text=text)],
        full_text=text,
        char_count=len(text),
    )

    analysis = analyze_document(document)

    assert analysis.analysis_mode == "rules"
    assert analysis.project_info.project_name == "智慧园区平台项目"
    assert analysis.project_info.purchaser == "测试采购单位"
    assert analysis.project_info.budget == "180 万元"
    assert analysis.project_info.bid_deadline.startswith("2026 年 9 月 15 日")
    assert analysis.mandatory_requirements
    assert analysis.qualification_requirements
    assert analysis.required_documents
    assert analysis.scoring_items[0].points == "20"
    assert analysis.risks
    assert len(analysis.outline) == 6


class FakeChunkClient:
    def __init__(self) -> None:
        self.calls = 0

    def chat_json(self, messages: list[dict[str, str]]) -> dict:
        self.calls += 1
        if self.calls == 1:
            return {
                "project_info": {"project_name": "长文档测试项目", "purchaser": "测试采购人"},
                "mandatory_requirements": [
                    {
                        "content": "投标人必须提交营业执照。",
                        "source_page": 1,
                        "source_quote": "投标人必须提交营业执照。",
                        "confidence": 0.9,
                        "status": "待确认",
                    }
                ],
            }
        return {
            "scoring_items": [
                {
                    "criterion": "技术方案评分标准：20 分。",
                    "points": "20",
                    "source_page": 2,
                    "source_quote": "技术方案评分标准：20 分。",
                    "confidence": 0.9,
                    "status": "待确认",
                }
            ]
        }


def test_llm_analysis_splits_and_merges_long_document() -> None:
    page_one = "项目名称：长文档测试项目\n投标人必须提交营业执照。\n" + "甲" * 30000
    page_two = "技术方案评分标准：20 分。\n" + "乙" * 30000
    document = ParsedDocument(
        filename="long.pdf",
        file_type="pdf",
        pages=[
            ParsedPage(page_number=1, text=page_one),
            ParsedPage(page_number=2, text=page_two),
        ],
        full_text=f"[第 1 页]\n{page_one}\n\n[第 2 页]\n{page_two}",
        char_count=len(page_one) + len(page_two),
    )
    client = FakeChunkClient()

    analysis = analyze_document(document, client, use_llm=True)

    assert client.calls == 2
    assert analysis.analysis_mode == "llm_chunked"
    assert analysis.project_info.project_name == "长文档测试项目"
    assert len(analysis.mandatory_requirements) == 1
    assert len(analysis.scoring_items) == 1
    assert any("分 2 段分析" in warning for warning in analysis.warnings)


class LooseTypeClient:
    def chat_json(self, messages: list[dict[str, str]]) -> dict:
        return {
            "project_info": {"project_name": "结构化输出兼容测试"},
            "mandatory_requirements": [
                {
                    "content": "投标人必须提交营业执照。",
                    "source_page": "第 1 页",
                    "source_quote": "投标人必须提交营业执照。",
                    "confidence": "高",
                    "status": "确认",
                }
            ],
            "scoring_items": [
                {
                    "criterion": "技术方案评分 30 分。",
                    "points": 30,
                    "source_page": "1",
                    "source_quote": "技术方案评分 30 分。",
                    "confidence": "80%",
                    "status": "待确认",
                }
            ],
        }


def test_llm_analysis_normalizes_common_small_model_types() -> None:
    text = "投标人必须提交营业执照。\n技术方案评分 30 分。"
    document = ParsedDocument(
        filename="loose-types.txt",
        file_type="txt",
        pages=[ParsedPage(page_number=1, text=text)],
        full_text=text,
        char_count=len(text),
    )

    analysis = analyze_document(document, LooseTypeClient(), use_llm=True)

    assert analysis.analysis_mode == "llm"
    assert analysis.mandatory_requirements[0].confidence == 0.9
    assert analysis.mandatory_requirements[0].status == "待确认"
    assert analysis.mandatory_requirements[0].source_page == 1
    assert analysis.scoring_items[0].points == "30"
    assert analysis.scoring_items[0].confidence == 0.8
