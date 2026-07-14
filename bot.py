#!/usr/bin/env python3
"""
DistroKid → Roblox → Telegram логгер.

Непрерывно проверяет новые аудио DistroKid в Roblox (запрос за запросом,
с автоматическим бэкоффом при 429), анализирует звук (LUFS, peak dB,
стерео/моно, Hz, длительность), и если трек bypassed (громче -3 LUFS или
пик выше +4 dB) — рисует минималистичную ч/б карточку (обложка + название +
артист + waveform) и постит в Telegram-канал: фото с подписью + mp3 ниже.

Зависимости: см. requirements.txt
Переменные окружения:
  TELEGRAM_BOT_TOKEN  - токен бота от @BotFather
  TELEGRAM_CHANNEL_ID - @username канала или числовой ID (бот должен быть админом)
  CHECK_INTERVAL      - пауза между проверками в секундах (по умолчанию 0 — непрерывно)
  ONLY_BYPASSED       - "1" постить только bypassed (по умолчанию), "0" — все треки
  BYPASS_LUFS         - порог LUFS для bypass (по умолчанию -3)
  BYPASS_PEAK_DB      - порог пика dB для bypass (по умолчанию 4)
"""

import gzip
import io
import logging
import math
import os
import sqlite3
import sys
import threading
import time
import urllib.parse

import lameenc
import numpy as np
import requests
import soundfile as sf
from PIL import Image, ImageDraw, ImageFont, ImageOps
from requests.adapters import HTTPAdapter
from scipy.signal import lfilter, resample_poly

# ---------------------------------------------------------------- config

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
CHANNEL_ID = os.environ.get("TELEGRAM_CHANNEL_ID", "")
# 0 = непрерывная проверка (запрос за запросом без пауз).
# При 429 от Roblox поллер сам выждет указанный сервером Retry-After и продолжит.
CHECK_INTERVAL = float(os.environ.get("CHECK_INTERVAL", "30"))
# Публиковать только bypassed-треки (громче порогов ниже). "0" — постить все.
ONLY_BYPASSED = os.environ.get("ONLY_BYPASSED", "1") != "0"
# После стольких неудачных попыток трек помечается пропущенным навсегда.
# Защищает от бесконечного цикла, если процесс жёстко убивают (например OOM-killer).
MAX_ATTEMPTS = int(os.environ.get("MAX_ATTEMPTS", "3"))
# Bypass-детект: трек считается "пробившим" лимиты, если громче этих порогов
BYPASS_LUFS = float(os.environ.get("BYPASS_LUFS", "-2"))
BYPASS_PEAK_DB = float(os.environ.get("BYPASS_PEAK_DB", "6"))
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "posted.db")
FONTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts")

DISTROKID_CREATOR_ID = 7135127272
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)
HEADERS = {"User-Agent": UA, "Accept": "application/json"}

