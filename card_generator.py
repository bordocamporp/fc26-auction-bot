from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

CARD_DIR = Path("cards")
CARD_DIR.mkdir(exist_ok=True)

def _font(size=28, bold=False):
    candidates = [
        "arialbd.ttf" if bold else "arial.ttf",
        "C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf",
        "C:/Windows/Fonts/segoeuib.ttf" if bold else "C:/Windows/Fonts/segoeui.ttf",
    ]
    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            pass
    return ImageFont.load_default()

def _tier(overall):
    overall = int(overall or 0)
    if overall >= 90:
        return "ELITE"
    if overall >= 85:
        return "GOLD"
    if overall >= 80:
        return "RARE"
    return "COMMON"

def _bg_color(overall):
    overall = int(overall or 0)
    if overall >= 90:
        return (238, 201, 120)
    if overall >= 85:
        return (215, 178, 83)
    if overall >= 80:
        return (189, 157, 87)
    return (150, 150, 150)

def create_player_card(player):
    player_id = str(player["id"])
    out = CARD_DIR / f"player_{player_id}.png"

    width, height = 720, 1024
    bg = _bg_color(player["overall"])
    img = Image.new("RGB", (width, height), bg)
    draw = ImageDraw.Draw(img)

    # dark panels
    draw.rounded_rectangle((45, 45, width - 45, height - 45), radius=45, fill=(24, 24, 28), outline=(255, 230, 160), width=5)
    draw.rounded_rectangle((80, 80, width - 80, 360), radius=32, fill=(38, 38, 46))
    draw.ellipse((235, 130, 485, 380), fill=(70, 70, 82), outline=(255, 230, 160), width=4)

    # head placeholder
    initials = "".join([part[0] for part in str(player["name"]).split()[:2]]).upper()
    draw.text((360, 245), initials, font=_font(76, True), anchor="mm", fill=(255, 255, 255))

    # rating and position
    draw.text((115, 105), str(player["overall"]), font=_font(82, True), fill=(255, 235, 160))
    draw.text((125, 195), str(player["position"]), font=_font(42, True), fill=(255, 255, 255))
    draw.text((560, 125), _tier(player["overall"]), font=_font(34, True), anchor="mm", fill=(255, 235, 160))

    # name
    name = str(player["name"]).upper()
    if len(name) > 22:
        name = name[:21] + "…"
    draw.text((360, 435), name, font=_font(44, True), anchor="mm", fill=(255, 255, 255))

    team = str(player["team"] or "N/D")
    nation = str(player["nation"] or "N/D")
    league = str(player["league"] or "N/D")
    draw.text((360, 495), f"{team} • {nation}", font=_font(26), anchor="mm", fill=(220, 220, 220))
    draw.text((360, 532), league, font=_font(24), anchor="mm", fill=(185, 185, 190))

    # stats grid
    stats = [
        ("PAC", player["pace"]),
        ("SHO", player["shooting"]),
        ("PAS", player["passing"]),
        ("DRI", player["dribbling"]),
        ("DEF", player["defending"]),
        ("PHY", player["physical"]),
    ]

    x1, y1 = 105, 610
    cell_w, cell_h = 255, 105
    for i, (label, value) in enumerate(stats):
        col = i % 2
        row = i // 2
        x = x1 + col * cell_w
        y = y1 + row * cell_h
        draw.rounded_rectangle((x, y, x + 215, y + 76), radius=18, fill=(44, 44, 54))
        draw.text((x + 30, y + 38), str(value), font=_font(38, True), anchor="lm", fill=(255, 235, 160))
        draw.text((x + 115, y + 38), label, font=_font(30, True), anchor="lm", fill=(255, 255, 255))

    # footer
    wf = player["weak_foot"] if "weak_foot" in player.keys() else None
    sm = player["skill_moves"] if "skill_moves" in player.keys() else None
    age = player["age"] if "age" in player.keys() else None
    footer = []
    if age:
        footer.append(f"AGE {age}")
    if wf:
        footer.append(f"WF {wf}★")
    if sm:
        footer.append(f"SM {sm}★")
    draw.text((360, 940), "   |   ".join(footer) if footer else "FC26 AUCTION CARD", font=_font(26, True), anchor="mm", fill=(255, 235, 160))

    img.save(out, quality=95)
    return out
