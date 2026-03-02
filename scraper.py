from __future__ import annotations

import logging
import re
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Iterable

from playwright.sync_api import Browser, Page, sync_playwright

LOGGER = logging.getLogger(__name__)

RANK_LINE_RE = re.compile(r"^(\d+)\.\s+(.+)$")
VISITS_RE = re.compile(r"^\d+(?:\.\d+)?[KMBT]$")
RANK_CHANGE_RE = re.compile(r"([+-]\d+|↑\s*\d+|↓\s*\d+)")
WHITESPACE_RE = re.compile(r"\s+")

IGNORE_TEXT = {
    "view tool",
    "visit website",
    "visit",
    "open",
    "learn more",
    "read more",
    "pricing",
    "sign in",
    "log in",
    "download",
}

SOURCE_CONFIG = {
    "most_used": {
        "target": 150,
        "urls": [
            "https://www.toolify.ai/most-used",
            "https://www.toolify.ai/most-used-ai-tools",
        ],
    },
    "new_ais": {
        "target": 120,
        "urls": [
            "https://www.toolify.ai/new",
            "https://www.toolify.ai/new-ai-tools",
        ],
    },
}


@dataclass
class ToolItem:
    name: str
    tag: str
    description: str
    rank: int
    rank_change: str
    visits: str
    source_page: str
    url: str
    captured_at: str


def _normalize_text(text: str) -> str:
    return WHITESPACE_RE.sub(" ", text or "").strip()


def _extract_tags(lines: Iterable[str]) -> str:
    tags: list[str] = []
    for line in lines:
        for token in line.split():
            if token.startswith("#") and len(token) > 1:
                tags.append(token)
    unique_tags: list[str] = []
    for tag in tags:
        if tag not in unique_tags:
            unique_tags.append(tag)
    return " ".join(unique_tags[:3])


def _extract_visits(lines: Iterable[str]) -> str:
    for line in lines:
        compact = line.replace(" ", "")
        if VISITS_RE.match(compact):
            return compact
    return ""


def _extract_rank_change(lines: Iterable[str]) -> str:
    for line in lines:
        match = RANK_CHANGE_RE.search(line)
        if match:
            return match.group(1)
    return ""


def _likely_noise(line: str) -> bool:
    if not line:
        return True
    normalized = line.lower().strip()
    if normalized in IGNORE_TEXT:
        return True
    if normalized.startswith("share") or normalized.startswith("follow"):
        return True
    if normalized.endswith("tools") and len(normalized.split()) <= 3:
        return True
    return False


def _extract_description(lines: Iterable[str]) -> str:
    for line in lines:
        if _likely_noise(line):
            continue
        compact = line.replace(" ", "")
        if VISITS_RE.match(compact):
            continue
        if line.startswith("#"):
            continue
        if len(line) < 15:
            continue
        return line
    return ""


def _extract_links(page: Page) -> list[dict[str, str]]:
    script = """
    () => {
      const links = Array.from(document.querySelectorAll('a[href]'));
      return links.map((el) => ({
        href: el.href || '',
        text: (el.innerText || '').replace(/\s+/g, ' ').trim(),
      }));
    }
    """
    try:
        raw_links = page.evaluate(script)
    except Exception:  # noqa: BLE001
        return []

    results = []
    for item in raw_links:
        href = _normalize_text(item.get("href", ""))
        text = _normalize_text(item.get("text", ""))
        if not href or not text:
            continue
        if "/tool/" not in href and "/ai/" not in href:
            continue
        results.append({"href": href, "text": text})
    return results


def _match_url(name: str, links: list[dict[str, str]]) -> str:
    name_norm = name.lower()
    candidates: list[tuple[int, str]] = []
    for item in links:
        text = item["text"].lower()
        if name_norm not in text:
            continue
        score = abs(len(text) - len(name))
        candidates.append((score, item["href"]))
    if not candidates:
        return ""
    candidates.sort(key=lambda item: item[0])
    return candidates[0][1]


