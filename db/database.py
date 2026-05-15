import sqlite3
import os
from datetime import datetime, timedelta

DB_PATH = os.path.join(
    os.getenv("DATA_DIR", os.path.join(os.path.dirname(__file__), "..")),
    "brawliq.db",
)
MAX_TAGS_PER_USER = 4
MAX_TOTAL_TAGS = 1000


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=15000")
    return conn


def init_db() -> None:
    with get_conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id                   INTEGER PRIMARY KEY AUTOINCREMENT,
                username             TEXT    NOT NULL UNIQUE,
                password_hash        TEXT    NOT NULL DEFAULT '',
                email                TEXT,
                google_id            TEXT    UNIQUE,
                reset_token          TEXT,
                reset_token_expires  TEXT,
                player_tag           TEXT,
                created_at           TEXT    NOT NULL DEFAULT (datetime('now')),
                last_login_at        TEXT
            );

            CREATE TABLE IF NOT EXISTS player_tags (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id       INTEGER NOT NULL REFERENCES users(id),
                tag           TEXT    NOT NULL,
                first_seen_at TEXT    NOT NULL DEFAULT (datetime('now')),
                UNIQUE(user_id, tag)
            );

            CREATE TABLE IF NOT EXISTS player_snapshots (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id    INTEGER NOT NULL REFERENCES users(id),
                tag        TEXT,
                fetched_at TEXT    NOT NULL DEFAULT (datetime('now')),
                data       TEXT    NOT NULL
            );

            CREATE TABLE IF NOT EXISTS community_battles (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                battle_time   TEXT    NOT NULL,
                player_tag    TEXT    NOT NULL,
                mode          TEXT,
                type          TEXT,
                map           TEXT,
                brawler_name  TEXT,
                result        TEXT,
                is_star_player INTEGER NOT NULL DEFAULT 0,
                trophy_band   TEXT,
                UNIQUE(battle_time, player_tag)
            );

            CREATE TABLE IF NOT EXISTS battles (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id        INTEGER NOT NULL REFERENCES users(id),
                tag            TEXT    NOT NULL,
                battle_time    TEXT    NOT NULL,
                mode           TEXT,
                type           TEXT,
                map            TEXT,
                result         TEXT,
                brawler_name   TEXT,
                is_star_player INTEGER NOT NULL DEFAULT 0,
                UNIQUE(user_id, tag, battle_time)
            );
        """)
        for stmt in [
            "ALTER TABLE player_snapshots ADD COLUMN tag TEXT",
            "ALTER TABLE battles ADD COLUMN type TEXT",
            "ALTER TABLE users ADD COLUMN email TEXT",
            "ALTER TABLE users ADD COLUMN google_id TEXT",
            "ALTER TABLE users ADD COLUMN reset_token TEXT",
            "ALTER TABLE users ADD COLUMN reset_token_expires TEXT",
            "ALTER TABLE player_tags ADD COLUMN first_seen_at TEXT",
            "ALTER TABLE player_tags ADD COLUMN last_requested_at TEXT",
        ]:
            try:
                conn.execute(stmt)
            except Exception:
                pass
        conn.execute("""
            INSERT OR IGNORE INTO player_tags (user_id, tag)
            SELECT id, player_tag FROM users WHERE player_tag IS NOT NULL
        """)
        # migrate battles unique constraint from (tag, battle_time) to (user_id, tag, battle_time)
        row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='battles'"
        ).fetchone()
        if row and "user_id, tag, battle_time" not in row["sql"]:
            conn.executescript("""
                CREATE TABLE battles_new (
                    id             INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id        INTEGER NOT NULL REFERENCES users(id),
                    tag            TEXT    NOT NULL,
                    battle_time    TEXT    NOT NULL,
                    mode           TEXT,
                    type           TEXT,
                    map            TEXT,
                    result         TEXT,
                    brawler_name   TEXT,
                    is_star_player INTEGER NOT NULL DEFAULT 0,
                    UNIQUE(user_id, tag, battle_time)
                );
                INSERT OR IGNORE INTO battles_new
                    (id, user_id, tag, battle_time, mode, type, map, result, brawler_name, is_star_player)
                SELECT id, user_id, tag, battle_time, mode, type, map, result, brawler_name, is_star_player
                FROM battles;
                DROP TABLE battles;
                ALTER TABLE battles_new RENAME TO battles;
            """)


# --- user helpers ---

def create_user(username: str, password_hash: str, email: str = "") -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO users (username, password_hash, email) VALUES (?, ?, ?)",
            (username, password_hash, email or None),
        )
        return cur.lastrowid


def get_user_by_username(username: str) -> sqlite3.Row | None:
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM users WHERE username = ?", (username,)
        ).fetchone()


def update_last_login(user_id: int) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE users SET last_login_at = datetime('now') WHERE id = ?",
            (user_id,),
        )


def get_user_count() -> int:
    with get_conn() as conn:
        return conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]


def _unique_username(conn: sqlite3.Connection, name: str, email: str) -> str:
    base = name.split()[0].lower() if name else email.split("@")[0].lower()
    base = "".join(c for c in base if c.isalnum())[:20] or "user"
    username = base
    i = 1
    while conn.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone():
        username = f"{base}{i}"
        i += 1
    return username


MAX_USERS = 100
PUBLIC_USERNAME = "__public__"


def get_public_user_id() -> int:
    with get_conn() as conn:
        row = conn.execute("SELECT id FROM users WHERE username = ?", (PUBLIC_USERNAME,)).fetchone()
        if row:
            return row["id"]
        cur = conn.execute(
            "INSERT OR IGNORE INTO users (username, password_hash) VALUES (?, '')",
            (PUBLIC_USERNAME,),
        )
        if cur.lastrowid:
            return cur.lastrowid
        return conn.execute("SELECT id FROM users WHERE username = ?", (PUBLIC_USERNAME,)).fetchone()["id"]


def get_or_create_google_user(google_id: str, email: str, name: str) -> tuple[sqlite3.Row, bool]:
    """Returns (user_row, is_new_user)."""
    with get_conn() as conn:
        user = conn.execute("SELECT * FROM users WHERE google_id = ?", (google_id,)).fetchone()
        if user:
            conn.execute("UPDATE users SET last_login_at = datetime('now') WHERE id = ?", (user["id"],))
            return conn.execute("SELECT * FROM users WHERE id = ?", (user["id"],)).fetchone(), False
        user = conn.execute(
            "SELECT * FROM users WHERE LOWER(email) = LOWER(?)", (email,)
        ).fetchone()
        if user:
            conn.execute("UPDATE users SET google_id = ?, last_login_at = datetime('now') WHERE id = ?", (google_id, user["id"]))
            return conn.execute("SELECT * FROM users WHERE id = ?", (user["id"],)).fetchone(), False
        if conn.execute("SELECT COUNT(*) FROM users WHERE username != ?", (PUBLIC_USERNAME,)).fetchone()[0] >= MAX_USERS:
            raise ValueError("BrawlIQ is currently at capacity. Try again later.")
        username = _unique_username(conn, name, email)
        conn.execute(
            "INSERT INTO users (username, email, google_id, password_hash) VALUES (?, ?, ?, '')",
            (username, email or None, google_id),
        )
        conn.execute("UPDATE users SET last_login_at = datetime('now') WHERE google_id = ?", (google_id,))
        return conn.execute("SELECT * FROM users WHERE google_id = ?", (google_id,)).fetchone(), True


# --- password reset ---

def set_reset_token(username: str, email: str, token: str) -> bool:
    """Set a reset token if username+email match. Returns False if no match."""
    expires = (datetime.utcnow() + timedelta(minutes=30)).strftime("%Y-%m-%d %H:%M:%S")
    with get_conn() as conn:
        cur = conn.execute(
            """UPDATE users SET reset_token = ?, reset_token_expires = ?
               WHERE LOWER(username) = LOWER(?) AND LOWER(email) = LOWER(?)""",
            (token, expires, username, email),
        )
        return cur.rowcount > 0


def get_user_by_reset_token(token: str) -> sqlite3.Row | None:
    with get_conn() as conn:
        return conn.execute(
            """SELECT * FROM users
               WHERE reset_token = ?
                 AND reset_token_expires > datetime('now')""",
            (token,),
        ).fetchone()


def update_password(user_id: int, password_hash: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE users SET password_hash = ?, reset_token = NULL, reset_token_expires = NULL WHERE id = ?",
            (password_hash, user_id),
        )


# --- player tag helpers ---

def get_player_tags(user_id: int) -> list[sqlite3.Row]:
    with get_conn() as conn:
        return conn.execute(
            "SELECT tag FROM player_tags WHERE user_id = ? ORDER BY id",
            (user_id,),
        ).fetchall()


def add_player_tag(user_id: int, tag: str) -> bool:
    with get_conn() as conn:
        per_user = conn.execute(
            "SELECT COUNT(*) FROM player_tags WHERE user_id = ?", (user_id,)
        ).fetchone()[0]
        if per_user >= MAX_TAGS_PER_USER:
            return False
        total = conn.execute("SELECT COUNT(*) FROM player_tags").fetchone()[0]
        if total >= MAX_TOTAL_TAGS:
            return False
        conn.execute(
            "INSERT OR IGNORE INTO player_tags (user_id, tag) VALUES (?, ?)",
            (user_id, tag),
        )
        return True


def remove_player_tag(user_id: int, tag: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "DELETE FROM player_tags WHERE user_id = ? AND tag = ?",
            (user_id, tag),
        )


def touch_player_tag(user_id: int, tag: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE player_tags SET last_requested_at = datetime('now') WHERE user_id = ? AND tag = ?",
            (user_id, tag),
        )


def add_public_tag(tag: str) -> tuple[int, bool]:
    """Add tag under the public user. Returns (pub_user_id, is_new_tag)."""
    pub_id = get_public_user_id()
    with get_conn() as conn:
        existing = conn.execute(
            "SELECT id FROM player_tags WHERE user_id = ? AND tag = ?", (pub_id, tag)
        ).fetchone()
        if existing:
            conn.execute(
                "UPDATE player_tags SET last_requested_at = datetime('now') WHERE user_id = ? AND tag = ?",
                (pub_id, tag),
            )
            return pub_id, False
        total = conn.execute("SELECT COUNT(*) FROM player_tags").fetchone()[0]
        if total >= MAX_TOTAL_TAGS:
            return pub_id, False
        conn.execute(
            "INSERT OR IGNORE INTO player_tags (user_id, tag, last_requested_at) VALUES (?, ?, datetime('now'))",
            (pub_id, tag),
        )
        return pub_id, True


def cleanup_stale_tags(inactive_days: int = 30) -> int:
    """Remove public-user tags not requested in inactive_days. Returns count removed."""
    pub_id = get_public_user_id()
    with get_conn() as conn:
        cur = conn.execute(
            """DELETE FROM player_tags
               WHERE user_id = ? AND (
                   last_requested_at IS NULL OR
                   last_requested_at < datetime('now', ? || ' days')
               )""",
            (pub_id, f"-{inactive_days}"),
        )
        removed = cur.rowcount
        if removed:
            conn.execute(
                """DELETE FROM battles WHERE user_id = ? AND tag NOT IN (
                    SELECT tag FROM player_tags WHERE user_id = ?
                )""",
                (pub_id, pub_id),
            )
            conn.execute(
                """DELETE FROM player_snapshots WHERE user_id = ? AND tag NOT IN (
                    SELECT tag FROM player_tags WHERE user_id = ?
                )""",
                (pub_id, pub_id),
            )
        return removed


def get_active_public_tags() -> list[sqlite3.Row]:
    """Return (id, tag) rows for public tags requested within the last 30 days."""
    pub_id = get_public_user_id()
    with get_conn() as conn:
        return conn.execute(
            """SELECT user_id AS id, tag FROM player_tags
               WHERE user_id = ?
                 AND last_requested_at >= datetime('now', '-30 days')""",
            (pub_id,),
        ).fetchall()


# --- snapshot helpers ---

def save_snapshot(user_id: int, tag: str, data: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "DELETE FROM player_snapshots WHERE user_id = ? AND tag = ?",
            (user_id, tag),
        )
        conn.execute(
            "INSERT INTO player_snapshots (user_id, tag, data) VALUES (?, ?, ?)",
            (user_id, tag, data),
        )


def get_latest_snapshot(user_id: int, tag: str) -> sqlite3.Row | None:
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM player_snapshots WHERE user_id = ? AND tag = ? ORDER BY fetched_at DESC LIMIT 1",
            (user_id, tag),
        ).fetchone()


def get_earliest_tracking_date(user_id: int, tag: str) -> str | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT first_seen_at FROM player_tags WHERE user_id = ? AND tag = ?",
            (user_id, tag),
        ).fetchone()
        if row and row["first_seen_at"]:
            return row["first_seen_at"]
        # fallback for rows added before the first_seen_at column existed
        row = conn.execute(
            "SELECT MIN(battle_time) AS since FROM battles WHERE user_id = ? AND tag = ?",
            (user_id, tag),
        ).fetchone()
        return row["since"] if row else None


# --- battle helpers ---

def save_battles(user_id: int, tag: str, battles: list[dict]) -> None:
    with get_conn() as conn:
        conn.executemany(
            """
            INSERT INTO battles
                (user_id, tag, battle_time, mode, type, map, result, brawler_name, is_star_player)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id, tag, battle_time) DO UPDATE SET
                type = excluded.type
            WHERE battles.type IS NULL
            """,
            [
                (
                    user_id, tag,
                    b["battle_time"], b["mode"], b.get("type"), b.get("map", ""),
                    b["result"], b["brawler_name"], int(b["is_star_player"]),
                )
                for b in battles
            ],
        )


def get_brawler_stats(user_id: int, tag: str, ranked_only: bool = False, since: str | None = None, until: str | None = None) -> list[sqlite3.Row]:
    type_filter = "AND type IN ('ranked','soloRanked','teamRanked')" if ranked_only else ""
    since_filter = "AND battle_time >= ?" if since else ""
    until_filter = "AND battle_time <= ?" if until else ""
    params: list = [user_id, tag] + ([since] if since else []) + ([until] if until else [])
    with get_conn() as conn:
        return conn.execute(
            f"""
            SELECT
                brawler_name,
                COUNT(*)                                                                  AS games,
                ROUND(100.0 * SUM(CASE WHEN result='victory' THEN 1 ELSE 0 END) / COUNT(*), 1) AS win_rate,
                ROUND(100.0 * SUM(is_star_player) / COUNT(*), 1)                         AS star_rate
            FROM battles
            WHERE user_id = ? AND tag = ? AND result IS NOT NULL {type_filter} {since_filter} {until_filter}
            GROUP BY brawler_name
            ORDER BY games DESC
            """,
            params,
        ).fetchall()


def get_mode_stats(user_id: int, tag: str, since: str | None = None, until: str | None = None) -> list[sqlite3.Row]:
    since_filter = "AND battle_time >= ?" if since else ""
    until_filter = "AND battle_time <= ?" if until else ""
    params: list = [user_id, tag] + ([since] if since else []) + ([until] if until else [])
    with get_conn() as conn:
        return conn.execute(
            f"""
            SELECT
                mode,
                COUNT(*) AS games,
                ROUND(100.0 * SUM(CASE WHEN result='victory' THEN 1 ELSE 0 END) / COUNT(*), 1) AS win_rate
            FROM battles
            WHERE user_id = ? AND tag = ? AND result IS NOT NULL {since_filter} {until_filter}
            GROUP BY mode
            ORDER BY games DESC
            """,
            params,
        ).fetchall()


def get_nth_battle_time(user_id: int, tag: str, n: int) -> str | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT battle_time FROM battles WHERE user_id = ? AND tag = ? AND result IS NOT NULL ORDER BY battle_time DESC LIMIT 1 OFFSET ?",
            (user_id, tag, n - 1),
        ).fetchone()
        return row["battle_time"] if row else None


def get_battle_results(user_id: int, tag: str, n: int = 500, since: str | None = None, until: str | None = None) -> list[sqlite3.Row]:
    since_clause = "AND battle_time >= ?" if since else ""
    until_clause = "AND battle_time <= ?" if until else ""
    params: list = [user_id, tag] + ([since] if since else []) + ([until] if until else []) + [n]
    with get_conn() as conn:
        return conn.execute(
            f"SELECT result, type, battle_time FROM battles WHERE user_id = ? AND tag = ? AND result IS NOT NULL {since_clause} {until_clause} ORDER BY battle_time DESC LIMIT ?",
            params,
        ).fetchall()


TROPHY_BAND_ORDER = ["under5k", "5k-15k", "15k-30k", "30k-50k", "50k+"]
TROPHY_BAND_LABELS = {
    "under5k": "🥉 Under 5k",
    "5k-15k":  "🥈 5k – 15k",
    "15k-30k": "🥇 15k – 30k",
    "30k-50k": "💎 30k – 50k",
    "50k+":    "👑 50k+",
}


def get_trophy_band(trophies: int) -> str:
    if trophies < 5000:  return "under5k"
    if trophies < 15000: return "5k-15k"
    if trophies < 30000: return "15k-30k"
    if trophies < 50000: return "30k-50k"
    return "50k+"


def save_community_battles(observations: list[dict], band: str) -> None:
    with get_conn() as conn:
        conn.executemany(
            """INSERT OR IGNORE INTO community_battles
               (battle_time, player_tag, mode, type, map, brawler_name, result, is_star_player, trophy_band)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                (o["battle_time"], o["player_tag"], o["mode"], o.get("type"),
                 o.get("map", ""), o["brawler_name"], o.get("result"),
                 int(o.get("is_star_player", False)), band)
                for o in observations
                if o.get("player_tag") and o.get("brawler_name")
            ],
        )


