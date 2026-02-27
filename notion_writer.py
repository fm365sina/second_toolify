from __future__ import annotations

import re
from typing import Any

from notion_client import Client

MAX_RICH_TEXT_LEN = 1900


def _normalize_notion_id(value: str) -> str:
    return (value or "").replace("-", "").strip().lower()


def _chunk_text(text: str, max_len: int = MAX_RICH_TEXT_LEN) -> list[str]:
    if not text:
        return [""]
    chunks: list[str] = []
    start = 0
    while start < len(text):
        chunks.append(text[start : start + max_len])
        start += max_len
    return chunks


def _markdown_bold_to_rich_text(text: str) -> list[dict[str, Any]]:
    parts = re.split(r"(\*\*.*?\*\*)", text)
    rich_text: list[dict[str, Any]] = []

    for part in parts:
        if not part:
            continue
        is_bold = part.startswith("**") and part.endswith("**") and len(part) >= 4
        content = part[2:-2] if is_bold else part

        for chunk in _chunk_text(content):
            rich_text.append(
                {
                    "type": "text",
                    "text": {"content": chunk},
                    "annotations": {
                        "bold": is_bold,
                        "italic": False,
                        "strikethrough": False,
                        "underline": False,
                        "code": False,
                        "color": "default",
                    },
                }
            )

    return rich_text or [{"type": "text", "text": {"content": ""}}]


def _line_to_block(line: str) -> dict[str, Any] | None:
    stripped = line.strip()
    if not stripped:
        return None

    if stripped.startswith("## "):
        content = stripped[3:].strip()
        return {
            "object": "block",
            "type": "heading_2",
            "heading_2": {"rich_text": _markdown_bold_to_rich_text(content)},
        }

    if stripped.startswith("# "):
        content = stripped[2:].strip()
        return {
            "object": "block",
            "type": "heading_1",
            "heading_1": {"rich_text": _markdown_bold_to_rich_text(content)},
        }

    if stripped.startswith("* ") or stripped.startswith("- "):
        content = stripped[2:].strip()
        return {
            "object": "block",
            "type": "bulleted_list_item",
            "bulleted_list_item": {"rich_text": _markdown_bold_to_rich_text(content)},
        }

    if re.match(r"^\d+\.\s+", stripped):
        content = re.sub(r"^\d+\.\s+", "", stripped)
        return {
            "object": "block",
            "type": "numbered_list_item",
            "numbered_list_item": {"rich_text": _markdown_bold_to_rich_text(content)},
        }

    return {
        "object": "block",
        "type": "paragraph",
        "paragraph": {"rich_text": _markdown_bold_to_rich_text(stripped)},
    }


def markdown_to_notion_blocks(markdown_text: str) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    for line in markdown_text.splitlines():
        block = _line_to_block(line)
        if block:
            blocks.append(block)

    if not blocks:
        blocks.append(
            {
                "object": "block",
                "type": "paragraph",
                "paragraph": {"rich_text": [{"type": "text", "text": {"content": markdown_text[:MAX_RICH_TEXT_LEN]}}]},
            }
        )
    return blocks


def _find_existing_child_page(notion: Client, parent_page_id: str, title: str) -> dict[str, Any] | None:
    normalized_parent = _normalize_notion_id(parent_page_id)
    response = notion.search(query=title, filter={"property": "object", "value": "page"}, page_size=50)
    for result in response.get("results", []):
        parent = result.get("parent", {})
        if parent.get("type") != "page_id":
            continue
        if _normalize_notion_id(parent.get("page_id", "")) != normalized_parent:
            continue

        properties = result.get("properties", {})
        title_prop = properties.get("title", {})
        title_arr = title_prop.get("title", [])
        current_title = "".join(chunk.get("plain_text", "") for chunk in title_arr)
        if current_title == title:
            return result
    return None


def _append_blocks(notion: Client, page_id: str, blocks: list[dict[str, Any]]) -> None:
    batch_size = 80
    for i in range(0, len(blocks), batch_size):
        batch = blocks[i : i + batch_size]
        notion.blocks.children.append(block_id=page_id, children=batch)


def write_daily_report_to_notion(
    markdown_text: str,
    notion_token: str,
    parent_page_id: str,
    date_str: str,
    skip_if_exists: bool = True,
) -> dict[str, str]:
    notion = Client(auth=notion_token)
    title = f"Toolify 每日5D风向标 | {date_str}"

    existing = _find_existing_child_page(notion, parent_page_id, title)
    if existing and skip_if_exists:
        return {
            "page_id": existing["id"],
            "url": existing.get("url", ""),
            "status": "skipped_existing",
            "title": title,
        }

    page = notion.pages.create(
        parent={"page_id": parent_page_id},
        properties={
            "title": {
                "title": [
                    {
                        "type": "text",
                        "text": {
                            "content": title,
                        },
                    }
                ]
            }
        },
    )

    blocks = markdown_to_notion_blocks(markdown_text)
    _append_blocks(notion, page["id"], blocks)

    return {
        "page_id": page["id"],
        "url": page.get("url", ""),
        "status": "created",
        "title": title,
    }
