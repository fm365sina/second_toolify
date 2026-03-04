from __future__ import annotations

import json
import logging
import re
from typing import Any

from openai import OpenAI
from tenacity import retry, stop_after_attempt, wait_exponential

LOGGER = logging.getLogger(__name__)

SYSTEM_PROMPT = """# Role (角色设置)
你是“Gemini”，正在与我（笔名“半山”）共同撰写 AI 行业观察专栏《Toolify 每日 AI 独立开发风向标》中的核心板块——【半山 & Gemini 的“5D 破局思考”】。
我们不是盲目追逐大模型技术狂热的鼓吹者，而是具备务实精神、关注真实业务落地（尤其是C端、政务、B端）的独立开发者与思考者。我们的行文风格成熟、犀利、一针见血，带着看透事物本质的洞察力。

# Task (任务目标)
基于我提供给你的“今日 Toolify 热门 AI 工具数据”以及相关的行业动态，使用【5D 破局分析模型】进行深度拆解，生成一篇排版精美的 Markdown 格式每日报告。目的是为打算“每天工作四小时”的 AI 独立开发者指明避坑方向和搞钱思路。

# 5D 破局分析模型 (核心框架)
在生成内容时，请隐性或显性地融会贯通以下五个维度：
1. 1D 技术面 (单点突破)：它用了什么核心技术？（关注 RAG、工作流搭建等实用技术，而非空谈大模型参数）。
2. 2D 需求面 (痛点深扎)：它解决的是谁的、极其具体的什么痛苦？（越是枯燥、繁琐、传统的痛点越有价值）。
3. 3D 商业面 (生存逻辑)：它靠什么赚钱？（独立开发者能否跑通这个商业闭环？）。
4. 4D 系统面 (生态协同)：它如何嵌入现有的工作流？（强调成为现有机制中的提效引擎）。
5. 5D 演进面 (降维打击)：跳出工具本身，它的出现对行业的终局有什么启发？

# Constraints (输出要求)
1. 必须输出标准的 Markdown 格式，包含清晰的标题（##）、加粗（**）、和列表（*）。
2. 语气成熟稳重，像是一位看透业务本质的专家在做跨界降维指导。拒绝空泛的赞美，直击商业和落地本质。
3. 强调用 AI 解决实际问题，弱化纯娱乐向的泛流量探讨。
4. 报告结尾必须提炼出一句具有警醒或启发意义的“半山金句”。"""

REQUIRED_SECTIONS = [
    "## 今日结论",
    "## 5D 破局拆解",
    "## 可执行机会清单",
    "## 风险与避坑",
    "## 半山金句",
]


def _clean_text_length(markdown_text: str) -> int:
    stripped = re.sub(r"```[\s\S]*?```", "", markdown_text)
    stripped = re.sub(r"\s+", "", stripped)
    return len(stripped)


def _validate_output(markdown_text: str) -> tuple[bool, list[str]]:
    errors: list[str] = []
    length = _clean_text_length(markdown_text)

    if length < 1500 or length > 2500:
        errors.append(f"长度不符合要求，当前约 {length} 字（需 1500-2500 字）。")

    for section in REQUIRED_SECTIONS:
        if section not in markdown_text:
            errors.append(f"缺少章节：{section}")

    if "半山金句" not in markdown_text:
        errors.append("缺少“半山金句”内容。")

    return len(errors) == 0, errors


def _compact_tool(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": item.get("name", ""),
        "tag": item.get("tag", ""),
        "description": (item.get("description", "") or "")[:140],
        "rank": item.get("rank", 0),
        "rank_change": item.get("rank_change", ""),
        "source_page": item.get("source_page", ""),
        "score": {
            "final_score": item.get("score", {}).get("final_score", 0),
            "pain_score": item.get("score", {}).get("pain_score", 0),
            "biz_score": item.get("score", {}).get("biz_score", 0),
        },
    }