# Одна сессия на весь процесс: keep-alive соединения к Roblox/Telegram
# вместо нового TLS-хендшейка на каждый запрос — заметно быстрее.
SESSION = requests.Session()
SESSION.headers.update({"User-Agent": UA})
_adapter = HTTPAdapter(pool_connections=8, pool_maxsize=8, max_retries=2)
SESSION.mount("https://", _adapter)
SESSION.mount("http://", _adapter)

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
    # timeout + WAL: поллер и воркер пишут из разных потоков без "database is locked"
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
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
    # Счётчик попыток обработки: строка появляется ДО обработки трека и
    # переживает жёсткий краш процесса, поэтому один и тот же трек не может
    # зациклиться навечно.
    conn.execute(
        """CREATE TABLE IF NOT EXISTS attempts (
             asset_id INTEGER PRIMARY KEY,
             count INTEGER NOT NULL DEFAULT 0
           )"""
    )
    # Персистентная очередь: поллер складывает сюда новые треки, воркер
    # разбирает по одному (FIFO по seq). Ничего не теряется — даже при
    # наплыве треков или рестарте процесса.
    # ВАЖНО: seq — отдельный AUTOINCREMENT-столбец. Нельзя использовать
    # asset_id как PRIMARY KEY для порядка: в SQLite он стал бы алиасом
    # rowid, и очередь сортировалась бы по ID ассета, а не по времени
    # добавления (сломались бы FIFO и requeue_to_back).
    conn.execute(
        """CREATE TABLE IF NOT EXISTS queue (
             seq INTEGER PRIMARY KEY AUTOINCREMENT,
             asset_id INTEGER NOT NULL UNIQUE,
             name TEXT,
             artist TEXT,
             created_utc TEXT
           )"""
    )
    # Миграция со старой схемы (asset_id был PRIMARY KEY, без seq)
    cols = [r[1] for r in conn.execute("PRAGMA table_info(queue)").fetchall()]
    if "seq" not in cols:
        conn.execute("ALTER TABLE queue RENAME TO queue_old")
        conn.execute(
            """CREATE TABLE queue (
                 seq INTEGER PRIMARY KEY AUTOINCREMENT,
                 asset_id INTEGER NOT NULL UNIQUE,
                 name TEXT,
                 artist TEXT,
                 created_utc TEXT
               )"""
        )
        conn.execute(
            "INSERT INTO queue (asset_id, name, artist, created_utc) "
            "SELECT asset_id, name, artist, created_utc FROM queue_old ORDER BY rowid"
        )
        conn.execute("DROP TABLE queue_old")
    conn.commit()
    return conn


def enqueue(conn: sqlite3.Connection, item: dict):
    conn.execute(
        "INSERT OR IGNORE INTO queue (asset_id, name, artist, created_utc) VALUES (?, ?, ?, ?)",
        (item["id"], item["name"], item["artist"], item["created_utc"]),
    )
    conn.commit()


def queue_next(conn: sqlite3.Connection) -> dict | None:
    row = conn.execute(
        "SELECT asset_id, name, artist, created_utc FROM queue ORDER BY seq LIMIT 1"
    ).fetchone()
    if not row:
        return None
    return {"id": row[0], "name": row[1], "artist": row[2], "created_utc": row[3]}


def dequeue(conn: sqlite3.Connection, asset_id: int):
    conn.execute("DELETE FROM queue WHERE asset_id = ?", (asset_id,))
    conn.commit()


def requeue_to_back(conn: sqlite3.Connection, item: dict):
    """Переставляет неудавшийся трек в конец очереди (новый seq)."""
    conn.execute("DELETE FROM queue WHERE asset_id = ?", (item["id"],))
    conn.execute(
        "INSERT INTO queue (asset_id, name, artist, created_utc) VALUES (?, ?, ?, ?)",
        (item["id"], item["name"], item["artist"], item["created_utc"]),
    )
    conn.commit()


def queue_size(conn: sqlite3.Connection) -> int:
    (n,) = conn.execute("SELECT COUNT(*) FROM queue").fetchone()
    return n


def in_queue(conn: sqlite3.Connection, asset_id: int) -> bool:
    return conn.execute("SELECT 1 FROM queue WHERE asset_id = ?", (asset_id,)).fetchone() is not None


def get_attempts(conn: sqlite3.Connection, asset_id: int) -> int:
    row = conn.execute(
        "SELECT count FROM attempts WHERE asset_id = ?", (asset_id,)
    ).fetchone()
    return row[0] if row else 0


def bump_attempt(conn: sqlite3.Connection, asset_id: int) -> int:
    """Увеличивает счётчик попыток и СРАЗУ коммитит (до рискованной обработки)."""
    conn.execute(
        "INSERT INTO attempts (asset_id, count) VALUES (?, 1) "
        "ON CONFLICT(asset_id) DO UPDATE SET count = count + 1",
        (asset_id,),
    )
    conn.commit()
    return get_attempts(conn, asset_id)


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
    r = SESSION.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    return [d["id"] for d in r.json().get("data", [])]


