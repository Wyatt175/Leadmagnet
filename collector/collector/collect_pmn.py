"""
Public Notice collector (Utah Public Notice Website: utah.gov/pmn)

Utah cities, counties, school districts and special districts must post bid
ads here, so it covers many agencies that never use Bonfire or UDOT.

Every notice has a numbered page:
    https://www.utah.gov/pmn/sitemap/notice/<number>.html
New notices get the next numbers, so each run reads the pages after the last
number it saw (kept in the collector_state table), politely, one at a time.

What it does, in plain English:
  1. Picks up where the last run stopped. On the very first run it finds the
     newest notice number and starts about two to three weeks back.
  2. Reads each new notice page and keeps only open requests for bids or
     proposals (not meeting agendas, awards, surplus sales, and so on).
  3. Tags each bid's trades from its title and description, and pulls the
     engineer's estimate when the notice lists one.
  4. Saves them to your Supabase "bids" table, updating ones it already has.
  5. Saves the first few pages it read into "debug" so problems are easy to see.

Settings come from environment variables (GitHub Actions supplies them):
  SUPABASE_URL               your project URL
  SUPABASE_SERVICE_ROLE_KEY  your SECRET service key
  DRY_RUN=1                  optional: print results without saving anything
  PMN_START_ID=1100000       optional: force the first notice number to read
"""

import os
import re
import time
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup
from dateutil import parser as dateparser

from matching import classify_notice, estimated_value, is_bid_notice, legacy_category

SOURCE = "pmn"
NOTICE_URL = "https://www.utah.gov/pmn/sitemap/notice/{}.html"
DEBUG_DIR = "debug"
UTAH_TZ = ZoneInfo("America/Denver")

KNOWN_RECENT_ID = 1089203     # a real notice from June 2026, used to find the newest one
BACKFILL = 4000               # on the first run, start this many notices back (about 2 to 3 weeks)
MAX_PER_RUN = 1500            # notice pages read per run at most
STOP_AFTER_MISSES = 60        # this many missing numbers in a row means we reached the newest notice
PAUSE = 0.25                  # seconds between requests, to be polite to the state's server

HEADERS = {"User-Agent": "BidSlateBot/1.0 (public bid aggregator for Utah contractors)"}

LABELS = [
    "Notice Title", "Entity", "Public Body", "Government Type", "Notice Type",
    "Posted On", "Event Start", "Event End", "Deadline", "Description",
    "Notice Date & Time", "Attachments", "Contact", "Contact Information",
    "Special Accommodations", "Electronic Participation", "Location", "Notice Location",
    "Audio File", "Last Edited", "Notice Tags",
]
STOP_LABELS = set(LABELS) - {"Description"}

# Turn the state's government types into the group prefixes the website understands
GROUPS = [
    (r"municipal|city|town", "CITIES"),
    (r"county", "COUNTIES"),
    (r"school district", "SCHOOL DISTRICTS"),
    (r"charter", "CHARTER SCHOOLS"),
    (r"higher education|university|college", "HIGHER EDUCATION"),
    (r"special|local district|service district|interlocal|conservancy|improvement", "SPECIAL DISTRICTS"),
    (r"state", "STATE AGENCIES"),
]

session = requests.Session()
session.headers.update(HEADERS)


def fetch(notice_id):
    """Return the notice page's HTML, or None when that number has no notice."""
    try:
        resp = session.get(NOTICE_URL.format(notice_id), timeout=25)
    except requests.RequestException as err:
        print(f"  network problem on {notice_id}: {err}")
        return None
    if resp.status_code != 200:
        return None
    if "Notice Title" not in resp.text:
        return None
    return resp.text


def page_lines(html):
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "nav", "header", "footer"]):
        tag.decompose()
    return [ln.strip() for ln in soup.get_text("\n").split("\n") if ln.strip()]


def field(lines, label):
    """Value next to a label, whether it's on the same line ('Label: value') or the next one."""
    for i, line in enumerate(lines):
        if line == label or line == label + ":":
            for nxt in lines[i + 1:i + 3]:
                if nxt.rstrip(":") not in LABELS:
                    return nxt
            return ""
        if line.startswith(label + ":"):
            return line[len(label) + 1:].strip()
    return ""


def description(lines):
    for i, line in enumerate(lines):
        if line.startswith("Description:") and len(line) > len("Description:"):
            parts = [line[len("Description:"):].strip()]
            for nxt in lines[i + 1:]:
                if nxt.split(":")[0] in STOP_LABELS:
                    break
                parts.append(nxt)
            return " ".join(parts)[:4000]
        if line.rstrip(":") == "Description":
            parts = []
            for nxt in lines[i + 1:]:
                if nxt.rstrip(":") in STOP_LABELS:
                    break
                parts.append(nxt)
            return " ".join(parts)[:4000]
    return ""