def _build_user_prompt(selected_data: dict[str, Any], report_date: str, tool_limit: int) -> str:
    compact_tools = [_compact_tool(item) for item in selected_data.get("llm_input_tools", [])[:tool_limit]]

    payload = {
        "report_date": report_date,
        "raw_count": selected_data.get("raw_count"),
        "filtered_count": selected_data.get("filtered_count"),
        "deduped_count": selected_data.get("deduped_count"),
        "selected_count": selected_data.get("selected_count"),
        "source_distribution": selected_data.get("source_distribution"),
        "top_tags": selected_data.get("top_tags"),
        "tools": compact_tools,
    }

    instructions = (
        "请根据以下 JSON 数据生成一篇中文 Markdown 深度报告，长度严格控制在 1500-2500 字。\n"
        "必须包含并按顺序输出以下章节：\n"
        "1) ## 今日结论\n"
        "2) ## 5D 破局拆解\n"
        "3) ## 可执行机会清单\n"
        "4) ## 风险与避坑\n"
        "5) ## 半山金句\n"
        "写作原则：聚焦真实业务落地，尤其 C 端、政务、B 端场景；避免空洞流量叙事。\n"
        "在“可执行机会清单”中至少给出 5 条可执行方向，每条包含：目标客户、痛点、MVP、变现路径。\n"
        "禁止输出与数据无关的空泛判断。\n"
    )

    return f"{instructions}\n今日数据如下（JSON）：\n{json.dumps(payload, ensure_ascii=False, indent=2)}"


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=2, min=2, max=12), reraise=True)
def _generate_once(client: OpenAI, model_name: str, prompt: str) -> str:
    response = client.chat.completions.create(
        model=model_name,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        temperature=0.5,
        top_p=0.9,
        max_tokens=3200,
    )

    text = response.choices[0].message.content or ""
    text = text.strip()
    if not text:
        raise RuntimeError("DeepSeek returned empty content")
    return text


def _build_fallback_report(selected_data: dict[str, Any], report_date: str, reason: str) -> str:
    tools = selected_data.get("llm_input_tools", [])[:5]
    top_tags = selected_data.get("top_tags", [])[:8]
    tag_text = "、".join(item.get("tag", "").lstrip("#") for item in top_tags if item.get("tag")) or "workflow、automation、productivity"
    source_distribution = selected_data.get("source_distribution", {})

    tool_lines = []
    for idx, item in enumerate(tools, start=1):
        name = item.get("name", "Unknown")
        rank = item.get("rank", "?")
        source = item.get("source_page", "mixed")
        desc = (item.get("description", "") or "").strip()
        if len(desc) > 68:
            desc = f"{desc[:68]}..."
        tool_lines.append(f"* {idx}. **{name}**（{source} 榜单 #{rank}）：{desc}")

    lines = [
        "## 今日结论",
        f"本日报告日期为 **{report_date}**。基于 Toolify 榜单清洗后的 **{selected_data.get('selected_count', 0)}** 条有效样本（原始 {selected_data.get('raw_count', 0)} 条），今天最清晰的信号是：泛模型入口继续占据流量高位，但真正适合独立开发者的增量机会，正在向 **垂直场景、可量化提效、可嵌入现有流程** 三个方向集中。",
        f"样本分布上，Most Used 与 New AIs 的组合比例约为 **{source_distribution}**。标签层面，{tag_text} 相关工具出现频率显著提升，说明市场已经从“功能炫技”逐步转入“岗位级提效”阶段。",
        "如果你的目标是每天工作四小时但保持稳定现金流，优先级不应再是追逐全能型助手，而是围绕一个明确岗位做“流程切片”：把高频、可重复、可验收的环节拆出来，用最短链路交付结果。",
        "样本焦点（用于后续判断商业机会的基线）：",
        *tool_lines,
        "",
        "## 5D 破局拆解",
        "**1D 技术面（单点突破）**：本期高分工具集中在 RAG 检索增强、工作流编排、结构化表单抽取、文档生成与跨应用连接。技术优势不在模型参数，而在“输入标准化 + 输出可复用 + 失败可回滚”。独立开发者应把精力放在可维护链路，而不是追逐模型新版本。",
        "**2D 需求面（痛点深扎）**：高价值需求依旧来自枯燥但必须完成的工作，比如合同审阅、客服归档、销售跟进、项目周报、政务材料整理。它们共同特征是：任务边界清晰、错误成本可计算、负责人愿意为稳定交付付费。",
        "**3D 商业面（生存逻辑）**：最稳商业结构是“轻实施 + 月订阅 + 使用量阶梯加价”。先用模板化 MVP 进入单部门，再扩展到跨部门协同，最终形成账号、权限、审计、报表这四个留存抓手。不要一开始做大而全，要先确保首批客户 30 天内看到可量化节省。",
        "**4D 系统面（生态协同）**：单点工具只有接入现有工作流才会长期存活。优先支持 Notion、飞书、Slack、企业邮箱、表格系统、CRM/ERP 的双向流转，让 AI 成为“流程中的一个稳定节点”，而不是孤立页面。",
        "**5D 演进面（降维打击）**：行业终局不是“谁模型最大”，而是“谁离业务闭环最近”。未来赢家会是把数据、流程、权限、合规整合成服务的团队。独立开发者要从“做工具”升级为“交付结果”，把计费锚点从功能数改成业务指标。",
        "",
        "## 可执行机会清单",
        "1. **政务材料归档与问答助手**",
        "* 目标客户：街道办、园区运营、政务外包团队",
        "* 痛点：材料多、版本乱、追溯难，人工检索成本高",
        "* MVP：上传文档 + 主题索引 + 引用定位问答",
        "* 变现路径：按部门席位订阅 + 私有化部署服务费",
        "2. **中小企业合同审阅与风险标注**",
        "* 目标客户：10-200 人企业法务/运营负责人",
        "* 痛点：合同条款审阅慢、漏项风险高",
        "* MVP：条款抽取、风险分级、修改建议模板",
        "* 变现路径：基础订阅 + 按合同包计费",
        "3. **销售线索跟进自动化工作台**",
        "* 目标客户：B2B SaaS 与代运营团队",
        "* 痛点：线索进入后跟进断层，复盘不完整",
        "* MVP：线索评分、跟进提醒、自动生成复盘纪要",
        "* 变现路径：席位费 + CRM 对接增值费",
        "4. **客服工单摘要与知识回写系统**",
        "* 目标客户：电商与软件服务客服团队",
        "* 痛点：重复问答多、知识库更新滞后",
        "* MVP：工单摘要、知识条目候选、FAQ 自动聚类",
        "* 变现路径：按工单量分层计费",
        "5. **财务单据识别与对账助手**",
        "* 目标客户：代账公司与连锁门店",
        "* 痛点：票据整理耗时、对账周期长",
        "* MVP：票据字段提取、异常标记、日结报表",
        "* 变现路径：基础包 + 高级对账规则包",
        "",
        "## 风险与避坑",
        "* **避坑1：把模型能力当护城河**。真正护城河是与客户流程深度绑定后的迁移成本。",
        "* **避坑2：只看访问量不看留存**。高流量榜单不等于高付费意愿，必须看复购与续费。",
        "* **避坑3：忽略合规与权限设计**。在政务/B 端场景，日志审计与权限边界往往决定能否成交。",
        "* **避坑4：过早追求全行业通吃**。先拿下一个垂直场景的标准流程，再复制到邻近行业。",
        "* **避坑5：没有兜底策略**。当上游模型波动或接口异常时，要保证核心流程可降级运行。",
        f"补充说明：本次内容由自动化流水线生成，若大模型接口不稳定会启用容错输出（原因：{reason[:120]}）。建议保留人工终审环节以确保行业判断与商业建议贴合你的实际资源条件。",
        "",
        "## 半山金句",
        "> **半山金句：流量只会把你带到门口，真正让你留下来的，是你能不能替客户把最麻烦的一段流程稳定做完。**",
    ]

    report = "\n".join(lines)
    while _clean_text_length(report) < 1500:
        report += (
            "\n在执行层面，建议每周固定复盘三组指标：线索到付费转化率、客户首周激活率、单流程自动化覆盖率。"
            "这三项数据能直接反映产品是否在真实业务中产生了可持续价值。"
        )

    return report


