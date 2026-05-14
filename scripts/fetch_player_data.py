"""
Fetch Brawl Stars player data for all active users and store snapshots.

Run manually or via cron:
    python -m scripts.fetch_player_data
    python -m scripts.fetch_player_data --inactive-days 14

A user is considered inactive (and skipped) if they haven't logged in within
--inactive-days days. Default is 30 days.
"""

import argparse
import json
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from db.database import get_active_users, save_snapshot, save_battles, init_db
from services.brawlstars import get_player, get_player_battlelog, parse_battlelog


def run(inactive_days: int) -> None:
    init_db()
    users = get_active_users(inactive_days=inactive_days)
    print(f"Fetching data for {len(users)} active user(s) (inactive threshold: {inactive_days} days)")

    success, skipped = 0, 0
    for user in users:
        tag = user["tag"]
        try:
            data = get_player(tag)
            save_snapshot(user["id"], tag, json.dumps(data))
            battlelog = get_player_battlelog(tag)
            battles = parse_battlelog(battlelog, tag)
            save_battles(user["id"], tag, battles)
            print(f"  [OK] {user['username']} ({tag}) — {len(battles)} battles stored")
            success += 1
        except Exception as exc:
            print(f"  [SKIP] {user['username']} ({tag}): {exc}")
            skipped += 1

    print(f"\nDone — {success} fetched, {skipped} skipped.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Fetch Brawl Stars data for active users.")
    parser.add_argument(
        "--inactive-days",
        type=int,
        default=30,
        help="Skip users who haven't logged in within this many days (default: 30)",
    )
    args = parser.parse_args()
    run(args.inactive_days)