def fetch_details(asset_ids: list[int]) -> list[dict]:
    if not asset_ids:
        return []
    ids = ",".join(str(i) for i in asset_ids)
    url = f"https://apis.roblox.com/toolbox-service/v1/items/details?assetIds={ids}"
    r = SESSION.get(url, headers=HEADERS, timeout=30)
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


def fetch_thumbnail(asset_id: int, retries: int = 5, delay: float = 3.0) -> bytes | None:
    """Тянет обложку. У только что залитых аудио превью часто ещё в статусе
    Pending — поэтому опрашиваем несколько раз, ожидая Completed."""
    for attempt in range(retries):
        try:
            r = SESSION.get(
                f"https://thumbnails.roblox.com/v1/assets?assetIds={asset_id}&size=420x420&format=Png",
                headers=HEADERS,
                timeout=30,
            )
            r.raise_for_status()
            data = r.json().get("data") or []
            state = data[0].get("state") if data else None
            image_url = data[0].get("imageUrl") if data else None

            if state == "Completed" and image_url:
                img = SESSION.get(image_url, headers=HEADERS, timeout=30)
                img.raise_for_status()
                return img.content

            # Превью ещё генерируется — ждём и пробуем снова
            if state == "Pending" and attempt < retries - 1:
                log.info("thumbnail pending for %s, retry %d/%d", asset_id, attempt + 1, retries)
                time.sleep(delay)
                continue

            # Blocked / Error / TempUnavailable и т.п. — обложки не будет
            log.info("thumbnail unavailable for %s (state=%s)", asset_id, state)
            return None
        except Exception as e:
            log.warning("thumbnail request failed for %s: %s", asset_id, e)
            if attempt < retries - 1:
                time.sleep(delay)
    return None


