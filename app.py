import json
import os
import threading
import time
import urllib.parse
from datetime import datetime
import pandas as pd
import streamlit as st
from db.database import (
    MAX_TAGS_PER_USER,
    MAX_TOTAL_TAGS,
    init_db,
    get_user_by_username,
    update_last_login,
    get_player_tags,
    add_player_tag,
    remove_player_tag,
    get_latest_snapshot,
    get_earliest_tracking_date,
    save_snapshot,
    save_battles,
    get_brawler_stats,
    get_mode_stats,
    get_battle_results,
    get_nth_battle_time,
    get_map_stats,
    get_weekly_stats,
    get_hourly_stats,
    get_weekday_stats,
    get_battles_for_analysis,
    get_community_brawler_stats,
    get_total_battles_tracked,
    get_active_users,
    get_or_create_google_user,
    get_public_user_id,
    add_public_tag,
    touch_player_tag,
    cleanup_stale_tags,
    get_active_public_tags,
)
from services.brawlstars import get_player, get_player_battlelog, parse_battlelog
from services.google_auth import get_auth_url, exchange_code
from services.email import notify_admin

APP_URL = os.getenv("APP_URL", "http://localhost:8501")

init_db()


# ── background jobs ───────────────────────────────────────────────────────────

def _log_outbound_ip():
    try:
        import urllib.request
        ip = urllib.request.urlopen("https://ifconfig.me", timeout=5).read().decode()
        print(f"[BrawlIQ] outbound IP: {ip}")
    except Exception:
        pass


def _background_scheduler():
    while True:
        time.sleep(30 * 60)
        try:
            for user in get_active_users(inactive_days=30):
                try:
                    tag = user["tag"]
                    data = get_player(tag)
                    save_snapshot(user["id"], tag, json.dumps(data))
                    bl = get_player_battlelog(tag)
                    save_battles(user["id"], tag, parse_battlelog(bl, tag))
                except Exception:
                    pass
        except Exception:
            pass
        try:
            for row in get_active_public_tags():
                try:
                    pub_id = row["id"]
                    tag = row["tag"]
                    data = get_player(tag)
                    save_snapshot(pub_id, tag, json.dumps(data))
                    bl = get_player_battlelog(tag)
                    save_battles(pub_id, tag, parse_battlelog(bl, tag))
                except Exception:
                    pass
        except Exception:
            pass
        try:
            cleanup_stale_tags(inactive_days=30)
        except Exception:
            pass


threading.Thread(target=_background_scheduler, daemon=True).start()
threading.Thread(target=_log_outbound_ip, daemon=True).start()

# ── page config & session ─────────────────────────────────────────────────────

st.set_page_config(page_title="BrawlIQ", page_icon="⚡", layout="centered")

for key, default in [
    ("user_id", None),
    ("username", None),
    ("selected_tag", None),
    ("public_tag", None),
    ("public_data", None),
    ("public_uid", None),
]:
    if key not in st.session_state:
        st.session_state[key] = default


def logout():
    st.session_state.user_id = None
    st.session_state.username = None
    st.session_state.selected_tag = None


# ── helpers ───────────────────────────────────────────────────────────────────

def _fetch_and_store(user_id: int, tag: str) -> dict:
    data = get_player(tag)
    save_snapshot(user_id, tag, json.dumps(data))
    bl = get_player_battlelog(tag)
    save_battles(user_id, tag, parse_battlelog(bl, tag))
    return data


def _win_streaks(results: list) -> tuple[int, int]:
    """Return (current_streak, best_streak) from results ordered newest-first."""
    current = 0
    for r in results:
        if r["result"] == "victory":
            current += 1
        else:
            break
    best, streak = 0, 0
    for r in reversed(results):
        if r["result"] == "victory":
            streak += 1
            best = max(best, streak)
        else:
            streak = 0
    return current, best


_RANK_LABELS = {
    0: "Unranked",
    1: "Bronze I",   2: "Bronze II",   3: "Bronze III",
    4: "Silver I",   5: "Silver II",   6: "Silver III",
    7: "Gold I",     8: "Gold II",     9: "Gold III",
    10: "Diamond I", 11: "Diamond II", 12: "Diamond III",
    13: "Mythic I",  14: "Mythic II",  15: "Mythic III",
    16: "Legendary I", 17: "Legendary II", 18: "Legendary III",
    19: "Masters",
}


def _rank_label(rank: int) -> str:
    return _RANK_LABELS.get(rank, f"Rank {rank}")


# ── public profile (anonymous lookup) ────────────────────────────────────────

