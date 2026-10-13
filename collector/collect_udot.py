"""
UDOT collector (Contractor Zone: contractorzone.udot.utah.gov/projects)

UDOT's road construction jobs (paving, sidewalks, ramps, widening) are not
on the Bonfire portal. They are listed publicly on UDOT's Contractor Zone
"Advertised Projects" page.

What it does, in plain English:
  1. Opens the Advertised Projects page in a hidden browser.
  2. Listens for the data the page loads behind the scenes. If it can't
     find that, it reads the table on screen instead. The table columns are:
     Project Name | PIN | Project Number | Region | Advertised Date | Bid Open Date & Time
  3. Sorts each project with the road rules in matching.py (concrete,
     general construction, or skip for signals/signs/striping).
  4. Saves them to your Supabase "bids" table, updating ones it already has.
  5. Saves a screenshot, page copy, and raw data into "debug".

Settings come from environment variables (GitHub Actions supplies them):
  SUPABASE_URL               your project URL
  SUPABASE_SERVICE_ROLE_KEY  your SECRET service key (never put this in Lovable)
  DRY_RUN=1                  optional: print results without saving
"""

import json
import os
import re
from zoneinfo import ZoneInfo

from dateutil import parser as dateparser
from playwright.sync_api import sync_playwright

from matching import classify_road_project

SITE = "https://contractorzone.udot.utah.gov"
PROJECTS_URL = f"{SITE}/projects"
DEFAULT_BID_HOUR = 14  # UDOT bid openings are 2:00 PM local time when no time is given
SOURCE = "udot"
DEBUG_DIR = "debug"
UTAH_TZ = ZoneInfo("America/Denver")


def to_iso(value):
    """Turn 'October 01, 2026 2:00 PM MST' (or an ISO date) into a standard timestamp in Utah time."""
    if not value:
        return None
    text = str(value)
    is_iso = bool(re.match(r"^\d{4}-\d{2}-\d{2}T", text))
    if not is_iso:
        # Drop time zone words; UDOT bid openings are Utah local time
        text = re.sub(r"\b(MST|MDT|MT)\b", "", text)
    try:
        dt = dateparser.parse(text, fuzzy=True)
    except (ValueError, OverflowError):
        return None
    has_time = bool(re.search(r"\d{1,2}:\d{2}", str(value)))
    if not has_time and not is_iso:
        dt = dt.replace(hour=DEFAULT_BID_HOUR, minute=0)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTAH_TZ)
    return dt.isoformat()


def field(record: dict, *fragments):
    """Return the first non-empty value whose key contains any of the fragments (case-insensitive)."""
    for frag in fragments:
        for key, val in record.items():
            if frag in str(key).lower().replace("_", "") and val not in (None, ""):
                return val
    return None


def find_project_list(obj):
    """Dig through background data for a list of project records (dicts with a PIN and a name)."""
    if isinstance(obj, list) and obj and all(isinstance(x, dict) for x in obj[:5]):
        keys = " ".join(str(k).lower() for k in obj[0].keys())
        if "pin" in keys and "name" in keys:
            return obj
    if isinstance(obj, dict):
        for val in obj.values():
            found = find_project_list(val)
            if found:
                return found
    elif isinstance(obj, list):
        for val in obj:
            found = find_project_list(val)
            if found:
                return found
    return None


def scrape():
    """Open Contractor Zone and return (background data responses, on-screen table rows)."""
    os.makedirs(DEBUG_DIR, exist_ok=True)
    responses = []
    captured = []

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.on("response", lambda resp: responses.append(resp))

        page.goto(PROJECTS_URL, wait_until="networkidle", timeout=90_000)
        page.wait_for_timeout(5_000)

        for resp in responses:
            try:
                if "json" in (resp.headers.get("content-type") or ""):
                    captured.append({"url": resp.url, "data": resp.json()})
            except Exception:
                pass

        page.screenshot(path=f"{DEBUG_DIR}/udot.png", full_page=True)
        with open(f"{DEBUG_DIR}/udot.html", "w", encoding="utf-8") as f:
            f.write(page.content())

        rows = page.eval_on_selector_all(
            "tr",
            """els => els.map(tr => {
                const a = tr.querySelector('a[href]');
                return {
                    cells: Array.from(tr.querySelectorAll('td')).map(td => (td.innerText || '').trim()),
                    href:  a ? a.href : ''
                };
            })""",
        )
        browser.close()

    with open(f"{DEBUG_DIR}/udot_data.json", "w", encoding="utf-8") as f:
        json.dump(captured, f, indent=2, default=str)

    return captured, rows


