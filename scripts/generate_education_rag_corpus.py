from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path


DEFAULT_OUTPUT = Path(r"D:\灵坤\投标文件\教育培训_模拟RAG知识库")

CORPUS: dict[str, list[dict[str, str]]] = {
    "company": [
        {"title": "公司概况与教育培训服务定位", "focus": "面向企业、学校和公共机构提供课程研发、讲师交付、学习平台和培训运营服务", "keywords": "企业培训 课程研发 教育服务 组织学习"},
        {"title": "组织架构与项目治理", "focus": "项目经理、教研负责人、交付负责人、客户成功和质量监督岗位的协作边界", "keywords": "组织架构 项目治理 项目经理 责任矩阵"},
        {"title": "教研团队与课程开发能力", "focus": "需求调研、学习目标设计、课程脚本、试讲评审和版本管理", "keywords": "教研 课程开发 学习目标 试讲评审"},
        {"title": "讲师管理与授课质量", "focus": "讲师准入、试讲、排课、授课评价、复训和替补讲师机制", "keywords": "讲师管理 授课评价 讲师准入 排课"},
        {"title": "学员服务与班级运营", "focus": "报名、通知、签到、班主任服务、作业提醒和结业材料管理", "keywords": "学员服务 班级运营 签到 班主任"},
        {"title": "培训项目质量管理体系", "focus": "培训计划评审、交付检查、满意度回收、问题整改和项目复盘", "keywords": "质量管理 培训交付 满意度 整改闭环"},
        {"title": "信息安全与个人信息保护", "focus": "学员信息、成绩、录音录像、企业资料和账号权限的最小化管理", "keywords": "信息安全 个人信息 权限 数据留存"},
        {"title": "未成年人培训保护制度", "focus": "未成年人报名、监护人授权、课堂安全、影像使用和异常事件上报", "keywords": "未成年人 监护人授权 课堂安全 事件上报"},
        {"title": "教学场地与设备保障", "focus": "教室容量、网络、投影、录播、消防通道和设备巡检安排", "keywords": "教学场地 设备巡检 录播 网络 消防"},
        {"title": "财务采购与供应商管理", "focus": "讲师采购、教材采购、场地租赁、费用审批和供应商评价", "keywords": "采购管理 供应商 费用审批 教材采购"},
        {"title": "课程审核与内容合规", "focus": "课程内容审校、版权来源、宣传用语、行业规范和敏感内容复核", "keywords": "课程审核 内容合规 版权 审校"},
        {"title": "培训活动安全与应急管理", "focus": "大型培训活动的风险识别、应急联系人、疏散预案和事故记录", "keywords": "培训安全 应急预案 疏散 风险识别"},
        {"title": "绿色低碳培训运营", "focus": "电子资料、低碳会务、场地节能、交通提示和绿色采购建议", "keywords": "绿色培训 低碳会务 节能 电子资料"},
        {"title": "校企合作项目管理", "focus": "学校、企业、培训机构三方的目标确认、实训安排、导师协作和评价", "keywords": "校企合作 实训 三方协作 就业"},
        {"title": "培训数据分析与持续改进", "focus": "报名率、到课率、完课率、测评提升、满意度和转化结果的复盘", "keywords": "培训数据 到课率 完课率 学习分析"},
    ],
    "product": [
        {"title": "企业内训课程体系", "focus": "通用管理、岗位技能、文化融入和合规安全四类企业内训课程组合", "keywords": "企业内训 管理课程 岗位技能 合规"},
        {"title": "公开课与专题研修", "focus": "面向多个组织的公开课、专题班、报名管理和学习成果输出", "keywords": "公开课 专题研修 报名 学习成果"},
        {"title": "校园职业素养课程", "focus": "面向高校和职业院校的职业规划、沟通协作、就业能力和职业道德", "keywords": "校园培训 职业素养 就业能力 职业道德"},
        {"title": "管理者训练营", "focus": "基层主管、项目负责人和中层管理者的目标管理、反馈、授权与复盘", "keywords": "管理者训练营 目标管理 授权 反馈"},
        {"title": "新员工入职培训", "focus": "企业介绍、岗位认知、制度流程、安全要求、导师带教和试用期任务", "keywords": "新员工 入职培训 岗位认知 导师带教"},
        {"title": "安全生产与应急培训", "focus": "风险辨识、作业许可、应急处置、事故预防和现场演练课程", "keywords": "安全生产 应急培训 风险辨识 现场演练"},
        {"title": "数字化学习平台", "focus": "课程发布、学习任务、考试、通知、数据看板和学习档案管理", "keywords": "学习平台 课程发布 数据看板 学习档案"},
        {"title": "直播与录播混合教学", "focus": "直播授课、录播微课、回放、互动问答和课后资料的组合模式", "keywords": "直播教学 录播 混合式学习 回放"},
        {"title": "岗位学习地图", "focus": "按岗位阶段建立入门、胜任、提升和专家级学习路径", "keywords": "学习地图 岗位能力 胜任力 学习路径"},
        {"title": "考试测评与证书管理", "focus": "题库组卷、随机抽题、阅卷、补考、成绩发布和培训证明", "keywords": "考试测评 题库 组卷 补考 证书"},
        {"title": "题库作业与案例研讨", "focus": "章节作业、案例讨论、实践任务、同伴互评和教师点评", "keywords": "题库 作业 案例研讨 同伴互评"},
        {"title": "教学督导与课堂观察", "focus": "听课表、课堂观察、讲师反馈、改进建议和跟踪验证", "keywords": "教学督导 课堂观察 听课表 讲师反馈"},
        {"title": "实训营与项目制学习", "focus": "以真实任务为主线的分组实践、阶段评审、成果展示和答辩", "keywords": "实训营 项目制学习 分组实践 成果答辩"},
        {"title": "课程定制与内容共创", "focus": "依据岗位场景、企业制度和学员画像定制案例、练习与评价标准", "keywords": "课程定制 内容共创 学员画像 企业案例"},
        {"title": "培训效果评估方案", "focus": "从反应、学习、行为和业务结果四个层次设计评估与回访", "keywords": "培训效果 评估 回访 业务结果"},
    ],
    "history": [
        {"title": "制造业新员工入职培训案例", "focus": "模拟制造企业的新员工集中培训、岗位实操和导师带教复盘", "keywords": "制造业 新员工 入职培训 导师"},
        {"title": "教育主管部门教师研修案例", "focus": "模拟区域教师数字化教学研修的需求调研、分层教学和成果展示", "keywords": "教师研修 数字化教学 区域教育 成果展示"},
        {"title": "高职院校实训基地共建案例", "focus": "模拟学校与企业共建实训基地的课程、设备、师资和安全管理", "keywords": "高职 实训基地 校企共建 设备"},
        {"title": "企业管理者训练营案例", "focus": "模拟中层管理者训练营的行动学习、教练辅导和结业答辩", "keywords": "管理者训练营 行动学习 教练辅导 答辩"},
        {"title": "安全生产专项培训案例", "focus": "模拟化工园区安全生产培训的风险识别、演练和考试闭环", "keywords": "安全生产 园区培训 风险识别 演练"},
        {"title": "制造企业数字化转型培训案例", "focus": "模拟生产、质量和设备团队的数字化工具应用培训", "keywords": "数字化转型 生产质量 设备 工具应用"},
        {"title": "线上线下混合学习案例", "focus": "模拟连锁企业使用直播、微课、线下辅导和线上考试的组合交付", "keywords": "混合学习 直播 微课 线上考试"},
        {"title": "乡村振兴职业技能培训案例", "focus": "模拟县域职业技能培训的需求摸排、分班、实操和就业跟踪", "keywords": "乡村振兴 职业技能 县域培训 就业"},
        {"title": "公共机构应急演练培训案例", "focus": "模拟公共机构开展消防、疏散、急救和信息报告综合演练", "keywords": "应急演练 消防 疏散 急救"},
        {"title": "客户服务能力提升案例", "focus": "模拟服务中心围绕沟通、投诉处理、知识库和质检开展培训", "keywords": "客户服务 投诉处理 知识库 质检"},
        {"title": "质量管理体系内训案例", "focus": "模拟企业围绕质量意识、流程管理、问题分析和纠正预防开展内训", "keywords": "质量管理 内训 流程 问题分析"},
        {"title": "校企双导师项目案例", "focus": "模拟职业院校与企业共同指导学生完成岗位项目和评价", "keywords": "校企合作 双导师 岗位项目 学生评价"},
        {"title": "班主任能力提升案例", "focus": "模拟培训机构班主任围绕班级管理、沟通和风险识别开展训练", "keywords": "班主任 班级管理 沟通 风险识别"},
        {"title": "供应链岗位技能培训案例", "focus": "模拟物流与采购团队开展计划、库存、供应商和协同培训", "keywords": "供应链 物流 采购 库存"},
        {"title": "客户成功复盘与续训案例", "focus": "模拟培训项目通过学习数据、访谈和业务指标推动续训优化", "keywords": "客户成功 复盘 续训 学习数据"},
    ],
}