def _community_where(mode=None, trophy_band=None, ranked_only=False, since=None):
    filters = ["result IS NOT NULL"]
    params  = []
    if mode:
        filters.append("mode = ?"); params.append(mode)
    if trophy_band:
        filters.append("trophy_band = ?"); params.append(trophy_band)
    if ranked_only:
        filters.append("type IN ('ranked','soloRanked','teamRanked')")
    if since:
        filters.append("battle_time >= ?"); params.append(since)
    return " AND ".join(filters), params


def get_community_total(mode=None, trophy_band=None, ranked_only=False, since=None) -> int:
    where, params = _community_where(mode, trophy_band, ranked_only, since)
    with get_conn() as conn:
        return conn.execute(f"SELECT COUNT(*) FROM community_battles WHERE {where}", params).fetchone()[0]


def get_community_available_modes() -> list[str]:
    with get_conn() as conn:
        return [r["mode"] for r in conn.execute(
            "SELECT DISTINCT mode FROM community_battles WHERE mode IS NOT NULL ORDER BY mode"
        ).fetchall()]


def get_community_available_trophy_bands() -> list[str]:
    with get_conn() as conn:
        existing = {r["trophy_band"] for r in conn.execute(
            "SELECT DISTINCT trophy_band FROM community_battles WHERE trophy_band IS NOT NULL"
        ).fetchall()}
    return [b for b in TROPHY_BAND_ORDER if b in existing]


