"""
Email alerts: sends each subscribed contractor one digest of new bids.

What it does, in plain English:
  1. Finds bids that haven't been emailed yet (alerted_at is empty) and are still open.
  2. Gets the list of contractors who want email alerts (profiles.notify_by is
     'email' or 'both'), using the alert_recipients() database function.
  3. Sends each one a single email listing the new bids that match their
     trades (profiles.trades). Contractors who haven't picked trades yet fall
     back to the older setting: 'all' or 'concrete' only.
  4. Marks those bids as emailed so nobody gets them twice.

Settings come from environment variables (GitHub Actions supplies them):
  SUPABASE_URL               your project URL
  SUPABASE_SERVICE_ROLE_KEY  your SECRET service key
  RESEND_API_KEY             your Resend API key (secret)
  ALERT_FROM_EMAIL           who the email is from, e.g. "BidSlate <onboarding@resend.dev>"
  APP_URL                    optional: your published site, e.g. https://leadmagnet.lovable.app
  DRY_RUN=1                  optional: print the emails instead of sending, and don't mark anything
"""

import html
import os
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import requests
from supabase import create_client

from matching import TRADE_LABELS

UTAH_TZ = ZoneInfo("America/Denver")
RESEND_URL = "https://api.resend.com/emails"
ORANGE = "#0E5A43"  # BidSlate forest green


def parse_ts(value):
    if not value:
        return None
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def fmt_close(value):
    dt = parse_ts(value)
    if not dt:
        return "Close date not listed"
    local = dt.astimezone(UTAH_TZ)
    return local.strftime("%b %-d, %-I:%M %p ") + local.tzname()


def days_left(value, now):
    dt = parse_ts(value)
    if not dt:
        return ""
    days = (dt - now).total_seconds() / 86400
    if days < 1:
        return "closes in under 24 hours"
    if days < 2:
        return "closes in 1 day"
    return f"closes in {int(days)} days"


def estimate_html(bid):
    value = bid.get("estimated_value")
    if not value:
        return ""
    value = float(value)
    text = f"${value / 1_000_000:.1f}M" if value >= 1_000_000 else f"${value:,.0f}"
    return f" &middot; Est. {text}"


def bid_row(bid, now):
    trades = bid.get("trades") or [bid.get("category") or "general_construction"]
    is_concrete = "concrete" in trades
    badge_color = ORANGE if is_concrete else "#64748B"
    badge_text = " · ".join(TRADE_LABELS.get(t, t) for t in trades[:3]).upper()
    title = html.escape(bid["title"] or "")
    agency = html.escape(bid["agency"] or "")
    url = html.escape(bid["url"] or "#")
    return f"""
    <tr><td style="padding:16px 0;border-bottom:1px solid #E5E7EB;">
      <div style="font-size:11px;font-weight:700;letter-spacing:.06em;color:{badge_color};">{badge_text}</div>
      <div style="font-size:16px;font-weight:700;color:#111827;margin:4px 0;">
        <a href="{url}" style="color:#111827;text-decoration:none;">{title}</a>
      </div>
      <div style="font-size:13px;color:#6B7280;">{agency}</div>
      <div style="font-size:13px;color:#374151;margin-top:6px;">
        Due {html.escape(fmt_close(bid["close_date"]))} &middot; <b>{days_left(bid["close_date"], now)}</b>{estimate_html(bid)}
      </div>
      <div style="margin-top:10px;">
        <a href="{url}" style="display:inline-block;background:{ORANGE};color:#fff;font-size:13px;font-weight:700;
           padding:8px 14px;border-radius:6px;text-decoration:none;">View bid</a>
      </div>
    </td></tr>"""


