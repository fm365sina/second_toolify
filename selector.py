from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable

NORMALIZE_RE = re.compile(r"[^a-z0-9]+")

PAIN_KEYWORDS = {
    "compliance",
    "contract",
    "invoice",
    "workflow",
    "automation",
    "crm",
    "erp",
    "document",
    "support",
    "service",
    "legal",
    "medical",
    "governance",
    "security",
    "audit",
    "finance",
    "data",
    "analysis",
    "knowledge",
    "rag",
}

BIZ_KEYWORDS = {
    "api",
    "saas",
    "enterprise",
    "team",
    "b2b",
    "subscription",
    "platform",
    "automation",
    "workflow",
    "sales",
    "marketing",
    "support",
}

WORKFLOW_KEYWORDS = {
    "integrate",
    "integration",
    "plugin",
    "zapier",
    "notion",
    "slack",
    "gmail",
    "excel",
    "sheet",
    "workflow",
    "pipeline",
    "agent",
    "crm",
    "erp",
}

MONOPOLY_TERMS = {
    "chatgpt",
    "openai",
    "claude",
    "gemini",
    "perplexity",
    "midjourney",
    "copilot",
    "grok",
    "character ai",
    "hugging face",
}

ENTERTAINMENT_TERMS = {
    "anime",
    "meme",
    "dating",
    "girlfriend",
    "boyfriend",
    "fun",
    "game",
    "roleplay",
}


@dataclass
class ScoreBreakdown:
    trend_score: float
    pain_score: float
    biz_score: float
    workflow_score: float
    monopoly_penalty: float
    final_score: float


def _normalize_name(name: str) -> str:
    return NORMALIZE_RE.sub("", name.lower())


def _extract_domain(url: str) -> str:
    if not url:
        return ""
    try:
        no_proto = url.split("//", 1)[-1]
        return no_proto.split("/", 1)[0].lower()
    except Exception:  # noqa: BLE001
        return ""


def _keyword_hits(text: str, terms: Iterable[str]) -> int:
    lowered = text.lower()
    return sum(1 for term in terms if term in lowered)


def _score_trend(item: dict) -> float:
    rank = int(item.get("rank") or 999)
    source = item.get("source_page", "")

    rank_component = max(0.0, 100.0 - (rank - 1) * 0.7)
    source_bonus = 12.0 if source == "new_ais" else 5.0
    top_bonus = 10.0 if rank <= 20 else 0.0

    return min(100.0, rank_component + source_bonus + top_bonus)


def _score_pain(item: dict) -> float:
    text = f"{item.get('name', '')} {item.get('description', '')} {item.get('tag', '')}"
    hit = _keyword_hits(text, PAIN_KEYWORDS)
    noise = _keyword_hits(text, ENTERTAINMENT_TERMS)
    base = 30.0 + min(50.0, hit * 10.0)
    return max(0.0, min(100.0, base - noise * 15.0))


def _score_biz(item: dict) -> float:
    text = f"{item.get('name', '')} {item.get('description', '')}"
    hit = _keyword_hits(text, BIZ_KEYWORDS)
    return min(100.0, 25.0 + hit * 12.5)


def _score_workflow(item: dict) -> float:
    text = f"{item.get('name', '')} {item.get('description', '')}"
    hit = _keyword_hits(text, WORKFLOW_KEYWORDS)
    return min(100.0, 25.0 + hit * 12.0)


def _monopoly_penalty(item: dict) -> float:
    text = f"{item.get('name', '')} {item.get('description', '')}".lower()
    hits = _keyword_hits(text, MONOPOLY_TERMS)
    return min(25.0, hits * 10.0)


def _quality_filter(item: dict) -> bool:
    name = (item.get("name") or "").strip()
    description = (item.get("description") or "").strip()
    rank = int(item.get("rank") or 9999)

    if len(name) < 2 or len(name) > 120:
        return False
    if rank <= 0 or rank > 5000:
        return False
    if len(description) < 12:
        return False

    lower_desc = description.lower()
    if lower_desc in {"n/a", "none", "-"}:
        return False
    return True