def get_community_brawler_stats(mode=None, trophy_band=None, ranked_only=False, since=None) -> list[sqlite3.Row]:
    where, params = _community_where(mode, trophy_band, ranked_only, since)
    total = get_community_total(mode, trophy_band, ranked_only, since)
    if total == 0:
        return []
    with get_conn() as conn:
        return conn.execute(
            f"""
            SELECT
                brawler_name,
                COUNT(*)                                                                          AS games,
                ROUND(100.0 * SUM(CASE WHEN result='victory' THEN 1 ELSE 0 END) / COUNT(*), 1)  AS win_rate,
                ROUND(100.0 * SUM(is_star_player) / COUNT(*), 1)                                 AS star_rate,
                ROUND(100.0 * COUNT(*) / {total}, 2)                                             AS pick_rate
            FROM community_battles
            WHERE {where}
            GROUP BY brawler_name
            ORDER BY games DESC
            """,
            params,
        ).fetchall()


def get_community_map_stats(mode=None, trophy_band=None, ranked_only=False, since=None) -> list[sqlite3.Row]:
    where, params = _community_where(mode, trophy_band, ranked_only, since)
    with get_conn() as conn:
        return conn.execute(
            f"""
            SELECT map, mode,
                COUNT(*) AS games,
                ROUND(100.0 * SUM(CASE WHEN result='victory' THEN 1 ELSE 0 END) / COUNT(*), 1) AS win_rate,
                ROUND(100.0 * SUM(is_star_player) / COUNT(*), 1) AS star_rate
            FROM community_battles
            WHERE {where} AND map != ''
            GROUP BY map, mode
            ORDER BY games DESC
            """,
            params,
        ).fetchall()


