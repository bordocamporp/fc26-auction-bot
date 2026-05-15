import os
import asyncio
import random
import unicodedata
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

DEFAULT_BUDGET = 500
MIN_RAISE = 10
AUCTION_SECONDS = 45
ANTI_SNIPE_THRESHOLD = 10
ANTI_SNIPE_EXTENSION = 10

MAX_GK = 2
MAX_DEF = 6
MAX_MID = 6
MAX_ATT = 4

intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree

auction_timers = {}
auction_last_bids = {}


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


@tree.command(name="registrami", description="Registrati al torneo FC26")
async def registrami(interaction: discord.Interaction):
    conn = connect()
    cur = conn.cursor()
    cur.execute(
        "INSERT OR IGNORE INTO managers (discord_id, name, budget) VALUES (?, ?, ?)",
        (str(interaction.user.id), interaction.user.display_name, DEFAULT_BUDGET)
    )
    conn.commit()
    conn.close()
    await interaction.response.send_message(f"✅ Registrato con budget {DEFAULT_BUDGET} crediti.", ephemeral=True)


@tree.command(name="budget", description="Mostra il tuo budget residuo")
async def budget(interaction: discord.Interaction):
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

    conn = connect()
    cur = conn.cursor()
    cur.execute("UPDATE auctions SET message_id = ? WHERE id = ?", (str(message.id), auction_id))
    conn.commit()
    conn.close()

    await run_auction_countdown(interaction.channel, auction_id, message)


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
            cur.execute("UPDATE managers SET budget = budget - ? WHERE discord_id = ?", (auction["highest_bid"], auction["highest_bidder_id"]))
            cur.execute("UPDATE players SET owner_discord_id = ?, sold_price = ? WHERE id = ?", (auction["highest_bidder_id"], auction["highest_bid"], auction["player_id"]))
            cur.execute("UPDATE auctions SET status = 'closed' WHERE id = ?", (auction_id,))
            conn.commit()
            conn.close()

            winner = await bot.fetch_user(int(auction["highest_bidder_id"]))
            embed = discord.Embed(
                title="✅ ASTA CHIUSA",
                description=f"**{auction['player_name']}** assegnato a **{winner.display_name}**.",
                color=discord.Color.green()
            )
            embed.add_field(name="Prezzo finale", value=f"{auction['highest_bid']} crediti", inline=True)

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


@tree.command(name="offri", description="Fai un'offerta manuale nell'asta aperta")
@app_commands.describe(prezzo="Prezzo totale offerto")
async def offri(interaction: discord.Interaction, prezzo: int):
    conn = connect()
    cur = conn.cursor()

    cur.execute("SELECT * FROM managers WHERE discord_id = ?", (str(interaction.user.id),))
    manager = cur.fetchone()

    if not manager:
        conn.close()
        await interaction.response.send_message("Prima usa /registrami.", ephemeral=True)
        return

    if manager["budget"] < prezzo:
        conn.close()
        await interaction.response.send_message("Budget insufficiente.", ephemeral=True)
        return

    cur.execute("""
        SELECT a.*, p.name AS player_name
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

    if prezzo < auction["highest_bid"] + MIN_RAISE:
        conn.close()
        await interaction.response.send_message(f"Devi offrire almeno {auction['highest_bid'] + MIN_RAISE}.", ephemeral=True)
        return

    cur.execute("UPDATE auctions SET highest_bid = ?, highest_bidder_id = ? WHERE id = ?", (prezzo, str(interaction.user.id), auction["id"]))
    conn.commit()
    conn.close()

    record_bid(auction["id"], auction["player_id"], str(interaction.user.id), interaction.user.display_name, prezzo)
    auction_last_bids.setdefault(int(auction["id"]), [])
    auction_last_bids[int(auction["id"])].append(f"• **{interaction.user.display_name}** manuale → **{prezzo}** cr")

    await interaction.response.send_message(f"🔥 **{interaction.user.display_name}** offre **{prezzo}** per **{auction['player_name']}**!", ephemeral=True)



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



if __name__ == "__main__":
    if not TOKEN:
        raise RuntimeError("DISCORD_TOKEN mancante nel file .env")
    bot.run(TOKEN)
