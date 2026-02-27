from __future__ import annotations

import json
import logging
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

from config import ConfigError, load_settings
from llm_processor import generate_daily_report
from notion_writer import write_daily_report_to_notion
from scraper import scrape_toolify_data
from selector import select_effective_tools


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )


def _write_artifact(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def run() -> int:
    _setup_logging()
    logger = logging.getLogger("main")

    load_dotenv()

    try:
        settings = load_settings()
    except ConfigError as exc:
        logger.error("Configuration error: %s", exc)
        return 1

    report_date = datetime.now(ZoneInfo(settings.timezone)).strftime("%Y-%m-%d")
    artifacts_dir = Path("artifacts") / report_date
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    try:
        logger.info("Step 1/4: scraping Toolify pages")
        scraped = scrape_toolify_data(
            max_most_used=settings.max_most_used,
            max_new=settings.max_new,
            headless=settings.headless,
        )
        logger.info("Scraping complete: %s raw records", len(scraped))
        _write_artifact(artifacts_dir / "scraped.json", json.dumps(scraped, ensure_ascii=False, indent=2))
    except Exception as exc:  # noqa: BLE001
        logger.exception("Scraping failed: %s", exc)
        return 2

    try:
        logger.info("Step 2/4: selecting effective candidates")
        selected_data = select_effective_tools(
            items=scraped,
            selected_min=settings.selected_min,
            selected_max=settings.selected_max,
        )
        logger.info(
            "Selection complete: selected %s from deduped %s",
            selected_data["selected_count"],
            selected_data["deduped_count"],
        )
        _write_artifact(
            artifacts_dir / "selected.json",
            json.dumps(selected_data, ensure_ascii=False, indent=2),
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("Selection failed: %s", exc)
        return 3

    try:
        logger.info("Step 3/4: generating 5D markdown report")
        markdown = generate_daily_report(
            selected_data=selected_data,
            api_key=settings.deepseek_api_key,
            model_name=settings.deepseek_model,
            report_date=report_date,
            base_url=settings.deepseek_base_url,
        )
        _write_artifact(artifacts_dir / "report.md", markdown)
        logger.info("Report generation complete")
    except Exception as exc:  # noqa: BLE001
        logger.exception("LLM processing failed: %s", exc)
        return 4

    try:
        logger.info("Step 4/4: writing report to Notion page")
        notion_result = write_daily_report_to_notion(
            markdown_text=markdown,
            notion_token=settings.notion_token,
            parent_page_id=settings.notion_parent_page_id,
            date_str=report_date,
            skip_if_exists=settings.notion_skip_if_exists,
        )
        _write_artifact(
            artifacts_dir / "notion_result.json",
            json.dumps(notion_result, ensure_ascii=False, indent=2),
        )
        logger.info(
            "Notion sync complete: status=%s, url=%s",
            notion_result.get("status"),
            notion_result.get("url"),
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("Notion write failed: %s", exc)
        return 5

    logger.info("Pipeline finished successfully for %s", report_date)
    return 0


if __name__ == "__main__":
    sys.exit(run())
