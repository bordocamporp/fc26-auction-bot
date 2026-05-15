import os
import asyncio
import random
import unicodedata
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
import discord
from discord.ext import commands
from discord import app_commands
from dotenv import load_dotenv
from db import connect, init_db, reset_auction_state
from card_generator import create_player_card

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID = os.getenv("GUILD_ID")
AUCTION_CHANNEL_ID = os.getenv("AUCTION_CHANNEL_ID")
AUCTION_LOG_CHANNEL_ID = os.getenv("AUCTION_LOG_CHANNEL_ID", "1504830394908803142")
ADMIN_ROLE_ID = os.getenv("ADMIN_ROLE_ID", "1398358193197027408")
SEARCH_CHANNEL_ID = os.getenv("SEARCH_CHANNEL_ID", "1504833349414551703")
SPAM_CHANNEL_ID = "1504846794142781480"
ROSE_CHANNEL_ID = "1504847438727610519"
SCAMBI_CHANNEL_ID = "1504847601361616996"
PRE_ISCRITTO_ROLE_ID = "1398323859056365599"

RESULTS_CHANNEL_ID = "1504874612805337229"
STANDINGS_CHANNEL_ID = "1504874671064223784"
STATS_CHANNEL_ID = "1504874788349542431"
CALENDAR_CHANNEL_ID = "1504884471286075532"
LEAGUE_PLAYER_ROLE_ID = "1398332847655358554"
LEAGUE_ADMIN_ROLE_ID = "1398358193197027408"

DEFAULT_BUDGET = 500
MIN_RAISE = 10
AUCTION_SECONDS = 45
ANTI_SNIPE_THRESHOLD = 10
ANTI_SNIPE_EXTENSION = 10
MARKET_TAX = 5

MAX_GK = 2
MAX_DEF = 6
MAX_MID = 6
MAX_ATT = 4

intents = discord.Intents.default()
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
    if ADMIN_ROLE_ID:
        return any(str(role.id) == str(ADMIN_ROLE_ID) for role in getattr(interaction.user, "roles", []))

    return bool(interaction.user.guild_permissions.administrator)


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

    cur.execute("""
    CREATE TABLE IF NOT EXISTS bid_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        auction_id INTEGER NOT NULL,
        player_id TEXT NOT NULL,
        bidder_id TEXT NOT NULL,
        bidder_name TEXT,
        amount INTEGER NOT NULL,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS transfer_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        player_id TEXT NOT NULL,
        player_name TEXT,
        manager_id TEXT NOT NULL,
        manager_name TEXT,
        price INTEGER DEFAULT 0,
        source TEXT DEFAULT 'auction',
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS blacklist_players (
        player_id TEXT PRIMARY KEY,
        reason TEXT,
        created_by TEXT,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS trade_offers (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        proposer_id TEXT NOT NULL,
        proposer_name TEXT,
        target_id TEXT NOT NULL,
        target_name TEXT,
        offer_player_id TEXT,
        request_player_id TEXT,
        credits_to_target INTEGER DEFAULT 0,
        credits_to_proposer INTEGER DEFAULT 0,
        status TEXT DEFAULT 'pending',
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
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
        avg_overall REAL,
        assigned_budget INTEGER,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )
    """)

    cur.execute("""
        INSERT OR IGNORE INTO league_settings (key, value)
        VALUES ('mode', 'fantacalcio')
    """)


    cur.execute("""
    CREATE TABLE IF NOT EXISTS championships (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        status TEXT DEFAULT 'active',
        group_count INTEGER DEFAULT 1,
        teams_per_group INTEGER DEFAULT 0,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS championship_groups (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        championship_id INTEGER NOT NULL,
        name TEXT NOT NULL
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS championship_players (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        championship_id INTEGER NOT NULL,
        group_id INTEGER NOT NULL,
        discord_id TEXT NOT NULL,
        display_name TEXT NOT NULL
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS championship_matches (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
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
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS match_scorers (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        match_id INTEGER NOT NULL,
        scorer_player_id TEXT,
        scorer_name TEXT NOT NULL,
        team_owner_id TEXT NOT NULL,
        goals INTEGER DEFAULT 1
    )
    """)
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


def auction_embed(player, auction, remaining=None):
    highest_bid = auction["highest_bid"] or 0
    bidder_id = auction["highest_bidder_id"]
    leader = f"<@{bidder_id}>" if bidder_id else "Nessuno"

    auction_id = auction["id"]
    recent = auction_last_bids.get(int(auction_id), [])
    recent_text = "\n".join(recent[-5:]) if recent else "Nessuna offerta ancora."

    embed = discord.Embed(
        title="🔨 ASTA LIVE",
        description=f"**{player['name']}** è ora all'asta.",
        color=discord.Color.gold()
    )

    embed.add_field(name="Ruolo", value=player["position"], inline=True)
    embed.add_field(name="Squadra", value=player["team"], inline=True)
    embed.add_field(name="Overall", value=str(player["overall"]), inline=True)
    embed.add_field(name="Prezzo attuale", value=f"**{highest_bid}** crediti", inline=True)
    embed.add_field(name="Leader", value=leader, inline=True)
    embed.add_field(name="Tempo", value=f"⏱️ {remaining}s" if remaining is not None else f"{AUCTION_SECONDS}s", inline=True)
    embed.add_field(name="Ultime offerte", value=recent_text, inline=False)
    embed.add_field(
        name="Offerte",
        value="Usa i bottoni sotto: **+10**, **+50**, **All In** oppure **Offerta custom**.",
        inline=False
    )
    embed.set_footer(text=f"ID asta: {auction_id} • ID giocatore: {player['id']} • Anti-snipe attivo")
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


def get_roster_role_count(discord_id, group):
    conn = connect()
    cur = conn.cursor()
    cur.execute("""
        SELECT position
        FROM players
        WHERE owner_discord_id = ?
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
        VALUES (?, ?, ?, ?, ?)
    """, (auction_id, player_id, bidder_id, bidder_name, amount))
    conn.commit()
    conn.close()


def record_transfer(player_id, player_name, manager_id, manager_name, price, source="auction"):
    conn = connect()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO transfer_history (player_id, player_name, manager_id, manager_name, price, source)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (str(player_id), player_name, str(manager_id), manager_name, int(price or 0), source))
    conn.commit()
    conn.close()