def to_iso(value):
    if not value:
        return None
    try:
        dt = dateparser.parse(re.sub(r"\b(MST|MDT|MT)\b", "", value), fuzzy=True)
    except (ValueError, OverflowError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTAH_TZ)
    return dt.isoformat()


def agency_label(entity, gov_type):
    for pattern, group in GROUPS:
        if re.search(pattern, gov_type or "", re.IGNORECASE):
            return f"{group} - {entity}"
    return entity or "Utah public notice"


def parse(notice_id, html):
    lines = page_lines(html)
    title = field(lines, "Notice Title")
    entity = field(lines, "Entity")
    body = field(lines, "Public Body")
    gov_type = field(lines, "Government Type")
    desc = description(lines)
    deadline = field(lines, "Deadline") or field(lines, "Event End")
    return {
        "id": notice_id, "title": title, "entity": entity, "public_body": body,
        "gov_type": gov_type, "description": desc, "deadline": deadline,
        "posted": field(lines, "Posted On"),
    }


def exists(notice_id):
    time.sleep(PAUSE)
    return fetch(notice_id) is not None


def find_newest():
    """Find the newest notice number by stepping forward from a known one."""
    current = KNOWN_RECENT_ID
    step = 4096
    while step >= 1:
        probe = current + step
        if any(exists(probe + k) for k in range(0, 6)):
            current = probe
        else:
            step //= 2
    return current


def get_state(client):
    if client is None:
        return None
    rows = client.table("collector_state").select("last_id").eq("source", SOURCE).execute().data
    return rows[0]["last_id"] if rows else None


def save_state(client, last_id):
    if client is None:
        return
    client.table("collector_state").upsert({"source": SOURCE, "last_id": last_id}).execute()


def main():
    dry_run = os.environ.get("DRY_RUN") == "1"
    client = None
    if not dry_run:
        from supabase import create_client
        client = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"])

    os.makedirs(DEBUG_DIR, exist_ok=True)
    start = os.environ.get("PMN_START_ID")
    last_id = int(start) - 1 if start else get_state(client)
    if last_id is None:
        newest = find_newest()
        last_id = newest - BACKFILL
        print(f"First run: newest notice is about {newest}, starting from {last_id + 1}")
    else:
        print(f"Continuing after notice {last_id}")

    bids = {}
    read = misses = saved_debug = 0
    highest_seen = last_id
    notice_id = last_id
    while read < MAX_PER_RUN and misses < STOP_AFTER_MISSES:
        notice_id += 1
        time.sleep(PAUSE)
        html = fetch(notice_id)
        if html is None:
            misses += 1
            continue
        misses = 0
        read += 1
        highest_seen = notice_id
        if saved_debug < 3:
            with open(f"{DEBUG_DIR}/pmn_{notice_id}.html", "w", encoding="utf-8") as f:
                f.write(html)
            saved_debug += 1

        n = parse(notice_id, html)
        if not n["title"]:
            continue
        if not is_bid_notice(n["title"], n["public_body"], n["description"]):
            continue
        trades = classify_notice(n["title"], n["description"])
        if not trades:
            print(f"  [skipped, not trade work] {n['entity']}: {n['title'][:90]}")
            continue
        title = n["title"]
        # Generic titles like "Advertisement for Bid" get the first sentence of the description
        if re.fullmatch(r"(?i)\s*(advertisement|invitation|notice|request|call)\s.*(bids?|proposals?)\s*", title) \
                and n["description"]:
            title = f"{n['entity']}: {n['description'].split('. ')[0][:180]}"
        bids[str(notice_id)] = {
            "source": SOURCE,
            "source_bid_id": str(notice_id),
            "title": title[:500],
            "agency": agency_label(n["entity"], n["gov_type"]),
            "description": n["description"],
            "close_date": to_iso(n["deadline"]),
            "url": NOTICE_URL.format(notice_id),
            "trades": trades,
            "category": legacy_category(trades),
            "estimated_value": estimated_value(n["description"]),
        }
        print(f"  [{', '.join(trades)}] {bids[str(notice_id)]['title'][:100]}  "
              f"({bids[str(notice_id)]['agency']}, due {bids[str(notice_id)]['close_date']})")

    print(f"Read {read} notices ({last_id + 1} to {highest_seen}); found {len(bids)} trade bids")
    if read == 0:
        print("No new notices read. If this keeps happening, check debug/ and the network.")

    if dry_run:
        print("DRY_RUN is on, so nothing was saved.")
        return

    if bids:
        client.table("bids").upsert(
            list(bids.values()),
            on_conflict="source,source_bid_id",
            ignore_duplicates=False,
        ).execute()
        print("Saved to Supabase.")
    save_state(client, highest_seen)
    print(f"Next run starts after notice {highest_seen}.")


if __name__ == "__main__":
    main()