def _render_public_profile(pub_uid: int, tag: str, data: dict) -> None:
    name = data.get("name", tag)
    st.markdown(f"### {name}")

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Trophies", f"{data.get('trophies', 0):,}")
    col2.metric("Highest trophies", f"{data.get('highestTrophies', 0):,}")
    col3.metric("EXP level", data.get("expLevel", "—"))
    club = data.get("club", {})
    col4.metric("Club", club.get("name", "—") if club else "—")

    col1, col2, col3 = st.columns(3)
    col1.metric("3v3 victories", f"{data.get('3vs3Victories', 0):,}")
    col2.metric("Solo victories", f"{data.get('soloVictories', 0):,}")
    col3.metric("Duo victories", f"{data.get('duoVictories', 0):,}")

    results = get_battle_results(pub_uid, tag, n=25)
    if results:
        st.divider()
        last10 = results[:10]
        dots = " ".join(
            "🟢" if r["result"] == "victory" else ("⚫" if r["result"] == "draw" else "🔴")
            for r in last10
        )
        st.markdown(f"**Recent form:** {dots}")
        st.caption("Last 10 games — 🟢 Win  🔴 Loss  ⚫ Draw")
        wins = sum(1 for r in results if r["result"] == "victory")
        wr = round(100 * wins / len(results), 1)
        col1, col2 = st.columns(2)
        col1.metric("Battles tracked", len(results))
        col2.metric("Win rate", f"{wr}%")

    brawler_rows = get_brawler_stats(pub_uid, tag)
    if brawler_rows:
        st.divider()
        st.subheader("Top brawlers")
        df = pd.DataFrame([dict(r) for r in brawler_rows[:10]])
        df.columns = ["Brawler", "Games", "Win Rate %", "Star Rate %"]
        st.dataframe(df, use_container_width=True, hide_index=True)

    st.info("Sign in with Google below to track this player long-term — full Insights, ranked breakdown, and background refresh every 30 minutes.")


# ── home page (unauthenticated) ───────────────────────────────────────────────

def page_home():
    st.markdown(
        """<script>
(function(){
  var e=document.querySelector('meta[name="description"]');
  if(e)e.remove();
  var m=document.createElement('meta');
  m.name='description';
  m.content='BrawlIQ — free Brawl Stars stats tracker. Look up any player tag instantly: win rates, brawler performance, map analytics, tilt detection, and community meta. No login required.';
  document.head.appendChild(m);
})();
</script>""",
        unsafe_allow_html=True,
    )

    st.title("⚡ BrawlIQ")
    st.markdown(
        "**Free Brawl Stars stats tracker.** "
        "Look up any player tag instantly — no account needed. "
        "Sign in to unlock long-term tracking, full Insights, and background refresh."
    )

    # ── public tag search ─────────────────────────────────────────────────────
    with st.form("public_search_form"):
        tag_input = st.text_input(
            "Player tag",
            placeholder="#ABC123",
            label_visibility="collapsed",
        )
        searched = st.form_submit_button("Look up player", use_container_width=True, type="primary")

    if searched and tag_input.strip():
        raw = tag_input.strip().upper()
        tag = raw if raw.startswith("#") else "#" + raw
        with st.spinner("Fetching player data…"):
            try:
                pub_id = get_public_user_id()
                data = get_player(tag)
                save_snapshot(pub_id, tag, json.dumps(data))
                bl = get_player_battlelog(tag)
                save_battles(pub_id, tag, parse_battlelog(bl, tag))
                add_public_tag(tag)
                st.session_state.public_tag = tag
                st.session_state.public_data = data
                st.session_state.public_uid = pub_id
                st.rerun()
            except ValueError as exc:
                st.error(str(exc))
            except Exception as exc:
                st.error(f"Could not find player: {exc}")

    if st.session_state.public_tag and st.session_state.public_data:
        st.divider()
        _render_public_profile(
            st.session_state.public_uid,
            st.session_state.public_tag,
            st.session_state.public_data,
        )

    # ── features ──────────────────────────────────────────────────────────────
    st.divider()
    st.markdown("## What BrawlIQ tracks")
    c1, c2, c3, c4 = st.columns(4)
    c1.markdown("**📊 Win rates**\n\nBy brawler, mode, and map — see exactly where you win and where you struggle.")
    c2.markdown("**🗺️ Map analytics**\n\nYour best and worst maps ranked by win rate across all game modes.")
    c3.markdown("**⏰ Best time to play**\n\nWin rate broken down by hour of day and day of week.")
    c4.markdown("**🔥 Tilt detection**\n\nSpot losing streaks inside sessions so you know when to take a break.")

    st.divider()
    st.markdown("## How it works")
    st.markdown(
        "BrawlIQ connects to the official **Brawl Stars API** and stores every battle it sees. "
        "The longer you're tracked, the more history you build up. "
        "Data refreshes automatically every 30 minutes for all active players.\n\n"
        "**Player Tag** — your unique Brawl Stars ID, like `#ABC123`. "
        "Find it in-game by tapping your profile picture."
    )

    total = get_total_battles_tracked()
    if total > 0:
        st.caption(f"⚡ **{total:,} battles** tracked so far across all BrawlIQ players")

    # ── sign-in section ───────────────────────────────────────────────────────
    st.divider()
    st.markdown("## Unlock full analytics")
    st.markdown(
        "Sign in with Google to track up to **4 player tags** with the complete feature set:\n\n"
        "- Full Insights tab: win rate trend, map performance, tilt analysis, best hour to play\n"
        "- Ranked vs casual breakdown\n"
        "- Personal brawler stats vs community average\n"
        "- Background refresh every 30 minutes\n\n"
        "> **Important:** Log in at least once every **30 days** to keep automatic tracking active "
        "for your tags. Tags from inactive accounts stop being refreshed after 30 days."
    )
    st.link_button("Sign in with Google", get_auth_url(), use_container_width=True, type="primary")


