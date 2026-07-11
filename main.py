"""
Roblox DistroKid -> Telegram бот.
Следит за новыми аудио из раздела DistroKid Hits в Creator Store
и постит ч/б карточку (обложка + waveform) в Telegram-канал.
"""

import io
import json
import logging
import os
import time
from pathlib import Path
from urllib.parse import quote

import numpy as np
import pyloudnorm as pyln
import requests
from PIL import Image, ImageDraw, ImageFont, ImageOps
from pydub import AudioSegment

# ---------------- Настройки ----------------
BOT_TOKEN = os.environ["BOT_TOKEN"]
CHANNEL_ID = os.environ["CHANNEL_ID"]        # "@мой_канал" или "-100123456789"
POLL_SECONDS = int(os.environ.get("POLL_SECONDS", "300"))
ROBLOSECURITY = os.environ.get("ROBLOSECURITY", "")  # опционально: кука, если скачивание аудио требует логин

# Эндпоинт раздела DistroKid Hits (категория 3 = Audio).
# Если перестанет работать — возьми актуальный URL из DevTools (см. заметку на странице).
DISCOVER_URL = (
    "https://apis.roblox.com/toolbox-service/v1/marketplace/3"
    "?limit=30&sortOrder=Desc&sortCategory=CreateTime"
    "&audioDiscoveryCollection=distrokid-hits"
)
DETAILS_BASE = "https://apis.roblox.com/toolbox-service/v1/items/details?assetIds="
THUMB_BASE = "https://thumbnails.roblox.com/v1/assets?size=420x420&format=Png&assetIds="
AUDIO_BASE = "https://assetdelivery.roblox.com/v1/asset/?id="
# Три строки ниже — мусор от редактора, можно удалить:
# DETAILS_URL = "https://apis.roblox.com/toolbox-service/v1/items/details?assetIds={ids}"
# THUMB_URL = "https://thumbnails.roblox.com/v1/assets?assetIds={id}&size=420x420&format=Png"
# AUDIO_URL = "https://assetdelivery.roblox.com/v1/asset/?id={id}"

SEEN_FILE = Path("seen_ids.json")
FONT_BOLD = "fonts/Inter-Bold.ttf"
FONT_REGULAR = "fonts/Inter-Regular.ttf"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("distrokid-bot")

session = requests.Session()
session.headers["User-Agent"] = "Mozilla/5.0 (distrokid-tg-bot)"
if ROBLOSECURITY:
    session.cookies.set(".ROBLOSECURITY", ROBLOSECURITY, domain=".roblox.com")


# ---------------- Roblox API ----------------
def fetch_new_asset_ids() -> list[int]:
    r = session.get(DISCOVER_URL, timeout=30)
    r.raise_for_status()
    data = r.json()
    ids = []
    for item in data.get("data", []):
        asset_id = item.get("id") or item.get("asset", {}).get("id")
        if asset_id:
            ids.append(int(asset_id))
    return ids


def fetch_details(asset_ids: list[int]) -> dict[int, dict]:
    r = session.get(DETAILS_BASE + ",".join(map(str, asset_ids)), timeout=30)
    r.raise_for_status()
    out = {}
    for item in r.json().get("data", []):
        asset = item.get("asset", {})
        audio = asset.get("audioDetails") or {}
        creator = item.get("creator", {})
        aid = int(asset["id"])
        out[aid] = {
            "title": audio.get("title") or asset.get("name", "Unknown"),
            "artist": audio.get("artist") or creator.get("name", "Unknown"),
        }
    return out


def fetch_cover(asset_id: int) -> bytes | None:
    try:
        r = session.get(THUMB_BASE + str(asset_id), timeout=30)
        url = r.json()["data"][0]["imageUrl"]
        return session.get(url, timeout=30).content
    except Exception:
        return None


def fetch_audio(asset_id: int) -> bytes | None:
    try:
        r = session.get(AUDIO_BASE + str(asset_id), timeout=60)
        r.raise_for_status()
        return r.content
    except Exception as e:
        log.warning("Не удалось скачать аудио %s: %s", asset_id, e)
        return None


# ---------------- Анализ аудио ----------------
def analyze(audio_bytes: bytes) -> dict:
    seg = AudioSegment.from_file(io.BytesIO(audio_bytes))
    arr = np.array(seg.get_array_of_samples()).astype(np.float32)
    arr /= float(1 << (8 * seg.sample_width - 1))
    if seg.channels > 1:
        arr = arr.reshape((-1, seg.channels))
        mono = arr.mean(axis=1)
    else:
        mono = arr
    try:
        lufs = pyln.Meter(seg.frame_rate).integrated_loudness(
            arr if seg.channels > 1 else mono
        )
    except Exception:
        lufs = float("nan")
    return {
        "duration": seg.duration_seconds,
        "lufs": lufs,
        "peak_db": seg.max_dBFS,
        "channels": seg.channels,
        "rate": seg.frame_rate,
        "mono": mono,
    }


# ---------------- Картинка-карточка ----------------
def _font(path: str, size: int) -> ImageFont.FreeTypeFont:
    try:
        return ImageFont.truetype(path, size)
    except OSError:
        return ImageFont.load_default()


