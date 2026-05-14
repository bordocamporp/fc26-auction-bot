# FC26 Auction Bot PRO

Versione con:
- embed grafici
- card giocatore generate automaticamente
- database FC26 esteso da CSV
- comando `/card`
- comando `/database`
- aste con card grafica

## Installazione

Sostituisci nella vecchia cartella questi file:
- `bot.py`
- `db.py`
- `import_players.py`
- aggiungi `card_generator.py`
- sostituisci `requirements.txt`

Poi installa le nuove dipendenze:

```bash
py -3.12 -m pip install -r requirements.txt
```

Importa/aggiorna database:

```bash
py -3.12 import_players.py
```

Avvia:

```bash
py -3.12 bot.py
```

Deve uscire:

```text
Comandi sincronizzati nel server ...: 8
```

## Nuovi comandi

- `/card player_id:2`
- `/database`
- `/cerca nome:mbappe`
- `/asta player_id:2 base:50`
- `/offri prezzo:60`
- `/rosa`
- `/registrami`
- `/budget`

## Database giocatori

Il file deve essere:

```text
data/players.csv
```

Colonne obbligatorie:

```csv
id,name,team,position,overall,pace,shooting,passing,dribbling,defending,physical
```

Colonne opzionali:

```csv
nation,league,age,weak_foot,skill_moves,image_url
```
