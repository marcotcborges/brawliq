"""
Bootstrap Community Meta by BFS-expanding from signed-in users' battle logs.

Round 0 — fetch known user tags, discover teammates/opponents
Round 1 — fetch those discovered tags, get ~25 battles each
Round 2 — optional further expansion (--rounds 3)

Run on production:
    fly ssh console --app brawliq -C "python scripts/bootstrap_community.py"
    fly ssh console --app brawliq -C "python scripts/bootstrap_community.py --rounds 3"

Run locally (needs prod DB or .env with API key):
    python scripts/bootstrap_community.py --dry-run
"""
import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from dotenv import load_dotenv
load_dotenv()

from db.database import (
    add_public_tag,
    get_active_users,
    get_community_total,
    get_player_tags,
    get_public_user_id,
    get_trophy_band,
    save_community_battles,
)
from services.brawlstars import (
    get_player,
    get_player_battlelog,
    parse_battlelog_all_players,
)

DELAY = 0.35  # seconds between API calls — well under the ~10 req/s limit


def _fetch_one(tag: str, dry_run: bool) -> tuple[int, set[str]]:
    """Fetch a single tag. Returns (observations_saved, discovered_tags)."""
    if dry_run:
        return 0, set()
    try:
        data = get_player(tag)
        time.sleep(DELAY)
        bl = get_player_battlelog(tag)
        time.sleep(DELAY)
        band = get_trophy_band(data.get("trophies", 0))
        all_obs = parse_battlelog_all_players(bl, tag)
        save_community_battles(all_obs, band)
        discovered = {o["player_tag"] for o in all_obs if o.get("player_tag")}
        return len(all_obs), discovered
    except Exception as e:
        print(f"    ✗ {tag}: {e}")
        return 0, set()


def _seed_tags() -> list[str]:
    """Collect all tags already being tracked (signed-in users + public)."""
    tags = set()
    for user in get_active_users(inactive_days=90):
        tags.add(user["tag"])
    pub_id = get_public_user_id()
    for row in get_player_tags(pub_id):
        tags.add(row["tag"])
    return list(tags)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--rounds", type=int, default=2,
                        help="BFS depth (default 2; use 3 for deeper expansion)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print what would be fetched without calling the API")
    args = parser.parse_args()

    print(f"\n{'[DRY RUN] ' if args.dry_run else ''}BrawlIQ community bootstrap — {args.rounds} rounds\n")

    total_obs = 0
    queue = _seed_tags()
    seen = set(queue)

    for round_num in range(args.rounds):
        if not queue:
            print("No tags to process — stopping early.")
            break

        print(f"Round {round_num}: {len(queue)} tag(s) to fetch")
        next_queue = []

        for i, tag in enumerate(queue, 1):
            print(f"  [{i}/{len(queue)}] {tag}", end=" ", flush=True)
            obs, discovered = _fetch_one(tag, args.dry_run)
            total_obs += obs
            print(f"→ {obs} obs", end="")

            new_tags = []
            for t in discovered:
                if t not in seen:
                    _, is_new = add_public_tag(t)
                    seen.add(t)
                    if is_new:
                        new_tags.append(t)

            if new_tags:
                print(f", +{len(new_tags)} new tags", end="")
                next_queue.extend(new_tags)

            print()

        community_total = get_community_total() if not args.dry_run else "?"
        print(f"\nAfter round {round_num}: {total_obs} obs this run | {community_total} total in DB\n")
        queue = next_queue

    print(f"Done. {total_obs} new observations saved. Community total: {get_community_total() if not args.dry_run else '?'}\n")


if __name__ == "__main__":
    main()
