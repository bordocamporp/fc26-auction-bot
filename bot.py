import os
import asyncio
import unicodedata
import discord
from discord.ext import commands
from discord import app_commands
from dotenv import load_dotenv
from db import connect, init_db, reset_auction_state
from card_generator import create_player_card
from import_players import main as import_players_main

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID = os.getenv("GUILD_ID")
DEFAULT_BUDGET = 500
MIN_RAISE = 1
AUCTION_SECONDS = 20

intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree


def normalize_text(value):
    value = str(value or "").lower()
    value = unicodedata.normalize("NFKD", value)
    return "".join(c for c in value if not unicodedata.combining(c))


def get_guild():
    return discord.Object(id=int(GUILD_ID)) if GUILD_ID else None


def player_embed(player, title="FC26 Player Card"):
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
    for key, label in [("nation", "🌍"), ("league", "🏆")]:
        if player[key]:
            extra.append(f"{label} {player[key]}")
    if player["age"]:
        extra.append(f"🎂 {player['age']} anni")
    if player["weak_foot"]:
        extra.append(f"WF {player['weak_foot']}★")
    if player["skill_moves"]:
        extra.append(f"SM {player['skill_moves']}★")
    if extra:
        embed.add_field(name="Info", value=" • ".join(extra), inline=False)

    if player["owner_discord_id"]:
        embed.add_field(name="Stato", value=f"✅ Assegnato per **{player['sold_price']}** crediti", inline=False)
    else:
        embed.add_field(name="Stato", value="🟢 Libero", inline=False)

    embed.set_footer(text=f"ID giocatore: {player['id']} • FC26 Auction Bot")
    return embed


@bot.event
async def on_ready():
    init_db()
    try:
        import_players_main()
    except Exception as e:
        print(f"Errore import giocatori: {e}")

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

    embed = discord.Embed(
        title="📊 Database FC26",
        description="Statistiche database giocatori importati nel bot.",
        color=discord.Color.blue()
    )
    embed.add_field(name="Giocatori totali", value=str(total), inline=True)
    embed.add_field(name="Liberi", value=str(free), inline=True)
    embed.add_field(name="Assegnati", value=str(sold), inline=True)
    embed.add_field(name="Overall medio", value=f"{avg_ovr:.1f}", inline=True)
    await interaction.response.send_message(embed=embed)


@tree.command(name="cerca", description="Cerca un giocatore FC26")
@app_commands.describe(nome="Nome o parte del nome, anche senza accenti")
async def cerca(interaction: discord.Interaction, nome: str):
    search = normalize_text(nome)
    conn = connect()
    cur = conn.cursor()
    cur.execute("SELECT * FROM players ORDER BY overall DESC")
    all_rows = cur.fetchall()
    conn.close()

    results = []
    for r in all_rows:
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
        await interaction.response.send_message("Nessun giocatore trovato.", ephemeral=True)
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
    await interaction.response.send_message(embed=embed)


@tree.command(name="card", description="Mostra la card grafica di un giocatore")
@app_commands.describe(player_id="ID giocatore")
async def card(interaction: discord.Interaction, player_id: str):
    conn = connect()
    cur = conn.cursor()
    cur.execute("SELECT * FROM players WHERE id = ?", (player_id,))
    player = cur.fetchone()
    conn.close()
    if not player:
        await interaction.response.send_message("Giocatore non trovato.", ephemeral=True)
        return

    card_path = create_player_card(player)
    file = discord.File(str(card_path), filename="player_card.png")
    embed = player_embed(player)
    embed.set_image(url="attachment://player_card.png")
    await interaction.response.send_message(embed=embed, file=file)


@tree.command(name="asta", description="Avvia un'asta per un giocatore")
@app_commands.describe(player_id="ID giocatore", base="Prezzo base")
async def asta(interaction: discord.Interaction, player_id: str, base: int = 1):
    conn = connect()
    cur = conn.cursor()
    cur.execute("SELECT * FROM players WHERE id = ?", (player_id,))
    player = cur.fetchone()

    if not player:
        conn.close()
        await interaction.response.send_message("Giocatore non trovato.", ephemeral=True)
        return

    if player["owner_discord_id"]:
        conn.close()
        await interaction.response.send_message("Questo giocatore è già stato assegnato.", ephemeral=True)
        return

    cur.execute("SELECT * FROM auctions WHERE status = 'open'")
    if cur.fetchone():
        conn.close()
        await interaction.response.send_message("C'è già un'asta aperta. Chiudila prima.", ephemeral=True)
        return

    cur.execute(
        "INSERT INTO auctions (player_id, status, highest_bid, channel_id) VALUES (?, 'open', ?, ?)",
        (player_id, base, str(interaction.channel_id))
    )
    auction_id = cur.lastrowid
    conn.commit()
    conn.close()

    card_path = create_player_card(player)
    file = discord.File(str(card_path), filename="auction_card.png")
    embed = discord.Embed(
        title="🔨 ASTA APERTA",
        description=f"**{player['name']}** è ora all'asta.",
        color=discord.Color.gold()
    )
    embed.add_field(name="Ruolo", value=player["position"], inline=True)
    embed.add_field(name="Squadra", value=player["team"], inline=True)
    embed.add_field(name="Overall", value=str(player["overall"]), inline=True)
    embed.add_field(name="Base", value=f"{base} crediti", inline=True)
    embed.add_field(name="Rilancio minimo", value=f"{MIN_RAISE} credito", inline=True)
    embed.add_field(name="Durata", value=f"{AUCTION_SECONDS} secondi", inline=True)
    embed.add_field(name="Come offrire", value=f"`/offri prezzo:{base + MIN_RAISE}`", inline=False)
    embed.set_image(url="attachment://auction_card.png")
    embed.set_footer(text=f"ID asta: {auction_id} • ID giocatore: {player_id}")
    await interaction.response.send_message(embed=embed, file=file)

    await asyncio.sleep(AUCTION_SECONDS)
    await close_auction(interaction.channel, auction_id)


async def close_auction(channel, auction_id: int):
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
            cur.execute("UPDATE managers SET budget = budget - ? WHERE discord_id = ?",
                        (auction["highest_bid"], auction["highest_bidder_id"]))
            cur.execute("UPDATE players SET owner_discord_id = ?, sold_price = ? WHERE id = ?",
                        (auction["highest_bidder_id"], auction["highest_bid"], auction["player_id"]))
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
            await channel.send(embed=embed)
            return

    cur.execute("UPDATE auctions SET status = 'closed' WHERE id = ?", (auction_id,))
    conn.commit()
    conn.close()
    embed = discord.Embed(
        title="❌ ASTA CHIUSA",
        description=f"Nessuna offerta valida per **{auction['player_name']}**.",
        color=discord.Color.red()
    )
    await channel.send(embed=embed)


@tree.command(name="offri", description="Fai un'offerta nell'asta aperta")
@app_commands.describe(prezzo="Prezzo offerto")
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

    cur.execute("UPDATE auctions SET highest_bid = ?, highest_bidder_id = ? WHERE id = ?",
                (prezzo, str(interaction.user.id), auction["id"]))
    conn.commit()
    conn.close()

    embed = discord.Embed(
        title="🔥 Nuova offerta",
        description=f"**{interaction.user.display_name}** offre **{prezzo}** crediti.",
        color=discord.Color.orange()
    )
    embed.add_field(name="Giocatore", value=auction["player_name"], inline=True)
    await interaction.response.send_message(embed=embed)


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
