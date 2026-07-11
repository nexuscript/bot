import io
import json
import logging
import math
import os
import shutil
import time
from pathlib import Path
from urllib.parse import quote

import numpy as np
import pyloudnorm as pyln
import requests
from PIL import Image, ImageDraw, ImageFont, ImageOps
from pydub import AudioSegment


def required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"Не задана переменная окружения {name}")
    return value


BOT_TOKEN = required_env("BOT_TOKEN")
CHANNEL_ID = required_env("CHANNEL_ID")
ROBLOSECURITY = required_env("ROBLOSECURITY")
POLL_SECONDS = max(30, int(os.environ.get("POLL_SECONDS", "300")))
SEND_ON_START = os.environ.get("SEND_ON_START", "1").lower() in {"1", "true", "yes"}
START_LIMIT = max(1, int(os.environ.get("START_LIMIT", "3")))

DISCOVER_URL = (
    "https://apis.roblox.com/toolbox-service/v1/marketplace/3"
    "?limit=30&sortOrder=Desc&sortCategory=CreateTime"
    "&audioDiscoveryCollection=distrokid-hits"
)
DETAILS_BASE = "https://apis.roblox.com/toolbox-service/v1/items/details?assetIds="
THUMB_BASE = "https://thumbnails.roblox.com/v1/assets"
AUDIO_URL = "https://assetdelivery.roblox.com/v1/asset/"

SEEN_FILE = Path(os.environ.get("SEEN_FILE", "seen_ids.json"))
FONT_BOLD = os.environ.get("FONT_BOLD", "fonts/Inter-Bold.ttf")
FONT_REGULAR = os.environ.get("FONT_REGULAR", "fonts/Inter-Regular.ttf")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("distrokid-bot")

session = requests.Session()
session.headers.update(
    {
        "User-Agent": "Mozilla/5.0 (compatible; distrokid-telegram-bot/2.0)",
        "Accept-Language": "en-US,en;q=0.9",
    }
)
session.cookies.set(
    ".ROBLOSECURITY",
    ROBLOSECURITY,
    domain=".roblox.com",
    path="/",
)


def get(url: str, **kwargs) -> requests.Response:
    last_error = None
    for attempt in range(4):
        try:
            response = session.get(url, **kwargs)
            if response.status_code == 429 or response.status_code >= 500:
                wait = int(response.headers.get("Retry-After", 2**attempt))
                time.sleep(min(max(wait, 1), 30))
                continue
            return response
        except requests.RequestException as exc:
            last_error = exc
            time.sleep(2**attempt)
    raise RuntimeError(
        f"Roblox недоступен после повторных попыток: {last_error}"
    )


def fetch_new_asset_ids() -> list[int]:
    response = get(DISCOVER_URL, timeout=30)
    response.raise_for_status()

    ids = []
    for item in response.json().get("data", []):
        asset = item.get("asset") or {}
        asset_id = item.get("id") or item.get("assetId") or asset.get("id")
        if asset_id:
            ids.append(int(asset_id))

    if not ids:
        log.warning("Roblox вернул пустой список DistroKid Hits")
    return ids


def fetch_details(asset_ids: list[int]) -> dict[int, dict]:
    if not asset_ids:
        return {}

    response = get(
        DETAILS_BASE + ",".join(map(str, asset_ids)),
        timeout=30,
    )
    response.raise_for_status()

    result = {}
    for item in response.json().get("data", []):
        asset = item.get("asset") or {}
        asset_id = asset.get("id") or item.get("id")
        if not asset_id:
            continue

        audio = asset.get("audioDetails") or item.get("audioDetails") or {}
        creator = item.get("creator") or {}
        result[int(asset_id)] = {
            "title": (
                audio.get("title")
                or asset.get("name")
                or item.get("name")
                or "Unknown"
            ),
            "artist": audio.get("artist") or creator.get("name") or "Unknown",
        }
    return result


