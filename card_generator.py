from pathlib import Path
from urllib.request import urlopen, Request
from io import BytesIO
from PIL import Image, ImageDraw, ImageFont

CARD_DIR = Path("cards")
CARD_DIR.mkdir(exist_ok=True)


def _get(player, key, default=""):
    try:
        value = player[key]
    except Exception:
        value = default
    if value is None:
        return default
    return value


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


def _safe_int(value, default=0):
    try:
        if value in ("", None):
            return default
        return int(float(value))
    except Exception:
        return default


def _safe_text(value, default="N/D"):
    value = str(value or "").strip()
    return value if value else default


def _tier(overall):
    overall = _safe_int(overall)
    if overall >= 90:
        return "ELITE"
    if overall >= 85:
        return "GOLD"
    if overall >= 80:
        return "RARE"
    return "COMMON"


def _bg_color(overall):
    overall = _safe_int(overall)
    if overall >= 90:
        return (238, 201, 120)
    if overall >= 85:
        return (215, 178, 83)
    if overall >= 80:
        return (189, 157, 87)
    return (150, 150, 150)


def _download_card_image(url, out_path):
    if not url:
        return None

    url = str(url).strip()
    if not url.startswith("http"):
        return None

    try:
        req = Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0"
            }
        )
        with urlopen(req, timeout=12) as response:
            raw = response.read()

        img = Image.open(BytesIO(raw)).convert("RGB")
        img.thumbnail((720, 1024), Image.LANCZOS)

        canvas = Image.new("RGB", (720, 1024), (24, 24, 28))
        x = (720 - img.width) // 2
        y = (1024 - img.height) // 2
        canvas.paste(img, (x, y))
        canvas.save(out_path, quality=95)
        return out_path
    except Exception as e:
        print(f"Impossibile scaricare card image: {e}")
        return None


def create_player_card(player):
    player_id = str(_get(player, "id", "unknown"))
    out = CARD_DIR / f"player_{player_id}.png"

    # Se il CSV ha una card/URL immagine valida, usa quella.
    image_url = _get(player, "image_url", "")
    downloaded = _download_card_image(image_url, out)
    if downloaded:
        return downloaded

    # Altrimenti genera una card grafica completa.
    width, height = 720, 1024
    overall = _safe_int(_get(player, "overall", 0))
    bg = _bg_color(overall)

    img = Image.new("RGB", (width, height), bg)
    draw = ImageDraw.Draw(img)

    draw.rounded_rectangle(
        (45, 45, width - 45, height - 45),
        radius=45,
        fill=(24, 24, 28),
        outline=(255, 230, 160),
        width=5
    )

    draw.rounded_rectangle((80, 80, width - 80, 360), radius=32, fill=(38, 38, 46))
    draw.ellipse((235, 130, 485, 380), fill=(70, 70, 82), outline=(255, 230, 160), width=4)

    name = _safe_text(_get(player, "name", "Player"))
    initials = "".join([part[0] for part in name.split()[:2]]).upper() or "FC"
    draw.text((360, 245), initials, font=_font(76, True), anchor="mm", fill=(255, 255, 255))

    position = _safe_text(_get(player, "position", "N/D"))
    team = _safe_text(_get(player, "team", "N/D"))
    nation = _safe_text(_get(player, "nation", "N/D"))
    league = _safe_text(_get(player, "league", "N/D"))

    draw.text((115, 105), str(overall), font=_font(82, True), fill=(255, 235, 160))
    draw.text((125, 195), position, font=_font(42, True), fill=(255, 255, 255))
    draw.text((560, 125), _tier(overall), font=_font(34, True), anchor="mm", fill=(255, 235, 160))

    display_name = name.upper()
    if len(display_name) > 22:
        display_name = display_name[:21] + "…"
    draw.text((360, 435), display_name, font=_font(44, True), anchor="mm", fill=(255, 255, 255))

    draw.text((360, 495), f"{team} • {nation}", font=_font(26), anchor="mm", fill=(220, 220, 220))
    draw.text((360, 532), league, font=_font(24), anchor="mm", fill=(185, 185, 190))

    stats = [
        ("PAC", _safe_int(_get(player, "pace", 0))),
        ("SHO", _safe_int(_get(player, "shooting", 0))),
        ("PAS", _safe_int(_get(player, "passing", 0))),
        ("DRI", _safe_int(_get(player, "dribbling", 0))),
        ("DEF", _safe_int(_get(player, "defending", 0))),
        ("PHY", _safe_int(_get(player, "physical", 0))),
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

    footer = []
    age = _safe_int(_get(player, "age", 0))
    weak_foot = _safe_int(_get(player, "weak_foot", 0))
    skill_moves = _safe_int(_get(player, "skill_moves", 0))

    if age:
        footer.append(f"AGE {age}")
    if weak_foot:
        footer.append(f"WF {weak_foot}★")
    if skill_moves:
        footer.append(f"SM {skill_moves}★")

    draw.text(
        (360, 940),
        "   |   ".join(footer) if footer else "FC26 AUCTION CARD",
        font=_font(26, True),
        anchor="mm",
        fill=(255, 235, 160)
    )

    img.save(out, quality=95)
    return out