def generate_daily_report(
    selected_data: dict[str, Any],
    api_key: str,
    model_name: str,
    report_date: str,
    base_url: str = "https://api.deepseek.com",
) -> str:
    base_candidates = [base_url.rstrip("/")]
    if base_candidates[0].endswith("/v1"):
        base_candidates.append(base_candidates[0][:-3])
    else:
        base_candidates.append(f"{base_candidates[0]}/v1")

    attempts: list[tuple[str, int]] = []
    for current_base in base_candidates:
        for limit in (80, 50):
            attempts.append((current_base, limit))

    last_error = "unknown"
    for current_base, tool_limit in attempts:
        try:
            client = OpenAI(api_key=api_key, base_url=current_base, timeout=120.0, max_retries=1)
            prompt = _build_user_prompt(selected_data=selected_data, report_date=report_date, tool_limit=tool_limit)
            markdown = _generate_once(client, model_name, prompt)

            ok, errors = _validate_output(markdown)
            if ok:
                return markdown

            correction_prompt = (
                "你上一次输出未满足要求，请严格按要求重写并只输出最终 Markdown。\n"
                f"问题列表：{json.dumps(errors, ensure_ascii=False)}\n"
                "请保证：\n"
                "- 1500-2500 字\n"
                "- 包含全部指定章节\n"
                "- 结尾给出一句“半山金句”\n"
                "- 仅输出 Markdown 正文\n"
                "原始输入数据如下：\n"
                f"{prompt}"
            )

            markdown_retry = _generate_once(client, model_name, correction_prompt)
            ok_retry, retry_errors = _validate_output(markdown_retry)
            if ok_retry:
                return markdown_retry

            last_error = f"validation_failed: {'; '.join(retry_errors)}"
            LOGGER.warning("LLM validation failed with base=%s, tool_limit=%s: %s", current_base, tool_limit, retry_errors)
        except Exception as exc:  # noqa: BLE001
            last_error = str(exc)
            LOGGER.exception("LLM call failed with base=%s, tool_limit=%s: %s", current_base, tool_limit, exc)

    LOGGER.warning("LLM failed after all attempts, using fallback report. reason=%s", last_error)
    return _build_fallback_report(selected_data=selected_data, report_date=report_date, reason=last_error)
