import os
import requests
from dotenv import load_dotenv

load_dotenv()

BASE_URL = "https://api.brawlstars.com/v1"


def _headers() -> dict:
    api_key = os.getenv("BRAWLSTARS_API_KEY", "")
    return {"Authorization": f"Bearer {api_key}"}


def _normalize_tag(tag: str) -> str:
    """Ensure the tag starts with # and is uppercased."""
    tag = tag.strip().upper()
    if not tag.startswith("#"):
        tag = "#" + tag
    return tag


def get_player(player_tag: str) -> dict:
    tag = _normalize_tag(player_tag)
    encoded = tag.replace("#", "%23")
    response = requests.get(f"{BASE_URL}/players/{encoded}", headers=_headers(), timeout=10)
    if response.status_code == 404:
        raise ValueError(f"Player '{tag}' not found. Check the tag and try again.")
    if response.status_code == 403:
        raise ValueError("API key rejected — the key may not be authorised for this server's IP address.")
    response.raise_for_status()
    return response.json()


def get_player_battlelog(player_tag: str) -> dict:
    tag = _normalize_tag(player_tag)
    encoded = tag.replace("#", "%23")
    response = requests.get(f"{BASE_URL}/players/{encoded}/battlelog", headers=_headers(), timeout=10)
    if response.status_code == 403:
        raise ValueError("API key rejected — the key may not be authorised for this server's IP address.")
    response.raise_for_status()
    return response.json()


def parse_battlelog_all_players(battlelog: dict, tracked_tag: str) -> list[dict]:
    """Extract battle observations for every player seen in each battle (not just the tracked player)."""
    tag = _normalize_tag(tracked_tag)
    observations = []

    for item in battlelog.get("items", []):
        battle = item.get("battle", {})
        event  = item.get("event", {})
        mode   = battle.get("mode", "")
        b_time = item.get("battleTime", "")
        if not b_time:
            continue

        b_type = battle.get("type")
        b_map  = event.get("map", "")

        if "showdown" in mode.lower():
            threshold = 4 if "solo" in mode.lower() else 2
            for player in battle.get("players", []):
                b = player.get("brawler", {})
                brawler_name = b.get("name")
                player_tag   = player.get("tag", "")
                rank         = player.get("rank")
                if brawler_name and rank is not None and player_tag:
                    observations.append({
                        "player_tag":    player_tag,
                        "battle_time":   b_time,
                        "mode":          mode,
                        "type":          b_type,
                        "map":           b_map,
                        "result":        "victory" if rank <= threshold else "defeat",
                        "brawler_name":  brawler_name,
                        "is_star_player": False,
                    })
        else:
            teams    = battle.get("teams", [])
            star_tag = (battle.get("starPlayer") or {}).get("tag", "").upper()
            tracked_result = battle.get("result")

            tracked_team_idx = None
            for i, team in enumerate(teams):
                for player in team:
                    if player.get("tag", "").upper() == tag:
                        tracked_team_idx = i
                        break
                if tracked_team_idx is not None:
                    break

            flip = {"victory": "defeat", "defeat": "victory"}

            for team_idx, team in enumerate(teams):
                if tracked_team_idx is not None:
                    team_result = tracked_result if team_idx == tracked_team_idx else flip.get(tracked_result, tracked_result)
                else:
                    team_result = None

                for player in team:
                    b            = player.get("brawler", {})
                    brawler_name = b.get("name")
                    player_tag   = player.get("tag", "")
                    if brawler_name and player_tag:
                        observations.append({
                            "player_tag":    player_tag,
                            "battle_time":   b_time,
                            "mode":          mode,
                            "type":          b_type,
                            "map":           b_map,
                            "result":        team_result,
                            "brawler_name":  brawler_name,
                            "is_star_player": star_tag == player_tag.upper(),
                        })

    return observations


def parse_battlelog(battlelog: dict, player_tag: str) -> list[dict]:
    """Extract per-battle stats for a specific player from their battlelog."""
    tag = _normalize_tag(player_tag)
    parsed = []

    for item in battlelog.get("items", []):
        battle = item.get("battle", {})
        event = item.get("event", {})
        mode = battle.get("mode", "")
        battle_time = item.get("battleTime", "")

        if not battle_time:
            continue

        brawler_name = None
        result = None
        is_star_player = False

        if "showdown" in mode.lower():
            b = battle.get("brawler", {})
            brawler_name = b.get("name")
            rank = battle.get("rank")
            if rank is not None:
                threshold = 4 if "solo" in mode.lower() else 2
                result = "victory" if rank <= threshold else "defeat"
        else:
            for team in battle.get("teams", []):
                for player in team:
                    if player.get("tag", "").upper() == tag:
                        b = player.get("brawler", {})
                        brawler_name = b.get("name")
                        result = battle.get("result")
                        sp = battle.get("starPlayer") or {}
                        is_star_player = sp.get("tag", "").upper() == tag
                        break
                if brawler_name:
                    break

        if brawler_name:
            parsed.append({
                "battle_time": battle_time,
                "mode": mode,
                "type": battle.get("type"),
                "map": event.get("map", ""),
                "result": result,
                "brawler_name": brawler_name,
                "is_star_player": is_star_player,
            })

    return parsed