def is_blacklisted(player_id):
    conn = connect()
    cur = conn.cursor()
    cur.execute("SELECT player_id FROM blacklist_players WHERE player_id = ?", (str(player_id),))
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
        INSERT OR REPLACE INTO league_settings (key, value)
        VALUES ('mode', ?)
    """, (mode,))
    conn.commit()
    conn.close()


def budget_from_team_overall(avg_ovr):
    avg_ovr = float(avg_ovr or 0)

    if avg_ovr >= 85:
        return 150
    if avg_ovr >= 82:
        return 220
    if avg_ovr >= 80:
        return 280
    if avg_ovr >= 78:
        return 350
    if avg_ovr >= 75:
        return 430

    return 500


def normalize_team_name(team):
    return normalize_text(team).strip()


def get_team_stats(team_name):
    search = normalize_team_name(team_name)

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

    matched = [r for r in rows if normalize_team_name(r["team"]) == search]

    if not matched:
        return [], 0, 0

    avg_ovr = sum(safe_int(r["overall"]) for r in matched) / len(matched)
    budget = budget_from_team_overall(avg_ovr)

    return matched, avg_ovr, budget


async def safe_dm(user_id, message=None, embed=None):
    try:
        user = await bot.fetch_user(int(user_id))
        await user.send(content=message, embed=embed)
        return True
    except Exception:
        return False


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
        WHERE owner_discord_id = ?
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


async def place_bid(interaction: discord.Interaction, increment=None, all_in=False):
    conn = connect()
    cur = conn.cursor()

    cur.execute("SELECT * FROM managers WHERE discord_id = ?", (str(interaction.user.id),))
    manager = cur.fetchone()

    if not manager:
        conn.close()
        await interaction.response.send_message("Prima usa `/registrami`.", ephemeral=True)
        return

    cur.execute("""
        SELECT a.*, p.name AS player_name, p.id AS player_id, p.position AS player_position
        FROM auctions a
        JOIN players p ON p.id = a.player_id
        WHERE a.status = 'open'
        LIMIT 1
    """)
    auction = cur.fetchone()

    if not auction:
        conn.close()
        await interaction.response.send_message("Non c'è nessuna asta aperta.", ephemeral=True)
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
    new_bid = safe_int(manager["budget"]) if all_in else current_bid + safe_int(increment)

    if new_bid <= current_bid:
        conn.close()
        await interaction.response.send_message("L'offerta deve superare quella attuale.", ephemeral=True)
        return

    if new_bid < current_bid + MIN_RAISE:
        conn.close()
        await interaction.response.send_message(f"Devi rilanciare almeno di {MIN_RAISE} crediti.", ephemeral=True)
        return

    if safe_int(manager["budget"]) < new_bid:
        conn.close()
        await interaction.response.send_message("Budget insufficiente.", ephemeral=True)
        return

    cur.execute("""
        UPDATE auctions
        SET highest_bid = ?, highest_bidder_id = ?
        WHERE id = ?
    """, (new_bid, str(interaction.user.id), auction["id"]))
    conn.commit()

    cur.execute("""
        SELECT a.*, p.*
        FROM auctions a
        JOIN players p ON p.id = a.player_id
        WHERE a.id = ?
    """, (auction["id"],))
    updated = cur.fetchone()
    conn.close()

    auction_id = int(auction["id"])
    player_id = str(auction["player_id"])
    bidder_name = interaction.user.display_name

    record_bid(auction_id, player_id, str(interaction.user.id), bidder_name, new_bid)

    if previous_bidder_id and str(previous_bidder_id) != str(interaction.user.id):
        await safe_dm(
            previous_bidder_id,
            f"🔔 Sei stato superato nell'asta di **{auction['player_name']}**. Nuova offerta: **{new_bid}** crediti."
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
    except Exception:
        pass

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


@bot.event
async def on_ready():
    init_db()
    ensure_extra_tables()
    reset_auction_state()

    guild = get_guild()

    if guild:
        tree.copy_global_to(guild=guild)
        synced = await tree.sync(guild=guild)
        print(f"Comandi sincronizzati nel server {GUILD_ID}: {len(synced)}")
    else:
        synced = await tree.sync()
        print(f"Comandi globali sincronizzati: {len(synced)}")

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

        await interaction.response.defer(ephemeral=True)

        guild = interaction.guild
        member = guild.get_member(int(self.member_id)) if guild else None

        if not member:
            await interaction.followup.send("Utente non trovato nel server.", ephemeral=True)
            return

        players, avg_ovr, budget = get_team_stats(str(self.squadra.value))

        if not players:
            await interaction.followup.send("Squadra non trovata o senza giocatori liberi disponibili.", ephemeral=True)
            return

        conn = connect()
        cur = conn.cursor()

        cur.execute(
            "INSERT OR IGNORE INTO managers (discord_id, name, budget) VALUES (?, ?, ?)",
            (str(member.id), member.display_name, budget)
        )

        # Se aveva già giocatori, li svincola prima.
        cur.execute(
            "UPDATE players SET owner_discord_id = NULL, sold_price = NULL WHERE owner_discord_id = ?",
            (str(member.id),)
        )

        for p in players:
            cur.execute(
                "UPDATE players SET owner_discord_id = ?, sold_price = ? WHERE id = ?",
                (str(member.id), 0, p["id"])
            )

        cur.execute(
            "UPDATE managers SET budget = ?, name = ? WHERE discord_id = ?",
            (budget, member.display_name, str(member.id))
        )

        cur.execute("""
            INSERT OR REPLACE INTO real_team_assignments
            (discord_id, manager_name, team_name, avg_overall, assigned_budget)
            VALUES (?, ?, ?, ?, ?)
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
        member = interaction.guild.get_member(member_id) if interaction.guild else None

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
            "INSERT OR IGNORE INTO managers (discord_id, name, budget) VALUES (?, ?, ?)",
            (str(member.id), member.display_name, DEFAULT_BUDGET)
        )
        cur.execute(
            "UPDATE managers SET name = ?, budget = ? WHERE discord_id = ?",
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



@tree.command(name="budget", description="Mostra il tuo budget residuo")
async def budget(interaction: discord.Interaction):
    if not is_spam_channel(interaction):
        await interaction.response.send_message("❌ Usa questo comando solo nel canale SPAM-CHAT.", ephemeral=True)
        return

    conn = connect()
    cur = conn.cursor()
    cur.execute("SELECT budget FROM managers WHERE discord_id = ?", (str(interaction.user.id),))
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
    cur.execute("UPDATE managers SET budget = ?", (importo,))
    conn.commit()
    conn.close()

    await interaction.response.send_message(f"✅ Budget resettato a **{importo}** crediti per tutti.")


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


@tree.command(name="cerca", description="Cerca un giocatore FC26")
@app_commands.describe(nome="Nome o parte del nome")
async def cerca(interaction: discord.Interaction, nome: str):
    if not is_search_channel(interaction):
        await interaction.response.send_message(
            "❌ Puoi cercare i giocatori solo nel canale dedicato alla ricerca.",
            ephemeral=True
        )
        return

    await interaction.response.defer(ephemeral=True)

    search = normalize_text(nome)

    conn = connect()
    cur = conn.cursor()
    cur.execute("SELECT * FROM players ORDER BY overall DESC")
    rows = cur.fetchall()
    conn.close()

    results = []
    for r in rows:
        haystack = " ".join([
            normalize_text(r["name"]),
            normalize_text(r["team"]),
            normalize_text(r["position"]),
            normalize_text(r["nation"]),
            normalize_text(r["league"])
        ])
        if search in haystack:
            results.append(r)
            if len(results) >= 10:
                break

    if not results:
        await interaction.followup.send("Nessun giocatore trovato.", ephemeral=True)
        return

    embed = discord.Embed(
        title=f"🔎 Risultati ricerca: {nome}",
        description="Usa l'ID del giocatore per avviare l'asta o vedere la card.",
        color=discord.Color.blue()
    )

    for r in results:
        stato = "🟢 libero" if not r["owner_discord_id"] else f"🔴 assegnato {r['sold_price']} cr"
        embed.add_field(
            name=f"{r['name']} • ID {r['id']}",
            value=f"{r['position']} • {r['team']} • OVR **{r['overall']}** • {stato}",
            inline=False
        )

    await interaction.followup.send(embed=embed, ephemeral=True)


@tree.command(name="card", description="Mostra la card grafica di un giocatore")
@app_commands.describe(player_id="ID giocatore")
async def card(interaction: discord.Interaction, player_id: str):
    if not is_spam_channel(interaction):
        await interaction.response.send_message("❌ Usa questo comando solo nel canale SPAM-CHAT.", ephemeral=True)
        return

    await interaction.response.defer()

    conn = connect()
    cur = conn.cursor()
    cur.execute("SELECT * FROM players WHERE id = ?", (player_id,))
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


@tree.command(name="asta", description="Avvia un'asta per un giocatore")
@app_commands.describe(player_id="ID giocatore")
async def asta(interaction: discord.Interaction, player_id: str):
    if AUCTION_CHANNEL_ID and str(interaction.channel_id) != str(AUCTION_CHANNEL_ID):
        await interaction.response.send_message("❌ Puoi avviare le aste solo nel canale aste.", ephemeral=True)
        return

    await interaction.response.defer()

    conn = connect()
    cur = conn.cursor()

    cur.execute("SELECT * FROM players WHERE id = ?", (player_id,))
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

    cur.execute("SELECT * FROM auctions WHERE status = 'open'")
    open_auction = cur.fetchone()
    if open_auction:
        conn.close()
        await interaction.followup.send("C'è già un'asta aperta. Chiudila prima con `/reset_asta`.", ephemeral=True)
        return

    cur.execute("SELECT * FROM managers WHERE discord_id = ?", (str(interaction.user.id),))
    starter_manager = cur.fetchone()

    if not starter_manager:
        conn.close()
        await interaction.followup.send("Prima usa `/registrami` per partecipare alle aste.", ephemeral=True)
        return

    base = base_price_from_overall(player["overall"])

    if safe_int(starter_manager["budget"]) < base:
        conn.close()
        await interaction.followup.send(f"Budget insufficiente per aprire l'asta. Servono almeno {base} crediti.", ephemeral=True)
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
        INSERT INTO auctions (player_id, status, highest_bid, highest_bidder_id, channel_id)
        VALUES (?, 'open', ?, ?, ?)
    """, (player_id, base, str(interaction.user.id), str(interaction.channel_id)))
    auction_id = cur.lastrowid
    conn.commit()

    record_bid(auction_id, str(player_id), str(interaction.user.id), interaction.user.display_name, base)
    auction_last_bids[int(auction_id)] = [f"• **{interaction.user.display_name}** apre → **{base}** cr"]

    cur.execute("""
        SELECT a.*, p.*
        FROM auctions a
        JOIN players p ON p.id = a.player_id
        WHERE a.id = ?
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
    except Exception:
        auction_thread = None

    conn = connect()
    cur = conn.cursor()
    cur.execute("UPDATE auctions SET message_id = ? WHERE id = ?", (str(message.id), auction_id))
    conn.commit()
    conn.close()

    await run_auction_countdown(auction_thread or interaction.channel, auction_id, message)


async def run_auction_countdown(channel, auction_id: int, message):
    auction_timers[int(auction_id)] = AUCTION_SECONDS

    while auction_timers.get(int(auction_id), 0) > 0:
        remaining = auction_timers[int(auction_id)]

        conn = connect()
        cur = conn.cursor()
        cur.execute("""
            SELECT a.*, p.*
            FROM auctions a
            JOIN players p ON p.id = a.player_id
            WHERE a.id = ? AND a.status = 'open'
        """, (auction_id,))
        row = cur.fetchone()
        conn.close()

        if not row:
            auction_timers.pop(int(auction_id), None)
            return

        if remaining % 5 == 0 or remaining <= 10:
            try:
                await message.edit(embed=auction_embed(row, row, remaining), view=AuctionView())
            except Exception:
                pass

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
        JOIN players p ON p.id = a.player_id
        WHERE a.id = ? AND a.status = 'open'
    """, (auction_id,))
    auction = cur.fetchone()

    if not auction:
        conn.close()
        return

    if auction["highest_bidder_id"]:
        cur.execute("SELECT budget FROM managers WHERE discord_id = ?", (auction["highest_bidder_id"],))
        manager = cur.fetchone()

        ok, group, current, limit = can_add_player_to_roster(auction["highest_bidder_id"], auction["player_position"])

        if manager and manager["budget"] >= auction["highest_bid"] and ok:
            tax_amount = int((auction["highest_bid"] * MARKET_TAX) / 100)
            final_price = int(auction["highest_bid"]) + tax_amount

            cur.execute(
                "UPDATE managers SET budget = budget - ? WHERE discord_id = ?",
                (final_price, auction["highest_bidder_id"])
            )
            cur.execute("UPDATE players SET owner_discord_id = ?, sold_price = ? WHERE id = ?", (auction["highest_bidder_id"], final_price, auction["player_id"]))
            cur.execute("UPDATE auctions SET status = 'closed' WHERE id = ?", (auction_id,))
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
                f"🏆 Hai vinto l'asta di **{auction['player_name']}** per **{auction['highest_bid']}** crediti!"
            )

            embed = discord.Embed(
                title="✅ ASTA CHIUSA",
                description=f"**{auction['player_name']}** assegnato a **{winner.display_name}**.",
                color=discord.Color.green()
            )
            embed.add_field(name="Prezzo finale", value=f"{auction['highest_bid']} crediti", inline=True)
            embed.add_field(name="Tassa mercato", value=f"{tax_amount} crediti ({MARKET_TAX}%)", inline=True)
            embed.add_field(name="Costo totale", value=f"{final_price} crediti", inline=True)
            embed.set_image(url=WALKOUT_GIF)

            if message:
                try:
                    await message.edit(view=None)
                except Exception:
                    pass

            await channel.send(embed=embed)

            log_channel = await get_log_channel()
            if log_channel:
                log_embed = discord.Embed(
                    title="📜 Log asta",
                    description=f"**{auction['player_name']}** → **{winner.display_name}**",
                    color=discord.Color.green()
                )
                log_embed.add_field(name="Prezzo", value=f"{auction['highest_bid']} crediti", inline=True)
                log_embed.add_field(name="ID giocatore", value=str(auction["player_id"]), inline=True)
                await log_channel.send(embed=log_embed)

            auction_last_bids.pop(int(auction_id), None)
            return

    cur.execute("UPDATE auctions SET status = 'closed' WHERE id = ?", (auction_id,))
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





def build_roster_embed(discord_id, display_name):
    conn = connect()
    cur = conn.cursor()

    cur.execute("SELECT budget FROM managers WHERE discord_id = ?", (str(discord_id),))
    manager = cur.fetchone()
    budget = manager["budget"] if manager else 0

    cur.execute("""
        SELECT name, team, position, overall, sold_price
        FROM players
        WHERE owner_discord_id = ?
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
        cur.execute("SELECT name FROM managers WHERE discord_id = ?", (selected_id,))
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
        SELECT m.discord_id, m.name, m.budget, COUNT(p.id) AS player_count
        FROM managers m
        LEFT JOIN players p ON p.owner_discord_id = m.discord_id
        GROUP BY m.discord_id, m.name, m.budget
        ORDER BY m.name ASC
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




@tree.command(name="mercato", description="Mostra giocatori liberi filtrabili")
@app_commands.describe(ruolo="Ruolo, es. ST, CM, CB", overall_min="Overall minimo", overall_max="Overall massimo")
async def mercato(interaction: discord.Interaction, ruolo: str = None, overall_min: int = 0, overall_max: int = 99):
    await interaction.response.defer(ephemeral=True)

    conn = connect()
    cur = conn.cursor()

    if ruolo:
        cur.execute("""
            SELECT *
            FROM players
            WHERE owner_discord_id IS NULL
              AND LOWER(position) = LOWER(?)
              AND overall BETWEEN ? AND ?
            ORDER BY overall DESC
            LIMIT 15
        """, (ruolo, overall_min, overall_max))
    else:
        cur.execute("""
            SELECT *
            FROM players
            WHERE owner_discord_id IS NULL
              AND overall BETWEEN ? AND ?
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
        WHERE owner_discord_id = ?
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
    cur.execute("SELECT name, owner_discord_id, sold_price FROM players WHERE id = ?", (player_id,))
    player = cur.fetchone()

    if not player:
        conn.close()
        await interaction.response.send_message("Giocatore non trovato.", ephemeral=True)
        return

    if player["owner_discord_id"] and player["sold_price"]:
        cur.execute(
            "UPDATE managers SET budget = budget + ? WHERE discord_id = ?",
            (player["sold_price"], player["owner_discord_id"])
        )

    cur.execute("UPDATE players SET owner_discord_id = NULL, sold_price = NULL WHERE id = ?", (player_id,))
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

    cur.execute("SELECT * FROM players WHERE id = ?", (player_id,))
    player = cur.fetchone()

    if not player:
        conn.close()
        await interaction.response.send_message("Giocatore non trovato.", ephemeral=True)
        return

    cur.execute("SELECT * FROM managers WHERE discord_id = ?", (str(utente.id),))
    manager = cur.fetchone()

    if not manager:
        conn.close()
        await interaction.response.send_message("L'utente deve prima usare `/registrami`.", ephemeral=True)
        return

    if safe_int(manager["budget"]) < prezzo:
        conn.close()
        await interaction.response.send_message("Budget insufficiente per questo utente.", ephemeral=True)
        return

    cur.execute("UPDATE managers SET budget = budget - ? WHERE discord_id = ?", (prezzo, str(utente.id)))
    cur.execute("UPDATE players SET owner_discord_id = ?, sold_price = ? WHERE id = ?", (str(utente.id), prezzo, player_id))
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

    cur.execute("SELECT * FROM managers WHERE discord_id = ?", (str(utente.id),))
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
        LIMIT ?
    """, (numero,))
    players = cur.fetchall()

    if not players:
        conn.close()
        await interaction.response.send_message("Non ci sono abbastanza giocatori liberi per il pack.", ephemeral=True)
        return

    for p in players:
        cur.execute(
            "UPDATE players SET owner_discord_id = ?, sold_price = ? WHERE id = ?",
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
        cur.execute("SELECT name, budget FROM managers WHERE discord_id = ?", (manager_id,))
        manager = cur.fetchone()

        cur.execute("""
            SELECT player_name, price, source, created_at
            FROM transfer_history
            WHERE manager_id = ?
            ORDER BY id DESC
            LIMIT 15
        """, (manager_id,))
        rows = cur.fetchall()

        cur.execute("""
            SELECT COUNT(*) AS total, AVG(overall) AS avg_ovr, SUM(sold_price) AS spent
            FROM players
            WHERE owner_discord_id = ?
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


class TradeView(discord.ui.View):
    def __init__(self, trade_id):
        super().__init__(timeout=86400)
        self.trade_id = trade_id

    @discord.ui.button(label="Accetta", style=discord.ButtonStyle.success)
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button):
        conn = connect()
        cur = conn.cursor()

        cur.execute("SELECT * FROM trade_offers WHERE id = ? AND status = 'pending'", (self.trade_id,))
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

        # Validate players ownership.
        if offer_player_id:
            cur.execute("SELECT name, owner_discord_id FROM players WHERE id = ?", (offer_player_id,))
            p = cur.fetchone()
            if not p or str(p["owner_discord_id"]) != proposer_id:
                conn.close()
                await interaction.response.send_message("Scambio non valido: il proponente non possiede più il giocatore offerto.", ephemeral=True)
                return

        if request_player_id:
            cur.execute("SELECT name, owner_discord_id FROM players WHERE id = ?", (request_player_id,))
            p = cur.fetchone()
            if not p or str(p["owner_discord_id"]) != target_id:
                conn.close()
                await interaction.response.send_message("Scambio non valido: non possiedi più il giocatore richiesto.", ephemeral=True)
                return

        # Validate budgets.
        cur.execute("SELECT budget FROM managers WHERE discord_id = ?", (proposer_id,))
        proposer = cur.fetchone()
        cur.execute("SELECT budget FROM managers WHERE discord_id = ?", (target_id,))
        target = cur.fetchone()

        if not proposer or not target:
            conn.close()
            await interaction.response.send_message("Uno dei due utenti non è registrato.", ephemeral=True)
            return

        if safe_int(proposer["budget"]) < credits_to_target:
            conn.close()
            await interaction.response.send_message("Scambio non valido: il proponente non ha abbastanza crediti.", ephemeral=True)
            return

        if safe_int(target["budget"]) < credits_to_proposer:
            conn.close()
            await interaction.response.send_message("Scambio non valido: non hai abbastanza crediti.", ephemeral=True)
            return

        # Money movements.
        tax_target = int((credits_to_target * MARKET_TAX) / 100)
        tax_proposer = int((credits_to_proposer * MARKET_TAX) / 100)

        if credits_to_target:
            cur.execute("UPDATE managers SET budget = budget - ? WHERE discord_id = ?", (credits_to_target + tax_target, proposer_id))
            cur.execute("UPDATE managers SET budget = budget + ? WHERE discord_id = ?", (credits_to_target, target_id))

        if credits_to_proposer:
            cur.execute("UPDATE managers SET budget = budget - ? WHERE discord_id = ?", (credits_to_proposer + tax_proposer, target_id))
            cur.execute("UPDATE managers SET budget = budget + ? WHERE discord_id = ?", (credits_to_proposer, proposer_id))

        # Player movements.
        if offer_player_id:
            cur.execute("UPDATE players SET owner_discord_id = ? WHERE id = ?", (target_id, offer_player_id))

        if request_player_id:
            cur.execute("UPDATE players SET owner_discord_id = ? WHERE id = ?", (proposer_id, request_player_id))

        cur.execute("UPDATE trade_offers SET status = 'accepted' WHERE id = ?", (self.trade_id,))
        conn.commit()
        conn.close()

        await interaction.response.edit_message(content="✅ Scambio accettato e completato.", embed=None, view=None)

    @discord.ui.button(label="Rifiuta", style=discord.ButtonStyle.danger)
    async def reject(self, interaction: discord.Interaction, button: discord.ui.Button):
        conn = connect()
        cur = conn.cursor()

        cur.execute("SELECT * FROM trade_offers WHERE id = ? AND status = 'pending'", (self.trade_id,))
        trade = cur.fetchone()

        if not trade:
            conn.close()
            await interaction.response.send_message("Scambio non trovato o già concluso.", ephemeral=True)
            return

        if str(interaction.user.id) != str(trade["target_id"]):
            conn.close()
            await interaction.response.send_message("Solo il destinatario dello scambio può rifiutare.", ephemeral=True)
            return

        cur.execute("UPDATE trade_offers SET status = 'rejected' WHERE id = ?", (self.trade_id,))
        conn.commit()
        conn.close()

        await interaction.response.edit_message(content="❌ Scambio rifiutato.", embed=None, view=None)


@tree.command(name="scambio", description="Proponi uno scambio con giocatori e/o crediti")
@app_commands.describe(
    utente="Utente a cui proporre lo scambio",
    offro_player_id="ID giocatore che offri, opzionale",
    chiedo_player_id="ID giocatore che vuoi ricevere, opzionale",
    crediti_offerti="Crediti che offri all'altro utente",
    crediti_richiesti="Crediti che chiedi all'altro utente"
)
async def scambio(
    interaction: discord.Interaction,
    utente: discord.Member,
    offro_player_id: str = None,
    chiedo_player_id: str = None,
    crediti_offerti: int = 0,
    crediti_richiesti: int = 0
):
    if not is_scambi_channel(interaction):
        await interaction.response.send_message("❌ Usa questo comando solo nel canale SCAMBI.", ephemeral=True)
        return

    if utente.id == interaction.user.id:
        await interaction.response.send_message("Non puoi proporre uno scambio a te stesso.", ephemeral=True)
        return

    if not offro_player_id and not chiedo_player_id and crediti_offerti <= 0 and crediti_richiesti <= 0:
        await interaction.response.send_message("Devi inserire almeno un giocatore o dei crediti.", ephemeral=True)
        return

    conn = connect()
    cur = conn.cursor()

    cur.execute("SELECT * FROM managers WHERE discord_id = ?", (str(interaction.user.id),))
    proposer = cur.fetchone()
    cur.execute("SELECT * FROM managers WHERE discord_id = ?", (str(utente.id),))
    target = cur.fetchone()

    if not proposer or not target:
        conn.close()
        await interaction.response.send_message("Entrambi gli utenti devono essere registrati con `/registrami`.", ephemeral=True)
        return

    if crediti_offerti > safe_int(proposer["budget"]):
        conn.close()
        await interaction.response.send_message("Non hai abbastanza crediti da offrire.", ephemeral=True)
        return

    if crediti_richiesti > safe_int(target["budget"]):
        conn.close()
        await interaction.response.send_message("L'altro utente non ha abbastanza crediti per questa proposta.", ephemeral=True)
        return

    offer_name = "Nessuno"
    request_name = "Nessuno"

    if offro_player_id:
        cur.execute("SELECT name, owner_discord_id FROM players WHERE id = ?", (offro_player_id,))
        p = cur.fetchone()
        if not p or str(p["owner_discord_id"]) != str(interaction.user.id):
            conn.close()
            await interaction.response.send_message("Non possiedi il giocatore che vuoi offrire.", ephemeral=True)
            return
        offer_name = p["name"]

    if chiedo_player_id:
        cur.execute("SELECT name, owner_discord_id FROM players WHERE id = ?", (chiedo_player_id,))
        p = cur.fetchone()
        if not p or str(p["owner_discord_id"]) != str(utente.id):
            conn.close()
            await interaction.response.send_message("L'altro utente non possiede il giocatore richiesto.", ephemeral=True)
            return
        request_name = p["name"]

    cur.execute("""
        INSERT INTO trade_offers
        (proposer_id, proposer_name, target_id, target_name, offer_player_id, request_player_id, credits_to_target, credits_to_proposer)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        str(interaction.user.id),
        interaction.user.display_name,
        str(utente.id),
        utente.display_name,
        offro_player_id,
        chiedo_player_id,
        crediti_offerti,
        crediti_richiesti
    ))
    trade_id = cur.lastrowid
    conn.commit()
    conn.close()

    embed = discord.Embed(
        title="🔁 Proposta di scambio",
        description=f"**{interaction.user.display_name}** propone uno scambio a **{utente.display_name}**.",
        color=discord.Color.blue()
    )
    embed.add_field(name="Offre", value=f"Giocatore: **{offer_name}**\nCrediti: **{crediti_offerti}**", inline=True)
    embed.add_field(name="Chiede", value=f"Giocatore: **{request_name}**\nCrediti: **{crediti_richiesti}**", inline=True)
    embed.set_footer(text=f"ID scambio: {trade_id}")

    await interaction.response.send_message(content=f"{utente.mention}", embed=embed, view=TradeView(trade_id))


@tree.command(name="blacklist_add", description="Admin: aggiungi un giocatore alla blacklist")
@app_commands.describe(player_id="ID giocatore", motivo="Motivo blacklist")
async def blacklist_add(interaction: discord.Interaction, player_id: str, motivo: str = "Non specificato"):
    if not is_admin(interaction):
        await interaction.response.send_message("❌ Solo gli admin possono usare questo comando.", ephemeral=True)
        return

    conn = connect()
    cur = conn.cursor()
    cur.execute("SELECT name FROM players WHERE id = ?", (player_id,))
    player = cur.fetchone()

    if not player:
        conn.close()
        await interaction.response.send_message("Giocatore non trovato.", ephemeral=True)
        return

    cur.execute("""
        INSERT OR REPLACE INTO blacklist_players (player_id, reason, created_by)
        VALUES (?, ?, ?)
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
    cur.execute("DELETE FROM blacklist_players WHERE player_id = ?", (player_id,))
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
        LEFT JOIN players p ON p.id = b.player_id
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
                "• OVR medio 85+ → 150 crediti\n"
                "• OVR medio 82-84 → 220 crediti\n"
                "• OVR medio 80-81 → 280 crediti\n"
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


@tree.command(name="lista_squadre", description="Mostra le squadre reali disponibili")
@app_commands.describe(nome="Filtro nome squadra, opzionale")
async def lista_squadre(interaction: discord.Interaction, nome: str = None):
    await interaction.response.defer(ephemeral=True)

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
        "INSERT OR IGNORE INTO managers (discord_id, name, budget) VALUES (?, ?, ?)",
        (str(utente.id), utente.display_name, budget)
    )

    # Se l'utente aveva già una rosa, la svincoliamo prima di assegnare la squadra.
    cur.execute("UPDATE players SET owner_discord_id = NULL, sold_price = NULL WHERE owner_discord_id = ?", (str(utente.id),))

    for p in players:
        cur.execute(
            "UPDATE players SET owner_discord_id = ?, sold_price = ? WHERE id = ?",
            (str(utente.id), 0, p["id"])
        )

    cur.execute("UPDATE managers SET budget = ?, name = ? WHERE discord_id = ?", (budget, utente.display_name, str(utente.id)))

    cur.execute("""
        INSERT OR REPLACE INTO real_team_assignments
        (discord_id, manager_name, team_name, avg_overall, assigned_budget)
        VALUES (?, ?, ?, ?, ?)
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


@tree.command(name="reset_modalita", description="Admin: resetta modalità, rose e squadre assegnate")
async def reset_modalita(interaction: discord.Interaction):
    if not is_admin(interaction):
        await interaction.response.send_message("❌ Solo gli admin possono usare questo comando.", ephemeral=True)
        return

    conn = connect()
    cur = conn.cursor()
    cur.execute("UPDATE players SET owner_discord_id = NULL, sold_price = NULL")
    cur.execute("UPDATE managers SET budget = ?", (DEFAULT_BUDGET,))
    cur.execute("DELETE FROM real_team_assignments")
    cur.execute("UPDATE auctions SET status = 'closed' WHERE status = 'open'")
    conn.commit()
    conn.close()

    await interaction.response.send_message("✅ Reset completato: rose svuotate, budget ripristinato, squadre assegnate cancellate.")



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
        WHERE championship_id = ? AND group_id = ?
    """, (championship_id, group_id))
    players = cur.fetchall()

    table = {}
    for p in players:
        table[p["discord_id"]] = {
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
        WHERE championship_id = ? AND group_id = ? AND status = 'confirmed'
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
            VALUES (?, 'active', ?, ?)
        """, (str(self.nome.value), group_count, teams_per_group))
        championship_id = cur.lastrowid

        group_ids = []
        for gname in group_names:
            cur.execute("INSERT INTO championship_groups (championship_id, name) VALUES (?, ?)", (championship_id, gname))
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
                    VALUES (?, ?, ?, ?)
                """, (championship_id, group_id, str(member.id), member.display_name))

        # Generate fixtures
        for group_id, players in groups.items():
            rounds = generate_round_robin(players)
            for round_idx, pairs in enumerate(rounds, start=1):
                for home, away in pairs:
                    cur.execute("""
                        INSERT INTO championship_matches
                        (championship_id, group_id, round_number, home_id, away_id, home_name, away_name)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                    """, (
                        championship_id, group_id, round_idx,
                        home[0], away[0], home[1], away[1]
                    ))

        conn.commit()
        conn.close()

        embed = discord.Embed(
            title="🏆 Campionato creato",
            description=f"**{self.nome.value}** creato con calendario andata/ritorno.",
            color=discord.Color.gold()
        )
        embed.add_field(name="Gironi", value=str(group_count), inline=True)
        embed.add_field(name="Squadre per girone", value=str(teams_per_group), inline=True)
        embed.add_field(name="Iscritti usati", value=str(len(selected)), inline=True)
        embed.add_field(name="Nomi gironi", value=", ".join(group_names), inline=False)

        await interaction.response.send_message(embed=embed)


@tree.command(name="crea_campionato", description="Admin: crea gironi e calendario automatico")
async def crea_campionato(interaction: discord.Interaction):
    if not is_league_admin(interaction):
        await interaction.response.send_message("❌ Solo gli admin possono creare il campionato.", ephemeral=True)
        return

    await interaction.response.send_modal(CreaCampionatoModal())


@tree.command(name="reset_campionato", description="Admin: archivia il campionato attivo")
async def reset_campionato(interaction: discord.Interaction):
    if not is_league_admin(interaction):
        await interaction.response.send_message("❌ Solo gli admin possono resettare il campionato.", ephemeral=True)
        return

    conn = connect()
    cur = conn.cursor()
    cur.execute("UPDATE championships SET status = 'archived' WHERE status = 'active'")
    conn.commit()
    conn.close()

    await interaction.response.send_message("✅ Campionato attivo archiviato.")


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
        cur.execute("SELECT * FROM championship_matches WHERE id = ? AND status = 'pending'", (self.match_id,))
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
            SET home_goals = ?, away_goals = ?, status = 'awaiting_confirmation', submitted_by = ?, confirm_by = ?
            WHERE id = ?
        """, (home_goals, away_goals, user_id, str(confirm_by), self.match_id))

        cur.execute("DELETE FROM match_scorers WHERE match_id = ?", (self.match_id,))

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
                    VALUES (?, ?, ?, ?)
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
    cur.execute("SELECT * FROM championship_matches WHERE id = ?", (match_id,))
    m = cur.fetchone()
    cur.execute("SELECT scorer_name, team_owner_id, goals FROM match_scorers WHERE match_id = ?", (match_id,))
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
        cur.execute("UPDATE championship_matches SET status = 'confirmed' WHERE id = ?", (self.match_id,))
        conn.commit()
        conn.close()

        embed = build_result_embed(self.match_id)
        await interaction.response.edit_message(content="✅ Risultato confermato.", embed=embed, view=None)

    @discord.ui.button(label="Contesta", style=discord.ButtonStyle.danger)
    async def contest(self, interaction: discord.Interaction, button: discord.ui.Button):
        if str(interaction.user.id) != self.confirm_by:
            await interaction.response.send_message("Solo l'avversario può contestare questo risultato.", ephemeral=True)
            return

        conn = connect()
        cur = conn.cursor()
        cur.execute("UPDATE championship_matches SET status = 'contested' WHERE id = ?", (self.match_id,))
        conn.commit()
        conn.close()

        embed = build_result_embed(self.match_id)
        await interaction.response.edit_message(content="⚠️ Risultato contestato. Staff richiesto.", embed=embed, view=None)