def fetch_cover(asset_id: int) -> bytes | None:
    try:
        response = get(
            THUMB_BASE,
            params={
                "assetIds": asset_id,
                "size": "420x420",
                "format": "Png",
                "isCircular": "false",
            },
            timeout=30,
        )
        response.raise_for_status()
        data = response.json().get("data", [])
        image_url = data[0].get("imageUrl") if data else None
        if not image_url:
            return None

        image_response = get(image_url, timeout=30)
        image_response.raise_for_status()
        return image_response.content
    except Exception as exc:
        log.warning("Не удалось скачать обложку %s: %s", asset_id, exc)
        return None


def fetch_audio(asset_id: int) -> bytes | None:
    try:
        response = get(
            AUDIO_URL,
            params={"id": asset_id},
            headers={"Accept": "application/octet-stream"},
            timeout=90,
            allow_redirects=True,
        )

        if response.status_code in (401, 403):
            raise RuntimeError(
                "Roblox отклонил ROBLOSECURITY. Обнови cookie целиком, "
                "включая начало _|WARNING."
            )
        response.raise_for_status()

        content_type = response.headers.get("Content-Type", "").lower()
        if "json" in content_type:
            payload = response.json()
            location = payload.get("location") or payload.get("Location")
            if not location:
                raise RuntimeError(
                    f"Asset Delivery не вернул ссылку: {payload}"
                )
            response = get(location, timeout=90, allow_redirects=True)
            response.raise_for_status()

        if len(response.content) < 512:
            raise RuntimeError(
                f"Ответ с аудио слишком маленький: {len(response.content)} байт"
            )
        return response.content
    except Exception as exc:
        log.error("Не удалось скачать аудио %s: %s", asset_id, exc)
        return None


def analyze(audio_bytes: bytes) -> dict:
    segment = AudioSegment.from_file(io.BytesIO(audio_bytes))
    samples = np.array(segment.get_array_of_samples()).astype(np.float32)
    samples /= float(1 << (8 * segment.sample_width - 1))

    if segment.channels > 1:
        samples = samples.reshape((-1, segment.channels))
        mono = samples.mean(axis=1)
    else:
        mono = samples

    try:
        meter_input = samples if segment.channels > 1 else mono
        loudness = pyln.Meter(segment.frame_rate).integrated_loudness(
            meter_input
        )
    except Exception:
        loudness = float("nan")

    return {
        "duration": segment.duration_seconds,
        "lufs": loudness,
        "peak_db": segment.max_dBFS,
        "channels": segment.channels,
        "rate": segment.frame_rate,
        "mono": mono,
    }


def load_font(path: str, size: int):
    try:
        return ImageFont.truetype(path, size)
    except OSError:
        return ImageFont.load_default()


def fit_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    selected_font,
    max_width: int,
) -> str:
    if draw.textlength(text, font=selected_font) <= max_width:
        return text

    while text and draw.textlength(
        text + "…",
        font=selected_font,
    ) > max_width:
        text = text[:-1]
    return text + "…"


def render_card(
    cover: bytes | None,
    title: str,
    artist: str,
    mono: np.ndarray,
) -> bytes:
    width, height = 1200, 675
    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)

    cover_x, cover_y, cover_size = 60, 60, 300
    if cover:
        cover_image = Image.open(io.BytesIO(cover)).convert("L").convert("RGB")
        cover_image = ImageOps.fit(cover_image, (cover_size, cover_size))
        image.paste(cover_image, (cover_x, cover_y))

    draw.rectangle(
        [
            cover_x,
            cover_y,
            cover_x + cover_size,
            cover_y + cover_size,
        ],
        outline="black",
        width=2,
    )

    title_font = load_font(FONT_BOLD, 54)
    artist_font = load_font(FONT_REGULAR, 36)
    text_x = cover_x + cover_size + 50

    draw.text(
        (text_x, 130),
        fit_text(draw, title, title_font, width - text_x - 60),
        font=title_font,
        fill="black",
    )
    draw.text(
        (text_x, 210),
        fit_text(draw, artist, artist_font, width - text_x - 60),
        font=artist_font,
        fill=(110, 110, 110),
    )

    bars = 140
    gap = 3
    waveform_x = 60
    waveform_width = width - 120
    waveform_middle = 520
    waveform_amplitude = 90
    bar_width = (waveform_width - gap * (bars - 1)) / bars

    chunks = np.array_split(np.abs(mono), bars)
    peaks = np.array(
        [chunk.max() if len(chunk) else 0.0 for chunk in chunks]
    )
    if len(peaks) and peaks.max() > 0:
        peaks /= peaks.max()

    for index, peak in enumerate(peaks):
        bar_height = max(3, int(peak * waveform_amplitude))
        x = waveform_x + index * (bar_width + gap)
        draw.rectangle(
            [
                x,
                waveform_middle - bar_height,
                x + bar_width,
                waveform_middle + bar_height,
            ],
            fill="black",
        )

    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


