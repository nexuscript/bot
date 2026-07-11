#!/usr/bin/env python3
"""
DistroKid → Roblox → Telegram логгер.

Каждые CHECK_INTERVAL секунд проверяет новые аудио DistroKid в Roblox,
анализирует звук (LUFS, peak dB, стерео/моно, Hz, длительность),
рисует минималистичную ч/б карточку (обложка + название + артист + waveform)
и постит в Telegram-канал: фото с подписью + mp3-файл ниже.

Зависимости: см. requirements.txt
Переменные окружения:
  TELEGRAM_BOT_TOKEN  - токен бота от @BotFather
  TELEGRAM_CHANNEL_ID - @username канала или числовой ID (бот должен быть админом)
  CHECK_INTERVAL      - интервал проверки в секундах (по умолчанию 300)
"""

import gzip
import io
import logging
import math
import os
import sqlite3
import sys
import time
import urllib.parse

import lameenc
import numpy as np
import requests
import soundfile as sf
from PIL import Image, ImageDraw, ImageFont, ImageOps
from scipy.signal import lfilter

# ---------------------------------------------------------------- config

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
CHANNEL_ID = os.environ.get("TELEGRAM_CHANNEL_ID", "")
CHECK_INTERVAL = int(os.environ.get("CHECK_INTERVAL", "300"))
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "posted.db")
FONTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts")

DISTROKID_CREATOR_ID = 7135127272
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)
HEADERS = {"User-Agent": UA, "Accept": "application/json"}

WAVEFORM_BUCKETS = 96
CARD_W, CARD_H = 1080, 1080
MARGIN = 88
COVER_SIZE = 440

FONT_URLS = {
    "Inter-Regular.ttf": "https://fonts.gstatic.com/s/inter/v20/UcCO3FwrK3iLTeHuS_nVMrMxCp50SjIw2boKoduKmMEVuLyfMZg.ttf",
    "Inter-SemiBold.ttf": "https://fonts.gstatic.com/s/inter/v20/UcCO3FwrK3iLTeHuS_nVMrMxCp50SjIw2boKoduKmMEVuGKYMZg.ttf",
    "Inter-Bold.ttf": "https://fonts.gstatic.com/s/inter/v20/UcCO3FwrK3iLTeHuS_nVMrMxCp50SjIw2boKoduKmMEVuFuYMZg.ttf",
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("distrokid-bot")

# ---------------------------------------------------------------- db


def db_connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """CREATE TABLE IF NOT EXISTS posted_assets (
             asset_id INTEGER PRIMARY KEY,
             name TEXT,
             artist TEXT,
             created_utc TEXT,
             posted_at TEXT NOT NULL DEFAULT (datetime('now')),
             seeded INTEGER NOT NULL DEFAULT 0
           )"""
    )
    conn.commit()
    return conn


def is_first_run(conn: sqlite3.Connection) -> bool:
    (count,) = conn.execute("SELECT COUNT(*) FROM posted_assets").fetchone()
    return count == 0


def already_posted(conn: sqlite3.Connection, asset_id: int) -> bool:
    row = conn.execute(
        "SELECT 1 FROM posted_assets WHERE asset_id = ?", (asset_id,)
    ).fetchone()
    return row is not None


def mark_posted(conn, asset_id: int, name: str, artist: str, created_utc: str, seeded: bool):
    conn.execute(
        "INSERT OR IGNORE INTO posted_assets (asset_id, name, artist, created_utc, seeded)"
        " VALUES (?, ?, ?, ?, ?)",
        (asset_id, name, artist, created_utc, 1 if seeded else 0),
    )
    conn.commit()


# ---------------------------------------------------------------- roblox api


def fetch_latest_ids(limit: int = 50) -> list[int]:
    url = (
        f"https://apis.roblox.com/toolbox-service/v1/marketplace/3?limit={limit}"
        f"&creatorTargetId={DISTROKID_CREATOR_ID}&creatorType=1&audioTypes=0"
        f"&uiSortIntent=10&sortDirection=Descending"
    )
    r = requests.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    return [d["id"] for d in r.json().get("data", [])]


