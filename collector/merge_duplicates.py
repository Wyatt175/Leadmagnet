"""
Merges duplicate bids (the same job posted on Bonfire, UDOT or the Public Notice
site) by calling the mark_duplicate_bids() database function. The listing with
the best link stays visible; the others are hidden and kept as extra links.

Settings: SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY (GitHub Actions supplies them).
"""

import os

from supabase import create_client


def main():
    client = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"])
    merged = client.rpc("mark_duplicate_bids").execute().data
    print(f"Merged {merged or 0} duplicate bids.")


if __name__ == "__main__":
    main()