# ── dashboard sections ────────────────────────────────────────────────────────

def _render_profile(data: dict, fetched_at: str, since: str | None, brawlers_by_name: dict | None = None, overall_streaks: tuple[int, int] | None = None) -> None:
    brawlers_by_name = brawlers_by_name or {}

    col_date, _ = st.columns([2, 1])
    col_date.caption(f"Last updated: {fetched_at} UTC")
    if since:
        col_date.caption(f"Tracking since: {since[:10]}")
    st.divider()

    col1, col2, col3 = st.columns(3)
    col1.metric("Player", data.get("name", "—"))
    col2.metric("Tag", data.get("tag", "—"))
    club = data.get("club", {})
    col3.metric("Club", club.get("name", "—") if club else "—")
    st.divider()

    col1, col2, col3 = st.columns(3)
    col1.metric("Trophies", f"{data.get('trophies', 0):,}")
    col2.metric("Highest trophies", f"{data.get('highestTrophies', 0):,}")
    col3.metric("EXP level", data.get("expLevel", "—"))
    st.divider()

    col1, col2, col3 = st.columns(3)
    col1.metric("3v3 victories", f"{data.get('3vs3Victories', 0):,}")
    col2.metric("Solo victories", f"{data.get('soloVictories', 0):,}")
    col3.metric("Duo victories", f"{data.get('duoVictories', 0):,}")

    # ── ranked ────────────────────────────────────────────────────────────────
    ranked_cur = data.get("rankedRank", 0)
    ranked_season = data.get("highestSeasonRankedRank", 0)
    ranked_alltime = data.get("highestAllTimeRankedRank", 0)
    if ranked_cur or ranked_season or ranked_alltime:
        st.divider()
        st.subheader("Ranked")
        col1, col2, col3 = st.columns(3)
        col1.metric("Current rank", _rank_label(ranked_cur))
        col2.metric("Best this season", _rank_label(ranked_season))
        col3.metric("Best all-time", _rank_label(ranked_alltime))
        elo_cur = data.get("rankedElo", 0)
        elo_season = data.get("highestSeasonRankedElo", 0)
        elo_alltime = data.get("highestAllTimeRankedElo", 0)
        if elo_cur or elo_season or elo_alltime:
            col1.caption(f"{elo_cur:,} pts")
            col2.caption(f"{elo_season:,} pts")
            col3.caption(f"{elo_alltime:,} pts")

    # ── win streaks ───────────────────────────────────────────────────────────
    show_streaks = overall_streaks or brawlers_by_name
    if show_streaks:
        st.divider()
        st.subheader("Win streaks")

        if overall_streaks:
            overall_cur, overall_best = overall_streaks
            st.caption("Overall — any brawler, tracked by BrawlIQ")
            col1, col2 = st.columns(2)
            col1.metric("Current streak", overall_cur)
            col2.metric("Best streak tracked", overall_best)

        if brawlers_by_name:
            cur_streaks = [(name, b.get("currentWinStreak", 0)) for name, b in brawlers_by_name.items() if b.get("currentWinStreak", 0) > 0]
            max_streaks = [(name, b.get("maxWinStreak", 0)) for name, b in brawlers_by_name.items() if b.get("maxWinStreak", 0) > 0]
            if cur_streaks or max_streaks:
                st.caption("Per brawler — from the official Brawl Stars API")
                col1, col2 = st.columns(2)
                if cur_streaks:
                    best_cur = max(cur_streaks, key=lambda x: x[1])
                    col1.metric("Current streak", best_cur[1], f"with {best_cur[0].title()}")
                if max_streaks:
                    best_max = max(max_streaks, key=lambda x: x[1])
                    col2.metric("Best streak ever", best_max[1], f"with {best_max[0].title()}")


def _render_my_stats(user_id: int, tag: str, since: str | None = None, until: str | None = None) -> None:
    results = get_battle_results(user_id, tag, n=500, since=since, until=until)
    if not results:
        st.info("No battle data yet — hit **Refresh** to load your recent battles.")
        return

    # recent form (last 10)
    st.subheader("Recent form")
    last10 = results[:10]
    dots = " ".join("🟢" if r["result"] == "victory" else ("⚫" if r["result"] == "draw" else "🔴") for r in last10)
    st.markdown(f"### {dots}")
    st.caption("Last 10 games — 🟢 Win  🔴 Loss  ⚫ Draw")

    st.divider()

    wins = sum(1 for r in results if r["result"] == "victory")
    overall_wr = round(100 * wins / len(results), 1) if results else 0
    current, best = _win_streaks(results)
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Battles tracked", len(results))
    col2.metric("Overall win rate", f"{overall_wr}%")
    col3.metric("Current win streak", current)
    col4.metric("Best win streak", best)

    st.divider()

    # win rate by mode
    mode_rows = get_mode_stats(user_id, tag, since=since, until=until)
    if mode_rows:
        st.subheader("Win rate by mode")
        df = pd.DataFrame([dict(r) for r in mode_rows])
        df.columns = ["Mode", "Games", "Win Rate %"]
        df["Mode"] = df["Mode"].str.replace(r"([A-Z])", r" \1", regex=True).str.strip().str.title()
        st.dataframe(df, use_container_width=True, hide_index=True)


