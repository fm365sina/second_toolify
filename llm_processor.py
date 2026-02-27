from __future__ import annotations

import json
import re
from typing import Any

from openai import OpenAI
from tenacity import retry, stop_after_attempt, wait_exponential

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


def _build_user_prompt(selected_data: dict[str, Any], report_date: str) -> str:
    compact_tools = []
    for item in selected_data.get("llm_input_tools", []):
        compact_tools.append(
            {
                "name": item.get("name", ""),
                "tag": item.get("tag", ""),
                "description": item.get("description", ""),
                "rank": item.get("rank", 0),
                "rank_change": item.get("rank_change", ""),
                "source_page": item.get("source_page", ""),
                "visits": item.get("visits", ""),
                "score": item.get("score", {}),
            }
        )

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
        max_tokens=4096,
    )

    text = response.choices[0].message.content or ""
    text = text.strip()
    if not text:
        raise RuntimeError("DeepSeek returned empty content")
    return text


def generate_daily_report(
    selected_data: dict[str, Any],
    api_key: str,
    model_name: str,
    report_date: str,
    base_url: str = "https://api.deepseek.com/v1",
) -> str:
    client = OpenAI(api_key=api_key, base_url=base_url)

    prompt = _build_user_prompt(selected_data=selected_data, report_date=report_date)
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
        "原始输入数据如下：\n"
        f"{prompt}"
    )

    markdown_retry = _generate_once(client, model_name, correction_prompt)
    ok_retry, retry_errors = _validate_output(markdown_retry)
    if not ok_retry:
        raise RuntimeError(f"LLM output validation failed after retry: {'; '.join(retry_errors)}")

    return markdown_retry