def get_community_mode_stats(trophy_band=None, since=None) -> list[sqlite3.Row]:
    where, params = _community_where(trophy_band=trophy_band, since=since)
    total = get_community_total(trophy_band=trophy_band, since=since)
    if total == 0:
        return []
    with get_conn() as conn:
        return conn.execute(
            f"""
            SELECT mode,
                COUNT(*) AS games,
                ROUND(100.0 * SUM(CASE WHEN result='victory' THEN 1 ELSE 0 END) / COUNT(*), 1) AS win_rate,
                ROUND(100.0 * COUNT(*) / {total}, 1) AS play_rate
            FROM community_battles
            WHERE {where}
            GROUP BY mode
            ORDER BY games DESC
            """,
            params,
        ).fetchall()


def get_total_battles_tracked() -> int:
    with get_conn() as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM (SELECT DISTINCT tag, battle_time FROM battles WHERE result IS NOT NULL)"
        ).fetchone()[0]


def get_all_users_with_email() -> list[sqlite3.Row]:
    with get_conn() as conn:
        return conn.execute(
            "SELECT id, username, email FROM users WHERE email IS NOT NULL AND email != '' AND username != ?",
            (PUBLIC_USERNAME,),
        ).fetchall()


def get_active_users(inactive_days: int = 30) -> list[sqlite3.Row]:
    with get_conn() as conn:
        return conn.execute(
            """
            SELECT u.id, u.username, u.last_login_at, pt.tag
            FROM users u
            JOIN player_tags pt ON pt.user_id = u.id
            WHERE u.last_login_at >= datetime('now', ? || ' days')
            """,
            (f"-{inactive_days}",),
        ).fetchall()


