import sqlite3
import os

_DATA_DIR = os.getenv("DATA_DIR", os.path.join(os.path.dirname(__file__), ".."))
DB_PATH = os.path.join(_DATA_DIR, "brawliq.db")
MAX_TAGS_PER_USER = 4


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with get_conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                username      TEXT    NOT NULL UNIQUE,
                password_hash TEXT    NOT NULL,
                player_tag    TEXT,
                created_at    TEXT    NOT NULL DEFAULT (datetime('now')),
                last_login_at TEXT
            );

            CREATE TABLE IF NOT EXISTS player_tags (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id    INTEGER NOT NULL REFERENCES users(id),
                tag        TEXT    NOT NULL,
                UNIQUE(user_id, tag)
            );

            CREATE TABLE IF NOT EXISTS player_snapshots (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id    INTEGER NOT NULL REFERENCES users(id),
                tag        TEXT,
                fetched_at TEXT    NOT NULL DEFAULT (datetime('now')),
                data       TEXT    NOT NULL
            );

            CREATE TABLE IF NOT EXISTS battles (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id        INTEGER NOT NULL REFERENCES users(id),
                tag            TEXT    NOT NULL,
                battle_time    TEXT    NOT NULL,
                mode           TEXT,
                map            TEXT,
                result         TEXT,
                brawler_name   TEXT,
                is_star_player INTEGER NOT NULL DEFAULT 0,
                UNIQUE(tag, battle_time)
            );
        """)
        try:
            conn.execute("ALTER TABLE player_snapshots ADD COLUMN tag TEXT")
        except Exception:
            pass
        conn.execute("""
            INSERT OR IGNORE INTO player_tags (user_id, tag)
            SELECT id, player_tag FROM users WHERE player_tag IS NOT NULL
        """)


# --- user helpers ---

def create_user(username: str, password_hash: str) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO users (username, password_hash) VALUES (?, ?)",
            (username, password_hash),
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


# --- player tag helpers ---

def get_player_tags(user_id: int) -> list[sqlite3.Row]:
    with get_conn() as conn:
        return conn.execute(
            "SELECT tag FROM player_tags WHERE user_id = ? ORDER BY id",
            (user_id,),
        ).fetchall()


def add_player_tag(user_id: int, tag: str) -> bool:
    with get_conn() as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM player_tags WHERE user_id = ?", (user_id,)
        ).fetchone()[0]
        if count >= MAX_TAGS_PER_USER:
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


# --- snapshot helpers ---

def save_snapshot(user_id: int, tag: str, data: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO player_snapshots (user_id, tag, data) VALUES (?, ?, ?)",
            (user_id, tag, data),
        )


def get_latest_snapshot(user_id: int, tag: str) -> sqlite3.Row | None:
    with get_conn() as conn:
        return conn.execute(
            """
            SELECT * FROM player_snapshots
            WHERE user_id = ? AND tag = ?
            ORDER BY fetched_at DESC
            LIMIT 1
            """,
            (user_id, tag),
        ).fetchone()


# --- battle helpers ---

def save_battles(user_id: int, tag: str, battles: list[dict]) -> None:
    with get_conn() as conn:
        conn.executemany(
            """
            INSERT OR IGNORE INTO battles
                (user_id, tag, battle_time, mode, map, result, brawler_name, is_star_player)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    user_id, tag,
                    b["battle_time"], b["mode"], b.get("map", ""),
                    b["result"], b["brawler_name"], int(b["is_star_player"]),
                )
                for b in battles
            ],
        )


def get_brawler_stats(user_id: int, tag: str) -> list[sqlite3.Row]:
    with get_conn() as conn:
        return conn.execute(
            """
            SELECT
                brawler_name,
                COUNT(*)                                                        AS games,
                ROUND(100.0 * SUM(CASE WHEN result = 'victory' THEN 1 ELSE 0 END) / COUNT(*), 1) AS win_rate,
                ROUND(100.0 * SUM(is_star_player) / COUNT(*), 1)               AS star_rate
            FROM battles
            WHERE user_id = ? AND tag = ? AND result IS NOT NULL
            GROUP BY brawler_name
            ORDER BY games DESC
            """,
            (user_id, tag),
        ).fetchall()


def get_community_brawler_stats() -> list[sqlite3.Row]:
    with get_conn() as conn:
        return conn.execute(
            """
            SELECT
                brawler_name,
                COUNT(*)                                                        AS games,
                ROUND(100.0 * SUM(CASE WHEN result = 'victory' THEN 1 ELSE 0 END) / COUNT(*), 1) AS win_rate,
                ROUND(100.0 * SUM(is_star_player) / COUNT(*), 1)               AS star_rate,
                ROUND(100.0 * COUNT(*) / (SELECT COUNT(*) FROM battles WHERE result IS NOT NULL), 2) AS pick_rate
            FROM battles
            WHERE result IS NOT NULL
            GROUP BY brawler_name
            ORDER BY games DESC
            """,
        ).fetchall()


def get_total_battles_tracked() -> int:
    with get_conn() as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM battles WHERE result IS NOT NULL"
        ).fetchone()[0]


# --- cron helpers ---

def get_active_users(inactive_days: int = 30) -> list[sqlite3.Row]:
    """Return one row per (user, tag) for users active within inactive_days."""
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
