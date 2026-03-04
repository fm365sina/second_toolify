# Toolify Daily 5D Report Generator

Automates daily Toolify trend scraping, DeepSeek 5D analysis, and Notion page publishing.

## Features

- Scrape `Most Used` and `New AIs` using Playwright.
- Select 100+ effective candidates with anti-monopoly down-weighting.
- Generate a 1500-2500 Chinese deep report via DeepSeek.
- Publish to a Notion parent page (non-database) as daily sub-pages.
- Run automatically with GitHub Actions cron.

## Quick Start

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
cp .env.example .env
# fill .env
python main.py
```

## Environment Variables

- `DEEPSEEK_API_KEY`
- `NOTION_TOKEN`
- `NOTION_PARENT_PAGE_ID`
- `DEEPSEEK_MODEL` (optional, default `deepseek-chat`)
- `DEEPSEEK_BASE_URL` (optional, default `https://api.deepseek.com`)
- `MAX_MOST_USED` (optional, default `150`)
- `MAX_NEW_AIS` (optional, default `120`)
- `SELECTED_MIN` (optional, default `100`)
- `SELECTED_MAX` (optional, default `140`)
- `PLAYWRIGHT_HEADLESS` (optional, default `true`)
- `NOTION_SKIP_IF_EXISTS` (optional, default `true`)
- `REPORT_TIMEZONE` (optional, default `Asia/Shanghai`)

Note:
- In GitHub Actions, manual `workflow_dispatch` runs set `NOTION_SKIP_IF_EXISTS=false` automatically to always create a new page.
- Scheduled runs keep `NOTION_SKIP_IF_EXISTS=true` to avoid duplicate daily pages.

## Output Artifacts

Each run writes debug artifacts to `artifacts/YYYY-MM-DD/`:

- `scraped.json`
- `selected.json`
- `report.md`
- `notion_result.json`
