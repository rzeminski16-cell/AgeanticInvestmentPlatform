"""Walk one run's pages in a real browser and keep what a person would have seen.

    uv run python -m audit.driver.screenshots <job-id> <page> --out audit/out/msft1/screens

Runs the synchronous Playwright API in its own process, because that API owns the main
thread's event loop and the driver is asyncio. Saves a full-page PNG, the HTML, and a small
JSON of what the page declares — the payload hash the form would submit, the title, and
every raw-looking identifier the page shows a reader.
"""

from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path
from typing import Final

from playwright.sync_api import sync_playwright

PAGES: Final[dict[str, str]] = {
    "console": "/runs/{job}",
    "plan": "/runs/{job}/plan",
    "sector": "/runs/{job}/sector",
    "peers": "/runs/{job}/peers",
    "themes": "/runs/{job}/themes",
    "financials": "/runs/{job}/financials",
    "assumptions": "/runs/{job}/assumptions",
    "review": "/runs/{job}/review",
    "sources": "/runs/{job}/sources",
    "claims": "/runs/{job}/claims",
    "valuation": "/runs/{job}/valuation",
    "report": "/reports/{report}",
}
_CHROMIUM = Path(os.environ.get("PLAYWRIGHT_CHROMIUM_PATH", "/opt/pw-browsers/chromium"))
# Identifiers a reader should never meet: snake_case tokens and bare UUIDs in visible text.
_RAW_TOKEN: Final = re.compile(r"\b[a-z]+(?:_[a-z0-9]+){1,}\b")
_UUID: Final = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b")


def capture(
    base_url: str, job_id: str, page_key: str, out: Path, *, report_id: str = ""
) -> dict[str, object]:
    out.mkdir(parents=True, exist_ok=True)
    path = PAGES[page_key].format(job=job_id, report=report_id)
    with sync_playwright() as playwright:
        executable = str(_CHROMIUM) if _CHROMIUM.exists() else None
        browser = playwright.chromium.launch(args=["--no-sandbox"], executable_path=executable)
        context = browser.new_context(
            locale="en-GB", timezone_id="Europe/London", viewport={"width": 1280, "height": 900}
        )
        page = context.new_page()
        response = page.goto(f"{base_url}{path}", wait_until="networkidle", timeout=60_000)
        page.screenshot(path=str(out / f"{page_key}.png"), full_page=True)
        html = page.content()
        (out / f"{page_key}.html").write_text(html, encoding="utf-8")
        text = page.inner_text("body")
        hash_input = page.locator("#payload-hash")
        payload_hash = hash_input.get_attribute("value") if hash_input.count() else None
        record: dict[str, object] = {
            "page": page_key,
            "url": f"{base_url}{path}",
            "status": response.status if response is not None else None,
            "title": page.title(),
            "payload_hash": payload_hash,
            "words": len(text.split()),
            "raw_tokens": sorted(set(_RAW_TOKEN.findall(text)))[:60],
            "uuids": len(_UUID.findall(text)),
            "approve_control": page.locator("#approve").count(),
        }
        browser.close()
    (out / f"{page_key}.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    return record


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("job_id")
    parser.add_argument("page", choices=sorted(PAGES))
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--report-id", default="")
    args = parser.parse_args(argv)
    record = capture(args.base_url, args.job_id, args.page, args.out, report_id=args.report_id)
    print(json.dumps(record))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
