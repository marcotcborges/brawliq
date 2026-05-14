import json
import threading
import time
import pandas as pd
import streamlit as st
from db.database import (
    MAX_TAGS_PER_USER,
    init_db,
    create_user,
    get_user_by_username,
    get_user_count,
    update_last_login,
    get_player_tags,
    add_player_tag,
    remove_player_tag,
    get_latest_snapshot,
    save_snapshot,
    save_battles,
    get_brawler_stats,
    get_community_brawler_stats,
    get_total_battles_tracked,
    get_active_users,
)
from services.auth import hash_password, verify_password
from services.brawlstars import get_player, get_player_battlelog, parse_battlelog

MAX_USERS = 100

init_db()


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

st.set_page_config(page_title="BrawlIQ", page_icon="⚡", layout="centered")

if "user_id" not in st.session_state:
    st.session_state.user_id = None
if "username" not in st.session_state:
    st.session_state.username = None
if "selected_tag" not in st.session_state:
    st.session_state.selected_tag = None


def logout():
    st.session_state.user_id = None
    st.session_state.username = None
    st.session_state.selected_tag = None


# ── AUTH PAGES ────────────────────────────────────────────────────────────────

def page_login():
    st.title("⚡ BrawlIQ")
    tab_login, tab_register = st.tabs(["Log in", "Create account"])

    with tab_login:
        with st.form("login_form"):
            username = st.text_input("Username")
            password = st.text_input("Password", type="password")
            submitted = st.form_submit_button("Log in")

        if submitted:
            user = get_user_by_username(username)
            if user and verify_password(password, user["password_hash"]):
                st.session_state.user_id = user["id"]
                st.session_state.username = user["username"]
                update_last_login(user["id"])
                st.rerun()
            else:
                st.error("Invalid username or password.")

    with tab_register:
        with st.form("register_form"):
            new_username = st.text_input("Choose a username")
            new_password = st.text_input("Choose a password", type="password")
            confirm = st.text_input("Confirm password", type="password")
            submitted = st.form_submit_button("Create account")

        if submitted:
            if not new_username or not new_password:
                st.error("Username and password are required.")
            elif new_password != confirm:
                st.error("Passwords do not match.")
            elif get_user_by_username(new_username):
                st.error("Username already taken.")
            elif get_user_count() >= MAX_USERS:
                st.error("BrawlIQ is currently at capacity. Try again later.")
            else:
                create_user(new_username, hash_password(new_password))
                st.success("Account created! You can now log in.")


# ── DASHBOARD ─────────────────────────────────────────────────────────────────

def _fetch_and_store(user_id: int, tag: str) -> dict:
    data = get_player(tag)
    save_snapshot(user_id, tag, json.dumps(data))
    battlelog = get_player_battlelog(tag)
    battles = parse_battlelog(battlelog, tag)
    save_battles(user_id, tag, battles)
    return data


def _render_profile(data: dict, fetched_at: str) -> None:
    st.caption(f"Last updated: {fetched_at} UTC")
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


def _render_my_brawlers(user_id: int, tag: str) -> None:
    rows = get_brawler_stats(user_id, tag)
    if not rows:
        st.info("No battle data yet — hit **Refresh** to load your recent battles.")
        return

    df = pd.DataFrame([dict(r) for r in rows])
    df.columns = ["Brawler", "Games", "Win Rate %", "Star Rate %"]

    total = df["Games"].sum()
    st.caption(f"{total} battles tracked for this tag (last 25 per refresh)")

    # highlight best brawler (≥5 games, highest win rate)
    qualified = df[df["Games"] >= 5]
    if not qualified.empty:
        best = qualified.loc[qualified["Win Rate %"].idxmax()]
        gem_pool = qualified[qualified["Games"] < qualified["Games"].median()]
        gem = gem_pool.loc[gem_pool["Win Rate %"].idxmax()] if not gem_pool.empty else None

        col1, col2 = st.columns(2)
        col1.success(f"**Best brawler:** {best['Brawler']} — {best['Win Rate %']}% win rate ({int(best['Games'])} games)")
        if gem is not None and gem["Brawler"] != best["Brawler"]:
            col2.info(f"**Hidden gem:** {gem['Brawler']} — {gem['Win Rate %']}% win rate but only {int(gem['Games'])} games played")

    st.dataframe(df, use_container_width=True, hide_index=True)


def _render_community_meta() -> None:
    total = get_total_battles_tracked()
    if total < 50:
        st.info(f"Community meta needs more data — {total} battles tracked so far. Share BrawlIQ with friends to grow it!")
        return

    rows = get_community_brawler_stats()
    if not rows:
        st.info("No community data yet.")
        return

    df = pd.DataFrame([dict(r) for r in rows])
    df.columns = ["Brawler", "Games", "Win Rate %", "Star Rate %", "Pick Rate %"]

    st.caption(f"Based on **{total:,}** battles tracked across all BrawlIQ users")

    # top 3 highlights
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


def page_dashboard():
    user = get_user_by_username(st.session_state.username)
    tags = [row["tag"] for row in get_player_tags(user["id"])]

    st.sidebar.write(f"Logged in as **{user['username']}**")
    if st.sidebar.button("Log out"):
        logout()
        st.rerun()
    st.sidebar.link_button("☕ Support BrawlIQ", "https://ko-fi.com/martulio", use_container_width=True)

    st.title("⚡ BrawlIQ")

    tab_profile, tab_meta = st.tabs(["My Profile", "Community Meta"])

    with tab_profile:
        # add tag form
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
                                st.error(f"You can have a maximum of {MAX_TAGS_PER_USER} player tags.")
                            else:
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

        # tag selector
        if st.session_state.selected_tag not in tags:
            st.session_state.selected_tag = tags[0]

        col_select, col_remove, col_refresh = st.columns([3, 1, 1])
        selected = col_select.selectbox(
            "Player tag",
            tags,
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

        # profile sub-tabs
        sub_profile, sub_brawlers = st.tabs(["Stats", "Brawler Performance"])

        with sub_profile:
            snapshot = get_latest_snapshot(user["id"], selected)
            if snapshot:
                _render_profile(json.loads(snapshot["data"]), snapshot["fetched_at"])
            else:
                st.info("Hit **Refresh** to load this player's stats.")

        with sub_brawlers:
            _render_my_brawlers(user["id"], selected)

    with tab_meta:
        _render_community_meta()


# ── ROUTER ────────────────────────────────────────────────────────────────────

if st.session_state.user_id is None:
    page_login()
else:
    page_dashboard()