def build_caption(info: dict, asset_id: int, artist: str) -> str:
    minutes, seconds = divmod(round(info["duration"]), 60)
    channels = "Stereo" if info["channels"] > 1 else "Mono"
    lufs = (
        f'{info["lufs"]:.1f} LUFS'
        if math.isfinite(info["lufs"])
        else "— LUFS"
    )
    peak = (
        f'{info["peak_db"]:.1f}'
        if math.isfinite(info["peak_db"])
        else "—"
    )
    rate = info["rate"]
    artist_url = (
        "https://create.roblox.com/store/audio?artistName="
        + quote(artist)
        + "&sortCategory=CreateTime"
    )
    asset_url = "https://create.roblox.com/store/asset/" + str(asset_id)

    lines = [
        f"{minutes}:{seconds:02d} · {lufs} / {peak} dB peak · "
        f"{channels} · {rate} Hz",
        f'ID: <a href="{asset_url}">{asset_id}</a>',
        f'Артист: <a href="{artist_url}">{artist}</a>',
    ]
    return chr(10).join(lines)


def send_photo(photo: bytes, caption: str) -> None:
    response = requests.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto",
        data={
            "chat_id": CHANNEL_ID,
            "caption": caption,
            "parse_mode": "HTML",
        },
        files={"photo": ("card.png", photo, "image/png")},
        timeout=90,
    )
    if not response.ok:
        raise RuntimeError(
            f"Telegram {response.status_code}: {response.text[:500]}"
        )


def load_seen() -> set[int]:
    try:
        if SEEN_FILE.exists():
            saved_ids = json.loads(SEEN_FILE.read_text(encoding="utf-8"))
            return {int(item) for item in saved_ids}
    except Exception as exc:
        log.warning("Не удалось прочитать seen-файл: %s", exc)
    return set()


def save_seen(seen: set[int]) -> None:
    SEEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    temporary_file = SEEN_FILE.with_suffix(".tmp")
    temporary_file.write_text(
        json.dumps(sorted(seen)),
        encoding="utf-8",
    )
    temporary_file.replace(SEEN_FILE)


def process(asset_id: int, metadata: dict) -> bool:
    audio = fetch_audio(asset_id)
    if not audio:
        return False

    info = analyze(audio)
    card = render_card(
        fetch_cover(asset_id),
        metadata["title"],
        metadata["artist"],
        info["mono"],
    )
    caption = build_caption(info, asset_id, metadata["artist"])
    send_photo(card, caption)
    log.info(
        "Отправлено: %s — %s (%s)",
        metadata["artist"],
        metadata["title"],
        asset_id,
    )
    return True


def check_startup() -> None:
    if not shutil.which("ffmpeg") and not shutil.which("avconv"):
        raise RuntimeError(
            "На хостинге не установлен FFmpeg. Добавь системный пакет ffmpeg."
        )

    response = requests.get(
        f"https://api.telegram.org/bot{BOT_TOKEN}/getMe",
        timeout=30,
    )
    response.raise_for_status()
    if not response.json().get("ok"):
        raise RuntimeError("BOT_TOKEN неверный")
    log.info("Проверка запуска пройдена")


