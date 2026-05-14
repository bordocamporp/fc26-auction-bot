import pandas as pd
from db import connect, init_db

CSV_PATH = "data/players.csv"

REQUIRED = [
    "id", "name", "team", "position", "overall", "pace", "shooting",
    "passing", "dribbling", "defending", "physical"
]

OPTIONAL = ["nation", "league", "age", "weak_foot", "skill_moves", "image_url"]

def clean_int(value, default=None):
    try:
        if pd.isna(value):
            return default
        return int(value)
    except Exception:
        return default

def clean_text(value, default=""):
    if pd.isna(value):
        return default
    return str(value)

def main():
    init_db()
    df = pd.read_csv(CSV_PATH)

    missing = [c for c in REQUIRED if c not in df.columns]
    if missing:
        raise ValueError(f"Colonne mancanti nel CSV: {missing}")

    conn = connect()
    cur = conn.cursor()

    for _, r in df.iterrows():
        player_id = clean_text(r["id"])

        values = {
            "id": player_id,
            "name": clean_text(r["name"]),
            "team": clean_text(r["team"]),
            "position": clean_text(r["position"]),
            "overall": clean_int(r["overall"], 0),
            "pace": clean_int(r["pace"], 0),
            "shooting": clean_int(r["shooting"], 0),
            "passing": clean_int(r["passing"], 0),
            "dribbling": clean_int(r["dribbling"], 0),
            "defending": clean_int(r["defending"], 0),
            "physical": clean_int(r["physical"], 0),
            "nation": clean_text(r["nation"]) if "nation" in df.columns else "",
            "league": clean_text(r["league"]) if "league" in df.columns else "",
            "age": clean_int(r["age"]) if "age" in df.columns else None,
            "weak_foot": clean_int(r["weak_foot"]) if "weak_foot" in df.columns else None,
            "skill_moves": clean_int(r["skill_moves"]) if "skill_moves" in df.columns else None,
            "image_url": clean_text(r["image_url"]) if "image_url" in df.columns else "",
        }

        cur.execute("""
            INSERT OR REPLACE INTO players
            (id, name, team, position, overall, pace, shooting, passing,
             dribbling, defending, physical, nation, league, age, weak_foot,
             skill_moves, image_url, owner_discord_id, sold_price)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    COALESCE((SELECT owner_discord_id FROM players WHERE id = ?), NULL),
                    COALESCE((SELECT sold_price FROM players WHERE id = ?), NULL))
        """, (
            values["id"], values["name"], values["team"], values["position"],
            values["overall"], values["pace"], values["shooting"], values["passing"],
            values["dribbling"], values["defending"], values["physical"], values["nation"],
            values["league"], values["age"], values["weak_foot"], values["skill_moves"],
            values["image_url"], player_id, player_id
        ))

    conn.commit()
    conn.close()
    print(f"Import completato: {len(df)} giocatori.")

if __name__ == "__main__":
    main()