def _render_my_brawlers(user_id: int, tag: str, brawlers_by_name: dict | None = None, since: str | None = None, until: str | None = None) -> None:
    brawlers_by_name = brawlers_by_name or {}
    ranked_only = st.toggle("Ranked only", key="ranked_toggle")
    rows = get_brawler_stats(user_id, tag, ranked_only=ranked_only, since=since, until=until)

    if not rows:
        st.info("No battle data yet — hit **Refresh** to load your recent battles.")
        return

    df = pd.DataFrame([dict(r) for r in rows])
    df.columns = ["Brawler", "Games", "Win Rate %", "Star Rate %"]
    total = df["Games"].sum()
    st.caption(f"{total} battles tracked {'(ranked only)' if ranked_only else ''}")

    qualified = df[df["Games"] >= 5]
    if not qualified.empty:
        best = qualified.loc[qualified["Win Rate %"].idxmax()]
        most_played = df.loc[df["Games"].idxmax()]
        top_star = qualified.loc[qualified["Star Rate %"].idxmax()]

        st.subheader("Your podium")
        col1, col2, col3 = st.columns(3)
        col1.success(f"**Best win rate**\n\n{best['Brawler']}\n\n{best['Win Rate %']}% ({int(best['Games'])} games)")
        col2.info(f"**Most played**\n\n{most_played['Brawler']}\n\n{int(most_played['Games'])} games")
        col3.warning(f"**Top carry**\n\n{top_star['Brawler']}\n\n{top_star['Star Rate %']}% star rate")

        # hidden gem
        gem_pool = qualified[qualified["Games"] < qualified["Games"].median()]
        if not gem_pool.empty:
            gem = gem_pool.loc[gem_pool["Win Rate %"].idxmax()]
            if gem["Win Rate %"] >= 55 and gem["Brawler"] != best["Brawler"]:
                st.info(f"**Hidden gem:** {gem['Brawler']} — {gem['Win Rate %']}% win rate but only {int(gem['Games'])} games played. Play this more!")

    # ── full table with power + streaks ───────────────────────────────────────
    comm_rows = get_community_brawler_stats()
    display = df.copy()
    if comm_rows:
        comm_df = pd.DataFrame([dict(r) for r in comm_rows])[["brawler_name", "win_rate"]]
        comm_df.columns = ["Brawler", "Community Win Rate %"]
        display = display.merge(comm_df, on="Brawler", how="left")
        display["vs Community"] = (display["Win Rate %"] - display["Community Win Rate %"]).round(1)
        display["vs Community"] = display["vs Community"].apply(
            lambda x: f"+{x}%" if x > 0 else f"{x}%" if pd.notna(x) else "—"
        )
        display = display[["Brawler", "Games", "Win Rate %", "Star Rate %", "vs Community"]]

    if brawlers_by_name:
        display["Power"] = display["Brawler"].apply(lambda n: brawlers_by_name.get(n, {}).get("power", "—"))
        display["Cur. Streak"] = display["Brawler"].apply(lambda n: brawlers_by_name.get(n, {}).get("currentWinStreak", 0))
        display["Max Streak"] = display["Brawler"].apply(lambda n: brawlers_by_name.get(n, {}).get("maxWinStreak", 0))

    st.subheader("Full table")
    st.dataframe(display, use_container_width=True, hide_index=True)

    if not qualified.empty:
        st.caption("Play at least 5 games with a brawler to unlock podium insights.")

    # ── upgrade recommendations ───────────────────────────────────────────────
    if brawlers_by_name and not qualified.empty:
        st.divider()
        st.subheader("🎯 Focus recommendations")
        suggestions = []
        for _, row in qualified.iterrows():
            name = row["Brawler"]
            info = brawlers_by_name.get(name, {})
            power = info.get("power", 0)
            gadgets = len(info.get("gadgets", []))
            star_powers = len(info.get("starPowers", []))
            win_rate = row["Win Rate %"]
            games = int(row["Games"])

            if power == 0:
                continue

            tips = []
            if power < 7 and gadgets == 0:
                tips.append("reach power 7 to unlock gadget")
            elif power < 9 and star_powers == 0:
                tips.append("reach power 9 to unlock star power")
            elif power < 11:
                tips.append(f"upgrade from power {power} to 11")

            if win_rate >= 55 and tips:
                suggestions.append({
                    "Brawler": name.title(),
                    "Win Rate %": win_rate,
                    "Games": games,
                    "Power": power,
                    "Tip": tips[0].capitalize(),
                })

        if suggestions:
            sdf = pd.DataFrame(sorted(suggestions, key=lambda x: x["Win Rate %"], reverse=True))
            st.caption("Brawlers where you already win often but haven't maxed out yet — worth the investment.")
            st.dataframe(sdf, use_container_width=True, hide_index=True)
        else:
            st.caption("No upgrade recommendations right now — play more games with each brawler to unlock suggestions.")


