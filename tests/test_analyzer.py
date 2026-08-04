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