@tree.command(name="risultato", description="Inserisci un risultato del tuo girone")
async def risultato(interaction: discord.Interaction):
    if not is_results_channel(interaction):
        await interaction.response.send_message("❌ I risultati si inseriscono solo nel canale RISULTATI.", ephemeral=True)
        return

    champ = active_championship()
    if not champ:
        await interaction.response.send_message("Nessun campionato attivo.", ephemeral=True)
        return

    conn = connect()
    cur = conn.cursor()
    cur.execute("""
        SELECT *, ? AS requester_id
        FROM championship_matches
        WHERE championship_id = ?
          AND status = 'pending'
          AND (home_id = ? OR away_id = ?)
        ORDER BY round_number ASC
        LIMIT 25
    """, (str(interaction.user.id), champ["id"], str(interaction.user.id), str(interaction.user.id)))
    matches = cur.fetchall()
    conn.close()

    if not matches:
        await interaction.response.send_message("Non hai partite da inserire.", ephemeral=True)
        return

    embed = discord.Embed(
        title="⚽ Inserisci risultato",
        description="Scegli dalla tendina la partita da aggiornare.",
        color=discord.Color.blue()
    )

    await interaction.response.send_message(embed=embed, view=ResultOpponentView(matches), ephemeral=True)


@tree.command(name="classifica", description="Mostra la classifica del campionato")
async def classifica(interaction: discord.Interaction):
    if not is_standings_channel(interaction):
        await interaction.response.send_message("❌ La classifica si vede solo nel canale CLASSIFICHE.", ephemeral=True)
        return

    champ = active_championship()
    if not champ:
        await interaction.response.send_message("Nessun campionato attivo.", ephemeral=True)
        return

    conn = connect()
    cur = conn.cursor()
    cur.execute("SELECT * FROM championship_groups WHERE championship_id = ? ORDER BY id ASC", (champ["id"],))
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