def _document(category: str, index: int, item: dict[str, str]) -> str:
    category_label = {"company": "企业资料", "product": "产品资料", "history": "历史方案"}[category]
    return f"""# {item['title']}

> 资料类别：{category_label}  
> 数据性质：模拟教育培训知识库，仅用于本地 RAG、检索和 Word 生成测试，不代表任何真实机构、客户、合同或资质。

## 资料定位

本文件围绕“{item['focus']}”整理。适合用于教育、培训、职业技能、校企合作和企业学习类投标文件的章节检索。引用本资料时应结合真实项目的招标要求、企业证明材料和客户确认结果，不得把模拟内容直接写入正式投标文件。

## 核心信息（演示口径）

- 适用场景：教育培训项目的需求分析、课程设计、培训实施、学习支持和效果评估。
- 建议角色：项目经理、教研负责人、主讲教师、班主任、技术支持和质量督导。
- 交付方式：可组合采用集中面授、线上直播、录播微课、实训任务、案例研讨和阶段测评。
- 过程记录：保存培训计划、签到记录、学习任务、作业或考试结果、满意度和改进事项。
- 关键词：{item['keywords']}。

## 实施要点

1. 先确认培训对象、岗位场景、学习基础、时间窗口和合规边界，再确定课程目标。
2. 将课程拆成“目标—内容—练习—评价—改进”闭环，避免只描述授课时长而没有学习产出。
3. 对涉及个人信息、未成年人、录音录像、证书和考试结果的环节设置权限、授权和留存规则。
4. 交付过程中保留问题清单和阶段复盘记录；发生延期、到课率下降或设备故障时及时调整计划。

## 示例指标（仅供测试）

- 计划完成率示例目标：不低于 90%。
- 学员到课率示例目标：不低于 85%。
- 课程满意度示例目标：不低于 4.5/5 分。
- 重点岗位课后测评通过率示例目标：不低于 80%。

以上数值是模拟检索数据，不是企业承诺。真实项目必须根据合同、招标评分办法和客户确认口径重新填写。

## 可引用表达（需人工核验）

可将本资料转化为“围绕培训目标建立课程体系，采用线上线下结合的方式组织学习，通过签到、作业、测评、课堂观察和满意度回收形成质量闭环”的初稿表达。涉及具体人数、课时、讲师、平台功能、案例名称和效果数据时必须替换为真实证据。

## 检索提示

建议问句：{item['keywords']}；{item['focus']}怎么组织；教育培训项目如何做过程管理；如何证明培训效果；如何设置风险和应急措施。
"""