def fetch_details(asset_ids: list[int]) -> list[dict]:
    if not asset_ids:
        return []
    ids = ",".join(str(i) for i in asset_ids)
    url = f"https://apis.roblox.com/toolbox-service/v1/items/details?assetIds={ids}"
    r = requests.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    items = []
    for it in r.json().get("data", []):
        asset = it.get("asset", {})
        audio = asset.get("audioDetails") or {}
        creator = it.get("creator") or {}
        items.append(
            {
                "id": asset["id"],
                "name": audio.get("title") or asset.get("name", "Unknown"),
                "artist": audio.get("artist") or creator.get("name") or "Unknown",
                "created_utc": asset.get("createdUtc", ""),
            }
        )
    return items


def fetch_thumbnail(asset_id: int) -> bytes | None:
    try:
        r = requests.get(
            f"https://thumbnails.roblox.com/v1/assets?assetIds={asset_id}&size=420x420&format=Png",
            headers=HEADERS,
            timeout=30,
        )
        r.raise_for_status()
        data = r.json().get("data") or []
        if not data or data[0].get("state") != "Completed" or not data[0].get("imageUrl"):
            return None
        img = requests.get(data[0]["imageUrl"], headers=HEADERS, timeout=30)
        img.raise_for_status()
        return img.content
    except Exception as e:
        log.warning("thumbnail failed for %s: %s", asset_id, e)
        return None


def download_audio(asset_id: int) -> bytes:
    r = requests.get(
        f"https://assetdelivery.roblox.com/v1/asset?id={asset_id}",
        headers={"User-Agent": UA},
        timeout=60,
        allow_redirects=True,
    )
    r.raise_for_status()
    buf = r.content
    # Иногда прилетает сырой gzip без заголовка Content-Encoding
    if len(buf) > 2 and buf[0] == 0x1F and buf[1] == 0x8B:
        buf = gzip.decompress(buf)
    return buf


def asset_url(asset_id: int) -> str:
    return f"https://create.roblox.com/store/asset/{asset_id}"


def artist_url(artist: str) -> str:
    q = urllib.parse.quote(artist, safe="")
    return f"https://create.roblox.com/store/audio?artistName={q}&sortCategory=CreateTime"


# ---------------------------------------------------------------- audio analysis