@tree.command(name="calendario", description="Mostra le partite ancora da disputare")
async def calendario(interaction: discord.Interaction):
    if not is_calendar_channel(interaction):
        await interaction.response.send_message("❌ Il calendario si vede solo nel canale CALENDARIO.", ephemeral=True)
        return

    champ = active_championship()
    if not champ:
        await interaction.response.send_message("Nessun campionato attivo.", ephemeral=True)
        return

    conn = connect()
    cur = conn.cursor()

    # Calcola quante giornate totali ci sono.
    cur.execute("""
        SELECT MAX(round_number) AS max_round
        FROM championship_matches
        WHERE championship_id = ?
    """, (champ["id"],))
    max_round_row = cur.fetchone()
    max_round = int(max_round_row["max_round"] or 0)
    first_leg_last_round = max_round // 2 if max_round else 0

    # Prima mostriamo solo l'andata. Il ritorno compare solo quando tutta l'andata è completata.
    cur.execute("""
        SELECT COUNT(*) AS pending_first_leg
        FROM championship_matches
        WHERE championship_id = ?
          AND status = 'pending'
          AND round_number <= ?
    """, (champ["id"], first_leg_last_round))
    pending_first_leg = int(cur.fetchone()["pending_first_leg"] or 0)

    if pending_first_leg > 0:
        leg_label = "Andata"
        round_filter_min = 1
        round_filter_max = first_leg_last_round
    else:
        leg_label = "Ritorno"
        round_filter_min = first_leg_last_round + 1
        round_filter_max = max_round

    # Mostra solo partite ancora da disputare, ordinate per giornata.
    cur.execute("""
        SELECT m.*, g.name AS group_name
        FROM championship_matches m
        JOIN championship_groups g ON g.id = m.group_id
        WHERE m.championship_id = ?
          AND m.status = 'pending'
          AND m.round_number BETWEEN ? AND ?
        ORDER BY m.round_number ASC, g.id ASC, m.id ASC
        LIMIT 30
    """, (champ["id"], round_filter_min, round_filter_max))
    rows = cur.fetchall()
    conn.close()

    if not rows:
        await interaction.response.send_message("✅ Non ci sono partite da disputare in calendario.", ephemeral=True)
        return

    embed = discord.Embed(
        title=f"📅 Calendario — {champ['name']}",
        description=f"Fase visualizzata: **{leg_label}**\\nMostro solo le prossime partite ancora da disputare.",
        color=discord.Color.blue()
    )

    for m in rows:
        embed.add_field(
            name=f"{m['group_name']} • Giornata {m['round_number']}",
            value=f"**{m['home_name']}** vs **{m['away_name']}**",
            inline=False
        )

    embed.set_footer(text="Il ritorno comparirà solo quando tutte le partite di andata saranno completate.")
    await interaction.response.send_message(embed=embed)



