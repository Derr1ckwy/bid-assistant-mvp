# 投标初稿助手

面向单人开发和小范围试用的投标文件初稿生成 MVP。系统将招标文件解析、要求提取、人工确认、企业资料检索、章节草稿生成和 Word 导出串成一条可复核流程。

当前版本的首要目标不是“一键生成最终标书”，而是把最耗时、最容易遗漏的整理工作变成可检查的半自动流程。

## 已实现

- 新建和管理本地投标项目
- 解析 PDF、DOCX、TXT、Markdown
- 规则模式提取项目信息、强制要求、评分项、资格、材料、时间节点和废标风险
- 可选的 OpenAI 兼容 LLM 分析与章节生成，长文档自动分段并合并结果
- 对分析结果和章节目录进行人工编辑确认
- 管理企业资料、产品资料、历史方案三类项目知识
- 带文件变更缓存的轻量关键词检索和资料引用
- 自动检查遗漏项、未确认条款、废标风险、缺失章节和正文占位符
- 生成带封面、章节、页眉页脚、页码、分析附录及复核报告的 Word 初稿
- GitHub Actions 自动执行编译和测试

## 暂不包含

- 扫描件 OCR，只进行扫描件风险提示
- 多用户、角色权限、在线协同编辑
- 任意甲方 Word 模板的像素级还原
- 自动盖章、签名、图片生成和外部数据爬取
- 无人工确认的一键最终投标

## 快速启动

在 PowerShell 中进入项目目录：

```powershell
Set-Location -LiteralPath 'D:\灵坤\投标系统'
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
.\run.ps1
```

浏览器访问 `http://127.0.0.1:8501`。

不配置模型也能使用规则模式。需要 LLM 时，在 `.env` 中填写 OpenAI 兼容接口；默认示例指向本机 Ollama。

## 测试

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m compileall -q app.py bid_assistant
```

## 数据位置

默认业务数据写入 `D:\灵坤\投标系统\data`，该目录已被 Git 忽略。每个项目的源文件、分析 JSON、复核报告、知识资料和导出文件都放在自己的项目目录中。

详细架构、路径和单人开发路线见 [实施方案与项目路径.md](./实施方案与项目路径.md)。