def main() -> None:
    check_startup()
    seen = load_seen()
    first_run = not seen

    while True:
        try:
            ids = fetch_new_asset_ids()
            new_ids = [asset_id for asset_id in ids if asset_id not in seen]

            if first_run and not SEND_ON_START:
                seen.update(new_ids)
                save_seen(seen)
                log.info(
                    "Первый запуск: запомнил %d аудио без отправки",
                    len(new_ids),
                )
            else:
                if first_run:
                    new_ids = new_ids[:START_LIMIT]

                details = fetch_details(new_ids)
                for asset_id in reversed(new_ids):
                    metadata = details.get(
                        asset_id,
                        {"title": "Unknown", "artist": "Unknown"},
                    )
                    try:
                        if process(asset_id, metadata):
                            seen.add(asset_id)
                            save_seen(seen)
                            time.sleep(3)
                    except Exception:
                        log.exception(
                            "Ошибка обработки %s, попробую снова в следующем цикле",
                            asset_id,
                        )

            first_run = False
        except Exception:
            log.exception("Ошибка цикла")

        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
            allow_redirects=True,
        )
        if r.status_code in (401, 403):
            raise RuntimeError(
                "Roblox отклонил ROBLOSECURITY. Обнови cookie целиком: с предупреждением _|WARNING..."
            )
        r.raise_for_status()

        content_type = r.headers.get("Content-Type", "").lower()
        if "json" in content_type:
            payload = r.json()
            location = payload.get("location") or payload.get("Location")
            if not location:
                raise RuntimeError(f"Asset Delivery не вернул ссылку: {payload}")
            r = get(location, timeout=90, allow_redirects=True)
            r.raise_for_status()

        if len(r.content) < 512:
            raise RuntimeError(f"Ответ слишком маленький ({len(r.content)} байт)")
        return r.content
    except Exception as exc:
        log.error("Не удалось скачать аудио %s: %s", asset_id, exc)
        return None


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
        lufs = pyln.Meter(seg.frame_rate).integrated_loudness(arr if seg.channels > 1 else mono)
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


def font(path: str, size: int):
    try:
        return ImageFont.truetype(path, size)
    except OSError:
        return ImageFont.load_default()


def fit_text(draw: ImageDraw.ImageDraw, text: str, selected_font, max_w: int) -> str:
    if draw.textlength(text, font=selected_font) <= max_w:
        return text
    while text and draw.textlength(text + "…", font=selected_font) > max_w:
        text = text[:-1]
    return text + "…"


def render_card(cover: bytes | None, title: str, artist: str, mono: np.ndarray) -> bytes:
    width, height = 1200, 675
    img = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(img)
    cx, cy, csize = 60, 60, 300

    if cover:
        image = Image.open(io.BytesIO(cover)).convert("L").convert("RGB")
        image = ImageOps.fit(image, (csize, csize))
        img.paste(image, (cx, cy))
    draw.rectangle([cx, cy, cx + csize, cy + csize], outline="black", width=2)

    title_font = font(FONT_BOLD, 54)
    artist_font = font(FONT_REGULAR, 36)
    tx = cx + csize + 50
    draw.text((tx, 130), fit_text(draw, title, title_font, width - tx - 60), font=title_font, fill="black")
    draw.text((tx, 210), fit_text(draw, artist, artist_font, width - tx - 60), font=artist_font, fill=(110, 110, 110))

    bars, gap = 140, 3
    wf_x, wf_w, wf_mid, wf_amp = 60, width - 120, 520, 90
    bar_w = (wf_w - gap * (bars - 1)) / bars
    chunks = np.array_split(np.abs(mono), bars)
    peaks = np.array([chunk.max() if len(chunk) else 0.0 for chunk in chunks])
    if len(peaks) and peaks.max() > 0:
        peaks /= peaks.max()
    for i, peak in enumerate(peaks):
        h = max(3, int(peak * wf_amp))
        x = wf_x + i * (bar_w + gap)
        draw.rectangle([x, wf_mid - h, x + bar_w, wf_mid + h], fill="black")

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def build_caption(info: dict, asset_id: int, artist: str) -> str:
    minutes, seconds = divmod(round(info["duration"]), 60)
    channels = "Stereo" if info["channels"] > 1 else "Mono"
    lufs = f'{info["lufs"]:.1f} LUFS' if math.isfinite(info["lufs"]) else "— LUFS"
    peak = f'{info["peak_db"]:.1f}' if math.isfinite(info["peak_db"]) else "—"
    artist_url = "https://create.roblox.com/store/audio?artistName=" + quote(artist) + "&sortCategory=CreateTime"
    asset_url = "https://create.roblox.com/store/asset/" + str(asset_id)
    return (
        f"{minutes}:{seconds:02d} · {lufs} / {peak} dB peak · {channels} · {info['rate']} Hz
"
        f'ID: <a href="{asset_url}">{asset_id}</a>
'
        f'Артист: <a href="{artist_url}">{artist}</a>'
    )