@tree.command(name="prossima_partita", description="Mostra la tua prossima partita")
async def prossima_partita(interaction: discord.Interaction):
    if not is_calendar_channel(interaction):
        await interaction.response.send_message("❌ Questo comando si usa solo nel canale CALENDARIO.", ephemeral=True)
        return

    champ = active_championship()
    if not champ:
        await interaction.response.send_message("Nessun campionato attivo.", ephemeral=True)
        return

    conn = connect()
    cur = conn.cursor()
    cur.execute("""
        SELECT m.*, g.name AS group_name
        FROM championship_matches m
        JOIN championship_groups g ON g.id = m.group_id
        WHERE m.championship_id = ?
          AND m.status = 'pending'
          AND (m.home_id = ? OR m.away_id = ?)
        ORDER BY m.round_number ASC
        LIMIT 1
    """, (champ["id"], str(interaction.user.id), str(interaction.user.id)))
    m = cur.fetchone()
    conn.close()

    if not m:
        await interaction.response.send_message("Non hai prossime partite.", ephemeral=True)
        return

    embed = discord.Embed(title="⏭️ Prossima partita", color=discord.Color.blue())
    embed.add_field(name="Girone", value=m["group_name"], inline=True)
    embed.add_field(name="Giornata", value=str(m["round_number"]), inline=True)
    embed.add_field(name="Match", value=f"**{m['home_name']}** vs **{m['away_name']}**", inline=False)

    await interaction.response.send_message(embed=embed)


