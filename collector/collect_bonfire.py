"""
Bonfire collector (State of Utah portal: utah.bonfirehub.com)

The State of Utah, plus many Utah cities, counties and districts, now post
their bids here. This is the main source for Utah public work.

What it does, in plain English:
  1. Opens the portal's "Open Opportunities" page in a hidden browser.
  2. Listens for the data the page loads behind the scenes (the full list of
     open bids). If it can't find that, it falls back to reading the table
     on screen.
  3. Keeps only bids that look like concrete work.
  4. Saves them to your Supabase "bids" table, skipping any it already has.
  5. Saves a screenshot, page copy, and the raw data into "debug".

Settings come from environment variables (GitHub Actions supplies them):
  SUPABASE_URL               your project URL
  SUPABASE_SERVICE_ROLE_KEY  your SECRET service key (never put this in Lovable)
  DRY_RUN=1                  optional: print results without saving
"""

import json
import os
import re
from datetime import timezone
from zoneinfo import ZoneInfo

from dateutil import parser as dateparser
from playwright.sync_api import sync_playwright

PORTAL = "https://utah.bonfirehub.com"
OPEN_URL = f"{PORTAL}/portal/?tab=openOpportunities"
SOURCE = "bonfire_utah"
DEBUG_DIR = "debug"
UTAH_TZ = ZoneInfo("America/Denver")

# Words that signal concrete work. Add or remove to tune what gets through.
CONCRETE_KEYWORDS = [
    "concrete", "cement", "flatwork", "sidewalk", "curb", "gutter",
    "slab", "footing", "foundation", "retaining wall", "ada ramp",
    "pedestrian ramp", "driveway", "paving", "pavement", "shotcrete",
    "rebar", "precast", "bridge", "parking lot", "sitework", "site work",
    "street improvement", "road improvement", "trail", "park improvement",
]


def is_concrete(text: str) -> bool:
    t = (text or "").lower()
    return any(word in t for word in CONCRETE_KEYWORDS)


def pick(record: dict, *names):
    """Return the first non-empty field, ignoring upper/lower case differences."""
    lowered = {str(k).lower(): v for k, v in record.items()}
    for name in names:
        value = lowered.get(name.lower())
        if value not in (None, ""):
            return value
    return None


def to_iso(value, assume_tz):
    """Turn a date like '2026-10-12 20:00:00' or 'Oct 12th 2026, 2:00 PM (MDT)' into a standard timestamp."""
    if not value:
        return None
    try:
        text = re.sub(r"\(.*?\)", "", str(value))
        dt = dateparser.parse(text, fuzzy=True)
    except (ValueError, OverflowError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=assume_tz)
    return dt.isoformat()


def find_projects(obj):
    """Dig through the page's background data for the list of projects."""
    if isinstance(obj, dict):
        for key, val in obj.items():
            if str(key).lower() == "projects":
                if isinstance(val, dict):
                    return list(val.values())
                if isinstance(val, list):
                    return val
        for val in obj.values():
            found = find_projects(val)
            if found:
                return found
    elif isinstance(obj, list):
        for val in obj:
            found = find_projects(val)
            if found:
                return found
    return None


def scrape():
    """Open the portal and return (background data responses, on-screen table rows)."""
    os.makedirs(DEBUG_DIR, exist_ok=True)
    responses = []
    captured = []

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.on("response", lambda resp: responses.append(resp))

        page.goto(OPEN_URL, wait_until="networkidle", timeout=90_000)
        page.wait_for_timeout(5_000)

        # Read any background data the page loaded
        for resp in responses:
            try:
                if "json" in (resp.headers.get("content-type") or ""):
                    captured.append({"url": resp.url, "data": resp.json()})
            except Exception:
                pass

        # Debug copies
        page.screenshot(path=f"{DEBUG_DIR}/bonfire.png", full_page=True)
        with open(f"{DEBUG_DIR}/bonfire.html", "w", encoding="utf-8") as f:
            f.write(page.content())

        # Fallback: the table on screen
        rows = page.eval_on_selector_all(
            "tr",
            """els => els.map(tr => {
                const a = tr.querySelector('a[href*="/opportunities/"]');
                return {
                    cells: Array.from(tr.querySelectorAll('td')).map(td => (td.innerText || '').trim()),
                    text:  (tr.innerText || '').trim(),
                    href:  a ? a.href : ''
                };
            })""",
        )
        browser.close()

    with open(f"{DEBUG_DIR}/bonfire_data.json", "w", encoding="utf-8") as f:
        json.dump(captured, f, indent=2, default=str)

    return captured, rows


def bids_from_data(captured):
    """Build bids from the page's background data (the preferred route)."""
    for item in captured:
        projects = find_projects(item["data"])
        if not projects:
            continue
        bids = []
        for proj in projects:
            if not isinstance(proj, dict):
                continue
            pid = pick(proj, "ProjectID", "projectId", "id")
            name = pick(proj, "ProjectName", "projectName", "name", "title")
            if not pid or not name:
                continue
            ref = pick(proj, "ReferenceID", "referenceId", "reference")
            agency = pick(proj, "DepartmentName", "OrganizationName", "department", "organization")
            desc = pick(proj, "ProjectDescription", "Description", "description") or ""
            title = f"{ref} {name}" if ref and str(ref) not in str(name) else str(name)
            bids.append({
                "source_bid_id": str(pid),
                "title": title,
                "agency": agency or "State of Utah (Bonfire)",
                "description": f"{desc}",
                # Bonfire's raw data stores times in UTC
                "close_date": to_iso(pick(proj, "DateClose", "CloseDate", "closeDate"), timezone.utc),
                "url": f"{PORTAL}/opportunities/{pid}",
            })
        if bids:
            return bids
    return []


def bids_from_table(rows):
    """Backup route: read the table on screen. Columns are usually
    Status | Ref. # | Project | Close Date | Days Left | Action."""
    bids = []
    for row in rows:
        match = re.search(r"/opportunities/(\d+)", row.get("href") or "")
        if not match:
            continue
        cells = row.get("cells") or []
        ref = cells[1] if len(cells) > 1 else ""
        name = cells[2] if len(cells) > 2 else row["text"][:200]
        close = cells[3] if len(cells) > 3 else None
        bids.append({
            "source_bid_id": match.group(1),
            "title": f"{ref} {name}".strip(),
            "agency": "State of Utah (Bonfire)",
            "description": row["text"],
            "close_date": to_iso(close, UTAH_TZ),
            "url": row["href"],
        })
    return bids


def main():
    captured, rows = scrape()

    all_bids = bids_from_data(captured)
    route = "background data"
    if not all_bids:
        all_bids = bids_from_table(rows)
        route = "on-screen table"

    print(f"Read {len(all_bids)} open bids from Bonfire (via {route})")
    for b in all_bids[:5]:
        print(f"  sample: {b['title']}")

    concrete = {}
    for b in all_bids:
        if is_concrete(f"{b['title']} {b['description']} {b['agency']}"):
            b["source"] = SOURCE
            b["category"] = "concrete"
            b["title"] = b["title"][:500]
            b["description"] = (b["description"] or "")[:4000]
            concrete[b["source_bid_id"]] = b

    print(f"Found {len(concrete)} concrete bids")
    for b in concrete.values():
        print(f"  - {b['title']}  (closes {b['close_date']})")

    if not concrete:
        print("Nothing matched. Check debug/bonfire.png and debug/bonfire_data.json.")
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
        list(concrete.values()),
        on_conflict="source,source_bid_id",
        ignore_duplicates=True,
    ).execute()
    print("Saved to Supabase.")


if __name__ == "__main__":
    main()
