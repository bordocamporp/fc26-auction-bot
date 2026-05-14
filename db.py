import sqlite3
from pathlib import Path

DB_PATH = Path("fc26_auction.db")

PLAYER_COLUMNS = {
    "id": "TEXT PRIMARY KEY",
    "name": "TEXT NOT NULL",
    "team": "TEXT",
    "position": "TEXT",
    "overall": "INTEGER",
    "pace": "INTEGER",
    "shooting": "INTEGER",
    "passing": "INTEGER",
    "dribbling": "INTEGER",
    "defending": "INTEGER",
    "physical": "INTEGER",
    "nation": "TEXT",
    "league": "TEXT",
    "age": "INTEGER",
    "weak_foot": "INTEGER",
    "skill_moves": "INTEGER",
    "image_url": "TEXT",
    "owner_discord_id": "TEXT",
    "sold_price": "INTEGER"
}

def connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def ensure_column(cur, table, column, definition):
    cur.execute(f"PRAGMA table_info({table})")
    existing = {row[1] for row in cur.fetchall()}
    if column not in existing:
        cur.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

def init_db():
    conn = connect()
    cur = conn.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS players (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL
    )
    """)

    for column, definition in PLAYER_COLUMNS.items():
        if column in ("id", "name"):
            continue
        ensure_column(cur, "players", column, definition)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS managers (
        discord_id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        budget INTEGER NOT NULL DEFAULT 500
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS auctions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        player_id TEXT NOT NULL,
        status TEXT NOT NULL,
        highest_bid INTEGER DEFAULT 0,
        highest_bidder_id TEXT,
        channel_id TEXT,
        message_id TEXT
    )
    """)

    conn.commit()
    conn.close()

def reset_auction_state():
    conn = connect()
    cur = conn.cursor()
    cur.execute("DELETE FROM auctions WHERE status = 'open'")
    conn.commit()
    conn.close()