def send_photo(photo: bytes, caption: str) -> None:
    r = requests.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto",
        data={"chat_id": CHANNEL_ID, "caption": caption, "parse_mode": "HTML"},
        files={"photo": ("card.png", photo, "image/png")},
        timeout=90,
    )
    if not r.ok:
        raise RuntimeError(f"Telegram {r.status_code}: {r.text[:500]}")


def load_seen() -> set[int]:
    try:
        if SEEN_FILE.exists():
            return {int(x) for x in json.loads(SEEN_FILE.read_text(encoding="utf-8"))}
    except Exception as exc:
        log.warning("Не удалось прочитать seen-файл: %s", exc)
    return set()


def save_seen(seen: set[int]) -> None:
    SEEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = SEEN_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(sorted(seen)), encoding="utf-8")
    tmp.replace(SEEN_FILE)


def process(asset_id: int, meta: dict) -> bool:
    audio = fetch_audio(asset_id)
    if not audio:
        return False
    info = analyze(audio)
    card = render_card(fetch_cover(asset_id), meta["title"], meta["artist"], info["mono"])
    send_photo(card, build_caption(info, asset_id, meta["artist"]))
    log.info("Отправлено: %s — %s (%s)", meta["artist"], meta["title"], asset_id)
    return True


def check_startup() -> None:
    if not shutil.which("ffmpeg") and not shutil.which("avconv"):
        raise RuntimeError("На хостинге не установлен FFmpeg. Добавь системный пакет ffmpeg.")
    me = requests.get(f"https://api.telegram.org/bot{BOT_TOKEN}/getMe", timeout=30)
    me.raise_for_status()
    if not me.json().get("ok"):
        raise RuntimeError("BOT_TOKEN неверный")
    log.info("Проверка запуска пройдена")


def main() -> None:
    check_startup()
    seen = load_seen()
    first_run = not seen

    while True:
        try:
            ids = fetch_new_asset_ids()
            new = [asset_id for asset_id in ids if asset_id not in seen]

            if first_run and not SEND_ON_START:
                seen.update(new)
                save_seen(seen)
                log.info("Первый запуск: запомнил %d аудио без отправки", len(new))
            else:
                if first_run:
                    new = new[:START_LIMIT]
                details = fetch_details(new)
                for asset_id in reversed(new):
                    meta = details.get(asset_id, {"title": "Unknown", "artist": "Unknown"})
                    try:
                        if process(asset_id, meta):
                            seen.add(asset_id)
                            save_seen(seen)
                            time.sleep(3)
                    except Exception:
                        log.exception("Ошибка обработки %s, попробую снова в следующем цикле", asset_id)

            first_run = False
        except Exception:
            log.exception("Ошибка цикла")

        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