def _render_ranked(user_id: int, tag: str, since: str | None = None, until: str | None = None) -> None:
    all_results = get_battle_results(user_id, tag, n=500, since=since, until=until)
    if not all_results:
        st.info("No battle data yet — hit **Refresh** to load your recent battles.")
        return

    ranked_types = {"ranked", "soloRanked", "teamRanked"}
    ranked = [r for r in all_results if r["type"] in ranked_types]
    casual = [r for r in all_results if r["type"] not in ranked_types]

    if not ranked:
        st.info("No ranked battles tracked yet. Play some ranked matches and hit **Refresh**.")
        return

    def win_rate(results):
        if not results:
            return 0.0
        return round(100 * sum(1 for r in results if r["result"] == "victory") / len(results), 1)

    ranked_wr = win_rate(ranked)
    casual_wr = win_rate(casual)
    delta = round(ranked_wr - casual_wr, 1)

    col1, col2, col3 = st.columns(3)
    col1.metric("Ranked win rate", f"{ranked_wr}%")
    col2.metric("Casual win rate", f"{casual_wr}%")
    col3.metric("Ranked vs casual", f"{'+' if delta >= 0 else ''}{delta}%",
                delta_color="normal" if delta >= 0 else "inverse")

    st.divider()

    ranked_rows = get_brawler_stats(user_id, tag, ranked_only=True, since=since, until=until)
    if ranked_rows:
        df = pd.DataFrame([dict(r) for r in ranked_rows])
        df.columns = ["Brawler", "Games", "Win Rate %", "Star Rate %"]
        qualified = df[df["Games"] >= 3]
        if not qualified.empty:
            best = qualified.loc[qualified["Win Rate %"].idxmax()]
            st.success(f"**Best ranked brawler:** {best['Brawler']} — {best['Win Rate %']}% win rate ({int(best['Games'])} ranked games)")
        st.subheader("Ranked brawler breakdown")
        st.dataframe(df, use_container_width=True, hide_index=True)


def _render_community_meta() -> None:
    ranked_only = st.toggle("Ranked only", key="meta_ranked_toggle")
    total = get_total_battles_tracked()
    if total < 50:
        st.info(f"Community meta needs more data — {total} battles tracked so far. Share BrawlIQ to grow it!")
        return

    rows = get_community_brawler_stats(ranked_only=ranked_only)
    if not rows:
        st.info("No community data yet.")
        return

    df = pd.DataFrame([dict(r) for r in rows])
    df.columns = ["Brawler", "Games", "Win Rate %", "Star Rate %", "Pick Rate %"]
    meta_total = sum(r["games"] for r in rows)
    label = "ranked battles" if ranked_only else "battles"
    st.caption(f"Based on **{meta_total:,}** {label} tracked across all BrawlIQ users")

    qualified = df[df["Games"] >= 10]
    if not qualified.empty:
        top_win = qualified.loc[qualified["Win Rate %"].idxmax()]
        top_pick = df.loc[df["Pick Rate %"].idxmax()]
        top_star = qualified.loc[qualified["Star Rate %"].idxmax()]
        col1, col2, col3 = st.columns(3)
        col1.metric("Highest win rate", top_win["Brawler"], f"{top_win['Win Rate %']}%")
        col2.metric("Most picked", top_pick["Brawler"], f"{top_pick['Pick Rate %']}%")
        col3.metric("Most star players", top_star["Brawler"], f"{top_star['Star Rate %']}%")

    st.divider()
    st.dataframe(df, use_container_width=True, hide_index=True)


# ── insights ─────────────────────────────────────────────────────────────────

def _parse_battle_time(bt: str) -> datetime | None:
    try:
        return datetime.strptime(bt[:15], "%Y%m%dT%H%M%S")
    except Exception:
        return None