def _build_score(item: dict) -> ScoreBreakdown:
    trend = _score_trend(item)
    pain = _score_pain(item)
    biz = _score_biz(item)
    workflow = _score_workflow(item)
    penalty = _monopoly_penalty(item)

    final = (
        trend * 0.25
        + pain * 0.25
        + biz * 0.20
        + workflow * 0.20
        - penalty * 0.20
    )
    final = max(0.0, min(100.0, final))

    return ScoreBreakdown(
        trend_score=round(trend, 2),
        pain_score=round(pain, 2),
        biz_score=round(biz, 2),
        workflow_score=round(workflow, 2),
        monopoly_penalty=round(penalty, 2),
        final_score=round(final, 2),
    )


def _dedupe(items: list[dict]) -> list[dict]:
    seen: dict[str, dict] = {}

    for item in items:
        key_name = _normalize_name(item.get("name", ""))
        key_domain = _extract_domain(item.get("url", ""))
        key = f"{key_name}|{key_domain}" if key_domain else key_name
        if not key:
            continue

        rank = int(item.get("rank") or 9999)
        existing = seen.get(key)
        if existing is None:
            seen[key] = item
            continue

        existing_rank = int(existing.get("rank") or 9999)
        if rank < existing_rank:
            seen[key] = item

    return list(seen.values())


def _slice_selected(scored_items: list[dict], selected_min: int, selected_max: int) -> list[dict]:
    scored_items = sorted(scored_items, key=lambda item: item["score"]["final_score"], reverse=True)
    selected = scored_items[:selected_max]

    if len(selected) >= selected_min:
        return selected

    rank_sorted = sorted(scored_items, key=lambda item: int(item.get("rank") or 9999))
    index = 0
    selected_keys = {
        f"{_normalize_name(item.get('name', ''))}|{_extract_domain(item.get('url', ''))}"
        for item in selected
    }
    while len(selected) < selected_min and index < len(rank_sorted):
        candidate = rank_sorted[index]
        key = f"{_normalize_name(candidate.get('name', ''))}|{_extract_domain(candidate.get('url', ''))}"
        if key not in selected_keys:
            selected.append(candidate)
            selected_keys.add(key)
        index += 1

    return selected


def _count_tags(items: list[dict]) -> list[dict[str, int]]:
    counter: Counter[str] = Counter()
    for item in items:
        tag_text = item.get("tag", "")
        if not tag_text:
            continue
        for token in tag_text.split():
            if token.startswith("#"):
                counter[token] += 1
    return [{"tag": tag, "count": count} for tag, count in counter.most_common(12)]


def select_effective_tools(items: list[dict], selected_min: int = 100, selected_max: int = 140) -> dict:
    filtered = [item for item in items if _quality_filter(item)]
    deduped = _dedupe(filtered)

    scored: list[dict] = []
    for item in deduped:
        score = _build_score(item)
        enriched = dict(item)
        enriched["score"] = score.__dict__
        enriched["description"] = (item.get("description") or "")[:240]
        scored.append(enriched)

    selected = _slice_selected(scored, selected_min=selected_min, selected_max=selected_max)
    selected.sort(key=lambda item: item["score"]["final_score"], reverse=True)

    source_counter = Counter(item.get("source_page", "unknown") for item in selected)

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "raw_count": len(items),
        "filtered_count": len(filtered),
        "deduped_count": len(deduped),
        "selected_count": len(selected),
        "source_distribution": dict(source_counter),
        "top_tags": _count_tags(selected),
        "selection_rules": {
            "selected_min": selected_min,
            "selected_max": selected_max,
            "weights": {
                "trend": 0.25,
                "pain": 0.25,
                "biz": 0.20,
                "workflow": 0.20,
                "monopoly_penalty": -0.20,
            },
            "penalty_terms": sorted(MONOPOLY_TERMS),
        },
        "selected_tools": selected,
        "llm_input_tools": selected[:120],
    }
