import os
import asyncio
import random
import unicodedata
import shutil
from pathlib import Path
from datetime import datetime, UTC
from PIL import Image, ImageDraw, ImageFont
import discord
import psycopg2
from discord.ext import commands
from discord import app_commands
from dotenv import load_dotenv
from psycopg2.extras import RealDictCursor
from db import connect, init_db, reset_auction_state
from card_generator import create_player_card

load_dotenv()
print("[BOOT] Avvio bot.py")
print("[PATCH FINAL] Nessuna query WHERE players.id::text = parametro numerico; uso CAST(%s AS BIGINT)")

TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID = os.getenv("GUILD_ID", "1392747701308751943")
AUCTION_CHANNEL_ID = os.getenv("AUCTION_CHANNEL_ID", "1504825224422756463")
AUCTION_LOG_CHANNEL_ID = os.getenv("AUCTION_LOG_CHANNEL_ID", "1504830394908803142")
ADMIN_ROLE_ID = os.getenv("ADMIN_ROLE_ID", "1398342848436240434")
SEARCH_CHANNEL_ID = os.getenv("SEARCH_CHANNEL_ID", "1504833349414551703")
SPAM_CHANNEL_ID = "1504846794142781480"
ROSE_CHANNEL_ID = "1504847438727610519"
SCAMBI_CHANNEL_ID = "1504847601361616996"
REQUEST_ROLE_ID = "1398323695558332604"
PRE_ISCRITTO_ROLE_ID = "1398323859056365599"

RESULTS_CHANNEL_ID = "1504874612805337229"
STANDINGS_CHANNEL_ID = "1504874671064223784"
STATS_CHANNEL_ID = "1504874788349542431"
CALENDAR_CHANNEL_ID = "1504884471286075532"
LEAGUE_PLAYER_ROLE_ID = "1398332847655358554"
LEAGUE_ADMIN_ROLE_ID = "1398342848436240434"

# === FC26 ISCRIZIONI AUTOMATICHE ===
SIGNUP_REQUEST_CHANNEL_ID = os.getenv("SIGNUP_REQUEST_CHANNEL_ID", "1504868857624399872")   # RICHIESTE ISCRIZIONI
SIGNUP_STAFF_CHANNEL_ID = os.getenv("SIGNUP_STAFF_CHANNEL_ID", "1506320879015952535")     # LOG ISCRIZIONI / staff richieste
SIGNUP_REJECT_CHANNEL_ID = os.getenv("SIGNUP_REJECT_CHANNEL_ID", "1506320840168308911")    # CANALE ISCRIZIONI RIFIUTATE
SIGNUP_ACCEPT_CHANNEL_ID = os.getenv("SIGNUP_ACCEPT_CHANNEL_ID", "1506320769964183742")    # CANALE ISCRIZIONI ACCETTATE
MEDIA_CHANNEL_ID = "1506321171493163199"
SIGNUP_PENDING_ROLE_ID = PRE_ISCRITTO_ROLE_ID        # 1505180973208440954
SIGNUP_REGISTERED_ROLE_ID = LEAGUE_PLAYER_ROLE_ID    # 1505181066695016619

# Ruoli autorizzati a gestire ACCETTA/RIFIUTA e assegnazione club
SIGNUP_STAFF_ROLE_IDS = {
    "1398342848436240434",
    "1398358193197027408",
}

# Club e campionati NON sono più hardcoded nel file.
# Il database Supabase/PostgreSQL è la fonte principale dei dati.
# Tabelle usate dal bot: fc26_clubs, championships, championship_groups, ecc.


# ================= MEDIA SYSTEM - SOLO NEWS IMPORTANTI =================

MEDIA_CHANNEL_ID = "1506321171493163199"
MEDIA_TRANSFER_MIN_OVERALL = 85
MEDIA_TRANSFER_MIN_PRICE = 150
MEDIA_SPECIAL_MATCH_MIN_GOALS = 5
PLAYER_REPORT_CHANNEL_ID = "1506321044519125062"

TOP_CLUBS_FOR_MEDIA = {
    "Real Madrid", "Barcellona", "Atletico Madrid", "Manchester City",
    "Manchester United", "Liverpool", "Arsenal", "Chelsea", "Tottenham",
    "PSG", "Bayern Monaco", "Borussia Dortmund", "Inter", "Milan",
    "Juventus", "Napoli", "Roma", "Benfica", "Porto", "Ajax"
}

TROPHY_KEYWORDS_FOR_MEDIA = {
    "campionato", "coppa nazionale", "champions", "europa league",
    "conference league", "finale", "semifinale", "trofeo", "titolo"
}


def is_important_transfer(overall=None, price=None):
    return safe_int(overall) >= MEDIA_TRANSFER_MIN_OVERALL or safe_int(price) >= MEDIA_TRANSFER_MIN_PRICE


def is_important_match(total_goals=0, is_final=False, is_semifinal=False, is_derby=False, is_big_match=False):
    return (
        bool(is_final)
        or bool(is_semifinal)
        or bool(is_derby)
        or bool(is_big_match)
        or safe_int(total_goals) >= MEDIA_SPECIAL_MATCH_MIN_GOALS
    )


def is_important_trophy(title="", competition=""):
    check = normalize_text(f"{title} {competition}")
    return any(normalize_text(word) in check for word in TROPHY_KEYWORDS_FOR_MEDIA)


def is_important_manager_change(club_name="", inherited=False, has_trophies=False):
    return bool(inherited) or bool(has_trophies) or str(club_name).strip() in TOP_CLUBS_FOR_MEDIA


async def publish_media_news(
    guild,
    title,
    description,
    *,
    category="generic",
    club_name=None,
    player_overall=None,
    price=None,
    total_goals=None,
    is_final=False,
    is_semifinal=False,
    is_derby=False,
    is_big_match=False,
    inherited=False,
    has_trophies=False,
    force=False
):
    """
    Pubblica nel canale media SOLO notizie importanti.

    Pubblica se:
    - force=True
    - trasferimento con OVR >= 85 o prezzo >= 150
    - trofeo/finale/competizione importante
    - partita speciale: finale, semifinale, derby, big match o 5+ gol
    - cambio manager importante: top club, club ereditato o club con trofei
    """

    important = bool(force)

    if category == "transfer":
        important = important or is_important_transfer(player_overall, price)

    elif category == "match":
        important = important or is_important_match(
            total_goals=total_goals or 0,
            is_final=is_final,
            is_semifinal=is_semifinal,
            is_derby=is_derby,
            is_big_match=is_big_match
        )

    elif category == "trophy":
        important = True

    elif category == "manager_change":
        important = important or is_important_manager_change(
            club_name=club_name or "",
            inherited=inherited,
            has_trophies=has_trophies
        )

    else:
        important = important or is_important_trophy(title, description)

    if not important:
        return False

    channel = None
    if guild:
        channel = guild.get_channel(int(MEDIA_CHANNEL_ID))

    if not channel:
        try:
            channel = await bot.fetch_channel(int(MEDIA_CHANNEL_ID))
        except Exception:
            channel = None

    if not channel:
        return False

    embed = discord.Embed(
        title=title,
        description=description,
        color=discord.Color.gold()
    )
    embed.set_footer(text="BordoCampo FC26 Media • News importanti")
    await channel.send(embed=embed)
    return True


async def publish_transfer_news_if_important(guild, club_name, player_name, price=0, overall=0):
    return await publish_media_news(
        guild,
        "📰 BREAKING NEWS DI MERCATO",
        (
            f"Il club **{club_name}** piazza un colpo importante:\n"
            f"⚽ **{player_name}**\n"
            f"⭐ Overall: **{overall}**\n"
            f"💰 Operazione: **{price} crediti**"
        ),
        category="transfer",
        club_name=club_name,
        player_overall=overall,
        price=price
    )


async def publish_trophy_news(guild, club_name, trophy_name, manager_name=None, bonus_budget=None):
    extra = ""
    if manager_name:
        extra += f"\n👤 Manager: **{manager_name}**"
    if bonus_budget is not None:
        extra += f"\n💰 Bonus budget: **+{bonus_budget} crediti**"

    return await publish_media_news(
        guild,
        "🏆 TROPHY NEWS",
        f"**{club_name}** conquista **{trophy_name}**!{extra}",
        category="trophy",
        club_name=club_name,
        force=True
    )


async def publish_manager_change_news_if_important(guild, club_name, old_manager=None, new_manager=None, inherited=True, has_trophies=False):
    old_text = old_manager or "precedente gestione"
    new_text = new_manager or "nuovo manager"

    return await publish_media_news(
        guild,
        "🧠 NUOVA ERA IN PANCHINA",
        (
            f"Il club **{club_name}** passa da **{old_text}** a **{new_text}**.\n"
            f"La squadra eredita rosa, budget e percorso sportivo già esistente."
        ),
        category="manager_change",
        club_name=club_name,
        inherited=inherited,
        has_trophies=has_trophies
    )





async def send_backup_notification(message: str):
    try:
        channel = bot.get_channel(BACKUP_NOTIFICATION_CHANNEL_ID)
        if channel:
            embed = discord.Embed(
                title="💾 Backup database",
                description=message,
                color=discord.Color.green()
            )
            await channel.send(embed=embed)
    except Exception as e:
        print(f"[BACKUP] Errore invio notifica backup: {e}")


async def automatic_daily_backup_loop():
    await bot.wait_until_ready()

    while not bot.is_closed():
        try:
            path, error = create_database_backup("daily_auto")

            if path:
                print(f"[BACKUP] Backup automatico creato: {path}")
                await send_backup_notification(
                    f"✅ Backup automatico creato correttamente.\n```{path}```"
                )
            else:
                print(f"[BACKUP] Errore backup automatico: {error}")

        except Exception as e:
            print(f"[BACKUP] Errore loop backup automatico: {e}")

        # 24 ore
        await asyncio.sleep(86400)


# ======================================================================

# ===================================

BOT_ONLY_BYPASS_ROLE_IDS = {
    "1398342848436240434",
    "1398358193197027408",
}

BOT_ONLY_CHANNELS = {
    1504884471286075532,  # calendario
    1504874612805337229,  # risultati
    1504874671064223784,  # classifiche
    1504874788349542431,  # statistiche
    1504825224422756463,  # asta
    1504847601361616996,  # scambi
    1504833349414551703,  # ricerca-giocatori
    1504846794142781480,  # spam-chat
    1504847438727610519,  # rose
}

DEFAULT_BUDGET = 500
MIN_RAISE = 10
AUCTION_SECONDS = 45
ANTI_SNIPE_THRESHOLD = 10
ANTI_SNIPE_EXTENSION = 20
MARKET_TAX = 5

MAX_GK = 2
MAX_DEF = 6
MAX_MID = 6
MAX_ATT = 4

intents = discord.Intents.default()
# Necessario per recuperare i membri del server quando lo staff accetta/rifiuta
# e per assegnare/rimuovere i ruoli. Va attivato anche nel Discord Developer Portal.
intents.members = True
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree

auction_timers = {}
auction_last_bids = {}

GRAPHICS_DIR = Path('generated_graphics')
GRAPHICS_DIR.mkdir(exist_ok=True)
WALKOUT_GIF = 'https://media.giphy.com/media/3oriO0OEd9QIDdllqo/giphy.gif'


def get_guild():
    return discord.Object(id=int(GUILD_ID)) if GUILD_ID else None


def normalize_text(value):
    value = str(value or "").lower()
    value = unicodedata.normalize("NFKD", value)
    return "".join(c for c in value if not unicodedata.combining(c))


def is_admin(interaction: discord.Interaction):
    # Operazioni staff normali: ruolo owner staff + ruolo staff limitato.
    return can_use_normal_staff(interaction.user)


def is_search_channel(interaction: discord.Interaction):
    if not SEARCH_CHANNEL_ID:
        return True

    return str(interaction.channel_id) == str(SEARCH_CHANNEL_ID)


def is_spam_channel(interaction: discord.Interaction):
    return str(interaction.channel_id) == str(SPAM_CHANNEL_ID)

def is_rose_channel(interaction: discord.Interaction):
    return str(interaction.channel_id) == str(ROSE_CHANNEL_ID)

def is_scambi_channel(interaction: discord.Interaction):
    return str(interaction.channel_id) == str(SCAMBI_CHANNEL_ID)


def is_results_channel(interaction: discord.Interaction):
    return str(interaction.channel_id) == str(RESULTS_CHANNEL_ID)

def is_standings_channel(interaction: discord.Interaction):
    return str(interaction.channel_id) == str(STANDINGS_CHANNEL_ID)

def is_stats_channel(interaction: discord.Interaction):
    return str(interaction.channel_id) == str(STATS_CHANNEL_ID)

def is_calendar_channel(interaction: discord.Interaction):
    return str(interaction.channel_id) == str(CALENDAR_CHANNEL_ID)

def is_league_admin(interaction: discord.Interaction):
    if LEAGUE_ADMIN_ROLE_ID:
        return any(str(role.id) == str(LEAGUE_ADMIN_ROLE_ID) for role in getattr(interaction.user, "roles", []))
    return is_admin(interaction)


def safe_int(value, default=0):
    try:
        if value in ("", None):
            return default
        return int(float(value))
    except Exception:
        return default


async def safe_send(interaction: discord.Interaction, *args, **kwargs):
    """Invia una risposta Discord in modo sicuro.

    Regole:
    - non sostituire mai interaction.response.send_message con questa funzione;
    - usala solo con: await safe_send(interaction, ...);
    - se il comando è già stato deferito, usa automaticamente followup.
    """
    try:
        if interaction.response.is_done():
            return await interaction.followup.send(*args, **kwargs)
        return await interaction.response.send_message(*args, **kwargs)
    except discord.NotFound as e:
        # 10062 Unknown interaction: Discord ha invalidato l'interazione perché
        # il comando non ha risposto/deferito entro circa 3 secondi.
        print(f"[SAFE SEND NOTFOUND] Interazione scaduta o sconosciuta: {e}")
    except discord.HTTPException as e:
        print(f"[SAFE SEND HTTP ERROR] {e}")
    except Exception as e:
        print(f"[SAFE SEND ERROR] {type(e).__name__}: {e}")
    return None


async def safe_defer(interaction: discord.Interaction, *, ephemeral=False, thinking=False):
    """Esegue defer una sola volta; evita Unknown Interaction, doppie risposte e ricorsione."""
    try:
        if not interaction.response.is_done():
            return await interaction.response.defer(ephemeral=ephemeral, thinking=thinking)
    except discord.NotFound as e:
        print(f"[SAFE DEFER NOTFOUND] Interazione già scaduta: {e}")
    except discord.HTTPException as e:
        print(f"[SAFE DEFER HTTP ERROR] {e}")
    except Exception as e:
        print(f"[SAFE DEFER ERROR] {type(e).__name__}: {e}")
    return None


def base_price_from_overall(overall):
    overall = safe_int(overall)

    if 60 <= overall <= 70:
        return 10
    if 71 <= overall <= 75:
        return 20
    if 76 <= overall <= 79:
        return 50
    if 80 <= overall <= 90:
        return 100
    if overall >= 91:
        return 150

    return 5


def role_group(position):
    pos = normalize_text(position).upper()

    if pos in {"GK", "POR"}:
        return "GK"

    defenders = {"CB", "LB", "RB", "LWB", "RWB", "DC", "TS", "TD", "DIF"}
    midfielders = {"CDM", "CM", "CAM", "LM", "RM", "MCO", "CDC", "CC", "CEN"}
    attackers = {"ST", "CF", "LW", "RW", "LF", "RF", "ATT", "AS", "AD", "P"}

    if pos in defenders:
        return "DEF"
    if pos in midfielders:
        return "MID"
    if pos in attackers:
        return "ATT"

    return "OTHER"


def role_limit(group):
    if group == "GK":
        return MAX_GK
    if group == "DEF":
        return MAX_DEF
    if group == "MID":
        return MAX_MID
    if group == "ATT":
        return MAX_ATT
    return 99


def role_label(group):
    labels = {
        "GK": "Portieri",
        "DEF": "Difensori",
        "MID": "Centrocampisti",
        "ATT": "Attaccanti",
        "OTHER": "Altro"
    }
    return labels.get(group, group)


def ensure_extra_tables():
    conn = connect()
    cur = conn.cursor()

    # ================= ASTE: schema PostgreSQL/Supabase =================
    try:
        cur.execute("""
        CREATE TABLE IF NOT EXISTS auctions (
            id SERIAL PRIMARY KEY,
            player_id TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'open',
            highest_bid INTEGER DEFAULT 0,
            highest_bidder_id TEXT,
            channel_id TEXT,
            message_id TEXT,
            created_by TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            closed_at TIMESTAMP
        )
        """)
    except Exception as e:
        print(f"[DB] Errore creazione auctions: {e}")

    for sql in [
        "ALTER TABLE auctions ADD COLUMN IF NOT EXISTS player_id TEXT",
        "ALTER TABLE auctions ADD COLUMN IF NOT EXISTS status TEXT DEFAULT 'open'",
        "ALTER TABLE auctions ADD COLUMN IF NOT EXISTS highest_bid INTEGER DEFAULT 0",
        "ALTER TABLE auctions ADD COLUMN IF NOT EXISTS highest_bidder_id TEXT",
        "ALTER TABLE auctions ADD COLUMN IF NOT EXISTS channel_id TEXT",
        "ALTER TABLE auctions ADD COLUMN IF NOT EXISTS message_id TEXT",
        "ALTER TABLE auctions ADD COLUMN IF NOT EXISTS created_by TEXT",
        "ALTER TABLE auctions ADD COLUMN IF NOT EXISTS created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP",
        "ALTER TABLE auctions ADD COLUMN IF NOT EXISTS closed_at TIMESTAMP"
    ]:
        try:
            cur.execute(sql)
        except Exception:
            pass

    try:
        cur.execute("CREATE INDEX IF NOT EXISTS idx_auctions_status ON auctions(status)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_auctions_player ON auctions(player_id)")
    except Exception:
        pass
    # ================================================================


    # Compatibilità PostgreSQL/Supabase: alcune versioni hanno manager_name invece di name.
    try:
        cur.execute("ALTER TABLE managers ADD COLUMN IF NOT EXISTS name TEXT")
    except Exception:
        pass
    try:
        cur.execute("ALTER TABLE managers ADD COLUMN IF NOT EXISTS manager_name TEXT")
    except Exception:
        pass
    try:
        cur.execute("UPDATE managers SET name = COALESCE(name, manager_name, discord_id) WHERE name IS NULL")
    except Exception:
        pass
    try:
        cur.execute("UPDATE managers SET manager_name = COALESCE(manager_name, name, discord_id) WHERE manager_name IS NULL")
    except Exception:
        pass


    cur.execute("""
    CREATE TABLE IF NOT EXISTS bid_history (
        id SERIAL PRIMARY KEY,
        auction_id INTEGER NOT NULL,
        player_id TEXT NOT NULL,
        bidder_id TEXT NOT NULL,
        bidder_name TEXT,
        amount INTEGER NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS transfer_history (
        id SERIAL PRIMARY KEY,
        player_id TEXT NOT NULL,
        player_name TEXT,
        manager_id TEXT NOT NULL,
        manager_name TEXT,
        price INTEGER DEFAULT 0,
        source TEXT DEFAULT 'auction',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS blacklist_players (
        player_id TEXT PRIMARY KEY,
        reason TEXT,
        created_by TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS trade_offers (
        id SERIAL PRIMARY KEY,
        proposer_id TEXT NOT NULL,
        proposer_name TEXT,
        target_id TEXT NOT NULL,
        target_name TEXT,
        offer_player_id TEXT,
        request_player_id TEXT,
        credits_to_target INTEGER DEFAULT 0,
        credits_to_proposer INTEGER DEFAULT 0,
        status TEXT DEFAULT 'pending',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS league_settings (
        key TEXT PRIMARY KEY,
        value TEXT
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS real_team_assignments (
        discord_id TEXT PRIMARY KEY,
        manager_name TEXT,
        team_name TEXT,
        avg_overall DOUBLE PRECISION,
        assigned_budget INTEGER,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)

    cur.execute("""
        INSERT INTO league_settings (key, value)
        VALUES ('mode', 'fantacalcio')
        ON CONFLICT (key) DO NOTHING
    """)

    cur.execute("""
        INSERT INTO league_settings (key, value)
        VALUES ('market_open', 'closed')
        ON CONFLICT (key) DO NOTHING
    """)


    cur.execute("""
    CREATE TABLE IF NOT EXISTS championships (
        id SERIAL PRIMARY KEY,
        name TEXT NOT NULL,
        status TEXT DEFAULT 'active',
        group_count INTEGER DEFAULT 1,
        teams_per_group INTEGER DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS championship_groups (
        id SERIAL PRIMARY KEY,
        championship_id INTEGER NOT NULL,
        name TEXT NOT NULL
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS championship_players (
        id SERIAL PRIMARY KEY,
        championship_id INTEGER NOT NULL,
        group_id INTEGER NOT NULL,
        discord_id TEXT NOT NULL,
        display_name TEXT NOT NULL
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS championship_matches (
        id SERIAL PRIMARY KEY,
        championship_id INTEGER NOT NULL,
        group_id INTEGER NOT NULL,
        round_number INTEGER NOT NULL,
        home_id TEXT NOT NULL,
        away_id TEXT NOT NULL,
        home_name TEXT,
        away_name TEXT,
        home_goals INTEGER,
        away_goals INTEGER,
        status TEXT DEFAULT 'pending',
        submitted_by TEXT,
        confirm_by TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS match_scorers (
        id SERIAL PRIMARY KEY,
        match_id INTEGER NOT NULL,
        scorer_player_id TEXT,
        scorer_name TEXT NOT NULL,
        team_owner_id TEXT NOT NULL,
        goals INTEGER DEFAULT 1
    )
    """)

    # Tabelle sistema iscrizioni FC26
    cur.execute("""
    CREATE TABLE IF NOT EXISTS signup_requests (
        id SERIAL PRIMARY KEY,
        discord_id TEXT NOT NULL,
        discord_name TEXT,
        real_name TEXT,
        age TEXT,
        platform TEXT,
        game_id TEXT,
        club_preferences TEXT,
        status TEXT DEFAULT 'pending',
        club_name TEXT,
        handled_by TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        handled_at TIMESTAMP
    )
    """)

    try:
        cur.execute("ALTER TABLE signup_requests ADD COLUMN IF NOT EXISTS club_preferences TEXT")
    except Exception:
        pass

    cur.execute("""
    CREATE TABLE IF NOT EXISTS fc26_clubs (
        name TEXT PRIMARY KEY,
        league TEXT,
        assigned_to TEXT,
        assigned_at TIMESTAMP,
        previous_owner_id TEXT,
        previous_owner_name TEXT
    )
    """)

    # Migrazione per chi aveva già la vecchia tabella senza colonna league.
    try:
        cur.execute("ALTER TABLE fc26_clubs ADD COLUMN IF NOT EXISTS league TEXT")
    except Exception:
        pass
    try:
        cur.execute("ALTER TABLE fc26_clubs ADD COLUMN IF NOT EXISTS previous_owner_id TEXT")
    except Exception:
        pass
    try:
        cur.execute("ALTER TABLE fc26_clubs ADD COLUMN IF NOT EXISTS previous_owner_name TEXT")
    except Exception:
        pass

    # I club e i campionati vengono letti da Supabase/PostgreSQL.
    # Non vengono più inseriti automaticamente da liste hardcoded nel codice.

    # Tabelle extra: coppe, premi, hall of fame, media e offerte/controfferte
    cur.execute("""
    CREATE TABLE IF NOT EXISTS national_cups (
        id SERIAL PRIMARY KEY,
        championship_id INTEGER NOT NULL,
        group_id INTEGER,
        name TEXT NOT NULL,
        status TEXT DEFAULT 'active',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS national_cup_matches (
        id SERIAL PRIMARY KEY,
        cup_id INTEGER NOT NULL,
        round_number INTEGER NOT NULL,
        home_id TEXT,
        away_id TEXT,
        home_name TEXT,
        away_name TEXT,
        home_goals INTEGER,
        away_goals INTEGER,
        status TEXT DEFAULT 'pending',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS european_cups (
        id SERIAL PRIMARY KEY,
        championship_id INTEGER NOT NULL,
        name TEXT NOT NULL,
        cup_type TEXT NOT NULL,
        season_number INTEGER DEFAULT 1,
        qualification_mode TEXT DEFAULT 'random',
        status TEXT DEFAULT 'active',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS european_cup_players (
        id SERIAL PRIMARY KEY,
        cup_id INTEGER NOT NULL,
        discord_id TEXT NOT NULL,
        display_name TEXT NOT NULL
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS hall_of_fame (
        id SERIAL PRIMARY KEY,
        season TEXT,
        competition TEXT,
        winner_id TEXT,
        winner_name TEXT,
        club_name TEXT,
        prize_budget INTEGER DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS media_news (
        id SERIAL PRIMARY KEY,
        title TEXT NOT NULL,
        body TEXT NOT NULL,
        created_by TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS player_trade_offers (
        id SERIAL PRIMARY KEY,
        proposer_id TEXT NOT NULL,
        proposer_name TEXT,
        target_id TEXT NOT NULL,
        target_name TEXT,
        player_id TEXT NOT NULL,
        player_name TEXT,
        amount INTEGER DEFAULT 0,
        counter_amount INTEGER,
        status TEXT DEFAULT 'pending',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP
    )
    """)


    # ================= SITO WEB: classifiche / risultati / tabellone =================
    # Queste tabelle sono lette da Next.js su Vercel.
    # Il bot aggiorna qui i risultati confermati e il sito si aggiorna automaticamente.
    cur.execute("""
    CREATE TABLE IF NOT EXISTS standings (
        id SERIAL PRIMARY KEY,
        competition_name TEXT,
        competition_type TEXT,
        club_name TEXT,
        logo_url TEXT,
        played INTEGER DEFAULT 0,
        wins INTEGER DEFAULT 0,
        draws INTEGER DEFAULT 0,
        losses INTEGER DEFAULT 0,
        goals_for INTEGER DEFAULT 0,
        goals_against INTEGER DEFAULT 0,
        points INTEGER DEFAULT 0,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)

    for sql in [
        "ALTER TABLE standings ADD COLUMN IF NOT EXISTS competition_name TEXT",
        "ALTER TABLE standings ADD COLUMN IF NOT EXISTS competition_type TEXT",
        "ALTER TABLE standings ADD COLUMN IF NOT EXISTS club_name TEXT",
        "ALTER TABLE standings ADD COLUMN IF NOT EXISTS logo_url TEXT",
        "ALTER TABLE standings ADD COLUMN IF NOT EXISTS played INTEGER DEFAULT 0",
        "ALTER TABLE standings ADD COLUMN IF NOT EXISTS wins INTEGER DEFAULT 0",
        "ALTER TABLE standings ADD COLUMN IF NOT EXISTS draws INTEGER DEFAULT 0",
        "ALTER TABLE standings ADD COLUMN IF NOT EXISTS losses INTEGER DEFAULT 0",
        "ALTER TABLE standings ADD COLUMN IF NOT EXISTS goals_for INTEGER DEFAULT 0",
        "ALTER TABLE standings ADD COLUMN IF NOT EXISTS goals_against INTEGER DEFAULT 0",
        "ALTER TABLE standings ADD COLUMN IF NOT EXISTS points INTEGER DEFAULT 0",
        "ALTER TABLE standings ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP",
    ]:
        try:
            cur.execute(sql)
        except Exception as e:
            print(f"[SITE SYNC] Errore alter standings: {e}")

    cur.execute("""
    CREATE TABLE IF NOT EXISTS match_results (
        id SERIAL PRIMARY KEY,
        source_table TEXT,
        source_match_id TEXT,
        competition_name TEXT NOT NULL,
        competition_type TEXT NOT NULL,
        round TEXT,
        home_team TEXT NOT NULL,
        away_team TEXT NOT NULL,
        home_score INTEGER DEFAULT 0,
        away_score INTEGER DEFAULT 0,
        winner TEXT,
        status TEXT DEFAULT 'played',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)

    for sql in [
        "ALTER TABLE match_results ADD COLUMN IF NOT EXISTS source_table TEXT",
        "ALTER TABLE match_results ADD COLUMN IF NOT EXISTS source_match_id TEXT",
        "ALTER TABLE match_results ADD COLUMN IF NOT EXISTS competition_name TEXT",
        "ALTER TABLE match_results ADD COLUMN IF NOT EXISTS competition_type TEXT",
        "ALTER TABLE match_results ADD COLUMN IF NOT EXISTS round TEXT",
        "ALTER TABLE match_results ADD COLUMN IF NOT EXISTS home_team TEXT",
        "ALTER TABLE match_results ADD COLUMN IF NOT EXISTS away_team TEXT",
        "ALTER TABLE match_results ADD COLUMN IF NOT EXISTS home_score INTEGER DEFAULT 0",
        "ALTER TABLE match_results ADD COLUMN IF NOT EXISTS away_score INTEGER DEFAULT 0",
        "ALTER TABLE match_results ADD COLUMN IF NOT EXISTS winner TEXT",
        "ALTER TABLE match_results ADD COLUMN IF NOT EXISTS status TEXT DEFAULT 'played'",
        "ALTER TABLE match_results ADD COLUMN IF NOT EXISTS created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP",
        "ALTER TABLE match_results ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP",
    ]:
        try:
            cur.execute(sql)
        except Exception as e:
            print(f"[SITE SYNC] Errore alter match_results: {e}")

    cur.execute("""
    CREATE TABLE IF NOT EXISTS cup_matches (
        id SERIAL PRIMARY KEY,
        source_table TEXT,
        source_match_id TEXT,
        competition_name TEXT,
        round TEXT,
        home_team TEXT,
        away_team TEXT,
        home_score INTEGER DEFAULT 0,
        away_score INTEGER DEFAULT 0,
        winner TEXT,
        status TEXT DEFAULT 'played',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)

    for sql in [
        "ALTER TABLE cup_matches ADD COLUMN IF NOT EXISTS source_table TEXT",
        "ALTER TABLE cup_matches ADD COLUMN IF NOT EXISTS source_match_id TEXT",
        "ALTER TABLE cup_matches ADD COLUMN IF NOT EXISTS competition_name TEXT",
        "ALTER TABLE cup_matches ADD COLUMN IF NOT EXISTS round TEXT",
        "ALTER TABLE cup_matches ADD COLUMN IF NOT EXISTS home_team TEXT",
        "ALTER TABLE cup_matches ADD COLUMN IF NOT EXISTS away_team TEXT",
        "ALTER TABLE cup_matches ADD COLUMN IF NOT EXISTS home_score INTEGER DEFAULT 0",
        "ALTER TABLE cup_matches ADD COLUMN IF NOT EXISTS away_score INTEGER DEFAULT 0",
        "ALTER TABLE cup_matches ADD COLUMN IF NOT EXISTS winner TEXT",
        "ALTER TABLE cup_matches ADD COLUMN IF NOT EXISTS status TEXT DEFAULT 'played'",
        "ALTER TABLE cup_matches ADD COLUMN IF NOT EXISTS created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP",
        "ALTER TABLE cup_matches ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP",
    ]:
        try:
            cur.execute(sql)
        except Exception as e:
            print(f"[SITE SYNC] Errore alter cup_matches: {e}")

    try:
        cur.execute("CREATE INDEX IF NOT EXISTS idx_standings_comp ON standings(competition_name, competition_type)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_match_results_source ON match_results(source_table, source_match_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_cup_matches_source ON cup_matches(source_table, source_match_id)")
    except Exception:
        pass
    # ======================================================================

    conn.commit()
    conn.close()


def player_embed(player, title="FC26 Player Card"):
    sold = player["sold_price"]
    owner = player["owner_discord_id"]

    embed = discord.Embed(
        title=f"{title}: {player['name']}",
        description=f"**{player['position']}** • {player['team']} • OVR **{player['overall']}**",
        color=discord.Color.gold() if safe_int(player["overall"]) >= 85 else discord.Color.dark_grey()
    )

    embed.add_field(name="PAC", value=str(player["pace"]), inline=True)
    embed.add_field(name="SHO", value=str(player["shooting"]), inline=True)
    embed.add_field(name="PAS", value=str(player["passing"]), inline=True)
    embed.add_field(name="DRI", value=str(player["dribbling"]), inline=True)
    embed.add_field(name="DEF", value=str(player["defending"]), inline=True)
    embed.add_field(name="PHY", value=str(player["physical"]), inline=True)

    extra = []
    if player["nation"]:
        extra.append(f"🌍 {player['nation']}")
    if player["league"]:
        extra.append(f"🏆 {player['league']}")
    if player["age"]:
        extra.append(f"🎂 {player['age']} anni")
    if player["weak_foot"]:
        extra.append(f"WF {player['weak_foot']}★")
    if player["skill_moves"]:
        extra.append(f"SM {player['skill_moves']}★")

    if extra:
        embed.add_field(name="Info", value=" • ".join(extra), inline=False)

    if owner:
        embed.add_field(name="Stato", value=f"✅ Assegnato per **{sold}** crediti", inline=False)
    else:
        embed.add_field(name="Stato", value="🟢 Libero", inline=False)

    embed.set_footer(text=f"ID giocatore: {player['id']} • FC26 Auction Bot")
    return embed


def format_auction_timer(remaining):
    remaining = max(0, safe_int(remaining))
    total = max(1, safe_int(AUCTION_SECONDS, 45))
    minutes = remaining // 60
    seconds = remaining % 60

    if remaining <= 5:
        icon = "🔴"
        label = "ULTIMI SECONDI"
    elif remaining <= ANTI_SNIPE_THRESHOLD:
        icon = "🟠"
        label = "ANTI-SNIPE ATTIVO"
    else:
        icon = "🟢"
        label = "ASTA IN CORSO"

    blocks = 12
    filled = max(0, min(blocks, round((remaining / total) * blocks)))
    bar = "█" * filled + "░" * (blocks - filled)
    return f"{icon} **{label}**\n⏱️ **{minutes:02d}:{seconds:02d}**  `{bar}`  **{remaining}s**"


def auction_embed(player, auction, remaining=None):
    highest_bid = auction["highest_bid"] or 0
    bidder_id = auction["highest_bidder_id"]
    leader = f"<@{bidder_id}>" if bidder_id else "Nessuno"

    auction_id = auction["id"]
    recent = auction_last_bids.get(int(auction_id), [])
    recent_text = "\n".join(recent[-5:]) if recent else "Nessuna offerta ancora."
    timer_text = format_auction_timer(AUCTION_SECONDS if remaining is None else remaining)

    embed = discord.Embed(
        title="🔨 ASTA LIVE",
        description=(
            f"## {player['name']}\n"
            f"🏟️ **{player['team']}** • **{player['position']}** • OVR **{player['overall']}**"
        ),
        color=discord.Color.gold()
    )

    embed.add_field(name="💰 Prezzo attuale", value=f"**{highest_bid}** crediti", inline=True)
    embed.add_field(name="👑 Leader", value=leader, inline=True)
    embed.add_field(name="🆔 Asta", value=f"`{auction_id}`", inline=True)
    embed.add_field(name="⏳ Timer", value=timer_text, inline=False)
    embed.add_field(name="📈 Ultime offerte", value=recent_text, inline=False)
    embed.add_field(
        name="🛡️ Anti-snipe",
        value=f"Offerta sotto i **{ANTI_SNIPE_THRESHOLD}s** → +**{ANTI_SNIPE_EXTENSION}s** automatici.",
        inline=False
    )
    embed.add_field(
        name="🎮 Offerte",
        value="Usa i bottoni sotto: **+10**, **+50**, **All In** oppure **Offerta custom**.",
        inline=False
    )
    embed.set_footer(text=f"ID giocatore: {player['id']} • FC26 Auction Bot")
    return embed


async def get_log_channel():
    if not AUCTION_LOG_CHANNEL_ID:
        return None

    try:
        channel = bot.get_channel(int(AUCTION_LOG_CHANNEL_ID))
        if channel:
            return channel
        return await bot.fetch_channel(int(AUCTION_LOG_CHANNEL_ID))
    except Exception:
        return None


async def send_auction_history_log(guild, title, description, *, color=None, embed=None):
    """
    Storico aste/scambi nel canale 1505148650723217540.
    """
    try:
        channel = None
        if guild:
            channel = guild.get_channel(int(AUCTION_LOG_CHANNEL_ID))
        if not channel:
            channel = await bot.fetch_channel(int(AUCTION_LOG_CHANNEL_ID))

        if embed is None:
            embed = discord.Embed(
                title=title,
                description=description,
                color=color or discord.Color.blurple()
            )
        embed.set_footer(text="FC26 • Storico aste e scambi")
        await channel.send(embed=embed)
        return True
    except Exception as e:
        print(f"[AUCTION HISTORY] Errore invio storico: {e}")
        return False


async def send_outbid_dm(user_id, player_name, new_bid, bidder_name):
    """
    DM opzionale al superato: se l'utente ha i DM chiusi, ignora senza errori.
    """
    try:
        user = await bot.fetch_user(int(user_id))
        embed = discord.Embed(
            title="🔔 Offerta superata",
            description=(
                f"Sei stato superato nell'asta di **{player_name}**.\n\n"
                f"Nuova offerta: **{new_bid} crediti**\n"
                f"Nuovo leader: **{bidder_name}**"
            ),
            color=discord.Color.orange()
        )
        await user.send(embed=embed)
        return True
    except Exception:
        return False


async def publish_auction_news(guild, title, description, *, force=False, overall=0, price=0):
    """
    News automatiche per aste importanti.
    Pubblica nel canale media solo eventi interessanti.
    """
    try:
        important = bool(force) or safe_int(overall) >= MEDIA_TRANSFER_MIN_OVERALL or safe_int(price) >= MEDIA_TRANSFER_MIN_PRICE
        if not important:
            return False

        return await publish_media_news(
            guild,
            title,
            description,
            category="transfer",
            player_overall=overall,
            price=price,
            force=force
        )
    except Exception as e:
        print(f"[AUCTION NEWS] Errore news asta: {e}")
        return False



def get_roster_role_count(discord_id, group):
    conn = connect()
    cur = conn.cursor()
    cur.execute("""
        SELECT position
        FROM players
        WHERE owner_discord_id = %s
    """, (str(discord_id),))
    rows = cur.fetchall()
    conn.close()

    return sum(1 for r in rows if role_group(r["position"]) == group)


def can_add_player_to_roster(discord_id, position):
    group = role_group(position)
    current = get_roster_role_count(discord_id, group)
    limit = role_limit(group)
    return current < limit, group, current, limit


def record_bid(auction_id, player_id, bidder_id, bidder_name, amount):
    conn = connect()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO bid_history (auction_id, player_id, bidder_id, bidder_name, amount)
        VALUES (%s, %s, %s, %s, %s)
    """, (auction_id, player_id, bidder_id, bidder_name, amount))
    conn.commit()
    conn.close()


def record_transfer(player_id, player_name, manager_id, manager_name, price, source="auction"):
    conn = connect()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO transfer_history (player_id, player_name, manager_id, manager_name, price, source)
        VALUES (%s, %s, %s, %s, %s, %s)
    """, (str(player_id), player_name, str(manager_id), manager_name, int(price or 0), source))
    conn.commit()
    conn.close()


def is_blacklisted(player_id):
    conn = connect()
    cur = conn.cursor()
    cur.execute("SELECT player_id FROM blacklist_players WHERE player_id = %s", (str(player_id),))
    row = cur.fetchone()
    conn.close()
    return row is not None


def get_league_mode():
    conn = connect()
    cur = conn.cursor()
    cur.execute("SELECT value FROM league_settings WHERE key = 'mode'")
    row = cur.fetchone()
    conn.close()
    return row["value"] if row else "fantacalcio"


def set_league_mode(mode):
    conn = connect()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO league_settings (key, value) VALUES ('mode', %s) ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
    """, (mode,))
    conn.commit()
    conn.close()


def is_market_open():
    conn = connect()
    cur = conn.cursor()
    cur.execute("SELECT value FROM league_settings WHERE key = 'market_open'")
    row = cur.fetchone()
    conn.close()
    return (row["value"] if row else "closed") == "open"


def set_market_open(opened: bool):
    conn = connect()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO league_settings (key, value) VALUES ('market_open', %s) ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
    """, ("open" if opened else "closed",))
    conn.commit()
    conn.close()


def market_status_label():
    return "APERTO ✅" if is_market_open() else "CHIUSO 🔒"


def budget_from_team_overall(avg_ovr):
    avg_ovr = float(avg_ovr or 0)

    # Budget modalità squadre reali
    # Top club: pochi crediti, club più deboli: più crediti.
    if avg_ovr >= 85:
        return 50
    if avg_ovr >= 82:
        return 80
    if avg_ovr >= 80:
        return 150
    if avg_ovr >= 78:
        return 350
    if avg_ovr >= 75:
        return 430

    return 500


def normalize_team_name(team):
    return normalize_text(team).strip()



CLUB_NAME_ALIASES = {
    "inter": ["inter", "inter milan", "internazionale"],
    "milan": ["milan", "ac milan"],
    "juventus": ["juventus", "juve"],
    "roma": ["roma", "as roma"],
    "lazio": ["lazio", "ss lazio"],
    "napoli": ["napoli", "ssc napoli"],
    "bayern monaco": ["bayern monaco", "bayern munich", "fc bayern"],
    "barcellona": ["barcellona", "barcelona", "fc barcelona"],
    "psg": ["psg", "paris saint-germain", "paris sg"],
    "manchester united": ["manchester united", "man united", "man utd"],
    "manchester city": ["manchester city", "man city"],
    "atletico madrid": ["atletico madrid", "atlético madrid"],
    "athletic club": ["athletic club", "athletic bilbao"],
    "rb lipsia": ["rb lipsia", "rb leipzig"],
    "borussia m'gladbach": ["borussia m'gladbach", "borussia monchengladbach", "monchengladbach", "mönchengladbach"],
    "sporting cp": ["sporting cp", "sporting lisbon", "sporting"],
}


def possible_team_names(team_name):
    base = normalize_team_name(team_name)
    aliases = CLUB_NAME_ALIASES.get(base, [base])
    return {normalize_team_name(x) for x in aliases}


def get_team_stats(team_name):
    searches = possible_team_names(team_name)

    conn = connect()
    cur = conn.cursor()
    cur.execute("""
        SELECT *
        FROM players
        WHERE owner_discord_id IS NULL
        ORDER BY overall DESC
    """)
    rows = cur.fetchall()
    conn.close()

    matched = [r for r in rows if normalize_team_name(r["team"]) in searches]

    if not matched:
        return [], 0, 0

    avg_ovr = sum(safe_int(r["overall"]) for r in matched) / len(matched)
    budget = budget_from_team_overall(avg_ovr)

    return matched, avg_ovr, budget



# ================= FIX MODALITÀ REALE: ROSA + BUDGET =================

TEAM_ALIASES = {
    "inter": ["inter", "internazionale", "inter milan", "fc internazionale"],
    "arsenal": ["arsenal", "arsenal fc"],
    "milan": ["milan", "ac milan"],
    "juventus": ["juventus", "juve", "juventus fc"],
    "manchester city": ["manchester city", "man city", "manchester city fc"],
    "manchester united": ["manchester united", "man united", "man utd"],
    "real madrid": ["real madrid", "real madrid cf"],
    "barcellona": ["barcellona", "barcelona", "fc barcelona"],
    "psg": ["psg", "paris saint germain", "paris saint-germain"],
    "bayern monaco": ["bayern monaco", "bayern munich", "fc bayern munich"],
}


def get_team_aliases(team_name):
    base = normalize_team_name(team_name)
    aliases = {base}
    for key, values in TEAM_ALIASES.items():
        if base == normalize_team_name(key) or base in [normalize_team_name(v) for v in values]:
            aliases.update(normalize_team_name(v) for v in values)
    return aliases


def get_team_stats_reale(team_name, include_owned_by=None):
    """
    Trova i giocatori reali del club usando la colonna corretta players.team.
    Considera liberi i giocatori con owner_discord_id NULL o vuoto.
    Se include_owned_by è valorizzato, include anche i giocatori già assegnati a quel manager.
    """
    aliases = get_team_aliases(team_name)

    conn = connect()
    cur = conn.cursor()

    cur.execute("""
        SELECT *
        FROM players
        ORDER BY overall DESC NULLS LAST
    """)
    rows = cur.fetchall()
    conn.close()

    matched = []
    for r in rows:
        team_value = r.get("team") if hasattr(r, "get") else r["team"]
        team_norm = normalize_team_name(team_value)

        owner = r.get("owner_discord_id") if hasattr(r, "get") else r["owner_discord_id"]
        owner_empty = owner is None or str(owner).strip() == ""

        if team_norm in aliases:
            if owner_empty or (include_owned_by and str(owner) == str(include_owned_by)):
                matched.append(r)

    if not matched:
        return [], 0, 0

    avg_ovr = sum(safe_int(r.get("overall") if hasattr(r, "get") else r["overall"]) for r in matched) / len(matched)
    budget = budget_from_team_overall(avg_ovr)

    return matched, avg_ovr, budget


def get_exact_team_names_like(team_name):
    aliases = get_team_aliases(team_name)
    conn = connect()
    cur = conn.cursor()
    cur.execute("SELECT DISTINCT team FROM players ORDER BY team ASC")
    rows = cur.fetchall()
    conn.close()

    results = []
    search = normalize_team_name(team_name)
    for r in rows:
        team = r["team"]
        norm = normalize_team_name(team)
        if norm in aliases or search in norm or norm in search:
            results.append(team)

    return results[:20]

# =====================================================================


# ================= SYNC ROSA REALE PLAYER =================

def sync_real_team_roster_to_manager(discord_id, club_name):
    """Assegna automaticamente al manager tutti i giocatori reali del club scelto.

    Fix definitivo Supabase/PostgreSQL:
    - players.id è BIGINT;
    - i valori arrivano spesso come stringa/text dai menu Discord;
    - ogni confronto su players.id usa CAST(%s AS BIGINT), evitando errori text = integer.

    Ritorna: players_count, avg_ovr, budget, real_team_name.
    """
    discord_id = str(discord_id)
    club_name = str(club_name).strip()

    players, avg_ovr, budget = get_team_stats_reale(club_name, include_owned_by=discord_id)

    if not players:
        print(f"[REAL TEAM SYNC] Nessun giocatore trovato per club={club_name}")
        return 0, 0, 0, None

    first_team = players[0].get("team") if hasattr(players[0], "get") else players[0]["team"]
    real_team_name = first_team or club_name

    conn = connect()
    cur = conn.cursor()

    try:
        # Libera eventuale rosa precedente del manager.
        cur.execute("""
            UPDATE players
            SET owner_discord_id = NULL,
                sold_price = NULL
            WHERE owner_discord_id = %s
        """, (discord_id,))

        # Assegna tutti i giocatori trovati.
        # IMPORTANTE: players.id è BIGINT su Supabase, quindi il parametro viene castato a BIGINT.
        assigned_count = 0
        for p in players:
            raw_pid = p.get("id") if hasattr(p, "get") else p["id"]
            pid = str(raw_pid).strip()
            if not pid:
                continue

            cur.execute("""
                UPDATE players
                SET owner_discord_id = %s,
                    sold_price = 0
                WHERE id = CAST(%s AS BIGINT)
            """, (discord_id, pid))
            assigned_count += cur.rowcount if cur.rowcount is not None else 1

        # Crea/aggiorna manager.
        cur.execute("""
            INSERT INTO managers (discord_id, name, manager_name, club_name, budget)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (discord_id) DO UPDATE SET
                name = EXCLUDED.name,
                manager_name = EXCLUDED.manager_name,
                club_name = EXCLUDED.club_name,
                budget = EXCLUDED.budget
        """, (discord_id, discord_id, discord_id, club_name, int(budget)))

        # Libera eventuale club precedente assegnato allo stesso manager.
        cur.execute("""
            UPDATE fc26_clubs
            SET assigned_to = NULL,
                assigned_at = NULL
            WHERE assigned_to = %s
        """, (discord_id,))

        # Assegna club scelto.
        cur.execute("""
            UPDATE fc26_clubs
            SET assigned_to = %s,
                assigned_at = CURRENT_TIMESTAMP
            WHERE LOWER(name) = LOWER(%s)
        """, (discord_id, club_name))

        # Salva assegnazione reale.
        cur.execute("""
            INSERT INTO real_team_assignments
            (discord_id, manager_name, team_name, avg_overall, assigned_budget)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (discord_id) DO UPDATE SET
                manager_name = EXCLUDED.manager_name,
                team_name = EXCLUDED.team_name,
                avg_overall = EXCLUDED.avg_overall,
                assigned_budget = EXCLUDED.assigned_budget
        """, (discord_id, discord_id, real_team_name, float(avg_ovr), int(budget)))

        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    print(
        f"[REAL TEAM SYNC FIX] Manager={discord_id} Club={club_name} "
        f"RealTeam={real_team_name} Giocatori={assigned_count} "
        f"OVR={avg_ovr:.2f} Budget={budget}"
    )

    return assigned_count, avg_ovr, int(budget), real_team_name

# ===========================================================



async def safe_dm(user_id, message=None, embed=None):
    try:
        user = await bot.fetch_user(int(user_id))
        await user.send(content=message, embed=embed)
        return True
    except Exception:
        return False



async def safe_dm_signup_result(user_id, title, description, color=None):
    try:
        user = await bot.fetch_user(int(user_id))
        embed = discord.Embed(
            title=title,
            description=description,
            color=color or discord.Color.blue()
        )
        embed.set_footer(text="FC26 Iscrizioni")
        await user.send(embed=embed)
        return True
    except Exception:
        return False


async def get_member_safe(guild, member_id):
    """Recupera un membro anche se non è nella cache Discord."""
    if not guild or not member_id:
        return None

    member_id = int(member_id)
    member = guild.get_member(member_id)
    if member is not None:
        return member

    try:
        return await guild.fetch_member(member_id)
    except Exception:
        return None



# ================= BORDO CAMPO - GESTIONE RUOLI ISCRIZIONE =================
BASE_ROLE_ID = REQUEST_ROLE_ID                 # 1495072035624325130
PRE_SIGNUP_ROLE_ID = SIGNUP_PENDING_ROLE_ID    # 1505180973208440954
REGISTERED_ROLE_ID = SIGNUP_REGISTERED_ROLE_ID # 1505181066695016619


async def apply_signup_role_pending(guild, member, reason="Richiesta iscrizione FC26"):
    """Quando un player fa richiesta: aggiunge PRE ISCRITTO e lascia il ruolo base."""
    if not guild or not member:
        return

    try:
        pre_role = guild.get_role(int(PRE_SIGNUP_ROLE_ID))

        if pre_role and pre_role not in getattr(member, "roles", []):
            await member.add_roles(pre_role, reason=reason)
    except Exception as e:
        print(f"[SIGNUP ROLES] Errore pending: {e}")


async def apply_signup_role_accepted(guild, member, reason="Iscrizione FC26 accettata"):
    """Quando viene accettato: aggiunge ISCRITTO FC26 e rimuove BASE + PRE ISCRITTO."""
    if not guild or not member:
        return

    try:
        base_role = guild.get_role(int(BASE_ROLE_ID))
        pre_role = guild.get_role(int(PRE_SIGNUP_ROLE_ID))
        registered_role = guild.get_role(int(REGISTERED_ROLE_ID))

        roles_to_remove = [
            role for role in (base_role, pre_role)
            if role and role in getattr(member, "roles", [])
        ]

        if roles_to_remove:
            await member.remove_roles(*roles_to_remove, reason=reason)

        if registered_role and registered_role not in getattr(member, "roles", []):
            await member.add_roles(registered_role, reason=reason)
    except Exception as e:
        print(f"[SIGNUP ROLES] Errore accepted: {e}")


async def apply_signup_role_rejected(guild, member, reason="Iscrizione FC26 rifiutata"):
    """Quando viene rifiutato: rimuove PRE ISCRITTO e lascia il ruolo base."""
    if not guild or not member:
        return

    try:
        pre_role = guild.get_role(int(PRE_SIGNUP_ROLE_ID))

        if pre_role and pre_role in getattr(member, "roles", []):
            await member.remove_roles(pre_role, reason=reason)
    except Exception as e:
        print(f"[SIGNUP ROLES] Errore rejected: {e}")

# ========================================================================

def _font(size=24, bold=False):
    candidates = [
        "arialbd.ttf" if bold else "arial.ttf",
        "C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf",
        "C:/Windows/Fonts/segoeuib.ttf" if bold else "C:/Windows/Fonts/segoeui.ttf",
    ]
    for f in candidates:
        try:
            return ImageFont.truetype(f, size)
        except Exception:
            pass
    return ImageFont.load_default()


def generate_roster_graphic(discord_id, display_name):
    conn = connect()
    cur = conn.cursor()
    cur.execute("""
        SELECT name, team, position, overall, sold_price
        FROM players
        WHERE owner_discord_id = %s
        ORDER BY overall DESC
    """, (str(discord_id),))
    rows = cur.fetchall()
    conn.close()

    width, height = 1100, 1500
    img = Image.new("RGB", (width, height), (18, 95, 58))
    draw = ImageDraw.Draw(img)

    # pitch
    draw.rounded_rectangle((45, 45, width-45, height-45), radius=30, outline=(235, 235, 235), width=5)
    draw.line((45, height//2, width-45, height//2), fill=(235,235,235), width=4)
    draw.ellipse((width//2-120, height//2-120, width//2+120, height//2+120), outline=(235,235,235), width=4)
    draw.text((width//2, 105), f"ROSA {display_name}".upper(), font=_font(48, True), fill=(255,255,255), anchor="mm")

    groups = {"GK": [], "DEF": [], "MID": [], "ATT": [], "OTHER": []}
    for r in rows:
        groups.setdefault(role_group(r["position"]), []).append(r)

    slots = {
        "GK": [(550, 1280), (350, 1280)],
        "DEF": [(220, 1010), (440, 1040), (660, 1040), (880, 1010), (330, 900), (770, 900)],
        "MID": [(220, 700), (440, 740), (660, 740), (880, 700), (330, 610), (770, 610)],
        "ATT": [(300, 360), (550, 310), (800, 360), (550, 450)],
        "OTHER": [(150, 1350), (950, 1350)]
    }

    def draw_card(x, y, p):
        draw.rounded_rectangle((x-85, y-58, x+85, y+58), radius=18, fill=(31, 31, 36), outline=(255, 220, 130), width=3)
        draw.text((x-68, y-35), str(p["overall"]), font=_font(30, True), fill=(255, 224, 130))
        draw.text((x+55, y-35), str(p["position"]), font=_font(20, True), fill=(255, 255, 255), anchor="mm")
        name = str(p["name"])
        if len(name) > 16:
            name = name[:15] + "…"
        draw.text((x, y+5), name, font=_font(22, True), fill=(255,255,255), anchor="mm")
        draw.text((x, y+34), f"{p['sold_price'] or 0} cr", font=_font(18), fill=(220,220,220), anchor="mm")

    for group, players in groups.items():
        for idx, p in enumerate(players[:len(slots.get(group, []))]):
            x, y = slots[group][idx]
            draw_card(x, y, p)

    total_spent = sum(safe_int(r["sold_price"]) for r in rows)
    avg_ovr = (sum(safe_int(r["overall"]) for r in rows) / len(rows)) if rows else 0
    draw.rounded_rectangle((120, 1400, 980, 1460), radius=20, fill=(25,25,30))
    draw.text((550, 1430), f"Giocatori: {len(rows)}  •  OVR medio: {avg_ovr:.1f}  •  Speso: {total_spent} cr", font=_font(28, True), fill=(255,255,255), anchor="mm")

    out = GRAPHICS_DIR / f"rosa_{discord_id}.png"
    img.save(out, quality=95)
    return out


async def get_open_auction_for_message(message_id=None):
    conn = connect()
    cur = conn.cursor()

    if message_id:
        cur.execute("""
            SELECT a.*, p.name AS player_name, p.id AS player_id, p.position AS player_position,
                   p.team AS player_team, p.overall AS player_overall
            FROM auctions a
            JOIN players p ON p.id::text = a.player_id::text
            WHERE a.status = 'open'
              AND a.message_id = %s
            LIMIT 1
        """, (str(message_id),))
        row = cur.fetchone()
        conn.close()
        if row:
            return row

    conn = connect()
    cur = conn.cursor()
    cur.execute("""
        SELECT a.*, p.name AS player_name, p.id AS player_id, p.position AS player_position,
               p.team AS player_team, p.overall AS player_overall
        FROM auctions a
        JOIN players p ON p.id::text = a.player_id::text
        WHERE a.status = 'open'
        ORDER BY a.id DESC
        LIMIT 1
    """)
    row = cur.fetchone()
    conn.close()
    return row


async def place_bid(interaction: discord.Interaction, increment=None, all_in=False):
    if not is_market_open():
        await interaction.response.send_message("🔒 Il mercato è chiuso. Non puoi fare offerte in questo momento.", ephemeral=True)
        return

    message_id = str(interaction.message.id) if interaction.message else None
    auction = await get_open_auction_for_message(message_id)

    if not auction:
        await safe_send(interaction, "Non c'è nessuna asta aperta su questo messaggio.", ephemeral=True)
        return

    conn = connect()
    cur = conn.cursor()

    cur.execute("SELECT * FROM managers WHERE discord_id = %s", (str(interaction.user.id),))
    manager = cur.fetchone()

    if not manager:
        conn.close()
        await interaction.response.send_message("Prima devi essere registrato/iscritto per partecipare alle aste.", ephemeral=True)
        return

    if str(auction.get("highest_bidder_id") or "") == str(interaction.user.id) and not all_in:
        conn.close()
        await safe_send(interaction, "Sei già il miglior offerente.", ephemeral=True)
        return

    ok, group, current, limit = can_add_player_to_roster(interaction.user.id, auction["player_position"])
    if not ok:
        conn.close()
        await interaction.response.send_message(
            f"Non puoi offrire: hai già raggiunto il limite per {role_label(group)} ({current}/{limit}).",
            ephemeral=True
        )
        return

    previous_bidder_id = auction["highest_bidder_id"]
    current_bid = safe_int(auction["highest_bid"])
    manager_budget = safe_int(manager["budget"])

    if all_in:
        new_bid = manager_budget
    else:
        new_bid = current_bid + safe_int(increment)

    if new_bid <= current_bid:
        conn.close()
        await interaction.response.send_message("L'offerta deve superare quella attuale.", ephemeral=True)
        return

    if new_bid < current_bid + MIN_RAISE:
        conn.close()
        await interaction.response.send_message(f"Devi rilanciare almeno di {MIN_RAISE} crediti.", ephemeral=True)
        return

    if manager_budget < new_bid:
        conn.close()
        await interaction.response.send_message("Budget insufficiente.", ephemeral=True)
        return

    cur.execute("""
        UPDATE auctions
        SET highest_bid = %s,
            highest_bidder_id = %s
        WHERE id = %s
          AND status = 'open'
    """, (new_bid, str(interaction.user.id), auction["id"]))
    conn.commit()

    cur.execute("""
        SELECT a.*, p.*
        FROM auctions a
        JOIN players p ON p.id::text = a.player_id::text
        WHERE a.id = %s
    """, (auction["id"],))
    updated = cur.fetchone()
    conn.close()

    auction_id = int(auction["id"])
    player_id = str(auction["player_id"])
    bidder_name = interaction.user.display_name

    record_bid(auction_id, player_id, str(interaction.user.id), bidder_name, new_bid)

    try:
        await send_auction_history_log(
            interaction.guild,
            "📈 Nuova offerta asta",
            (
                f"Giocatore: **{auction['player_name']}** (`{player_id}`)\n"
                f"Offerente: {interaction.user.mention}\n"
                f"Offerta: **{new_bid} crediti**"
            ),
            color=discord.Color.gold()
        )
    except Exception:
        pass

    if previous_bidder_id and str(previous_bidder_id) != str(interaction.user.id):
        await send_outbid_dm(
            previous_bidder_id,
            auction["player_name"],
            new_bid,
            bidder_name
        )

    auction_last_bids.setdefault(auction_id, [])
    label = "ALL IN" if all_in else f"+{increment}"
    auction_last_bids[auction_id].append(f"• **{bidder_name}** {label} → **{new_bid}** cr")

    if auction_id in auction_timers and auction_timers[auction_id] <= ANTI_SNIPE_THRESHOLD:
        auction_timers[auction_id] += ANTI_SNIPE_EXTENSION
        auction_last_bids[auction_id].append(f"⏱️ Anti-snipe: +{ANTI_SNIPE_EXTENSION}s")

    embed = auction_embed(updated, updated, auction_timers.get(auction_id))

    try:
        await interaction.message.edit(embed=embed, view=AuctionView())
    except Exception as e:
        print(f"[ASTA] Errore update messaggio offerta: {e}")

    await interaction.response.send_message(
        f"🔥 Offerta registrata: **{new_bid}** crediti per **{auction['player_name']}**.",
        ephemeral=True
    )


class CustomBidModal(discord.ui.Modal, title="Offerta personalizzata"):
    amount = discord.ui.TextInput(
        label="Quanto vuoi rilanciare?",
        placeholder="Esempio: 20, 30, 40",
        required=True,
        max_length=5
    )

    async def on_submit(self, interaction: discord.Interaction):
        raw = str(self.amount.value).strip()

        if not raw.isdigit():
            await interaction.response.send_message("Inserisci solo numeri interi.", ephemeral=True)
            return

        increment = int(raw)

        if increment <= 0:
            await interaction.response.send_message("Il rilancio deve essere maggiore di 0.", ephemeral=True)
            return

        if increment % 10 != 0:
            await interaction.response.send_message("Il rilancio personalizzato deve essere multiplo di 10.", ephemeral=True)
            return

        await place_bid(interaction, increment=increment)


class AuctionView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="+10", style=discord.ButtonStyle.primary, custom_id="auction_plus_10")
    async def plus_10(self, interaction: discord.Interaction, button: discord.ui.Button):
        await place_bid(interaction, increment=10)

    @discord.ui.button(label="+50", style=discord.ButtonStyle.primary, custom_id="auction_plus_50")
    async def plus_50(self, interaction: discord.Interaction, button: discord.ui.Button):
        await place_bid(interaction, increment=50)

    @discord.ui.button(label="All In", style=discord.ButtonStyle.danger, custom_id="auction_all_in")
    async def all_in(self, interaction: discord.Interaction, button: discord.ui.Button):
        await place_bid(interaction, all_in=True)

    @discord.ui.button(label="Offerta custom", style=discord.ButtonStyle.secondary, custom_id="auction_custom")
    async def custom_bid(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(CustomBidModal())




# ================= PERMESSI STAFF / LOG / CONFERME =================

OWNER_STAFF_ROLE_ID = "1398342848436240434"      # FOUNDER: comandi sensibili
LIMITED_STAFF_ROLE_ID = "1398358193197027408"    # STAFF: no comandi sensibili
STAFF_LOG_CHANNEL_ID = 1506321007873495070

DANGEROUS_ACTIONS = {
    "reset",
    "backup",
    "restore",
    "fine_stagione",
    "inizio_stagione",
    "nuova_stagione",
    "reset_modalita",
    "reset_competizione"
}


def has_role_id(member, role_id: str):
    return any(str(role.id) == str(role_id) for role in getattr(member, "roles", []))


def is_owner_staff_member(member):
    return has_role_id(member, OWNER_STAFF_ROLE_ID) or getattr(member.guild_permissions, "administrator", False)


def is_limited_staff_member(member):
    return has_role_id(member, LIMITED_STAFF_ROLE_ID)


def can_use_dangerous_commands(member):
    # Solo ruolo 1498341567105339492 o Administrator Discord
    return is_owner_staff_member(member)


def can_use_staff_panel(member):
    # Staff panel SOLO ruolo 1498341567105339492 o Administrator Discord
    return is_owner_staff_member(member)


def can_use_normal_staff(member):
    # Entrambi i ruoli possono fare operazioni normali
    return is_owner_staff_member(member) or is_limited_staff_member(member)


def can_manage_signup(member):
    """Permessi gestione iscrizioni."""
    try:
        if can_use_normal_staff(member):
            return True
    except Exception:
        pass

    try:
        if getattr(member.guild_permissions, "administrator", False):
            return True
    except Exception:
        pass

    try:
        return any(str(role.id) in SIGNUP_STAFF_ROLE_IDS for role in getattr(member, "roles", []))
    except Exception:
        return False


async def send_staff_log(guild, title, description, *, user=None, color=None):
    try:
        channel = None
        if guild:
            channel = guild.get_channel(int(STAFF_LOG_CHANNEL_ID))
        if not channel:
            channel = await bot.fetch_channel(int(STAFF_LOG_CHANNEL_ID))

        embed = discord.Embed(
            title=title,
            description=description,
            color=color or discord.Color.blue()
        )
        if user:
            embed.add_field(name="Eseguito da", value=f"{user.mention} (`{user.id}`)", inline=False)
        embed.set_footer(text="FC26 Staff Log")
        await channel.send(embed=embed)
    except Exception as e:
        print(f"[STAFF LOG] Errore invio log: {e}")


class ConfirmDangerView(discord.ui.View):
    def __init__(self, action_name, callback_func, *, timeout=180):
        super().__init__(timeout=timeout)
        self.action_name = action_name
        self.callback_func = callback_func

    @discord.ui.button(label="Conferma", style=discord.ButtonStyle.danger, emoji="⚠️")
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not can_use_dangerous_commands(interaction.user):
            await interaction.response.send_message("❌ Non hai i permessi per confermare questa azione.", ephemeral=True)
            return

        await self.callback_func(interaction)

    @discord.ui.button(label="Annulla", style=discord.ButtonStyle.secondary, emoji="❌")
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = discord.Embed(
            title="❌ Azione annullata",
            description=f"L'azione **{self.action_name}** è stata annullata.",
            color=discord.Color.dark_grey()
        )
        await interaction.response.edit_message(embed=embed, view=None)
        await send_staff_log(
            interaction.guild,
            "♻️ Backup ripristinato dallo staff",
            f"Backup ripristinato: `{selected.name}`\nDatabase: `{db_path}`",
            user=interaction.user,
            color=discord.Color.orange()
        )
        await send_staff_log(
            interaction.guild,
            "❌ Azione annullata",
            f"Azione annullata: **{self.action_name}**",
            user=interaction.user,
            color=discord.Color.dark_grey()
        )


async def ask_danger_confirmation(interaction, action_name, description, callback_func):
    if not can_use_dangerous_commands(interaction.user):
        await interaction.response.send_message(
            "❌ Non hai i permessi per usare questa azione critica.",
            ephemeral=True
        )
        return

    embed = discord.Embed(
        title=f"⚠️ Conferma richiesta: {action_name}",
        description=description + "\n\nPremi **Conferma** per procedere oppure **Annulla**.",
        color=discord.Color.orange()
    )

    await interaction.response.send_message(
        embed=embed,
        view=ConfirmDangerView(action_name, callback_func),
        ephemeral=True
    )

# ===========================================================

# ================= SISTEMA BACKUP DATABASE =================

BACKUP_DIR = Path("backups")
BACKUP_DIR.mkdir(exist_ok=True)
MAX_BACKUPS_TO_KEEP = 5
BACKUP_NOTIFICATION_CHANNEL_ID = 1506321007873495070

# Se il tuo db.py usa un nome diverso, modifica qui.
DATABASE_CANDIDATES = [
    Path("data/fc26.db"),
    Path("data/database.db"),
    Path("data/bot.db"),
    Path("fc26.db"),
    Path("database.db"),
    Path("bot.db"),
]


def get_database_path():
    for candidate in DATABASE_CANDIDATES:
        if candidate.exists():
            return candidate

    # fallback: cerca il primo .db nelle cartelle più probabili
    for base in [Path("data"), Path(".")]:
        if base.exists():
            db_files = sorted(base.glob("*.db"))
            if db_files:
                return db_files[0]

    return None


def cleanup_old_backups():
    backups = sorted(BACKUP_DIR.glob("backup_*.db"), key=lambda p: p.stat().st_mtime, reverse=True)
    for old_backup in backups[MAX_BACKUPS_TO_KEEP:]:
        try:
            old_backup.unlink()
        except Exception:
            pass


def create_database_backup(reason="manuale"):
    db_path = get_database_path()
    if not db_path:
        return None, "Backup locale SQLite non disponibile: il bot ora usa PostgreSQL/Supabase."

    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    safe_reason = normalize_text(reason).replace(" ", "_")[:40] or "manuale"
    backup_path = BACKUP_DIR / f"backup_{timestamp}_{safe_reason}.db"

    shutil.copy2(db_path, backup_path)
    cleanup_old_backups()
    return backup_path, None


def list_database_backups():
    return sorted(BACKUP_DIR.glob("backup_*.db"), key=lambda p: p.stat().st_mtime, reverse=True)


async def auto_backup(reason="automatico"):
    path, error = create_database_backup(reason)
    return path is not None


async def create_backup_before_sensitive_action(reason):
    # Wrapper usato prima di reset, mercato, gironi, coppe, ecc.
    try:
        return await auto_backup(reason)
    except Exception:
        return False


@tree.command(name="backup_now", description="Owner staff: crea subito un backup del database")
async def backup_now(interaction: discord.Interaction):
    async def do_backup(confirm_interaction: discord.Interaction):
        path, error = create_database_backup("manuale")

        if error:
            await confirm_interaction.response.edit_message(
                embed=discord.Embed(
                    title="❌ Backup non creato",
                    description=str(error),
                    color=discord.Color.red()
                ),
                view=None
            )
            return

        embed = discord.Embed(
            title="✅ Backup creato correttamente",
            description=f"`{path}`",
            color=discord.Color.green()
        )
        await confirm_interaction.response.edit_message(embed=embed, view=None)

        await send_backup_notification(
            f"🛠️ Backup manuale creato da {confirm_interaction.user.mention}.\n```{path}```"
        )
        await send_staff_log(
            confirm_interaction.guild,
            "💾 Backup manuale creato",
            f"Percorso backup:\n`{path}`",
            user=confirm_interaction.user,
            color=discord.Color.green()
        )

    await ask_danger_confirmation(
        interaction,
        "Backup manuale",
        "Stai per creare un backup manuale del database.",
        do_backup
    )


@tree.command(name="backup_list", description="Staff: mostra gli ultimi backup disponibili")
async def backup_list(interaction: discord.Interaction):
    if not can_use_dangerous_commands(interaction.user):
        await interaction.response.send_message("❌ Solo lo staff autorizzato può vedere i backup.", ephemeral=True)
        return

    backups = list_database_backups()

    if not backups:
        await interaction.response.send_message("Nessun backup disponibile.", ephemeral=True)
        return

    lines = []
    for idx, backup in enumerate(backups[:10], start=1):
        size_mb = backup.stat().st_size / (1024 * 1024)
        lines.append(f"**{idx}.** `{backup.name}` — {size_mb:.2f} MB")

    await interaction.response.send_message(
        "📦 **Backup disponibili**\n\n" + "\n".join(lines),
        ephemeral=True
    )


class RestoreBackupSelect(discord.ui.Select):
    def __init__(self, backups):
        options = []
        for backup in backups[:25]:
            size_mb = backup.stat().st_size / (1024 * 1024)
            options.append(discord.SelectOption(
                label=backup.name[:100],
                value=backup.name,
                description=f"{size_mb:.2f} MB"
            ))

        super().__init__(
            placeholder="Scegli il backup da ripristinare...",
            min_values=1,
            max_values=1,
            options=options
        )

    async def callback(self, interaction: discord.Interaction):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ Solo lo staff può ripristinare backup.", ephemeral=True)
            return

        selected = BACKUP_DIR / self.values[0]
        db_path = get_database_path()

        if not selected.exists():
            await interaction.response.send_message("❌ Backup non trovato.", ephemeral=True)
            return

        if not db_path:
            await interaction.response.send_message("❌ Database attuale non trovato.", ephemeral=True)
            return

        # Backup di sicurezza prima del restore
        create_database_backup("prima_del_restore")

        try:
            shutil.copy2(selected, db_path)
        except Exception as e:
            await interaction.response.send_message(f"❌ Errore durante il ripristino: `{e}`", ephemeral=True)
            return

        embed = discord.Embed(
            title="✅ Backup ripristinato",
            description=(
                f"Backup ripristinato:\n`{selected.name}`\n\n"
                "⚠️ Riavvia il bot per assicurarti che tutte le connessioni leggano il database ripristinato."
            ),
            color=discord.Color.green()
        )
        await interaction.response.edit_message(embed=embed, view=None)


class RestoreBackupView(discord.ui.View):
    def __init__(self, backups):
        super().__init__(timeout=180)
        self.add_item(RestoreBackupSelect(backups))


@tree.command(name="restore_backup", description="Staff: ripristina un backup del database")
async def restore_backup(interaction: discord.Interaction):
    if not can_use_dangerous_commands(interaction.user):
        await interaction.response.send_message("❌ Solo lo staff autorizzato può ripristinare backup.", ephemeral=True)
        return

    backups = list_database_backups()

    if not backups:
        await interaction.response.send_message("Nessun backup disponibile da ripristinare.", ephemeral=True)
        return

    embed = discord.Embed(
        title="⚠️ Ripristino backup",
        description=(
            "Scegli il backup da ripristinare.\n\n"
            "Il bot creerà automaticamente un backup di sicurezza prima del ripristino.\n"
            "Dopo il restore è consigliato riavviare il bot."
        ),
        color=discord.Color.orange()
    )

    await interaction.response.send_message(embed=embed, view=RestoreBackupView(backups), ephemeral=True)

# ===========================================================



# ================= NOMI CUSTOM GIRONI RANDOM =================

def parse_custom_league_names(raw_names: str, expected_count: int = None):
    names = []
    for part in str(raw_names or "").replace(";", "\n").split("\n"):
        clean = part.strip()
        if clean:
            names.append(clean[:80])

    if expected_count:
        names = names[:expected_count]

    return names


def build_default_group_names(count: int):
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    names = []
    for i in range(max(1, int(count))):
        if i < len(alphabet):
            names.append(f"Girone {alphabet[i]}")
        else:
            names.append(f"Girone {i + 1}")
    return names


class RandomLeagueNamesModal(discord.ui.Modal, title="Nomi campionati random"):
    group_count = discord.ui.TextInput(
        label="Numero campionati/gironi",
        placeholder="Esempio: 2",
        required=True,
        max_length=2
    )

    league_names = discord.ui.TextInput(
        label="Nomi campionati",
        placeholder="Uno per riga. Esempio: Super League, Elite Division",
        required=True,
        style=discord.TextStyle.paragraph,
        max_length=500
    )

    async def on_submit(self, interaction: discord.Interaction):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ Solo lo staff può configurare i gironi.", ephemeral=True)
            return

        raw_count = str(self.group_count.value).strip()

        if not raw_count.isdigit() or int(raw_count) <= 0:
            await interaction.response.send_message("❌ Inserisci un numero valido di campionati/gironi.", ephemeral=True)
            return

        count = min(int(raw_count), 25)
        names = parse_custom_league_names(str(self.league_names.value), count)

        if len(names) < count:
            defaults = build_default_group_names(count)
            for default_name in defaults:
                if len(names) >= count:
                    break
                if default_name not in names:
                    names.append(default_name)

        conn = connect()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO league_settings (key, value) VALUES ('random_group_names', %s) ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
        """, ("|".join(names),))
        cur.execute("""
            INSERT INTO league_settings (key, value) VALUES ('random_group_count', %s) ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
        """, (str(count),))
        cur.execute("""
            INSERT INTO league_settings (key, value) VALUES ('group_generation_mode', 'random_custom') ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
        """)
        conn.commit()
        conn.close()

        await create_backup_before_sensitive_action("nomi_gironi_random")

        embed = discord.Embed(
            title="✅ Nomi campionati random salvati",
            description=(
                "Il bot userà questi nomi per la generazione random dei campionati/gironi:\n\n"
                + "\n".join(f"• **{name}**" for name in names)
            ),
            color=discord.Color.green()
        )
        embed.set_footer(text="Se usi i campionati reali, il bot userà invece i nomi reali delle competizioni.")

        await interaction.response.send_message(embed=embed, ephemeral=True)


class GroupNamingModeView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=180)

    @discord.ui.button(label="Random con nomi custom", style=discord.ButtonStyle.primary, emoji="🎲")
    async def random_custom(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ Solo lo staff può configurare i gironi.", ephemeral=True)
            return
        await interaction.response.send_modal(RandomLeagueNamesModal())

    @discord.ui.button(label="Campionati reali automatici", style=discord.ButtonStyle.success, emoji="🏆")
    async def real_leagues(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ Solo lo staff può configurare i gironi.", ephemeral=True)
            return

        conn = connect()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO league_settings (key, value) VALUES ('group_generation_mode', 'real_leagues') ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
        """)
        conn.commit()
        conn.close()

        embed = discord.Embed(
            title="✅ Campionati reali attivati",
            description=(
                "Quando generi i gironi, il bot userà i nomi reali delle competizioni:\n"
                "Serie A, Premier League, LaLiga, Bundesliga, Ligue 1, ecc.\n\n"
                "I nomi vengono letti dal campo `league` dei club assegnati."
            ),
            color=discord.Color.green()
        )
        await interaction.response.edit_message(embed=embed, view=None)


@tree.command(name="configura_gironi", description="Staff: configura nomi gironi random o campionati reali")
async def configura_gironi(interaction: discord.Interaction):
    if not is_admin(interaction):
        await interaction.response.send_message("❌ Solo lo staff può configurare i gironi.", ephemeral=True)
        return

    embed = discord.Embed(
        title="⚙️ Configurazione gironi/campionati",
        description=(
            "Scegli come nominare i campionati quando generi i gironi:\n\n"
            "🎲 **Random con nomi custom**\n"
            "Lo staff inserisce nomi personalizzati, esempio Super League, Elite Division.\n\n"
            "🏆 **Campionati reali automatici**\n"
            "Il bot usa automaticamente i nomi reali, esempio Serie A, Premier League, LaLiga."
        ),
        color=discord.Color.blue()
    )

    await interaction.response.send_message(embed=embed, view=GroupNamingModeView(), ephemeral=True)


def get_random_group_names_for_generation(default_count=1):
    conn = connect()
    cur = conn.cursor()
    cur.execute("SELECT value FROM league_settings WHERE key = 'random_group_names'")
    names_row = cur.fetchone()
    cur.execute("SELECT value FROM league_settings WHERE key = 'random_group_count'")
    count_row = cur.fetchone()
    conn.close()

    count = safe_int(count_row["value"], default_count) if count_row else default_count

    if names_row and names_row["value"]:
        names = [n.strip() for n in str(names_row["value"]).split("|") if n.strip()]
        if names:
            return names[:count]

    return build_default_group_names(count)


def get_group_generation_mode():
    conn = connect()
    cur = conn.cursor()
    cur.execute("SELECT value FROM league_settings WHERE key = 'group_generation_mode'")
    row = cur.fetchone()
    conn.close()
    return row["value"] if row else "random_custom"


def get_real_league_names_from_assigned_clubs():
    conn = connect()
    cur = conn.cursor()
    try:
        cur.execute("""
            SELECT DISTINCT c.league
            FROM fc26_clubs c
            WHERE c.assigned_to IS NOT NULL
              AND c.league IS NOT NULL
              AND c.league != ''
            ORDER BY c.league ASC
        """)
        rows = cur.fetchall()
        names = [r["league"] for r in rows]
    except Exception:
        names = []
    conn.close()

    return names or get_current_league_names()


@tree.command(name="nomi_gironi_random", description="Staff: imposta direttamente i nomi dei gironi random")
async def nomi_gironi_random(interaction: discord.Interaction):
    if not is_admin(interaction):
        await interaction.response.send_message("❌ Solo lo staff può configurare i gironi.", ephemeral=True)
        return

    await interaction.response.send_modal(RandomLeagueNamesModal())

# ======================================================================



# ================= SISTEMA ATTIVITÀ PLAYER CORRETTO =================

INACTIVITY_CHANNEL_ID = 1505325803683184743
INACTIVITY_HOURS_LIMIT = 22
INACTIVITY_CHECK_INTERVAL = 79200  # 22 ore


def ensure_activity_tables():
    conn = connect()
    cur = conn.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS player_activity (
        discord_id TEXT PRIMARY KEY,
        last_discord_activity TIMESTAMP,
        last_match_played TIMESTAMP,
        last_response TIMESTAMP,
        warnings INTEGER DEFAULT 0,
        status TEXT DEFAULT 'active',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)

    for sql in [
        "ALTER TABLE player_activity ADD COLUMN IF NOT EXISTS last_discord_activity TIMESTAMP",
        "ALTER TABLE player_activity ADD COLUMN IF NOT EXISTS last_match_played TIMESTAMP",
        "ALTER TABLE player_activity ADD COLUMN IF NOT EXISTS last_response TIMESTAMP",
        "ALTER TABLE player_activity ADD COLUMN IF NOT EXISTS warnings INTEGER DEFAULT 0",
        "ALTER TABLE player_activity ADD COLUMN IF NOT EXISTS warned_discord INTEGER DEFAULT 0",
        "ALTER TABLE player_activity ADD COLUMN IF NOT EXISTS warned_match INTEGER DEFAULT 0",
        "ALTER TABLE player_activity ADD COLUMN IF NOT EXISTS warned_response INTEGER DEFAULT 0",
        "ALTER TABLE player_activity ADD COLUMN IF NOT EXISTS status TEXT DEFAULT 'active'",
        "ALTER TABLE player_activity ADD COLUMN IF NOT EXISTS created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP"
    ]:
        try:
            cur.execute(sql)
        except Exception:
            pass

    try:
        cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_player_activity_discord_id ON player_activity(discord_id)")
    except Exception:
        pass

    # Inserisce in player_activity tutti i manager/utenti iscritti senza creare duplicati.
    try:
        cur.execute("""
            INSERT INTO player_activity
                (discord_id, last_discord_activity, last_match_played, last_response, created_at)
            SELECT x.discord_id, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
            FROM (
                SELECT discord_id FROM managers WHERE discord_id IS NOT NULL
                UNION
                SELECT discord_id FROM signup_requests WHERE discord_id IS NOT NULL AND status = 'accepted'
                UNION
                SELECT assigned_to AS discord_id FROM fc26_clubs WHERE assigned_to IS NOT NULL AND assigned_to <> ''
            ) AS x
            WHERE x.discord_id IS NOT NULL
            ON CONFLICT (discord_id) DO NOTHING
        """)
    except Exception as e:
        print(f"[ATTIVITA] Errore popolamento player_activity: {e}")

    conn.commit()
    conn.close()


def update_player_activity(discord_id, activity_type="discord"):
    ensure_activity_tables()

    field = {
        "discord": "last_discord_activity",
        "match": "last_match_played",
        "response": "last_response"
    }.get(activity_type, "last_discord_activity")

    warned_field = {
        "discord": "warned_discord",
        "match": "warned_match",
        "response": "warned_response"
    }.get(activity_type, "warned_discord")

    conn = connect()
    cur = conn.cursor()
    try:
        cur.execute(f"""
            INSERT INTO player_activity (discord_id, {field}, {warned_field}, created_at)
            VALUES (%s, CURRENT_TIMESTAMP, 0, CURRENT_TIMESTAMP)
            ON CONFLICT (discord_id)
            DO UPDATE SET {field} = CURRENT_TIMESTAMP, {warned_field} = 0
        """, (str(discord_id),))
        conn.commit()
    except Exception as e:
        conn.rollback()
        print(f"[ATTIVITA] Errore update_player_activity: {e}")
    finally:
        conn.close()


def ensure_registered_players_in_activity():
    """
    Inserisce automaticamente nella tabella attività tutti i manager/iscritti.
    Versione PostgreSQL/Supabase sicura: ogni errore fa rollback e non lascia
    la transazione in stato aborted.
    """
    ensure_activity_tables()

    conn = connect()
    cur = conn.cursor()
    try:
        cur.execute("""
            INSERT INTO player_activity
                (discord_id, last_discord_activity, last_match_played, last_response, created_at)
            SELECT x.discord_id, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
            FROM (
                SELECT discord_id FROM managers WHERE discord_id IS NOT NULL AND discord_id <> ''
                UNION
                SELECT discord_id FROM signup_requests WHERE discord_id IS NOT NULL AND discord_id <> '' AND status = 'accepted'
                UNION
                SELECT assigned_to AS discord_id FROM fc26_clubs WHERE assigned_to IS NOT NULL AND assigned_to <> ''
            ) AS x
            ON CONFLICT (discord_id) DO NOTHING
        """)
        conn.commit()
    except Exception as e:
        conn.rollback()
        print(f"[ATTIVITA] Errore ensure_registered_players_in_activity: {e}")
    finally:
        conn.close()


def mark_match_played(discord_id):
    update_player_activity(discord_id, "match")


def mark_player_response(discord_id):
    update_player_activity(discord_id, "response")


async def send_inactivity_warning(guild, member, reason, field):
    channel = guild.get_channel(INACTIVITY_CHANNEL_ID)

    if not channel:
        try:
            channel = await bot.fetch_channel(INACTIVITY_CHANNEL_ID)
        except Exception:
            channel = None

    if not channel:
        return False

    embed = discord.Embed(
        title="⚠️ Segnalazione inattività",
        description=(
            f"👤 Player: {member.mention}\n"
            f"📌 Motivo: **{reason}**\n\n"
            f"Controllo automatico ogni **22 ore**.\n\n"
            f"Lo staff può valutare:\n"
            f"• richiamo\n"
            f"• sostituzione manager\n"
            f"• liberazione club"
        ),
        color=discord.Color.orange()
    )

    await channel.send(embed=embed)

    conn = connect()
    cur = conn.cursor()
    warned_field = {
        "last_discord_activity": "warned_discord",
        "last_match_played": "warned_match",
        "last_response": "warned_response"
    }.get(field)

    if warned_field:
        try:
            cur.execute(f"""
                UPDATE player_activity
                SET {warned_field} = 1
                WHERE discord_id = %s
            """, (str(member.id),))
            conn.commit()
        except Exception as e:
            conn.rollback()
            print(f"[ATTIVITA] Errore aggiornamento warning: {e}")

    conn.close()
    return True


def _parse_sqlite_datetime(value):
    if not value:
        return None

    raw = str(value).replace("Z", "").strip()

    try:
        dt = datetime.fromisoformat(raw)
        return dt.replace(tzinfo=None) if getattr(dt, "tzinfo", None) else dt
    except Exception:
        pass

    try:
        return datetime.strptime(raw, "%Y-%m-%d %H:%M:%S")
    except Exception:
        return None




# ================= SAFE ACTIVITY HELPERS =================
# Definiti prima dei comandi/task: nel file originale erano dopo 
@tree.command(name="calendario", description="Mostra calendario partite da disputare divise per competizione")
async def calendario(interaction: discord.Interaction):
    await safe_defer(interaction, ephemeral=True, thinking=True)
    try:
        matches = unified_pending_matches(discord_id=None, only_user=False)
        if not matches:
            await interaction.followup.send("📅 Nessuna partita da disputare.", ephemeral=True)
            return

        grouped = {}
        for m in matches:
            key = f"{m['competition_name']} ({m['competition_type']})"
            grouped.setdefault(key, []).append(m)

        embeds = []
        for comp, items in grouped.items():
            lines = []
            for m in items[:12]:
                lines.append(f"• **{m['home_club']}** vs **{m['away_club']}** — {m['round']} {m['leg']}")
            embeds.append(discord.Embed(title=f"📅 {comp}", description="\\n".join(lines), color=discord.Color.blurple()))

        await interaction.followup.send(embeds=embeds[:10], ephemeral=True)
    except Exception as e:
        print(f"[CALENDARIO ERROR] {type(e).__name__}: {e}")
        await interaction.followup.send(f"❌ Errore calendario: `{type(e).__name__}`", ephemeral=True)

@tree.command(name="risultati_lista", description="Mostra ultimi risultati divisi per competizione")
async def risultati_lista(interaction: discord.Interaction):
    await safe_defer(interaction, ephemeral=True, thinking=True)
    try:
        conn = db_connect_safe()
        cur = conn.cursor()
        cur.execute("""
            SELECT competition_name, competition_type, round, home_team, away_team, home_score, away_score, created_at
            FROM match_results
            ORDER BY created_at DESC
            LIMIT 40
        """)
        rows = cur.fetchall()
        conn.close()

        if not rows:
            await interaction.followup.send("📊 Nessun risultato inserito.", ephemeral=True)
            return

        grouped = {}
        for r in rows:
            key = f"{row_get(r,'competition_name','Competizione')} ({row_get(r,'competition_type','')})"
            grouped.setdefault(key, []).append(r)

        embeds = []
        for comp, items in grouped.items():
            lines = []
            for r in items[:12]:
                lines.append(f"• **{row_get(r,'home_team')} {row_get(r,'home_score')} - {row_get(r,'away_score')} {row_get(r,'away_team')}** — {row_get(r,'round','')}")
            embeds.append(discord.Embed(title=f"📊 {comp}", description="\\n".join(lines), color=discord.Color.green()))

        await interaction.followup.send(embeds=embeds[:10], ephemeral=True)
    except Exception as e:
        print(f"[RISULTATI LISTA ERROR] {type(e).__name__}: {e}")
        await interaction.followup.send(f"❌ Errore risultati: `{type(e).__name__}`", ephemeral=True)


# e quindi non venivano mai caricati prima dell'avvio del bot.
def _fetch_all_player_activity_rows():
    """Legge i dati attività giocatori da Supabase.
    Se la tabella non esiste o lo schema non è pronto, ritorna lista vuota
    invece di generare errori continui nei log.
    """
    try:
        conn = connect()
        cur = conn.cursor()
        try:
            cur.execute("SELECT * FROM player_activity")
        except Exception:
            try:
                cur.execute("SELECT * FROM player_activity_log")
            except Exception:
                try:
                    cur.execute("SELECT * FROM players_activity")
                except Exception:
                    conn.close()
                    return []
        rows = cur.fetchall()
        conn.close()
        return rows or []
    except Exception as e:
        print(f"[ATTIVITA] Lettura attività saltata: {e}")
        return []

# =========================================================
async def check_player_inactivity():
    """Controllo automatico disattivato.
    Evita query periodiche su Supabase e spam nei log.
    Usa il comando /controllo_inattivi per eseguirlo manualmente.
    """
    print("[ATTIVITA] Controllo automatico disattivato: usa /controllo_inattivi manualmente.")
    return


@tree.command(name="controllo_inattivi", description="Staff: esegue subito il controllo inattività")
async def controllo_inattivi(interaction: discord.Interaction):
    if not can_use_normal_staff(interaction.user):
        await interaction.response.send_message("❌ Solo lo staff può usare questo comando.", ephemeral=True)
        return

    await safe_defer(interaction, ephemeral=True, thinking=True)

    # Esegue un controllo singolo senza aspettare il loop.
    try:
        rows = await asyncio.to_thread(_fetch_all_player_activity_rows)

        now = datetime.now()
        sent = 0

        for row in rows:
            discord_id = str(row["discord_id"])
            member = await get_member_safe(interaction.guild, discord_id)

            if not member or member.bot:
                continue

            registered_role = interaction.guild.get_role(int(SIGNUP_REGISTERED_ROLE_ID))
            if registered_role and registered_role not in member.roles:
                continue

            checks = [
                ("last_discord_activity", "warned_discord", "Inattività Discord nelle ultime 22 ore"),
                ("last_match_played", "warned_match", "Nessuna partita giocata/registrata nelle ultime 22 ore"),
                ("last_response", "warned_response", "Mancata risposta/interazione nelle ultime 22 ore")
            ]

            for field, warned_field, reason in checks:
                if safe_int(row[warned_field]) == 1:
                    continue

                dt = _parse_sqlite_datetime(row[field]) or _parse_sqlite_datetime(row["created_at"])
                if not dt:
                    continue

                delta_hours = (now - dt).total_seconds() / 3600
                if delta_hours >= INACTIVITY_HOURS_LIMIT:
                    ok = await send_inactivity_warning(interaction.guild, member, reason, field)
                    if ok:
                        sent += 1

        await interaction.followup.send(
            f"✅ Controllo inattività completato. Segnalazioni inviate: **{sent}**.",
            ephemeral=True
        )

    except Exception as e:
        await interaction.followup.send(f"❌ Errore controllo inattività: `{e}`", ephemeral=True)

# ===========================================================

# ================= SISTEMA FINE / NUOVA STAGIONE =================

def ensure_season_tables():
    conn = connect()
    cur = conn.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS seasons (
        id SERIAL PRIMARY KEY,
        season_name TEXT,
        name TEXT,
        status TEXT DEFAULT 'active',
        active BOOLEAN DEFAULT TRUE,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)

    for sql in [
        "ALTER TABLE seasons ADD COLUMN IF NOT EXISTS name TEXT",
        "ALTER TABLE seasons ADD COLUMN IF NOT EXISTS season_name TEXT",
        "ALTER TABLE seasons ADD COLUMN IF NOT EXISTS status TEXT DEFAULT 'active'",
        "ALTER TABLE seasons ADD COLUMN IF NOT EXISTS active BOOLEAN DEFAULT TRUE"
    ]:
        try:
            cur.execute(sql)
        except Exception:
            pass

    try:
        cur.execute("""
            UPDATE seasons
            SET name = COALESCE(name, season_name)
            WHERE name IS NULL
        """)
    except Exception:
        pass

    conn.commit()
    conn.close()


def get_active_season():
    ensure_season_tables()
    ensure_activity_tables()
    conn = connect()
    cur = conn.cursor()
    cur.execute("SELECT * FROM seasons WHERE status = 'active' ORDER BY id DESC LIMIT 1")
    row = cur.fetchone()
    conn.close()
    return row


def close_active_season():
    ensure_season_tables()
    ensure_activity_tables()
    conn = connect()
    cur = conn.cursor()
    cur.execute("UPDATE seasons SET status = 'closed', closed_at = CURRENT_TIMESTAMP WHERE status = 'active'")
    conn.commit()
    conn.close()


def create_next_season():
    ensure_season_tables()
    ensure_activity_tables()
    conn = connect()
    cur = conn.cursor()
    cur.execute("SELECT MAX(id) AS max_id FROM seasons")
    row = cur.fetchone()
    next_num = safe_int(row["max_id"], 0) + 1
    cur.execute("INSERT INTO seasons (name, season_name, status, active) VALUES (%s, %s, 'active', TRUE)", (f"Stagione {next_num}", f"Stagione {next_num}"))
    season_id = cur.lastrowid
    conn.commit()
    conn.close()
    return season_id


def reset_season_competition_data():
    """
    Reset leggero per nuova stagione:
    - chiude/azzera partite campionato vecchie
    - chiude campionati attivi
    - mantiene manager, club, rose, budget e storico trasferimenti.
    """
    conn = connect()
    cur = conn.cursor()

    try:
        cur.execute("UPDATE championship_matches SET status = 'archived' WHERE status != 'archived'")
    except Exception:
        pass

    try:
        cur.execute("UPDATE championships SET status = 'archived' WHERE status = 'active'")
    except Exception:
        pass

    try:
        cur.execute("DELETE FROM match_scorers")
    except Exception:
        pass

    conn.commit()
    conn.close()


def get_current_league_names():
    conn = connect()
    cur = conn.cursor()

    names = []

    try:
        cur.execute("""
            SELECT DISTINCT name
            FROM championship_groups
            ORDER BY name ASC
        """)
        names = [r["name"] for r in cur.fetchall()]
    except Exception:
        names = []

    if not names:
        try:
            cur.execute("""
                SELECT DISTINCT league
                FROM fc26_clubs
                WHERE league IS NOT NULL AND league != ''
                ORDER BY league ASC
            """)
            names = [r["league"] for r in cur.fetchall()]
        except Exception:
            names = []

    conn.close()

    # fallback
    if not names:
        names = ["Girone A"]

    return names


def save_league_hierarchy(league_name, parent_league=None, hierarchy_type="top"):
    season = get_active_season()
    season_id = season["id"] if season else None

    conn = connect()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO league_hierarchy (league_name, parent_league, hierarchy_type, season_id)
        VALUES (%s, %s, %s, %s)
    """, (league_name, parent_league, hierarchy_type, season_id))
    conn.commit()
    conn.close()


async def generate_new_season_competitions(interaction: discord.Interaction, with_europe=True):
    """
    Hook centrale per generare nuovi calendari.
    Se nel tuo bot hai già funzioni/comandi specifici per generare calendari/coppe,
    puoi collegarli qui. Questa funzione prepara il flusso e crea backup.
    """
    await create_backup_before_sensitive_action("avvio_nuova_stagione")

    reset_season_competition_data()
    new_season_id = create_next_season()

    # Qui il bot mantiene rose/budget/club.
    # Calendari e coppe nazionali/europee possono essere rigenerati usando le funzioni già presenti nel bot.
    # Se le tue funzioni hanno nomi specifici, collegale qui.
    generation_mode = get_group_generation_mode()
    if generation_mode == "real_leagues":
        league_names = get_real_league_names_from_assigned_clubs()
        league_note = "✅ Campionati reali usati automaticamente: " + ", ".join(league_names[:10])
    else:
        league_names = get_random_group_names_for_generation()
        league_note = "✅ Gironi random con nomi custom: " + ", ".join(league_names[:10])

    generated_notes = [
        "✅ Calendari/classifiche/statistiche della stagione precedente azzerati/archiviati.",
        "✅ Nuova stagione creata.",
        "✅ Rose, budget, club e trasferimenti mantenuti.",
        league_note,
        "✅ Coppe nazionali pronte per essere rigenerate sui nuovi gironi."
    ]

    if with_europe:
        generated_notes.append("✅ Coppe europee da generare in base ai piazzamenti della stagione precedente.")

    return new_season_id, generated_notes


class EndSeasonView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=300)

    @discord.ui.button(label="Avvia stagione nuova", style=discord.ButtonStyle.success, emoji="✅")
    async def start_new_season(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ Solo lo staff può avviare la nuova stagione.", ephemeral=True)
            return

        await safe_defer(interaction, ephemeral=True, thinking=True)

        close_active_season()
        season_id, notes = await generate_new_season_competitions(interaction, with_europe=True)

        embed = discord.Embed(
            title="✅ Nuova stagione avviata",
            description="\n".join(notes),
            color=discord.Color.green()
        )
        embed.add_field(name="ID nuova stagione", value=str(season_id), inline=True)
        embed.set_footer(text="Ora puoi rigenerare/controllare calendari, coppe nazionali e coppe europee.")

        try:
            await interaction.message.edit(embed=embed, view=None)
        except Exception:
            pass

        await interaction.followup.send("✅ Nuova stagione avviata correttamente.", ephemeral=True)

    @discord.ui.button(label="Avvia stagione con nuovi campionati", style=discord.ButtonStyle.primary, emoji="🏗️")
    async def start_with_new_leagues(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ Solo lo staff può modificare la struttura campionati.", ephemeral=True)
            return

        leagues = get_current_league_names()

        embed = discord.Embed(
            title="🏗️ Nuovi campionati",
            description=(
                "Scegli un campionato corrente dalla tendina.\n\n"
                "Poi potrai decidere se creare:\n"
                "• un campionato inferiore collegato a quello scelto\n"
                "• un nuovo campionato superiore/parallelo"
            ),
            color=discord.Color.blue()
        )

        await interaction.response.edit_message(embed=embed, view=LeagueExpansionView(leagues))


class LeagueExpansionSelect(discord.ui.Select):
    def __init__(self, leagues):
        options = []
        for league in leagues[:25]:
            options.append(discord.SelectOption(
                label=str(league)[:100],
                value=str(league),
                description="Campionato corrente"
            ))

        super().__init__(
            placeholder="Scegli il campionato corrente...",
            min_values=1,
            max_values=1,
            options=options
        )

    async def callback(self, interaction: discord.Interaction):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ Solo lo staff può modificare i campionati.", ephemeral=True)
            return

        selected_league = self.values[0]

        embed = discord.Embed(
            title="Scegli tipo nuovo campionato",
            description=(
                f"Campionato scelto: **{selected_league}**\n\n"
                "• **Campionato inferiore**: esempio Girone A → A1\n"
                "• **Nuovo campionato**: esempio nuovo Girone B"
            ),
            color=discord.Color.orange()
        )

        await interaction.response.edit_message(
            embed=embed,
            view=LeagueExpansionTypeView(selected_league)
        )


class LeagueExpansionView(discord.ui.View):
    def __init__(self, leagues):
        super().__init__(timeout=300)
        self.add_item(LeagueExpansionSelect(leagues))


class LeagueExpansionTypeView(discord.ui.View):
    def __init__(self, selected_league):
        super().__init__(timeout=300)
        self.selected_league = selected_league

    @discord.ui.button(label="Campionato inferiore", style=discord.ButtonStyle.secondary, emoji="⬇️")
    async def lower_league(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ Solo lo staff può modificare i campionati.", ephemeral=True)
            return

        await interaction.response.send_modal(NewLeagueNameModal(self.selected_league, "lower"))

    @discord.ui.button(label="Nuovo campionato", style=discord.ButtonStyle.primary, emoji="🆕")
    async def new_top_league(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ Solo lo staff può modificare i campionati.", ephemeral=True)
            return

        await interaction.response.send_modal(NewLeagueNameModal(self.selected_league, "top"))


class NewLeagueNameModal(discord.ui.Modal, title="Crea nuovo campionato"):
    league_name = discord.ui.TextInput(
        label="Nome nuovo campionato",
        placeholder="Esempio: A1 oppure Girone B",
        required=True,
        max_length=80
    )

    def __init__(self, parent_league, hierarchy_type):
        super().__init__()
        self.parent_league = parent_league
        self.hierarchy_type = hierarchy_type

    async def on_submit(self, interaction: discord.Interaction):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ Solo lo staff può creare campionati.", ephemeral=True)
            return

        await safe_defer(interaction, ephemeral=True, thinking=True)
        await create_backup_before_sensitive_action("nuovi_campionati")

        new_name = str(self.league_name.value).strip()

        if self.hierarchy_type == "lower":
            save_league_hierarchy(new_name, parent_league=self.parent_league, hierarchy_type="lower")
            description = (
                f"✅ Creato campionato inferiore **{new_name}** collegato a **{self.parent_league}**.\n\n"
                "Questo potrà essere usato per promozioni/retrocessioni nelle stagioni successive."
            )
        else:
            save_league_hierarchy(new_name, parent_league=None, hierarchy_type="top")
            description = (
                f"✅ Creato nuovo campionato principale/parallelo: **{new_name}**.\n\n"
                "Esempio: se prima avevi Girone A, ora puoi avere anche Girone B."
            )

        close_active_season()
        season_id, notes = await generate_new_season_competitions(interaction, with_europe=True)

        embed = discord.Embed(
            title="🏗️ Stagione con nuovi campionati avviata",
            description=description + "\n\n" + "\n".join(notes),
            color=discord.Color.green()
        )
        embed.add_field(name="Nuova stagione ID", value=str(season_id), inline=True)

        await interaction.followup.send(embed=embed, ephemeral=True)


@tree.command(name="fine_stagione", description="Owner staff: chiude la stagione e avvia il flusso nuova stagione")
async def fine_stagione(interaction: discord.Interaction):
    async def show_end_panel(confirm_interaction: discord.Interaction):
        ensure_season_tables()
        season = get_active_season()
        await create_backup_before_sensitive_action("fine_stagione")

        embed = discord.Embed(
            title="🏁 Fine stagione",
            description=(
                f"Stagione attiva: **{season['name'] if season else 'N/D'}**\n\n"
                "Scegli cosa fare:\n\n"
                "✅ **Avvia stagione nuova**\n"
                "Resetta calendari, classifiche e statistiche stagionali; mantiene rose, budget, club e storico.\n"
                "Rigenera campionati, coppe nazionali e coppe europee in base ai piazzamenti.\n\n"
                "🏗️ **Avvia stagione con nuovi campionati**\n"
                "Permette di aggiungere campionati inferiori o nuovi campionati principali/paralleli."
            ),
            color=discord.Color.gold()
        )

        await confirm_interaction.response.edit_message(embed=embed, view=EndSeasonView())
        await send_staff_log(
            confirm_interaction.guild,
            "🏁 Fine stagione confermata",
            "Lo staff ha confermato l'apertura del pannello di fine stagione.",
            user=confirm_interaction.user,
            color=discord.Color.gold()
        )

    await ask_danger_confirmation(
        interaction,
        "Fine stagione",
        "Stai per chiudere la stagione e aprire il pannello di nuova stagione.",
        show_end_panel
    )

@tree.command(name="setup_iscrizioni", description="Staff: pubblica il pannello richiesta iscrizione FC26")
async def setup_iscrizioni(interaction: discord.Interaction):
    try:
        await safe_defer(interaction, ephemeral=True, thinking=True)
    except Exception as e:
        print(f"[SETUP ISCRIZIONI] Defer fallito: {e}")
        return

    try:
        if not can_manage_signup(interaction.user):
            await interaction.followup.send("❌ Solo lo staff può usare questo comando.", ephemeral=True)
            return

        channel = None
        if interaction.guild:
            channel = interaction.guild.get_channel(int(SIGNUP_REQUEST_CHANNEL_ID))

        if not channel:
            try:
                channel = await bot.fetch_channel(int(SIGNUP_REQUEST_CHANNEL_ID))
            except Exception:
                channel = None

        if not channel:
            await interaction.followup.send(
                f"❌ Canale richiesta iscrizione non trovato.\n"
                f"Server attuale: `{interaction.guild.id if interaction.guild else None}`\n"
                f"Canale cercato: `{SIGNUP_REQUEST_CHANNEL_ID}`\n"
                "Controlla che il bot abbia permesso **Visualizza canale** e che sia un canale testo normale.",
                ephemeral=True
            )
            return

        embed = discord.Embed(
            title="📋 Richiesta iscrizione torneo FC 26",
            description=(
                "Premi il pulsante qui sotto e compila:\n\n"
                "• **Nome**\n"
                "• **Età**\n"
                "• **Piattaforma**\n"
                "• **ID PSN/Xbox/EA**\n\n"
                "Dopo l'invio, lo staff controllerà la richiesta e assegnerà un club libero.\n"
                "Se la modalità attiva è **Squadre reali**, dovrai indicare almeno **2 club preferiti**."
            ),
            color=discord.Color.blue()
        )

        await channel.send(embed=embed, view=SignupStartView())
        await interaction.followup.send("✅ Pannello iscrizioni pubblicato.", ephemeral=True)

        try:
            await send_staff_log(
                interaction.guild,
                "📋 Pannello iscrizioni pubblicato",
                f"Canale: <#{SIGNUP_REQUEST_CHANNEL_ID}>",
                user=interaction.user,
                color=discord.Color.blue()
            )
        except Exception as e:
            print(f"[SETUP ISCRIZIONI] Errore log staff: {e}")

    except Exception as e:
        print(f"[SETUP ISCRIZIONI] Errore: {e}")
        try:
            await interaction.followup.send(f"❌ Errore setup iscrizioni: `{e}`", ephemeral=True)
        except Exception:
            pass


@tree.command(name="assegna_club", description="Staff: assegna manualmente un club a una richiesta/utente")
@app_commands.describe(utente="Player da accettare", club="Nome del club da assegnare")
async def assegna_club(interaction: discord.Interaction, utente: discord.Member, club: str):
    if not can_manage_signup(interaction.user):
        await interaction.response.send_message("❌ Solo lo staff può usare questo comando.", ephemeral=True)
        return

    conn = connect()
    cur = conn.cursor()
    cur.execute("""
        SELECT *
        FROM signup_requests
        WHERE discord_id = %s AND status = 'pending'
        ORDER BY id DESC
        LIMIT 1
    """, (str(utente.id),))
    request = cur.fetchone()
    conn.close()

    if not request:
        await interaction.response.send_message("❌ Questo utente non ha una richiesta pending.", ephemeral=True)
        return

    club_row = get_club_row_by_name(club)
    if not club_row:
        await interaction.response.send_message("❌ Club non trovato nel database fc26_clubs.", ephemeral=True)
        return
    if club_row["assigned_to"]:
        await interaction.response.send_message("❌ Questo club è già stato assegnato.", ephemeral=True)
        return

    await complete_signup_accept(interaction, int(request["id"]), club_row["name"])


@tree.command(name="club_liberi", description="Mostra i club liberi per le iscrizioni")
async def club_liberi(interaction: discord.Interaction):
    if not can_manage_signup(interaction.user):
        await interaction.response.send_message("❌ Solo lo staff può usare questo comando.", ephemeral=True)
        return
    clubs = get_free_signup_clubs()
    text = "\n".join(f"• {c}" for c in clubs[:50]) if clubs else "Nessun club libero."
    await interaction.response.send_message(f"🏟️ **Club liberi**\n{text}", ephemeral=True)


@tree.command(name="libera_club", description="Staff: libera il club di un player mantenendo rosa, budget e dati per il prossimo assegnatario")
@app_commands.describe(utente="Player che abbandona o da rimuovere dal club")
async def libera_club(interaction: discord.Interaction, utente: discord.Member):
    if not can_manage_signup(interaction.user):
        await interaction.response.send_message("❌ Solo lo staff può usare questo comando.", ephemeral=True)
        return

    await safe_defer(interaction, ephemeral=True, thinking=True)

    conn = connect()
    cur = conn.cursor()
    cur.execute("SELECT name, league FROM fc26_clubs WHERE assigned_to = %s", (str(utente.id),))
    club = cur.fetchone()

    if not club:
        conn.close()
        await interaction.followup.send("❌ Questo player non ha nessun club assegnato.", ephemeral=True)
        return

    club_name = club["name"]
    league_name = club["league"] or "N/D"

    cur.execute("""
        UPDATE fc26_clubs
        SET assigned_to = NULL,
            assigned_at = NULL,
            previous_owner_id = %s,
            previous_owner_name = %s
        WHERE name = %s
    """, (str(utente.id), utente.display_name, club_name))

    cur.execute("""
        UPDATE signup_requests
        SET status = 'released'
        WHERE discord_id = %s AND status = 'accepted'
    """, (str(utente.id),))

    conn.commit()
    conn.close()

    registered_role = interaction.guild.get_role(int(SIGNUP_REGISTERED_ROLE_ID)) if interaction.guild else None
    request_role = interaction.guild.get_role(int(REQUEST_ROLE_ID)) if interaction.guild else None

    if registered_role:
        try:
            await utente.remove_roles(registered_role, reason="Club liberato dallo staff")
        except Exception:
            pass

    if request_role:
        try:
            await utente.add_roles(request_role, reason="Club liberato dallo staff")
        except Exception:
            pass

    await interaction.followup.send(
        f"✅ Club **{club_name}** liberato.\n"
        f"👤 Vecchio player: {utente.mention}\n"
        f"🏆 Campionato: **{league_name}**\n\n"
        f"Il club torna disponibile nel menu iscrizioni. Il prossimo player che riceverà **{club_name}** erediterà rosa, budget, partite e statistiche.",
        ephemeral=True
    )

# ===========================================================





class SignupModal(discord.ui.Modal, title="Richiesta iscrizione FC26"):
    nome = discord.ui.TextInput(label="Nome", placeholder="Inserisci il tuo nome", required=True, max_length=50)
    eta = discord.ui.TextInput(label="Età", placeholder="Esempio: 18", required=True, max_length=3)
    piattaforma = discord.ui.TextInput(label="Piattaforma", placeholder="PS5 / Xbox / PC", required=True, max_length=30)
    game_id = discord.ui.TextInput(label="ID PSN/Xbox/EA", placeholder="Inserisci il tuo ID", required=True, max_length=60)
    club_preferiti = discord.ui.TextInput(
        label="Club che vorresti",
        placeholder="Solo modalità reale: scrivine almeno 2, es. Milan, Real Madrid",
        required=False,
        max_length=200
    )

    async def on_submit(self, interaction: discord.Interaction):
        # Risposta immediata al modal: evita "Unknown interaction" / "Qualcosa è andato storto".
        try:
            await safe_defer(interaction, ephemeral=True, thinking=True)
        except Exception as e:
            print(f"[SIGNUP MODAL] Defer fallito: {e}")
            return

        try:
            # Compatibilità con i nomi reali dei campi del modal:
            # nome / eta / piattaforma / game_id / club_preferiti
            real_name = str(getattr(self, "nome").value).strip()
            age = str(getattr(self, "eta").value).strip()
            platform = str(getattr(self, "piattaforma").value).strip()
            game_id = str(getattr(self, "game_id").value).strip()

            try:
                club_preferences = str(getattr(self, "club_preferiti").value).strip()
            except Exception:
                club_preferences = ""

            conn = connect()
            cur = conn.cursor()

            # Sicurezza schema Supabase
            for sql in [
                "ALTER TABLE signup_requests ADD COLUMN IF NOT EXISTS discord_name TEXT",
                "ALTER TABLE signup_requests ADD COLUMN IF NOT EXISTS real_name TEXT",
                "ALTER TABLE signup_requests ADD COLUMN IF NOT EXISTS age TEXT",
                "ALTER TABLE signup_requests ADD COLUMN IF NOT EXISTS platform TEXT",
                "ALTER TABLE signup_requests ADD COLUMN IF NOT EXISTS ea_id TEXT",
                "ALTER TABLE signup_requests ADD COLUMN IF NOT EXISTS game_id TEXT",
                "ALTER TABLE signup_requests ADD COLUMN IF NOT EXISTS preferred_clubs TEXT",
                "ALTER TABLE signup_requests ADD COLUMN IF NOT EXISTS club_preferences TEXT",
                "ALTER TABLE signup_requests ADD COLUMN IF NOT EXISTS club_name TEXT",
                "ALTER TABLE signup_requests ADD COLUMN IF NOT EXISTS handled_by TEXT",
                "ALTER TABLE signup_requests ADD COLUMN IF NOT EXISTS handled_at TIMESTAMP",
                "ALTER TABLE signup_requests ADD COLUMN IF NOT EXISTS status TEXT DEFAULT 'pending'",
                "ALTER TABLE signup_requests ADD COLUMN IF NOT EXISTS created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP",
                "ALTER TABLE signup_requests ADD COLUMN IF NOT EXISTS staff_message_id TEXT",
                "ALTER TABLE signup_requests ADD COLUMN IF NOT EXISTS staff_channel_id TEXT",
                "ALTER TABLE signup_requests ADD COLUMN IF NOT EXISTS signup_source TEXT",
                "ALTER TABLE signup_requests ADD COLUMN IF NOT EXISTS source TEXT"
            ]:
                try:
                    cur.execute(sql)
                except Exception:
                    pass

            cur.execute(
                "SELECT id FROM signup_requests WHERE discord_id = %s AND status = 'pending' LIMIT 1",
                (str(interaction.user.id),)
            )
            existing = cur.fetchone()

            if existing:
                conn.close()
                await interaction.followup.send("⚠️ Hai già una richiesta in attesa di valutazione.", ephemeral=True)
                return

            cur.execute(
                "SELECT id FROM signup_requests WHERE discord_id = %s AND status = 'accepted' LIMIT 1",
                (str(interaction.user.id),)
            )
            accepted = cur.fetchone()

            if accepted:
                conn.close()
                await interaction.followup.send(
                    "❌ Sei già iscritto al torneo. Non puoi inviare una nuova richiesta.",
                    ephemeral=True
                )
                return

            cur.execute("""
                INSERT INTO signup_requests
                (discord_id, discord_name, real_name, age, platform, game_id,
                 ea_id, preferred_clubs, club_preferences, status, created_at, signup_source, source)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'pending', CURRENT_TIMESTAMP, 'discord', 'discord')
            """, (
                str(interaction.user.id),
                str(interaction.user),
                real_name,
                age,
                platform,
                game_id,
                game_id,
                club_preferences,
                club_preferences
            ))

            conn.commit()

            cur.execute("""
                SELECT id
                FROM signup_requests
                WHERE discord_id = %s
                ORDER BY id DESC
                LIMIT 1
            """, (str(interaction.user.id),))
            req = cur.fetchone()
            request_id = req["id"] if req else "?"

            conn.close()

            try:
                await apply_signup_role_pending(
                    interaction.guild,
                    interaction.user,
                    reason="Richiesta iscrizione FC26"
                )
            except Exception as e:
                print(f"[SIGNUP MODAL] Errore ruolo PRE-ISCRITTO: {e}")

            try:
                await publish_signup_request_once(int(request_id), interaction.guild, source="discord")
            except Exception as e:
                print(f"[SIGNUP MODAL] Errore invio staff unico: {e}")

            await interaction.followup.send(
                "✅ Richiesta inviata correttamente. Lo staff la controllerà appena possibile.",
                ephemeral=True
            )

        except Exception as e:
            print(f"[SIGNUP MODAL] Errore submit: {e}")
            try:
                await interaction.followup.send(f"❌ Errore invio richiesta: `{e}`", ephemeral=True)
            except Exception:
                pass


def get_signup_request(request_id):
    conn = connect()
    cur = conn.cursor()
    cur.execute("SELECT * FROM signup_requests WHERE id = %s", (int(request_id),))
    row = cur.fetchone()
    conn.close()
    return row


def _row_get(row, key, default=None):
    try:
        value = row.get(key)
    except Exception:
        try:
            value = row[key]
        except Exception:
            value = default
    return default if value is None else value


async def publish_signup_request_once(request_id: int, guild=None, *, source="discord"):
    """Pubblica una richiesta iscrizione nel canale staff una sola volta.

    Usa Supabase/PostgreSQL come unica fonte dati. Per evitare doppi messaggi,
    acquisisce un lock direttamente nella riga signup_requests prima di inviare.
    """
    request_id = int(request_id)
    staff_channel_id = str(SIGNUP_STAFF_CHANNEL_ID)

    # Lock atomico: se un altro processo/deploy ha già preso in carico la richiesta,
    # non inviamo un secondo messaggio.
    conn = connect()
    cur = conn.cursor()
    locked = None
    try:
        # Le colonne devono essere presenti nel DB Supabase; se non lo sono, le aggiunge una sola volta.
        # Se vuoi zero migrazioni nel bot, crea queste colonne da Supabase SQL Editor e rimuovi questi ALTER.
        for sql in [
            "ALTER TABLE signup_requests ADD COLUMN IF NOT EXISTS staff_message_id TEXT",
            "ALTER TABLE signup_requests ADD COLUMN IF NOT EXISTS staff_channel_id TEXT",
            "ALTER TABLE signup_requests ADD COLUMN IF NOT EXISTS signup_source TEXT",
            "ALTER TABLE signup_requests ADD COLUMN IF NOT EXISTS source TEXT"
        ]:
            try:
                cur.execute(sql)
            except Exception:
                pass

        cur.execute("""
            UPDATE signup_requests
            SET staff_message_id = %s,
                staff_channel_id = %s,
                signup_source = COALESCE(NULLIF(signup_source, ''), %s),
                source = COALESCE(NULLIF(source, ''), %s)
            WHERE id = %s
              AND status = 'pending'
              AND (staff_message_id IS NULL OR staff_message_id = '')
            RETURNING *
        """, ("LOCKING", staff_channel_id, str(source), str(source), request_id))
        locked = cur.fetchone()
        conn.commit()
    except Exception as e:
        conn.rollback()
        print(f"[SIGNUP STAFF] Errore lock richiesta #{request_id}: {e}")
    finally:
        conn.close()

    if not locked:
        print(f"[SIGNUP STAFF] Richiesta #{request_id} già pubblicata o non pending: salto invio doppio.")
        return False

    req = get_signup_request(request_id) or locked
    discord_id = str(_row_get(req, "discord_id", "") or "").strip()
    real_name = str(_row_get(req, "real_name", _row_get(req, "discord_name", "-")) or "-")
    age = str(_row_get(req, "age", "-") or "-")
    platform = str(_row_get(req, "platform", "-") or "-")
    game_id = str(_row_get(req, "game_id", _row_get(req, "psn_id", _row_get(req, "ea_id", "-"))) or "-")
    preferred = str(_row_get(req, "preferred_clubs", _row_get(req, "club_preferences", "")) or "").strip()

    try:
        staff_channel = None
        if guild:
            staff_channel = guild.get_channel(int(staff_channel_id))
        if not staff_channel:
            staff_channel = bot.get_channel(int(staff_channel_id))
        if not staff_channel:
            staff_channel = await bot.fetch_channel(int(staff_channel_id))
    except Exception as e:
        print(f"[SIGNUP STAFF] Canale log iscrizioni non trovato ({staff_channel_id}): {e}")
        # Sblocca la riga per poter riprovare dopo aver sistemato canale/permessi.
        conn = connect()
        cur = conn.cursor()
        try:
            cur.execute("""
                UPDATE signup_requests
                SET staff_message_id = NULL,
                    staff_channel_id = NULL
                WHERE id = %s AND staff_message_id = 'LOCKING'
            """, (request_id,))
            conn.commit()
        except Exception:
            conn.rollback()
        finally:
            conn.close()
        return False

    embed = discord.Embed(
        title="📩 Nuova richiesta iscrizione FC26",
        description=f"Richiesta ID: **{request_id}**",
        color=discord.Color.orange()
    )
    if source == "website":
        embed.description += "\nFonte: **sito web**"

    if discord_id:
        embed.add_field(name="Player Discord", value=f"<@{discord_id}>", inline=False)
        embed.add_field(name="Discord ID", value=discord_id, inline=False)
    else:
        embed.add_field(name="Player Discord", value=str(_row_get(req, "discord_name", "Player")), inline=False)

    embed.add_field(name="Nome", value=real_name or "-", inline=True)
    embed.add_field(name="Età", value=age or "-", inline=True)
    embed.add_field(name="Piattaforma", value=platform or "-", inline=True)
    embed.add_field(name="ID PSN/Xbox/EA", value=game_id or "-", inline=False)
    if preferred and preferred != "-":
        embed.add_field(name="Club preferiti", value=preferred, inline=False)
    try:
        embed.add_field(name="Modalità attiva", value=get_league_mode(), inline=False)
    except Exception:
        pass
    embed.set_footer(text="Lo staff deve scegliere ACCETTA o RIFIUTA.")

    try:
        staff_message = await staff_channel.send(embed=embed, view=SignupStaffView(request_id))
    except Exception as e:
        print(f"[SIGNUP STAFF] Errore invio richiesta #{request_id}: {e}")
        conn = connect()
        cur = conn.cursor()
        try:
            cur.execute("""
                UPDATE signup_requests
                SET staff_message_id = NULL,
                    staff_channel_id = NULL
                WHERE id = %s AND staff_message_id = 'LOCKING'
            """, (request_id,))
            conn.commit()
        except Exception:
            conn.rollback()
        finally:
            conn.close()
        return False

    conn = connect()
    cur = conn.cursor()
    try:
        cur.execute("""
            UPDATE signup_requests
            SET staff_message_id = %s,
                staff_channel_id = %s
            WHERE id = %s
        """, (str(staff_message.id), str(staff_channel.id), request_id))
        conn.commit()
    except Exception as e:
        conn.rollback()
        print(f"[SIGNUP STAFF] Errore salvataggio messaggio richiesta #{request_id}: {e}")
    finally:
        conn.close()

    print(f"[SIGNUP STAFF] Richiesta #{request_id} pubblicata una sola volta nel canale {staff_channel_id}.")
    return True


def get_free_signup_clubs(league=None):
    conn = connect()
    cur = conn.cursor()
    try:
        if league:
            cur.execute(
                "SELECT name FROM fc26_clubs WHERE assigned_to IS NULL AND league = %s ORDER BY name ASC",
                (str(league),)
            )
        else:
            cur.execute("SELECT name FROM fc26_clubs WHERE assigned_to IS NULL ORDER BY name ASC")
        rows = cur.fetchall()
        clubs = [r["name"] for r in rows]
    except Exception as e:
        print(f"[SIGNUP CLUBS] Errore lettura club da database: {e}")
        clubs = []
    conn.close()
    return clubs


def get_free_signup_leagues():
    conn = connect()
    cur = conn.cursor()
    try:
        cur.execute("""
            SELECT league, COUNT(*) AS total
            FROM fc26_clubs
            WHERE assigned_to IS NULL
            GROUP BY league
            ORDER BY league ASC
        """)
        rows = cur.fetchall()
        leagues = [(r["league"] or "Senza campionato", r["total"]) for r in rows if r["total"] > 0]
    except Exception as e:
        print(f"[SIGNUP LEAGUES] Errore lettura campionati da database: {e}")
        leagues = []
    conn.close()
    return leagues


async def complete_signup_accept(interaction: discord.Interaction, request_id: int, club: str):
    request = get_signup_request(request_id)
    if not request or request["status"] != "pending":
        await interaction.response.send_message("Richiesta non valida o già gestita.", ephemeral=True)
        return

    guild = interaction.guild
    member = await get_member_safe(guild, request["discord_id"]) if "get_member_safe" in globals() else guild.get_member(int(request["discord_id"]))
    if not member:
        await interaction.response.send_message("Player non trovato nel server.", ephemeral=True)
        return

    conn = connect()
    cur = conn.cursor()

    try:
        cur.execute("ALTER TABLE fc26_clubs ADD COLUMN IF NOT EXISTS previous_owner_id TEXT")
    except Exception:
        pass

    cur.execute("SELECT name, league, assigned_to, previous_owner_id FROM fc26_clubs WHERE LOWER(name) = LOWER(%s)", (str(club).strip(),))
    club_row = cur.fetchone()
    if not club_row:
        conn.close()
        await interaction.response.send_message("❌ Club non trovato nel database.", ephemeral=True)
        return
    if club_row["assigned_to"]:
        conn.close()
        await interaction.response.send_message("❌ Questo club non è più libero. Scegli un altro club.", ephemeral=True)
        return

    club_name = club_row["name"]
    league_name = club_row["league"] or "N/D"
    mode = get_league_mode() if "get_league_mode" in globals() else "fantacalcio"

    budget = DEFAULT_BUDGET

    if mode == "squadre_reali":
        previous_owner_id = None
        try:
            previous_owner_id = club_row["previous_owner_id"]
        except Exception:
            previous_owner_id = None

        # Se il club era stato liberato, il nuovo manager eredita rosa/budget dal vecchio owner.
        if previous_owner_id:
            cur.execute("SELECT budget FROM managers WHERE discord_id = %s", (str(previous_owner_id),))
            old_manager = cur.fetchone()
            if old_manager:
                budget = safe_int(old_manager["budget"], DEFAULT_BUDGET)

            cur.execute("UPDATE players SET owner_discord_id = %s WHERE owner_discord_id = %s", (str(member.id), str(previous_owner_id)))
            cur.execute("UPDATE championship_players SET discord_id = %s, display_name = %s WHERE discord_id = %s", (str(member.id), member.display_name, str(previous_owner_id)))
            cur.execute("UPDATE championship_matches SET home_id = %s, home_name = %s WHERE home_id = %s", (str(member.id), member.display_name, str(previous_owner_id)))
            cur.execute("UPDATE championship_matches SET away_id = %s, away_name = %s WHERE away_id = %s", (str(member.id), member.display_name, str(previous_owner_id)))
            cur.execute("UPDATE match_scorers SET team_owner_id = %s WHERE team_owner_id = %s", (str(member.id), str(previous_owner_id)))
            cur.execute("UPDATE transfer_history SET manager_id = %s, manager_name = %s WHERE manager_id = %s", (str(member.id), member.display_name, str(previous_owner_id)))
        else:
            players, avg_ovr, budget_real = get_team_stats(club_name)

            if not players:
                conn.close()
                await interaction.response.send_message(
                    "❌ Modalità Squadre Reali attiva, ma non ho trovato giocatori liberi per questo club nel database. "
                    "Usa `/diagnostica_squadra nome:` per verificare il nome esatto della squadra nel database.",
                    ephemeral=True
                )
                return

            budget = budget_real
            for p in players:
                cur.execute(
                    "UPDATE players SET owner_discord_id = %s, sold_price = %s WHERE id = CAST(%s AS BIGINT)",
                    (str(member.id), 0, p["id"])
                )

    cur.execute(
        "INSERT INTO managers (discord_id, name, budget) VALUES (%s, %s, %s)",
        (str(member.id), member.display_name, budget)
    )
    cur.execute(
        "UPDATE managers SET name = %s, budget = %s WHERE discord_id = %s",
        (member.display_name, budget, str(member.id))
    )

    cur.execute("UPDATE fc26_clubs SET assigned_to = %s, assigned_at = CURRENT_TIMESTAMP, previous_owner_id = NULL, previous_owner_name = NULL WHERE name = %s", (str(member.id), club_name))

    cur.execute("""
        UPDATE signup_requests
        SET status = 'accepted', club_name = %s, handled_by = %s, handled_at = CURRENT_TIMESTAMP
        WHERE id = %s
    """, (club_name, str(interaction.user.id), int(request_id)))

    conn.commit()
    conn.close()

    pending_role = guild.get_role(int(SIGNUP_PENDING_ROLE_ID))
    registered_role = guild.get_role(int(SIGNUP_REGISTERED_ROLE_ID))
    if registered_role:
        try:
            await member.add_roles(registered_role, reason="Iscrizione FC26 accettata")
        except Exception:
            pass
    if pending_role:
        try:
            await member.remove_roles(pending_role, reason="Iscrizione FC26 completata")
        except Exception:
            pass

    dm_embed = discord.Embed(
        title="✅ Richiesta accettata!",
        description="La tua iscrizione al torneo **FC 26 Manager Mode** è stata approvata.",
        color=discord.Color.green()
    )
    dm_embed.add_field(name="Club assegnato", value=club_name, inline=True)
    dm_embed.add_field(name="Campionato", value=league_name, inline=True)
    dm_embed.add_field(name="Piattaforma", value=request["platform"], inline=True)
    dm_embed.add_field(name="ID PSN/Xbox/EA", value=request["game_id"], inline=False)
    dm_embed.add_field(name="Budget iniziale", value=f"{budget} crediti", inline=True)
    dm_embed.add_field(name="Stato", value="Iscritto ufficiale", inline=True)

    try:
        await member.send(embed=dm_embed)
    except Exception:
        pass

    # Notifica unica: il messaggio staff originale viene aggiornato sotto.

    embed = discord.Embed(
        title="✅ Iscrizione completata",
        description=f"{member.mention} è stato registrato ufficialmente.",
        color=discord.Color.green()
    )
    embed.add_field(name="Club", value=club_name, inline=True)
    embed.add_field(name="Campionato", value=league_name, inline=True)
    embed.add_field(name="ID PSN/Xbox/EA", value=request["game_id"], inline=True)
    embed.add_field(name="Budget", value=f"{budget} crediti", inline=True)

    try:
        await interaction.response.edit_message(embed=embed, view=None)
    except Exception:
        await interaction.message.edit(embed=embed, view=None)

    # Nessuna seconda comunicazione nel log: resta solo il messaggio staff aggiornato.



SIGNUP_MENU_PAGE_SIZE = 25


async def send_signup_result_channel(guild, status, request_id, discord_id, *, req=None, club_name=None, league=None, budget=None, players_count=None, avg_ovr=None, staff_user=None):
    """Invia una comunicazione pubblica unica nel canale ACCETTATE/RIFIUTATE."""
    try:
        accepted = str(status) == "accepted"
        channel_id = SIGNUP_ACCEPT_CHANNEL_ID if accepted else SIGNUP_REJECT_CHANNEL_ID
        channel = None
        if guild:
            channel = guild.get_channel(int(channel_id))
        if not channel:
            channel = bot.get_channel(int(channel_id))
        if not channel:
            channel = await bot.fetch_channel(int(channel_id))
        if not channel:
            return False

        if accepted:
            embed = discord.Embed(
                title="✅ Iscrizione accettata",
                description=f"<@{discord_id}> è stato registrato ufficialmente.",
                color=discord.Color.green()
            )
            embed.add_field(name="Richiesta", value=f"#{request_id}", inline=True)
            if club_name:
                embed.add_field(name="Club", value=str(club_name), inline=True)
            if league:
                embed.add_field(name="Campionato", value=str(league), inline=True)
            if budget is not None:
                embed.add_field(name="Budget", value=f"{budget} crediti", inline=True)
            if players_count is not None:
                embed.add_field(name="Giocatori assegnati", value=str(players_count), inline=True)
            if avg_ovr:
                embed.add_field(name="OVR medio", value=f"{float(avg_ovr):.1f}", inline=True)
            if staff_user:
                embed.add_field(name="Gestito da", value=staff_user.mention, inline=False)
            embed.set_footer(text="FC26 Iscrizioni • Accettate")
        else:
            embed = discord.Embed(
                title="❌ Iscrizione rifiutata",
                description=f"<@{discord_id}> non è stato accettato al torneo FC26.",
                color=discord.Color.red()
            )
            embed.add_field(name="Richiesta", value=f"#{request_id}", inline=True)
            if req:
                embed.add_field(name="Nome", value=str(req.get("real_name") or req.get("discord_name") or "-"), inline=True)
                embed.add_field(name="Piattaforma", value=str(req.get("platform") or "-"), inline=True)
            if staff_user:
                embed.add_field(name="Gestito da", value=staff_user.mention, inline=False)
            embed.set_footer(text="FC26 Iscrizioni • Rifiutate")

        await channel.send(embed=embed)
        return True
    except Exception as e:
        print(f"[SIGNUP RESULT CHANNEL] Errore invio esito richiesta #{request_id}: {e}")
        return False


async def close_original_signup_staff_message(guild, req, embed):
    """Chiude/aggiorna il messaggio originale della richiesta nel canale LOG ISCRIZIONI."""
    try:
        channel_id = str(req.get("staff_channel_id") or "").strip()
        message_id = str(req.get("staff_message_id") or "").strip()
        if not channel_id or not message_id or message_id == "LOCKING":
            return False
        channel = None
        if guild:
            channel = guild.get_channel(int(channel_id))
        if not channel:
            channel = bot.get_channel(int(channel_id))
        if not channel:
            channel = await bot.fetch_channel(int(channel_id))
        if not channel:
            return False
        msg = await channel.fetch_message(int(message_id))
        await msg.edit(embed=embed, view=None)
        return True
    except Exception as e:
        print(f"[SIGNUP STAFF CLOSE] Errore chiusura richiesta originale: {e}")
        return False


class SignupLeagueSelect(discord.ui.Select):
    def __init__(self, request_id: int, rows, page=0):
        self.request_id = int(request_id)
        self.rows = rows
        self.page = int(page or 0)

        page_rows = rows[self.page * SIGNUP_MENU_PAGE_SIZE:(self.page + 1) * SIGNUP_MENU_PAGE_SIZE]
        options = []

        for row in page_rows:
            league_name = row.get("league") or "Altri"
            free_count = row.get("free_count") or 0
            options.append(discord.SelectOption(
                label=str(league_name)[:100],
                value=str(league_name)[:100],
                description=f"{free_count} squadre libere"
            ))

        if not options:
            options.append(discord.SelectOption(
                label="Nessuna squadra libera",
                value="__none__",
                description="Non ci sono club disponibili"
            ))

        super().__init__(
            placeholder=f"Scegli il campionato... pagina {self.page + 1}",
            min_values=1,
            max_values=1,
            options=options
        )

    async def callback(self, interaction: discord.Interaction):
        try:
            await safe_defer(interaction, ephemeral=True, thinking=True)
        except Exception:
            pass

        if not can_manage_signup(interaction.user):
            await interaction.followup.send("❌ Non hai i permessi.", ephemeral=True)
            return

        league = self.values[0]
        if league == "__none__":
            await interaction.followup.send("❌ Nessuna squadra libera disponibile.", ephemeral=True)
            return

        await interaction.followup.send(
            f"Campionato selezionato: **{league}**\nOra scegli la squadra libera:",
            view=SignupClubSelectView(self.request_id, league, page=0),
            ephemeral=True
        )


class SignupLeagueSelectView(discord.ui.View):
    def __init__(self, request_id: int, page=0):
        super().__init__(timeout=180)
        self.request_id = int(request_id)
        self.page = int(page or 0)

        conn = connect()
        cur = conn.cursor()
        try:
            cur.execute("""
                SELECT league, COUNT(*) AS free_count
                FROM fc26_clubs
                WHERE assigned_to IS NULL OR assigned_to = ''
                GROUP BY league
                ORDER BY league ASC
            """)
            self.rows = cur.fetchall()
        except Exception as e:
            print(f"[SIGNUP LEAGUE SELECT] Errore caricamento campionati: {e}")
            self.rows = []
        conn.close()

        self.max_page = max(0, (len(self.rows) - 1) // SIGNUP_MENU_PAGE_SIZE)
        self.page = max(0, min(self.page, self.max_page))
        self.add_item(SignupLeagueSelect(self.request_id, self.rows, self.page))

    @discord.ui.button(label="⬅️ Indietro", style=discord.ButtonStyle.secondary)
    async def previous_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(
            content=f"Modalità **Squadre reali** attiva.\nScegli prima il campionato:\nPagina **{max(0, self.page - 1) + 1}/{self.max_page + 1}**",
            view=SignupLeagueSelectView(self.request_id, max(0, self.page - 1))
        )

    @discord.ui.button(label="Avanti ➡️", style=discord.ButtonStyle.primary)
    async def next_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(
            content=f"Modalità **Squadre reali** attiva.\nScegli prima il campionato:\nPagina **{min(self.max_page, self.page + 1) + 1}/{self.max_page + 1}**",
            view=SignupLeagueSelectView(self.request_id, min(self.max_page, self.page + 1))
        )


class SignupClubSelect(discord.ui.Select):
    def __init__(self, request_id: int, league: str, rows, page=0):
        self.request_id = int(request_id)
        self.league = str(league)
        self.rows = rows
        self.page = int(page or 0)

        page_rows = rows[self.page * SIGNUP_MENU_PAGE_SIZE:(self.page + 1) * SIGNUP_MENU_PAGE_SIZE]
        options = []

        for row in page_rows:
            club_name = row.get("name")
            if club_name:
                options.append(discord.SelectOption(
                    label=str(club_name)[:100],
                    value=str(club_name)[:100],
                    description=f"{self.league}"
                ))

        if not options:
            options.append(discord.SelectOption(
                label="Nessuna squadra libera",
                value="__none__",
                description="Tutte le squadre sono occupate"
            ))

        super().__init__(
            placeholder=f"Scegli la squadra libera... pagina {self.page + 1}",
            min_values=1,
            max_values=1,
            options=options
        )

    async def callback(self, interaction: discord.Interaction):
        try:
            await safe_defer(interaction, ephemeral=True, thinking=True)
        except Exception:
            pass

        if not can_manage_signup(interaction.user):
            await interaction.followup.send("❌ Non hai i permessi.", ephemeral=True)
            return

        club_name = self.values[0]
        if club_name == "__none__":
            await interaction.followup.send("❌ Nessuna squadra libera in questo campionato.", ephemeral=True)
            return

        conn = connect()
        cur = conn.cursor()

        cur.execute("""
            SELECT *
            FROM signup_requests
            WHERE id = %s
            LIMIT 1
        """, (self.request_id,))
        req = cur.fetchone()

        if not req:
            conn.close()
            await interaction.followup.send("❌ Richiesta non trovata.", ephemeral=True)
            return

        if str(req.get("status", "pending")) not in {"pending", "choosing_club"}:
            conn.close()
            await interaction.followup.send("⚠️ Questa richiesta è già stata gestita.", ephemeral=True)
            return

        discord_id = str(req.get("discord_id"))

        cur.execute("""
            SELECT assigned_to
            FROM fc26_clubs
            WHERE LOWER(name) = LOWER(%s)
            LIMIT 1
        """, (club_name,))
        club_row = cur.fetchone()

        if not club_row:
            conn.close()
            await interaction.followup.send("❌ Club non trovato.", ephemeral=True)
            return

        if club_row.get("assigned_to"):
            conn.close()
            await interaction.followup.send("❌ Questa squadra è già stata assegnata.", ephemeral=True)
            return

        cur.execute("""
            UPDATE signup_requests
            SET status = 'accepted',
                club_name = %s,
                handled_by = %s,
                handled_at = CURRENT_TIMESTAMP
            WHERE id = %s
        """, (club_name, str(interaction.user.id), self.request_id))

        cur.execute("""
            UPDATE fc26_clubs
            SET assigned_to = %s,
                assigned_at = CURRENT_TIMESTAMP
            WHERE LOWER(name) = LOWER(%s)
        """, (discord_id, club_name))

        conn.commit()
        conn.close()

        players_count, avg_ovr, budget, real_team_name = sync_real_team_roster_to_manager(discord_id, club_name)

        if players_count <= 0:
            await interaction.followup.send(
                f"❌ Nessun giocatore trovato per **{club_name}** nella tabella `players.team`.",
                ephemeral=True
            )
            return

        try:
            member = await get_member_safe(interaction.guild, discord_id)
            if member:
                await apply_signup_role_accepted(
                    interaction.guild,
                    member,
                    reason="Iscrizione FC26 accettata con club"
                )
        except Exception as e:
            print(f"[SIGNUP CLUB SELECT] Errore ruoli accepted: {e}")

        embed = discord.Embed(
            title="✅ Iscrizione accettata",
            description=f"<@{discord_id}> è stato registrato ufficialmente.",
            color=discord.Color.green()
        )
        embed.add_field(name="Club", value=club_name, inline=True)
        embed.add_field(name="Campionato", value=self.league, inline=True)
        embed.add_field(name="Budget", value=f"{budget} crediti", inline=True)
        embed.add_field(name="Giocatori assegnati", value=str(players_count), inline=True)
        embed.add_field(name="OVR medio", value=f"{avg_ovr:.1f}" if avg_ovr else "N/D", inline=True)
        embed.add_field(name="Gestito da", value=interaction.user.mention, inline=False)

        try:
            await interaction.message.edit(embed=embed, view=None)
        except Exception:
            pass

        await close_original_signup_staff_message(interaction.guild, req, embed)
        await send_signup_result_channel(
            interaction.guild,
            "accepted",
            self.request_id,
            discord_id,
            req=req,
            club_name=club_name,
            league=self.league,
            budget=budget,
            players_count=players_count,
            avg_ovr=avg_ovr,
            staff_user=interaction.user
        )

        await safe_dm_signup_result(
            discord_id,
            "✅ Iscrizione accettata",
            (
                f"La tua iscrizione a **FC26** è stata accettata.\n\n"
                f"Club assegnato: **{club_name}**\n"
                f"Campionato: **{self.league}**\n"
                f"Budget: **{budget} crediti**\n"
                f"Giocatori assegnati: **{players_count}**"
            ),
            discord.Color.green()
        )

        # Nessuna seconda comunicazione nel log: resta solo il messaggio staff aggiornato.

        await interaction.followup.send(
            f"✅ Iscrizione accettata e squadra **{club_name}** assegnata.",
            ephemeral=True
        )


class SignupClubSelectView(discord.ui.View):
    def __init__(self, request_id: int, league: str, page=0):
        super().__init__(timeout=180)
        self.request_id = int(request_id)
        self.league = str(league)
        self.page = int(page or 0)

        conn = connect()
        cur = conn.cursor()
        try:
            cur.execute("""
                SELECT name
                FROM fc26_clubs
                WHERE league = %s
                  AND (assigned_to IS NULL OR assigned_to = '')
                ORDER BY name ASC
            """, (self.league,))
            self.rows = cur.fetchall()
        except Exception as e:
            print(f"[SIGNUP CLUB SELECT] Errore caricamento club: {e}")
            self.rows = []
        conn.close()

        self.max_page = max(0, (len(self.rows) - 1) // SIGNUP_MENU_PAGE_SIZE)
        self.page = max(0, min(self.page, self.max_page))
        self.add_item(SignupClubSelect(self.request_id, self.league, self.rows, self.page))

    @discord.ui.button(label="⬅️ Indietro", style=discord.ButtonStyle.secondary)
    async def previous_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(
            content=f"Campionato selezionato: **{self.league}**\nOra scegli la squadra libera:\nPagina **{max(0, self.page - 1) + 1}/{self.max_page + 1}**",
            view=SignupClubSelectView(self.request_id, self.league, max(0, self.page - 1))
        )

    @discord.ui.button(label="Avanti ➡️", style=discord.ButtonStyle.primary)
    async def next_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(
            content=f"Campionato selezionato: **{self.league}**\nOra scegli la squadra libera:\nPagina **{min(self.max_page, self.page + 1) + 1}/{self.max_page + 1}**",
            view=SignupClubSelectView(self.request_id, self.league, min(self.max_page, self.page + 1))
        )


class SignupStaffView(discord.ui.View):
    def __init__(self, request_id: int):
        super().__init__(timeout=None)
        self.request_id = int(request_id)

    async def _get_request(self):
        conn = connect()
        cur = conn.cursor()
        cur.execute("""
            SELECT *
            FROM signup_requests
            WHERE id = %s
            LIMIT 1
        """, (self.request_id,))
        row = cur.fetchone()
        conn.close()
        return row

    async def _set_status(self, status: str, interaction: discord.Interaction):
        conn = connect()
        cur = conn.cursor()

        for sql in [
            "ALTER TABLE signup_requests ADD COLUMN IF NOT EXISTS handled_by TEXT",
            "ALTER TABLE signup_requests ADD COLUMN IF NOT EXISTS handled_at TIMESTAMP",
            "ALTER TABLE signup_requests ADD COLUMN IF NOT EXISTS club_name TEXT",
            "ALTER TABLE signup_requests ADD COLUMN IF NOT EXISTS status TEXT DEFAULT 'pending'",
            "ALTER TABLE fc26_clubs ADD COLUMN IF NOT EXISTS assigned_at TIMESTAMP"
        ]:
            try:
                cur.execute(sql)
            except Exception:
                pass

        cur.execute("""
            UPDATE signup_requests
            SET status = %s,
                handled_by = %s,
                handled_at = CURRENT_TIMESTAMP
            WHERE id = %s
        """, (status, str(interaction.user.id), self.request_id))

        conn.commit()
        conn.close()

    @discord.ui.button(label="Accetta", style=discord.ButtonStyle.success, custom_id="signup_staff_accept")
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            await safe_defer(interaction, ephemeral=True, thinking=True)
        except Exception:
            pass

        if not can_manage_signup(interaction.user):
            await interaction.followup.send("❌ Non hai i permessi per gestire questa iscrizione.", ephemeral=True)
            return

        req = await self._get_request()
        if not req:
            await interaction.followup.send("❌ Richiesta non trovata.", ephemeral=True)
            return

        if str(req.get("status", "pending")) != "pending":
            await interaction.followup.send("⚠️ Questa richiesta è già stata gestita.", ephemeral=True)
            return

        mode = get_league_mode()

        if mode == "squadre_reali":
            # Non accetta subito: apre scelta campionato/squadra libera.
            conn = connect()
            cur = conn.cursor()
            try:
                cur.execute("""
                    UPDATE signup_requests
                    SET status = 'choosing_club',
                        handled_by = %s
                    WHERE id = %s
                """, (str(interaction.user.id), self.request_id))
                conn.commit()
            except Exception as e:
                print(f"[SIGNUP STAFF] Errore status choosing_club: {e}")
            conn.close()

            await interaction.followup.send(
                "Modalità **Squadre reali** attiva.\nScegli prima il campionato:",
                view=SignupLeagueSelectView(self.request_id, page=0),
                ephemeral=True
            )
            return

        # Modalità fantacalcio / altre: accetta senza squadra reale.
        await self._set_status("accepted", interaction)

        discord_id = str(req.get("discord_id"))
        member = None
        try:
            member = await get_member_safe(interaction.guild, discord_id)
        except Exception:
            member = None

        if member:
            try:
                await apply_signup_role_accepted(
                    interaction.guild,
                    member,
                    reason="Iscrizione FC26 accettata"
                )
            except Exception as e:
                print(f"[SIGNUP STAFF] Errore ruoli accetta: {e}")

        embed = discord.Embed(
            title="✅ Iscrizione accettata",
            description=f"Richiesta **#{self.request_id}** accettata da {interaction.user.mention}.",
            color=discord.Color.green()
        )
        embed.add_field(name="Player", value=f"<@{discord_id}>", inline=False)
        embed.add_field(name="Nome", value=str(req.get("real_name") or "-"), inline=True)
        embed.add_field(name="Piattaforma", value=str(req.get("platform") or "-"), inline=True)
        embed.add_field(name="ID PSN/Xbox/EA", value=str(req.get("game_id") or req.get("ea_id") or "-"), inline=False)

        try:
            await interaction.message.edit(embed=embed, view=None)
        except Exception:
            pass

        await send_signup_result_channel(
            interaction.guild,
            "accepted",
            self.request_id,
            discord_id,
            req=req,
            staff_user=interaction.user
        )

        await safe_dm_signup_result(
            discord_id,
            "✅ Iscrizione accettata",
            "La tua iscrizione a **FC26** è stata accettata dallo staff.",
            discord.Color.green()
        )

        await interaction.followup.send("✅ Richiesta accettata.", ephemeral=True)

    @discord.ui.button(label="Rifiuta", style=discord.ButtonStyle.danger, custom_id="signup_staff_reject")
    async def reject(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            await safe_defer(interaction, ephemeral=True, thinking=True)
        except Exception:
            pass

        if not can_manage_signup(interaction.user):
            await interaction.followup.send("❌ Non hai i permessi per gestire questa iscrizione.", ephemeral=True)
            return

        req = await self._get_request()
        if not req:
            await interaction.followup.send("❌ Richiesta non trovata.", ephemeral=True)
            return

        if str(req.get("status", "pending")) not in {"pending", "choosing_club"}:
            await interaction.followup.send("⚠️ Questa richiesta è già stata gestita.", ephemeral=True)
            return

        await self._set_status("rejected", interaction)

        discord_id = str(req.get("discord_id"))

        try:
            member = await get_member_safe(interaction.guild, discord_id)
            if member:
                await apply_signup_role_rejected(
                    interaction.guild,
                    member,
                    reason="Iscrizione FC26 rifiutata"
                )
        except Exception as e:
            print(f"[SIGNUP STAFF] Errore ruoli rifiuta: {e}")

        embed = discord.Embed(
            title="❌ Iscrizione rifiutata",
            description=f"Richiesta **#{self.request_id}** rifiutata da {interaction.user.mention}.",
            color=discord.Color.red()
        )
        embed.add_field(name="Player", value=f"<@{discord_id}>", inline=False)
        embed.add_field(name="Nome", value=str(req.get("real_name") or "-"), inline=True)
        embed.add_field(name="Piattaforma", value=str(req.get("platform") or "-"), inline=True)
        embed.add_field(name="ID PSN/Xbox/EA", value=str(req.get("game_id") or req.get("ea_id") or "-"), inline=False)

        try:
            await interaction.message.edit(embed=embed, view=None)
        except Exception:
            pass

        await send_signup_result_channel(
            interaction.guild,
            "rejected",
            self.request_id,
            discord_id,
            req=req,
            staff_user=interaction.user
        )

        await safe_dm_signup_result(
            discord_id,
            "❌ Iscrizione rifiutata",
            "La tua iscrizione a **FC26** è stata rifiutata dallo staff.",
            discord.Color.red()
        )

        await interaction.followup.send("❌ Richiesta rifiutata.", ephemeral=True)


class StaffDecisionSelect(discord.ui.Select):
    def __init__(self, request_id):
        self.request_id = int(request_id)
        options = [
            discord.SelectOption(label="ACCETTA", value="accept", emoji="✅", description="Accetta e scegli il club"),
            discord.SelectOption(label="RIFIUTA", value="reject", emoji="❌", description="Rifiuta la richiesta")
        ]
        super().__init__(placeholder="Scegli esito richiesta...", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        if not can_use_normal_staff(interaction.user):
            await interaction.response.send_message("❌ Solo lo staff può gestire le richieste.", ephemeral=True)
            return

        request = get_signup_request(self.request_id)
        if not request:
            await interaction.response.send_message("Richiesta non trovata.", ephemeral=True)
            return
        if request["status"] != "pending":
            await interaction.response.send_message("Questa richiesta è già stata gestita.", ephemeral=True)
            return

        if self.values[0] == "reject":
            guild = interaction.guild
            member = await get_member_safe(guild, request["discord_id"]) if "get_member_safe" in globals() else None

            conn = connect()
            cur = conn.cursor()
            cur.execute("""
                UPDATE signup_requests
                SET status = 'rejected', handled_by = %s, handled_at = CURRENT_TIMESTAMP
                WHERE id = %s
            """, (str(interaction.user.id), self.request_id))
            conn.commit()
            conn.close()

            if member:
                pending_role = guild.get_role(int(SIGNUP_PENDING_ROLE_ID))
                if pending_role:
                    try:
                        await member.remove_roles(pending_role, reason="Richiesta FC26 rifiutata")
                    except Exception:
                        pass
                try:
                    await member.send("❌ **Richiesta rifiutata**\n\nLa tua richiesta per il torneo **FC 26** è stata rifiutata dallo staff.")
                except Exception:
                    pass

            reject_channel = guild.get_channel(1505229160057143366)
            if reject_channel:
                await reject_channel.send(f"❌ **Richiesta rifiutata**\n\n👤 Player: <@{request['discord_id']}>")

            embed = discord.Embed(
                title="❌ Richiesta rifiutata",
                description=f"Player: <@{request['discord_id']}>\nGestita da: {interaction.user.mention}",
                color=discord.Color.red()
            )
            await interaction.response.edit_message(embed=embed, view=None)
            return

        leagues = get_free_signup_leagues()
        embed = discord.Embed(
            title="✅ Richiesta accettata: scegli campionato",
            description=(
                f"Player: <@{request['discord_id']}>\n"
                "Prima scegli il campionato, poi il bot mostrerà solo i club liberi."
            ),
            color=discord.Color.green()
        )
        await interaction.response.edit_message(embed=embed, view=LeagueAssignView(self.request_id, leagues))


class StaffDecisionView(discord.ui.View):
    def __init__(self, request_id):
        super().__init__(timeout=None)
        self.add_item(StaffDecisionSelect(request_id))


class LeagueAssignSelect(discord.ui.Select):
    def __init__(self, request_id, leagues):
        self.request_id = int(request_id)
        options = []
        for league_name, free_count in leagues[:25]:
            options.append(discord.SelectOption(
                label=str(league_name)[:100],
                value=str(league_name),
                description=f"Club liberi: {free_count}"
            ))
        super().__init__(placeholder="Scegli il campionato...", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        if not can_use_normal_staff(interaction.user):
            await interaction.response.send_message("❌ Solo lo staff può assegnare il club.", ephemeral=True)
            return

        league = self.values[0]
        clubs = get_free_signup_clubs(league)
        if not clubs:
            await interaction.response.send_message("❌ Non ci sono club liberi in questo campionato.", ephemeral=True)
            return

        request = get_signup_request(self.request_id)
        embed = discord.Embed(
            title="🏟️ Scegli club libero",
            description=f"Player: <@{request['discord_id']}>\nCampionato scelto: **{league}**",
            color=discord.Color.blue()
        )
        if len(clubs) > 25:
            embed.set_footer(text="Discord mostra massimo 25 club per menu. Per gli altri usa /assegna_club.")

        await interaction.response.edit_message(embed=embed, view=ClubAssignView(self.request_id, clubs, league))


class LeagueAssignView(discord.ui.View):
    def __init__(self, request_id, leagues):
        super().__init__(timeout=180)
        self.add_item(LeagueAssignSelect(request_id, leagues))


class ClubAssignSelect(discord.ui.Select):
    def __init__(self, request_id, clubs, league=None):
        self.request_id = int(request_id)
        options = [discord.SelectOption(label=club[:100], value=club) for club in clubs[:25]]
        super().__init__(placeholder="Scegli il club libero da assegnare...", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        if not can_use_normal_staff(interaction.user):
            await interaction.response.send_message("❌ Solo lo staff può assegnare il club.", ephemeral=True)
            return
        await complete_signup_accept(interaction, self.request_id, self.values[0])


class ClubAssignView(discord.ui.View):
    def __init__(self, request_id, clubs, league=None):
        super().__init__(timeout=180)
        self.add_item(ClubAssignSelect(request_id, clubs, league))

# ========================================================================

class SignupStartView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Richiedi iscrizione",
        style=discord.ButtonStyle.green,
        custom_id="fc26_signup_start"
    )
    async def signup_start(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            # IMPORTANTISSIMO:
            # non fare defer() prima di send_modal()
            # altrimenti Discord dice:
            # "Interaction has already been acknowledged"
            await interaction.response.send_modal(SignupModal())

        except Exception as e:
            print(f"[SIGNUP BUTTON] Errore apertura modal: {e}")

            try:
                if interaction.response.is_done():
                    await interaction.followup.send(
                        f"❌ Errore apertura modulo: `{e}`",
                        ephemeral=True
                    )
                else:
                    await interaction.response.send_message(
                        f"❌ Errore apertura modulo: `{e}`",
                        ephemeral=True
                    )
            except Exception:
                pass




# ===========================================================
# BORDO CAMPO - SYNC DATABASE UNICO FC26
# Usa Supabase come unica fonte dati:
# - players_fc26 = dataset completo importato dal sito
# - players = tabella operativa usata dal bot aste/rose/mercato
# - fc26_clubs = club generati dal dataset completo
# ===========================================================

def sync_fc26_dataset_to_bot_tables():
    """
    Sincronizza il dataset completo FC26 importato in Supabase.

    Il bot storicamente usa la tabella `players` per aste, rose e mercato.
    Il sito importa il CSV completo in `players_fc26`.

    Questa funzione copia/aggiorna i dati principali da `players_fc26` a `players`
    senza cancellare owner_discord_id e sold_price già presenti.
    Poi aggiorna `fc26_clubs` con tutti i club reali presenti nel dataset.
    """
    conn = connect()
    cur = conn.cursor()

    try:
        cur.execute("SELECT to_regclass('public.players_fc26') AS table_name")
        row = cur.fetchone()
        if not row or not row.get("table_name"):
            print("[FC26 SYNC] Tabella players_fc26 non trovata. Importa prima il CSV sul sito.")
            conn.close()
            return

        cur.execute("""
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = 'players_fc26'
        """)
        available_columns = {str(r["column_name"]) for r in cur.fetchall()}

        def pick(*names):
            for name in names:
                if name in available_columns:
                    return name
            return None

        column_map = {
            "id": pick("id", "ID"),
            "name": pick("name", "Name"),
            "team": pick("team", "Team", "club", "Club"),
            "league": pick("league", "League"),
            "position": pick("position", "Position"),
            "overall": pick("overall", "OVR", "ovr"),
            "pace": pick("pace", "pac", "PAC"),
            "shooting": pick("shooting", "sho", "SHO"),
            "passing": pick("passing", "pas", "PAS"),
            "dribbling": pick("dribbling", "dri", "DRI"),
            "defending": pick("defending", "def", "DEF"),
            "physical": pick("physical", "phy", "PHY"),
            "nation": pick("nation", "Nation"),
            "age": pick("age", "Age"),
            "weak_foot": pick("weak_foot", "Weak foot", "weak foot"),
            "skill_moves": pick("skill_moves", "Skill moves", "skill moves"),
        }

        required = ["id", "name"]
        missing_required = [key for key in required if not column_map.get(key)]
        if missing_required:
            print(f"[FC26 SYNC] Colonne obbligatorie mancanti in players_fc26: {missing_required}")
            conn.close()
            return

        cur.execute("""
            CREATE TABLE IF NOT EXISTS players (
                id BIGINT PRIMARY KEY,
                name TEXT,
                team TEXT,
                league TEXT,
                position TEXT,
                overall INTEGER,
                pace INTEGER,
                shooting INTEGER,
                passing INTEGER,
                dribbling INTEGER,
                defending INTEGER,
                physical INTEGER,
                nation TEXT,
                age INTEGER,
                weak_foot INTEGER,
                skill_moves INTEGER,
                owner_discord_id TEXT,
                sold_price INTEGER
            )
        """)

        for sql in [
            "ALTER TABLE players ADD COLUMN IF NOT EXISTS name TEXT",
            "ALTER TABLE players ADD COLUMN IF NOT EXISTS team TEXT",
            "ALTER TABLE players ADD COLUMN IF NOT EXISTS league TEXT",
            "ALTER TABLE players ADD COLUMN IF NOT EXISTS position TEXT",
            "ALTER TABLE players ADD COLUMN IF NOT EXISTS overall INTEGER",
            "ALTER TABLE players ADD COLUMN IF NOT EXISTS pace INTEGER",
            "ALTER TABLE players ADD COLUMN IF NOT EXISTS shooting INTEGER",
            "ALTER TABLE players ADD COLUMN IF NOT EXISTS passing INTEGER",
            "ALTER TABLE players ADD COLUMN IF NOT EXISTS dribbling INTEGER",
            "ALTER TABLE players ADD COLUMN IF NOT EXISTS defending INTEGER",
            "ALTER TABLE players ADD COLUMN IF NOT EXISTS physical INTEGER",
            "ALTER TABLE players ADD COLUMN IF NOT EXISTS nation TEXT",
            "ALTER TABLE players ADD COLUMN IF NOT EXISTS age INTEGER",
            "ALTER TABLE players ADD COLUMN IF NOT EXISTS weak_foot INTEGER",
            "ALTER TABLE players ADD COLUMN IF NOT EXISTS skill_moves INTEGER",
            "ALTER TABLE players ADD COLUMN IF NOT EXISTS owner_discord_id TEXT",
            "ALTER TABLE players ADD COLUMN IF NOT EXISTS sold_price INTEGER",
            "ALTER TABLE fc26_clubs ADD COLUMN IF NOT EXISTS league TEXT",
            "ALTER TABLE fc26_clubs ADD COLUMN IF NOT EXISTS assigned_to TEXT",
            "ALTER TABLE fc26_clubs ADD COLUMN IF NOT EXISTS assigned_at TIMESTAMP",
            "ALTER TABLE fc26_clubs ADD COLUMN IF NOT EXISTS previous_owner_id TEXT",
            "ALTER TABLE fc26_clubs ADD COLUMN IF NOT EXISTS previous_owner_name TEXT",
        ]:
            try:
                cur.execute(sql)
            except Exception:
                pass

        def expr(key, default="NULL"):
            col = column_map.get(key)
            if not col:
                return default
            return f'"{col}"'

        # Copia/aggiorna tutti i giocatori del dataset completo nella tabella operativa del bot.
        # Non sovrascrive owner_discord_id e sold_price, così le rose già assegnate restano valide.
        cur.execute(f"""
            INSERT INTO players (
                id, name, team, league, position, overall,
                pace, shooting, passing, dribbling, defending, physical,
                nation, age, weak_foot, skill_moves
            )
            SELECT
                NULLIF(TRIM({expr('id')}::text), '')::bigint AS id,
                {expr('name')}::text AS name,
                {expr('team')}::text AS team,
                {expr('league')}::text AS league,
                {expr('position')}::text AS position,
                NULLIF({expr('overall', "NULL")}::text, '')::integer AS overall,
                NULLIF({expr('pace', "NULL")}::text, '')::integer AS pace,
                NULLIF({expr('shooting', "NULL")}::text, '')::integer AS shooting,
                NULLIF({expr('passing', "NULL")}::text, '')::integer AS passing,
                NULLIF({expr('dribbling', "NULL")}::text, '')::integer AS dribbling,
                NULLIF({expr('defending', "NULL")}::text, '')::integer AS defending,
                NULLIF({expr('physical', "NULL")}::text, '')::integer AS physical,
                {expr('nation')}::text AS nation,
                NULLIF({expr('age', "NULL")}::text, '')::integer AS age,
                NULLIF({expr('weak_foot', "NULL")}::text, '')::integer AS weak_foot,
                NULLIF({expr('skill_moves', "NULL")}::text, '')::integer AS skill_moves
            FROM players_fc26
            WHERE {expr('id')} IS NOT NULL
              AND TRIM({expr('id')}::text) <> ''
            ON CONFLICT (id) DO UPDATE SET
                name = EXCLUDED.name,
                team = EXCLUDED.team,
                league = EXCLUDED.league,
                position = EXCLUDED.position,
                overall = EXCLUDED.overall,
                pace = EXCLUDED.pace,
                shooting = EXCLUDED.shooting,
                passing = EXCLUDED.passing,
                dribbling = EXCLUDED.dribbling,
                defending = EXCLUDED.defending,
                physical = EXCLUDED.physical,
                nation = EXCLUDED.nation,
                age = EXCLUDED.age,
                weak_foot = EXCLUDED.weak_foot,
                skill_moves = EXCLUDED.skill_moves
        """)

        team_col = column_map.get("team")
        league_col = column_map.get("league")
        if team_col:
            if league_col:
                cur.execute(f"""
                    INSERT INTO fc26_clubs (name, league)
                    SELECT
                        "{team_col}"::text AS name,
                        MIN("{league_col}"::text) AS league
                    FROM players_fc26
                    WHERE "{team_col}" IS NOT NULL
                      AND TRIM("{team_col}"::text) <> ''
                    GROUP BY "{team_col}"
                    ON CONFLICT (name) DO UPDATE SET
                        league = COALESCE(EXCLUDED.league, fc26_clubs.league)
                """)
            else:
                cur.execute(f"""
                    INSERT INTO fc26_clubs (name)
                    SELECT DISTINCT "{team_col}"::text AS name
                    FROM players_fc26
                    WHERE "{team_col}" IS NOT NULL
                      AND TRIM("{team_col}"::text) <> ''
                    ON CONFLICT (name) DO NOTHING
                """)

        conn.commit()

        cur.execute("SELECT COUNT(*) AS total FROM players")
        total_players = cur.fetchone()["total"]
        cur.execute("SELECT COUNT(*) AS total FROM fc26_clubs")
        total_clubs = cur.fetchone()["total"]
        print(f"[FC26 SYNC] Sincronizzazione completata: {total_players} giocatori, {total_clubs} club.")

    except Exception as e:
        conn.rollback()
        print(f"[FC26 SYNC] Errore sincronizzazione dataset FC26: {e}")
    finally:
        try:
            conn.close()
        except Exception:
            pass

# ===========================================================


async def sync_pending_signup_roles_loop():
    """Assegna automaticamente PRE ISCRITTO anche alle richieste create dal sito."""
    await bot.wait_until_ready()

    while not bot.is_closed():
        try:
            guild = bot.get_guild(int(GUILD_ID))

            if guild:
                conn = connect()
                cur = conn.cursor()
                cur.execute("""
                    SELECT id, discord_id
                    FROM signup_requests
                    WHERE status = 'pending'
                      AND discord_id IS NOT NULL
                    ORDER BY id DESC
                    LIMIT 50
                """)
                rows = cur.fetchall()
                conn.close()

                for row in rows:
                    discord_id = str(row.get("discord_id"))
                    member = await get_member_safe(guild, discord_id)

                    if member:
                        await apply_signup_role_pending(
                            guild,
                            member,
                            reason="Richiesta iscrizione FC26 da sito/Discord"
                        )

        except Exception as e:
            print(f"[SIGNUP ROLES] Errore sync pending: {e}")

        await asyncio.sleep(15)


@tree.error
async def on_app_command_error(interaction: discord.Interaction, error):
    """Logga e risponde agli errori degli slash command senza far scadere l'interazione."""
    original_error = getattr(error, "original", error)
    print(f"[SLASH ERROR] Comando={getattr(interaction.command, 'name', 'sconosciuto')} Errore={type(original_error).__name__}: {original_error!r}")

    message = f"❌ Errore comando: `{type(original_error).__name__}`"
    try:
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)
    except discord.NotFound as e:
        print(f"[SLASH ERROR RESPONSE NOTFOUND] Interazione scaduta: {e}")
    except Exception as e:
        print(f"[SLASH ERROR RESPONSE FAILED] {type(e).__name__}: {e}")


@bot.event
async def on_ready():
    """Startup Railway/Supabase safe.

    - non esegue migrazioni/ALTER TABLE all'avvio;
    - non avvia loop website-sync/inattività che causano deadlock;
    - registra le view necessarie;
    - sincronizza tutti i comandi slash nel server configurato.
    """
    print("[BOOT SAFE] on_ready avviato - sync comandi prima dei task")

    try:
        bot.add_view(SignupStartView())
    except Exception as e:
        print(f"[ON_READY] SignupStartView non registrata: {e}")

    try:
        bot.add_view(AuctionView())
    except Exception as e:
        print(f"[ON_READY] AuctionView non registrata: {e}")

    try:
        local_commands = tree.get_commands(guild=None)
        print(f"[SYNC DEBUG] Comandi registrati localmente prima del sync: {len(local_commands)}")
        print("[SYNC DEBUG] Nomi comandi:", ", ".join(cmd.name for cmd in local_commands[:120]))

        guild = get_guild()
        if guild:
            tree.copy_global_to(guild=guild)
            synced = await tree.sync(guild=guild)
            print(f"[SYNC OK] Comandi sincronizzati nel server {GUILD_ID}: {len(synced)}")
            print("[SYNC DEBUG] Comandi syncati:", ", ".join(cmd.name for cmd in synced[:120]))
        else:
            synced = await tree.sync()
            print(f"[SYNC OK] Comandi globali sincronizzati: {len(synced)}")
    except Exception as e:
        print(f"[SYNC ERROR] Errore sincronizzazione comandi: {type(e).__name__}: {e}")

    print("[SUPABASE] Migrazioni automatiche disattivate: uso solo tabelle esistenti.")
    print("[ON_READY] sync_pending_signup_roles_loop disattivato per evitare deadlock Supabase.")
    print("[ON_READY] register_pending_signup_views disattivato: viste persistenti non necessarie dopo il sync comandi.")
    print("[ON_READY] process_website_signup_actions_loop disattivato: evita errori e doppie notifiche.")
    print("[ON_READY] process_website_signup_requests_loop disattivato: evita errori e doppie notifiche.")
    print("[ON_READY] check_player_inactivity disattivato: usa /controllo_inattivi manualmente.")

    try:
        await asyncio.to_thread(reset_auction_state)
    except Exception as e:
        print(f"[ON_READY] reset_auction_state non bloccante fallito: {e}")

    print("[PATCH] Timer asta grafico attivo + finale asta inviato nel canale ASTA")
    print("[PATCH] Fix definitivo iscrizioni squadre reali: players.id = CAST(%s AS BIGINT)")
    print("[PATCH] Fix globale players.id BIGINT per aste, iscrizioni, mercato e scambi")
    print(f"Bot online come {bot.user}")


class SquadraRealeModal(discord.ui.Modal, title="Assegna squadra reale"):
    squadra = discord.ui.TextInput(
        label="Nome squadra da assegnare",
        placeholder="Esempio: Milan, Inter, Juventus...",
        required=True,
        max_length=80
    )

    def __init__(self, member_id: int, member_name: str):
        super().__init__()
        self.member_id = member_id
        self.member_name = member_name

    async def on_submit(self, interaction: discord.Interaction):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ Solo lo staff può completare questa registrazione.", ephemeral=True)
            return

        await safe_defer(interaction, ephemeral=True, thinking=True)

        guild = interaction.guild
        member = await get_member_safe(guild, self.member_id)

        if not member:
            await interaction.followup.send("Utente non trovato nel server.", ephemeral=True)
            return

        players, avg_ovr, budget = get_team_stats_reale(str(self.squadra.value), include_owned_by=str(member.id))

        if not players:
            await interaction.followup.send("Squadra non trovata o senza giocatori liberi disponibili.", ephemeral=True)
            return

        conn = connect()
        cur = conn.cursor()

        cur.execute(
            "INSERT INTO managers (discord_id, name, budget) VALUES (%s, %s, %s)",
            (str(member.id), member.display_name, budget)
        )

        # Se aveva già giocatori, li svincola prima.
        cur.execute(
            "UPDATE players SET owner_discord_id = NULL, sold_price = NULL WHERE owner_discord_id = %s",
            (str(member.id),)
        )

        for p in players:
            cur.execute(
                "UPDATE players SET owner_discord_id = %s, sold_price = %s WHERE id = CAST(%s AS BIGINT)",
                (str(member.id), 0, str(p["id"]))
            )

        cur.execute(
            "UPDATE managers SET budget = %s, name = %s WHERE discord_id = %s",
            (budget, member.display_name, str(member.id))
        )

        cur.execute("""
            INSERT INTO real_team_assignments (discord_id, manager_name, team_name, avg_overall, assigned_budget) VALUES (%s, %s, %s, %s, %s) ON CONFLICT (discord_id) DO UPDATE SET manager_name = EXCLUDED.manager_name, team_name = EXCLUDED.team_name, avg_overall = EXCLUDED.avg_overall, assigned_budget = EXCLUDED.assigned_budget
        """, (str(member.id), member.display_name, players[0]["team"], avg_ovr, budget))

        conn.commit()
        conn.close()

        embed = discord.Embed(
            title="✅ Registrazione completata",
            description=f"**{member.display_name}** registrato in modalità **Squadre Reali**.",
            color=discord.Color.green()
        )
        embed.add_field(name="Squadra", value=players[0]["team"], inline=True)
        embed.add_field(name="Giocatori assegnati", value=str(len(players)), inline=True)
        embed.add_field(name="OVR medio", value=f"{avg_ovr:.1f}", inline=True)
        embed.add_field(name="Budget mercato", value=f"{budget} crediti", inline=True)

        await interaction.followup.send(embed=embed, ephemeral=True)


class RegistraPreIscrittoSelect(discord.ui.Select):
    def __init__(self, members):
        options = []

        for member in members[:25]:
            options.append(
                discord.SelectOption(
                    label=member.display_name[:100],
                    value=str(member.id),
                    description=f"ID: {member.id}"
                )
            )

        super().__init__(
            placeholder="Scegli un player PRE-ISCRITTO...",
            min_values=1,
            max_values=1,
            options=options,
            custom_id="registra_pre_iscritto_select"
        )

    async def callback(self, interaction: discord.Interaction):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ Solo lo staff può registrare i player.", ephemeral=True)
            return

        member_id = int(self.values[0])
        member = await get_member_safe(interaction.guild, member_id)

        if not member:
            await interaction.response.send_message("Utente non trovato nel server.", ephemeral=True)
            return

        mode = get_league_mode()

        if mode == "squadre_reali":
            await interaction.response.send_modal(SquadraRealeModal(member.id, member.display_name))
            return

        conn = connect()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO managers (discord_id, name, budget) VALUES (%s, %s, %s)",
            (str(member.id), member.display_name, DEFAULT_BUDGET)
        )
        cur.execute(
            "UPDATE managers SET name = %s, budget = %s WHERE discord_id = %s",
            (member.display_name, DEFAULT_BUDGET, str(member.id))
        )
        conn.commit()
        conn.close()

        embed = discord.Embed(
            title="✅ Registrazione completata",
            description=f"**{member.display_name}** registrato in modalità **Fantacalcio**.",
            color=discord.Color.green()
        )
        embed.add_field(name="Budget iniziale", value=f"{DEFAULT_BUDGET} crediti", inline=True)

        await interaction.response.edit_message(embed=embed, view=None)


class RegistraPreIscrittoView(discord.ui.View):
    def __init__(self, members):
        super().__init__(timeout=180)
        self.add_item(RegistraPreIscrittoSelect(members))



def can_bypass_bot_only(member):
    return any(str(role.id) in BOT_ONLY_BYPASS_ROLE_IDS for role in getattr(member, "roles", []))


@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return

    try:
        update_player_activity(message.author.id, "discord")
    except Exception:
        pass


    # Gli admin/staff con questi ruoli possono scrivere liberamente nei canali bot-only.
    if can_bypass_bot_only(message.author):
        await bot.process_commands(message)
        return

    if message.channel.id in BOT_ONLY_CHANNELS:

        # Permette i comandi slash Discord
        if not message.content.startswith("/"):

            try:
                await message.delete()

                warning = await message.channel.send(
                    f"{message.author.mention} ⚠️ Su questo canale puoi usare solo i comandi `/` del bot."
                )

                await asyncio.sleep(5)
                await warning.delete()

            except discord.Forbidden:
                pass

            except discord.NotFound:
                pass

            except Exception:
                pass

    await bot.process_commands(message)


@tree.command(name="registra", description="Staff: registra un player pre-iscritto")
async def registra(interaction: discord.Interaction):
    if not is_admin(interaction):
        await interaction.response.send_message("❌ Solo lo staff può usare questo comando.", ephemeral=True)
        return

    role = interaction.guild.get_role(int(PRE_ISCRITTO_ROLE_ID)) if interaction.guild else None

    if not role:
        await interaction.response.send_message("Ruolo PRE-ISCRITTO non trovato.", ephemeral=True)
        return

    members = [m for m in role.members if not m.bot]

    if not members:
        await interaction.response.send_message("Non ci sono player con il ruolo PRE-ISCRITTO.", ephemeral=True)
        return

    mode = get_league_mode()
    mode_label = "Fantacalcio" if mode == "fantacalcio" else "Squadre Reali"

    embed = discord.Embed(
        title="📝 Registrazione player",
        description=f"Modalità attuale: **{mode_label}**\\nScegli dalla tendina un player con ruolo **PRE-ISCRITTO**.",
        color=discord.Color.blue()
    )

    if mode == "fantacalcio":
        embed.add_field(
            name="Effetto",
            value=f"Il player verrà registrato con **{DEFAULT_BUDGET} crediti**.",
            inline=False
        )
    else:
        embed.add_field(
            name="Effetto",
            value="Dopo la selezione si aprirà una finestra dove inserire la squadra reale da assegnare.",
            inline=False
        )

    if len(members) > 25:
        embed.set_footer(text="Discord permette massimo 25 utenti nella tendina. Mostro i primi 25.")

    await interaction.response.send_message(embed=embed, view=RegistraPreIscrittoView(members), ephemeral=True)






class MarketStatusView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=180)

        opened = is_market_open()
        self.add_item(MarketToggleButton(opened))


class MarketToggleButton(discord.ui.Button):
    def __init__(self, opened: bool):
        self.opened = opened

        if opened:
            super().__init__(
                label="Chiudi mercato",
                style=discord.ButtonStyle.danger,
                emoji="🔒"
            )
        else:
            super().__init__(
                label="Apri mercato",
                style=discord.ButtonStyle.success,
                emoji="✅"
            )

    async def callback(self, interaction: discord.Interaction):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ Solo lo staff può modificare lo stato del mercato.", ephemeral=True)
            return

        new_state = not self.opened
        await create_backup_before_sensitive_action("cambio_stato_mercato")
        set_market_open(new_state)

        if new_state:
            embed = discord.Embed(
                title="✅ Mercato aperto",
                description=(
                    "Da ora sono abilitate:\n"
                    "• aste giocatori\n"
                    "• offerte tra player\n"
                    "• controfferte"
                ),
                color=discord.Color.green()
            )
        else:
            embed = discord.Embed(
                title="🔒 Mercato chiuso",
                description=(
                    "Da ora sono bloccate:\n"
                    "• nuove aste\n"
                    "• nuove offerte tra player\n"
                    "• nuove controfferte"
                ),
                color=discord.Color.red()
            )

        await interaction.response.edit_message(embed=embed, view=MarketStatusView())
        await send_staff_log(
            interaction.guild,
            "📈 Stato mercato modificato",
            "Mercato impostato su: **APERTO**" if new_state else "Mercato impostato su: **CHIUSO**",
            user=interaction.user,
            color=discord.Color.green() if new_state else discord.Color.red()
        )


@tree.command(name="mercato_stato", description="Staff: mostra lo stato mercato e permette di aprirlo/chiuderlo")
async def mercato_stato(interaction: discord.Interaction):
    # Defer immediato: evita discord.errors.NotFound 10062 Unknown interaction
    # quando Supabase o Railway impiegano più di 3 secondi a rispondere.
    await safe_defer(interaction, ephemeral=True, thinking=True)

    if not is_admin(interaction):
        await safe_send(interaction, "❌ Solo lo staff può usare questo comando.", ephemeral=True)
        return

    opened = is_market_open()

    if opened:
        embed = discord.Embed(
            title="📊 Stato mercato",
            description="Il mercato è attualmente: **APERTO ✅**\n\nPremi il pulsante sotto per chiuderlo.",
            color=discord.Color.green()
        )
    else:
        embed = discord.Embed(
            title="📊 Stato mercato",
            description="Il mercato è attualmente: **CHIUSO 🔒**\n\nPremi il pulsante sotto per aprirlo.",
            color=discord.Color.red()
        )

    await safe_send(interaction, embed=embed, view=MarketStatusView(), ephemeral=True)


@tree.command(name="stato_mercato", description="Mostra se il mercato è aperto o chiuso")
async def stato_mercato(interaction: discord.Interaction):
    await safe_defer(interaction, ephemeral=True, thinking=True)

    opened = is_market_open()
    embed = discord.Embed(
        title="📊 Stato mercato",
        description=f"Il mercato è: **{market_status_label()}**",
        color=discord.Color.green() if opened else discord.Color.red()
    )
    await safe_send(interaction, embed=embed, ephemeral=True)



@tree.command(name="budget", description="Mostra il tuo budget residuo")
async def budget(interaction: discord.Interaction):
    if not is_spam_channel(interaction):
        await interaction.response.send_message("❌ Usa questo comando solo nel canale SPAM-CHAT.", ephemeral=True)
        return

    conn = connect()
    cur = conn.cursor()
    cur.execute("SELECT budget FROM managers WHERE discord_id = %s", (str(interaction.user.id),))
    row = cur.fetchone()
    conn.close()

    if not row:
        await interaction.response.send_message("Prima usa /registrami.", ephemeral=True)
        return

    await interaction.response.send_message(f"💰 Budget residuo: {row['budget']} crediti.", ephemeral=True)


@tree.command(name="reset_budget", description="Admin: resetta il budget di tutti")
@app_commands.describe(importo="Nuovo budget da assegnare")
async def reset_budget(interaction: discord.Interaction, importo: int = DEFAULT_BUDGET):
    if not is_admin(interaction):
        await interaction.response.send_message("❌ Solo gli admin possono usare questo comando.", ephemeral=True)
        return

    conn = connect()
    cur = conn.cursor()
    cur.execute("UPDATE managers SET budget = %s", (importo,))
    conn.commit()
    conn.close()

    await interaction.response.send_message(f"✅ Budget resettato a **{importo}** crediti per tutti.")
    await send_staff_log(
        interaction.guild,
        "💰 Budget resettato dallo staff",
        f"Nuovo budget: **{importo}** crediti per tutti.",
        user=interaction.user,
        color=discord.Color.orange()
    )


@tree.command(name="reset_asta", description="Admin: chiude tutte le aste aperte")
async def reset_asta(interaction: discord.Interaction):
    if not is_admin(interaction):
        await interaction.response.send_message("❌ Solo gli admin possono usare questo comando.", ephemeral=True)
        return

    conn = connect()
    cur = conn.cursor()
    cur.execute("UPDATE auctions SET status = 'closed' WHERE status = 'open'")
    conn.commit()
    conn.close()

    await interaction.response.send_message("✅ Aste aperte resettate.")
    await send_staff_log(
        interaction.guild,
        "🔨 Aste resettate dallo staff",
        "Tutte le aste aperte sono state chiuse.",
        user=interaction.user,
        color=discord.Color.orange()
    )


@tree.command(name="database", description="Mostra statistiche del database giocatori")
async def database(interaction: discord.Interaction):
    conn = connect()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) AS total FROM players")
    total = cur.fetchone()["total"]
    cur.execute("SELECT COUNT(*) AS liberi FROM players WHERE owner_discord_id IS NULL")
    free = cur.fetchone()["liberi"]
    cur.execute("SELECT COUNT(*) AS sold FROM players WHERE owner_discord_id IS NOT NULL")
    sold = cur.fetchone()["sold"]
    cur.execute("SELECT AVG(overall) AS avg_ovr FROM players")
    avg_ovr = cur.fetchone()["avg_ovr"] or 0
    conn.close()

    embed = discord.Embed(title="📊 Database FC26", description="Statistiche database giocatori importati nel bot.", color=discord.Color.blue())
    embed.add_field(name="Giocatori totali", value=str(total), inline=True)
    embed.add_field(name="Liberi", value=str(free), inline=True)
    embed.add_field(name="Assegnati", value=str(sold), inline=True)
    embed.add_field(name="Overall medio", value=f"{avg_ovr:.1f}", inline=True)
    await interaction.response.send_message(embed=embed)



@tree.command(name="card", description="Mostra la card grafica di un giocatore")
@app_commands.describe(player_id="ID giocatore")
async def card(interaction: discord.Interaction, player_id: str):
    if not is_spam_channel(interaction):
        await interaction.response.send_message("❌ Usa questo comando solo nel canale SPAM-CHAT.", ephemeral=True)
        return

    await interaction.response.defer()

    conn = connect()
    cur = conn.cursor()
    cur.execute("SELECT * FROM players WHERE id = CAST(%s AS BIGINT)", (player_id,))
    player = cur.fetchone()
    conn.close()

    if not player:
        await interaction.followup.send("Giocatore non trovato.", ephemeral=True)
        return

    card_path = create_player_card(player)
    file = discord.File(str(card_path), filename="player_card.png")
    embed = player_embed(player)
    embed.set_image(url="attachment://player_card.png")
    await interaction.followup.send(embed=embed, file=file)


async def start_auction_for_player(interaction: discord.Interaction, player_id: str):
    if AUCTION_CHANNEL_ID and str(interaction.channel_id) != str(AUCTION_CHANNEL_ID):
        await interaction.followup.send("❌ Puoi avviare le aste solo nel canale aste.", ephemeral=True)
        return

    if not is_market_open():
        await interaction.followup.send("🔒 Il mercato è chiuso. Lo staff deve aprirlo per avviare nuove aste.", ephemeral=True)
        return

    conn = connect()
    cur = conn.cursor()

    cur.execute("SELECT * FROM players WHERE id = CAST(%s AS BIGINT)", (str(player_id),))
    player = cur.fetchone()

    if not player:
        conn.close()
        await interaction.followup.send("Giocatore non trovato.", ephemeral=True)
        return

    if player["owner_discord_id"]:
        conn.close()
        await interaction.followup.send("Questo giocatore è già stato assegnato.", ephemeral=True)
        return

    if is_blacklisted(player_id):
        conn.close()
        await interaction.followup.send("Questo giocatore è in blacklist e non può andare all'asta.", ephemeral=True)
        return

    cur.execute("SELECT * FROM auctions WHERE status = 'open' ORDER BY id DESC LIMIT 1")
    open_auction = cur.fetchone()
    if open_auction:
        conn.close()
        msg_ref = ""
        try:
            if open_auction.get("channel_id") and open_auction.get("message_id"):
                msg_ref = f"\nAsta attiva: https://discord.com/channels/{interaction.guild.id}/{open_auction['channel_id']}/{open_auction['message_id']}"
        except Exception:
            pass
        await interaction.followup.send(
            "❌ C'è già un'asta aperta. Non puoi aprirne un'altra finché non termina o viene chiusa con `/chiudi_asta`."
            + msg_ref,
            ephemeral=True
        )
        return

    cur.execute("SELECT * FROM managers WHERE discord_id = %s", (str(interaction.user.id),))
    starter_manager = cur.fetchone()

    if not starter_manager:
        conn.close()
        await interaction.followup.send("Prima devi essere registrato/iscritto per aprire un'asta.", ephemeral=True)
        return

    base = base_price_from_overall(player["overall"])

    # Chi apre l'asta è automaticamente primo offerente e deve avere almeno la base.
    if safe_int(starter_manager["budget"]) < base:
        conn.close()
        await interaction.followup.send(f"Budget insufficiente per aprire l'asta. Servono almeno **{base}** crediti.", ephemeral=True)
        return

    ok, group, current, limit = can_add_player_to_roster(interaction.user.id, player["position"])
    if not ok:
        conn.close()
        await interaction.followup.send(
            f"Non puoi aprire questa asta: hai già raggiunto il limite per {role_label(group)} ({current}/{limit}).",
            ephemeral=True
        )
        return

    cur.execute("""
        INSERT INTO auctions
        (player_id, status, highest_bid, highest_bidder_id, channel_id, created_by, created_at)
        VALUES (%s, 'open', %s, %s, %s, %s, CURRENT_TIMESTAMP)
        RETURNING id
    """, (str(player_id), base, str(interaction.user.id), str(interaction.channel_id), str(interaction.user.id)))
    auction_id = cur.fetchone()["id"]
    conn.commit()

    record_bid(auction_id, str(player_id), str(interaction.user.id), interaction.user.display_name, base)
    auction_last_bids[int(auction_id)] = [f"• **{interaction.user.display_name}** apre → **{base}** cr"]

    cur.execute("""
        SELECT a.*, p.*
        FROM auctions a
        JOIN players p ON p.id::text = a.player_id::text
        WHERE a.id = %s
    """, (auction_id,))
    auction_row = cur.fetchone()
    conn.close()

    card_path = create_player_card(player)
    file = discord.File(str(card_path), filename="auction_card.png")
    embed = auction_embed(player, auction_row, AUCTION_SECONDS)
    embed.set_image(url="attachment://auction_card.png")

    message = await interaction.followup.send(embed=embed, file=file, view=AuctionView(), wait=True)

    auction_thread = None
    try:
        auction_thread = await interaction.channel.create_thread(
            name=f"Asta {player['name']}"[:90],
            message=message,
            auto_archive_duration=60
        )
        await auction_thread.send(f"Thread automatico per l'asta di **{player['name']}**.")
    except Exception as e:
        print(f"[ASTA] Thread non creato: {e}")
        auction_thread = None

    conn = connect()
    cur = conn.cursor()
    cur.execute("UPDATE auctions SET message_id = %s WHERE id = %s", (str(message.id), auction_id))
    conn.commit()
    conn.close()

    try:
        await send_staff_log(
            interaction.guild,
            "🔨 Asta avviata",
            f"Giocatore: **{player['name']}** (`{player_id}`)\nBase: **{base} crediti**\nAperta da: {interaction.user.mention}",
            user=interaction.user,
            color=discord.Color.gold()
        )
    except Exception:
        pass

    try:
        await send_auction_history_log(
            interaction.guild,
            "🔨 Asta avviata",
            (
                f"Giocatore: **{player['name']}** (`{player_id}`)\n"
                f"Squadra: **{player['team']}**\n"
                f"OVR: **{player['overall']}**\n"
                f"Base: **{base} crediti**\n"
                f"Aperta da: {interaction.user.mention}"
            ),
            color=discord.Color.gold()
        )
    except Exception:
        pass

    try:
        await publish_auction_news(
            interaction.guild,
            "🔨 ASTA TOP AVVIATA",
            (
                f"Parte l'asta per **{player['name']}**!\n"
                f"⭐ Overall: **{player['overall']}**\n"
                f"🏟️ Squadra: **{player['team']}**\n"
                f"💰 Base: **{base} crediti**"
            ),
            overall=player["overall"],
            price=base
        )
    except Exception:
        pass

    await run_auction_countdown(interaction.channel, int(auction_id), message)



AUCTION_MENU_PAGE_SIZE = 25


def chunk_options(rows, page=0, size=AUCTION_MENU_PAGE_SIZE):
    page = max(0, int(page or 0))
    start = page * size
    end = start + size
    return rows[start:end]


class AuctionLeagueSelect(discord.ui.Select):
    def __init__(self, rows, page=0):
        self.rows = rows
        self.page = int(page or 0)

        page_rows = chunk_options(rows, self.page)
        options = []

        for r in page_rows:
            league = str(r["league"] or "Senza campionato")
            options.append(discord.SelectOption(
                label=league[:100],
                value=league[:100],
                description=f"{r['free_count']} giocatori liberi"
            ))

        if not options:
            options.append(discord.SelectOption(label="Nessun giocatore libero", value="__none__"))

        super().__init__(
            placeholder=f"1️⃣ Scegli il campionato... pagina {self.page + 1}",
            min_values=1,
            max_values=1,
            options=options
        )

    async def callback(self, interaction: discord.Interaction):
        try:
            await safe_defer(interaction, ephemeral=True, thinking=True)
        except Exception:
            pass

        league = self.values[0]
        if league == "__none__":
            await interaction.followup.send("❌ Nessun giocatore libero disponibile.", ephemeral=True)
            return

        await interaction.followup.send(
            f"Campionato selezionato: **{league}**\nOra scegli la squadra:",
            view=AuctionTeamSelectView(league, page=0),
            ephemeral=True
        )


class AuctionLeagueSelectView(discord.ui.View):
    def __init__(self, page=0):
        super().__init__(timeout=180)
        self.page = int(page or 0)

        conn = connect()
        cur = conn.cursor()
        try:
            cur.execute("""
                SELECT COALESCE(league, 'Senza campionato') AS league, COUNT(*) AS free_count
                FROM players
                WHERE owner_discord_id IS NULL OR owner_discord_id = ''
                GROUP BY COALESCE(league, 'Senza campionato')
                ORDER BY league ASC
            """)
            self.rows = cur.fetchall()
        except Exception as e:
            print(f"[ASTA MENU] Errore campionati: {e}")
            self.rows = []
        conn.close()

        self.max_page = max(0, (len(self.rows) - 1) // AUCTION_MENU_PAGE_SIZE)
        self.page = max(0, min(self.page, self.max_page))

        self.add_item(AuctionLeagueSelect(self.rows, self.page))

    @discord.ui.button(label="⬅️ Indietro", style=discord.ButtonStyle.secondary)
    async def previous_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            await interaction.response.edit_message(
                content=f"🔨 **Avvio asta guidata**\nScegli il campionato del giocatore libero:\nPagina **{max(0, self.page - 1) + 1}/{self.max_page + 1}**",
                view=AuctionLeagueSelectView(max(0, self.page - 1))
            )
        except Exception as e:
            print(f"[ASTA MENU] Errore pagina precedente campionati: {e}")

    @discord.ui.button(label="Avanti ➡️", style=discord.ButtonStyle.primary)
    async def next_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            await interaction.response.edit_message(
                content=f"🔨 **Avvio asta guidata**\nScegli il campionato del giocatore libero:\nPagina **{min(self.max_page, self.page + 1) + 1}/{self.max_page + 1}**",
                view=AuctionLeagueSelectView(min(self.max_page, self.page + 1))
            )
        except Exception as e:
            print(f"[ASTA MENU] Errore pagina successiva campionati: {e}")


class AuctionTeamSelect(discord.ui.Select):
    def __init__(self, league: str, rows, page=0):
        self.league = str(league)
        self.rows = rows
        self.page = int(page or 0)

        page_rows = chunk_options(rows, self.page)
        options = []

        for r in page_rows:
            team = str(r["team"] or "Senza squadra")
            options.append(discord.SelectOption(
                label=team[:100],
                value=team[:100],
                description=f"{r['free_count']} giocatori liberi"
            ))

        if not options:
            options.append(discord.SelectOption(label="Nessuna squadra disponibile", value="__none__"))

        super().__init__(
            placeholder=f"2️⃣ Scegli la squadra... pagina {self.page + 1}",
            min_values=1,
            max_values=1,
            options=options
        )

    async def callback(self, interaction: discord.Interaction):
        try:
            await safe_defer(interaction, ephemeral=True, thinking=True)
        except Exception:
            pass

        team = self.values[0]
        if team == "__none__":
            await interaction.followup.send("❌ Nessuna squadra disponibile.", ephemeral=True)
            return

        await interaction.followup.send(
            f"Squadra selezionata: **{team}**\nOra scegli il giocatore libero da mandare all'asta:",
            view=AuctionPlayerSelectView(self.league, team, page=0),
            ephemeral=True
        )


class AuctionTeamSelectView(discord.ui.View):
    def __init__(self, league: str, page=0):
        super().__init__(timeout=180)
        self.league = str(league)
        self.page = int(page or 0)

        conn = connect()
        cur = conn.cursor()
        try:
            cur.execute("""
                SELECT COALESCE(team, 'Senza squadra') AS team, COUNT(*) AS free_count
                FROM players
                WHERE (owner_discord_id IS NULL OR owner_discord_id = '')
                  AND COALESCE(league, 'Senza campionato') = %s
                GROUP BY COALESCE(team, 'Senza squadra')
                ORDER BY team ASC
            """, (self.league,))
            self.rows = cur.fetchall()
        except Exception as e:
            print(f"[ASTA MENU] Errore squadre: {e}")
            self.rows = []
        conn.close()

        self.max_page = max(0, (len(self.rows) - 1) // AUCTION_MENU_PAGE_SIZE)
        self.page = max(0, min(self.page, self.max_page))

        self.add_item(AuctionTeamSelect(self.league, self.rows, self.page))

    @discord.ui.button(label="⬅️ Indietro", style=discord.ButtonStyle.secondary)
    async def previous_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(
            content=f"Campionato selezionato: **{self.league}**\nOra scegli la squadra:\nPagina **{max(0, self.page - 1) + 1}/{self.max_page + 1}**",
            view=AuctionTeamSelectView(self.league, max(0, self.page - 1))
        )

    @discord.ui.button(label="Avanti ➡️", style=discord.ButtonStyle.primary)
    async def next_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(
            content=f"Campionato selezionato: **{self.league}**\nOra scegli la squadra:\nPagina **{min(self.max_page, self.page + 1) + 1}/{self.max_page + 1}**",
            view=AuctionTeamSelectView(self.league, min(self.max_page, self.page + 1))
        )


class AuctionPlayerSelect(discord.ui.Select):
    def __init__(self, league: str, team: str, rows, page=0):
        self.league = str(league)
        self.team = str(team)
        self.rows = rows
        self.page = int(page or 0)

        page_rows = chunk_options(rows, self.page)
        options = []

        for r in page_rows:
            base = base_price_from_overall(r["overall"])
            label = f"{r['name']} • {r['position']} • OVR {r['overall']}"
            options.append(discord.SelectOption(
                label=label[:100],
                value=str(r["id"]),
                description=f"Base asta: {base} crediti"
            ))

        if not options:
            options.append(discord.SelectOption(label="Nessun giocatore libero", value="__none__"))

        super().__init__(
            placeholder=f"3️⃣ Scegli il giocatore... pagina {self.page + 1}",
            min_values=1,
            max_values=1,
            options=options
        )

    async def callback(self, interaction: discord.Interaction):
        try:
            await interaction.response.defer()
        except Exception:
            pass

        player_id = self.values[0]
        if player_id == "__none__":
            await interaction.followup.send("❌ Nessun giocatore libero in questa squadra.", ephemeral=True)
            return

        await start_auction_for_player(interaction, player_id)


class AuctionPlayerSelectView(discord.ui.View):
    def __init__(self, league: str, team: str, page=0):
        super().__init__(timeout=180)
        self.league = str(league)
        self.team = str(team)
        self.page = int(page or 0)

        conn = connect()
        cur = conn.cursor()
        try:
            cur.execute("""
                SELECT id, name, position, overall
                FROM players
                WHERE (owner_discord_id IS NULL OR owner_discord_id = '')
                  AND COALESCE(league, 'Senza campionato') = %s
                  AND COALESCE(team, 'Senza squadra') = %s
                ORDER BY overall DESC NULLS LAST, name ASC
            """, (self.league, self.team))
            self.rows = cur.fetchall()
        except Exception as e:
            print(f"[ASTA MENU] Errore giocatori: {e}")
            self.rows = []
        conn.close()

        self.max_page = max(0, (len(self.rows) - 1) // AUCTION_MENU_PAGE_SIZE)
        self.page = max(0, min(self.page, self.max_page))

        self.add_item(AuctionPlayerSelect(self.league, self.team, self.rows, self.page))

    @discord.ui.button(label="⬅️ Indietro", style=discord.ButtonStyle.secondary)
    async def previous_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(
            content=f"Squadra selezionata: **{self.team}**\nOra scegli il giocatore libero da mandare all'asta:\nPagina **{max(0, self.page - 1) + 1}/{self.max_page + 1}**",
            view=AuctionPlayerSelectView(self.league, self.team, max(0, self.page - 1))
        )

    @discord.ui.button(label="Avanti ➡️", style=discord.ButtonStyle.primary)
    async def next_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(
            content=f"Squadra selezionata: **{self.team}**\nOra scegli il giocatore libero da mandare all'asta:\nPagina **{min(self.max_page, self.page + 1) + 1}/{self.max_page + 1}**",
            view=AuctionPlayerSelectView(self.league, self.team, min(self.max_page, self.page + 1))
        )


@tree.command(name="asta", description="Avvia un'asta guidata: campionato → squadra → giocatore libero")
async def asta(interaction: discord.Interaction):
    try:
        await safe_defer(interaction, ephemeral=True, thinking=True)
    except Exception as e:
        print(f"[ASTA] Defer fallito: {e}")
        return

    try:
        if AUCTION_CHANNEL_ID and str(interaction.channel_id) != str(AUCTION_CHANNEL_ID):
            await interaction.followup.send("❌ Puoi usare `/asta` solo nel canale aste.", ephemeral=True)
            return

        if not is_market_open():
            await interaction.followup.send("🔒 Il mercato è chiuso. Lo staff deve aprirlo per avviare aste.", ephemeral=True)
            return

        await interaction.followup.send(
            "🔨 **Avvio asta guidata**\nScegli il campionato del giocatore libero:",
            view=AuctionLeagueSelectView(page=0),
            ephemeral=True
        )

    except Exception as e:
        print(f"[ASTA] Errore comando /asta: {e}")
        try:
            await interaction.followup.send(f"❌ Errore asta: `{e}`", ephemeral=True)
        except Exception:
            pass



async def run_auction_countdown(channel, auction_id: int, message):
    auction_timers[int(auction_id)] = AUCTION_SECONDS

    while auction_timers.get(int(auction_id), 0) > 0:
        remaining = auction_timers[int(auction_id)]

        conn = connect()
        cur = conn.cursor()
        cur.execute("""
            SELECT a.*, p.*
            FROM auctions a
            JOIN players p ON p.id::text = a.player_id::text
            WHERE a.id = %s AND a.status = 'open'
        """, (auction_id,))
        row = cur.fetchone()
        conn.close()

        if not row:
            auction_timers.pop(int(auction_id), None)
            return

        try:
            await message.edit(embed=auction_embed(row, row, remaining), view=AuctionView())
        except Exception as e:
            print(f"[ASTA] Errore countdown edit: {e}")

        await asyncio.sleep(1)
        auction_timers[int(auction_id)] -= 1

    auction_timers.pop(int(auction_id), None)
    await close_auction(channel, auction_id, message)


async def close_auction(channel, auction_id: int, message=None):
    conn = connect()
    cur = conn.cursor()

    cur.execute("""
        SELECT a.*, p.name AS player_name, p.id AS player_id, p.position AS player_position
        FROM auctions a
        JOIN players p ON p.id::text = a.player_id::text
        WHERE a.id = %s AND a.status = 'open'
    """, (auction_id,))
    auction = cur.fetchone()

    if not auction:
        conn.close()
        return

    if auction["highest_bidder_id"]:
        cur.execute("SELECT * FROM managers WHERE discord_id = %s", (auction["highest_bidder_id"],))
        manager = cur.fetchone()

        ok, group, current, limit = can_add_player_to_roster(
            auction["highest_bidder_id"],
            auction["player_position"]
        )

        tax_amount = int((safe_int(auction["highest_bid"]) * MARKET_TAX) / 100)
        final_price = int(auction["highest_bid"]) + tax_amount

        if manager and safe_int(manager["budget"]) >= final_price and ok:
            cur.execute(
                "UPDATE managers SET budget = budget - %s WHERE discord_id = %s",
                (final_price, auction["highest_bidder_id"])
            )
            cur.execute(
                "UPDATE players SET owner_discord_id = %s, sold_price = %s WHERE id = CAST(%s AS BIGINT)",
                (auction["highest_bidder_id"], final_price, auction["player_id"])
            )
            cur.execute(
                "UPDATE auctions SET status = 'closed', closed_at = CURRENT_TIMESTAMP WHERE id = %s",
                (auction_id,)
            )
            conn.commit()
            conn.close()

            winner = await bot.fetch_user(int(auction["highest_bidder_id"]))
            record_transfer(
                auction["player_id"],
                auction["player_name"],
                auction["highest_bidder_id"],
                winner.display_name,
                final_price,
                source="auction"
            )

            await safe_dm(
                auction["highest_bidder_id"],
                f"🏆 Hai vinto l'asta di **{auction['player_name']}** per **{auction['highest_bid']}** crediti + tassa {tax_amount}. Totale: **{final_price}**."
            )

            club_name = "Nessun club"
            try:
                club_name = manager.get("club_name") or manager.get("team_name") or manager.get("club") or "Nessun club"
            except Exception:
                club_name = "Nessun club"

            embed = discord.Embed(
                title="🏆 ASTA CONCLUSA",
                description=(
                    f"## {auction['player_name']} è stato acquistato!\n"
                    f"👤 **Vincitore:** <@{auction['highest_bidder_id']}>\n"
                    f"🏟️ **Club:** **{club_name}**\n"
                    f"💰 **Offerta vincente:** **{auction['highest_bid']}** crediti"
                ),
                color=discord.Color.green()
            )
            embed.add_field(name="🏦 Tassa mercato", value=f"{tax_amount} crediti ({MARKET_TAX}%)", inline=True)
            embed.add_field(name="💳 Totale pagato", value=f"**{final_price}** crediti", inline=True)
            embed.add_field(name="📌 ID giocatore", value=f"`{auction['player_id']}`", inline=True)
            embed.set_footer(text="FC26 Auction Bot • Operazione completata")

            card_file = None
            try:
                conn_card = connect()
                cur_card = conn_card.cursor()
                cur_card.execute("SELECT * FROM players WHERE id = CAST(%s AS BIGINT)", (str(auction["player_id"]),))
                player_full = cur_card.fetchone()
                conn_card.close()
                if player_full:
                    card_path = create_player_card(player_full)
                    card_file = discord.File(str(card_path), filename="winner_card.png")
                    embed.set_image(url="attachment://winner_card.png")
                else:
                    embed.set_image(url=WALKOUT_GIF)
            except Exception as e:
                print(f"[ASTA] Errore card vincitore: {e}")
                embed.set_image(url=WALKOUT_GIF)

            if message:
                try:
                    final_live_embed = auction_embed(auction, auction, 0)
                    final_live_embed.title = "✅ ASTA TERMINATA"
                    final_live_embed.color = discord.Color.green()
                    await message.edit(embed=final_live_embed, view=None)
                except Exception:
                    try:
                        await message.edit(view=None)
                    except Exception:
                        pass

            if card_file:
                await channel.send(embed=embed, file=card_file)
            else:
                await channel.send(embed=embed)

            log_embed = discord.Embed(
                title="📜 Asta conclusa",
                description=f"**{auction['player_name']}** → **{winner.display_name}**",
                color=discord.Color.green()
            )
            log_embed.add_field(name="Prezzo", value=f"{auction['highest_bid']} crediti", inline=True)
            log_embed.add_field(name="Tassa", value=f"{tax_amount} crediti", inline=True)
            log_embed.add_field(name="Totale", value=f"{final_price} crediti", inline=True)
            log_embed.add_field(name="ID giocatore", value=str(auction["player_id"]), inline=True)
            log_embed.add_field(name="Vincitore", value=f"<@{auction['highest_bidder_id']}>", inline=True)

            await send_auction_history_log(
                channel.guild if hasattr(channel, "guild") else None,
                "📜 Asta conclusa",
                "",
                embed=log_embed
            )

            try:
                await publish_auction_news(
                    channel.guild if hasattr(channel, "guild") else None,
                    "📰 COLPO DI MERCATO",
                    (
                        f"**{winner.display_name}** si aggiudica **{auction['player_name']}**!\n"
                        f"💰 Prezzo finale: **{auction['highest_bid']}** crediti\n"
                        f"🏦 Totale con tassa: **{final_price}** crediti"
                    ),
                    price=final_price,
                    force=False
                )
            except Exception:
                pass

            auction_last_bids.pop(int(auction_id), None)
            return

        # Miglior offerente non più valido
        reason = "budget insufficiente per prezzo + tassa" if manager else "manager non trovato"
        if not ok:
            reason = f"limite rosa raggiunto per {role_label(group)} ({current}/{limit})"

        cur.execute(
            "UPDATE auctions SET status = 'closed', closed_at = CURRENT_TIMESTAMP WHERE id = %s",
            (auction_id,)
        )
        conn.commit()
        conn.close()

        if message:
            try:
                await message.edit(view=None)
            except Exception:
                pass

        embed = discord.Embed(
            title="❌ ASTA ANNULLATA",
            description=f"L'asta di **{auction['player_name']}** è stata chiusa senza assegnazione.\nMotivo: **{reason}**.",
            color=discord.Color.red()
        )
        await channel.send(embed=embed)
        try:
            await send_auction_history_log(
                channel.guild if hasattr(channel, "guild") else None,
                "❌ Asta annullata",
                f"Giocatore: **{auction['player_name']}**\nMotivo: **{reason}**",
                color=discord.Color.red()
            )
        except Exception:
            pass
        auction_last_bids.pop(int(auction_id), None)
        return

    cur.execute("UPDATE auctions SET status = 'closed', closed_at = CURRENT_TIMESTAMP WHERE id = %s", (auction_id,))
    conn.commit()
    conn.close()

    if message:
        try:
            await message.edit(view=None)
        except Exception:
            pass

    embed = discord.Embed(
        title="❌ ASTA CHIUSA",
        description=f"Nessuna offerta valida per **{auction['player_name']}**.",
        color=discord.Color.red()
    )
    await channel.send(embed=embed)
    auction_last_bids.pop(int(auction_id), None)




@tree.command(name="storico_aste_scambi", description="Mostra riepilogo storico aste e scambi")
async def storico_aste_scambi(interaction: discord.Interaction):
    conn = connect()
    cur = conn.cursor()

    cur.execute("""
        SELECT p.name, p.sold_price, p.owner_discord_id
        FROM players p
        WHERE p.owner_discord_id IS NOT NULL
          AND p.owner_discord_id <> ''
          AND p.sold_price IS NOT NULL
        ORDER BY p.sold_price DESC
        LIMIT 10
    """)
    buys = cur.fetchall()

    cur.execute("""
        SELECT proposer_name, target_name, player_name, amount, status, created_at
        FROM player_trade_offers
        ORDER BY id DESC
        LIMIT 10
    """)
    trades = cur.fetchall()
    conn.close()

    embed = discord.Embed(
        title="📜 Storico aste e scambi",
        color=discord.Color.blurple()
    )

    if buys:
        embed.add_field(
            name="Ultimi/Top acquisti asta",
            value="\\n".join(
                f"• **{r['name']}** → <@{r['owner_discord_id']}> • **{r['sold_price']} cr**"
                for r in buys[:10]
            ),
            inline=False
        )
    else:
        embed.add_field(name="Aste", value="Nessuna asta registrata.", inline=False)

    if trades:
        embed.add_field(
            name="Ultime offerte/scambi",
            value="\\n".join(
                f"• {r['proposer_name'] or '-'} → {r['target_name'] or '-'} | {r['player_name'] or '-'} | {r['amount'] or 0} cr | `{r['status']}`"
                for r in trades[:10]
            ),
            inline=False
        )
    else:
        embed.add_field(name="Scambi", value="Nessuno scambio registrato.", inline=False)

    await interaction.response.send_message(embed=embed, ephemeral=True)

@tree.command(name="mercato_panel", description="Mostra il pannello live del mercato")
async def mercato_panel(interaction: discord.Interaction):
    if not can_use_normal_staff(interaction.user):
        await interaction.response.send_message("❌ Solo lo staff può pubblicare il panel mercato.", ephemeral=True)
        return

    conn = connect()
    cur = conn.cursor()

    cur.execute("SELECT COUNT(*) AS c FROM players WHERE owner_discord_id IS NULL OR owner_discord_id = ''")
    free_players = cur.fetchone()["c"]

    cur.execute("SELECT COUNT(*) AS c FROM players WHERE owner_discord_id IS NOT NULL AND owner_discord_id <> ''")
    assigned_players = cur.fetchone()["c"]

    cur.execute("SELECT COUNT(*) AS c FROM auctions WHERE status = 'open'")
    open_auctions = cur.fetchone()["c"]

    cur.execute("SELECT COUNT(*) AS c FROM auctions WHERE status = 'closed'")
    closed_auctions = cur.fetchone()["c"]

    cur.execute("""
        SELECT p.name, p.overall, p.sold_price, p.owner_discord_id
        FROM players p
        WHERE p.owner_discord_id IS NOT NULL
          AND p.owner_discord_id <> ''
          AND p.sold_price IS NOT NULL
        ORDER BY p.sold_price DESC
        LIMIT 5
    """)
    top_sales = cur.fetchall()
    conn.close()

    status = market_status_label()

    embed = discord.Embed(
        title="📊 Panel live mercato FC26",
        description=f"Stato mercato: **{status}**",
        color=discord.Color.green() if is_market_open() else discord.Color.red()
    )
    embed.add_field(name="Giocatori liberi", value=str(free_players), inline=True)
    embed.add_field(name="Giocatori assegnati", value=str(assigned_players), inline=True)
    embed.add_field(name="Aste aperte", value=str(open_auctions), inline=True)
    embed.add_field(name="Aste concluse", value=str(closed_auctions), inline=True)

    if top_sales:
        lines = []
        for idx, r in enumerate(top_sales, 1):
            lines.append(
                f"**{idx}.** {r['name']} • OVR {r['overall']} • **{r['sold_price']} cr** → <@{r['owner_discord_id']}>"
            )
        embed.add_field(name="Top acquisti", value="\\n".join(lines), inline=False)
    else:
        embed.add_field(name="Top acquisti", value="Nessun acquisto registrato.", inline=False)

    embed.set_footer(text="Aggiornato live dal bot • Usa /asta_info per l'asta attiva")
    await interaction.response.send_message(embed=embed)


@tree.command(name="asta_info", description="Mostra l'asta attualmente aperta")
async def asta_info(interaction: discord.Interaction):
    conn = connect()
    cur = conn.cursor()
    cur.execute("""
        SELECT a.*, p.name AS player_name, p.team, p.position, p.overall
        FROM auctions a
        JOIN players p ON p.id::text = a.player_id::text
        WHERE a.status = 'open'
        ORDER BY a.id DESC
        LIMIT 1
    """)
    auction = cur.fetchone()
    conn.close()

    if not auction:
        await interaction.response.send_message("Non c'è nessuna asta aperta.", ephemeral=True)
        return

    embed = discord.Embed(
        title="🔨 Asta attiva",
        description=f"**{auction['player_name']}** — {auction['position']} — OVR {auction['overall']}",
        color=discord.Color.gold()
    )
    embed.add_field(name="Offerta attuale", value=f"{auction['highest_bid']} crediti", inline=True)
    embed.add_field(name="Leader", value=f"<@{auction['highest_bidder_id']}>" if auction["highest_bidder_id"] else "Nessuno", inline=True)
    embed.add_field(name="Tempo stimato", value=f"{auction_timers.get(int(auction['id']), 'N/D')}s", inline=True)

    await interaction.response.send_message(embed=embed, ephemeral=True)


@tree.command(name="chiudi_asta", description="Staff: chiude subito l'asta aperta")
async def chiudi_asta(interaction: discord.Interaction):
    if not can_use_normal_staff(interaction.user):
        await interaction.response.send_message("❌ Solo lo staff può chiudere manualmente un'asta.", ephemeral=True)
        return

    await safe_defer(interaction, ephemeral=True, thinking=True)

    conn = connect()
    cur = conn.cursor()
    cur.execute("SELECT id, channel_id, message_id FROM auctions WHERE status = 'open' ORDER BY id DESC LIMIT 1")
    auction = cur.fetchone()
    conn.close()

    if not auction:
        await interaction.followup.send("Non c'è nessuna asta aperta.", ephemeral=True)
        return

    auction_id = int(auction["id"])
    auction_timers[auction_id] = 0

    channel = interaction.channel
    message = None
    try:
        if auction.get("channel_id"):
            channel = await bot.fetch_channel(int(auction["channel_id"]))
        if auction.get("message_id"):
            message = await channel.fetch_message(int(auction["message_id"]))
    except Exception:
        pass

    await close_auction(channel, auction_id, message)
    await interaction.followup.send("✅ Asta chiusa manualmente.", ephemeral=True)





def build_roster_embed(discord_id, display_name):
    conn = connect()
    cur = conn.cursor()

    cur.execute("SELECT budget FROM managers WHERE discord_id = %s", (str(discord_id),))
    manager = cur.fetchone()
    budget = manager["budget"] if manager else 0

    cur.execute("""
        SELECT name, team, position, overall, sold_price
        FROM players
        WHERE owner_discord_id = %s
        ORDER BY overall DESC
    """, (str(discord_id),))
    rows = cur.fetchall()
    conn.close()

    if not rows:
        embed = discord.Embed(
            title=f"📋 Rosa di {display_name}",
            description="Questa rosa non ha ancora giocatori.",
            color=discord.Color.dark_grey()
        )
        embed.add_field(name="Budget residuo", value=f"{budget} crediti", inline=True)
        return embed

    total_spent = sum(r["sold_price"] or 0 for r in rows)
    avg_ovr = sum(int(r["overall"] or 0) for r in rows) / len(rows)

    grouped = {
        "🧤 Portieri": [],
        "🛡️ Difensori": [],
        "🎯 Centrocampisti": [],
        "⚽ Attaccanti": [],
        "📌 Altro": []
    }

    for r in rows:
        group = role_group(r["position"])
        line = f"**{r['name']}** — {r['position']} • OVR {r['overall']} • {r['sold_price']} cr"

        if group == "GK":
            grouped["🧤 Portieri"].append(line)
        elif group == "DEF":
            grouped["🛡️ Difensori"].append(line)
        elif group == "MID":
            grouped["🎯 Centrocampisti"].append(line)
        elif group == "ATT":
            grouped["⚽ Attaccanti"].append(line)
        else:
            grouped["📌 Altro"].append(line)

    embed = discord.Embed(
        title=f"📋 Rosa di {display_name}",
        description=f"Giocatori: **{len(rows)}** • Overall medio: **{avg_ovr:.1f}**",
        color=discord.Color.green()
    )

    embed.add_field(name="Budget residuo", value=f"{budget} crediti", inline=True)
    embed.add_field(name="Totale speso", value=f"{total_spent} crediti", inline=True)

    for title, items in grouped.items():
        if items:
            # Discord limita ogni field a 1024 caratteri.
            value = "\n".join(items)
            if len(value) > 1000:
                value = value[:997] + "..."
            embed.add_field(name=title, value=value, inline=False)

    return embed


class RosaSelect(discord.ui.Select):
    def __init__(self, managers):
        options = []

        for manager in managers[:25]:
            options.append(
                discord.SelectOption(
                    label=manager["name"][:100],
                    value=str(manager["discord_id"]),
                    description=f"Budget: {manager['budget']} crediti"
                )
            )

        super().__init__(
            placeholder="Scegli una rosa da visualizzare...",
            min_values=1,
            max_values=1,
            options=options,
            custom_id="rosa_select_manager"
        )

    async def callback(self, interaction: discord.Interaction):
        selected_id = self.values[0]

        conn = connect()
        cur = conn.cursor()
        cur.execute("SELECT name FROM managers WHERE discord_id = %s", (selected_id,))
        manager = cur.fetchone()
        conn.close()

        if not manager:
            await interaction.response.send_message("Manager non trovato.", ephemeral=True)
            return

        embed = build_roster_embed(selected_id, manager["name"])
        await interaction.response.edit_message(embed=embed, view=self.view)


class RosaView(discord.ui.View):
    def __init__(self, managers):
        super().__init__(timeout=180)
        self.add_item(RosaSelect(managers))


@tree.command(name="rosa", description="Mostra una rosa scegliendo il manager da una tendina")
async def rosa(interaction: discord.Interaction):
    if not is_rose_channel(interaction):
        await interaction.response.send_message("❌ Usa questo comando solo nel canale ROSE.", ephemeral=True)
        return

    conn = connect()
    cur = conn.cursor()
    cur.execute("""
        SELECT m.discord_id, COALESCE(m.name, m.manager_name, m.discord_id) AS name, m.budget, COUNT(p.id) AS player_count
        FROM managers m
        LEFT JOIN players p ON p.owner_discord_id = m.discord_id
        GROUP BY m.discord_id, COALESCE(m.name, m.manager_name, m.discord_id) AS name, m.budget
        ORDER BY COALESCE(m.name, m.manager_name, m.discord_id) ASC
    """)
    managers = cur.fetchall()
    conn.close()

    if not managers:
        await interaction.response.send_message("Nessun manager registrato.", ephemeral=True)
        return

    embed = discord.Embed(
        title="📋 Rose disponibili",
        description="Scegli dalla tendina quale rosa vuoi visualizzare.",
        color=discord.Color.green()
    )

    preview_lines = []
    for m in managers[:15]:
        preview_lines.append(f"• **{m['name']}** — {m['player_count']} giocatori — {m['budget']} cr")

    embed.add_field(
        name="Manager",
        value="\n".join(preview_lines) if preview_lines else "Nessun manager disponibile.",
        inline=False
    )

    if len(managers) > 25:
        embed.set_footer(text="Mostro solo i primi 25 manager nella tendina per limite Discord.")

    await interaction.response.send_message(embed=embed, view=RosaView(managers), ephemeral=True)



def split_discord_text(text, limit=1000):
    """Divide un testo in parti compatibili con i limiti dei field Embed di Discord."""
    text = str(text or "").strip()
    if not text:
        return []

    chunks = []
    while len(text) > limit:
        cut = text.rfind("\n", 0, limit)
        if cut <= 0:
            cut = limit
        chunks.append(text[:cut].strip())
        text = text[cut:].strip()

    if text:
        chunks.append(text)

    return chunks


class RoseCampionatoSelect(discord.ui.Select):
    def __init__(self, campionati):
        options = []
        for campionato in campionati[:25]:
            name = campionato["name"]
            club_count = campionato.get("club_count", 0) if hasattr(campionato, "get") else 0
            options.append(
                discord.SelectOption(
                    label=str(name)[:100],
                    value=str(name),
                    description=f"Club iscritti: {club_count}"[:100]
                )
            )

        super().__init__(
            placeholder="Scegli il campionato...",
            min_values=1,
            max_values=1,
            options=options,
            custom_id="rose_select_campionato"
        )

    async def callback(self, interaction: discord.Interaction):
        campionato = self.values[0]

        conn = connect()
        cur = conn.cursor()
        cur.execute("""
            SELECT
                c.name,
                c.assigned_to,
                COALESCE(m.name, m.manager_name, c.assigned_to) AS manager_name,
                COALESCE(m.budget, 0) AS budget,
                COUNT(p.id) AS player_count
            FROM fc26_clubs c
            LEFT JOIN managers m ON m.discord_id = c.assigned_to
            LEFT JOIN players p ON p.owner_discord_id = c.assigned_to
            WHERE LOWER(COALESCE(c.league, 'Senza campionato')) = LOWER(%s)
              AND c.assigned_to IS NOT NULL
              AND TRIM(c.assigned_to) != ''
            GROUP BY c.name, c.assigned_to, COALESCE(m.name, m.manager_name, c.assigned_to), COALESCE(m.budget, 0)
            ORDER BY c.name ASC
        """, (campionato,))
        clubs = cur.fetchall()
        conn.close()

        if not clubs:
            await interaction.response.send_message(
                "Nessun club iscritto trovato per questo campionato.",
                ephemeral=True
            )
            return

        preview = []
        for club in clubs[:15]:
            preview.append(
                f"• **{club['name']}** — Manager: **{club['manager_name']}** — {club['player_count']} giocatori"
            )

        embed = discord.Embed(
            title=f"🏆 {campionato}",
            description="Scegli un club iscritto per vedere la rosa completa.",
            color=discord.Color.green()
        )
        embed.add_field(
            name="Club iscritti",
            value="\n".join(preview) if preview else "Nessun club disponibile.",
            inline=False
        )
        if len(clubs) > 25:
            embed.set_footer(text="Mostro solo i primi 25 club nella tendina per limite Discord.")

        await interaction.response.edit_message(
            embed=embed,
            view=RoseClubView(campionato, clubs)
        )


class RoseClubSelect(discord.ui.Select):
    def __init__(self, campionato, clubs):
        self.campionato = campionato
        options = []

        for club in clubs[:25]:
            options.append(
                discord.SelectOption(
                    label=str(club["name"])[:100],
                    value=str(club["assigned_to"]),
                    description=f"{club['manager_name']} • {club['player_count']} giocatori"[:100]
                )
            )

        super().__init__(
            placeholder="Scegli il club iscritto...",
            min_values=1,
            max_values=1,
            options=options,
            custom_id="rose_select_club"
        )

    async def callback(self, interaction: discord.Interaction):
        owner_id = self.values[0]

        conn = connect()
        cur = conn.cursor()
        cur.execute("""
            SELECT
                COALESCE(m.name, m.manager_name, m.discord_id) AS manager_name,
                COALESCE(m.club_name, r.team_name, fc.name) AS club_name,
                COALESCE(m.budget, 0) AS budget
            FROM managers m
            LEFT JOIN real_team_assignments r ON r.discord_id = m.discord_id
            LEFT JOIN fc26_clubs fc ON fc.assigned_to = m.discord_id
            WHERE m.discord_id = %s
            LIMIT 1
        """, (str(owner_id),))
        manager = cur.fetchone()

        cur.execute("""
            SELECT name, team, position, overall, sold_price
            FROM players
            WHERE owner_discord_id = %s
            ORDER BY
                CASE
                    WHEN UPPER(position) IN ('GK', 'POR') THEN 1
                    WHEN UPPER(position) IN ('CB','LB','RB','LWB','RWB','DC','TS','TD','DIF') THEN 2
                    WHEN UPPER(position) IN ('CDM','CM','CAM','LM','RM','MCO','CDC','CC','CEN') THEN 3
                    WHEN UPPER(position) IN ('ST','CF','LW','RW','LF','RF','ATT','AS','AD','P') THEN 4
                    ELSE 5
                END,
                overall DESC NULLS LAST,
                name ASC
        """, (str(owner_id),))
        players = cur.fetchall()
        conn.close()

        if not manager:
            await interaction.response.send_message("Manager non trovato.", ephemeral=True)
            return

        embed = discord.Embed(
            title=f"📋 Rosa {manager['club_name'] or manager['manager_name']}",
            description=(
                f"Manager: **{manager['manager_name']}**\n"
                f"Giocatori: **{len(players)}**\n"
                f"Budget residuo: **{manager['budget']} crediti**"
            ),
            color=discord.Color.green() if players else discord.Color.dark_grey()
        )

        if not players:
            embed.add_field(name="Giocatori", value="Questo club non ha giocatori assegnati.", inline=False)
        else:
            grouped = {
                "🧤 Portieri": [],
                "🛡️ Difensori": [],
                "🎯 Centrocampisti": [],
                "⚽ Attaccanti": [],
                "📌 Altro": []
            }

            for p in players:
                line = f"**{p['name']}** — {p['position']} • OVR {p['overall']} • {p['team']}"
                group = role_group(p["position"])
                if group == "GK":
                    grouped["🧤 Portieri"].append(line)
                elif group == "DEF":
                    grouped["🛡️ Difensori"].append(line)
                elif group == "MID":
                    grouped["🎯 Centrocampisti"].append(line)
                elif group == "ATT":
                    grouped["⚽ Attaccanti"].append(line)
                else:
                    grouped["📌 Altro"].append(line)

            for title, items in grouped.items():
                if not items:
                    continue
                for index, chunk in enumerate(split_discord_text("\n".join(items), 1000), start=1):
                    embed.add_field(
                        name=title if index == 1 else f"{title} {index}",
                        value=chunk,
                        inline=False
                    )

        await interaction.response.edit_message(embed=embed, view=self.view)


class RoseCampionatoView(discord.ui.View):
    def __init__(self, campionati):
        super().__init__(timeout=180)
        self.add_item(RoseCampionatoSelect(campionati))


class RoseClubView(discord.ui.View):
    def __init__(self, campionato, clubs):
        super().__init__(timeout=180)
        self.add_item(RoseClubSelect(campionato, clubs))


@tree.command(name="rose", description="Mostra campionati, club iscritti e rose")
async def rose(interaction: discord.Interaction):
    if not is_rose_channel(interaction):
        await interaction.response.send_message("❌ Usa questo comando solo nel canale ROSE.", ephemeral=True)
        return

    conn = connect()
    cur = conn.cursor()
    cur.execute("""
        SELECT
            COALESCE(league, 'Senza campionato') AS name,
            COUNT(*) AS club_count
        FROM fc26_clubs
        WHERE assigned_to IS NOT NULL
          AND TRIM(assigned_to) != ''
        GROUP BY COALESCE(league, 'Senza campionato')
        ORDER BY COALESCE(league, 'Senza campionato') ASC
    """)
    campionati = cur.fetchall()
    conn.close()

    if not campionati:
        await interaction.response.send_message("Nessun campionato con club iscritti trovato.", ephemeral=True)
        return

    embed = discord.Embed(
        title="📋 Rose campionati",
        description="Scegli un campionato, poi un club iscritto. Verranno mostrati solo i club già assegnati/iscritti.",
        color=discord.Color.green()
    )

    preview = [f"• **{c['name']}** — {c['club_count']} club iscritti" for c in campionati[:15]]
    embed.add_field(name="Campionati", value="\n".join(preview), inline=False)
    if len(campionati) > 25:
        embed.set_footer(text="Mostro solo i primi 25 campionati nella tendina per limite Discord.")

    await interaction.response.send_message(embed=embed, view=RoseCampionatoView(campionati), ephemeral=True)


@tree.command(name="mia_squadra", description="Mostra direttamente la tua squadra")
async def mia_squadra(interaction: discord.Interaction):
    if not is_rose_channel(interaction):
        await interaction.response.send_message("❌ Usa questo comando solo nel canale ROSE.", ephemeral=True)
        return

    embed = build_roster_embed(interaction.user.id, interaction.user.display_name)
    await interaction.response.send_message(embed=embed, ephemeral=True)




@tree.command(name="mercato", description="Mostra giocatori liberi filtrabili")
@app_commands.describe(ruolo="Ruolo, es. ST, CM, CB", overall_min="Overall minimo", overall_max="Overall massimo")
async def mercato(interaction: discord.Interaction, ruolo: str = None, overall_min: int = 0, overall_max: int = 99):
    await safe_defer(interaction, ephemeral=True, thinking=True)

    conn = connect()
    cur = conn.cursor()

    if ruolo:
        cur.execute("""
            SELECT *
            FROM players
            WHERE owner_discord_id IS NULL
              AND LOWER(position) = LOWER(%s)
              AND overall BETWEEN %s AND %s
            ORDER BY overall DESC
            LIMIT 15
        """, (ruolo, overall_min, overall_max))
    else:
        cur.execute("""
            SELECT *
            FROM players
            WHERE owner_discord_id IS NULL
              AND overall BETWEEN %s AND %s
            ORDER BY overall DESC
            LIMIT 15
        """, (overall_min, overall_max))

    rows = cur.fetchall()
    conn.close()

    if not rows:
        await interaction.followup.send("Nessun giocatore libero trovato con questi filtri.", ephemeral=True)
        return

    embed = discord.Embed(
        title="🛒 Mercato giocatori liberi",
        description="Top risultati disponibili.",
        color=discord.Color.blue()
    )

    for r in rows:
        embed.add_field(
            name=f"{r['name']} • ID {r['id']}",
            value=f"{r['position']} • {r['team']} • OVR **{r['overall']}**",
            inline=False
        )

    await interaction.followup.send(embed=embed, ephemeral=True)


@tree.command(name="top_acquisti", description="Mostra gli acquisti più costosi")
async def top_acquisti(interaction: discord.Interaction):
    if not is_rose_channel(interaction):
        await interaction.response.send_message("❌ Usa questo comando solo nel canale ROSE.", ephemeral=True)
        return

    conn = connect()
    cur = conn.cursor()
    cur.execute("""
        SELECT name, team, position, overall, sold_price, owner_discord_id
        FROM players
        WHERE sold_price IS NOT NULL
        ORDER BY sold_price DESC
        LIMIT 10
    """)
    rows = cur.fetchall()
    conn.close()

    if not rows:
        await interaction.response.send_message("Nessun acquisto registrato.")
        return

    embed = discord.Embed(title="💸 Top acquisti", color=discord.Color.gold())

    for i, r in enumerate(rows, start=1):
        embed.add_field(
            name=f"{i}. {r['name']} — {r['sold_price']} cr",
            value=f"{r['position']} • {r['team']} • OVR {r['overall']} • <@{r['owner_discord_id']}>",
            inline=False
        )

    await interaction.response.send_message(embed=embed)


@tree.command(name="classifica_budget", description="Classifica budget residuo")
async def classifica_budget(interaction: discord.Interaction):
    if not is_spam_channel(interaction):
        await interaction.response.send_message("❌ Usa questo comando solo nel canale SPAM-CHAT.", ephemeral=True)
        return

    conn = connect()
    cur = conn.cursor()
    cur.execute("""
        SELECT discord_id, name, budget
        FROM managers
        ORDER BY budget DESC
        LIMIT 20
    """)
    rows = cur.fetchall()
    conn.close()

    if not rows:
        await interaction.response.send_message("Nessun manager registrato.")
        return

    embed = discord.Embed(title="💰 Classifica budget", color=discord.Color.green())

    for i, r in enumerate(rows, start=1):
        embed.add_field(
            name=f"{i}. {r['name']}",
            value=f"Budget: **{r['budget']}** crediti",
            inline=False
        )

    await interaction.response.send_message(embed=embed)


@tree.command(name="team_rating", description="Mostra overall medio della rosa")
@app_commands.describe(utente="Manager da controllare")
async def team_rating(interaction: discord.Interaction, utente: discord.Member = None):
    if not is_rose_channel(interaction):
        await interaction.response.send_message("❌ Usa questo comando solo nel canale ROSE.", ephemeral=True)
        return

    target = utente or interaction.user

    conn = connect()
    cur = conn.cursor()
    cur.execute("""
        SELECT COUNT(*) AS total, AVG(overall) AS avg_ovr, MAX(overall) AS max_ovr
        FROM players
        WHERE owner_discord_id = %s
    """, (str(target.id),))
    row = cur.fetchone()
    conn.close()

    if not row or not row["total"]:
        await interaction.response.send_message(f"{target.display_name} non ha ancora giocatori.")
        return

    embed = discord.Embed(
        title=f"⭐ Team rating — {target.display_name}",
        color=discord.Color.gold()
    )
    embed.add_field(name="Giocatori", value=str(row["total"]), inline=True)
    embed.add_field(name="Overall medio", value=f"{row['avg_ovr']:.1f}", inline=True)
    embed.add_field(name="Miglior OVR", value=str(row["max_ovr"]), inline=True)

    await interaction.response.send_message(embed=embed)


@tree.command(name="svincola", description="Admin: svincola un giocatore")
@app_commands.describe(player_id="ID giocatore da svincolare")
async def svincola(interaction: discord.Interaction, player_id: str):
    if not is_admin(interaction):
        await interaction.response.send_message("❌ Solo gli admin possono usare questo comando.", ephemeral=True)
        return

    conn = connect()
    cur = conn.cursor()
    cur.execute("SELECT name, owner_discord_id, sold_price FROM players WHERE id = CAST(%s AS BIGINT)", (player_id,))
    player = cur.fetchone()

    if not player:
        conn.close()
        await interaction.response.send_message("Giocatore non trovato.", ephemeral=True)
        return

    if player["owner_discord_id"] and player["sold_price"]:
        cur.execute(
            "UPDATE managers SET budget = budget + %s WHERE discord_id = %s",
            (player["sold_price"], player["owner_discord_id"])
        )

    cur.execute("UPDATE players SET owner_discord_id = NULL, sold_price = NULL WHERE id = CAST(%s AS BIGINT)", (player_id,))
    conn.commit()
    conn.close()

    await interaction.response.send_message(f"✅ **{player['name']}** svincolato. Budget rimborsato.")


@tree.command(name="assegna", description="Admin: assegna manualmente un giocatore")
@app_commands.describe(player_id="ID giocatore", utente="Utente a cui assegnare", prezzo="Prezzo assegnazione")
async def assegna(interaction: discord.Interaction, player_id: str, utente: discord.Member, prezzo: int):
    if not is_admin(interaction):
        await interaction.response.send_message("❌ Solo gli admin possono usare questo comando.", ephemeral=True)
        return

    conn = connect()
    cur = conn.cursor()

    cur.execute("SELECT * FROM players WHERE id = CAST(%s AS BIGINT)", (player_id,))
    player = cur.fetchone()

    if not player:
        conn.close()
        await interaction.response.send_message("Giocatore non trovato.", ephemeral=True)
        return

    cur.execute("SELECT * FROM managers WHERE discord_id = %s", (str(utente.id),))
    manager = cur.fetchone()

    if not manager:
        conn.close()
        await interaction.response.send_message("L'utente deve prima usare `/registrami`.", ephemeral=True)
        return

    if safe_int(manager["budget"]) < prezzo:
        conn.close()
        await interaction.response.send_message("Budget insufficiente per questo utente.", ephemeral=True)
        return

    cur.execute("UPDATE managers SET budget = budget - %s WHERE discord_id = %s", (prezzo, str(utente.id)))
    cur.execute("UPDATE players SET owner_discord_id = %s, sold_price = %s WHERE id = CAST(%s AS BIGINT)", (str(utente.id), prezzo, player_id))
    conn.commit()
    conn.close()

    await interaction.response.send_message(f"✅ **{player['name']}** assegnato a **{utente.display_name}** per **{prezzo}** crediti.")


@tree.command(name="pack_gold", description="Admin: assegna un pack gold casuale a un utente")
@app_commands.describe(utente="Utente che riceve il pack", numero="Numero giocatori da assegnare")
async def pack_gold(interaction: discord.Interaction, utente: discord.Member, numero: int = 3):
    if not is_admin(interaction):
        await interaction.response.send_message("❌ Solo gli admin possono usare questo comando.", ephemeral=True)
        return

    numero = max(1, min(numero, 5))

    conn = connect()
    cur = conn.cursor()

    cur.execute("SELECT * FROM managers WHERE discord_id = %s", (str(utente.id),))
    manager = cur.fetchone()

    if not manager:
        conn.close()
        await interaction.response.send_message("L'utente deve prima usare `/registrami`.", ephemeral=True)
        return

    cur.execute("""
        SELECT *
        FROM players
        WHERE owner_discord_id IS NULL
          AND overall BETWEEN 75 AND 84
        ORDER BY RANDOM()
        LIMIT %s
    """, (numero,))
    players = cur.fetchall()

    if not players:
        conn.close()
        await interaction.response.send_message("Non ci sono abbastanza giocatori liberi per il pack.", ephemeral=True)
        return

    for p in players:
        cur.execute(
            "UPDATE players SET owner_discord_id = %s, sold_price = %s WHERE id = CAST(%s AS BIGINT)",
            (str(utente.id), 0, p["id"])
        )

    conn.commit()
    conn.close()

    embed = discord.Embed(
        title="🎁 Pack Gold assegnato",
        description=f"Admin ha assegnato un pack a **{utente.display_name}**.",
        color=discord.Color.gold()
    )

    for p in players:
        embed.add_field(
            name=f"{p['name']} • OVR {p['overall']}",
            value=f"{p['position']} • {p['team']}",
            inline=False
        )

    await interaction.response.send_message(embed=embed)



def free_players_embed(title, groups, limit=15):
    embed = discord.Embed(
        title=title,
        description="Lista dei migliori giocatori liberi. Usa l'ID per avviare un'asta.",
        color=discord.Color.blue()
    )

    conn = connect()
    cur = conn.cursor()

    placeholders = ",".join(["?"] * len(groups))
    query = f"""
        SELECT *
        FROM players
        WHERE owner_discord_id IS NULL
          AND UPPER(position) IN ({placeholders})
        ORDER BY overall DESC
        LIMIT ?
    """

    cur.execute(query, [g.upper() for g in groups] + [limit])
    rows = cur.fetchall()
    conn.close()

    if not rows:
        embed.add_field(name="Nessun giocatore", value="Non ci sono giocatori liberi per questo ruolo.", inline=False)
        return embed

    for i, r in enumerate(rows, start=1):
        embed.add_field(
            name=f"{i}. {r['name']} • ID {r['id']}",
            value=f"{r['position']} • {r['team']} • OVR **{r['overall']}**",
            inline=False
        )

    return embed


class LiberiSelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(
                label="Portieri",
                value="gk",
                emoji="🧤",
                description="Mostra i migliori portieri liberi"
            ),
            discord.SelectOption(
                label="Difensori",
                value="def",
                emoji="🛡️",
                description="Mostra i migliori difensori liberi"
            ),
            discord.SelectOption(
                label="Centrocampisti",
                value="mid",
                emoji="🎯",
                description="Mostra i migliori centrocampisti liberi"
            ),
            discord.SelectOption(
                label="Attaccanti",
                value="att",
                emoji="⚽",
                description="Mostra i migliori attaccanti liberi"
            ),
        ]

        super().__init__(
            placeholder="Scegli un ruolo...",
            min_values=1,
            max_values=1,
            options=options,
            custom_id="liberi_select_role"
        )

    async def callback(self, interaction: discord.Interaction):
        value = self.values[0]

        if value == "gk":
            embed = free_players_embed("🧤 Portieri liberi", ["GK", "POR"])
        elif value == "def":
            embed = free_players_embed("🛡️ Difensori liberi", ["CB", "LB", "RB", "LWB", "RWB", "DC", "TS", "TD", "DIF"])
        elif value == "mid":
            embed = free_players_embed("🎯 Centrocampisti liberi", ["CDM", "CM", "CAM", "LM", "RM", "MCO", "CDC", "CC", "CEN"])
        elif value == "att":
            embed = free_players_embed("⚽ Attaccanti liberi", ["ST", "CF", "LW", "RW", "LF", "RF", "ATT", "AS", "AD", "P"])
        else:
            await interaction.response.send_message("Ruolo non valido.", ephemeral=True)
            return

        await interaction.response.edit_message(embed=embed, view=self.view)


class LiberiView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=180)
        self.add_item(LiberiSelect())


@tree.command(name="liberi", description="Mostra i giocatori liberi divisi per ruolo")
async def liberi(interaction: discord.Interaction):
    if not is_search_channel(interaction):
        await interaction.response.send_message(
            "❌ Puoi usare `/liberi` solo nel canale dedicato alla ricerca giocatori.",
            ephemeral=True
        )
        return

    embed = discord.Embed(
        title="🛒 Giocatori liberi",
        description="Scegli un ruolo dalla tendina qui sotto.",
        color=discord.Color.blue()
    )
    embed.add_field(name="Disponibili", value="🧤 Portieri\\n🛡️ Difensori\\n🎯 Centrocampisti\\n⚽ Attaccanti", inline=False)
    embed.set_footer(text="La lista mostra i migliori 15 liberi per ruolo.")

    await interaction.response.send_message(embed=embed, view=LiberiView(), ephemeral=True)




@tree.command(name="rosa_grafica", description="Genera una rosa grafica stile FUT")
@app_commands.describe(utente="Manager da visualizzare")
async def rosa_grafica(interaction: discord.Interaction, utente: discord.Member = None):
    if not is_rose_channel(interaction):
        await interaction.response.send_message("❌ Usa questo comando solo nel canale ROSE.", ephemeral=True)
        return

    await interaction.response.defer()

    target = utente or interaction.user
    image_path = generate_roster_graphic(target.id, target.display_name)
    file = discord.File(str(image_path), filename="rosa_grafica.png")

    embed = discord.Embed(
        title=f"🖼️ Rosa grafica di {target.display_name}",
        color=discord.Color.green()
    )
    embed.set_image(url="attachment://rosa_grafica.png")

    await interaction.followup.send(embed=embed, file=file)


class StoricoSelect(discord.ui.Select):
    def __init__(self, managers):
        options = []
        for manager in managers[:25]:
            options.append(
                discord.SelectOption(
                    label=manager["name"][:100],
                    value=str(manager["discord_id"]),
                    description=f"Budget: {manager['budget']} crediti"
                )
            )

        super().__init__(
            placeholder="Scegli un manager...",
            min_values=1,
            max_values=1,
            options=options,
            custom_id="storico_select_manager"
        )

    async def callback(self, interaction: discord.Interaction):
        manager_id = self.values[0]

        conn = connect()
        cur = conn.cursor()
        cur.execute("SELECT name, budget FROM managers WHERE discord_id = %s", (manager_id,))
        manager = cur.fetchone()

        cur.execute("""
            SELECT player_name, price, source, created_at
            FROM transfer_history
            WHERE manager_id = %s
            ORDER BY id DESC
            LIMIT 15
        """, (manager_id,))
        rows = cur.fetchall()

        cur.execute("""
            SELECT COUNT(*) AS total, AVG(overall) AS avg_ovr, SUM(sold_price) AS spent
            FROM players
            WHERE owner_discord_id = %s
        """, (manager_id,))
        summary = cur.fetchone()
        conn.close()

        if not manager:
            await interaction.response.send_message("Manager non trovato.", ephemeral=True)
            return

        embed = discord.Embed(
            title=f"📜 Storico di {manager['name']}",
            color=discord.Color.blue()
        )
        embed.add_field(name="Budget", value=f"{manager['budget']} crediti", inline=True)
        embed.add_field(name="Giocatori rosa", value=str(summary["total"] or 0), inline=True)
        embed.add_field(name="OVR medio", value=f"{(summary['avg_ovr'] or 0):.1f}", inline=True)
        embed.add_field(name="Speso totale", value=f"{safe_int(summary['spent'])} crediti", inline=True)

        if rows:
            lines = []
            for r in rows:
                lines.append(f"• **{r['player_name']}** — {r['price']} cr — {r['source']}")
            embed.add_field(name="Ultimi movimenti", value="\n".join(lines), inline=False)
        else:
            embed.add_field(name="Ultimi movimenti", value="Nessun movimento registrato.", inline=False)

        await interaction.response.edit_message(embed=embed, view=self.view)


class StoricoView(discord.ui.View):
    def __init__(self, managers):
        super().__init__(timeout=180)
        self.add_item(StoricoSelect(managers))


@tree.command(name="storico", description="Mostra lo storico mercato scegliendo un manager")
async def storico(interaction: discord.Interaction):
    if not is_rose_channel(interaction):
        await interaction.response.send_message("❌ Usa questo comando solo nel canale ROSE.", ephemeral=True)
        return

    conn = connect()
    cur = conn.cursor()
    cur.execute("""
        SELECT discord_id, name, budget
        FROM managers
        ORDER BY name ASC
    """)
    managers = cur.fetchall()
    conn.close()

    if not managers:
        await interaction.response.send_message("Nessun manager registrato.", ephemeral=True)
        return

    embed = discord.Embed(
        title="📜 Storico mercato",
        description="Scegli un manager dalla tendina.",
        color=discord.Color.blue()
    )

    await interaction.response.send_message(embed=embed, view=StoricoView(managers), ephemeral=True)


# ================= SISTEMA SCAMBI GUIDATO =================

TRADE_PAGE_SIZE = 25


def trade_player_line(player):
    return f"**{player['name']}** — {player['position']} • OVR {player['overall']} • {player['team']}"


def fetch_trade_managers(exclude_discord_id=None):
    conn = connect()
    cur = conn.cursor()
    cur.execute("""
        SELECT
            m.discord_id,
            COALESCE(m.name, m.manager_name, m.discord_id) AS manager_name,
            COALESCE(m.club_name, r.team_name, c.name, 'Club non impostato') AS club_name,
            COALESCE(m.budget, 0) AS budget
        FROM managers m
        LEFT JOIN real_team_assignments r ON r.discord_id = m.discord_id
        LEFT JOIN fc26_clubs c ON c.assigned_to = m.discord_id
        WHERE m.discord_id IS NOT NULL
          AND m.discord_id != ''
          AND (%s IS NULL OR m.discord_id != %s)
        ORDER BY club_name ASC, manager_name ASC
    """, (str(exclude_discord_id) if exclude_discord_id else None, str(exclude_discord_id) if exclude_discord_id else None))
    rows = cur.fetchall()
    conn.close()
    return rows


def fetch_manager_info(discord_id):
    conn = connect()
    cur = conn.cursor()
    cur.execute("""
        SELECT
            m.discord_id,
            COALESCE(m.name, m.manager_name, m.discord_id) AS manager_name,
            COALESCE(m.club_name, r.team_name, c.name, 'Club non impostato') AS club_name,
            COALESCE(m.budget, 0) AS budget
        FROM managers m
        LEFT JOIN real_team_assignments r ON r.discord_id = m.discord_id
        LEFT JOIN fc26_clubs c ON c.assigned_to = m.discord_id
        WHERE m.discord_id = %s
        LIMIT 1
    """, (str(discord_id),))
    row = cur.fetchone()
    conn.close()
    return row


def fetch_roster_players(owner_id):
    conn = connect()
    cur = conn.cursor()
    cur.execute("""
        SELECT id, name, team, position, overall, owner_discord_id, sold_price
        FROM players
        WHERE owner_discord_id = %s
        ORDER BY overall DESC NULLS LAST, name ASC
    """, (str(owner_id),))
    rows = cur.fetchall()
    conn.close()
    return rows


def fetch_player_by_id(player_id):
    if not player_id:
        return None
    conn = connect()
    cur = conn.cursor()
    cur.execute("""
        SELECT id, name, team, position, overall, owner_discord_id
        FROM players
        WHERE id = CAST(%s AS BIGINT)
        LIMIT 1
    """, (str(player_id),))
    row = cur.fetchone()
    conn.close()
    return row


def make_trade_embed(trade_id, proposer_id, target_id, request_player_id, offer_player_id=None, credits_to_target=0, title="🔁 Nuova proposta di scambio"):
    proposer = fetch_manager_info(proposer_id)
    target = fetch_manager_info(target_id)
    requested = fetch_player_by_id(request_player_id)
    offered = fetch_player_by_id(offer_player_id) if offer_player_id else None

    proposer_name = proposer['manager_name'] if proposer else str(proposer_id)
    proposer_club = proposer['club_name'] if proposer else 'Club non impostato'
    target_name = target['manager_name'] if target else str(target_id)
    target_club = target['club_name'] if target else 'Club non impostato'

    embed = discord.Embed(
        title=title,
        description=(
            f"Offerta ID: **{trade_id}**\n\n"
            f"📤 Proponente: <@{proposer_id}> — **{proposer_club}**\n"
            f"📥 Destinatario: <@{target_id}> — **{target_club}**"
        ),
        color=discord.Color.orange()
    )

    embed.add_field(
        name="🎯 Giocatore richiesto",
        value=trade_player_line(requested) if requested else "Giocatore non trovato",
        inline=False
    )
    embed.add_field(
        name="🔄 Giocatore offerto",
        value=trade_player_line(offered) if offered else "Nessun giocatore offerto",
        inline=False
    )
    embed.add_field(
        name="💰 Budget offerto",
        value=f"**{safe_int(credits_to_target)} crediti**",
        inline=True
    )
    embed.set_footer(text="Il destinatario può accettare, rifiutare o fare una controfferta.")
    return embed


async def notify_trade_dm(target_id, request_player_name):
    try:
        user = await bot.fetch_user(int(target_id))
        await user.send(
            f"🔁 Ti è stata fatta un'offerta per **{request_player_name}**. "
            f"Vai a vedere nel canale aste/scambi per accettare, rifiutare o fare una controfferta."
        )
    except Exception:
        pass


async def post_trade_offer(interaction, trade_id, proposer_id, target_id, request_player_id, offer_player_id, credits_to_target, *, title="🔁 Nuova proposta di scambio"):
    embed = make_trade_embed(
        trade_id,
        proposer_id,
        target_id,
        request_player_id,
        offer_player_id,
        credits_to_target,
        title=title
    )

    channel = None
    for cid in (SCAMBI_CHANNEL_ID, AUCTION_CHANNEL_ID):
        try:
            channel = interaction.guild.get_channel(int(cid)) if interaction.guild else None
            if not channel:
                channel = await bot.fetch_channel(int(cid))
            if channel:
                break
        except Exception:
            channel = None

    if channel:
        await channel.send(embed=embed, view=TradeView(trade_id))

    requested = fetch_player_by_id(request_player_id)
    await notify_trade_dm(target_id, requested['name'] if requested else 'un tuo giocatore')


class TradeManagerSelect(discord.ui.Select):
    def __init__(self, managers, page=0):
        self.managers = managers
        self.page = page
        start = page * TRADE_PAGE_SIZE
        chunk = managers[start:start + TRADE_PAGE_SIZE]

        options = []
        for manager in chunk:
            club = str(manager['club_name'] or 'Club non impostato')
            name = str(manager['manager_name'] or manager['discord_id'])
            options.append(discord.SelectOption(
                label=f"@{name}"[:100],
                description=f"Club: {club}"[:100],
                value=str(manager['discord_id'])
            ))

        super().__init__(placeholder="Scegli il player/manager con cui trattare...", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        target_id = self.values[0]
        roster = fetch_roster_players(target_id)
        target = fetch_manager_info(target_id)

        if not roster:
            await interaction.response.send_message("Questo player non ha giocatori in rosa.", ephemeral=True)
            return

        embed = discord.Embed(
            title=f"🔁 Scambio con @{target['manager_name'] if target else target_id}",
            description=(
                f"Club: **{target['club_name'] if target else 'Club non impostato'}**\n"
                "Scegli il giocatore che vuoi chiedere."
            ),
            color=discord.Color.blurple()
        )
        embed.add_field(
            name="Rosa disponibile",
            value="\n".join(f"• {trade_player_line(p)}" for p in roster[:15]),
            inline=False
        )
        if len(roster) > 15:
            embed.set_footer(text=f"Mostrati 15 giocatori su {len(roster)}. Nel menu li trovi tutti, divisi a pagine se necessario.")

        await interaction.response.edit_message(embed=embed, view=TradeTargetPlayerView(interaction.user.id, target_id, roster, page=0))


class TradeManagerView(discord.ui.View):
    def __init__(self, requester_id, managers, page=0):
        super().__init__(timeout=300)
        self.requester_id = str(requester_id)
        self.managers = managers
        self.page = page
        self.max_page = max(0, (len(managers) - 1) // TRADE_PAGE_SIZE)
        self.add_item(TradeManagerSelect(managers, page))

    @discord.ui.button(label="⬅️", style=discord.ButtonStyle.secondary)
    async def previous_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        if str(interaction.user.id) != self.requester_id:
            await interaction.response.send_message("Questo menu non è tuo.", ephemeral=True)
            return
        new_page = max(0, self.page - 1)
        await interaction.response.edit_message(view=TradeManagerView(self.requester_id, self.managers, new_page))

    @discord.ui.button(label="➡️", style=discord.ButtonStyle.secondary)
    async def next_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        if str(interaction.user.id) != self.requester_id:
            await interaction.response.send_message("Questo menu non è tuo.", ephemeral=True)
            return
        new_page = min(self.max_page, self.page + 1)
        await interaction.response.edit_message(view=TradeManagerView(self.requester_id, self.managers, new_page))


class TradeTargetPlayerSelect(discord.ui.Select):
    def __init__(self, proposer_id, target_id, players, page=0):
        self.proposer_id = str(proposer_id)
        self.target_id = str(target_id)
        self.players = players
        self.page = page
        chunk = players[page * TRADE_PAGE_SIZE:(page + 1) * TRADE_PAGE_SIZE]
        options = [discord.SelectOption(
            label=str(p['name'])[:100],
            description=f"{p['position']} • OVR {p['overall']} • {p['team']}"[:100],
            value=str(p['id'])
        ) for p in chunk]
        super().__init__(placeholder="Scegli il giocatore che vuoi dall'altra rosa...", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        if str(interaction.user.id) != self.proposer_id:
            await interaction.response.send_message("Questo menu non è tuo.", ephemeral=True)
            return

        request_player_id = self.values[0]
        own_roster = fetch_roster_players(self.proposer_id)
        if not own_roster:
            await interaction.response.send_message("Non hai giocatori in rosa da usare per uno scambio.", ephemeral=True)
            return

        requested = fetch_player_by_id(request_player_id)
        embed = discord.Embed(
            title="🔁 Crea offerta",
            description=(
                f"Giocatore richiesto: **{requested['name'] if requested else request_player_id}**\n\n"
                "Ora scegli il giocatore da offrire oppure scegli **Nessun giocatore**. Dopo ti verrà chiesto il budget."
            ),
            color=discord.Color.orange()
        )
        await interaction.response.edit_message(embed=embed, view=TradeOwnPlayerView(self.proposer_id, self.target_id, request_player_id, own_roster, page=0))


class TradeTargetPlayerView(discord.ui.View):
    def __init__(self, proposer_id, target_id, players, page=0):
        super().__init__(timeout=300)
        self.proposer_id = str(proposer_id)
        self.target_id = str(target_id)
        self.players = players
        self.page = page
        self.max_page = max(0, (len(players) - 1) // TRADE_PAGE_SIZE)
        self.add_item(TradeTargetPlayerSelect(proposer_id, target_id, players, page))

    @discord.ui.button(label="⬅️", style=discord.ButtonStyle.secondary)
    async def previous_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        if str(interaction.user.id) != self.proposer_id:
            await interaction.response.send_message("Questo menu non è tuo.", ephemeral=True)
            return
        await interaction.response.edit_message(view=TradeTargetPlayerView(self.proposer_id, self.target_id, self.players, max(0, self.page - 1)))

    @discord.ui.button(label="➡️", style=discord.ButtonStyle.secondary)
    async def next_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        if str(interaction.user.id) != self.proposer_id:
            await interaction.response.send_message("Questo menu non è tuo.", ephemeral=True)
            return
        await interaction.response.edit_message(view=TradeTargetPlayerView(self.proposer_id, self.target_id, self.players, min(self.max_page, self.page + 1)))


class TradeOwnPlayerSelect(discord.ui.Select):
    def __init__(self, proposer_id, target_id, request_player_id, players, page=0):
        self.proposer_id = str(proposer_id)
        self.target_id = str(target_id)
        self.request_player_id = str(request_player_id)
        self.players = players
        self.page = page
        chunk = players[page * (TRADE_PAGE_SIZE - 1):page * (TRADE_PAGE_SIZE - 1) + (TRADE_PAGE_SIZE - 1)]
        options = [discord.SelectOption(label="Nessun giocatore", description="Offri solo budget", value="none")]
        options.extend(discord.SelectOption(
            label=str(p['name'])[:100],
            description=f"{p['position']} • OVR {p['overall']} • {p['team']}"[:100],
            value=str(p['id'])
        ) for p in chunk)
        super().__init__(placeholder="Scegli il tuo giocatore da offrire...", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        if str(interaction.user.id) != self.proposer_id:
            await interaction.response.send_message("Questo menu non è tuo.", ephemeral=True)
            return
        offer_player_id = None if self.values[0] == "none" else self.values[0]
        await interaction.response.send_modal(TradeBudgetModal(self.proposer_id, self.target_id, self.request_player_id, offer_player_id))


class TradeOwnPlayerView(discord.ui.View):
    def __init__(self, proposer_id, target_id, request_player_id, players, page=0):
        super().__init__(timeout=300)
        self.proposer_id = str(proposer_id)
        self.target_id = str(target_id)
        self.request_player_id = str(request_player_id)
        self.players = players
        self.page = page
        self.max_page = max(0, (len(players) - 1) // (TRADE_PAGE_SIZE - 1))
        self.add_item(TradeOwnPlayerSelect(proposer_id, target_id, request_player_id, players, page))

    @discord.ui.button(label="⬅️", style=discord.ButtonStyle.secondary)
    async def previous_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        if str(interaction.user.id) != self.proposer_id:
            await interaction.response.send_message("Questo menu non è tuo.", ephemeral=True)
            return
        await interaction.response.edit_message(view=TradeOwnPlayerView(self.proposer_id, self.target_id, self.request_player_id, self.players, max(0, self.page - 1)))

    @discord.ui.button(label="➡️", style=discord.ButtonStyle.secondary)
    async def next_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        if str(interaction.user.id) != self.proposer_id:
            await interaction.response.send_message("Questo menu non è tuo.", ephemeral=True)
            return
        await interaction.response.edit_message(view=TradeOwnPlayerView(self.proposer_id, self.target_id, self.request_player_id, self.players, min(self.max_page, self.page + 1)))


class TradeBudgetModal(discord.ui.Modal, title="Budget offerto"):
    budget = discord.ui.TextInput(label="Budget da offrire", placeholder="Scrivi 0 se offri solo un giocatore", required=True, max_length=8)

    def __init__(self, proposer_id, target_id, request_player_id, offer_player_id=None):
        super().__init__()
        self.proposer_id = str(proposer_id)
        self.target_id = str(target_id)
        self.request_player_id = str(request_player_id)
        self.offer_player_id = str(offer_player_id) if offer_player_id else None

    async def on_submit(self, interaction: discord.Interaction):
        raw_budget = str(self.budget.value).strip()
        if not raw_budget.isdigit():
            await interaction.response.send_message("Il budget deve essere un numero intero.", ephemeral=True)
            return
        credits = int(raw_budget)

        if not self.offer_player_id and credits <= 0:
            await interaction.response.send_message("Devi offrire almeno un giocatore o un budget maggiore di 0.", ephemeral=True)
            return

        proposer = fetch_manager_info(self.proposer_id)
        if not proposer:
            await interaction.response.send_message("Non sei registrato come manager.", ephemeral=True)
            return
        if safe_int(proposer['budget']) < credits:
            await interaction.response.send_message("Budget insufficiente.", ephemeral=True)
            return

        conn = connect()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO trade_offers
            (proposer_id, proposer_name, target_id, target_name, offer_player_id, request_player_id, credits_to_target, credits_to_proposer, status)
            VALUES (%s, %s, %s, %s, %s, %s, %s, 0, 'pending')
            RETURNING id
        """, (
            self.proposer_id,
            interaction.user.display_name,
            self.target_id,
            str(self.target_id),
            self.offer_player_id,
            self.request_player_id,
            credits
        ))
        row = cur.fetchone()
        trade_id = row['id'] if isinstance(row, dict) or hasattr(row, 'get') else row[0]
        conn.commit()
        conn.close()

        await post_trade_offer(interaction, trade_id, self.proposer_id, self.target_id, self.request_player_id, self.offer_player_id, credits)
        await interaction.response.send_message("✅ Offerta inviata nel canale scambi/aste.", ephemeral=True)


class CounterOfferModal(discord.ui.Modal, title="Controfferta"):
    budget = discord.ui.TextInput(label="Budget richiesto", placeholder="Esempio: 50 oppure 0", required=True, max_length=8)

    def __init__(self, trade_id, selected_offer_player_id):
        super().__init__()
        self.trade_id = int(trade_id)
        self.selected_offer_player_id = None if selected_offer_player_id == "none" else str(selected_offer_player_id)

    async def on_submit(self, interaction: discord.Interaction):
        raw_budget = str(self.budget.value).strip()
        if not raw_budget.isdigit():
            await interaction.response.send_message("Il budget deve essere un numero intero.", ephemeral=True)
            return
        credits = int(raw_budget)

        conn = connect()
        cur = conn.cursor()
        cur.execute("SELECT * FROM trade_offers WHERE id = %s AND status = 'pending'", (self.trade_id,))
        trade = cur.fetchone()
        if not trade:
            conn.close()
            await interaction.response.send_message("Scambio non trovato o già concluso.", ephemeral=True)
            return
        if str(interaction.user.id) != str(trade['target_id']):
            conn.close()
            await interaction.response.send_message("Solo il destinatario può fare una controfferta.", ephemeral=True)
            return

        proposer = fetch_manager_info(trade['proposer_id'])
        if proposer and safe_int(proposer['budget']) < credits:
            conn.close()
            await interaction.response.send_message("Il proponente non ha abbastanza budget per questa controfferta.", ephemeral=True)
            return

        cur.execute("""
            UPDATE trade_offers
            SET offer_player_id = %s,
                credits_to_target = %s,
                credits_to_proposer = 0,
                status = 'pending'
            WHERE id = %s
        """, (self.selected_offer_player_id, credits, self.trade_id))
        conn.commit()
        conn.close()

        await post_trade_offer(
            interaction,
            self.trade_id,
            trade['proposer_id'],
            trade['target_id'],
            trade['request_player_id'],
            self.selected_offer_player_id,
            credits,
            title="🔁 Controfferta proposta"
        )
        await interaction.response.send_message("✅ Controfferta pubblicata.", ephemeral=True)


class CounterOfferPlayerSelect(discord.ui.Select):
    def __init__(self, trade_id, proposer_players):
        self.trade_id = int(trade_id)
        options = [discord.SelectOption(label="Nessun giocatore", description="Chiedo solo più budget", value="none")]
        options.extend(discord.SelectOption(
            label=str(p['name'])[:100],
            description=f"{p['position']} • OVR {p['overall']} • {p['team']}"[:100],
            value=str(p['id'])
        ) for p in proposer_players[:24])
        super().__init__(placeholder="Scegli il giocatore che vuoi chiedere in controfferta...", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_modal(CounterOfferModal(self.trade_id, self.values[0]))


class CounterOfferView(discord.ui.View):
    def __init__(self, trade_id, proposer_players):
        super().__init__(timeout=180)
        self.add_item(CounterOfferPlayerSelect(trade_id, proposer_players))


class TradeView(discord.ui.View):
    def __init__(self, trade_id):
        super().__init__(timeout=86400)
        self.trade_id = trade_id

    @discord.ui.button(label="ACCETTA", style=discord.ButtonStyle.success)
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button):
        conn = connect()
        cur = conn.cursor()

        cur.execute("SELECT * FROM trade_offers WHERE id = %s AND status = 'pending'", (self.trade_id,))
        trade = cur.fetchone()

        if not trade:
            conn.close()
            await interaction.response.send_message("Scambio non trovato o già concluso.", ephemeral=True)
            return

        if str(interaction.user.id) != str(trade["target_id"]):
            conn.close()
            await interaction.response.send_message("Solo il destinatario dello scambio può accettare.", ephemeral=True)
            return

        proposer_id = str(trade["proposer_id"])
        target_id = str(trade["target_id"])
        offer_player_id = trade["offer_player_id"]
        request_player_id = trade["request_player_id"]
        credits_to_target = safe_int(trade["credits_to_target"])
        credits_to_proposer = safe_int(trade["credits_to_proposer"])

        if offer_player_id:
            cur.execute("SELECT name, owner_discord_id FROM players WHERE id = CAST(%s AS BIGINT)", (offer_player_id,))
            p = cur.fetchone()
            if not p or str(p["owner_discord_id"]) != proposer_id:
                conn.close()
                await interaction.response.send_message("Scambio non valido: il proponente non possiede più il giocatore offerto.", ephemeral=True)
                return

        if request_player_id:
            cur.execute("SELECT name, owner_discord_id FROM players WHERE id = CAST(%s AS BIGINT)", (request_player_id,))
            p = cur.fetchone()
            if not p or str(p["owner_discord_id"]) != target_id:
                conn.close()
                await interaction.response.send_message("Scambio non valido: non possiedi più il giocatore richiesto.", ephemeral=True)
                return

        cur.execute("SELECT budget FROM managers WHERE discord_id = %s", (proposer_id,))
        proposer = cur.fetchone()
        cur.execute("SELECT budget FROM managers WHERE discord_id = %s", (target_id,))
        target = cur.fetchone()

        if not proposer or not target:
            conn.close()
            await interaction.response.send_message("Uno dei due utenti non è registrato.", ephemeral=True)
            return

        tax_target = int((credits_to_target * MARKET_TAX) / 100)
        tax_proposer = int((credits_to_proposer * MARKET_TAX) / 100)

        if safe_int(proposer["budget"]) < credits_to_target + tax_target:
            conn.close()
            await interaction.response.send_message("Scambio non valido: il proponente non ha abbastanza crediti.", ephemeral=True)
            return

        if safe_int(target["budget"]) < credits_to_proposer + tax_proposer:
            conn.close()
            await interaction.response.send_message("Scambio non valido: non hai abbastanza crediti.", ephemeral=True)
            return

        if credits_to_target:
            cur.execute("UPDATE managers SET budget = budget - %s WHERE discord_id = %s", (credits_to_target + tax_target, proposer_id))
            cur.execute("UPDATE managers SET budget = budget + %s WHERE discord_id = %s", (credits_to_target, target_id))

        if credits_to_proposer:
            cur.execute("UPDATE managers SET budget = budget - %s WHERE discord_id = %s", (credits_to_proposer + tax_proposer, target_id))
            cur.execute("UPDATE managers SET budget = budget + %s WHERE discord_id = %s", (credits_to_proposer, proposer_id))

        if offer_player_id:
            cur.execute("UPDATE players SET owner_discord_id = %s WHERE id = CAST(%s AS BIGINT)", (target_id, offer_player_id))

        if request_player_id:
            cur.execute("UPDATE players SET owner_discord_id = %s WHERE id = CAST(%s AS BIGINT)", (proposer_id, request_player_id))

        cur.execute("UPDATE trade_offers SET status = 'accepted' WHERE id = %s", (self.trade_id,))
        conn.commit()
        conn.close()

        await interaction.response.edit_message(content="✅ Scambio accettato e completato.", embed=None, view=None)

    @discord.ui.button(label="RIFIUTA", style=discord.ButtonStyle.danger)
    async def reject(self, interaction: discord.Interaction, button: discord.ui.Button):
        conn = connect()
        cur = conn.cursor()

        cur.execute("SELECT * FROM trade_offers WHERE id = %s AND status = 'pending'", (self.trade_id,))
        trade = cur.fetchone()

        if not trade:
            conn.close()
            await interaction.response.send_message("Scambio non trovato o già concluso.", ephemeral=True)
            return

        if str(interaction.user.id) != str(trade["target_id"]):
            conn.close()
            await interaction.response.send_message("Solo il destinatario dello scambio può rifiutare.", ephemeral=True)
            return

        cur.execute("UPDATE trade_offers SET status = 'rejected' WHERE id = %s", (self.trade_id,))
        conn.commit()
        conn.close()

        await interaction.response.edit_message(content="❌ Scambio rifiutato.", embed=None, view=None)

    @discord.ui.button(label="CONTROFFERTA", style=discord.ButtonStyle.primary)
    async def counter(self, interaction: discord.Interaction, button: discord.ui.Button):
        conn = connect()
        cur = conn.cursor()
        cur.execute("SELECT * FROM trade_offers WHERE id = %s AND status = 'pending'", (self.trade_id,))
        trade = cur.fetchone()
        conn.close()

        if not trade:
            await interaction.response.send_message("Scambio non trovato o già concluso.", ephemeral=True)
            return
        if str(interaction.user.id) != str(trade['target_id']):
            await interaction.response.send_message("Solo il destinatario può fare una controfferta.", ephemeral=True)
            return

        proposer_players = fetch_roster_players(trade['proposer_id'])
        if not proposer_players:
            await interaction.response.send_message("Il proponente non ha giocatori in rosa. Puoi chiedere solo budget scegliendo Nessun giocatore.", ephemeral=True)
            proposer_players = []

        await interaction.response.send_message(
            "Scegli il giocatore che vuoi chiedere nella controfferta, poi inserisci il budget richiesto.",
            view=CounterOfferView(self.trade_id, proposer_players),
            ephemeral=True
        )


@tree.command(name="scambi", description="Crea una proposta di scambio guidata")
async def scambi(interaction: discord.Interaction):
    if not is_scambi_channel(interaction):
        await interaction.response.send_message("❌ Usa questo comando solo nel canale SCAMBI.", ephemeral=True)
        return

    requester = fetch_manager_info(interaction.user.id)
    if not requester:
        await interaction.response.send_message("Prima devi essere registrato/iscritto come manager.", ephemeral=True)
        return

    managers = fetch_trade_managers(exclude_discord_id=interaction.user.id)
    if not managers:
        await interaction.response.send_message("Non ci sono altri player disponibili per gli scambi.", ephemeral=True)
        return

    description_lines = []
    for m in managers[:TRADE_PAGE_SIZE]:
        description_lines.append(f"• <@{m['discord_id']}> — **{m['club_name']}**")

    embed = discord.Embed(
        title="🔁 Scambi",
        description="Scegli il player/manager con cui vuoi trattare.\n\n" + "\n".join(description_lines),
        color=discord.Color.blurple()
    )
    if len(managers) > TRADE_PAGE_SIZE:
        embed.set_footer(text=f"Pagina 1 di {(len(managers) - 1) // TRADE_PAGE_SIZE + 1}")

    await interaction.response.send_message(embed=embed, view=TradeManagerView(interaction.user.id, managers, page=0), ephemeral=True)

# ========================================================================


@tree.command(name="blacklist_add", description="Admin: aggiungi un giocatore alla blacklist")
@app_commands.describe(player_id="ID giocatore", motivo="Motivo blacklist")
async def blacklist_add(interaction: discord.Interaction, player_id: str, motivo: str = "Non specificato"):
    if not is_admin(interaction):
        await interaction.response.send_message("❌ Solo gli admin possono usare questo comando.", ephemeral=True)
        return

    conn = connect()
    cur = conn.cursor()
    cur.execute("SELECT name FROM players WHERE id = CAST(%s AS BIGINT)", (player_id,))
    player = cur.fetchone()

    if not player:
        conn.close()
        await interaction.response.send_message("Giocatore non trovato.", ephemeral=True)
        return

    cur.execute("""
        INSERT INTO blacklist_players (player_id, reason, created_by)
        VALUES (%s, %s, %s)
        ON CONFLICT (player_id) DO UPDATE SET reason = EXCLUDED.reason, created_by = EXCLUDED.created_by
        ON CONFLICT (player_id) DO UPDATE SET
            reason = EXCLUDED.reason,
            created_by = EXCLUDED.created_by
    """, (player_id, motivo, str(interaction.user.id)))
    conn.commit()
    conn.close()

    await interaction.response.send_message(f"🚫 **{player['name']}** aggiunto alla blacklist.")


@tree.command(name="blacklist_remove", description="Admin: rimuovi un giocatore dalla blacklist")
@app_commands.describe(player_id="ID giocatore")
async def blacklist_remove(interaction: discord.Interaction, player_id: str):
    if not is_admin(interaction):
        await interaction.response.send_message("❌ Solo gli admin possono usare questo comando.", ephemeral=True)
        return

    conn = connect()
    cur = conn.cursor()
    cur.execute("DELETE FROM blacklist_players WHERE player_id = %s", (player_id,))
    conn.commit()
    conn.close()

    await interaction.response.send_message("✅ Giocatore rimosso dalla blacklist.")


@tree.command(name="blacklist", description="Mostra i giocatori in blacklist")
async def blacklist(interaction: discord.Interaction):
    conn = connect()
    cur = conn.cursor()
    cur.execute("""
        SELECT b.player_id, b.reason, p.name
        FROM blacklist_players b
        LEFT JOIN players p ON p.id::text = b.player_id::text
        ORDER BY b.created_at DESC
        LIMIT 20
    """)
    rows = cur.fetchall()
    conn.close()

    if not rows:
        await interaction.response.send_message("Blacklist vuota.")
        return

    embed = discord.Embed(title="🚫 Blacklist giocatori", color=discord.Color.red())

    for r in rows:
        embed.add_field(
            name=f"{r['name'] or 'Sconosciuto'} • ID {r['player_id']}",
            value=r["reason"] or "Nessun motivo",
            inline=False
        )

    await interaction.response.send_message(embed=embed)





class ModalitaSelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(
                label="Fantacalcio",
                value="fantacalcio",
                emoji="🏆",
                description="Tutti partono da zero con lo stesso budget"
            ),
            discord.SelectOption(
                label="Squadre reali",
                value="squadre_reali",
                emoji="🏟️",
                description="Admin assegna squadre reali e budget compensativo"
            ),
        ]

        super().__init__(
            placeholder="Scegli la modalità della lega...",
            min_values=1,
            max_values=1,
            options=options,
            custom_id="modalita_select"
        )

    async def callback(self, interaction: discord.Interaction):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ Solo gli admin possono cambiare modalità.", ephemeral=True)
            return

        mode = self.values[0]
        set_league_mode(mode)

        if mode == "fantacalcio":
            description = (
                "🏆 Modalità impostata su **Fantacalcio**.\n\n"
                "Tutti i manager costruiscono la rosa da zero tramite aste.\n"
                f"Budget standard consigliato: **{DEFAULT_BUDGET}** crediti.\n\n"
                "Puoi usare `/reset_budget` per pareggiare tutti i budget."
            )
        else:
            description = (
                "🏟️ Modalità impostata su **Squadre reali**.\n\n"
                "Gli admin assegnano una squadra reale ai player con `/assegna_squadra`.\n"
                "Il bot assegna automaticamente i giocatori di quel club e calcola un budget compensativo:\n"
                "• OVR medio 85+ → 50 crediti\n"
                "• OVR medio 82–84 → 80 crediti\n"
                "• OVR medio 80–81 → 150 crediti\n"
                "• OVR medio 78-79 → 350 crediti\n"
                "• OVR medio 75-77 → 430 crediti\n"
                "• sotto 75 → 500 crediti"
            )

        embed = discord.Embed(
            title="⚙️ Modalità lega aggiornata",
            description=description,
            color=discord.Color.gold()
        )

        await interaction.response.edit_message(embed=embed, view=None)


class ModalitaView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=180)
        self.add_item(ModalitaSelect())


@tree.command(name="modalita", description="Admin: scegli la modalità della lega")
async def modalita(interaction: discord.Interaction):
    if not is_admin(interaction):
        await interaction.response.send_message("❌ Solo gli admin possono usare questo comando.", ephemeral=True)
        return

    current_mode = get_league_mode()
    pretty = "Fantacalcio" if current_mode == "fantacalcio" else "Squadre reali"

    embed = discord.Embed(
        title="⚙️ Modalità lega",
        description=f"Modalità attuale: **{pretty}**\n\nScegli la nuova modalità dalla tendina.",
        color=discord.Color.blue()
    )

    await interaction.response.send_message(embed=embed, view=ModalitaView(), ephemeral=True)


@tree.command(name="modalita_attuale", description="Mostra la modalità attuale della lega")
async def modalita_attuale(interaction: discord.Interaction):
    current_mode = get_league_mode()
    pretty = "Fantacalcio" if current_mode == "fantacalcio" else "Squadre reali"

    await interaction.response.send_message(f"⚙️ Modalità attuale: **{pretty}**.", ephemeral=True)



@tree.command(name="diagnostica_squadra", description="Staff: controlla se una squadra reale ha giocatori nel database")
@app_commands.describe(nome="Nome squadra da controllare")
async def diagnostica_squadra(interaction: discord.Interaction, nome: str):
    if not is_admin(interaction):
        await interaction.response.send_message("❌ Solo lo staff può usare questo comando.", ephemeral=True)
        return

    searches = possible_team_names(nome)
    conn = connect()
    cur = conn.cursor()
    cur.execute("""
        SELECT team, COUNT(*) AS total, AVG(overall) AS avg_ovr,
               SUM(CASE WHEN owner_discord_id IS NULL THEN 1 ELSE 0 END) AS liberi
        FROM players
        WHERE team IS NOT NULL AND team != ''
        GROUP BY team
        ORDER BY total DESC
    """)
    rows = cur.fetchall()
    conn.close()

    exact = [r for r in rows if normalize_team_name(r["team"]) in searches]
    similar = [r for r in rows if normalize_text(nome) in normalize_text(r["team"]) or normalize_text(r["team"]) in normalize_text(nome)]

    embed = discord.Embed(
        title="Diagnostica squadra reale",
        description=f"Ricerca: **{nome}**",
        color=discord.Color.blue()
    )

    if exact:
        for r in exact[:10]:
            budget = budget_from_team_overall(r["avg_ovr"] or 0)
            embed.add_field(
                name=str(r["team"]),
                value=f"Giocatori totali: **{r['total']}** | Liberi: **{r['liberi']}** | OVR medio: **{(r['avg_ovr'] or 0):.1f}** | Budget: **{budget}**",
                inline=False
            )
    elif similar:
        embed.add_field(name="Nessuna corrispondenza esatta", value="Possibili nomi nel database:", inline=False)
        for r in similar[:15]:
            embed.add_field(
                name=str(r["team"]),
                value=f"Giocatori: **{r['total']}** | Liberi: **{r['liberi']}**",
                inline=False
            )
    else:
        embed.add_field(
            name="Nessuna squadra trovata",
            value="Il nome non sembra presente nella tabella players. Controlla `/lista_squadre`.",
            inline=False
        )

    await interaction.response.send_message(embed=embed, ephemeral=True)


@tree.command(name="lista_squadre", description="Mostra le squadre reali disponibili")
@app_commands.describe(nome="Filtro nome squadra, opzionale")
async def lista_squadre(interaction: discord.Interaction, nome: str = None):
    await safe_defer(interaction, ephemeral=True, thinking=True)

    conn = connect()
    cur = conn.cursor()

    cur.execute("""
        SELECT team, COUNT(*) AS total, AVG(overall) AS avg_ovr
        FROM players
        WHERE team IS NOT NULL AND team != ''
        GROUP BY team
        HAVING COUNT(*) >= 8
        ORDER BY avg_ovr DESC
    """)
    rows = cur.fetchall()
    conn.close()

    if nome:
        search = normalize_text(nome)
        rows = [r for r in rows if search in normalize_text(r["team"])]

    rows = rows[:20]

    if not rows:
        await interaction.followup.send("Nessuna squadra trovata.", ephemeral=True)
        return

    embed = discord.Embed(
        title="🏟️ Squadre disponibili",
        description="Lista squadre presenti nel database. Usa il nome con `/assegna_squadra`.",
        color=discord.Color.blue()
    )

    for r in rows:
        budget = budget_from_team_overall(r["avg_ovr"])
        embed.add_field(
            name=f"{r['team']}",
            value=f"Giocatori: **{r['total']}** • OVR medio: **{r['avg_ovr']:.1f}** • Budget stimato: **{budget} cr**",
            inline=False
        )

    await interaction.followup.send(embed=embed, ephemeral=True)


@tree.command(name="assegna_squadra", description="Admin: assegna una squadra reale a un manager")
@app_commands.describe(utente="Manager a cui assegnare la squadra", squadra="Nome squadra, es. Milan")
async def assegna_squadra(interaction: discord.Interaction, utente: discord.Member, squadra: str):
    if not is_admin(interaction):
        await interaction.response.send_message("❌ Solo gli admin possono usare questo comando.", ephemeral=True)
        return

    if get_league_mode() != "squadre_reali":
        await interaction.response.send_message(
            "❌ Questo comando funziona solo in modalità **Squadre reali**. Usa `/modalita` per cambiarla.",
            ephemeral=True
        )
        return

    await interaction.response.defer()

    players, avg_ovr, budget = get_team_stats(squadra)

    if not players:
        await interaction.followup.send("Squadra non trovata o senza giocatori liberi disponibili.", ephemeral=True)
        return

    conn = connect()
    cur = conn.cursor()

    cur.execute(
        "INSERT INTO managers (discord_id, name, budget) VALUES (%s, %s, %s)",
        (str(utente.id), utente.display_name, budget)
    )

    # Se l'utente aveva già una rosa, la svincoliamo prima di assegnare la squadra.
    cur.execute("UPDATE players SET owner_discord_id = NULL, sold_price = NULL WHERE owner_discord_id = %s", (str(utente.id),))

    for p in players:
        cur.execute(
            "UPDATE players SET owner_discord_id = %s, sold_price = %s WHERE id = CAST(%s AS BIGINT)",
            (str(utente.id), 0, p["id"])
        )

    cur.execute("UPDATE managers SET budget = %s, name = %s WHERE discord_id = %s", (budget, utente.display_name, str(utente.id)))

    cur.execute("""
        INSERT INTO real_team_assignments (discord_id, manager_name, team_name, avg_overall, assigned_budget) VALUES (%s, %s, %s, %s, %s) ON CONFLICT (discord_id) DO UPDATE SET manager_name = EXCLUDED.manager_name, team_name = EXCLUDED.team_name, avg_overall = EXCLUDED.avg_overall, assigned_budget = EXCLUDED.assigned_budget
    """, (str(utente.id), utente.display_name, players[0]["team"], avg_ovr, budget))

    conn.commit()
    conn.close()

    embed = discord.Embed(
        title="🏟️ Squadra reale assegnata",
        description=f"**{utente.display_name}** ora controlla **{players[0]['team']}**.",
        color=discord.Color.green()
    )
    embed.add_field(name="Giocatori assegnati", value=str(len(players)), inline=True)
    embed.add_field(name="OVR medio squadra", value=f"{avg_ovr:.1f}", inline=True)
    embed.add_field(name="Budget mercato", value=f"{budget} crediti", inline=True)
    embed.set_footer(text="Prezzo giocatori impostato a 0 perché assegnazione iniziale.")

    await interaction.followup.send(embed=embed)


@tree.command(name="squadre_assegnate", description="Mostra le squadre reali già assegnate")
async def squadre_assegnate(interaction: discord.Interaction):
    conn = connect()
    cur = conn.cursor()
    cur.execute("""
        SELECT *
        FROM real_team_assignments
        ORDER BY team_name ASC
    """)
    rows = cur.fetchall()
    conn.close()

    if not rows:
        await interaction.response.send_message("Nessuna squadra reale assegnata.", ephemeral=True)
        return

    embed = discord.Embed(
        title="🏟️ Squadre assegnate",
        color=discord.Color.blue()
    )

    for r in rows[:25]:
        embed.add_field(
            name=f"{r['team_name']} → {r['manager_name']}",
            value=f"OVR medio: **{r['avg_overall']:.1f}** • Budget: **{r['assigned_budget']} cr**",
            inline=False
        )

    await interaction.response.send_message(embed=embed)


@tree.command(name="reset_modalita", description="Owner staff: resetta modalità, rose e squadre assegnate")
async def reset_modalita(interaction: discord.Interaction):
    async def do_reset(confirm_interaction: discord.Interaction):
        await create_backup_before_sensitive_action("reset_modalita")

        conn = connect()
        cur = conn.cursor()
        cur.execute("UPDATE players SET owner_discord_id = NULL, sold_price = NULL")
        cur.execute("UPDATE managers SET budget = %s", (DEFAULT_BUDGET,))
        cur.execute("DELETE FROM real_team_assignments")
        cur.execute("UPDATE auctions SET status = 'closed' WHERE status = 'open'")
        try:
            cur.execute("UPDATE fc26_clubs SET assigned_to = NULL, assigned_at = NULL")
        except Exception:
            pass
        conn.commit()
        conn.close()

        # Ruoli reset: rimuove ISCRITTO, assegna RICHIESTA ISCRIZIONE
        changed = 0
        guild = confirm_interaction.guild
        registered_role = guild.get_role(int(LEAGUE_PLAYER_ROLE_ID)) if guild else None
        request_role = guild.get_role(int(REQUEST_ROLE_ID)) if guild else None
        if registered_role:
            for member in list(registered_role.members):
                try:
                    await member.remove_roles(registered_role, reason="Reset modalità FC26")
                    if request_role:
                        await member.add_roles(request_role, reason="Reset modalità FC26")
                    changed += 1
                except Exception:
                    pass

        embed = discord.Embed(
            title="✅ Reset modalità completato",
            description=(
                "Rose svuotate, budget ripristinato, squadre assegnate cancellate.\n"
                f"Ruoli aggiornati per **{changed}** player."
            ),
            color=discord.Color.green()
        )
        await confirm_interaction.response.edit_message(embed=embed, view=None)
        await send_staff_log(
            confirm_interaction.guild,
            "⚠️ Reset modalità eseguito",
            f"Reset modalità completato. Ruoli aggiornati: **{changed}**.",
            user=confirm_interaction.user,
            color=discord.Color.red()
        )

    await ask_danger_confirmation(
        interaction,
        "Reset modalità",
        "Questa azione resetta rose, budget, assegnazioni squadre e aggiorna i ruoli dei player.",
        do_reset
    )

@tree.command(name="dashboard_admin", description="Admin dashboard completa del bot")
async def dashboard_admin(interaction: discord.Interaction):
    if not is_admin(interaction):
        await interaction.response.send_message("❌ Solo gli admin possono usare questo comando.", ephemeral=True)
        return

    conn = connect()
    cur = conn.cursor()

    cur.execute("SELECT COUNT(*) AS total FROM players")
    total_players = cur.fetchone()["total"]

    cur.execute("SELECT COUNT(*) AS total FROM players WHERE owner_discord_id IS NULL")
    free_players = cur.fetchone()["total"]

    cur.execute("SELECT COUNT(*) AS total FROM managers")
    total_managers = cur.fetchone()["total"]

    cur.execute("SELECT COUNT(*) AS total FROM auctions WHERE status = 'open'")
    active_auctions = cur.fetchone()["total"]

    cur.execute("SELECT COUNT(*) AS total FROM transfer_history")
    total_transfers = cur.fetchone()["total"]

    cur.execute("SELECT SUM(sold_price) AS total FROM players WHERE sold_price IS NOT NULL")
    total_market = cur.fetchone()["total"] or 0

    cur.execute("SELECT COUNT(*) AS total FROM blacklist_players")
    blacklist_total = cur.fetchone()["total"]

    conn.close()

    embed = discord.Embed(
        title="🛠️ Dashboard Admin",
        description="Statistiche complete del bot FC26.",
        color=discord.Color.red()
    )

    embed.add_field(name="👥 Manager registrati", value=str(total_managers), inline=True)
    embed.add_field(name="⚽ Giocatori database", value=str(total_players), inline=True)
    embed.add_field(name="🟢 Giocatori liberi", value=str(free_players), inline=True)
    embed.add_field(name="🔨 Aste attive", value=str(active_auctions), inline=True)
    embed.add_field(name="💸 Trasferimenti", value=str(total_transfers), inline=True)
    embed.add_field(name="🏦 Mercato totale", value=f"{total_market} cr", inline=True)
    embed.add_field(name="🚫 Blacklist", value=str(blacklist_total), inline=True)
    embed.add_field(name="📈 Tassa mercato", value=f"{MARKET_TAX}%", inline=True)

    embed.add_field(
        name="⚙️ Comandi admin",
        value="/reset_budget\n/reset_asta\n/svincola\n/assegna\n/blacklist_add\n/pack_gold",
        inline=False
    )

    await interaction.response.send_message(embed=embed, ephemeral=True)




def active_championship():
    conn = connect()
    cur = conn.cursor()
    try:
        cur.execute("""
            SELECT *
            FROM championships
            WHERE status = 'active'
              AND LOWER(COALESCE(type, 'campionato')) IN ('campionato', 'campionati', 'league')
            ORDER BY id DESC
            LIMIT 1
        """)
        row = cur.fetchone()
        if row:
            conn.close()
            return row
    except Exception:
        pass

    cur.execute("SELECT * FROM championships WHERE status = 'active' ORDER BY id DESC LIMIT 1")
    row = cur.fetchone()
    conn.close()
    return row


def generate_round_robin(players):
    # players: list of (discord_id, display_name)
    players = list(players)
    if len(players) % 2 == 1:
        players.append((None, "Riposo"))

    n = len(players)
    rounds = []

    for rnd in range(n - 1):
        pairs = []
        for i in range(n // 2):
            home = players[i]
            away = players[n - 1 - i]
            if home[0] is not None and away[0] is not None:
                if rnd % 2 == 0:
                    pairs.append((home, away))
                else:
                    pairs.append((away, home))
        rounds.append(pairs)
        players = [players[0]] + [players[-1]] + players[1:-1]

    # ritorno
    second_leg = []
    for pairs in rounds:
        second_leg.append([(away, home) for home, away in pairs])

    return rounds + second_leg


def calculate_group_standings(championship_id, group_id):
    conn = connect()
    cur = conn.cursor()

    cur.execute("""
        SELECT discord_id, display_name
        FROM championship_players
        WHERE championship_id = %s AND group_id = %s
    """, (championship_id, group_id))
    players = cur.fetchall()

    table = {}
    for p in players:
        table[p["discord_id"]] = {
            "discord_id": str(p["discord_id"]),
            "name": p["display_name"],
            "pg": 0,
            "w": 0,
            "d": 0,
            "l": 0,
            "gf": 0,
            "ga": 0,
            "gd": 0,
            "pts": 0,
        }

    cur.execute("""
        SELECT *
        FROM championship_matches
        WHERE championship_id = %s AND group_id = %s AND status = 'confirmed'
    """, (championship_id, group_id))
    matches = cur.fetchall()
    conn.close()

    for m in matches:
        h = m["home_id"]
        a = m["away_id"]
        hg = int(m["home_goals"] or 0)
        ag = int(m["away_goals"] or 0)

        if h not in table or a not in table:
            continue

        table[h]["pg"] += 1
        table[a]["pg"] += 1
        table[h]["gf"] += hg
        table[h]["ga"] += ag
        table[a]["gf"] += ag
        table[a]["ga"] += hg

        if hg > ag:
            table[h]["w"] += 1
            table[a]["l"] += 1
            table[h]["pts"] += 3
        elif hg < ag:
            table[a]["w"] += 1
            table[h]["l"] += 1
            table[a]["pts"] += 3
        else:
            table[h]["d"] += 1
            table[a]["d"] += 1
            table[h]["pts"] += 1
            table[a]["pts"] += 1

    for row in table.values():
        row["gd"] = row["gf"] - row["ga"]

    return sorted(table.values(), key=lambda x: (x["pts"], x["gd"], x["gf"]), reverse=True)


def get_member_club_league(discord_id):
    conn = connect()
    cur = conn.cursor()
    cur.execute("SELECT name, league FROM fc26_clubs WHERE assigned_to = %s", (str(discord_id),))
    row = cur.fetchone()
    conn.close()
    if row:
        return row["name"], row["league"] or "Altri Campionati"
    return None, "Senza campionato"


def sort_members_by_real_league(members):
    buckets = {}
    for member in members:
        club_name, league_name = get_member_club_league(member.id)
        buckets.setdefault(league_name or "Senza campionato", []).append(member)
    ordered = []
    for league_name in sorted(buckets.keys()):
        random.shuffle(buckets[league_name])
        ordered.extend(buckets[league_name])
    return ordered


def generate_single_elimination_pairs(players):
    players = list(players)
    random.shuffle(players)
    pairs = []
    while len(players) >= 2:
        pairs.append((players.pop(0), players.pop(0)))
    if players:
        pairs.append((players.pop(0), (None, "BYE")))
    return pairs


def create_national_cups_for_groups(cur, championship_id, groups):
    created = 0
    for group_id, players in groups.items():
        cur.execute("SELECT name FROM championship_groups WHERE id = %s", (group_id,))
        g = cur.fetchone()
        group_name = g["name"] if g else f"Girone {group_id}"
        cup_name = f"Coppa Nazionale {group_name}"
        cur.execute("""
            INSERT INTO national_cups (championship_id, group_id, name, status)
            VALUES (%s, %s, %s, 'active')
        """, (championship_id, group_id, cup_name))
        cup_id = cur.lastrowid
        for home, away in generate_single_elimination_pairs(players):
            cur.execute("""
                INSERT INTO national_cup_matches
                (cup_id, round_number, home_id, away_id, home_name, away_name)
                VALUES (%s, 1, %s, %s, %s, %s)
            """, (cup_id, home[0], away[0], home[1], away[1]))
        created += 1
    return created


class CreaCampionatoModal(discord.ui.Modal, title="Crea campionato"):
    nome = discord.ui.TextInput(
        label="Nome campionato",
        placeholder="Esempio: FC26 League",
        required=True,
        max_length=80
    )
    numero_gironi = discord.ui.TextInput(
        label="Numero gironi",
        placeholder="Esempio: 2",
        required=True,
        max_length=2
    )
    nomi_gironi = discord.ui.TextInput(
        label="Nomi gironi separati da virgola",
        placeholder="Esempio: Girone A, Girone B",
        required=True,
        max_length=200
    )
    squadre_per_girone = discord.ui.TextInput(
        label="Squadre per girone",
        placeholder="Esempio: 8",
        required=True,
        max_length=2
    )

    def __init__(self, grouping_mode="random"):
        super().__init__()
        self.grouping_mode = grouping_mode

    async def on_submit(self, interaction: discord.Interaction):
        if not is_league_admin(interaction):
            await interaction.response.send_message("❌ Solo gli admin possono creare il campionato.", ephemeral=True)
            return

        try:
            group_count = int(str(self.numero_gironi.value).strip())
            teams_per_group = int(str(self.squadre_per_girone.value).strip())
        except Exception:
            await interaction.response.send_message("Numero gironi e squadre per girone devono essere numeri.", ephemeral=True)
            return

        group_names = [g.strip() for g in str(self.nomi_gironi.value).split(",") if g.strip()]

        if group_count <= 0 or teams_per_group <= 1:
            await interaction.response.send_message("Valori non validi.", ephemeral=True)
            return

        if len(group_names) != group_count:
            await interaction.response.send_message("Il numero dei nomi girone deve coincidere con il numero gironi.", ephemeral=True)
            return

        role = interaction.guild.get_role(int(LEAGUE_PLAYER_ROLE_ID)) if interaction.guild else None
        if not role:
            await interaction.response.send_message("Ruolo ISCRITTI non trovato.", ephemeral=True)
            return

        members = [m for m in role.members if not m.bot]
        if self.grouping_mode == "real_league" and get_league_mode() == "squadre_reali":
            members = sort_members_by_real_league(members)
        else:
            random.shuffle(members)

        total_needed = group_count * teams_per_group
        selected = members[:total_needed]

        if len(selected) < total_needed:
            await interaction.response.send_message(
                f"⚠️ Non ci sono abbastanza iscritti. Richiesti {total_needed}, trovati {len(selected)}.",
                ephemeral=True
            )
            return

        conn = connect()
        cur = conn.cursor()
        cur.execute("UPDATE championships SET status = 'archived' WHERE status = 'active'")
        cur.execute("""
            INSERT INTO championships (name, status, group_count, teams_per_group)
            VALUES (%s, 'active', %s, %s)
        """, (str(self.nome.value), group_count, teams_per_group))
        championship_id = cur.lastrowid

        group_ids = []
        for gname in group_names:
            cur.execute("INSERT INTO championship_groups (championship_id, name) VALUES (%s, %s)", (championship_id, gname))
            group_ids.append(cur.lastrowid)

        idx = 0
        groups = {}
        for group_id, gname in zip(group_ids, group_names):
            groups[group_id] = []
            for _ in range(teams_per_group):
                member = selected[idx]
                idx += 1
                groups[group_id].append((str(member.id), member.display_name))
                cur.execute("""
                    INSERT INTO championship_players (championship_id, group_id, discord_id, display_name)
                    VALUES (%s, %s, %s, %s)
                """, (championship_id, group_id, str(member.id), member.display_name))

        for group_id, players in groups.items():
            rounds = generate_round_robin(players)
            for round_idx, pairs in enumerate(rounds, start=1):
                for home, away in pairs:
                    cur.execute("""
                        INSERT INTO championship_matches
                        (championship_id, group_id, round_number, home_id, away_id, home_name, away_name)
                        VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """, (
                        championship_id, group_id, round_idx,
                        home[0], away[0], home[1], away[1]
                    ))

        cups_created = create_national_cups_for_groups(cur, championship_id, groups)
        conn.commit()
        conn.close()

        mode_label = "Per campionato reale" if self.grouping_mode == "real_league" else "Random"
        embed = discord.Embed(
            title="🏆 Campionato creato",
            description=f"**{self.nome.value}** creato con calendario andata/ritorno.",
            color=discord.Color.gold()
        )
        embed.add_field(name="Modalità gironi", value=mode_label, inline=True)
        embed.add_field(name="Gironi", value=str(group_count), inline=True)
        embed.add_field(name="Squadre per girone", value=str(teams_per_group), inline=True)
        embed.add_field(name="Iscritti usati", value=str(len(selected)), inline=True)
        embed.add_field(name="Coppe nazionali create", value=str(cups_created), inline=True)
        embed.add_field(name="Nomi gironi", value=", ".join(group_names), inline=False)
        embed.set_footer(text="Per creare Champions/Europa/Conference usa /genera_coppe_europee.")

        await interaction.response.send_message(embed=embed)


class ChampionshipGroupingSelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(label="Random", value="random", emoji="🎲", description="Gironi casuali"),
            discord.SelectOption(label="In base al campionato reale", value="real_league", emoji="🏆", description="Raggruppa usando il campionato del club assegnato"),
        ]
        super().__init__(placeholder="Scegli come generare i gironi...", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        if not is_league_admin(interaction):
            await interaction.response.send_message("❌ Solo gli admin possono creare il campionato.", ephemeral=True)
            return
        await interaction.response.send_modal(CreaCampionatoModal(self.values[0]))


class ChampionshipGroupingView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=180)
        self.add_item(ChampionshipGroupingSelect())

@tree.command(name="crea_campionato", description="Admin: crea gironi e calendario automatico")
async def crea_campionato(interaction: discord.Interaction):
    if not is_league_admin(interaction):
        await interaction.response.send_message("❌ Solo gli admin possono creare il campionato.", ephemeral=True)
        return

    if get_league_mode() == "squadre_reali":
        embed = discord.Embed(
            title="🏆 Creazione campionato",
            description="Modalità **Squadre reali** attiva. Scegli se creare i gironi random o in base al campionato reale del club assegnato.",
            color=discord.Color.gold()
        )
        await interaction.response.send_message(embed=embed, view=ChampionshipGroupingView(), ephemeral=True)
    else:
        await interaction.response.send_modal(CreaCampionatoModal("random"))


@tree.command(name="reset_campionato", description="Admin: archivia il campionato attivo")
async def reset_campionato(interaction: discord.Interaction):
    if not is_league_admin(interaction):
        await interaction.response.send_message("❌ Solo gli admin possono resettare il campionato.", ephemeral=True)
        return

    await safe_defer(interaction, ephemeral=True, thinking=True)

    await create_backup_before_sensitive_action("generazione_campionato")

    conn = connect()
    cur = conn.cursor()
    cur.execute("UPDATE championships SET status = 'archived' WHERE status = 'active'")
    conn.commit()
    conn.close()

    updated_roles = await reset_registered_players_to_request_role(interaction.guild)

    await interaction.followup.send(
        f"✅ Campionato attivo archiviato.\n"
        f"🔁 Ruoli aggiornati: **{updated_roles}** player hanno perso ISCRITTO e ricevuto RICHIESTA ISCRIZIONE.",
        ephemeral=True
    )


class ResultOpponentSelect(discord.ui.Select):
    def __init__(self, matches):
        options = []
        for m in matches[:25]:
            opponent_id = m["away_id"] if str(m["home_id"]) == str(m["requester_id"]) else m["home_id"]
            opponent_name = m["away_name"] if str(m["home_id"]) == str(m["requester_id"]) else m["home_name"]
            label = f"G{m['round_number']} vs {opponent_name}"
            options.append(
                discord.SelectOption(
                    label=label[:100],
                    value=str(m["id"]),
                    description="Partita non ancora giocata"
                )
            )

        super().__init__(placeholder="Scegli la partita...", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        match_id = int(self.values[0])
        await interaction.response.send_modal(ResultModal(match_id))


class ResultOpponentView(discord.ui.View):
    def __init__(self, matches):
        super().__init__(timeout=180)
        self.add_item(ResultOpponentSelect(matches))


class ResultModal(discord.ui.Modal, title="Inserisci risultato"):
    gol_miei = discord.ui.TextInput(label="Gol tuoi", placeholder="Esempio: 2", required=True, max_length=2)
    gol_avversario = discord.ui.TextInput(label="Gol avversario", placeholder="Esempio: 1", required=True, max_length=2)
    marcatori_miei = discord.ui.TextInput(
        label="Marcatori tuoi",
        placeholder="Nomi separati da virgola. Se doppietta ripeti il nome.",
        required=False,
        style=discord.TextStyle.paragraph,
        max_length=800
    )
    marcatori_avversario = discord.ui.TextInput(
        label="Marcatori avversario",
        placeholder="Nomi separati da virgola. Se doppietta ripeti il nome.",
        required=False,
        style=discord.TextStyle.paragraph,
        max_length=800
    )

    def __init__(self, match_id):
        super().__init__()
        self.match_id = match_id

    async def on_submit(self, interaction: discord.Interaction):
        try:
            my_goals = int(str(self.gol_miei.value).strip())
            opp_goals = int(str(self.gol_avversario.value).strip())
        except Exception:
            await interaction.response.send_message("I gol devono essere numeri.", ephemeral=True)
            return

        conn = connect()
        cur = conn.cursor()
        cur.execute("SELECT * FROM championship_matches WHERE id = %s AND status = 'pending'", (self.match_id,))
        match = cur.fetchone()

        if not match:
            conn.close()
            await interaction.response.send_message("Partita non trovata o già giocata.", ephemeral=True)
            return

        user_id = str(interaction.user.id)
        if user_id not in (str(match["home_id"]), str(match["away_id"])):
            conn.close()
            await interaction.response.send_message("Non fai parte di questa partita.", ephemeral=True)
            return

        is_home = user_id == str(match["home_id"])
        home_goals = my_goals if is_home else opp_goals
        away_goals = opp_goals if is_home else my_goals
        confirm_by = match["away_id"] if is_home else match["home_id"]

        # scorers check: skip if 0-0
        my_scorers = [s.strip() for s in str(self.marcatori_miei.value).split(",") if s.strip()]
        opp_scorers = [s.strip() for s in str(self.marcatori_avversario.value).split(",") if s.strip()]

        if my_goals == 0 and opp_goals == 0:
            my_scorers = []
            opp_scorers = []
        else:
            if len(my_scorers) != my_goals:
                conn.close()
                await interaction.response.send_message("Il numero dei tuoi marcatori deve coincidere con i tuoi gol.", ephemeral=True)
                return
            if len(opp_scorers) != opp_goals:
                conn.close()
                await interaction.response.send_message("Il numero dei marcatori avversari deve coincidere con i gol avversari.", ephemeral=True)
                return

        cur.execute("""
            UPDATE championship_matches
            SET home_goals = %s, away_goals = %s, status = 'awaiting_confirmation', submitted_by = %s, confirm_by = %s
            WHERE id = %s
        """, (home_goals, away_goals, user_id, str(confirm_by), self.match_id))

        cur.execute("DELETE FROM match_scorers WHERE match_id = %s", (self.match_id,))

        home_owner = str(match["home_id"])
        away_owner = str(match["away_id"])

        if is_home:
            home_scorers = my_scorers
            away_scorers = opp_scorers
        else:
            home_scorers = opp_scorers
            away_scorers = my_scorers

        def insert_scorers(names, owner_id):
            counts = {}
            for name in names:
                counts[name] = counts.get(name, 0) + 1
            for name, goals in counts.items():
                cur.execute("""
                    INSERT INTO match_scorers (match_id, scorer_name, team_owner_id, goals)
                    VALUES (%s, %s, %s, %s)
                """, (self.match_id, name, owner_id, goals))

        insert_scorers(home_scorers, home_owner)
        insert_scorers(away_scorers, away_owner)

        conn.commit()
        conn.close()

        embed = build_result_embed(self.match_id)
        await interaction.response.send_message(
            content=f"<@{confirm_by}> devi confermare o contestare il risultato.",
            embed=embed,
            view=ResultConfirmView(self.match_id, str(confirm_by))
        )


def build_result_embed(match_id):
    conn = connect()
    cur = conn.cursor()
    cur.execute("SELECT * FROM championship_matches WHERE id = %s", (match_id,))
    m = cur.fetchone()
    cur.execute("SELECT scorer_name, team_owner_id, goals FROM match_scorers WHERE match_id = %s", (match_id,))
    scorers = cur.fetchall()
    conn.close()

    status_label = {
        "awaiting_confirmation": "⏳ In attesa conferma",
        "confirmed": "✅ Ufficiale",
        "contested": "⚠️ Contestato",
        "pending": "📅 Da giocare"
    }.get(m["status"], m["status"])

    embed = discord.Embed(
        title=f"⚽ Risultato — Giornata {m['round_number']}",
        description=f"**{m['home_name']} {m['home_goals']} - {m['away_goals']} {m['away_name']}**",
        color=discord.Color.gold()
    )
    embed.add_field(name="Stato", value=status_label, inline=False)

    if not scorers:
        embed.add_field(name="Marcatori", value="Nessun marcatore.", inline=False)
    else:
        lines = []
        for s in scorers:
            suffix = f" x{s['goals']}" if int(s["goals"]) > 1 else ""
            lines.append(f"⚽ {s['scorer_name']}{suffix}")
        embed.add_field(name="Marcatori", value="\n".join(lines), inline=False)

    embed.set_footer(text=f"ID partita: {match_id}")
    return embed



# ================= SYNC RISULTATI DISCORD -> SITO =================

def site_winner_name(home_name, away_name, home_goals, away_goals):
    hg = safe_int(home_goals)
    ag = safe_int(away_goals)
    if hg > ag:
        return home_name
    if ag > hg:
        return away_name
    return None


def get_site_club_name(discord_id, fallback_name=None):
    """Restituisce il club assegnato al manager, se presente, altrimenti il nome Discord."""
    conn = connect()
    cur = conn.cursor()
    try:
        cur.execute("""
            SELECT name
            FROM fc26_clubs
            WHERE assigned_to = %s
            LIMIT 1
        """, (str(discord_id),))
        row = cur.fetchone()
        if row and row.get("name"):
            return row["name"]
    except Exception:
        pass

    try:
        cur.execute("""
            SELECT club_name, manager_name, name
            FROM managers
            WHERE discord_id = %s
            LIMIT 1
        """, (str(discord_id),))
        row = cur.fetchone()
        if row:
            return row.get("club_name") or row.get("manager_name") or row.get("name") or fallback_name or str(discord_id)
    except Exception:
        pass
    finally:
        conn.close()

    return fallback_name or str(discord_id)


def sync_site_match_result(match_id):
    """Salva il risultato confermato nella tabella match_results letta dal sito."""
    conn = connect()
    cur = conn.cursor()

    cur.execute("""
        SELECT m.*, c.name AS championship_name, g.name AS group_name
        FROM championship_matches m
        LEFT JOIN championships c ON c.id = m.championship_id
        LEFT JOIN championship_groups g ON g.id = m.group_id
        WHERE m.id = %s
        LIMIT 1
    """, (match_id,))
    m = cur.fetchone()

    if not m:
        conn.close()
        return False

    home_team = get_site_club_name(m["home_id"], m["home_name"])
    away_team = get_site_club_name(m["away_id"], m["away_name"])
    competition_name = m.get("group_name") or m.get("championship_name") or "Campionato"
    competition_type = "Campionati"
    round_name = f"Giornata {m['round_number']}"
    winner = site_winner_name(home_team, away_team, m["home_goals"], m["away_goals"])

    cur.execute("""
        DELETE FROM match_results
        WHERE source_table = 'championship_matches'
          AND source_match_id = %s
    """, (str(match_id),))

    cur.execute("""
        INSERT INTO match_results (
            source_table,
            source_match_id,
            competition_name,
            competition_type,
            round,
            home_team,
            away_team,
            home_score,
            away_score,
            winner,
            status,
            updated_at
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'played', CURRENT_TIMESTAMP)
    """, (
        "championship_matches",
        str(match_id),
        competition_name,
        competition_type,
        round_name,
        home_team,
        away_team,
        safe_int(m["home_goals"]),
        safe_int(m["away_goals"]),
        winner,
    ))

    conn.commit()
    conn.close()
    return True


def sync_site_standings_for_championship(championship_id):
    """Ricalcola le classifiche del campionato attivo e aggiorna la tabella standings del sito."""
    conn = connect()
    cur = conn.cursor()

    cur.execute("""
        SELECT *
        FROM championship_groups
        WHERE championship_id = %s
        ORDER BY id ASC
    """, (championship_id,))
    groups = cur.fetchall()

    group_names = [g["name"] for g in groups]

    if group_names:
        cur.execute("""
            DELETE FROM standings
            WHERE competition_type = 'Campionati'
              AND competition_name = ANY(%s)
        """, (group_names,))
    else:
        cur.execute("DELETE FROM standings WHERE competition_type = 'Campionati'")

    conn.commit()
    conn.close()

    # calculate_group_standings apre una sua connessione, quindi inseriamo dopo.
    conn = connect()
    cur = conn.cursor()

    for g in groups:
        table = calculate_group_standings(championship_id, g["id"])
        for row in table:
            discord_id = row.get("discord_id")
            club_name = get_site_club_name(discord_id, row["name"]) if discord_id else row["name"]

            cur.execute("""
                INSERT INTO standings (
                    competition_name,
                    competition_type,
                    club_name,
                    played,
                    wins,
                    draws,
                    losses,
                    goals_for,
                    goals_against,
                    points,
                    updated_at
                )
                VALUES (%s, 'Campionati', %s, %s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
            """, (
                g["name"],
                club_name,
                safe_int(row["pg"]),
                safe_int(row["w"]),
                safe_int(row["d"]),
                safe_int(row["l"]),
                safe_int(row["gf"]),
                safe_int(row["ga"]),
                safe_int(row["pts"]),
            ))

    conn.commit()
    conn.close()
    return True


def sync_site_after_confirmed_match(match_id):
    """Hook centrale chiamato quando un risultato campionato viene confermato."""
    try:
        conn = connect()
        cur = conn.cursor()
        cur.execute("SELECT championship_id FROM championship_matches WHERE id = %s", (match_id,))
        row = cur.fetchone()
        conn.close()

        if not row:
            return False

        sync_site_match_result(match_id)
        sync_site_standings_for_championship(row["championship_id"])
        print(f"[SITE SYNC] Classifica e risultato aggiornati per match_id={match_id}")
        return True
    except Exception as e:
        print(f"[SITE SYNC ERROR] match_id={match_id}: {e}")
        return False


def sync_site_manual_league_result(competition_name, home_team, away_team, home_score, away_score, round_name="Risultato"):
    """Comando staff: salva un risultato manuale e ricalcola la classifica partendo da match_results."""
    home_score = safe_int(home_score)
    away_score = safe_int(away_score)
    winner = site_winner_name(home_team, away_team, home_score, away_score)

    conn = connect()
    cur = conn.cursor()

    cur.execute("""
        INSERT INTO match_results (
            source_table,
            source_match_id,
            competition_name,
            competition_type,
            round,
            home_team,
            away_team,
            home_score,
            away_score,
            winner,
            status,
            updated_at
        )
        VALUES ('manual', %s, %s, 'Campionati', %s, %s, %s, %s, %s, %s, 'played', CURRENT_TIMESTAMP)
    """, (
        f"manual-{datetime.now().timestamp()}",
        competition_name,
        round_name,
        home_team,
        away_team,
        home_score,
        away_score,
        winner,
    ))

    cur.execute("SELECT * FROM match_results WHERE competition_name = %s AND competition_type = 'Campionati'", (competition_name,))
    matches = cur.fetchall()

    table = {}
    for m in matches:
        for team in (m["home_team"], m["away_team"]):
            table.setdefault(team, {"pg": 0, "w": 0, "d": 0, "l": 0, "gf": 0, "ga": 0, "pts": 0})

        hg = safe_int(m["home_score"])
        ag = safe_int(m["away_score"])
        h = m["home_team"]
        a = m["away_team"]

        table[h]["pg"] += 1
        table[a]["pg"] += 1
        table[h]["gf"] += hg
        table[h]["ga"] += ag
        table[a]["gf"] += ag
        table[a]["ga"] += hg

        if hg > ag:
            table[h]["w"] += 1
            table[a]["l"] += 1
            table[h]["pts"] += 3
        elif ag > hg:
            table[a]["w"] += 1
            table[h]["l"] += 1
            table[a]["pts"] += 3
        else:
            table[h]["d"] += 1
            table[a]["d"] += 1
            table[h]["pts"] += 1
            table[a]["pts"] += 1

    cur.execute("DELETE FROM standings WHERE competition_name = %s AND competition_type = 'Campionati'", (competition_name,))

    for team, row in table.items():
        cur.execute("""
            INSERT INTO standings (
                competition_name,
                competition_type,
                club_name,
                played,
                wins,
                draws,
                losses,
                goals_for,
                goals_against,
                points,
                updated_at
            )
            VALUES (%s, 'Campionati', %s, %s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
        """, (
            competition_name,
            team,
            row["pg"],
            row["w"],
            row["d"],
            row["l"],
            row["gf"],
            row["ga"],
            row["pts"],
        ))

    conn.commit()
    conn.close()
    return True


def sync_site_cup_result(competition_name, round_name, home_team, away_team, home_score, away_score):
    """Comando staff: salva risultato coppa nazionale e aggiorna tabellone letto dal sito."""
    home_score = safe_int(home_score)
    away_score = safe_int(away_score)
    winner = site_winner_name(home_team, away_team, home_score, away_score)

    conn = connect()
    cur = conn.cursor()

    source_id = f"manual-cup-{datetime.now().timestamp()}"

    cur.execute("""
        INSERT INTO match_results (
            source_table,
            source_match_id,
            competition_name,
            competition_type,
            round,
            home_team,
            away_team,
            home_score,
            away_score,
            winner,
            status,
            updated_at
        )
        VALUES ('manual_cup', %s, %s, 'Coppa Nazionale', %s, %s, %s, %s, %s, %s, 'played', CURRENT_TIMESTAMP)
    """, (
        source_id,
        competition_name,
        round_name,
        home_team,
        away_team,
        home_score,
        away_score,
        winner,
    ))

    cur.execute("""
        INSERT INTO cup_matches (
            source_table,
            source_match_id,
            competition_name,
            round,
            home_team,
            away_team,
            home_score,
            away_score,
            winner,
            status,
            updated_at
        )
        VALUES ('manual_cup', %s, %s, %s, %s, %s, %s, %s, %s, 'played', CURRENT_TIMESTAMP)
    """, (
        source_id,
        competition_name,
        round_name,
        home_team,
        away_team,
        home_score,
        away_score,
        winner,
    ))

    # La pagina classifiche attuale usa standings anche per il tabellone coppa:
    # inseriamo una riga per ciascuna squadra del match.
    cur.execute("""
        INSERT INTO standings (
            competition_name,
            competition_type,
            club_name,
            played,
            wins,
            draws,
            losses,
            goals_for,
            goals_against,
            points,
            updated_at
        )
        VALUES (%s, 'Coppa Nazionale', %s, 1, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
    """, (
        competition_name,
        home_team,
        1 if home_score > away_score else 0,
        1 if home_score == away_score else 0,
        1 if home_score < away_score else 0,
        home_score,
        away_score,
        3 if home_score > away_score else 1 if home_score == away_score else 0,
    ))

    cur.execute("""
        INSERT INTO standings (
            competition_name,
            competition_type,
            club_name,
            played,
            wins,
            draws,
            losses,
            goals_for,
            goals_against,
            points,
            updated_at
        )
        VALUES (%s, 'Coppa Nazionale', %s, 1, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
    """, (
        competition_name,
        away_team,
        1 if away_score > home_score else 0,
        1 if away_score == home_score else 0,
        1 if away_score < home_score else 0,
        away_score,
        home_score,
        3 if away_score > home_score else 1 if away_score == home_score else 0,
    ))

    conn.commit()
    conn.close()
    return True

# ========================================================


class ResultConfirmView(discord.ui.View):
    def __init__(self, match_id, confirm_by):
        super().__init__(timeout=86400)
        self.match_id = match_id
        self.confirm_by = str(confirm_by)

    @discord.ui.button(label="Conferma", style=discord.ButtonStyle.success)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        if str(interaction.user.id) != self.confirm_by:
            await interaction.response.send_message("Solo l'avversario può confermare questo risultato.", ephemeral=True)
            return

        conn = connect()
        cur = conn.cursor()
        cur.execute("UPDATE championship_matches SET status = 'confirmed' WHERE id = %s", (self.match_id,))
        conn.commit()
        conn.close()

        sync_site_after_confirmed_match(self.match_id)

        embed = build_result_embed(self.match_id)
        await interaction.response.edit_message(content="✅ Risultato confermato e sito aggiornato.", embed=embed, view=None)

    @discord.ui.button(label="Contesta", style=discord.ButtonStyle.danger)
    async def contest(self, interaction: discord.Interaction, button: discord.ui.Button):
        if str(interaction.user.id) != self.confirm_by:
            await interaction.response.send_message("Solo l'avversario può contestare questo risultato.", ephemeral=True)
            return

        conn = connect()
        cur = conn.cursor()
        cur.execute("UPDATE championship_matches SET status = 'contested' WHERE id = %s", (self.match_id,))
        conn.commit()
        conn.close()

        embed = build_result_embed(self.match_id)
        await interaction.response.edit_message(content="⚠️ Risultato contestato. Staff richiesto.", embed=embed, view=None)




# ================= RISULTATI GUIDATI DISCORD -> SITO =================
# Flusso:
# /avvia_andata o /avvia_ritorno decide quali partite di campionato sono inseribili.
# /risultato mostra menu competizione -> menu partita -> modal gol/marcatori -> conferma avversario -> sync sito.

RESULT_COMPETITIONS = {
    "campionato": {
        "label": "Campionato",
        "description": "Partite del campionato attivo",
        "emoji": "🏆",
    },
    "coppa_nazionale": {
        "label": "Coppa Nazionale",
        "description": "Tabellone coppa nazionale",
        "emoji": "🇮🇹",
    },
    "champions": {
        "label": "Champions League",
        "description": "Coppa europea: Champions League",
        "emoji": "⭐",
    },
    "europa": {
        "label": "Europa League",
        "description": "Coppa europea: Europa League",
        "emoji": "🟠",
    },
    "conference": {
        "label": "Conference League",
        "description": "Coppa europea: Conference League",
        "emoji": "🟢",
    },
}


def set_active_leg(value: str):
    value = "ritorno" if str(value).lower().strip() == "ritorno" else "andata"
    conn = connect()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO league_settings (key, value)
        VALUES ('active_leg', %s)
        ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
    """, (value,))
    conn.commit()
    conn.close()
    return value


def get_active_leg():
    conn = connect()
    cur = conn.cursor()
    cur.execute("SELECT value FROM league_settings WHERE key = 'active_leg'")
    row = cur.fetchone()
    conn.close()
    return (row["value"] if row else "andata") or "andata"


def get_first_leg_last_round(championship_id):
    conn = connect()
    cur = conn.cursor()
    cur.execute("""
        SELECT MAX(round_number) AS max_round
        FROM championship_matches
        WHERE championship_id = %s
    """, (championship_id,))
    row = cur.fetchone()
    conn.close()
    max_round = safe_int(row["max_round"] if row else 0)
    return max_round // 2 if max_round else 0, max_round


def get_manager_club_name_by_discord(discord_id, fallback_name=None):
    conn = connect()
    cur = conn.cursor()
    cur.execute("SELECT club_name, manager_name, name FROM managers WHERE discord_id = %s", (str(discord_id),))
    manager = cur.fetchone()
    if manager and manager.get("club_name"):
        conn.close()
        return manager["club_name"]

    cur.execute("SELECT club_name FROM signup_requests WHERE discord_id = %s AND status = 'accepted' ORDER BY handled_at DESC NULLS LAST LIMIT 1", (str(discord_id),))
    signup = cur.fetchone()
    if signup and signup.get("club_name"):
        conn.close()
        return signup["club_name"]

    cur.execute("SELECT team_name FROM real_team_assignments WHERE discord_id = %s", (str(discord_id),))
    real = cur.fetchone()
    conn.close()
    if real and real.get("team_name"):
        return real["team_name"]

    return fallback_name or str(discord_id)


def get_roster_names_for_manager(discord_id, limit=25):
    conn = connect()
    cur = conn.cursor()
    cur.execute("""
        SELECT name
        FROM players
        WHERE owner_discord_id = %s
        ORDER BY overall DESC NULLS LAST, name ASC
        LIMIT %s
    """, (str(discord_id), int(limit)))
    rows = cur.fetchall()
    conn.close()
    return [r["name"] for r in rows if r.get("name")]


def parse_scorers_input(raw: str):
    """Accetta formati: Mbappe 3, Rodri 2 oppure Mbappe=3; Rodri=2 oppure Mbappe, Mbappe, Rodri."""
    raw = str(raw or "").strip()
    if not raw:
        return []

    raw = raw.replace(";", ",").replace("\n", ",")
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    result = []

    for part in parts:
        goals = 1
        name = part
        if "=" in part:
            name, goals_txt = part.rsplit("=", 1)
            goals = safe_int(goals_txt, 1)
        else:
            tokens = part.split()
            if len(tokens) > 1 and tokens[-1].isdigit():
                goals = safe_int(tokens[-1], 1)
                name = " ".join(tokens[:-1])

        name = str(name).strip()
        goals = max(1, safe_int(goals, 1))
        if name:
            result.append((name, goals))

    return result


def scorers_total(scorers):
    return sum(max(1, safe_int(goals, 1)) for _, goals in scorers)


def insert_scorers_for_match(cur, match_id, owner_id, scorers):
    counts = {}
    for name, goals in scorers:
        name = str(name).strip()
        if not name:
            continue
        counts[name] = counts.get(name, 0) + safe_int(goals, 1)

    for name, goals in counts.items():
        cur.execute("""
            INSERT INTO match_scorers (match_id, scorer_name, team_owner_id, goals)
            VALUES (%s, %s, %s, %s)
        """, (match_id, name, str(owner_id), safe_int(goals, 1)))


def get_guided_competition_options(discord_id):
    options = []
    for key, meta in RESULT_COMPETITIONS.items():
        if get_available_matches_for_competition(discord_id, key):
            options.append(discord.SelectOption(
                label=meta["label"],
                value=key,
                description=meta["description"],
                emoji=meta["emoji"],
            ))
    return options


def get_available_matches_for_competition(discord_id, competition_key):
    discord_id = str(discord_id)
    conn = connect()
    cur = conn.cursor()

    if competition_key == "campionato":
        champ = active_championship()
        if not champ:
            conn.close()
            return []

        first_last, max_round = get_first_leg_last_round(champ["id"])
        active_leg = get_active_leg()
        if active_leg == "ritorno" and max_round:
            round_min = first_last + 1
            round_max = max_round
        else:
            round_min = 1
            round_max = first_last or max_round or 999

        cur.execute("""
            SELECT m.*, g.name AS group_name, c.name AS competition_name
            FROM championship_matches m
            LEFT JOIN championship_groups g ON g.id = m.group_id
            LEFT JOIN championships c ON c.id = m.championship_id
            WHERE m.championship_id = %s
              AND m.status = 'pending'
              AND m.round_number BETWEEN %s AND %s
              AND (m.home_id = %s OR m.away_id = %s)
            ORDER BY m.round_number ASC, m.id ASC
            LIMIT 25
        """, (champ["id"], round_min, round_max, discord_id, discord_id))
        rows = cur.fetchall()
        conn.close()
        for r in rows:
            r["requester_id"] = str(discord_id)
            r["_result_kind"] = "championship"
            r["_competition_label"] = r.get("group_name") or r.get("competition_name") or "Campionato"
        return rows

    if competition_key == "coppa_nazionale":
        cur.execute("""
            SELECT m.*, c.name AS competition_name
            FROM national_cup_matches m
            LEFT JOIN national_cups c ON c.id = m.cup_id
            WHERE COALESCE(m.status, 'pending') IN ('pending', 'active')
              AND (m.home_id = %s OR m.away_id = %s)
            ORDER BY m.round_number ASC, m.id ASC
            LIMIT 25
        """, (discord_id, discord_id))
        rows = cur.fetchall()
        conn.close()
        for r in rows:
            r["requester_id"] = str(discord_id)
            r["_result_kind"] = "national_cup"
            r["_competition_label"] = r.get("competition_name") or "Coppa Nazionale"
        return rows

    # Coppe europee: per ora leggiamo eventuali match da championship_matches filtrando per nome gruppo/campionato.
    keywords = {
        "champions": ["champions", "champion"],
        "europa": ["europa league", "europa"],
        "conference": ["conference"],
    }.get(competition_key, [])

    if not keywords:
        conn.close()
        return []

    cur.execute("""
        SELECT m.*, g.name AS group_name, c.name AS competition_name
        FROM championship_matches m
        LEFT JOIN championship_groups g ON g.id = m.group_id
        LEFT JOIN championships c ON c.id = m.championship_id
        WHERE m.status = 'pending'
          AND COALESCE(c.status, 'active') = 'active'
          AND (m.home_id = %s OR m.away_id = %s)
        ORDER BY m.round_number ASC, m.id ASC
        LIMIT 100
    """, (discord_id, discord_id))
    rows = cur.fetchall()
    conn.close()

    filtered = []
    for r in rows:
        check = normalize_text(f"{r.get('group_name')} {r.get('competition_name')}")
        if any(normalize_text(k) in check for k in keywords):
            r["requester_id"] = str(discord_id)
            r["_result_kind"] = "european_group"
            r["_competition_label"] = r.get("group_name") or RESULT_COMPETITIONS[competition_key]["label"]
            filtered.append(r)
    return filtered[:25]


def format_guided_match_label(match, requester_id):
    requester_id = str(requester_id)
    home_id = str(match.get("home_id") or "")
    away_id = str(match.get("away_id") or "")
    is_home = requester_id == home_id
    opponent_id = away_id if is_home else home_id
    opponent_name = match.get("away_name") if is_home else match.get("home_name")
    opponent_club = get_manager_club_name_by_discord(opponent_id, opponent_name)
    casa_trasferta = "Casa" if is_home else "Trasferta"
    round_number = match.get("round_number") or match.get("round") or "?"
    return f"{casa_trasferta} vs {opponent_club}"[:100], f"Turno/Giornata {round_number} • @{opponent_name or opponent_id}"[:100]


class GuidedCompetitionSelect(discord.ui.Select):
    def __init__(self, options):
        super().__init__(
            placeholder="Scegli la competizione...",
            min_values=1,
            max_values=1,
            options=options[:25],
        )

    async def callback(self, interaction: discord.Interaction):
        competition_key = self.values[0]
        matches = get_available_matches_for_competition(interaction.user.id, competition_key)
        if not matches:
            await interaction.response.edit_message(
                content="❌ Non hai partite disponibili per questa competizione.",
                embed=None,
                view=None,
            )
            return

        embed = discord.Embed(
            title=f"⚽ {RESULT_COMPETITIONS[competition_key]['label']}",
            description="Scegli la partita attiva da compilare.",
            color=discord.Color.blue(),
        )
        await interaction.followup.send(embed=embed, view=GuidedMatchSelectView(matches), ephemeral=True)


class GuidedCompetitionView(discord.ui.View):
    def __init__(self, options):
        super().__init__(timeout=180)
        self.add_item(GuidedCompetitionSelect(options))


class GuidedMatchSelect(discord.ui.Select):
    def __init__(self, competition_key, matches):
        self.competition_key = competition_key
        options = []
        for m in matches[:25]:
            label, desc = format_guided_match_label(m, m.get("requester_id") or "")
            # requester_id viene aggiunto sotto
            options.append(discord.SelectOption(
                label=label,
                value=f"{m['_result_kind']}:{m['id']}",
                description=desc,
            ))
        super().__init__(placeholder="Scegli avversario/partita...", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        kind, raw_id = self.values[0].split(":", 1)
        match_id = safe_int(raw_id)
        await interaction.response.send_modal(GuidedResultModal(kind, match_id))


class GuidedMatchSelectView(discord.ui.View):
    def __init__(self, competition_key, matches):
        super().__init__(timeout=180)
        # aggiunge requester_id per label corrette
        self.add_item(GuidedMatchSelect(competition_key, matches))


class GuidedResultModal(discord.ui.Modal, title="Risultato partita"):
    gol_miei = discord.ui.TextInput(label="Gol tuoi", placeholder="Esempio: 5", required=True, max_length=2)
    gol_avversario = discord.ui.TextInput(label="Gol avversario", placeholder="Esempio: 2", required=True, max_length=2)
    marcatori_miei = discord.ui.TextInput(
        label="Marcatori tuoi",
        placeholder="Esempio: Mbappe 3, Rodri 2",
        required=False,
        style=discord.TextStyle.paragraph,
        max_length=900,
    )
    marcatori_avversario = discord.ui.TextInput(
        label="Marcatori avversario",
        placeholder="Esempio: Lautaro 2",
        required=False,
        style=discord.TextStyle.paragraph,
        max_length=900,
    )

    def __init__(self, kind, match_id):
        super().__init__()
        self.kind = kind
        self.match_id = int(match_id)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            my_goals = safe_int(self.gol_miei.value)
            opp_goals = safe_int(self.gol_avversario.value)
        except Exception:
            await interaction.response.send_message("I gol devono essere numeri.", ephemeral=True)
            return

        my_scorers = parse_scorers_input(str(self.marcatori_miei.value))
        opp_scorers = parse_scorers_input(str(self.marcatori_avversario.value))

        if my_goals == 0 and opp_goals == 0:
            my_scorers = []
            opp_scorers = []
        else:
            if scorers_total(my_scorers) != my_goals:
                await interaction.response.send_message(
                    f"❌ I tuoi marcatori sommano **{scorers_total(my_scorers)}** gol, ma hai inserito **{my_goals}** gol.",
                    ephemeral=True,
                )
                return
            if scorers_total(opp_scorers) != opp_goals:
                await interaction.response.send_message(
                    f"❌ I marcatori avversari sommano **{scorers_total(opp_scorers)}** gol, ma hai inserito **{opp_goals}** gol avversari.",
                    ephemeral=True,
                )
                return

        if self.kind in ("championship", "european_group"):
            await self._submit_championship(interaction, my_goals, opp_goals, my_scorers, opp_scorers)
        elif self.kind == "national_cup":
            await self._submit_national_cup(interaction, my_goals, opp_goals, my_scorers, opp_scorers)
        else:
            await interaction.response.send_message("Tipo partita non riconosciuto.", ephemeral=True)

    async def _submit_championship(self, interaction, my_goals, opp_goals, my_scorers, opp_scorers):
        conn = connect()
        cur = conn.cursor()
        cur.execute("SELECT * FROM championship_matches WHERE id = %s AND status = 'pending'", (self.match_id,))
        match = cur.fetchone()

        if not match:
            conn.close()
            await interaction.response.send_message("Partita non trovata o già inserita.", ephemeral=True)
            return

        user_id = str(interaction.user.id)
        if user_id not in (str(match["home_id"]), str(match["away_id"])):
            conn.close()
            await interaction.response.send_message("Non fai parte di questa partita.", ephemeral=True)
            return

        is_home = user_id == str(match["home_id"])
        home_goals = my_goals if is_home else opp_goals
        away_goals = opp_goals if is_home else my_goals
        confirm_by = match["away_id"] if is_home else match["home_id"]

        home_scorers = my_scorers if is_home else opp_scorers
        away_scorers = opp_scorers if is_home else my_scorers

        cur.execute("""
            UPDATE championship_matches
            SET home_goals = %s,
                away_goals = %s,
                status = 'awaiting_confirmation',
                submitted_by = %s,
                confirm_by = %s
            WHERE id = %s
        """, (home_goals, away_goals, user_id, str(confirm_by), self.match_id))

        cur.execute("DELETE FROM match_scorers WHERE match_id = %s", (self.match_id,))
        insert_scorers_for_match(cur, self.match_id, match["home_id"], home_scorers)
        insert_scorers_for_match(cur, self.match_id, match["away_id"], away_scorers)

        conn.commit()
        conn.close()

        embed = build_result_embed(self.match_id)
        await interaction.response.send_message(
            content=f"<@{confirm_by}> devi confermare o contestare il risultato.",
            embed=embed,
            view=GuidedResultConfirmView("championship", self.match_id, str(confirm_by)),
        )

    async def _submit_national_cup(self, interaction, my_goals, opp_goals, my_scorers, opp_scorers):
        conn = connect()
        cur = conn.cursor()
        cur.execute("""
            SELECT m.*, c.name AS competition_name
            FROM national_cup_matches m
            LEFT JOIN national_cups c ON c.id = m.cup_id
            WHERE m.id = %s
              AND COALESCE(m.status, 'pending') IN ('pending', 'active')
        """, (self.match_id,))
        match = cur.fetchone()

        if not match:
            conn.close()
            await interaction.response.send_message("Partita di coppa non trovata o già inserita.", ephemeral=True)
            return

        user_id = str(interaction.user.id)
        if user_id not in (str(match["home_id"]), str(match["away_id"])):
            conn.close()
            await interaction.response.send_message("Non fai parte di questa partita.", ephemeral=True)
            return

        is_home = user_id == str(match["home_id"])
        home_goals = my_goals if is_home else opp_goals
        away_goals = opp_goals if is_home else my_goals
        confirm_by = match["away_id"] if is_home else match["home_id"]

        cur.execute("""
            UPDATE national_cup_matches
            SET home_goals = %s,
                away_goals = %s,
                status = 'awaiting_confirmation'
            WHERE id = %s
        """, (home_goals, away_goals, self.match_id))
        conn.commit()
        conn.close()

        embed = build_guided_cup_embed(self.match_id)
        await interaction.response.send_message(
            content=f"<@{confirm_by}> devi confermare o contestare il risultato.",
            embed=embed,
            view=GuidedResultConfirmView("national_cup", self.match_id, str(confirm_by)),
        )


def build_guided_cup_embed(match_id):
    conn = connect()
    cur = conn.cursor()
    cur.execute("""
        SELECT m.*, c.name AS competition_name
        FROM national_cup_matches m
        LEFT JOIN national_cups c ON c.id = m.cup_id
        WHERE m.id = %s
    """, (match_id,))
    m = cur.fetchone()
    conn.close()

    if not m:
        return discord.Embed(title="Coppa", description="Partita non trovata.", color=discord.Color.red())

    home = get_manager_club_name_by_discord(m.get("home_id"), m.get("home_name"))
    away = get_manager_club_name_by_discord(m.get("away_id"), m.get("away_name"))
    status_label = {
        "awaiting_confirmation": "⏳ In attesa conferma",
        "confirmed": "✅ Ufficiale",
        "contested": "⚠️ Contestato",
        "pending": "📅 Da giocare",
        "active": "📅 Attiva",
    }.get(str(m.get("status") or "pending"), str(m.get("status") or "pending"))

    embed = discord.Embed(
        title=f"🏆 {m.get('competition_name') or 'Coppa Nazionale'} — Turno {m.get('round_number')}",
        description=f"**{home} {safe_int(m.get('home_goals'))} - {safe_int(m.get('away_goals'))} {away}**",
        color=discord.Color.gold(),
    )
    embed.add_field(name="Stato", value=status_label, inline=False)
    embed.set_footer(text=f"ID coppa: {match_id}")
    return embed


class GuidedResultConfirmView(discord.ui.View):
    def __init__(self, kind, match_id, confirm_by):
        super().__init__(timeout=86400)
        self.kind = kind
        self.match_id = int(match_id)
        self.confirm_by = str(confirm_by)

    @discord.ui.button(label="Conferma", style=discord.ButtonStyle.success)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        if str(interaction.user.id) != self.confirm_by:
            await interaction.response.send_message("Solo l'avversario può confermare questo risultato.", ephemeral=True)
            return

        conn = connect()
        cur = conn.cursor()

        if self.kind in ("championship", "european_group"):
            cur.execute("UPDATE championship_matches SET status = 'confirmed' WHERE id = %s", (self.match_id,))
            conn.commit()
            conn.close()
            sync_site_after_confirmed_match(self.match_id)
            embed = build_result_embed(self.match_id)
            await interaction.response.edit_message(content="✅ Risultato confermato e sito aggiornato.", embed=embed, view=None)
            return

        if self.kind == "national_cup":
            cur.execute("""
                UPDATE national_cup_matches
                SET status = 'confirmed'
                WHERE id = %s
            """, (self.match_id,))
            cur.execute("""
                SELECT m.*, c.name AS competition_name
                FROM national_cup_matches m
                LEFT JOIN national_cups c ON c.id = m.cup_id
                WHERE m.id = %s
            """, (self.match_id,))
            m = cur.fetchone()
            conn.commit()
            conn.close()

            if m:
                home = get_manager_club_name_by_discord(m.get("home_id"), m.get("home_name"))
                away = get_manager_club_name_by_discord(m.get("away_id"), m.get("away_name"))
                sync_site_cup_result(
                    m.get("competition_name") or "Coppa Nazionale",
                    f"Turno {m.get('round_number')}",
                    home,
                    away,
                    safe_int(m.get("home_goals")),
                    safe_int(m.get("away_goals")),
                )
            embed = build_guided_cup_embed(self.match_id)
            await interaction.response.edit_message(content="✅ Risultato coppa confermato e sito aggiornato.", embed=embed, view=None)
            return

        conn.close()
        await interaction.response.send_message("Tipo risultato non riconosciuto.", ephemeral=True)

    @discord.ui.button(label="Contesta", style=discord.ButtonStyle.danger)
    async def contest(self, interaction: discord.Interaction, button: discord.ui.Button):
        if str(interaction.user.id) != self.confirm_by:
            await interaction.response.send_message("Solo l'avversario può contestare questo risultato.", ephemeral=True)
            return

        conn = connect()
        cur = conn.cursor()
        if self.kind in ("championship", "european_group"):
            cur.execute("UPDATE championship_matches SET status = 'contested' WHERE id = %s", (self.match_id,))
            conn.commit()
            conn.close()
            embed = build_result_embed(self.match_id)
        else:
            cur.execute("UPDATE national_cup_matches SET status = 'contested' WHERE id = %s", (self.match_id,))
            conn.commit()
            conn.close()
            embed = build_guided_cup_embed(self.match_id)

        await interaction.response.edit_message(content="⚠️ Risultato contestato. Staff richiesto.", embed=embed, view=None)




# ================= GENERATORI COMPETIZIONI STAFF =================
# Comandi aggiunti: /genera_campionato, /genera_coppa_nazionale, /genera_coppa_europea

def ensure_competition_generator_tables():
    conn = connect()
    cur = conn.cursor()
    for sql in [
        "ALTER TABLE championships ADD COLUMN IF NOT EXISTS status TEXT DEFAULT 'active'",
        "ALTER TABLE championships ADD COLUMN IF NOT EXISTS group_count INTEGER DEFAULT 1",
        "ALTER TABLE championships ADD COLUMN IF NOT EXISTS teams_per_group INTEGER DEFAULT 0",
        "ALTER TABLE championships ADD COLUMN IF NOT EXISTS created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP",
        "ALTER TABLE championship_groups ADD COLUMN IF NOT EXISTS championship_id INTEGER",
        "ALTER TABLE championship_groups ADD COLUMN IF NOT EXISTS name TEXT",
        "ALTER TABLE championship_groups ADD COLUMN IF NOT EXISTS group_name TEXT",
        "ALTER TABLE championship_players ADD COLUMN IF NOT EXISTS championship_id INTEGER",
        "ALTER TABLE championship_players ADD COLUMN IF NOT EXISTS group_id INTEGER",
        "ALTER TABLE championship_players ADD COLUMN IF NOT EXISTS discord_id TEXT",
        "ALTER TABLE championship_players ADD COLUMN IF NOT EXISTS display_name TEXT",
        "ALTER TABLE championships ADD COLUMN IF NOT EXISTS type TEXT DEFAULT 'campionato'",
        "ALTER TABLE championships ADD COLUMN IF NOT EXISTS participants INTEGER DEFAULT 0",
        "ALTER TABLE championships ADD COLUMN IF NOT EXISTS european_pass INTEGER DEFAULT 0",
        "ALTER TABLE championship_players ADD COLUMN IF NOT EXISTS club_name TEXT",
        "ALTER TABLE championship_matches ADD COLUMN IF NOT EXISTS leg TEXT DEFAULT 'andata'",
        "ALTER TABLE national_cups ADD COLUMN IF NOT EXISTS parent_championship_id INTEGER",
        "ALTER TABLE national_cup_matches ADD COLUMN IF NOT EXISTS leg TEXT DEFAULT 'andata'",
        "ALTER TABLE standings ADD COLUMN IF NOT EXISTS competition_name TEXT",
        "ALTER TABLE standings ADD COLUMN IF NOT EXISTS competition_type TEXT",
        "ALTER TABLE standings ADD COLUMN IF NOT EXISTS club_name TEXT",
        "ALTER TABLE standings ADD COLUMN IF NOT EXISTS played INTEGER DEFAULT 0",
        "ALTER TABLE standings ADD COLUMN IF NOT EXISTS wins INTEGER DEFAULT 0",
        "ALTER TABLE standings ADD COLUMN IF NOT EXISTS draws INTEGER DEFAULT 0",
        "ALTER TABLE standings ADD COLUMN IF NOT EXISTS losses INTEGER DEFAULT 0",
        "ALTER TABLE standings ADD COLUMN IF NOT EXISTS goals_for INTEGER DEFAULT 0",
        "ALTER TABLE standings ADD COLUMN IF NOT EXISTS goals_against INTEGER DEFAULT 0",
        "ALTER TABLE standings ADD COLUMN IF NOT EXISTS points INTEGER DEFAULT 0",
    ]:
        try:
            cur.execute(sql)
        except Exception as e:
            print(f"[GENERATOR TABLES] {e}")
    # Compatibilità con vecchie tabelle: alcune avevano group_name NOT NULL.
    for sql in [
        "ALTER TABLE championship_groups ALTER COLUMN group_name DROP NOT NULL",
        "UPDATE championship_groups SET name = COALESCE(name, group_name, 'Girone Unico') WHERE name IS NULL",
        "UPDATE championship_groups SET group_name = COALESCE(group_name, name, 'Girone Unico') WHERE group_name IS NULL",
    ]:
        try:
            cur.execute(sql)
        except Exception as e:
            print(f"[GENERATOR TABLES COMPAT] {e}")

    conn.commit()
    conn.close()


def generator_fetch_participants():
    conn = connect()
    cur = conn.cursor()
    cur.execute("""
        SELECT discord_id,
               COALESCE(NULLIF(manager_name, ''), NULLIF(name, ''), discord_id) AS display_name,
               COALESCE(NULLIF(club_name, ''), NULLIF(name, ''), discord_id) AS club_name
        FROM managers
        WHERE discord_id IS NOT NULL
        ORDER BY club_name ASC, display_name ASC
    """)
    rows = cur.fetchall()
    conn.close()
    cleaned = []
    seen = set()
    for r in rows:
        did = str(r.get("discord_id") or "").strip()
        if not did or did in seen:
            continue
        seen.add(did)
        cleaned.append({"discord_id": did, "display_name": str(r.get("display_name") or did), "club_name": str(r.get("club_name") or r.get("display_name") or did)})
    return cleaned


def generator_round_robin(teams, double_round=True):
    teams = list(teams)
    if len(teams) < 2:
        return []
    if len(teams) % 2 == 1:
        teams.append(None)
    n = len(teams)
    arr = list(teams)
    rounds = []
    for rnd in range(n - 1):
        pairs = []
        for i in range(n // 2):
            a = arr[i]
            b = arr[n - 1 - i]
            if a is None or b is None:
                continue
            home, away = (a, b) if (rnd + i) % 2 == 0 else (b, a)
            pairs.append((home, away))
        rounds.append(pairs)
        arr = [arr[0]] + [arr[-1]] + arr[1:-1]
    if double_round:
        rounds = rounds + [[(away, home) for home, away in pairs] for pairs in rounds]
    return rounds


def generator_insert_initial_standings(cur, competition_name, competition_type, teams):
    try:
        cur.execute("DELETE FROM standings WHERE competition_name = %s AND competition_type = %s", (competition_name, competition_type))
    except Exception:
        pass
    for team in teams:
        cur.execute("""
            INSERT INTO standings
            (competition_name, competition_type, club_name, played, wins, draws, losses, goals_for, goals_against, points)
            VALUES (%s, %s, %s, 0, 0, 0, 0, 0, 0, 0)
        """, (competition_name, competition_type, team["club_name"]))


def generator_insert_championship(name, teams, *, competition_type="campionato", group_count=1, teams_per_group=0, european_pass=0):
    ensure_competition_generator_tables()
    conn = connect()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO championships (name, status, group_count, teams_per_group, type, participants, european_pass)
        VALUES (%s, 'active', %s, %s, %s, %s, %s)
        RETURNING id
    """, (name, group_count, teams_per_group or len(teams), competition_type, len(teams), european_pass))
    championship_id = cur.fetchone()["id"]
    if group_count <= 1:
        grouped = [("Girone Unico", teams)]
    else:
        shuffled = list(teams)
        random.shuffle(shuffled)
        grouped = [(f"Girone {chr(65+i)}", shuffled[i::group_count]) for i in range(group_count)]
    groups = []
    for group_name, group_teams in grouped:
        cur.execute("INSERT INTO championship_groups (championship_id, name, group_name) VALUES (%s, %s, %s) RETURNING id", (championship_id, group_name, group_name))
        group_id = cur.fetchone()["id"]
        groups.append((group_id, group_name, group_teams))
        for t in group_teams:
            cur.execute("""
                INSERT INTO championship_players (championship_id, group_id, discord_id, display_name, club_name)
                VALUES (%s, %s, %s, %s, %s)
            """, (championship_id, group_id, t["discord_id"], t["display_name"], t["club_name"]))
        rounds = generator_round_robin(group_teams, double_round=True)
        first_leg_last = len(rounds) // 2
        for round_idx, pairs in enumerate(rounds, start=1):
            leg = "andata" if round_idx <= first_leg_last else "ritorno"
            for home, away in pairs:
                cur.execute("""
                    INSERT INTO championship_matches
                    (championship_id, group_id, round_number, home_id, away_id, home_name, away_name, status, leg)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, 'pending', %s)
                """, (championship_id, group_id, round_idx, home["discord_id"], away["discord_id"], home["club_name"], away["club_name"], leg))
    if competition_type == "campionato":
        generator_insert_initial_standings(cur, name, "Campionati", teams)
    elif competition_type == "europea":
        generator_insert_initial_standings(cur, name, "Coppe Europee", teams)
    conn.commit()
    conn.close()
    return championship_id, groups


def generator_create_national_cup(championship_id):
    ensure_competition_generator_tables()
    conn = connect()
    cur = conn.cursor()
    cur.execute("SELECT * FROM championships WHERE id = %s", (championship_id,))
    champ = cur.fetchone()
    if not champ:
        conn.close()
        raise ValueError("Campionato non trovato")
    cur.execute("""
        SELECT discord_id, COALESCE(NULLIF(club_name, ''), display_name, discord_id) AS club_name, COALESCE(display_name, discord_id) AS display_name
        FROM championship_players
        WHERE championship_id = %s
        ORDER BY id ASC
    """, (championship_id,))
    rows = cur.fetchall()
    teams = []
    seen = set()
    for r in rows:
        did = str(r.get("discord_id") or "").strip()
        if did and did not in seen:
            seen.add(did)
            teams.append({"discord_id": did, "display_name": str(r.get("display_name") or did), "club_name": str(r.get("club_name") or r.get("display_name") or did)})
    if len(teams) < 2:
        conn.close()
        raise ValueError("Servono almeno 2 squadre nel campionato scelto")
    cup_name = f"Coppa Nazionale - {champ['name']}"
    cur.execute("""
        INSERT INTO national_cups (championship_id, parent_championship_id, name, status)
        VALUES (%s, %s, %s, 'active')
        RETURNING id
    """, (championship_id, championship_id, cup_name))
    cup_id = cur.fetchone()["id"]
    random.shuffle(teams)
    matches = 0
    for i in range(0, len(teams) - 1, 2):
        home, away = teams[i], teams[i + 1]
        cur.execute("""
            INSERT INTO national_cup_matches (cup_id, round_number, home_id, away_id, home_name, away_name, status, leg)
            VALUES (%s, 1, %s, %s, %s, %s, 'pending', 'andata')
        """, (cup_id, home["discord_id"], away["discord_id"], home["club_name"], away["club_name"]))
        matches += 1
    generator_insert_initial_standings(cur, cup_name, "Coppa Nazionale", teams)
    conn.commit()
    conn.close()
    return cup_id, cup_name, len(teams), matches


def generator_championship_choices():
    conn = connect()
    cur = conn.cursor()
    cur.execute("""
        SELECT id, name, COALESCE(type, 'campionato') AS type
        FROM championships
        WHERE COALESCE(status, 'active') = 'active'
        ORDER BY id DESC
        LIMIT 25
    """)
    rows = cur.fetchall()
    conn.close()
    return rows


class CompetitionNameModal(discord.ui.Modal, title="Genera Campionato"):
    nome = discord.ui.TextInput(label="Nome campionato", placeholder="Esempio: Serie A", required=True, max_length=80)
    async def on_submit(self, interaction: discord.Interaction):
        participants = generator_fetch_participants()
        if len(participants) < 2:
            await interaction.response.send_message("❌ Servono almeno 2 manager con club assegnato.", ephemeral=True)
            return
        await interaction.response.send_message(f"🏆 **{self.nome.value}**\nSeleziona i partecipanti. Puoi cambiare pagina se sono più di 25.", view=PagedParticipantSelectView("campionato", str(self.nome.value), participants), ephemeral=True)


class EuropeanCupModal(discord.ui.Modal, title="Genera Coppa Europea"):
    nome = discord.ui.TextInput(label="Nome competizione", placeholder="Champions League / Europa League / Conference League", required=True, max_length=80)
    partecipanti = discord.ui.TextInput(label="Numero partecipanti", placeholder="Esempio: 32", required=True, max_length=3)
    gironi = discord.ui.TextInput(label="Numero gironi", placeholder="Esempio: 8", required=True, max_length=2)
    passano = discord.ui.TextInput(label="Quante squadre passano per girone", placeholder="Esempio: 2", required=True, max_length=2)
    async def on_submit(self, interaction: discord.Interaction):
        total, groups, advance = safe_int(self.partecipanti.value), safe_int(self.gironi.value), safe_int(self.passano.value)
        if total < 2 or groups < 1 or advance < 1:
            await interaction.response.send_message("❌ Numeri non validi.", ephemeral=True)
            return
        participants = generator_fetch_participants()
        if len(participants) < total:
            await interaction.response.send_message(f"❌ Hai chiesto {total} partecipanti ma disponibili sono {len(participants)}.", ephemeral=True)
            return
        payload = f"{self.nome.value}||{total}||{groups}||{advance}"
        await interaction.response.send_message(f"🌍 **{self.nome.value}**\nSeleziona **{total}** partecipanti. Se sono più di 25 usa le pagine.", view=PagedParticipantSelectView("europea", payload, participants), ephemeral=True)


class PagedParticipantSelect(discord.ui.Select):
    def __init__(self, parent):
        self.parent_view = parent
        start = parent.page * 25
        page_items = parent.participants[start:start + 25]
        options = []
        for p in page_items:
            selected = "✅ " if p["discord_id"] in parent.selected else ""
            options.append(discord.SelectOption(label=f"{selected}{p['club_name']}"[:100], value=p["discord_id"], description=f"@{p['display_name']}"[:100]))
        if not options:
            options = [discord.SelectOption(label="Nessun partecipante", value="none")]
        super().__init__(placeholder=f"Partecipanti pagina {parent.page + 1}/{parent.max_page + 1}", min_values=0, max_values=min(25, len(options)), options=options)
    async def callback(self, interaction: discord.Interaction):
        if "none" in self.values:
            await interaction.response.defer()
            return
        for value in self.values:
            if value in self.parent_view.selected:
                self.parent_view.selected.remove(value)
            else:
                self.parent_view.selected.add(value)
        await self.parent_view.refresh(interaction)


class PagedParticipantSelectView(discord.ui.View):
    def __init__(self, mode, payload, participants):
        super().__init__(timeout=900)
        self.mode, self.payload, self.participants = mode, payload, participants
        self.selected = set()
        self.page = 0
        self.max_page = max(0, (len(participants) - 1) // 25)
        self.rebuild()
    def rebuild(self):
        self.clear_items()
        self.add_item(PagedParticipantSelect(self))
        self.add_item(PrevPageButton())
        self.add_item(NextPageButton())
        self.add_item(GenerateCompetitionButton())
    async def refresh(self, interaction):
        self.rebuild()
        await interaction.response.edit_message(content=self.status_text(), view=self)
    def status_text(self):
        if self.mode == "campionato":
            return f"🏆 **{self.payload}**\nSelezionati: **{len(self.selected)}**."
        name, total, groups, advance = self.payload.split("||")
        return f"🌍 **{name}**\nSelezionati: **{len(self.selected)}/{total}** • Gironi: **{groups}** • Passano: **{advance}**."
    def selected_participants(self):
        by_id = {p["discord_id"]: p for p in self.participants}
        return [by_id[x] for x in self.selected if x in by_id]


class PrevPageButton(discord.ui.Button):
    def __init__(self): super().__init__(label="◀ Pagina", style=discord.ButtonStyle.secondary)
    async def callback(self, interaction: discord.Interaction):
        self.view.page = max(0, self.view.page - 1)
        await self.view.refresh(interaction)


class NextPageButton(discord.ui.Button):
    def __init__(self): super().__init__(label="Pagina ▶", style=discord.ButtonStyle.secondary)
    async def callback(self, interaction: discord.Interaction):
        self.view.page = min(self.view.max_page, self.view.page + 1)
        await self.view.refresh(interaction)


class GenerateCompetitionButton(discord.ui.Button):
    def __init__(self): super().__init__(label="✅ Genera", style=discord.ButtonStyle.success)
    async def callback(self, interaction: discord.Interaction):
        if not is_league_admin(interaction):
            await interaction.response.send_message("❌ Solo lo staff può generare competizioni.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        view = self.view
        selected = view.selected_participants()
        if view.mode == "campionato":
            if len(selected) < 2:
                await interaction.followup.send("❌ Seleziona almeno 2 partecipanti.", ephemeral=True)
                return
            champ_id, groups = generator_insert_championship(view.payload, selected, competition_type="campionato", group_count=1)
            total_matches = sum(len(pairs) for _gid, _gname, group_teams in groups for pairs in generator_round_robin(group_teams, True))
            await interaction.followup.send(f"✅ Campionato **{view.payload}** generato.\nPartecipanti: **{len(selected)}**\nPartite create: **{total_matches}**\nOra puoi usare `/genera_coppa_nazionale` e poi `/avvia_andata`.", ephemeral=True)
            return
        name, total_raw, groups_raw, advance_raw = view.payload.split("||")
        total, group_count, advance = safe_int(total_raw), safe_int(groups_raw), safe_int(advance_raw)
        if len(selected) != total:
            await interaction.followup.send(f"❌ Devi selezionare esattamente {total} partecipanti. Ora sono {len(selected)}.", ephemeral=True)
            return
        champ_id, groups = generator_insert_championship(name, selected, competition_type="europea", group_count=group_count, teams_per_group=max(1, total // max(1, group_count)), european_pass=advance)
        await interaction.followup.send(f"✅ Coppa europea **{name}** generata.\nPartecipanti: **{len(selected)}** • Gironi: **{group_count}** • Passano: **{advance}**.\nFase gironi creata con andata/ritorno. Il tabellone knockout sarà gestito dopo la fase a gironi.", ephemeral=True)


class ChampionshipCupSelect(discord.ui.Select):
    def __init__(self, championships):
        options = [discord.SelectOption(label=str(c.get("name") or f"Campionato {c.get('id')}")[:100], value=str(c.get("id")), description=f"ID {c.get('id')} • {c.get('type') or 'campionato'}"[:100]) for c in championships[:25]]
        if not options:
            options = [discord.SelectOption(label="Nessun campionato", value="none")]
        super().__init__(placeholder="Scegli il campionato per la Coppa Nazionale...", min_values=1, max_values=1, options=options)
    async def callback(self, interaction: discord.Interaction):
        if self.values[0] == "none":
            await interaction.response.send_message("❌ Nessun campionato disponibile.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            cup_id, cup_name, teams, matches = generator_create_national_cup(int(self.values[0]))
            await interaction.followup.send(f"✅ **{cup_name}** generata.\nPartecipanti: **{teams}**\nPartite primo turno: **{matches}**\nFormato: **partita secca / solo andata**.", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"❌ Errore generazione coppa: `{type(e).__name__}: {e}`", ephemeral=True)


class ChampionshipCupSelectView(discord.ui.View):
    def __init__(self, championships):
        super().__init__(timeout=300)
        self.add_item(ChampionshipCupSelect(championships))


@tree.command(name="genera_campionato", description="Staff: genera un campionato con calendario andata/ritorno")
async def genera_campionato(interaction: discord.Interaction):
    if not is_league_admin(interaction):
        await interaction.response.send_message("❌ Solo lo staff può usare questo comando.", ephemeral=True)
        return
    await interaction.response.send_modal(CompetitionNameModal())


@tree.command(name="genera_coppa_nazionale", description="Staff: genera una coppa nazionale dal campionato scelto")
async def genera_coppa_nazionale(interaction: discord.Interaction):
    await safe_defer(interaction, ephemeral=True, thinking=True)

    if not is_league_admin(interaction):
        await interaction.followup.send("❌ Solo lo staff può usare questo comando.", ephemeral=True)
        return

    ensure_competition_generator_tables()
    choices = generator_championship_choices()
    await interaction.followup.send(
        "🇮🇹 Scegli il campionato: la coppa userà solo gli iscritti di quel campionato.",
        view=ChampionshipCupSelectView(choices),
        ephemeral=True,
    )


@tree.command(name="genera_coppa_europea", description="Staff: genera Champions/Europa/Conference con gironi")
async def genera_coppa_europea(interaction: discord.Interaction):
    if not is_league_admin(interaction):
        await interaction.response.send_message("❌ Solo lo staff può usare questo comando.", ephemeral=True)
        return
    await interaction.response.send_modal(EuropeanCupModal())

# ================= FINE GENERATORI COMPETIZIONI STAFF =================


@tree.command(name="avvia_andata", description="Staff: abilita l'inserimento risultati dell'andata")
async def avvia_andata(interaction: discord.Interaction):
    await safe_defer(interaction, ephemeral=True, thinking=True)
    if not is_league_admin(interaction):
        await interaction.followup.send("❌ Solo lo staff può usare questo comando.", ephemeral=True)
        return
    set_active_leg("andata")
    await interaction.followup.send("✅ Andata attivata: i player vedranno solo le partite di andata in `/risultato`.", ephemeral=True)


@tree.command(name="avvia_ritorno", description="Staff: abilita l'inserimento risultati del ritorno")
async def avvia_ritorno(interaction: discord.Interaction):
    await safe_defer(interaction, ephemeral=True, thinking=True)
    if not is_league_admin(interaction):
        await interaction.followup.send("❌ Solo lo staff può usare questo comando.", ephemeral=True)
        return
    set_active_leg("ritorno")
    await interaction.followup.send("✅ Ritorno attivato: i player vedranno solo le partite di ritorno in `/risultato`.", ephemeral=True)

# ================= FINE RISULTATI GUIDATI =================




# ================= RISULTATI/CALENDARIO - UNIFIED FIX TUTTE COMPETIZIONI =================

def db_connect_safe():
    try:
        return connect()
    except Exception:
        url = os.getenv("DATABASE_URL") or os.getenv("POSTGRES_URL") or os.getenv("SUPABASE_DB_URL")
        if not url:
            raise RuntimeError("DATABASE_URL/POSTGRES_URL non configurato.")
        import psycopg2
        return psycopg2.connect(url)

def row_get(row, key, default=None):
    try:
        return row.get(key, default)
    except Exception:
        try:
            return row[key]
        except Exception:
            return default

def get_active_leg_safe():
    try:
        return get_active_leg()
    except Exception:
        return "andata"

def competition_group_from_type(value):
    v = normalize_text(value or "")
    if "nazionale" in v or ("coppa" in v and "europe" not in v and "champions" not in v and "europa" not in v and "conference" not in v):
        return "Coppa Nazionale"
    if "champions" in v or "europa" in v or "conference" in v or "europe" in v:
        return "Coppe Europee"
    return "Campionati"

def get_manager_club_for_user(discord_id):
    conn = db_connect_safe()
    cur = conn.cursor()
    try:
        for sql in [
            "SELECT club_name FROM managers WHERE discord_id = %s LIMIT 1",
            "SELECT team_name FROM real_team_assignments WHERE discord_id = %s LIMIT 1",
        ]:
            try:
                cur.execute(sql, (str(discord_id),))
                row = cur.fetchone()
                if row:
                    club = row_get(row, "club_name") or row_get(row, "team_name")
                    if club is None:
                        try:
                            club = row[0]
                        except Exception:
                            club = None
                    if club:
                        return str(club)
            except Exception:
                pass
    finally:
        conn.close()
    return None

def ensure_results_calendar_tables():
    conn = db_connect_safe()
    cur = conn.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS fixtures (
        id BIGSERIAL PRIMARY KEY,
        competition_name TEXT,
        competition_type TEXT,
        round TEXT,
        leg TEXT,
        home_user_id TEXT,
        away_user_id TEXT,
        home_club TEXT,
        away_club TEXT,
        home_goals INTEGER DEFAULT 0,
        away_goals INTEGER DEFAULT 0,
        played BOOLEAN DEFAULT FALSE,
        active BOOLEAN DEFAULT TRUE,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)

    for sql in [
        "ALTER TABLE fixtures ADD COLUMN IF NOT EXISTS competition_name TEXT",
        "ALTER TABLE fixtures ADD COLUMN IF NOT EXISTS competition_type TEXT",
        "ALTER TABLE fixtures ADD COLUMN IF NOT EXISTS round TEXT",
        "ALTER TABLE fixtures ADD COLUMN IF NOT EXISTS leg TEXT",
        "ALTER TABLE fixtures ADD COLUMN IF NOT EXISTS home_user_id TEXT",
        "ALTER TABLE fixtures ADD COLUMN IF NOT EXISTS away_user_id TEXT",
        "ALTER TABLE fixtures ADD COLUMN IF NOT EXISTS home_club TEXT",
        "ALTER TABLE fixtures ADD COLUMN IF NOT EXISTS away_club TEXT",
        "ALTER TABLE fixtures ADD COLUMN IF NOT EXISTS home_goals INTEGER DEFAULT 0",
        "ALTER TABLE fixtures ADD COLUMN IF NOT EXISTS away_goals INTEGER DEFAULT 0",
        "ALTER TABLE fixtures ADD COLUMN IF NOT EXISTS played BOOLEAN DEFAULT FALSE",
        "ALTER TABLE fixtures ADD COLUMN IF NOT EXISTS active BOOLEAN DEFAULT TRUE",
        "ALTER TABLE fixtures ADD COLUMN IF NOT EXISTS created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP",
        "ALTER TABLE fixtures ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP",
    ]:
        try:
            cur.execute(sql)
        except Exception as e:
            print(f"[ALTER fixtures] {e}")

    cur.execute("""
    CREATE TABLE IF NOT EXISTS match_results (
        id BIGSERIAL PRIMARY KEY,
        source_table TEXT,
        source_match_id TEXT,
        competition_name TEXT,
        competition_type TEXT,
        round TEXT,
        home_team TEXT,
        away_team TEXT,
        home_score INTEGER DEFAULT 0,
        away_score INTEGER DEFAULT 0,
        winner TEXT,
        status TEXT DEFAULT 'played',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS goalscorers (
        id BIGSERIAL PRIMARY KEY,
        fixture_id BIGINT,
        source_table TEXT,
        source_match_id TEXT,
        user_id TEXT,
        club_name TEXT,
        player_name TEXT,
        goals INTEGER DEFAULT 1,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)

    for sql in [
        "ALTER TABLE goalscorers ADD COLUMN IF NOT EXISTS user_id TEXT",
        "ALTER TABLE goalscorers ADD COLUMN IF NOT EXISTS club_name TEXT",
        "ALTER TABLE goalscorers ADD COLUMN IF NOT EXISTS player_name TEXT",
        "ALTER TABLE goalscorers ADD COLUMN IF NOT EXISTS goals INTEGER DEFAULT 1",
        "ALTER TABLE goalscorers ADD COLUMN IF NOT EXISTS source_table TEXT",
        "ALTER TABLE goalscorers ADD COLUMN IF NOT EXISTS source_match_id TEXT",
    ]:
        try:
            cur.execute(sql)
        except Exception:
            pass

    cur.execute("""
    CREATE TABLE IF NOT EXISTS standings (
        id BIGSERIAL PRIMARY KEY,
        competition_name TEXT,
        competition_type TEXT,
        club_name TEXT,
        logo_url TEXT,
        played INTEGER DEFAULT 0,
        wins INTEGER DEFAULT 0,
        draws INTEGER DEFAULT 0,
        losses INTEGER DEFAULT 0,
        goals_for INTEGER DEFAULT 0,
        goals_against INTEGER DEFAULT 0,
        points INTEGER DEFAULT 0,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS cup_matches (
        id BIGSERIAL PRIMARY KEY,
        source_table TEXT,
        source_match_id TEXT,
        competition_name TEXT,
        round TEXT,
        home_team TEXT,
        away_team TEXT,
        home_score INTEGER DEFAULT 0,
        away_score INTEGER DEFAULT 0,
        winner TEXT,
        status TEXT DEFAULT 'played',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)

    conn.commit()
    conn.close()

def unified_pending_matches(discord_id=None, only_user=True):
    """
    Legge TUTTE le partite ancora da disputare:
    - fixtures
    - championship_matches
    - national_cup_matches
    - european_cup_matches se esiste
    Le normalizza in dict unificati per calendario e /risultato.
    """
    ensure_results_calendar_tables()
    active_leg = normalize_text(get_active_leg_safe() or "andata")
    club = get_manager_club_for_user(discord_id) if discord_id else None
    out = []

    conn = db_connect_safe()
    cur = conn.cursor()

    # 1) fixtures nuove
    try:
        cur.execute("""
            SELECT *
            FROM fixtures
            WHERE COALESCE(played, FALSE) = FALSE
              AND COALESCE(active, TRUE) = TRUE
            ORDER BY competition_type, competition_name, round, id
        """)
        for r in cur.fetchall():
            leg = normalize_text(row_get(r, "leg", "") or "")
            if leg and leg not in ("unica", active_leg):
                continue
            home_club = str(row_get(r, "home_club", "") or "")
            away_club = str(row_get(r, "away_club", "") or "")
            home_id = str(row_get(r, "home_user_id", "") or "")
            away_id = str(row_get(r, "away_user_id", "") or "")
            if only_user and discord_id and club:
                if normalize_text(home_club) != normalize_text(club) and normalize_text(away_club) != normalize_text(club) and home_id != str(discord_id) and away_id != str(discord_id):
                    continue
            out.append({
                "source_table": "fixtures",
                "id": str(row_get(r, "id")),
                "competition_name": row_get(r, "competition_name", "Competizione"),
                "competition_type": row_get(r, "competition_type", "Campionati"),
                "round": row_get(r, "round", ""),
                "leg": row_get(r, "leg", ""),
                "home_user_id": home_id,
                "away_user_id": away_id,
                "home_club": home_club,
                "away_club": away_club,
            })
    except Exception as e:
        print(f"[UNIFIED fixtures] {e}")

    # 2) championship_matches vecchie
    try:
        cur.execute("""
            SELECT
                m.*,
                c.name AS competition_name,
                COALESCE(g.name, g.group_name, 'Girone Unico') AS group_label
            FROM championship_matches m
            LEFT JOIN championships c ON c.id = m.championship_id
            LEFT JOIN championship_groups g ON g.id = m.group_id
            WHERE COALESCE(m.status, 'pending') <> 'played'
              AND m.home_goals IS NULL
              AND m.away_goals IS NULL
            ORDER BY c.name, m.round_number, m.id
        """)
        for r in cur.fetchall():
            home_club = str(row_get(r, "home_name", "") or "")
            away_club = str(row_get(r, "away_name", "") or "")
            home_id = str(row_get(r, "home_id", "") or "")
            away_id = str(row_get(r, "away_id", "") or "")
            if only_user and discord_id and club:
                if normalize_text(home_club) != normalize_text(club) and normalize_text(away_club) != normalize_text(club) and home_id != str(discord_id) and away_id != str(discord_id):
                    continue
            out.append({
                "source_table": "championship_matches",
                "id": str(row_get(r, "id")),
                "competition_name": row_get(r, "competition_name", "Campionato"),
                "competition_type": "Campionati",
                "round": f"{row_get(r, 'group_label', 'Girone')} - Giornata {row_get(r, 'round_number', '')}",
                "leg": active_leg,
                "home_user_id": home_id,
                "away_user_id": away_id,
                "home_club": home_club,
                "away_club": away_club,
            })
    except Exception as e:
        print(f"[UNIFIED championship_matches] {e}")

    # 3) national_cup_matches
    try:
        cur.execute("""
            SELECT
                m.*,
                nc.name AS competition_name,
                c.name AS parent_championship
            FROM national_cup_matches m
            LEFT JOIN national_cups nc ON nc.id = m.cup_id
            LEFT JOIN championships c ON c.id = nc.championship_id
            WHERE COALESCE(m.status, 'pending') <> 'played'
              AND m.home_goals IS NULL
              AND m.away_goals IS NULL
            ORDER BY nc.name, m.round_number, m.id
        """)
        for r in cur.fetchall():
            home_club = str(row_get(r, "home_name", "") or "")
            away_club = str(row_get(r, "away_name", "") or "")
            home_id = str(row_get(r, "home_id", "") or "")
            away_id = str(row_get(r, "away_id", "") or "")
            if only_user and discord_id and club:
                if normalize_text(home_club) != normalize_text(club) and normalize_text(away_club) != normalize_text(club) and home_id != str(discord_id) and away_id != str(discord_id):
                    continue
            out.append({
                "source_table": "national_cup_matches",
                "id": str(row_get(r, "id")),
                "competition_name": row_get(r, "competition_name", "Coppa Nazionale"),
                "competition_type": "Coppa Nazionale",
                "round": f"Turno {row_get(r, 'round_number', '')}",
                "leg": "unica",
                "home_user_id": home_id,
                "away_user_id": away_id,
                "home_club": home_club,
                "away_club": away_club,
            })
    except Exception as e:
        print(f"[UNIFIED national_cup_matches] {e}")

    # 4) european_cup_matches, se presente
    try:
        cur.execute("""
            SELECT
                m.*,
                ec.name AS competition_name,
                ec.cup_type AS cup_type
            FROM european_cup_matches m
            LEFT JOIN european_cups ec ON ec.id = m.cup_id
            WHERE COALESCE(m.status, 'pending') <> 'played'
              AND m.home_goals IS NULL
              AND m.away_goals IS NULL
            ORDER BY ec.name, m.round_number, m.id
        """)
        for r in cur.fetchall():
            home_club = str(row_get(r, "home_name", "") or "")
            away_club = str(row_get(r, "away_name", "") or "")
            home_id = str(row_get(r, "home_id", "") or "")
            away_id = str(row_get(r, "away_id", "") or "")
            leg = normalize_text(row_get(r, "leg", "") or active_leg)
            if leg and leg not in ("unica", active_leg):
                continue
            if only_user and discord_id and club:
                if normalize_text(home_club) != normalize_text(club) and normalize_text(away_club) != normalize_text(club) and home_id != str(discord_id) and away_id != str(discord_id):
                    continue
            out.append({
                "source_table": "european_cup_matches",
                "id": str(row_get(r, "id")),
                "competition_name": row_get(r, "competition_name", "Coppa Europea"),
                "competition_type": row_get(r, "cup_type", "Coppe Europee") or "Coppe Europee",
                "round": f"Turno {row_get(r, 'round_number', '')}",
                "leg": row_get(r, "leg", active_leg),
                "home_user_id": home_id,
                "away_user_id": away_id,
                "home_club": home_club,
                "away_club": away_club,
            })
    except Exception as e:
        if "does not exist" not in str(e):
            print(f"[UNIFIED european_cup_matches] {e}")

    conn.close()
    return out

def get_guided_competition_options(discord_id):
    matches = unified_pending_matches(discord_id=discord_id, only_user=True)

    grouped = {}
    for m in matches:
        key = f"{m['competition_type']}|||{m['competition_name']}"
        grouped.setdefault(key, 0)
        grouped[key] += 1

    options = []
    for key, total in grouped.items():
        ctype, cname = key.split("|||", 1)
        group = competition_group_from_type(ctype or cname)
        emoji = "🏆"
        if group == "Coppa Nazionale":
            emoji = "🇮🇹"
        elif group == "Coppe Europee":
            emoji = "🌍"
        options.append(discord.SelectOption(
            label=str(cname)[:100],
            value=key[:100],
            description=f"{group} • {total} partite da disputare"[:100],
            emoji=emoji
        ))

    return options[:25]

def get_matches_for_competition(discord_id, competition_key):
    parts = str(competition_key).split("|||", 1)
    if len(parts) == 2:
        ctype, cname = parts
    else:
        ctype, cname = "", str(competition_key)

    matches = unified_pending_matches(discord_id=discord_id, only_user=True)
    return [
        m for m in matches
        if normalize_text(m["competition_name"]) == normalize_text(cname)
        and (not ctype or normalize_text(m["competition_type"]) == normalize_text(ctype))
    ][:25]

def upsert_standing_row(cur, competition_name, competition_type, club_name, gf, ga):
    gf = safe_int(gf)
    ga = safe_int(ga)
    win = 1 if gf > ga else 0
    draw = 1 if gf == ga else 0
    loss = 1 if gf < ga else 0
    pts = 3 if win else (1 if draw else 0)

    cur.execute("""
        SELECT id FROM standings
        WHERE LOWER(competition_name) = LOWER(%s)
          AND LOWER(competition_type) = LOWER(%s)
          AND LOWER(club_name) = LOWER(%s)
        LIMIT 1
    """, (competition_name, competition_type, club_name))
    existing = cur.fetchone()

    if existing:
        sid = row_get(existing, "id") or existing[0]
        cur.execute("""
            UPDATE standings
            SET played = COALESCE(played,0) + 1,
                wins = COALESCE(wins,0) + %s,
                draws = COALESCE(draws,0) + %s,
                losses = COALESCE(losses,0) + %s,
                goals_for = COALESCE(goals_for,0) + %s,
                goals_against = COALESCE(goals_against,0) + %s,
                points = COALESCE(points,0) + %s,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = %s
        """, (win, draw, loss, gf, ga, pts, sid))
    else:
        cur.execute("""
            INSERT INTO standings (
                competition_name, competition_type, club_name,
                played, wins, draws, losses, goals_for, goals_against, points, updated_at
            )
            VALUES (%s,%s,%s,1,%s,%s,%s,%s,%s,%s,CURRENT_TIMESTAMP)
        """, (competition_name, competition_type, club_name, win, draw, loss, gf, ga, pts))

def parse_scorers_text(raw):
    results = []
    for part in str(raw or "").split(","):
        item = part.strip()
        if not item:
            continue
        pieces = item.rsplit(" ", 1)
        if len(pieces) == 2 and pieces[1].strip().isdigit():
            name = pieces[0].strip()
            goals = int(pieces[1].strip())
        else:
            name = item
            goals = 1
        if name:
            results.append((name, max(1, goals)))
    return results

def save_unified_result_and_sync(source_table, match_id, home_goals, away_goals, home_scorers=None, away_scorers=None):
    home_scorers = home_scorers or []
    away_scorers = away_scorers or []
    conn = db_connect_safe()
    cur = conn.cursor()
    try:
        # Recupera la partita dal sistema unificato.
        all_matches = unified_pending_matches(discord_id=None, only_user=False)
        match = None
        for m in all_matches:
            if m["source_table"] == source_table and str(m["id"]) == str(match_id):
                match = m
                break
        if not match:
            raise RuntimeError("Partita non trovata o già giocata.")

        hg = safe_int(home_goals)
        ag = safe_int(away_goals)
        winner = match["home_club"] if hg > ag else (match["away_club"] if ag > hg else "Pareggio")

        if source_table == "fixtures":
            cur.execute("""
                UPDATE fixtures
                SET home_goals=%s, away_goals=%s, played=TRUE, active=FALSE, updated_at=CURRENT_TIMESTAMP
                WHERE id=%s
            """, (hg, ag, str(match_id)))
        elif source_table == "championship_matches":
            cur.execute("""
                UPDATE championship_matches
                SET home_goals=%s, away_goals=%s, status='played'
                WHERE id=%s
            """, (hg, ag, str(match_id)))
        elif source_table == "national_cup_matches":
            cur.execute("""
                UPDATE national_cup_matches
                SET home_goals=%s, away_goals=%s, status='played'
                WHERE id=%s
            """, (hg, ag, str(match_id)))
        elif source_table == "european_cup_matches":
            cur.execute("""
                UPDATE european_cup_matches
                SET home_goals=%s, away_goals=%s, status='played'
                WHERE id=%s
            """, (hg, ag, str(match_id)))

        cur.execute("""
            INSERT INTO match_results (
                source_table, source_match_id, competition_name, competition_type, round,
                home_team, away_team, home_score, away_score, winner, status, created_at, updated_at
            )
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'played',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)
        """, (
            source_table, str(match_id), match["competition_name"], match["competition_type"], match["round"],
            match["home_club"], match["away_club"], hg, ag, winner
        ))

        group = competition_group_from_type(match["competition_type"] or match["competition_name"])
        is_group_stage = "girone" in normalize_text(match["round"]) or group == "Campionati"

        if is_group_stage:
            upsert_standing_row(cur, match["competition_name"], match["competition_type"], match["home_club"], hg, ag)
            upsert_standing_row(cur, match["competition_name"], match["competition_type"], match["away_club"], ag, hg)
        else:
            cur.execute("""
                INSERT INTO cup_matches (
                    source_table, source_match_id, competition_name, round,
                    home_team, away_team, home_score, away_score, winner, status, created_at, updated_at
                )
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,'played',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)
            """, (
                source_table, str(match_id), match["competition_name"], match["round"],
                match["home_club"], match["away_club"], hg, ag, winner
            ))

        for name, goals in home_scorers:
            cur.execute("""
                INSERT INTO goalscorers (source_table, source_match_id, user_id, club_name, player_name, goals)
                VALUES (%s,%s,%s,%s,%s,%s)
            """, (source_table, str(match_id), match["home_user_id"], match["home_club"], name, safe_int(goals, 1)))

        for name, goals in away_scorers:
            cur.execute("""
                INSERT INTO goalscorers (source_table, source_match_id, user_id, club_name, player_name, goals)
                VALUES (%s,%s,%s,%s,%s,%s)
            """, (source_table, str(match_id), match["away_user_id"], match["away_club"], name, safe_int(goals, 1)))

        conn.commit()
        return match
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()



def get_players_for_club_or_user(club_name=None, user_id=None):
    conn = db_connect_safe()
    cur = conn.cursor()

    try:
        if user_id:
            try:
                cur.execute("""
                    SELECT *
                    FROM players
                    ORDER BY overall DESC NULLS LAST, name ASC
                """)
                all_rows = cur.fetchall()

                rows = []
                for rr in all_rows:
                    owner = str(row_get(rr, "owner_discord_id", "") or row_get(rr, "discord_id", "") or "")
                    if owner == str(user_id):
                        rows.append(rr)

                rows = rows[:25]

                if rows:
                    return rows
            except Exception as e:
                print(f"[PLAYERS BY USER] {e}")

        if club_name:
            try:
                aliases = list(get_team_aliases(club_name)) if "get_team_aliases" in globals() else [normalize_text(club_name)]
                cur.execute("""
                    SELECT id, name, position, overall, team
                    FROM players
                    ORDER BY overall DESC NULLS LAST, name ASC
                """)
                rows = cur.fetchall()

                filtered = []
                club_norm = normalize_text(club_name)

                for row in rows:
                    team_norm = normalize_text(row_get(row, "team", ""))
                    if team_norm in aliases or club_norm in team_norm or team_norm in club_norm:
                        filtered.append(row)
                    if len(filtered) >= 25:
                        break

                if filtered:
                    return filtered
            except Exception as e:
                print(f"[PLAYERS BY CLUB] {e}")

    finally:
        conn.close()

    return []

def scorer_label(player):
    name = str(row_get(player, "name", "Giocatore"))
    pos = str(row_get(player, "position", "") or "")
    ovr = str(row_get(player, "overall", "") or "")
    extra = " ".join(x for x in [pos, f"OVR {ovr}" if ovr else ""] if x)
    return name[:100], extra[:100]


class GoalCountSelect(discord.ui.Select):
    def __init__(self, flow_view, side, player_name):
        self.flow_view = flow_view
        self.side = side
        self.player_name = player_name

        options = [
            discord.SelectOption(label=f"{i} gol", value=str(i))
            for i in range(1, 8)
        ]

        super().__init__(
            placeholder=f"Quanti gol ha fatto {player_name[:40]}?",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction):
        goals = safe_int(self.values[0], 1)
        self.flow_view.add_scorer(self.side, self.player_name, goals)

        await interaction.response.edit_message(
            embed=self.flow_view.build_embed(),
            view=self.flow_view,
        )


class GoalCountView(discord.ui.View):
    def __init__(self, flow_view, side, player_name):
        super().__init__(timeout=180)
        self.add_item(GoalCountSelect(flow_view, side, player_name))


class PlayerScorerSelect(discord.ui.Select):
    def __init__(self, flow_view, side, players):
        self.flow_view = flow_view
        self.side = side

        options = []
        for p in players[:25]:
            name, extra = scorer_label(p)
            options.append(
                discord.SelectOption(
                    label=name,
                    value=name,
                    description=extra or "Giocatore",
                )
            )

        if not options:
            options = [
                discord.SelectOption(
                    label="Nessun giocatore trovato",
                    value="none",
                    description="Controlla la rosa della squadra",
                )
            ]

        side_label = "casa" if side == "home" else "trasferta"
        super().__init__(
            placeholder=f"Scegli marcatore squadra {side_label}...",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction):
        if self.values[0] == "none":
            await interaction.response.send_message(
                "❌ Nessun giocatore disponibile per questa squadra.",
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            f"⚽ Quanti gol ha fatto **{self.values[0]}**?",
            view=GoalCountView(self.flow_view, self.side, self.values[0]),
            ephemeral=True,
        )


class PlayerScorerView(discord.ui.View):
    def __init__(self, flow_view, side, players):
        super().__init__(timeout=180)
        self.add_item(PlayerScorerSelect(flow_view, side, players))


class GuidedScorerFlowView(discord.ui.View):
    def __init__(self, match):
        super().__init__(timeout=600)
        self.match = match
        self.home_scorers = []
        self.away_scorers = []

    def add_scorer(self, side, player_name, goals):
        target = self.home_scorers if side == "home" else self.away_scorers

        for idx, (name, old_goals) in enumerate(target):
            if normalize_text(name) == normalize_text(player_name):
                target[idx] = (name, safe_int(old_goals) + safe_int(goals))
                return

        target.append((player_name, safe_int(goals, 1)))

    def total_home(self):
        return sum(safe_int(goals) for _, goals in self.home_scorers)

    def total_away(self):
        return sum(safe_int(goals) for _, goals in self.away_scorers)

    def scorer_lines(self, rows):
        if not rows:
            return "Nessun marcatore inserito."
        return "\n".join(f"• **{name}** × {goals}" for name, goals in rows)

    def build_embed(self):
        home = self.match["home_club"]
        away = self.match["away_club"]
        home_goals = self.total_home()
        away_goals = self.total_away()

        embed = discord.Embed(
            title="⚽ Inserimento risultato guidato",
            description=(
                f"**{home} {home_goals} - {away_goals} {away}**\n\n"
                "Aggiungi i marcatori con i pulsanti sotto. "
                "Il risultato viene calcolato automaticamente."
            ),
            color=discord.Color.gold(),
        )
        embed.add_field(
            name=f"Marcatori {home}",
            value=self.scorer_lines(self.home_scorers),
            inline=False,
        )
        embed.add_field(
            name=f"Marcatori {away}",
            value=self.scorer_lines(self.away_scorers),
            inline=False,
        )
        embed.set_footer(
            text=f"{self.match['competition_name']} • {self.match['round']} {self.match['leg']}"
        )
        return embed

    @discord.ui.button(label="Aggiungi marcatore casa", style=discord.ButtonStyle.primary, emoji="🏠")
    async def add_home(self, interaction: discord.Interaction, button: discord.ui.Button):
        players = get_players_for_club_or_user(
            club_name=self.match["home_club"],
            user_id=self.match.get("home_user_id"),
        )
        await interaction.response.send_message(
            f"🏠 Scegli marcatore per **{self.match['home_club']}**:",
            view=PlayerScorerView(self, "home", players),
            ephemeral=True,
        )

    @discord.ui.button(label="Aggiungi marcatore trasferta", style=discord.ButtonStyle.primary, emoji="🚌")
    async def add_away(self, interaction: discord.Interaction, button: discord.ui.Button):
        players = get_players_for_club_or_user(
            club_name=self.match["away_club"],
            user_id=self.match.get("away_user_id"),
        )
        await interaction.response.send_message(
            f"🚌 Scegli marcatore per **{self.match['away_club']}**:",
            view=PlayerScorerView(self, "away", players),
            ephemeral=True,
        )

    @discord.ui.button(label="Reset marcatori", style=discord.ButtonStyle.secondary, emoji="♻️")
    async def reset_scorers(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.home_scorers = []
        self.away_scorers = []
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    @discord.ui.button(label="Conferma risultato", style=discord.ButtonStyle.success, emoji="✅")
    async def confirm_result(self, interaction: discord.Interaction, button: discord.ui.Button):
        home_goals = self.total_home()
        away_goals = self.total_away()

        if home_goals == 0 and away_goals == 0:
            await interaction.response.send_message(
                "❌ Devi inserire almeno un marcatore prima di confermare.",
                ephemeral=True,
            )
            return

        await safe_defer(interaction, ephemeral=True, thinking=True)

        try:
            match = save_unified_result_and_sync(
                self.match["source_table"],
                self.match["id"],
                home_goals,
                away_goals,
                self.home_scorers,
                self.away_scorers,
            )

            await interaction.followup.send(
                (
                    "✅ Risultato confermato e sincronizzato col sito.\n"
                    f"**{match['home_club']} {home_goals} - {away_goals} {match['away_club']}**"
                ),
                ephemeral=True,
            )

            for item in self.children:
                item.disabled = True

            try:
                await interaction.message.edit(embed=self.build_embed(), view=self)
            except Exception:
                pass

        except Exception as e:
            print(f"[CONFIRM RESULT ERROR] {type(e).__name__}: {e}")
            await interaction.followup.send(
                f"❌ Errore conferma risultato: `{type(e).__name__}`",
                ephemeral=True,
            )


class GuidedMatchSelect(discord.ui.Select):
    def __init__(self, matches):
        self.match_map = {}
        options = []

        for idx, match in enumerate(matches[:25], start=1):
            value = str(idx)
            self.match_map[value] = match
            label = f"{match['home_club']} vs {match['away_club']}"[:100]
            desc = f"{match['competition_name']} • {match['round']} {match['leg']}".strip()[:100]
            options.append(
                discord.SelectOption(
                    label=label,
                    value=value,
                    description=desc or "Partita attiva",
                )
            )

        if not options:
            options = [discord.SelectOption(label="Nessuna partita disponibile", value="none")]

        super().__init__(
            placeholder="Scegli la partita...",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction):
        if self.values[0] == "none":
            await interaction.response.send_message("❌ Nessuna partita disponibile.", ephemeral=True)
            return

        match = self.match_map[self.values[0]]
        flow = GuidedScorerFlowView(match)

        await interaction.response.send_message(
            embed=flow.build_embed(),
            view=flow,
            ephemeral=True,
        )


class GuidedMatchSelectView(discord.ui.View):
    def __init__(self, matches):
        super().__init__(timeout=300)
        self.add_item(GuidedMatchSelect(matches))


class GuidedCompetitionSelect(discord.ui.Select):
    def __init__(self, options):
        super().__init__(
            placeholder="Scegli la competizione...",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        try:
            matches = get_matches_for_competition(interaction.user.id, self.values[0])

            if not matches:
                await interaction.followup.send(
                    "❌ Non ci sono partite attive per questa competizione.",
                    ephemeral=True,
                )
                return

            embed = discord.Embed(
                title="📅 Scegli la partita",
                description=(
                    "Seleziona la partita. Dopo potrai aggiungere i marcatori "
                    "casa/trasferta e il risultato verrà calcolato automaticamente."
                ),
                color=discord.Color.green(),
            )

            await interaction.followup.send(
                embed=embed,
                view=GuidedMatchSelectView(matches),
                ephemeral=True,
            )

        except Exception as e:
            print(f"[GUIDED COMPETITION CALLBACK ERROR] {type(e).__name__}: {e}")
            await interaction.followup.send(
                f"❌ Errore: `{type(e).__name__}`",
                ephemeral=True,
            )


class GuidedCompetitionView(discord.ui.View):
    def __init__(self, *args):
        super().__init__(timeout=300)
        options = args[-1]
        self.add_item(GuidedCompetitionSelect(options))

@tree.command(name="risultato", description="Inserisci un risultato guidato: competizione, partita, gol e marcatori")
async def risultato(interaction: discord.Interaction):
    await safe_defer(interaction, ephemeral=True, thinking=True)

    if not is_results_channel(interaction):
        await interaction.followup.send(
            "❌ I risultati si inseriscono solo nel canale RISULTATI.",
            ephemeral=True
        )
        return

    try:
        options = get_guided_competition_options(interaction.user.id)

        if not options:
            await interaction.followup.send(
                "❌ Non hai partite attive da inserire. Lo staff deve prima generare le competizioni e usare `/avvia_andata` o `/avvia_ritorno`.",
                ephemeral=True
            )
            return

        embed = discord.Embed(
            title="⚽ Inserisci risultato",
            description=(
                "Scegli la competizione a cui stai partecipando.\n\n"
                "Poi selezioni la partita attiva e aggiungi i marcatori dai menu.\n"
                "Il risultato viene calcolato automaticamente dai gol inseriti."
            ),
            color=discord.Color.blue(),
        )
        embed.set_footer(text=f"Fase attiva: {get_active_leg_safe().upper()}")

        await interaction.followup.send(
            embed=embed,
            view=GuidedCompetitionView(options),
            ephemeral=True
        )

    except Exception as e:
        print(f"[SLASH ERROR] Comando-risultato Errore={type(e).__name__}: {e}")
        await interaction.followup.send(
            f"❌ Errore comando: `{type(e).__name__}`",
            ephemeral=True
        )


@tree.command(name="risultato_campionato", description="Staff: inserisce un risultato campionato e aggiorna sito/classifica")
@app_commands.describe(
    competizione="Nome campionato/girone, es: Serie A",
    casa="Squadra di casa",
    trasferta="Squadra in trasferta",
    gol_casa="Gol casa",
    gol_trasferta="Gol trasferta",
    giornata="Giornata o turno"
)
async def risultato_campionato(
    interaction: discord.Interaction,
    competizione: str,
    casa: str,
    trasferta: str,
    gol_casa: int,
    gol_trasferta: int,
    giornata: str = "Risultato"
):
    await safe_defer(interaction, ephemeral=True, thinking=True)

    if not is_league_admin(interaction):
        await interaction.followup.send("❌ Solo lo staff può usare questo comando.", ephemeral=True)
        return

    try:
        sync_site_manual_league_result(
            competizione,
            casa,
            trasferta,
            gol_casa,
            gol_trasferta,
            giornata
        )

        embed = discord.Embed(
            title="✅ Risultato campionato registrato",
            description=(
                f"🏆 **{competizione}**\n"
                f"📅 {giornata}\n\n"
                f"**{casa} {gol_casa} - {gol_trasferta} {trasferta}**\n\n"
                f"🌐 Classifica sito aggiornata."
            ),
            color=discord.Color.green()
        )

        await interaction.followup.send(embed=embed, ephemeral=True)

        try:
            channel = interaction.guild.get_channel(int(RESULTS_CHANNEL_ID)) if interaction.guild else None
            if channel:
                await channel.send(embed=embed)
        except Exception:
            pass

    except Exception as e:
        print(f"[RISULTATO CAMPIONATO ERROR] {e}")
        await interaction.followup.send(f"❌ Errore aggiornamento risultato: `{e}`", ephemeral=True)


@tree.command(name="risultato_coppa", description="Staff: inserisce un risultato coppa e aggiorna sito/tabellone")
@app_commands.describe(
    competizione="Nome coppa, es: Coppa Italia",
    turno="Turno, es: Ottavi, Quarti, Semifinale, Finale",
    casa="Squadra di casa",
    trasferta="Squadra in trasferta",
    gol_casa="Gol casa",
    gol_trasferta="Gol trasferta"
)
async def risultato_coppa(
    interaction: discord.Interaction,
    competizione: str,
    turno: str,
    casa: str,
    trasferta: str,
    gol_casa: int,
    gol_trasferta: int
):
    await safe_defer(interaction, ephemeral=True, thinking=True)

    if not is_league_admin(interaction):
        await interaction.followup.send("❌ Solo lo staff può usare questo comando.", ephemeral=True)
        return

    try:
        sync_site_cup_result(
            competizione,
            turno,
            casa,
            trasferta,
            gol_casa,
            gol_trasferta
        )

        winner = site_winner_name(casa, trasferta, gol_casa, gol_trasferta) or "Pareggio"

        embed = discord.Embed(
            title="🏆 Risultato coppa registrato",
            description=(
                f"🏆 **{competizione}**\n"
                f"📌 {turno}\n\n"
                f"**{casa} {gol_casa} - {gol_trasferta} {trasferta}**\n"
                f"✅ Qualificata/Vincitrice: **{winner}**\n\n"
                f"🌐 Tabellone sito aggiornato."
            ),
            color=discord.Color.orange()
        )

        await interaction.followup.send(embed=embed, ephemeral=True)

        try:
            channel = interaction.guild.get_channel(int(RESULTS_CHANNEL_ID)) if interaction.guild else None
            if channel:
                await channel.send(embed=embed)
        except Exception:
            pass

    except Exception as e:
        print(f"[RISULTATO COPPA ERROR] {e}")
        await interaction.followup.send(f"❌ Errore aggiornamento coppa: `{e}`", ephemeral=True)



@tree.command(name="classifica", description="Mostra la classifica del campionato")
async def classifica(interaction: discord.Interaction):
    if not is_standings_channel(interaction):
        await interaction.response.send_message("❌ La classifica si vede solo nel canale CLASSIFICHE.", delete_after=10)
        return

    champ = active_championship()
    if not champ:
        await interaction.response.send_message("Nessun campionato attivo.", ephemeral=True)
        return

    conn = connect()
    cur = conn.cursor()
    cur.execute("SELECT * FROM championship_groups WHERE championship_id = %s ORDER BY id ASC", (champ["id"],))
    groups = cur.fetchall()
    conn.close()

    embed = discord.Embed(
        title=f"📊 Classifica — {champ['name']}",
        color=discord.Color.green()
    )

    for g in groups:
        standings = calculate_group_standings(champ["id"], g["id"])
        if not standings:
            value = "Nessun dato."
        else:
            lines = []
            for i, row in enumerate(standings, start=1):
                lines.append(
                    f"**{i}. {row['name']}** — {row['pts']} pt | {row['pg']} PG | {row['w']}V {row['d']}N {row['l']}P | DR {row['gd']}"
                )
            value = "\n".join(lines[:10])
        embed.add_field(name=g["name"], value=value, inline=False)

    await interaction.response.send_message(embed=embed)

if not TOKEN:
    raise RuntimeError("DISCORD_TOKEN non configurato nelle variabili ambiente.")

print("[BOOT] Avvio finale bot.run(TOKEN)")
bot.run(TOKEN)