def _render_insights(user_id: int, tag: str, since: str | None = None, until: str | None = None) -> None:
    # ── map performance ───────────────────────────────────────────────────────
    st.subheader("Map performance")
    map_rows = get_map_stats(user_id, tag, since=since, until=until)
    if map_rows:
        df = pd.DataFrame([dict(r) for r in map_rows])
        df.columns = ["Map", "Mode", "Games", "Win Rate %", "Star Rate %"]
        df["Mode"] = df["Mode"].str.replace(r"([A-Z])", r" \1", regex=True).str.strip().str.title()
        qualified = df[df["Games"] >= 3]
        if not qualified.empty:
            best_map = qualified.loc[qualified["Win Rate %"].idxmax()]
            worst_map = qualified.loc[qualified["Win Rate %"].idxmin()]
            col1, col2 = st.columns(2)
            col1.success(f"**Best map:** {best_map['Map']} — {best_map['Win Rate %']}% ({int(best_map['Games'])} games)")
            col2.error(f"**Worst map:** {worst_map['Map']} — {worst_map['Win Rate %']}% ({int(worst_map['Games'])} games)")
        st.dataframe(df, use_container_width=True, hide_index=True)
    else:
        st.info("No map data yet — hit **Refresh** to load battles.")

    st.divider()

    # ── win rate trend ────────────────────────────────────────────────────────
    st.subheader("Win rate trend")
    week_rows = get_weekly_stats(user_id, tag, since=since, until=until)
    if week_rows:
        wdf = pd.DataFrame([dict(r) for r in week_rows])
        wdf.columns = ["Week", "Games", "Win Rate %"]
        wdf = wdf[wdf["Games"] >= 3]
        if len(wdf) >= 2:
            st.line_chart(wdf.set_index("Week")["Win Rate %"])
            st.caption("Weekly win rate — weeks with fewer than 3 battles are excluded")
        else:
            st.info("Need at least 2 weeks with 3+ battles for the trend chart.")
    else:
        st.info("Not enough battle history for trend analysis.")

    st.divider()

    # ── best time to play ─────────────────────────────────────────────────────
    st.subheader("Best time to play")
    hour_rows = get_hourly_stats(user_id, tag, since=since, until=until)
    day_rows = get_weekday_stats(user_id, tag, since=since, until=until)

    if hour_rows:
        hdf = pd.DataFrame([dict(r) for r in hour_rows])
        hdf.columns = ["Hour", "Games", "Win Rate %"]
        hdf = hdf[hdf["Games"] >= 3]
        if not hdf.empty:
            best_h = hdf.loc[hdf["Win Rate %"].idxmax()]
            worst_h = hdf.loc[hdf["Win Rate %"].idxmin()]
            col1, col2 = st.columns(2)
            col1.success(f"**Best hour:** {int(best_h['Hour']):02d}:00 UTC — {best_h['Win Rate %']}%")
            col2.error(f"**Worst hour:** {int(worst_h['Hour']):02d}:00 UTC — {worst_h['Win Rate %']}%")
            st.bar_chart(hdf.set_index("Hour")["Win Rate %"])
            st.caption("Win rate by hour of day (UTC) — hours with fewer than 3 battles excluded")

    if day_rows:
        day_names = {0: "Sun", 1: "Mon", 2: "Tue", 3: "Wed", 4: "Thu", 5: "Fri", 6: "Sat"}
        ddf = pd.DataFrame([dict(r) for r in day_rows])
        ddf.columns = ["DOW", "Games", "Win Rate %"]
        ddf["Day"] = ddf["DOW"].apply(lambda x: day_names.get(x, str(x)))
        ddf = ddf[ddf["Games"] >= 3]
        if not ddf.empty:
            best_d = ddf.loc[ddf["Win Rate %"].idxmax()]
            worst_d = ddf.loc[ddf["Win Rate %"].idxmin()]
            col1, col2 = st.columns(2)
            col1.success(f"**Best day:** {best_d['Day']} — {best_d['Win Rate %']}%")
            col2.error(f"**Worst day:** {worst_d['Day']} — {worst_d['Win Rate %']}%")
            st.bar_chart(ddf.set_index("Day")["Win Rate %"])

    if not hour_rows and not day_rows:
        st.info("Not enough data for time analysis.")

    st.divider()

    # ── session analysis ──────────────────────────────────────────────────────
    st.subheader("Session analysis")
    battles = get_battles_for_analysis(user_id, tag, since=since, until=until)
    if len(battles) < 5:
        st.info("Need at least 5 battles for session analysis.")
        return

    sessions: list[list[dict]] = []
    current: list[dict] = []
    prev_t: datetime | None = None
    for b in battles:
        t = _parse_battle_time(b["battle_time"])
        if t is None:
            continue
        if prev_t and (t - prev_t).total_seconds() > 1800:
            if current:
                sessions.append(current)
            current = []
        current.append({"result": b["result"]})
        prev_t = t
    if current:
        sessions.append(current)

    if not sessions:
        return

    tilt_sessions = 0
    rows_data = []
    for i, s in enumerate(sessions):
        wins = sum(1 for b in s if b["result"] == "victory")
        wr = round(100 * wins / len(s), 1)
        max_loss = curr_loss = 0
        for b in s:
            curr_loss = curr_loss + 1 if b["result"] != "victory" else 0
            max_loss = max(max_loss, curr_loss)
        tilt = max_loss >= 4
        if tilt:
            tilt_sessions += 1
        rows_data.append({
            "Session": f"#{len(sessions) - i}",
            "Games": len(s),
            "Win Rate %": wr,
            "Max Loss Streak": max_loss,
            "Status": "⚠️ Tilt" if tilt else "✅ OK",
        })

    col1, col2, col3 = st.columns(3)
    col1.metric("Total sessions", len(sessions))
    col2.metric("Avg games / session", round(len(battles) / len(sessions), 1))
    col3.metric("Tilt sessions", tilt_sessions)

    if tilt_sessions:
        st.warning(f"**{tilt_sessions} session(s)** ended with a losing streak of 4+. Consider taking a break when it hits!")

    sdf = pd.DataFrame(rows_data[:20])
    st.dataframe(sdf, use_container_width=True, hide_index=True)
    st.caption("A session = battles within 30 min of each other. Showing last 20 sessions.")


