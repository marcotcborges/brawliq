import json
import os
import threading
import time
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


threading.Thread(target=_background_scheduler, daemon=True).start()
threading.Thread(target=_log_outbound_ip, daemon=True).start()

# ── page config & session ─────────────────────────────────────────────────────

st.set_page_config(page_title="BrawlIQ", page_icon="⚡", layout="centered")

for key, default in [
    ("user_id", None),
    ("username", None),
    ("selected_tag", None),
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


# ── auth pages ────────────────────────────────────────────────────────────────

def page_login():
    st.title("⚡ BrawlIQ")
    st.markdown("### Track your Brawl Stars stats")
    st.markdown("Sign in with your Google account to get started.")
    st.link_button("Sign in with Google", get_auth_url(), use_container_width=True)


# ── dashboard sections ────────────────────────────────────────────────────────

def _render_profile(data: dict, fetched_at: str, since: str | None) -> None:
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


def _render_my_brawlers(user_id: int, tag: str, since: str | None = None, until: str | None = None) -> None:
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

        # vs community comparison
        comm_rows = get_community_brawler_stats()
        if comm_rows:
            comm_df = pd.DataFrame([dict(r) for r in comm_rows])[["brawler_name", "win_rate"]]
            comm_df.columns = ["Brawler", "Community Win Rate %"]
            merged = df.merge(comm_df, on="Brawler", how="left")
            merged["vs Community"] = (merged["Win Rate %"] - merged["Community Win Rate %"]).round(1)
            merged["vs Community"] = merged["vs Community"].apply(
                lambda x: f"+{x}%" if x > 0 else f"{x}%" if pd.notna(x) else "—"
            )
            display = merged[["Brawler", "Games", "Win Rate %", "Star Rate %", "vs Community"]]
            st.subheader("Full table")
            st.dataframe(display, use_container_width=True, hide_index=True)
        else:
            st.dataframe(df, use_container_width=True, hide_index=True)
    else:
        st.dataframe(df, use_container_width=True, hide_index=True)
        st.caption("Play at least 5 games with a brawler to unlock podium insights.")


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

        sub_profile, sub_stats, sub_brawlers, sub_ranked, sub_insights = st.tabs(
            ["Profile", "Stats", "Brawler Performance", "Ranked", "Insights"]
        )

        with sub_profile:
            snapshot = get_latest_snapshot(user["id"], selected)
            tracking_since = get_earliest_tracking_date(user["id"], selected)
            if snapshot:
                _render_profile(json.loads(snapshot["data"]), snapshot["fetched_at"], tracking_since)
            else:
                st.info("Hit **Refresh** to load this player's stats.")

        with sub_stats:
            _render_my_stats(user["id"], selected, since=data_since, until=data_until)

        with sub_brawlers:
            _render_my_brawlers(user["id"], selected, since=data_since, until=data_until)

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
    page_login()
else:
    page_dashboard()