@tree.command(name="capocannonieri", description="Classifica marcatori")
async def capocannonieri(interaction: discord.Interaction):
    if not is_stats_channel(interaction):
        await interaction.response.send_message("❌ Le statistiche si vedono solo nel canale STATISTICHE.", ephemeral=True)
        return

    champ = active_championship()
    if not champ:
        await interaction.response.send_message("Nessun campionato attivo.", ephemeral=True)
        return

    conn = connect()
    cur = conn.cursor()
    cur.execute("""
        SELECT s.scorer_name, SUM(s.goals) AS goals
        FROM match_scorers s
        JOIN championship_matches m ON m.id = s.match_id
        WHERE m.championship_id = ? AND m.status = 'confirmed'
        GROUP BY s.scorer_name
        ORDER BY goals DESC
        LIMIT 20
    """, (champ["id"],))
    rows = cur.fetchall()
    conn.close()

    embed = discord.Embed(title="⚽ Capocannonieri", color=discord.Color.gold())

    if not rows:
        embed.description = "Nessun marcatore registrato."
    else:
        for i, r in enumerate(rows, start=1):
            embed.add_field(name=f"{i}. {r['scorer_name']}", value=f"{r['goals']} gol", inline=False)

    await interaction.response.send_message(embed=embed)


@tree.command(name="miglior_difesa", description="Mostra le migliori difese")
async def miglior_difesa(interaction: discord.Interaction):
    if not is_stats_channel(interaction):
        await interaction.response.send_message("❌ Le statistiche si vedono solo nel canale STATISTICHE.", ephemeral=True)
        return

    champ = active_championship()
    if not champ:
        await interaction.response.send_message("Nessun campionato attivo.", ephemeral=True)
        return

    conn = connect()
    cur = conn.cursor()
    cur.execute("SELECT * FROM championship_groups WHERE championship_id = ?", (champ["id"],))
    groups = cur.fetchall()
    conn.close()

    all_rows = []
    for g in groups:
        all_rows.extend(calculate_group_standings(champ["id"], g["id"]))

    all_rows.sort(key=lambda x: (x["ga"], -x["pts"]))

    embed = discord.Embed(title="🧤 Miglior difesa", color=discord.Color.blue())
    for i, r in enumerate(all_rows[:15], start=1):
        embed.add_field(name=f"{i}. {r['name']}", value=f"Gol subiti: {r['ga']} | PG: {r['pg']}", inline=False)

    await interaction.response.send_message(embed=embed)