# ── insights queries ──────────────────────────────────────────────────────────

def get_map_stats(user_id: int, tag: str, since: str | None = None, until: str | None = None) -> list[sqlite3.Row]:
    since_f = "AND battle_time >= ?" if since else ""
    until_f = "AND battle_time <= ?" if until else ""
    params: list = [user_id, tag] + ([since] if since else []) + ([until] if until else [])
    with get_conn() as conn:
        return conn.execute(
            f"""
            SELECT
                map,
                mode,
                COUNT(*) AS games,
                ROUND(100.0 * SUM(CASE WHEN result='victory' THEN 1 ELSE 0 END) / COUNT(*), 1) AS win_rate,
                ROUND(100.0 * SUM(is_star_player) / COUNT(*), 1) AS star_rate
            FROM battles
            WHERE user_id = ? AND tag = ? AND result IS NOT NULL AND map != '' {since_f} {until_f}
            GROUP BY map, mode
            ORDER BY games DESC
            """,
            params,
        ).fetchall()


def get_weekly_stats(user_id: int, tag: str, since: str | None = None, until: str | None = None) -> list[sqlite3.Row]:
    since_f = "AND battle_time >= ?" if since else ""
    until_f = "AND battle_time <= ?" if until else ""
    params: list = [user_id, tag] + ([since] if since else []) + ([until] if until else [])
    with get_conn() as conn:
        return conn.execute(
            f"""
            SELECT
                strftime('%Y-W%W',
                    substr(battle_time,1,4)||'-'||substr(battle_time,5,2)||'-'||substr(battle_time,7,2)
                ) AS week,
                COUNT(*) AS games,
                ROUND(100.0 * SUM(CASE WHEN result='victory' THEN 1 ELSE 0 END) / COUNT(*), 1) AS win_rate
            FROM battles
            WHERE user_id = ? AND tag = ? AND result IS NOT NULL {since_f} {until_f}
            GROUP BY week
            ORDER BY week ASC
            """,
            params,
        ).fetchall()