def build_corpus(output: Path, *, overwrite: bool = False) -> dict[str, int]:
    marker = output / ".generated-education-rag"
    if output.exists() and any(output.iterdir()) and not (overwrite and marker.is_file()):
        raise RuntimeError(f"目标目录非空或不是本脚本生成目录，拒绝覆盖：{output}")
    output.mkdir(parents=True, exist_ok=True)
    counts: dict[str, int] = {}
    index_rows: list[dict[str, str]] = []
    fact_rows: list[dict[str, str]] = []
    for category, items in CORPUS.items():
        category_dir = output / category
        category_dir.mkdir(parents=True, exist_ok=True)
        counts[category] = len(items)
        for number, item in enumerate(items, start=1):
            filename = f"{number:02d}_{item['title']}.md"
            path = category_dir / filename
            path.write_text(_document(category, number, item), encoding="utf-8")
            index_rows.append(
                {
                    "category": category,
                    "category_label": {"company": "企业资料", "product": "产品资料", "history": "历史方案"}[category],
                    "filename": filename,
                    "title": item["title"],
                    "keywords": item["keywords"],
                    "synthetic": "是",
                }
            )
            fact_rows.append(
                {
                    "id": f"EDU-{category[:1].upper()}-{number:02d}",
                    "category": category,
                    "title": item["title"],
                    "fact": item["focus"],
                    "status": "模拟，需真实资料替换",
                }
            )

    (output / "00_知识库使用说明.md").write_text(
        """# 教育培训模拟 RAG 知识库\n\n本目录包含 45 份教育培训主题的模拟资料：企业资料、产品资料、历史方案各 15 份。\n\n所有内容用于验证上传、CSV/JSON/Markdown 解析、关键词检索、向量检索、章节生成和 Word 排版，不代表真实企业能力、项目业绩、客户名称、合同金额、讲师资格或培训效果。\n\n建议先在系统中建立“教育培训模拟测试项目”，再将三个子目录分别上传到对应类别。知识文件数量达到 40 份后，可配置 Embedding 服务测试混合检索。\n\n## 推荐测试问句\n\n- 如何设计企业新员工入职培训？\n- 培训项目如何进行签到、作业和考试闭环？\n- 校企合作实训基地需要哪些管理机制？\n- 如何保护学员个人信息和未成年人信息？\n- 如何用学习数据和满意度评估培训效果？\n- 线上直播和录播微课如何组合？\n- 安全生产培训如何组织现场演练？\n\n真实投标前，请用企业制度、人员证书、课程样本、平台截图、合同或验收材料替换本目录中的模拟内容。\n""",
        encoding="utf-8",
    )
    with (output / "知识库文档索引.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["category", "category_label", "filename", "title", "keywords", "synthetic"])
        writer.writeheader()
        writer.writerows(index_rows)
    (output / "RAG事实卡片.json").write_text(
        json.dumps(fact_rows, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    legacy_jsonl = output / "RAG事实卡片.jsonl"
    if legacy_jsonl.is_file():
        legacy_jsonl.unlink()
    marker.write_text("generated by scripts/generate_education_rag_corpus.py\n", encoding="ascii")
    return counts


def main() -> int:
    parser = argparse.ArgumentParser(description="生成教育培训主题的模拟 RAG 知识库")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()
    counts = build_corpus(args.output, overwrite=args.overwrite)
    print(f"已生成教育培训模拟知识库：{args.output}")
    print("；".join(f"{category} {count} 份" for category, count in counts.items()))
    print(f"总计 {sum(counts.values())} 份 Markdown，另含 CSV 索引、JSON 事实卡片和使用说明")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