def _fit_text(draw: ImageDraw.ImageDraw, text: str, font, max_w: int) -> str:
    if draw.textlength(text, font=font) <= max_w:
        return text
    while text and draw.textlength(text + "…", font=font) > max_w:
        text = text[:-1]
    return text + "…"


def render_card(cover: bytes | None, title: str, artist: str, mono: np.ndarray) -> bytes:
    W, H = 1200, 675
    img = Image.new("RGB", (W, H), "white")
    d = ImageDraw.Draw(img)

    # Обложка: ч/б, тонкая рамка
    cx, cy, csize = 60, 60, 300
    if cover:
        c = Image.open(io.BytesIO(cover)).convert("L").convert("RGB")
        c = ImageOps.fit(c, (csize, csize))
        img.paste(c, (cx, cy))
    d.rectangle([cx, cy, cx + csize, cy + csize], outline="black", width=2)

    # Название + артист
    f_title = _font(FONT_BOLD, 54)
    f_artist = _font(FONT_REGULAR, 36)
    tx = cx + csize + 50
    d.text((tx, 130), _fit_text(d, title, f_title, W - tx - 60), font=f_title, fill="black")
    d.text((tx, 210), _fit_text(d, artist, f_artist, W - tx - 60), font=f_artist, fill=(110, 110, 110))

    # Waveform: тонкие чёрные бары по центральной оси
    bars, gap = 140, 3
    wf_x, wf_w = 60, W - 120
    wf_mid, wf_amp = 520, 90
    bar_w = (wf_w - gap * (bars - 1)) / bars
    chunks = np.array_split(np.abs(mono), bars)
    peaks = np.array([c.max() if len(c) else 0.0 for c in chunks])
    if peaks.max() > 0:
        peaks = peaks / peaks.max()
    for i, p in enumerate(peaks):
        h = max(3, int(p * wf_amp))
        x = wf_x + i * (bar_w + gap)
        d.rectangle([x, wf_mid - h, x + bar_w, wf_mid + h], fill="black")

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


# ---------------- Telegram ----------------
def build_caption(info: dict, asset_id: int, artist: str) -> str:
    m, s = divmod(round(info["duration"]), 60)
    ch = "Stereo" if info["channels"] > 1 else "Mono"
    lufs = f"{info['lufs']:.1f} LUFS" if info["lufs"] == info["lufs"] else "— LUFS"
    artist_url = (
        "https://create.roblox.com/store/audio?artistName="
        + quote(artist) + "&sortCategory=CreateTime"
    )
    asset_url = "https://create.roblox.com/store/asset/" + str(asset_id)  # f"https://create.roblox.com/store/asset/{asset_id}"
    return (
        f"{m}:{s:02d}  ·  {lufs} / {info['peak_db']:.1f} dB peak  ·  {ch}  ·  {info['rate']} Hz\n"
        f'ID: <a href="{asset_url}">{asset_id}</a>\n'
        f'Артист: <a href="{artist_url}">{artist}</a>'
    )


def send_photo(photo: bytes, caption: str) -> None:
    r = requests.post(
        "https://api.telegram.org/bot" + BOT_TOKEN + "/sendPhoto",  # f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto",
        data={"chat_id": CHANNEL_ID, "caption": caption, "parse_mode": "HTML"},
        files={"photo": ("card.png", photo, "image/png")},
        timeout=60,
    )
    r.raise_for_status()


# ---------------- Основной цикл ----------------
def load_seen() -> set[int]:
    if SEEN_FILE.exists():
        return set(json.loads(SEEN_FILE.read_text()))
    return set()


def save_seen(seen: set[int]) -> None:
    SEEN_FILE.write_text(json.dumps(sorted(seen)))


def process(asset_id: int, meta: dict) -> None:
    audio = fetch_audio(asset_id)
    if not audio:
        return
    info = analyze(audio)
    card = render_card(fetch_cover(asset_id), meta["title"], meta["artist"], info["mono"])
    send_photo(card, build_caption(info, asset_id, meta["artist"]))
    log.info("Отправлено: %s — %s (%s)", meta["artist"], meta["title"], asset_id)


def main() -> None:
    seen = load_seen()
    first_run = not seen
    while True:
        try:
            ids = fetch_new_asset_ids()
            new = [i for i in ids if i not in seen]
            if first_run:
                # при первом запуске просто запоминаем текущие, без спама в канал
                seen.update(new)
                save_seen(seen)
                first_run = False
                log.info("Первый запуск: запомнил %d аудио", len(new))
            elif new:
                details = fetch_details(new)
                for aid in reversed(new):  # от старых к новым
                    try:
                        process(aid, details.get(aid, {"title": "Unknown", "artist": "Unknown"}))
                    except Exception:
                        log.exception("Ошибка обработки %s", aid)
                    seen.add(aid)
                    save_seen(seen)
                    time.sleep(3)  # не упереться в лимиты Telegram
        except Exception:
            log.exception("Ошибка цикла")
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
