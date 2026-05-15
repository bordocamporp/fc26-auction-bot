import os
import asyncio
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

DEFAULT_BUDGET = 500
MIN_RAISE = 10
AUCTION_SECONDS = 30

intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree


def get_guild():
    return discord.Object(id=int(GUILD_ID)) if GUILD_ID else None


def normalize_text(value):
    value = str(value or "").lower()
    value = unicodedata.normalize("NFKD", value)
    return "".join(c for c in value if not unicodedata.combining(c))


def is_admin(interaction: discord.Interaction):
    return bool(interaction.user.guild_permissions.administrator)


def base_price_from_overall(overall):
    overall = int(overall or 0)
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


def player_embed(player, title="FC26 Player Card"):
    sold = player["sold_price"]
    owner = player["owner_discord_id"]

    embed = discord.Embed(
        title=f"{title}: {player['name']}",
        description=f"**{player['position']}** • {player['team']} • OVR **{player['overall']}**",
        color=discord.Color.gold() if int(player["overall"] or 0) >= 85 else discord.Color.dark_grey()
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
    embed.add_field(
        name="Offerte",
        value="Usa i bottoni sotto: **+10**, **+50**, **All In** oppure **Offerta custom**.",
        inline=False
    )
    embed.set_footer(text=f"ID asta: {auction['id']} • ID giocatore: {player['id']}")
    return embed


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
        SELECT a.*, p.name AS player_name, p.id AS player_id
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

    current_bid = int(auction["highest_bid"] or 0)
    new_bid = int(manager["budget"]) if all_in else current_bid + int(increment or 0)

    if new_bid <= current_bid:
        conn.close()
        await interaction.response.send_message("L'offerta deve superare quella attuale.", ephemeral=True)
        return

    if new_bid < current_bid + MIN_RAISE:
        conn.close()
        await interaction.response.send_message(f"Devi rilanciare almeno di {MIN_RAISE} crediti.", ephemeral=True)
        return

    if int(manager["budget"]) < new_bid:
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

    embed = auction_embed(updated, updated)

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

    base = base_price_from_overall(player["overall"])

    cur.execute("""
        INSERT INTO auctions (player_id, status, highest_bid, channel_id)
        VALUES (?, 'open', ?, ?)
    """, (player_id, base, str(interaction.channel_id)))
    auction_id = cur.lastrowid
    conn.commit()

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
    embed = auction_embed(player, auction_row)
    embed.set_image(url="attachment://auction_card.png")

    message = await interaction.followup.send(embed=embed, file=file, view=AuctionView(), wait=True)

    conn = connect()
    cur = conn.cursor()
    cur.execute("UPDATE auctions SET message_id = ? WHERE id = ?", (str(message.id), auction_id))
    conn.commit()
    conn.close()

    await asyncio.sleep(AUCTION_SECONDS)
    await close_auction(interaction.channel, auction_id, message)


async def close_auction(channel, auction_id: int, message=None):
    conn = connect()
    cur = conn.cursor()

    cur.execute("""
        SELECT a.*, p.name AS player_name, p.id AS player_id
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

        if manager and manager["budget"] >= auction["highest_bid"]:
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

    await interaction.response.send_message(f"🔥 **{interaction.user.display_name}** offre **{prezzo}** per **{auction['player_name']}**!", ephemeral=True)


@tree.command(name="rosa", description="Mostra la rosa tua o di un altro manager")
@app_commands.describe(utente="Manager da controllare")
async def rosa(interaction: discord.Interaction, utente: discord.Member = None):
    target = utente or interaction.user

    conn = connect()
    cur = conn.cursor()
    cur.execute("""
        SELECT name, team, position, overall, sold_price
        FROM players
        WHERE owner_discord_id = ?
        ORDER BY position, overall DESC
    """, (str(target.id),))
    rows = cur.fetchall()
    conn.close()

    if not rows:
        await interaction.response.send_message(f"{target.display_name} non ha ancora giocatori.")
        return

    total = sum(r["sold_price"] or 0 for r in rows)
    embed = discord.Embed(
        title=f"📋 Rosa di {target.display_name}",
        description=f"Totale speso: **{total}** crediti",
        color=discord.Color.green()
    )

    for r in rows[:25]:
        embed.add_field(
            name=f"{r['position']} • {r['name']}",
            value=f"{r['team']} • OVR {r['overall']} • {r['sold_price']} cr",
            inline=False
        )

    await interaction.response.send_message(embed=embed)


if __name__ == "__main__":
    if not TOKEN:
        raise RuntimeError("DISCORD_TOKEN mancante nel file .env")
    bot.run(TOKEN)