# ── dashboard ─────────────────────────────────────────────────────────────────

def page_dashboard():
    user = get_user_by_username(st.session_state.username)
    tags = [row["tag"] for row in get_player_tags(user["id"])]

    st.sidebar.write(f"Logged in as **{user['username']}**")
    if st.sidebar.button("Log out"):
        logout()
        st.rerun()
    st.sidebar.link_button("☕ Support BrawlIQ", "https://ko-fi.com/martulio", use_container_width=True)

    selected_for_share = st.session_state.get("selected_tag")
    if selected_for_share:
        results_for_share = get_battle_results(user["id"], selected_for_share, n=500)
        if results_for_share:
            wins = sum(1 for r in results_for_share if r["result"] == "victory")
            wr = round(100 * wins / len(results_for_share), 1)
            tweet = (
                f"My Brawl Stars win rate is {wr}% 📊 "
                f"({len(results_for_share)} battles tracked with BrawlIQ — free stats tracker) "
                f"brawliq.fly.dev #BrawlStars"
            )
        else:
            tweet = "Just started tracking my Brawl Stars stats with BrawlIQ — free, no login needed 📊 brawliq.fly.dev #BrawlStars"
    else:
        tweet = "Just started tracking my Brawl Stars stats with BrawlIQ — free, no login needed 📊 brawliq.fly.dev #BrawlStars"

    share_url = "https://twitter.com/intent/tweet?text=" + urllib.parse.quote(tweet)
    st.sidebar.link_button("🐦 Share on Twitter", share_url, use_container_width=True)
    st.sidebar.caption("Log in at least once every 30 days to keep automatic data refresh active for your tags.")

    st.title("⚡ BrawlIQ")

    tab_profile, tab_meta, tab_about = st.tabs(["My Profile", "Community Meta", "How it works"])

    with tab_profile:
        st.caption(
            "A **Player Tag** is the unique ID of a Brawl Stars account (e.g. #ABC123). "
            "You can find it in-game by tapping your profile. "
            f"Each account can track up to **{MAX_TAGS_PER_USER} tags** — useful if you play on multiple accounts."
        )
        if len(tags) < MAX_TAGS_PER_USER:
            with st.form("add_tag_form"):
                new_tag = st.text_input(
                    f"Add Player Tag ({len(tags)}/{MAX_TAGS_PER_USER})",
                    placeholder="#ABC123",
                )
                submitted = st.form_submit_button("Add & load", use_container_width=True)
            if submitted:
                tag = new_tag.strip().upper()
                if not tag:
                    st.error("Please enter a player tag.")
                else:
                    with st.spinner("Validating tag…"):
                        try:
                            _fetch_and_store(user["id"], tag)
                            if not add_player_tag(user["id"], tag):
                                st.error(f"You can have a maximum of {MAX_TAGS_PER_USER} player tags, or BrawlIQ has reached its tracking capacity for now.")
                            else:
                                threading.Thread(
                                    target=notify_admin,
                                    args=("Player tag added", f"User: {user['username']}\nTag: {tag}"),
                                    daemon=True,
                                ).start()
                                st.session_state.selected_tag = tag
                                st.rerun()
                        except ValueError as exc:
                            st.error(str(exc))
                        except Exception as exc:
                            st.error(f"Could not fetch player: {exc}")
        else:
            st.info(f"Maximum of {MAX_TAGS_PER_USER} player tags reached.")

        if not tags:
            st.info("Add a player tag above to get started.")
            return

        st.divider()

        if st.session_state.selected_tag not in tags:
            st.session_state.selected_tag = tags[0]

        col_select, col_remove, col_refresh = st.columns([3, 1, 1])
        selected = col_select.selectbox(
            "Player tag", tags,
            index=tags.index(st.session_state.selected_tag),
            label_visibility="collapsed",
        )
        st.session_state.selected_tag = selected

        if col_remove.button("Remove", use_container_width=True):
            remove_player_tag(user["id"], selected)
            st.session_state.selected_tag = None
            st.rerun()

        if col_refresh.button("Refresh", use_container_width=True):
            with st.spinner("Refreshing…"):
                try:
                    _fetch_and_store(user["id"], selected)
                    st.rerun()
                except Exception as exc:
                    st.error(f"Could not refresh: {exc}")

        # ── filters ───────────────────────────────────────────────────────────
        with st.expander("Filters"):
            col_n, col_from, col_to = st.columns(3)
            filter_n = col_n.number_input(
                "Last N battles (0 = all time)", min_value=0, max_value=10000,
                value=100, step=25, key=f"fn_{selected}",
            ) or None
            filter_from = col_from.date_input("From date", value=None, key=f"ff_{selected}")
            filter_to = col_to.date_input("To date", value=None, key=f"ft_{selected}")

        # Compute since/until in battle_time format (YYYYMMDDTHHmmss.sssZ)
        # Date picker produces "YYYY-MM-DD" which compares wrong against stored format.
        data_since: str | None = None
        data_until: str | None = str(filter_to).replace("-", "") + "T235959.999Z" if filter_to else None
        if filter_from:
            data_since = str(filter_from).replace("-", "") + "T000000.000Z"
        elif filter_n:
            data_since = get_nth_battle_time(user["id"], selected, filter_n)

        snapshot = get_latest_snapshot(user["id"], selected)
        tracking_since = get_earliest_tracking_date(user["id"], selected)
        snap_data = json.loads(snapshot["data"]) if snapshot else {}
        brawlers_by_name = {b["name"]: b for b in snap_data.get("brawlers", [])}

        sub_profile, sub_stats, sub_brawlers, sub_ranked, sub_insights = st.tabs(
            ["Profile", "Stats", "Brawler Performance", "Ranked", "Insights"]
        )

        with sub_profile:
            if snapshot:
                streak_results = get_battle_results(user["id"], selected, n=500)
                overall_streaks = _win_streaks(streak_results) if streak_results else None
                _render_profile(snap_data, snapshot["fetched_at"], tracking_since, brawlers_by_name, overall_streaks)
            else:
                st.info("Hit **Refresh** to load this player's stats.")

        with sub_stats:
            _render_my_stats(user["id"], selected, since=data_since, until=data_until)

        with sub_brawlers:
            _render_my_brawlers(user["id"], selected, brawlers_by_name, since=data_since, until=data_until)

        with sub_ranked:
            _render_ranked(user["id"], selected, since=data_since, until=data_until)

        with sub_insights:
            _render_insights(user["id"], selected, since=data_since, until=data_until)

    with tab_meta:
        _render_community_meta()

    with tab_about:
        st.markdown("""
### How does BrawlIQ work?

BrawlIQ is built on top of the official **[Brawl Stars API](https://developer.brawlstars.com)**.
All stats come directly from Supercell's data — we don't estimate or guess anything.

---

**What is a Player Tag?**
Your unique Brawl Stars ID (e.g. `#ABC123`). Find it in-game by tapping your profile picture.
You can track up to 4 tags per account — handy if you play on multiple accounts.

**How often is data refreshed?**
BrawlIQ fetches fresh data automatically every 30 minutes for all active users.
You can also hit **Refresh** at any time to pull the latest battles manually.

**How much battle history is available?**
The Brawl Stars API returns your last 25 battles. BrawlIQ stores every batch it fetches,
so the longer you're tracked, the more historical data you'll have. Check in regularly to avoid gaps.

**What does "Tracking since" mean?**
The date BrawlIQ first saw your player tag. Stats and win rates are calculated from all battles collected since that date.

**Do I need to stay active?**
Yes — if you don't log in for **30 days**, automatic background refresh stops for your tags.
Log back in and your data will resume being collected.

**What is the Community Meta?**
Aggregated brawler stats (win rate, pick rate, star rate) across **all** BrawlIQ users.
The more players use BrawlIQ, the more accurate and representative the meta becomes.

**What does "vs Community" mean in the brawler table?**
The difference between your personal win rate and the community average for that brawler.
A positive number means you outperform the average BrawlIQ player with that brawler.

**Is my data private?**
Your individual battle history is only visible to you. The Community Meta only uses anonymised, aggregated stats.

---
*BrawlIQ is a fan-made tool and is not affiliated with Supercell.*
        """)



# ── router ────────────────────────────────────────────────────────────────────

oauth_code = st.query_params.get("code")
if oauth_code and st.session_state.user_id is None:
    st.query_params.clear()
    with st.spinner("Signing you in…"):
        try:
            info = exchange_code(oauth_code)
            user, is_new = get_or_create_google_user(info["sub"], info.get("email", ""), info.get("name", ""))
            st.session_state.user_id = user["id"]
            st.session_state.username = user["username"]
            if is_new:
                threading.Thread(
                    target=notify_admin,
                    args=("New user signed up", f"Username: {user['username']}\nEmail: {info.get('email', '—')}\nGoogle name: {info.get('name', '—')}"),
                    daemon=True,
                ).start()
            st.rerun()
        except ValueError as exc:
            st.error(str(exc))
        except Exception as exc:
            st.error(f"Sign-in failed: {exc}")
elif st.session_state.user_id is None:
    page_home()
else:
    page_dashboard()