@tree.command(name="forza_risultato", description="Admin: forza o corregge un risultato")
@app_commands.describe(match_id="ID partita", home_goals="Gol casa", away_goals="Gol trasferta")
async def forza_risultato(interaction: discord.Interaction, match_id: int, home_goals: int, away_goals: int):
    if not is_league_admin(interaction):
        await interaction.response.send_message("❌ Solo gli admin possono forzare risultati.", ephemeral=True)
        return

    conn = connect()
    cur = conn.cursor()
    cur.execute("""
        UPDATE championship_matches
        SET home_goals = ?, away_goals = ?, status = 'confirmed', submitted_by = ?, confirm_by = NULL
        WHERE id = ?
    """, (home_goals, away_goals, str(interaction.user.id), match_id))
    conn.commit()
    conn.close()

    await interaction.response.send_message(f"✅ Risultato forzato per partita ID {match_id}: {home_goals}-{away_goals}")


@tree.command(name="revisioni", description="Admin: mostra risultati contestati")
async def revisioni(interaction: discord.Interaction):
    if not is_league_admin(interaction):
        await interaction.response.send_message("❌ Solo gli admin possono vedere le revisioni.", ephemeral=True)
        return

    conn = connect()
    cur = conn.cursor()
    cur.execute("""
        SELECT *
        FROM championship_matches
        WHERE status = 'contested'
        ORDER BY round_number ASC
        LIMIT 20
    """)
    rows = cur.fetchall()
    conn.close()

    embed = discord.Embed(title="⚠️ Risultati contestati", color=discord.Color.orange())

    if not rows:
        embed.description = "Nessun risultato contestato."
    else:
        for m in rows:
            embed.add_field(
                name=f"ID {m['id']} • G{m['round_number']}",
                value=f"{m['home_name']} {m['home_goals']} - {m['away_goals']} {m['away_name']}",
                inline=False
            )

    await interaction.response.send_message(embed=embed, ephemeral=True)



