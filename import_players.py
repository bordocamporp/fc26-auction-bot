import csv
from db import connect, init_db

CSV_PATH = "data/players.csv"

REQUIRED = [
    "id", "name", "team", "position", "overall", "pace", "shooting",
    "passing", "dribbling", "defending", "physical"
]

def clean_int(value, default=None):
    try:
        if value is None or value == "":
            return default
        return int(float(value))
    except Exception:
        return default

def clean_text(value, default=""):
    if value is None:
        return default
    return str(value)

def main():
    init_db()

    with open(CSV_PATH, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    missing = [c for c in REQUIRED if c not in reader.fieldnames]
    if missing:
        raise ValueError(f"Colonne mancanti nel CSV: {missing}")

    conn = connect()
    cur = conn.cursor()

    for r in rows:
        player_id = clean_text(r.get("id"))

        cur.execute("""
            INSERT OR REPLACE INTO players
            (id, name, team, position, overall, pace, shooting, passing,
             dribbling, defending, physical, nation, league, age, weak_foot,
             skill_moves, image_url, owner_discord_id, sold_price)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    COALESCE((SELECT owner_discord_id FROM players WHERE id = ?), NULL),
                    COALESCE((SELECT sold_price FROM players WHERE id = ?), NULL))
        """, (
            player_id,
            clean_text(r.get("name")),
            clean_text(r.get("team")),
            clean_text(r.get("position")),
            clean_int(r.get("overall"), 0),
            clean_int(r.get("pace"), 0),
            clean_int(r.get("shooting"), 0),
            clean_int(r.get("passing"), 0),
            clean_int(r.get("dribbling"), 0),
            clean_int(r.get("defending"), 0),
            clean_int(r.get("physical"), 0),
            clean_text(r.get("nation")),
            clean_text(r.get("league")),
            clean_int(r.get("age")),
            clean_int(r.get("weak_foot")),
            clean_int(r.get("skill_moves")),
            clean_text(r.get("image_url")),
            player_id,
            player_id
        ))

    conn.commit()
    conn.close()
    print(f"Import completato: {len(rows)} giocatori.")

if __name__ == "__main__":
    main()