def build_email(bids, now, app_url):
    n = len(bids)
    subject = f"{n} new Utah bid{'s' if n != 1 else ''} for your trades"

    rows = "".join(bid_row(b, now) for b in bids)
    feed_link = (
        f'<p style="margin:24px 0 0;"><a href="{html.escape(app_url)}/feed" '
        f'style="color:{ORANGE};font-weight:700;">Open your feed</a></p>'
        if app_url else ""
    )
    settings = (
        f'<a href="{html.escape(app_url)}/profile" style="color:#6B7280;">alert settings</a>'
        if app_url else "alert settings on your Profile page"
    )
    body = f"""
    <div style="background:#F3F4F6;padding:24px 12px;font-family:Arial,Helvetica,sans-serif;">
      <div style="max-width:600px;margin:0 auto;background:#ffffff;border-radius:10px;padding:28px;">
        <div style="font-size:20px;font-weight:800;color:#111827;">Bid<span style="color:{ORANGE};">Slate</span></div>
        <h1 style="font-size:20px;color:#111827;margin:18px 0 4px;">{html.escape(subject)}</h1>
        <p style="font-size:14px;color:#6B7280;margin:0 0 8px;">Found on Utah public bid portals since your last alert.</p>
        <table width="100%" cellpadding="0" cellspacing="0">{rows}</table>
        {feed_link}
        <p style="font-size:12px;color:#9CA3AF;margin-top:28px;">
          You're getting this because email alerts are on for your BidSlate account.
          To change what you get or turn alerts off, update your {settings}.
        </p>
      </div>
    </div>"""
    return subject, body


def send_email(api_key, sender, to, subject, body):
    resp = requests.post(
        RESEND_URL,
        headers={"Authorization": f"Bearer {api_key}"},
        json={"from": sender, "to": [to], "subject": subject, "html": body},
        timeout=30,
    )
    return resp.status_code, resp.text[:300]


def main():
    dry_run = os.environ.get("DRY_RUN") == "1"
    api_key = os.environ.get("RESEND_API_KEY", "")
    sender = os.environ.get("ALERT_FROM_EMAIL", "")
    app_url = os.environ.get("APP_URL", "").rstrip("/")

    if not dry_run and (not api_key or not sender):
        print("RESEND_API_KEY or ALERT_FROM_EMAIL is missing, so no alerts were sent.")
        print("New bids stay unsent and will go out once those secrets are added.")
        return

    client = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"])
    now = datetime.now(timezone.utc)

    new_bids = (
        client.table("bids")
        .select("id,title,agency,category,trades,close_date,url,estimated_value,duplicate_of")
        .is_("alerted_at", "null")
        .execute()
        .data
    )
    open_bids = [b for b in new_bids
                 if b.get("duplicate_of") is None
                 and (not parse_ts(b["close_date"]) or parse_ts(b["close_date"]) > now)]
    open_bids.sort(key=lambda b: (b["category"] != "concrete", b["close_date"] or "9999"))
    print(f"{len(new_bids)} bids not yet emailed, {len(open_bids)} still open")

    if not new_bids:
        print("Nothing new to send.")
        return

    recipients = client.rpc("alert_recipients").execute().data or []
    print(f"{len(recipients)} contractors have email alerts on")

    sent = failed = 0
    for person in recipients:
        wanted = set(person.get("trades") or [])
        if wanted:
            mine = [b for b in open_bids if wanted & set(b.get("trades") or [b["category"]])]
        else:
            scope = person.get("alert_scope") or "all"
            mine = [b for b in open_bids if scope == "all" or b["category"] == "concrete"]
        if not mine:
            continue
        subject, body = build_email(mine, now, app_url)
        if dry_run:
            print(f"  [dry run] to {person['email']}: {subject} ({len(mine)} bids)")
            continue
        status, detail = send_email(api_key, sender, person["email"], subject, body)
        if 200 <= status < 300:
            sent += 1
            print(f"  sent to {person['email']}: {subject}")
        else:
            failed += 1
            print(f"  FAILED for {person['email']} (status {status}): {detail}")
        time.sleep(0.6)  # stay under Resend's rate limit

    print(f"Sent {sent} alert emails, {failed} failed")

    if dry_run:
        print("DRY_RUN is on, so bids were not marked as emailed.")
        return

    if failed and not sent:
        # Every send failed (bad API key, Resend outage, etc.). Leave the bids
        # unmarked so they go out on the next run once the problem is fixed.
        print("All sends failed, so bids were NOT marked as emailed. They'll retry next run.")
        return

    # Mark every new bid (open or already closed) as handled so it never repeats
    ids = [b["id"] for b in new_bids]
    stamp = now.isoformat()
    for i in range(0, len(ids), 100):
        client.table("bids").update({"alerted_at": stamp}).in_("id", ids[i:i + 100]).execute()
    print(f"Marked {len(ids)} bids as emailed.")


if __name__ == "__main__":
    main()