def _parse_main_text(main_text: str, source: str, captured_at: str, links: list[dict[str, str]]) -> list[ToolItem]:
    lines = [_normalize_text(line) for line in main_text.splitlines()]
    lines = [line for line in lines if line]

    items: list[ToolItem] = []
    i = 0
    while i < len(lines):
        rank_match = RANK_LINE_RE.match(lines[i])
        if not rank_match:
            i += 1
            continue

        rank = int(rank_match.group(1))
        name = _normalize_text(rank_match.group(2))

        block: list[str] = []
        j = i + 1
        while j < len(lines) and not RANK_LINE_RE.match(lines[j]) and len(block) < 8:
            block.append(lines[j])
            j += 1

        description = _extract_description(block)
        if not description and block:
            description = block[0]

        if not name or len(name) > 120:
            i = j
            continue

        tags = _extract_tags(block)
        visits = _extract_visits(block)
        rank_change = _extract_rank_change(block)
        url = _match_url(name, links)

        items.append(
            ToolItem(
                name=name,
                tag=tags,
                description=description,
                rank=rank,
                rank_change=rank_change,
                visits=visits,
                source_page=source,
                url=url,
                captured_at=captured_at,
            )
        )
        i = j

    return items


def _load_rank_page(page: Page, base_url: str, target: int, source: str) -> list[ToolItem]:
    captured_at = datetime.now(timezone.utc).isoformat()
    seen_keys: set[tuple[str, int, str]] = set()
    collected: list[ToolItem] = []

    def collect_from_current_page() -> int:
        try:
            main_text = page.inner_text("main")
        except Exception:  # noqa: BLE001
            main_text = page.inner_text("body")
        links = _extract_links(page)
        parsed_items = _parse_main_text(main_text, source=source, captured_at=captured_at, links=links)

        added = 0
        for item in parsed_items:
            key = (item.source_page, item.rank, item.name.lower())
            if key in seen_keys:
                continue
            seen_keys.add(key)
            collected.append(item)
            added += 1
        return added

    page.goto(base_url, wait_until="domcontentloaded", timeout=60_000)
    page.wait_for_timeout(2_500)
    collect_from_current_page()

    stagnant_rounds = 0
    for _ in range(12):
        if len(collected) >= target:
            break
        previous_count = len(collected)

        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        page.wait_for_timeout(1_800)
        collect_from_current_page()

        if len(collected) == previous_count:
            stagnant_rounds += 1
        else:
            stagnant_rounds = 0

        if stagnant_rounds >= 3:
            break

    if len(collected) < target:
        for page_num in range(2, 9):
            paged_url = f"{base_url}?page={page_num}"
            page.goto(paged_url, wait_until="domcontentloaded", timeout=60_000)
            page.wait_for_timeout(1_700)
            before = len(collected)
            collect_from_current_page()
            if len(collected) >= target:
                break
            if len(collected) == before:
                break

    collected.sort(key=lambda item: item.rank)
    return collected[:target]


def _choose_reachable_url(page: Page, candidates: list[str]) -> str:
    last_error: Exception | None = None
    for url in candidates:
        try:
            response = page.goto(url, wait_until="domcontentloaded", timeout=60_000)
            page.wait_for_timeout(1_000)
            status = response.status if response is not None else 0
            if status == 0 or 200 <= status < 500:
                return url
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            LOGGER.warning("Failed opening %s: %s", url, exc)
            continue
    raise RuntimeError(
        f"No reachable Toolify URL found in candidates: {candidates}; last_error={last_error}"
    )


def scrape_toolify_data(max_most_used: int = 150, max_new: int = 120, headless: bool = True) -> list[dict]:
    """Scrape ranked Toolify lists and return normalized dictionaries."""
    targets = {"most_used": max_most_used, "new_ais": max_new}
    all_items: list[ToolItem] = []
    source_errors: dict[str, str] = {}

    with sync_playwright() as p:
        browser: Browser = p.chromium.launch(headless=headless)
        context = browser.new_context(viewport={"width": 1440, "height": 2600})
        page = context.new_page()

        for source, config in SOURCE_CONFIG.items():
            try:
                base_url = _choose_reachable_url(page, config["urls"])
                target = targets[source]
                LOGGER.info("Scraping %s from %s (target=%s)", source, base_url, target)
                start = time.time()
                source_items = _load_rank_page(page, base_url=base_url, target=target, source=source)
                elapsed = round(time.time() - start, 2)
                LOGGER.info("Collected %s items for %s in %ss", len(source_items), source, elapsed)
                all_items.extend(source_items)
            except Exception as exc:  # noqa: BLE001
                source_errors[source] = str(exc)
                LOGGER.exception("Failed scraping source %s: %s", source, exc)

        context.close()
        browser.close()

    if not all_items:
        raise RuntimeError(f"Scraping produced no records. source_errors={source_errors}")

    return [asdict(item) for item in all_items]