def make_bid(name, pin, number, region, bid_open, href):
    # The "UDOT:" prefix keeps highway names like "US-89" from being
    # mistaken for a reference code by the website's title cleanup.
    title = f"UDOT: {name}"
    agency = f"UDOT - {region}" if region else "UDOT"
    details = [f"Project number: {number}" if number else "", f"PIN: {pin}"]
    return {
        "source_bid_id": str(pin),
        "title": title[:500],
        "agency": agency,
        "description": " | ".join(d for d in details if d),
        "close_date": to_iso(bid_open),
        "url": href or PROJECTS_URL,
    }


def bids_from_data(captured):
    for item in captured:
        projects = find_project_list(item["data"])
        if not projects:
            continue
        bids = []
        for proj in projects:
            name = field(proj, "projectname", "name", "title")
            pin = field(proj, "pin")
            if not name or not pin:
                continue
            bids.append(make_bid(
                name=str(name).strip(),
                pin=pin,
                number=field(proj, "projectnumber", "projectno", "number"),
                region=field(proj, "region"),
                bid_open=field(proj, "bidopen", "opendate", "bidopening", "letting", "biddate"),
                href=f"{SITE}/project/{field(proj, 'recorduuid', 'uuid')}" if field(proj, "recorduuid", "uuid") else None,
            ))
        if bids:
            return bids
    return []


def bids_from_table(rows):
    bids = []
    for row in rows:
        cells = row.get("cells") or []
        if len(cells) < 6:
            continue
        name, pin, number, region, _advertised, bid_open = cells[:6]
        if not pin or not re.search(r"\d", pin):
            continue  # skip header or empty rows
        bids.append(make_bid(name, pin, number, region, bid_open, row.get("href")))
    return bids


def main():
    captured, rows = scrape()

    all_bids = bids_from_data(captured)
    route = "background data"
    if all_bids:
        # The background data only has the bid date; the on-screen table also
        # shows the time (e.g. "October 15, 2026 2:00 PM MST"). Use it when available.
        times = {}
        for row in rows:
            cells = row.get("cells") or []
            if len(cells) >= 6 and re.search(r"\d{1,2}:\d{2}", cells[5]):
                times[cells[1].strip()] = cells[5]
        for b in all_bids:
            if b["source_bid_id"] in times:
                b["close_date"] = to_iso(times[b["source_bid_id"]])
    else:
        all_bids = bids_from_table(rows)
        route = "on-screen table"

    print(f"Read {len(all_bids)} advertised UDOT projects (via {route})")
    for b in all_bids[:5]:
        print(f"  sample: {b['title']}  (bid open {b['close_date']})")

    relevant = {}
    skipped = []
    for b in all_bids:
        category = classify_road_project(b["title"])
        if category:
            b["source"] = SOURCE
            b["category"] = category
            relevant[b["source_bid_id"]] = b
        else:
            skipped.append(b["title"])

    direct = [b for b in relevant.values() if b["category"] == "concrete"]
    general = [b for b in relevant.values() if b["category"] == "general_construction"]
    print(f"Found {len(direct)} concrete bids and {len(general)} general construction bids")
    for b in direct:
        print(f"  [concrete] {b['title']}  ({b['agency']}, bid open {b['close_date']})")
    for b in general:
        print(f"  [general]  {b['title']}  ({b['agency']}, bid open {b['close_date']})")
    for t in skipped:
        print(f"  [skipped]  {t}")

    if not relevant:
        print("Nothing to save. Check debug/udot.png and debug/udot_data.json.")
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
        list(relevant.values()),
        on_conflict="source,source_bid_id",
        ignore_duplicates=False,  # refresh details on bids we already have; keeps the original "found" time
    ).execute()
    print("Saved to Supabase.")


if __name__ == "__main__":
    main()