def get_hourly_stats(user_id: int, tag: str, since: str | None = None, until: str | None = None) -> list[sqlite3.Row]:
    since_f = "AND battle_time >= ?" if since else ""
    until_f = "AND battle_time <= ?" if until else ""
    params: list = [user_id, tag] + ([since] if since else []) + ([until] if until else [])
    with get_conn() as conn:
        return conn.execute(
            f"""
            SELECT
                CAST(substr(battle_time, 10, 2) AS INTEGER) AS hour,
                COUNT(*) AS games,
                ROUND(100.0 * SUM(CASE WHEN result='victory' THEN 1 ELSE 0 END) / COUNT(*), 1) AS win_rate
            FROM battles
            WHERE user_id = ? AND tag = ? AND result IS NOT NULL {since_f} {until_f}
            GROUP BY hour
            ORDER BY hour ASC
            """,
            params,
        ).fetchall()


def get_weekday_stats(user_id: int, tag: str, since: str | None = None, until: str | None = None) -> list[sqlite3.Row]:
    since_f = "AND battle_time >= ?" if since else ""
    until_f = "AND battle_time <= ?" if until else ""
    params: list = [user_id, tag] + ([since] if since else []) + ([until] if until else [])
    with get_conn() as conn:
        return conn.execute(
            f"""
            SELECT
                CAST(strftime('%w',
                    substr(battle_time,1,4)||'-'||substr(battle_time,5,2)||'-'||substr(battle_time,7,2)
                ) AS INTEGER) AS dow,
                COUNT(*) AS games,
                ROUND(100.0 * SUM(CASE WHEN result='victory' THEN 1 ELSE 0 END) / COUNT(*), 1) AS win_rate
            FROM battles
            WHERE user_id = ? AND tag = ? AND result IS NOT NULL {since_f} {until_f}
            GROUP BY dow
            ORDER BY dow ASC
            """,
            params,
        ).fetchall()


def get_battles_for_analysis(user_id: int, tag: str, since: str | None = None, until: str | None = None) -> list[sqlite3.Row]:
    since_f = "AND battle_time >= ?" if since else ""
    until_f = "AND battle_time <= ?" if until else ""
    params: list = [user_id, tag] + ([since] if since else []) + ([until] if until else [])
    with get_conn() as conn:
        return conn.execute(
            f"""
            SELECT result, battle_time, brawler_name, mode, type
            FROM battles
            WHERE user_id = ? AND tag = ? AND result IS NOT NULL {since_f} {until_f}
            ORDER BY battle_time ASC
            """,
            params,
        ).fetchall()