def download_audio(asset_id: int) -> bytes:
    r = SESSION.get(
        f"https://assetdelivery.roblox.com/v1/asset?id={asset_id}",
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


LAME_RATES = [8000, 11025, 12000, 16000, 22050, 24000, 32000, 44100, 48000]
BLOCK_SECONDS = 15  # размер блока потоковой обработки


def _target_mp3_rate(sr: int) -> int:
    """MP3 (lameenc) поддерживает только частоты <= 48000. Подбираем валидную."""
    if sr in LAME_RATES:
        return sr
    if sr % 48000 == 0:
        return 48000
    if sr % 44100 == 0:
        return 44100
    candidates = [r for r in LAME_RATES if r <= sr] or LAME_RATES
    return min(candidates, key=lambda r: abs(r - sr))


def analyze_and_encode(ogg: bytes) -> dict:
    """Потоково декодирует OGG: считает LUFS / peak dB / waveform и на лету
    кодирует MP3 (с даунсемплом до <=48 кГц). Память почти не зависит от длины трека."""
    bio = io.BytesIO(ogg)
    with sf.SoundFile(bio) as f:
        sr = f.samplerate
        ch = f.channels
        total = max(1, f.frames)
        duration = total / sr

        target_sr = _target_mp3_rate(sr)
        mp3_ch = min(ch, 2)
        enc = lameenc.Encoder()
        enc.set_bit_rate(192)
        enc.set_in_sample_rate(target_sr)
        enc.set_channels(mp3_ch)
        enc.set_quality(5)  # быстрее кодирование при 192 kbps, разница на слух неразличима
        mp3 = bytearray()

        up = down = 1
        if target_sr != sr:
            g = math.gcd(target_sr, sr)
            up, down = target_sr // g, sr // g

        # K-weighting (ITU-R BS.1770), состояние фильтров тянем между блоками
        sb, sa = _shelf_coeffs(sr)
        hb, ha = _highpass_coeffs(sr)
        zi_s = [np.zeros(max(len(sa), len(sb)) - 1) for _ in range(ch)]
        zi_h = [np.zeros(max(len(ha), len(hb)) - 1) for _ in range(ch)]

        sub_len = max(1, round(0.1 * sr))  # субблок 100 мс
        sub_ms: list[np.ndarray] = []      # per-channel mean-square на каждый субблок
        carry = np.zeros((0, ch))          # хвост взвешенных сэмплов между блоками

        peak = 0.0
        stereo = False
        buckets_sumsq = np.zeros(WAVEFORM_BUCKETS)
        buckets_cnt = np.zeros(WAVEFORM_BUCKETS)

        frame_pos = 0
        block_frames = BLOCK_SECONDS * sr

        while True:
            block = f.read(block_frames, dtype="float32", always_2d=True)
            bn = block.shape[0]
            if bn == 0:
                break

            bmax = float(np.max(np.abs(block)))
            if bmax > peak:
                peak = bmax

            if ch >= 2 and not stereo and np.any(np.abs(block[:, 0] - block[:, 1]) > 1e-4):
                stereo = True

            # waveform: раскладываем блок по глобальным бакетам
            mono = block.mean(axis=1).astype(np.float64)
            idx = np.clip(
                (np.arange(frame_pos, frame_pos + bn) * WAVEFORM_BUCKETS) // total,
                0,
                WAVEFORM_BUCKETS - 1,
            )
            np.add.at(buckets_sumsq, idx, mono**2)
            np.add.at(buckets_cnt, idx, 1.0)

            # K-weighting поканально с сохранением состояния
            weighted = np.empty((bn, ch))
            for c in range(ch):
                y1, zi_s[c] = lfilter(sb, sa, block[:, c].astype(np.float64), zi=zi_s[c])
                y2, zi_h[c] = lfilter(hb, ha, y1, zi=zi_h[c])
                weighted[:, c] = y2
            if carry.shape[0]:
                weighted = np.vstack([carry, weighted])
            nfull = weighted.shape[0] // sub_len
            if nfull:
                used = nfull * sub_len
                ms = (weighted[:used].reshape(nfull, sub_len, ch) ** 2).mean(axis=1)
                sub_ms.extend(ms)
                carry = weighted[used:].copy()
            else:
                carry = weighted

            # mp3: даунсемпл блока и инкрементальное кодирование
            res = block[:, :mp3_ch] if up == 1 and down == 1 else resample_poly(block[:, :mp3_ch], up, down, axis=0)
            i16 = np.clip(res * 32767.0, -32768, 32767).astype(np.int16)
            inter = i16[:, 0] if mp3_ch == 1 else i16.reshape(-1)
            mp3 += enc.encode(inter.tobytes())

            frame_pos += bn

        mp3 += enc.flush()

    peak_db = 20 * math.log10(peak) if peak > 0 else float("-inf")
    lufs = _lufs_from_subblocks(sub_ms)

    with np.errstate(invalid="ignore", divide="ignore"):
        rms = np.sqrt(np.where(buckets_cnt > 0, buckets_sumsq / np.maximum(buckets_cnt, 1), 0.0))
    mx = float(rms.max()) or 1e-9
    waveform = (rms / mx).tolist()

    return {
        "duration": duration,
        "sample_rate": sr,
        "channels": ch,
        "is_stereo": bool(stereo),
        "peak_db": peak_db,
        "lufs": lufs,
        "waveform": waveform,
        "mp3": bytes(mp3),
    }


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


def _lufs_from_subblocks(sub_ms: list[np.ndarray]) -> float:
    """Integrated loudness по ITU-R BS.1770-4 из накопленных 100 мс субблоков
    (гейтинг абсолютный -70 LUFS + относительный -10 LU)."""
    if len(sub_ms) < 4:
        return float("-inf")
    arr = np.asarray(sub_ms)  # (num_sub, channels), mean-square на канал
    num = arr.shape[0]

    # 400 мс окно = 4 субблока подряд, шаг 100 мс (1 субблок)
    block_loud = []
    for k in range(0, num - 3):
        s = arr[k : k + 4].mean(axis=0).sum()  # среднее за 400 мс по каналам, веса 1.0
        block_loud.append(-0.691 + 10 * math.log10(s + 1e-12))
    block_loud = np.asarray(block_loud)

    abs_gated = block_loud[block_loud > -70]
    if abs_gated.size == 0:
        return float("-inf")

    def mean_energy(vals: np.ndarray) -> float:
        return float(np.mean(10 ** ((vals + 0.691) / 10)))

    rel_threshold = -0.691 + 10 * math.log10(mean_energy(abs_gated)) - 10
    rel_gated = block_loud[block_loud > rel_threshold]
    if rel_gated.size == 0:
        return float("-inf")
    return float(-0.691 + 10 * math.log10(mean_energy(rel_gated)))


# ---------------------------------------------------------------- card rendering


def ensure_fonts():
    os.makedirs(FONTS_DIR, exist_ok=True)
    for fname, url in FONT_URLS.items():
        path = os.path.join(FONTS_DIR, fname)
        if not os.path.exists(path):
            log.info("downloading font %s", fname)
            r = SESSION.get(url, timeout=30)
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
    r = SESSION.post(
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


def is_bypassed(a: dict) -> bool:
    """Аудио 'пробило' лимиты громкости Roblox: громче -3 LUFS или пик выше +4 dB."""
    return a["lufs"] > BYPASS_LUFS or a["peak_db"] > BYPASS_PEAK_DB


def build_caption(item: dict, a: dict) -> str:
    stereo = "Стерео" if a["is_stereo"] else "Моно"
    lines = [
        f"<b>{escape_html(item['name'])}</b>",
        "",
        f"Длительность: {format_duration(a['duration'])}",
        f"Громкость: {format_db(a['lufs'])} LUFS / {format_db(a['peak_db'])} dB peak",
        f"{stereo} · {a['sample_rate']} Hz",
        f"ID: <a href=\"{asset_url(item['id'])}\">{item['id']}</a>",
        f"Артист: <a href=\"{artist_url(item['artist'])}\">{escape_html(item['artist'])}</a>",
    ]
    if is_bypassed(a):
        lines.insert(0, "#bypassed")
    return "\n".join(lines)


def process_track(item: dict) -> bool:
    """Обрабатывает трек. Возвращает True, если пост отправлен в канал."""
    log.info("processing %s — %s (%s)", item["artist"], item["name"], item["id"])
    # Сначала только скачиваем и анализируем — этого достаточно, чтобы понять,
    # bypassed трек или нет. Обложку/карточку не трогаем, пока не решили постить.
    ogg = download_audio(item["id"])
    analysis = analyze_and_encode(ogg)

    if ONLY_BYPASSED and not is_bypassed(analysis):
        log.info(
            "skipped %s — not bypassed (%.1f LUFS / %.1f dB peak)",
            item["id"], analysis["lufs"], analysis["peak_db"],
        )
        return False

    cover = fetch_thumbnail(item["id"])
    card = render_card(item["name"], item["artist"], cover, analysis["waveform"])
    caption = build_caption(item, analysis)
    photo_message_id = send_photo(card, caption)

    send_audio(analysis["mp3"], item["name"], item["artist"], cover, photo_message_id)
    log.info("posted %s%s", item["id"], " [bypassed]" if is_bypassed(analysis) else "")
    return True


def poll_once(conn: sqlite3.Connection):
    """Быстрая проверка: находит новые треки и ставит их в очередь (FIFO)."""
    ids = fetch_latest_ids(50)
    if not ids:
        return

    first_run = is_first_run(conn)
    new_ids = [i for i in ids if not already_posted(conn, i) and not in_queue(conn, i)]
    if not new_ids:
        return

    details = {d["id"]: d for d in fetch_details(new_ids)}

    if first_run:
        # Первый запуск: только запоминаем текущие треки, без спама в канал
        log.info("first run — seeding %d assets without posting", len(new_ids))
        for i in new_ids:
            d = details.get(i, {})
            mark_posted(conn, i, d.get("name", ""), d.get("artist", ""), d.get("created_utc", ""), seeded=True)
        return

    # В очередь от старых к новым — постятся в хронологическом порядке
    for i in reversed(new_ids):
        d = details.get(i)
        if not d:
            mark_posted(conn, i, "", "", "", seeded=False)
            continue
        enqueue(conn, d)
        log.info("queued %s — %s (%s), queue size: %d", d["artist"], d["name"], i, queue_size(conn))


def poller_loop():
    """Фоновый поток: непрерывно проверяет новинки (CHECK_INTERVAL=0 —
    запрос за запросом без пауз). Работает независимо от воркера — очередь
    наполняется даже пока воркер занят обработкой тяжёлого трека.

    Если Roblox отвечает 429 (слишком часто), выжидаем ровно Retry-After
    из ответа сервера и продолжаем — так бот никогда не попадёт в бан,
    сохраняя максимально возможную частоту проверок."""
    conn = db_connect()  # своё соединение для этого потока
    while True:
        delay = CHECK_INTERVAL
        try:
            poll_once(conn)
        except requests.HTTPError as e:
            status = e.response.status_code if e.response is not None else 0
            if status == 429:
                retry_after = (e.response.headers.get("Retry-After") or "").strip()
                delay = float(retry_after) if retry_after.replace(".", "", 1).isdigit() else 5.0
                delay = min(max(delay, 1.0), 60.0)
                log.warning("Roblox rate limit (429) — waiting %.0fs", delay)
            else:
                log.warning("poll failed with HTTP %s — waiting 3s", status)
                delay = max(delay, 3.0)
        except Exception:
            log.exception("poll failed — waiting 3s")
            delay = max(delay, 3.0)
        if delay > 0:
            time.sleep(delay)


def worker_loop():
    """Основной поток: разбирает очередь по одному треку (FIFO)."""
    conn = db_connect()
    while True:
        item = queue_next(conn)
        if item is None:
            time.sleep(1)
            continue

        i = item["id"]
        # Слишком много неудач (в т.ч. жёстких крашей) — пропускаем навсегда
        if get_attempts(conn, i) >= MAX_ATTEMPTS:
            log.warning("skipping %s after %d failed attempts", i, MAX_ATTEMPTS)
            mark_posted(conn, i, item["name"], item["artist"], item["created_utc"], seeded=False)
            dequeue(conn, i)
            continue

        # Счётчик увеличиваем и коммитим ДО обработки: если процесс жёстко
        # убьют посреди работы, после рестарта попытка уже учтена.
        bump_attempt(conn, i)
        try:
            posted = process_track(item)
        except Exception:
            log.exception(
                "failed to process %s — retry later (attempt %d/%d)",
                i, get_attempts(conn, i), MAX_ATTEMPTS,
            )
            requeue_to_back(conn, item)  # в конец очереди, не блокируем остальных
            time.sleep(3)
            continue

        mark_posted(conn, i, item["name"], item["artist"], item["created_utc"], seeded=False)
        dequeue(conn, i)
        if posted:
            time.sleep(3)  # не упираемся в rate limit телеграма (только после реального поста)


def main():
    if not BOT_TOKEN or not CHANNEL_ID:
        print("Ошибка: задай переменные окружения TELEGRAM_BOT_TOKEN и TELEGRAM_CHANNEL_ID")
        sys.exit(1)

    ensure_fonts()
    db_connect()  # создаём таблицы до старта потоков
    log.info(
        "started: %s polling, posting %s (bypass at >%s LUFS or >%s dB peak)",
        "continuous" if CHECK_INTERVAL <= 0 else f"every {CHECK_INTERVAL:g}s",
        "ONLY bypassed tracks" if ONLY_BYPASSED else "all tracks",
        BYPASS_LUFS, BYPASS_PEAK_DB,
    )

    threading.Thread(target=poller_loop, daemon=True, name="poller").start()
    worker_loop()


if __name__ == "__main__":
    main()