def analyze_ogg(ogg: bytes) -> dict:
    """Декодирует OGG Vorbis и считает LUFS / peak dB / waveform."""
    data, sample_rate = sf.read(io.BytesIO(ogg), dtype="float32", always_2d=True)
    # data: (samples, channels)
    samples, channels = data.shape
    duration = samples / sample_rate

    is_stereo = channels >= 2 and bool(
        np.any(np.abs(data[::  max(1, samples // 5000), 0] - data[:: max(1, samples // 5000), 1]) > 1e-4)
    )

    peak = float(np.max(np.abs(data)))
    peak_db = 20 * math.log10(peak) if peak > 0 else float("-inf")

    lufs = integrated_lufs(data, sample_rate)
    waveform = compute_waveform(data)

    return {
        "duration": duration,
        "sample_rate": sample_rate,
        "channels": channels,
        "is_stereo": is_stereo,
        "peak_db": peak_db,
        "lufs": lufs,
        "waveform": waveform,
        "pcm": data,  # (samples, channels) float32
    }


def compute_waveform(data: np.ndarray, buckets: int = WAVEFORM_BUCKETS) -> list[float]:
    mono = data.mean(axis=1)
    n = len(mono)
    bucket_size = max(1, n // buckets)
    out = []
    for b in range(buckets):
        seg = mono[b * bucket_size : min(n, (b + 1) * bucket_size)]
        out.append(float(np.sqrt(np.mean(seg**2))) if len(seg) else 0.0)
    mx = max(max(out), 1e-9)
    return [v / mx for v in out]


def _shelf_coeffs(fs: float):
    """K-weighting stage 1: high-shelf (ITU-R BS.1770)."""
    db = 3.999843853973347
    f0 = 1681.974450955533
    Q = 0.7071752369554196
    K = math.tan(math.pi * f0 / fs)
    Vh = 10 ** (db / 20)
    Vb = Vh**0.4996667741545416
    denom = 1 + K / Q + K * K
    b = [
        (Vh + Vb * K / Q + K * K) / denom,
        2 * (K * K - Vh) / denom,
        (Vh - Vb * K / Q + K * K) / denom,
    ]
    a = [1.0, 2 * (K * K - 1) / denom, (1 - K / Q + K * K) / denom]
    return b, a


def _highpass_coeffs(fs: float):
    """K-weighting stage 2: high-pass (ITU-R BS.1770)."""
    f0 = 38.13547087602444
    Q = 0.5003270373238773
    K = math.tan(math.pi * f0 / fs)
    denom = 1 + K / Q + K * K
    b = [1.0, -2.0, 1.0]
    a = [1.0, 2 * (K * K - 1) / denom, (1 - K / Q + K * K) / denom]
    return b, a


def integrated_lufs(data: np.ndarray, fs: int) -> float:
    """Integrated loudness по ITU-R BS.1770-4 (гейтинг -70 LUFS + относительный -10 LU)."""
    sb, sa = _shelf_coeffs(fs)
    hb, ha = _highpass_coeffs(fs)

    weighted = np.empty_like(data)
    for ch in range(data.shape[1]):
        weighted[:, ch] = lfilter(hb, ha, lfilter(sb, sa, data[:, ch]))

    block = round(0.4 * fs)
    hop = round(0.1 * fs)
    n = weighted.shape[0]
    if n < block:
        return float("-inf")

    sq = weighted**2
    block_loudness = []
    for start in range(0, n - block + 1, hop):
        mean_sq = sq[start : start + block].mean(axis=0).sum()  # веса каналов 1.0
        block_loudness.append(-0.691 + 10 * math.log10(mean_sq + 1e-12))

    abs_gated = [l for l in block_loudness if l > -70]
    if not abs_gated:
        return float("-inf")

    def mean_energy(vals):
        return sum(10 ** ((v + 0.691) / 10) for v in vals) / len(vals)

    rel_threshold = -0.691 + 10 * math.log10(mean_energy(abs_gated)) - 10
    rel_gated = [l for l in block_loudness if l > rel_threshold]
    if not rel_gated:
        return float("-inf")
    return -0.691 + 10 * math.log10(mean_energy(rel_gated))


# ---------------------------------------------------------------- mp3


def encode_mp3(pcm: np.ndarray, sample_rate: int) -> bytes:
    """PCM (samples, channels) float32 → MP3 192 kbps."""
    channels = min(pcm.shape[1], 2)
    int16 = np.clip(pcm[:, :channels] * 32767, -32768, 32767).astype(np.int16)
    if channels == 1:
        interleaved = int16[:, 0]
    else:
        interleaved = int16.reshape(-1)  # уже (samples, 2) → interleaved

    enc = lameenc.Encoder()
    enc.set_bit_rate(192)
    enc.set_in_sample_rate(sample_rate)
    enc.set_channels(channels)
    enc.set_quality(2)
    out = enc.encode(interleaved.tobytes())
    out += enc.flush()
    return bytes(out)


# ---------------------------------------------------------------- card rendering


def ensure_fonts():
    os.makedirs(FONTS_DIR, exist_ok=True)
    for fname, url in FONT_URLS.items():
        path = os.path.join(FONTS_DIR, fname)
        if not os.path.exists(path):
            log.info("downloading font %s", fname)
            r = requests.get(url, timeout=30)
            r.raise_for_status()
            with open(path, "wb") as f:
                f.write(r.content)


def _font(name: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(os.path.join(FONTS_DIR, name), size)


def _fit_text(draw: ImageDraw.ImageDraw, text: str, font_name: str, max_size: int, max_width: int):
    size = max_size
    font = _font(font_name, size)
    while draw.textlength(text, font=font) > max_width and size > 28:
        size -= 2
        font = _font(font_name, size)
    t = text
    while draw.textlength(t, font=font) > max_width and len(t) > 4:
        t = t[:-2]
    if t != text:
        t = t.rstrip() + "…"
    return t, font


def _spaced_text(draw, text: str, cx: int, y: int, font, spacing: int, fill):
    widths = [draw.textlength(ch, font=font) for ch in text]
    total = sum(widths) + spacing * (len(text) - 1)
    x = cx - total / 2
    for ch, w in zip(text, widths):
        draw.text((x, y), ch, font=font, fill=fill)
        x += w + spacing


def render_card(title: str, artist: str, cover: bytes | None, waveform: list[float]) -> bytes:
    img = Image.new("RGB", (CARD_W, CARD_H), "#ffffff")
    draw = ImageDraw.Draw(img)

    # --- обложка (ч/б), по центру ---
    cover_x = (CARD_W - COVER_SIZE) // 2
    cover_y = MARGIN + 20
    if cover:
        try:
            c = Image.open(io.BytesIO(cover)).convert("RGB")
            c = ImageOps.grayscale(c).convert("RGB")
            c = c.resize((COVER_SIZE, COVER_SIZE), Image.LANCZOS)
            img.paste(c, (cover_x, cover_y))
        except Exception:
            _cover_fallback(draw, cover_x, cover_y)
    else:
        _cover_fallback(draw, cover_x, cover_y)
    draw.rectangle(
        [cover_x, cover_y, cover_x + COVER_SIZE, cover_y + COVER_SIZE],
        outline="#000000",
        width=2,
    )

    # --- название ---
    title_y = cover_y + COVER_SIZE + 108
    t, t_font = _fit_text(draw, title, "Inter-Bold.ttf", 58, CARD_W - MARGIN * 2)
    draw.text((CARD_W / 2, title_y), t, font=t_font, fill="#000000", anchor="ms")

    # --- артист (разреженный, капсом) ---
    a, a_font = _fit_text(draw, artist.upper(), "Inter-SemiBold.ttf", 30, CARD_W - MARGIN * 2)
    _spaced_text(draw, a, CARD_W // 2, title_y + 34, a_font, 4, "#6b6b6b")

    # --- разделитель ---
    divider_y = title_y + 130
    draw.line([MARGIN, divider_y, CARD_W - MARGIN, divider_y], fill="#e2e2e2", width=1)

    # --- waveform ---
    wf_top = divider_y + 56
    wf_height = CARD_H - wf_top - MARGIN - 20
    _draw_waveform(draw, waveform, MARGIN, wf_top, CARD_W - MARGIN * 2, wf_height)

    out = io.BytesIO()
    img.save(out, "PNG")
    return out.getvalue()


def _cover_fallback(draw, x: int, y: int):
    draw.rectangle([x, y, x + COVER_SIZE, y + COVER_SIZE], fill="#f2f2f2")
    cx, cy = x + COVER_SIZE // 2, y + COVER_SIZE // 2
    r = int(COVER_SIZE * 0.3)
    draw.ellipse([cx - r, cy - r, cx + r, cy + r], outline="#c9c9c9", width=2)
    draw.ellipse([cx - 6, cy - 6, cx + 6, cy + 6], fill="#c9c9c9")


def _draw_waveform(draw, waveform: list[float], x: int, y: int, width: int, height: int):
    n = len(waveform)
    gap = 4
    bar_w = (width - gap * (n - 1)) / n
    center_y = y + height / 2
    for i, v in enumerate(waveform):
        v = max(v, 0.04)
        bar_h = max(4, v * height * 0.92)
        bx = x + i * (bar_w + gap)
        by = center_y - bar_h / 2
        radius = min(bar_w / 2, 3)
        draw.rounded_rectangle([bx, by, bx + bar_w, by + bar_h], radius=radius, fill="#000000")


# ---------------------------------------------------------------- telegram


def _tg(method: str, data: dict, files: dict | None = None) -> dict:
    r = requests.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/{method}",
        data=data,
        files=files,
        timeout=120,
    )
    j = r.json()
    if not j.get("ok"):
        raise RuntimeError(f"Telegram {method} failed: {j.get('description', r.status_code)}")
    return j["result"]


def escape_html(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def send_photo(photo: bytes, caption: str) -> int:
    result = _tg(
        "sendPhoto",
        {"chat_id": CHANNEL_ID, "caption": caption, "parse_mode": "HTML"},
        {"photo": ("card.png", photo, "image/png")},
    )
    return result["message_id"]


def send_audio(mp3: bytes, title: str, performer: str, thumbnail: bytes | None, reply_to: int):
    files = {"audio": ("track.mp3", mp3, "audio/mpeg")}
    if thumbnail:
        files["thumbnail"] = ("thumb.png", thumbnail, "image/png")
    _tg(
        "sendAudio",
        {
            "chat_id": CHANNEL_ID,
            "title": title,
            "performer": performer,
            "reply_to_message_id": reply_to,
        },
        files,
    )


# ---------------------------------------------------------------- pipeline


def format_duration(seconds: float) -> str:
    m = int(seconds // 60)
    s = round(seconds % 60)
    return f"{m}:{s:02d}"


def format_db(v: float) -> str:
    return "-inf" if math.isinf(v) else f"{v:.1f}"


def build_caption(item: dict, a: dict) -> str:
    stereo = "Стерео" if a["is_stereo"] else "Моно"
    return "\n".join(
        [
            f"<b>{escape_html(item['name'])}</b>",
            "",
            f"Длительность: {format_duration(a['duration'])}",
            f"Громкость: {format_db(a['lufs'])} LUFS / {format_db(a['peak_db'])} dB peak",
            f"{stereo} · {a['sample_rate']} Hz",
            f"ID: <a href=\"{asset_url(item['id'])}\">{item['id']}</a>",
            f"Артист: <a href=\"{artist_url(item['artist'])}\">{escape_html(item['artist'])}</a>",
        ]
    )


def process_track(item: dict):
    log.info("processing %s — %s (%s)", item["artist"], item["name"], item["id"])
    ogg = download_audio(item["id"])
    cover = fetch_thumbnail(item["id"])
    analysis = analyze_ogg(ogg)

    card = render_card(item["name"], item["artist"], cover, analysis["waveform"])
    caption = build_caption(item, analysis)
    photo_message_id = send_photo(card, caption)

    mp3 = encode_mp3(analysis["pcm"], analysis["sample_rate"])
    send_audio(mp3, item["name"], item["artist"], cover, photo_message_id)
    log.info("posted %s", item["id"])


def check_once(conn: sqlite3.Connection):
    ids = fetch_latest_ids(50)
    if not ids:
        log.info("no assets returned")
        return

    first_run = is_first_run(conn)
    new_ids = [i for i in ids if not already_posted(conn, i)]
    if not new_ids:
        log.info("no new tracks")
        return

    details = {d["id"]: d for d in fetch_details(new_ids)}

    if first_run:
        # Первый запуск: только запоминаем текущие треки, без спама в канал
        log.info("first run — seeding %d assets without posting", len(new_ids))
        for i in new_ids:
            d = details.get(i, {})
            mark_posted(conn, i, d.get("name", ""), d.get("artist", ""), d.get("created_utc", ""), seeded=True)
        return

    # Постим от старых к новым
    for i in reversed(new_ids):
        d = details.get(i)
        if not d:
            mark_posted(conn, i, "", "", "", seeded=False)
            continue
        try:
            process_track(d)
        except Exception:
            log.exception("failed to process %s — will not retry", i)
        mark_posted(conn, i, d["name"], d["artist"], d["created_utc"], seeded=False)
        time.sleep(3)  # не упираемся в rate limit телеграма


def main():
    if not BOT_TOKEN or not CHANNEL_ID:
        print("Ошибка: задай переменные окружения TELEGRAM_BOT_TOKEN и TELEGRAM_CHANNEL_ID")
        sys.exit(1)

    ensure_fonts()
    conn = db_connect()
    log.info("started, checking every %ds", CHECK_INTERVAL)

    while True:
        try:
            check_once(conn)
        except Exception:
            log.exception("check failed")
        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    main()