class ReplaceNewPlayerSelect(discord.ui.Select):
    def __init__(self, old_member, candidates):
        self.old_member = old_member

        options = []
        for m in candidates[:25]:
            options.append(
                discord.SelectOption(
                    label=m.display_name[:100],
                    value=str(m.id),
                    description=f"Nuovo player • ID {m.id}"
                )
            )

        super().__init__(
            placeholder="Scegli il nuovo player PRE-ISCRITTO...",
            min_values=1,
            max_values=1,
            options=options
        )

    async def callback(self, interaction: discord.Interaction):
        if not is_league_admin(interaction):
            await interaction.response.send_message("❌ Solo gli admin possono sostituire player.", ephemeral=True)
            return

        guild = interaction.guild
        new_member = guild.get_member(int(self.values[0])) if guild else None

        if not new_member:
            await interaction.response.send_message("Nuovo player non trovato.", ephemeral=True)
            return

        old_id = str(self.old_member.id)
        new_id = str(new_member.id)

        conn = connect()
        cur = conn.cursor()

        # Managers
        cur.execute("SELECT * FROM managers WHERE discord_id = ?", (old_id,))
        old_manager = cur.fetchone()

        if old_manager:
            cur.execute("""
                INSERT OR REPLACE INTO managers (discord_id, name, budget)
                VALUES (?, ?, ?)
            """, (
                new_id,
                new_member.display_name,
                old_manager["budget"]
            ))

            cur.execute("DELETE FROM managers WHERE discord_id = ?", (old_id,))

        # Rosa
        cur.execute("""
            UPDATE players
            SET owner_discord_id = ?
            WHERE owner_discord_id = ?
        """, (new_id, old_id))

        # Championship players
        cur.execute("""
            UPDATE championship_players
            SET discord_id = ?, display_name = ?
            WHERE discord_id = ?
        """, (new_id, new_member.display_name, old_id))

        # Matches
        cur.execute("""
            UPDATE championship_matches
            SET home_id = ?, home_name = ?
            WHERE home_id = ?
        """, (new_id, new_member.display_name, old_id))

        cur.execute("""
            UPDATE championship_matches
            SET away_id = ?, away_name = ?
            WHERE away_id = ?
        """, (new_id, new_member.display_name, old_id))

        # Real team assignments
        cur.execute("""
            UPDATE real_team_assignments
            SET discord_id = ?, manager_name = ?
            WHERE discord_id = ?
        """, (new_id, new_member.display_name, old_id))

        conn.commit()
        conn.close()

        embed = discord.Embed(
            title="🔄 Player sostituito",
            description=f"**{new_member.display_name}** prende il posto di **{self.old_member.display_name}**.",
            color=discord.Color.green()
        )

        embed.add_field(name="Trasferito", value="✅ Rosa\n✅ Budget\n✅ Girone\n✅ Calendario\n✅ Statistiche", inline=False)

        await interaction.response.edit_message(embed=embed, view=None)


class ReplaceNewPlayerView(discord.ui.View):
    def __init__(self, old_member, candidates):
        super().__init__(timeout=180)
        self.add_item(ReplaceNewPlayerSelect(old_member, candidates))


class ReplaceOldPlayerSelect(discord.ui.Select):
    def __init__(self, registered_members):
        options = []

        for m in registered_members[:25]:
            options.append(
                discord.SelectOption(
                    label=m.display_name[:100],
                    value=str(m.id),
                    description=f"Player iscritto • ID {m.id}"
                )
            )

        super().__init__(
            placeholder="Scegli il player da sostituire...",
            min_values=1,
            max_values=1,
            options=options
        )

    async def callback(self, interaction: discord.Interaction):
        if not is_league_admin(interaction):
            await interaction.response.send_message("❌ Solo gli admin possono usare questo comando.", ephemeral=True)
            return

        guild = interaction.guild

        old_member = guild.get_member(int(self.values[0])) if guild else None

        if not old_member:
            await interaction.response.send_message("Player non trovato.", ephemeral=True)
            return

        pre_role = guild.get_role(int(PRE_ISCRITTO_ROLE_ID)) if guild else None

        if not pre_role:
            await interaction.response.send_message("Ruolo PRE-ISCRITTO non trovato.", ephemeral=True)
            return

        candidates = [m for m in pre_role.members if not m.bot]

        if not candidates:
            await interaction.response.send_message("Nessun player PRE-ISCRITTO disponibile.", ephemeral=True)
            return

        embed = discord.Embed(
            title="🔄 Sostituzione player",
            description=f"Hai scelto di sostituire **{old_member.display_name}**.\n\nOra scegli il nuovo player PRE-ISCRITTO.",
            color=discord.Color.orange()
        )

        await interaction.response.edit_message(
            embed=embed,
            view=ReplaceNewPlayerView(old_member, candidates)
        )


class ReplaceOldPlayerView(discord.ui.View):
    def __init__(self, registered_members):
        super().__init__(timeout=180)
        self.add_item(ReplaceOldPlayerSelect(registered_members))


@tree.command(name="sostituisci_player", description="Admin: sostituisce un player nel campionato")
async def sostituisci_player(interaction: discord.Interaction):
    if not is_league_admin(interaction):
        await interaction.response.send_message("❌ Solo gli admin possono usare questo comando.", ephemeral=True)
        return

    guild = interaction.guild

    role = guild.get_role(int(LEAGUE_PLAYER_ROLE_ID)) if guild else None

    if not role:
        await interaction.response.send_message("Ruolo ISCRITTI non trovato.", ephemeral=True)
        return

    registered_members = [m for m in role.members if not m.bot]

    if not registered_members:
        await interaction.response.send_message("Nessun player ISCRITTO trovato.", ephemeral=True)
        return

    embed = discord.Embed(
        title="🔄 Sostituzione player campionato",
        description="Seleziona dalla tendina il player ISCRITTO da sostituire.",
        color=discord.Color.blue()
    )

    await interaction.response.send_message(
        embed=embed,
        view=ReplaceOldPlayerView(registered_members),
        ephemeral=True
    )


if __name__ == "__main__":
    if not TOKEN:
        raise RuntimeError("DISCORD_TOKEN mancante nel file .env")
    bot.run(TOKEN)
