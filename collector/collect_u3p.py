"""
U3P collector (Utah Public Procurement Place)

What it does, in plain English:
  1. Opens the public U3P bid page in a hidden browser (the page needs
     JavaScript, so a normal download won't work).
  2. Reads every row of the bid listings.
  3. Keeps only rows that look like concrete work.
  4. Saves them to your Supabase "bids" table, skipping any it already has.
  5. Saves a screenshot + page copy into a "debug" folder so you can see
     exactly what it saw if something looks off.

Settings come from environment variables (GitHub Actions supplies them):
  SUPABASE_URL               your project URL
  SUPABASE_SERVICE_ROLE_KEY  your SECRET service key (never put this in Lovable)
  DRY_RUN=1                  optional: print results without saving
"""

import hashlib
import os
import re
from zoneinfo import ZoneInfo

from dateutil import parser as dateparser
from playwright.sync_api import sync_playwright

U3P_URL = "https://bids.sciquest.com/apps/Router/PublicEvent?CustomerOrg=StateOfUtah"
SOURCE = "u3p"
DEBUG_DIR = "debug"
UTAH_TZ = ZoneInfo("America/Denver")

# Words that signal concrete work. Add or remove to tune what gets through.
CONCRETE_KEYWORDS = [
    "concrete", "cement", "flatwork", "sidewalk", "curb", "gutter",
    "slab", "footing", "foundation", "retaining wall", "ada ramp",
    "pedestrian ramp", "driveway", "paving", "pavement", "shotcrete",
    "rebar", "precast", "bridge deck", "parking lot",
]

# Matches dates like 10/22/2026 or 10/22/2026 2:00 PM
DATE_PATTERN = re.compile(
    r"\d{1,2}/\d{1,2}/\d{4}(?:\s+\d{1,2}:\d{2}(?::\d{2})?\s*[AP]M)?",
    re.IGNORECASE,
)

# Tries to find a stable ID number inside a bid's link
ID_IN_LINK = re.compile(r"(?:eventid|id|tabsheetid)=(\d+)", re.IGNORECASE)


def is_concrete(text: str) -> bool:
    t = text.lower()
    return any(word in t for word in CONCRETE_KEYWORDS)


def parse_close_date(text: str):
    """Find all dates in the row and treat the latest one as the close date."""
    found = []
    for match in DATE_PATTERN.findall(text):
        try:
            found.append(dateparser.parse(match))
        except (ValueError, OverflowError):
            pass
    if not found:
        return None
    latest = max(found)
    if latest.tzinfo is None:
        latest = latest.replace(tzinfo=UTAH_TZ)
    return latest.isoformat()


def make_bid_id(title: str, href: str) -> str:
    """Use the ID from the link if there is one, otherwise a fingerprint of the title."""
    match = ID_IN_LINK.search(href or "")
    if match:
        return match.group(1)
    normalized = re.sub(r"\s+", " ", title.strip().lower())
    return "t-" + hashlib.sha1(normalized.encode()).hexdigest()[:16]


def scrape_rows():
    """Open the U3P page and return every table row's text and first link."""
    os.makedirs(DEBUG_DIR, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.goto(U3P_URL, wait_until="networkidle", timeout=90_000)
        page.wait_for_timeout(5_000)  # give the listings a moment to finish drawing

        # Debug copies so you can see what the collector saw
        page.screenshot(path=f"{DEBUG_DIR}/u3p.png", full_page=True)
        with open(f"{DEBUG_DIR}/u3p.html", "w", encoding="utf-8") as f:
            f.write(page.content())

        rows = page.eval_on_selector_all(
            "tr",
            """els => els.map(tr => {
                const a = tr.querySelector('a[href]');
                return {
                    text:  (tr.innerText || '').trim(),
                    title: a ? (a.innerText || '').trim() : '',
                    href:  a ? a.href : ''
                };
            })""",
        )
        browser.close()
    return rows


def main():
    rows = scrape_rows()
    print(f"Read {len(rows)} rows from U3P")

    bids = {}
    for row in rows:
        title, text, href = row["title"], row["text"], row["href"]
        if not title or len(title) < 8:
            continue  # skip header rows, page numbers, etc.
        if not is_concrete(f"{title} {text}"):
            continue

        bid_id = make_bid_id(title, href)
        bids[bid_id] = {
            "source": SOURCE,
            "source_bid_id": bid_id,
            "title": title[:500],
            "agency": "State of Utah (U3P)",
            "category": "concrete",
            "description": text[:4000],
            "close_date": parse_close_date(text),
            "url": href or U3P_URL,
        }

    print(f"Found {len(bids)} concrete bids")
    for b in bids.values():
        print(f"  - {b['title']}  (closes {b['close_date']})")

    if not bids:
        print("Nothing matched. Open debug/u3p.png to check what the page showed.")
        return

    if os.environ.get("DRY_RUN") == "1":
        print("DRY_RUN is on, so nothing was saved.")
        return

    from supabase import create_client

    client = create_client(
        os.environ["SUPABASE_URL"],
        os.environ["SUPABASE_SERVICE_ROLE_KEY"],
    )
    client.table("bids").upsert(
        list(bids.values()),
        on_conflict="source,source_bid_id",
        ignore_duplicates=True,  # keeps the original "found at" time on bids we already have
    ).execute()
    print("Saved to Supabase.")


if __name__ == "__main__":
    main()
