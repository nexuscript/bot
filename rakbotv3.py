#!/usr/bin/env python3
"""
rbx_bot.py - Telegram-бот для Roblox (v3).
pyTeleBot (telebot). БЕЗ aiogram.

Фиксы v3:
  - ПРОКСИ для России (SOCKS5 / HTTP) через PROXY_URL
  - Друзья отображаются корректно (кэш + правильные поля)
  - Инлайн-кнопки на фото: удаляется фото, шлётся новое сообщение
  - Кэш друзей, групп, значков (не запрашивает повторно при пагинации)
  - Поиск игр работает (правильный API)
  - Игра по placeId (автоконверсия placeId -> universeId)
  - Значки без HTTP 400
  - Скачивание ассетов (Shirt, Pants, T-Shirt, Decal, Audio, Model)

Установка:
    pip install pyTelegramBotAPI requests[socks]

Запуск:
    export BOT_TOKEN="ваш-токен-от-BotFather"
    export PROXY_URL="socks5://user:pass@ip:port"   # если сервер в РФ
    python3 rbx_bot.py
"""

import telebot
from telebot import types
import requests
import re
import os
import io
import sys
import time
import json
import logging
import threading
from datetime import datetime, timezone
from typing import Optional

# ─────────────────────────────────────────────
# ЛОГИРОВАНИЕ
# ─────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("rbx_bot")

# ─────────────────────────────────────────────
# КОНФИГУРАЦИЯ
# ─────────────────────────────────────────────
TOKEN = os.getenv("BOT_TOKEN", "").strip()
if not TOKEN:
    try:
        from dotenv import load_dotenv
        load_dotenv()
        TOKEN = os.getenv("BOT_TOKEN", "").strip()
    except ImportError:
        pass

if not TOKEN:
    log.critical(
        "\n" + "=" * 50 +
        "\n  BOT_TOKEN не найден!" +
        '\n  export BOT_TOKEN="ваш-токен"' +
        "\n  python3 rbx_bot.py" +
        "\n" + "=" * 50
    )
    sys.exit(1)

# Прокси для обхода блокировки Roblox API в России
PROXY_URL = os.getenv("PROXY_URL", "").strip()

COOLDOWN_SEC = int(os.getenv("COOLDOWN_SEC", "2"))
REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "15"))
PAGE_SIZE = 10
CACHE_TTL = 120  # секунд

# ─────────────────────────────────────────────
# БОТ
# ─────────────────────────────────────────────
bot = telebot.TeleBot(TOKEN, threaded=True, num_threads=4)

_cooldowns: dict[int, float] = {}
_cd_lock = threading.Lock()


def check_cooldown(uid: int) -> Optional[int]:
    with _cd_lock:
        last = _cooldowns.get(uid, 0)
        elapsed = time.time() - last
        if elapsed < COOLDOWN_SEC:
            return int(COOLDOWN_SEC - elapsed) + 1
        _cooldowns[uid] = time.time()
        return None


def private_only(msg) -> bool:
    return msg.chat.type == "private" if hasattr(msg, "chat") else True


# ─────────────────────────────────────────────
# HTTP-СЕССИЯ С ПРОКСИ
# ─────────────────────────────────────────────
S = requests.Session()
S.headers.update({
    "Accept": "application/json",
    "User-Agent": "RBXInfoBot/3.0",
})

if PROXY_URL:
    S.proxies = {
        "http": PROXY_URL,
        "https": PROXY_URL,
    }
    log.info("Прокси включен: %s", PROXY_URL.split("@")[-1] if "@" in PROXY_URL else PROXY_URL)
else:
    log.info("Прокси не задан (PROXY_URL пуст)")


class RBXError(Exception):
    pass


def api_get(url, params=None):
    try:
        r = S.get(url, params=params, timeout=REQUEST_TIMEOUT)
        if r.status_code == 404:
            raise RBXError("Не найдено (404)")
        if r.status_code == 429:
            raise RBXError("Слишком много запросов, подожди")
        if r.status_code != 200:
            raise RBXError(f"HTTP {r.status_code}")
        return r.json()
    except requests.exceptions.ProxyError as e:
        raise RBXError(f"Ошибка прокси: {e}")
    except requests.exceptions.Timeout:
        raise RBXError("Таймаут запроса. Если сервер в РФ — задай PROXY_URL")
    except requests.RequestException as e:
        raise RBXError(f"Ошибка сети: {e}")


def api_post(url, data):
    try:
        r = S.post(url, json=data, timeout=REQUEST_TIMEOUT)
        if r.status_code == 429:
            raise RBXError("Слишком много запросов, подожди")
        if r.status_code not in (200, 201):
            raise RBXError(f"HTTP {r.status_code}")
        return r.json()
    except requests.exceptions.ProxyError as e:
        raise RBXError(f"Ошибка прокси: {e}")
    except requests.exceptions.Timeout:
        raise RBXError("Таймаут запроса. Если сервер в РФ — задай PROXY_URL")
    except requests.RequestException as e:
        raise RBXError(f"Ошибка сети: {e}")


# ─────────────────────────────────────────────
# КЭШ
# ─────────────────────────────────────────────
class SimpleCache:
    """Простой кэш с TTL."""
    def __init__(self, ttl: int = CACHE_TTL):
        self._data: dict = {}
        self._times: dict = {}
        self._ttl = ttl
        self._lock = threading.Lock()

    def get(self, key):
        with self._lock:
            if key in self._data:
                if time.time() - self._times[key] < self._ttl:
                    return self._data[key]
                else:
                    del self._data[key]
                    del self._times[key]
        return None

    def set(self, key, value):
        with self._lock:
            self._data[key] = value
            self._times[key] = time.time()


_friends_cache = SimpleCache(ttl=CACHE_TTL)
_groups_cache = SimpleCache(ttl=CACHE_TTL)
_badges_cache = SimpleCache(ttl=CACHE_TTL)


# ─────────────────────────────────────────────
# ROBLOX API: Пользователи
# ─────────────────────────────────────────────
def resolve_username(username: str) -> dict:
    data = api_post(
        "https://users.roblox.com/v1/usernames/users",
        {"usernames": [username], "excludeBannedUsers": False},
    )
    users = data.get("data", [])
    if not users:
        raise RBXError(f"Игрок '{username}' не найден")
    return users[0]


def get_user_info(user_id: int) -> dict:
    return api_get(f"https://users.roblox.com/v1/users/{user_id}")


def get_user_by_input(text: str) -> dict:
    text = text.strip().lstrip("@")
    if text.isdigit():
        return get_user_info(int(text))
    resolved = resolve_username(text)
    return get_user_info(resolved["id"])


def search_users(keyword: str, limit: int = 10) -> list:
    data = api_get(
        "https://users.roblox.com/v1/users/search",
        {"keyword": keyword, "limit": limit},
    )
    return data.get("data", [])


# ─── Аватары ───
def get_avatar_url(user_id: int, size="420x420") -> Optional[str]:
    try:
        data = api_get(
            "https://thumbnails.roblox.com/v1/users/avatar",
            {"userIds": user_id, "size": size, "format": "Png", "isCircular": "false"},
        )
        items = data.get("data", [])
        if items and items[0].get("imageUrl"):
            return items[0]["imageUrl"]
    except Exception:
        pass
    return None


def get_headshot_url(user_id: int, size="420x420") -> Optional[str]:
    try:
        data = api_get(
            "https://thumbnails.roblox.com/v1/users/avatar-headshot",
            {"userIds": user_id, "size": size, "format": "Png", "isCircular": "false"},
        )
        items = data.get("data", [])
        if items and items[0].get("imageUrl"):
            return items[0]["imageUrl"]
    except Exception:
        pass
    return None


# ─── Друзья / Подписчики ───
def get_friends_count(user_id: int) -> int:
    try:
        return api_get(
            f"https://friends.roblox.com/v1/users/{user_id}/friends/count"
        ).get("count", 0)
    except Exception:
        return 0


def get_followers_count(user_id: int) -> int:
    try:
        return api_get(
            f"https://friends.roblox.com/v1/users/{user_id}/followers/count"
        ).get("count", 0)
    except Exception:
        return 0


def get_followings_count(user_id: int) -> int:
    try:
        return api_get(
            f"https://friends.roblox.com/v1/users/{user_id}/followings/count"
        ).get("count", 0)
    except Exception:
        return 0


def get_friends_list(user_id: int) -> list:
    """Получить друзей с кэшем."""
    cached = _friends_cache.get(user_id)
    if cached is not None:
        return cached
    data = api_get(f"https://friends.roblox.com/v1/users/{user_id}/friends")
    friends = data.get("data", [])
    _friends_cache.set(user_id, friends)
    return friends


# ─── Группы ───
def get_user_groups(user_id: int) -> list:
    """Получить группы с кэшем."""
    cached = _groups_cache.get(user_id)
    if cached is not None:
        return cached
    data = api_get(f"https://groups.roblox.com/v1/users/{user_id}/groups/roles")
    groups = data.get("data", [])
    _groups_cache.set(user_id, groups)
    return groups


def get_group_info(group_id: int) -> dict:
    return api_get(f"https://groups.roblox.com/v1/groups/{group_id}")


# ─── Значки ───
def get_user_badges(user_id: int) -> list:
    """Получить значки с кэшем."""
    cached = _badges_cache.get(user_id)
    if cached is not None:
        return cached
    all_badges = []
    cursor = ""
    for _ in range(5):
        params = {"limit": 25, "sortOrder": "Desc"}
        if cursor:
            params["cursor"] = cursor
        data = api_get(
            f"https://badges.roblox.com/v1/users/{user_id}/badges", params
        )
        all_badges.extend(data.get("data", []))
        cursor = data.get("nextPageCursor")
        if not cursor:
            break
    _badges_cache.set(user_id, all_badges)
    return all_badges


# ─── Онлайн-статус ───
def get_presence(user_ids: list) -> list:
    data = api_post(
        "https://presence.roblox.com/v1/presence/users",
        {"userIds": user_ids},
    )
    return data.get("userPresences", [])


# ─── Игры ───
def place_to_universe(place_id: int) -> int:
    data = api_get(
        f"https://apis.roblox.com/universes/v1/places/{place_id}/universe"
    )
    uid = data.get("universeId")
    if not uid:
        raise RBXError("Не удалось найти universeId для этого placeId")
    return uid


def get_game_info(universe_id: int) -> dict:
    data = api_get(
        "https://games.roblox.com/v1/games",
        {"universeIds": str(universe_id)},
    )
    games = data.get("data", [])
    if not games:
        raise RBXError("Игра не найдена")
    return games[0]


def get_game_thumbnail(universe_id: int) -> Optional[str]:
    try:
        data = api_get(
            "https://thumbnails.roblox.com/v1/games/icons",
            {
                "universeIds": str(universe_id),
                "size": "512x512",
                "format": "Png",
                "isCircular": "false",
            },
        )
        items = data.get("data", [])
        if items and items[0].get("imageUrl"):
            return items[0]["imageUrl"]
    except Exception:
        pass
    return None


def search_games(keyword: str, limit: int = 10) -> list:
    """Поиск игр через несколько API с fallback."""
    # Способ 1: games.roblox.com/v1/games/list
    try:
        data = api_get(
            "https://games.roblox.com/v1/games/list",
            {
                "model.keyword": keyword,
                "model.startRows": 0,
                "model.maxRows": limit,
            },
        )
        games = data.get("games", [])
        if games:
            return games
    except Exception:
        pass

    # Способ 2: omni-search
    try:
        r = S.get(
            "https://apis.roblox.com/search-api/omni-search",
            params={
                "searchQuery": keyword,
                "searchType": "games",
                "pageToken": "",
                "sessionId": "",
            },
            timeout=REQUEST_TIMEOUT,
        )
        if r.status_code == 200:
            result = r.json()
            items = []
            for entry in result.get("searchResults", []):
                for item in entry.get("searchResultItems", []):
                    items.append(item)
            if items:
                return items[:limit]
    except Exception:
        pass

    return []


# ─── Ассеты ───
def get_asset_info(asset_id: int) -> dict:
    return api_get(f"https://economy.roblox.com/v2/assets/{asset_id}/details")


def get_asset_thumbnail(asset_id: int) -> Optional[str]:
    try:
        data = api_get(
            "https://thumbnails.roblox.com/v1/assets",
            {"assetIds": str(asset_id), "size": "420x420", "format": "Png"},
        )
        items = data.get("data", [])
        if items and items[0].get("imageUrl"):
            return items[0]["imageUrl"]
    except Exception:
        pass
    return None


def download_asset(asset_id: int) -> Optional[bytes]:
    urls_to_try = [
        f"https://assetdelivery.roblox.com/v1/asset/?id={asset_id}",
        f"https://assetdelivery.roblox.com/v1/assetId/{asset_id}",
    ]
    for url in urls_to_try:
        try:
            r = S.get(url, timeout=30, allow_redirects=True)
            if r.status_code == 200 and len(r.content) > 0:
                return r.content
        except Exception:
            continue
    return None


ASSET_TYPES = {
    1: "Image", 2: "T-Shirt", 3: "Audio", 4: "Mesh",
    5: "Lua", 8: "Hat", 9: "Place", 10: "Model",
    11: "Shirt", 12: "Pants", 13: "Decal", 17: "Head",
    18: "Face", 19: "Gear", 21: "Badge", 24: "Animation",
    27: "Torso", 28: "Right Arm", 29: "Left Arm",
    30: "Left Leg", 31: "Right Leg", 32: "Package",
    34: "GamePass", 38: "Plugin", 40: "MeshPart",
    41: "Hair Accessory", 42: "Face Accessory",
    43: "Neck Accessory", 44: "Shoulder Accessory",
    45: "Front Accessory", 46: "Back Accessory",
    47: "Waist Accessory",
}

DOWNLOADABLE = {1, 2, 3, 4, 10, 11, 12, 13, 24, 38, 40}

ASSET_EXT = {
    1: ".png", 2: ".png", 3: ".ogg", 4: ".mesh",
    10: ".rbxm", 11: ".png", 12: ".png", 13: ".png",
    24: ".rbxm", 38: ".rbxm", 40: ".rbxm",
}


# ─────────────────────────────────────────────
# ФОРМАТИРОВАНИЕ
# ─────────────────────────────────────────────
def fmt_num(n) -> str:
    if n is None:
        return "0"
    return f"{int(n):,}".replace(",", " ")


def fmt_date(iso_str: str) -> str:
    if not iso_str:
        return "—"
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        return dt.strftime("%d.%m.%Y %H:%M")
    except Exception:
        return str(iso_str)[:16]


def fmt_ago(iso_str: str) -> str:
    if not iso_str:
        return ""
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        delta = datetime.now(timezone.utc) - dt
        d = delta.days
        if d == 0:
            h = delta.seconds // 3600
            return "только что" if h == 0 else f"{h} ч. назад"
        if d < 30:
            return f"{d} дн. назад"
        if d < 365:
            return f"{d // 30} мес. назад"
        return f"{d // 365} г. назад"
    except Exception:
        return ""


PRESENCE_MAP = {
    0: "⚫ Оффлайн",
    1: "🟢 На сайте",
    2: "🎮 В игре",
    3: "🔨 В студии",
}


def fmt_presence(p: dict) -> str:
    status = PRESENCE_MAP.get(p.get("userPresenceType", 0), "⚫ Неизвестно")
    game = p.get("lastLocation", "")
    if game and p.get("userPresenceType") == 2:
        status += f"\n   └ 🕹 {game}"
    return status


def safe_html(text: str) -> str:
    if not text:
        return ""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# ─────────────────────────────────────────────
# ПАГИНАЦИЯ
# ─────────────────────────────────────────────
def make_page_keyboard(prefix: str, user_id: int, page: int, total_pages: int):
    kb = types.InlineKeyboardMarkup(row_width=3)
    buttons = []
    if page > 0:
        buttons.append(
            types.InlineKeyboardButton(
                "◀", callback_data=f"{prefix}:{user_id}:{page - 1}"
            )
        )
    else:
        buttons.append(types.InlineKeyboardButton("·", callback_data="noop"))

    buttons.append(
        types.InlineKeyboardButton(
            f"{page + 1}/{total_pages}", callback_data="noop"
        )
    )

    if page < total_pages - 1:
        buttons.append(
            types.InlineKeyboardButton(
                "▶", callback_data=f"{prefix}:{user_id}:{page + 1}"
            )
        )
    else:
        buttons.append(types.InlineKeyboardButton("·", callback_data="noop"))

    kb.row(*buttons)
    return kb


def paginate(items: list, page: int, per_page: int = PAGE_SIZE):
    total = max(1, (len(items) + per_page - 1) // per_page)
    page = max(0, min(page, total - 1))
    start = page * per_page
    return items[start : start + per_page], page, total


# ─────────────────────────────────────────────
# ПОСТРОЕНИЕ ТЕКСТА СТРАНИЦ
# ─────────────────────────────────────────────
def build_friends_text(user_id: int, page: int):
    friends = get_friends_list(user_id)
    if not friends:
        return "👤 Нет друзей", None

    page_items, page, total_pages = paginate(friends, page)
    total_count = len(friends)

    lines = [
        f"👥 <b>Друзья</b> (всего {fmt_num(total_count)}), "
        f"стр. {page + 1}/{total_pages}:\n"
    ]

    for i, fr in enumerate(page_items, start=page * PAGE_SIZE + 1):
        # API друзей возвращает: id, name, displayName, isOnline, ...
        display = fr.get("displayName") or fr.get("name") or "—"
        uname = fr.get("name") or "—"
        fid = fr.get("id", "?")
        is_online = fr.get("isOnline", False)
        verified = " ✅" if fr.get("hasVerifiedBadge") else ""
        dot = "🟢" if is_online else "⚫"

        lines.append(
            f"  {i}. {dot} <b>{safe_html(display)}</b> "
            f"(<code>{safe_html(uname)}</code>) "
            f"ID:{fid}{verified}"
        )

    kb = make_page_keyboard("friends", user_id, page, total_pages)
    return "\n".join(lines), kb


def build_groups_text(user_id: int, page: int):
    groups = get_user_groups(user_id)
    if not groups:
        return "🏰 Не состоит в группах", None

    page_items, page, total_pages = paginate(groups, page)

    lines = [
        f"🏰 <b>Группы</b> (всего {len(groups)}), "
        f"стр. {page + 1}/{total_pages}:\n"
    ]

    for i, g in enumerate(page_items, start=page * PAGE_SIZE + 1):
        group = g.get("group", {})
        role = g.get("role", {})
        gname = safe_html(group.get("name", "?"))
        rname = safe_html(role.get("name", "?"))
        members = fmt_num(group.get("memberCount", 0))
        gid = group.get("id", "?")
        lines.append(
            f"  {i}. <b>{gname}</b>\n"
            f"      └ 👑 {rname} | 👤 {members} | ID: <code>{gid}</code>"
        )

    kb = make_page_keyboard("groups", user_id, page, total_pages)
    return "\n".join(lines), kb


def build_badges_text(user_id: int, page: int):
    badges = get_user_badges(user_id)
    if not badges:
        return "🏅 Нет значков", None

    page_items, page, total_pages = paginate(badges, page)

    lines = [
        f"🏅 <b>Значки</b> (всего {len(badges)}), "
        f"стр. {page + 1}/{total_pages}:\n"
    ]

    for i, b in enumerate(page_items, start=page * PAGE_SIZE + 1):
        bname = safe_html(b.get("displayName") or b.get("name", "?"))
        bdesc = safe_html((b.get("description") or "—")[:80])
        lines.append(f"  {i}. <b>{bname}</b>\n      └ {bdesc}")

    kb = make_page_keyboard("badges", user_id, page, total_pages)
    return "\n".join(lines), kb


# ─────────────────────────────────────────────
# КОМАНДЫ
# ─────────────────────────────────────────────
HELP_TEXT = (
    "🎮 <b>RBX Info Bot v3</b>\n\n"
    "<b>👤 Игроки:</b>\n"
    "  /profile &lt;ник&gt; — полный профиль\n"
    "  /avatar &lt;ник&gt; — аватар\n"
    "  /headshot &lt;ник&gt; — хедшот\n"
    "  /friends &lt;ник&gt; — друзья (◀▶)\n"
    "  /groups &lt;ник&gt; — группы (◀▶)\n"
    "  /badges &lt;ник&gt; — значки (◀▶)\n"
    "  /online &lt;ник&gt; — статус онлайн\n"
    "  /search &lt;запрос&gt; — поиск игроков\n\n"
    "<b>🎮 Игры:</b>\n"
    "  /game &lt;id&gt; — инфо (placeId или universeId)\n"
    "  /searchgame &lt;название&gt; — поиск игр\n\n"
    "<b>👥 Группы:</b>\n"
    "  /group &lt;id&gt; — информация о группе\n\n"
    "<b>🎒 Ассеты:</b>\n"
    "  /asset &lt;id&gt; — информация об ассете\n"
    "  /download &lt;id&gt; — скачать ассет\n\n"
    "<b>⚙️ Прочее:</b>\n"
    "  /help — это сообщение\n"
    "  /ping — проверка\n"
    "  /proxy — статус прокси\n\n"
    "💡 Можно писать username или userId\n"
    "📥 Скачивание: Shirt, Pants, Decal, Audio, Model\n"
    "🇷🇺 Если сервер в РФ — задай PROXY_URL"
)


@bot.message_handler(commands=["start", "help"], func=private_only)
def cmd_help(m):
    bot.send_message(m.chat.id, HELP_TEXT, parse_mode="HTML")


@bot.message_handler(commands=["ping"], func=private_only)
def cmd_ping(m):
    t = time.time()
    msg = bot.reply_to(m, "🏓 ...")
    ms = int((time.time() - t) * 1000)
    bot.edit_message_text(
        f"🏓 Pong! <b>{ms} мс</b>",
        m.chat.id, msg.message_id, parse_mode="HTML",
    )


@bot.message_handler(commands=["proxy"], func=private_only)
def cmd_proxy(m):
    if PROXY_URL:
        # показываем только хост, не пароль
        display = PROXY_URL.split("@")[-1] if "@" in PROXY_URL else PROXY_URL
        proto = PROXY_URL.split("://")[0] if "://" in PROXY_URL else "?"
        text = (
            f"🔀 <b>Прокси активен</b>\n\n"
            f"├ Протокол: <code>{safe_html(proto)}</code>\n"
            f"└ Сервер: <code>{safe_html(display)}</code>\n\n"
            "✅ Все запросы к Roblox API идут через прокси"
        )
    else:
        text = (
            "🔀 <b>Прокси не задан</b>\n\n"
            "Запросы идут напрямую.\n"
            "Если сервер в РФ — задай переменную:\n"
            '<code>export PROXY_URL="socks5://user:pass@ip:port"</code>'
        )
    bot.send_message(m.chat.id, text, parse_mode="HTML")


# ─────────────────────────────────────────────
# /profile
# ─────────────────────────────────────────────
@bot.message_handler(commands=["profile"], func=private_only)
def cmd_profile(m):
    args = m.text.split(maxsplit=1)
    if len(args) < 2:
        return bot.reply_to(m, "❌ <code>/profile Roblox</code>", parse_mode="HTML")

    cd = check_cooldown(m.from_user.id)
    if cd:
        return bot.reply_to(m, f"⏳ Подожди {cd} сек.")

    w = bot.reply_to(m, "⏳ Загрузка...")

    try:
        user = get_user_by_input(args[1])
        uid = user["id"]
        fc = get_friends_count(uid)
        flc = get_followers_count(uid)
        fic = get_followings_count(uid)
        av = get_avatar_url(uid)

        try:
            pr = get_presence([uid])
            status = fmt_presence(pr[0]) if pr else "⚫ Неизвестно"
        except Exception:
            status = "⚫ Неизвестно"

        dn = safe_html(user.get("displayName", "?"))
        un = safe_html(user.get("name", "?"))
        banned = "🚫 ДА" if user.get("isBanned") else "✅ Нет"
        verified = "✅ Да" if user.get("hasVerifiedBadge") else "❌ Нет"

        text = (
            f"👤 <b>{dn}</b>\n"
            f"├ Username: <code>{un}</code>\n"
            f"├ ID: <code>{uid}</code>\n"
            f"├ Статус: {status}\n"
            f"├ Создан: {fmt_date(user.get('created', ''))}"
            f" ({fmt_ago(user.get('created', ''))})\n"
            f"├ Бан: {banned}\n"
            f"├ Верификация: {verified}\n"
            f"├ 👥 Друзья: <b>{fmt_num(fc)}</b>\n"
            f"├ 👁 Подписчики: <b>{fmt_num(flc)}</b>\n"
            f"└ 📌 Подписки: <b>{fmt_num(fic)}</b>"
        )

        desc = user.get("description", "")
        if desc:
            desc = safe_html(desc[:300])
            if len(user.get("description", "")) > 300:
                desc += "..."
            text += f"\n\n📝 <i>{desc}</i>"

        text += (
            f'\n\n🔗 <a href="https://www.roblox.com/users/{uid}/profile">'
            "Открыть на Roblox</a>"
        )

        # Кнопки быстрого доступа
        kb = types.InlineKeyboardMarkup(row_width=3)
        kb.add(
            types.InlineKeyboardButton(
                "👥 Друзья", callback_data=f"friends:{uid}:0"
            ),
            types.InlineKeyboardButton(
                "🏰 Группы", callback_data=f"groups:{uid}:0"
            ),
            types.InlineKeyboardButton(
                "🏅 Значки", callback_data=f"badges:{uid}:0"
            ),
        )

        if av:
            bot.send_photo(
                m.chat.id, av, caption=text,
                parse_mode="HTML", reply_markup=kb,
            )
        else:
            bot.send_message(
                m.chat.id, text, parse_mode="HTML",
                disable_web_page_preview=True, reply_markup=kb,
            )

        bot.delete_message(m.chat.id, w.message_id)
    except RBXError as e:
        bot.edit_message_text(f"❌ {e}", m.chat.id, w.message_id)
    except Exception as e:
        log.exception("/profile error")
        bot.edit_message_text(f"❌ {e}", m.chat.id, w.message_id)


# ─────────────────────────────────────────────
# /avatar, /headshot
# ─────────────────────────────────────────────
@bot.message_handler(commands=["avatar"], func=private_only)
def cmd_avatar(m):
    args = m.text.split(maxsplit=1)
    if len(args) < 2:
        return bot.reply_to(
            m, "❌ <code>/avatar Roblox</code>", parse_mode="HTML"
        )
    cd = check_cooldown(m.from_user.id)
    if cd:
        return bot.reply_to(m, f"⏳ Подожди {cd} сек.")
    try:
        user = get_user_by_input(args[1])
        url = get_avatar_url(user["id"])
        if url:
            dn = safe_html(user.get("displayName", "?"))
            bot.send_photo(
                m.chat.id, url,
                caption=f"🖼 Аватар <b>{dn}</b>",
                parse_mode="HTML",
            )
        else:
            bot.reply_to(m, "❌ Аватар недоступен")
    except RBXError as e:
        bot.reply_to(m, f"❌ {e}")


@bot.message_handler(commands=["headshot"], func=private_only)
def cmd_headshot(m):
    args = m.text.split(maxsplit=1)
    if len(args) < 2:
        return bot.reply_to(
            m, "❌ <code>/headshot Roblox</code>", parse_mode="HTML"
        )
    cd = check_cooldown(m.from_user.id)
    if cd:
        return bot.reply_to(m, f"⏳ Подожди {cd} сек.")
    try:
        user = get_user_by_input(args[1])
        url = get_headshot_url(user["id"])
        if url:
            dn = safe_html(user.get("displayName", "?"))
            bot.send_photo(
                m.chat.id, url,
                caption=f"👤 Хедшот <b>{dn}</b>",
                parse_mode="HTML",
            )
        else:
            bot.reply_to(m, "❌ Хедшот недоступен")
    except RBXError as e:
        bot.reply_to(m, f"❌ {e}")


# ─────────────────────────────────────────────
# /friends
# ─────────────────────────────────────────────
@bot.message_handler(commands=["friends"], func=private_only)
def cmd_friends(m):
    args = m.text.split(maxsplit=1)
    if len(args) < 2:
        return bot.reply_to(
            m, "❌ <code>/friends Roblox</code>", parse_mode="HTML"
        )
    cd = check_cooldown(m.from_user.id)
    if cd:
        return bot.reply_to(m, f"⏳ Подожди {cd} сек.")
    try:
        user = get_user_by_input(args[1])
        text, kb = build_friends_text(user["id"], 0)
        bot.send_message(
            m.chat.id, text, parse_mode="HTML", reply_markup=kb
        )
    except RBXError as e:
        bot.reply_to(m, f"❌ {e}")
    except Exception as e:
        log.exception("/friends error")
        bot.reply_to(m, f"❌ {e}")


# ─────────────────────────────────────────────
# /groups
# ─────────────────────────────────────────────
@bot.message_handler(commands=["groups"], func=private_only)
def cmd_groups(m):
    args = m.text.split(maxsplit=1)
    if len(args) < 2:
        return bot.reply_to(
            m, "❌ <code>/groups Roblox</code>", parse_mode="HTML"
        )
    cd = check_cooldown(m.from_user.id)
    if cd:
        return bot.reply_to(m, f"⏳ Подожди {cd} сек.")
    try:
        user = get_user_by_input(args[1])
        text, kb = build_groups_text(user["id"], 0)
        bot.send_message(
            m.chat.id, text, parse_mode="HTML", reply_markup=kb
        )
    except RBXError as e:
        bot.reply_to(m, f"❌ {e}")


# ─────────────────────────────────────────────
# /badges
# ─────────────────────────────────────────────
@bot.message_handler(commands=["badges"], func=private_only)
def cmd_badges(m):
    args = m.text.split(maxsplit=1)
    if len(args) < 2:
        return bot.reply_to(
            m, "❌ <code>/badges Roblox</code>", parse_mode="HTML"
        )
    cd = check_cooldown(m.from_user.id)
    if cd:
        return bot.reply_to(m, f"⏳ Подожди {cd} сек.")
    try:
        user = get_user_by_input(args[1])
        text, kb = build_badges_text(user["id"], 0)
        bot.send_message(
            m.chat.id, text, parse_mode="HTML", reply_markup=kb
        )
    except RBXError as e:
        bot.reply_to(m, f"❌ {e}")


# ─────────────────────────────────────────────
# /online
# ─────────────────────────────────────────────
@bot.message_handler(commands=["online"], func=private_only)
def cmd_online(m):
    args = m.text.split(maxsplit=1)
    if len(args) < 2:
        return bot.reply_to(
            m, "❌ <code>/online Roblox</code>", parse_mode="HTML"
        )
    cd = check_cooldown(m.from_user.id)
    if cd:
        return bot.reply_to(m, f"⏳ Подожди {cd} сек.")
    try:
        user = get_user_by_input(args[1])
        presences = get_presence([user["id"]])
        if not presences:
            return bot.reply_to(m, "❌ Статус недоступен")

        p = presences[0]
        dn = safe_html(user.get("displayName", "?"))
        un = safe_html(user.get("name", "?"))
        text = (
            f"👤 <b>{dn}</b> (@{un})\n\n"
            f"Статус: {fmt_presence(p)}\n"
            f"Последний онлайн: {fmt_date(p.get('lastOnline', ''))}\n"
        )

        place = p.get("placeId")
        if place:
            text += f"\n🎮 Place ID: <code>{place}</code>"
        root = p.get("rootPlaceId")
        if root:
            text += (
                f'\n🔗 <a href="https://www.roblox.com/games/{root}">'
                "Открыть игру</a>"
            )

        bot.send_message(
            m.chat.id, text,
            parse_mode="HTML", disable_web_page_preview=True,
        )
    except RBXError as e:
        bot.reply_to(m, f"❌ {e}")


# ─────────────────────────────────────────────
# /search
# ─────────────────────────────────────────────
@bot.message_handler(commands=["search"], func=private_only)
def cmd_search(m):
    args = m.text.split(maxsplit=1)
    if len(args) < 2:
        return bot.reply_to(
            m, "❌ <code>/search Builderman</code>", parse_mode="HTML"
        )
    cd = check_cooldown(m.from_user.id)
    if cd:
        return bot.reply_to(m, f"⏳ Подожди {cd} сек.")
    try:
        results = search_users(args[1], limit=10)
        if not results:
            return bot.reply_to(m, "🔍 Ничего не найдено")

        lines = [f"🔍 Поиск <b>{safe_html(args[1])}</b>:\n"]
        for i, u in enumerate(results, 1):
            dn = safe_html(u.get("displayName", "?"))
            un = safe_html(u.get("name", "?"))
            v = " ✅" if u.get("hasVerifiedBadge") else ""
            lines.append(
                f"  {i}. <b>{dn}</b> (<code>{un}</code>) "
                f"ID:{u.get('id', '?')}{v}"
            )

        bot.send_message(
            m.chat.id, "\n".join(lines), parse_mode="HTML"
        )
    except RBXError as e:
        bot.reply_to(m, f"❌ {e}")


# ─────────────────────────────────────────────
# /game
# ─────────────────────────────────────────────
@bot.message_handler(commands=["game"], func=private_only)
def cmd_game(m):
    args = m.text.split(maxsplit=1)
    if len(args) < 2 or not args[1].strip().isdigit():
        return bot.reply_to(
            m,
            "❌ Укажи ID (placeId или universeId)\n"
            "<code>/game 286090429</code>",
            parse_mode="HTML",
        )
    cd = check_cooldown(m.from_user.id)
    if cd:
        return bot.reply_to(m, f"⏳ Подожди {cd} сек.")

    w = bot.reply_to(m, "⏳ Загрузка...")
    try:
        game_id = int(args[1].strip())

        # Пробуем как universeId
        game = None
        try:
            game = get_game_info(game_id)
        except RBXError:
            pass

        # Не нашли — пробуем как placeId
        if not game:
            try:
                universe_id = place_to_universe(game_id)
                game = get_game_info(universe_id)
            except RBXError:
                raise RBXError(
                    f"Игра с ID {game_id} не найдена.\n"
                    "Попробуй /searchgame"
                )

        uid = game.get("id", game_id)
        thumb = get_game_thumbnail(uid)

        creator = game.get("creator", {})
        text = (
            f"🎮 <b>{safe_html(game.get('name', '?'))}</b>\n\n"
            f"├ 👤 Автор: <b>{safe_html(creator.get('name', '?'))}</b>\n"
            f"├ 🎭 Жанр: {game.get('genre', '—')}\n"
            f"├ 🟢 Играют: <b>{fmt_num(game.get('playing', 0))}</b>"
            f" / {game.get('maxPlayers', '?')}\n"
            f"├ 👁 Визиты: <b>{fmt_num(game.get('visits', 0))}</b>\n"
            f"├ ⭐ Избранное: <b>{fmt_num(game.get('favoritedCount', 0))}</b>\n"
            f"├ 📅 Создана: {fmt_date(game.get('created', ''))}\n"
            f"└ 🔄 Обновлена: {fmt_date(game.get('updated', ''))}"
        )

        desc = game.get("description", "")
        if desc:
            text += f"\n\n📝 <i>{safe_html(desc[:300])}</i>"

        root = game.get("rootPlaceId")
        if root:
            text += (
                f'\n\n🔗 <a href="https://www.roblox.com/games/{root}">'
                "Открыть на Roblox</a>"
            )

        if thumb:
            bot.send_photo(
                m.chat.id, thumb, caption=text, parse_mode="HTML"
            )
        else:
            bot.send_message(
                m.chat.id, text,
                parse_mode="HTML", disable_web_page_preview=True,
            )
        bot.delete_message(m.chat.id, w.message_id)

    except RBXError as e:
        bot.edit_message_text(
            f"❌ {e}", m.chat.id, w.message_id, parse_mode="HTML"
        )
    except Exception as e:
        log.exception("/game error")
        bot.edit_message_text(f"❌ {e}", m.chat.id, w.message_id)


# ─────────────────────────────────────────────
# /searchgame
# ─────────────────────────────────────────────
@bot.message_handler(commands=["searchgame"], func=private_only)
def cmd_searchgame(m):
    args = m.text.split(maxsplit=1)
    if len(args) < 2:
        return bot.reply_to(
            m, "❌ <code>/searchgame Adopt Me</code>", parse_mode="HTML"
        )
    cd = check_cooldown(m.from_user.id)
    if cd:
        return bot.reply_to(m, f"⏳ Подожди {cd} сек.")

    w = bot.reply_to(m, "🔍 Ищу...")
    try:
        games = search_games(args[1], limit=10)
        if not games:
            return bot.edit_message_text(
                "🔍 Ничего не найдено", m.chat.id, w.message_id
            )

        lines = [f"🎮 Поиск <b>{safe_html(args[1])}</b>:\n"]
        for i, g in enumerate(games, 1):
            name = g.get("name") or g.get("Name") or "?"
            playing = g.get("playerCount") or g.get("PlayerCount") or 0
            uid = g.get("universeId") or g.get("UniverseId") or "?"
            lines.append(
                f"  {i}. <b>{safe_html(str(name))}</b>\n"
                f"      └ 🟢 {fmt_num(playing)} | "
                f"ID: <code>{uid}</code>"
            )

        lines.append("\n💡 /game &lt;ID&gt; для подробностей")
        bot.edit_message_text(
            "\n".join(lines), m.chat.id, w.message_id, parse_mode="HTML"
        )
    except RBXError as e:
        bot.edit_message_text(f"❌ {e}", m.chat.id, w.message_id)
    except Exception as e:
        log.exception("/searchgame error")
        bot.edit_message_text(f"❌ {e}", m.chat.id, w.message_id)


# ─────────────────────────────────────────────
# /group
# ─────────────────────────────────────────────
@bot.message_handler(commands=["group"], func=private_only)
def cmd_group(m):
    args = m.text.split(maxsplit=1)
    if len(args) < 2 or not args[1].strip().isdigit():
        return bot.reply_to(
            m, "❌ <code>/group 1</code>", parse_mode="HTML"
        )
    cd = check_cooldown(m.from_user.id)
    if cd:
        return bot.reply_to(m, f"⏳ Подожди {cd} сек.")
    try:
        gid = int(args[1].strip())
        g = get_group_info(gid)
        owner = g.get("owner") or {}
        owner_name = safe_html(owner.get("username", "—"))
        verified = " ✅" if g.get("hasVerifiedBadge") else ""

        public = "✅" if g.get("publicEntryAllowed") else "❌"
        locked = "🔒 Да" if g.get("isLocked") else "✅ Нет"

        text = (
            f"👥 <b>{safe_html(g.get('name', '?'))}</b>{verified}\n\n"
            f"├ ID: <code>{gid}</code>\n"
            f"├ 👑 Владелец: <b>{owner_name}</b>\n"
            f"├ 👤 Участников: <b>{fmt_num(g.get('memberCount', 0))}</b>\n"
            f"├ 🔓 Открытая: {public}\n"
            f"└ 🔒 Заблокирована: {locked}"
        )

        desc = g.get("description", "")
        if desc:
            text += f"\n\n📝 <i>{safe_html(desc[:300])}</i>"

        text += (
            f'\n\n🔗 <a href="https://www.roblox.com/groups/{gid}">'
            "Открыть на Roblox</a>"
        )
        bot.send_message(
            m.chat.id, text,
            parse_mode="HTML", disable_web_page_preview=True,
        )
    except RBXError as e:
        bot.reply_to(m, f"❌ {e}")


# ─────────────────────────────────────────────
# /asset
# ─────────────────────────────────────────────
@bot.message_handler(commands=["asset"], func=private_only)
def cmd_asset(m):
    args = m.text.split(maxsplit=1)
    if len(args) < 2 or not args[1].strip().isdigit():
        return bot.reply_to(
            m, "❌ <code>/asset 607785314</code>", parse_mode="HTML"
        )
    cd = check_cooldown(m.from_user.id)
    if cd:
        return bot.reply_to(m, f"⏳ Подожди {cd} сек.")

    w = bot.reply_to(m, "⏳ Загрузка...")
    try:
        aid = int(args[1].strip())
        asset = get_asset_info(aid)
        thumb = get_asset_thumbnail(aid)

        creator = asset.get("Creator", {})
        price = asset.get("PriceInRobux")
        price_str = f"💰 {fmt_num(price)} R$" if price else "🆓 Бесплатно"
        type_id = asset.get("AssetTypeId", 0)
        atype = ASSET_TYPES.get(type_id, f"Type {type_id}")

        limited = ""
        if asset.get("IsLimited"):
            limited = "\n├ 🏷 Limited: ✅"
        if asset.get("IsLimitedUnique"):
            limited = "\n├ 🏷 Limited U: ✅"

        for_sale = "✅" if asset.get("IsForSale") else "❌"

        text = (
            f"🎒 <b>{safe_html(asset.get('Name', '?'))}</b>\n\n"
            f"├ ID: <code>{aid}</code>\n"
            f"├ Тип: {atype}\n"
            f"├ 👤 Автор: <b>{safe_html(creator.get('Name', '?'))}</b>\n"
            f"├ {price_str}\n"
            f"├ 🛒 В продаже: {for_sale}\n"
            f"├ 📊 Продаж: <b>{fmt_num(asset.get('Sales', 0))}</b>"
            f"{limited}\n"
            f"├ 📅 Создан: {fmt_date(asset.get('Created', ''))}\n"
            f"└ 🔄 Обновлён: {fmt_date(asset.get('Updated', ''))}"
        )

        desc = asset.get("Description", "")
        if desc:
            text += f"\n\n📝 <i>{safe_html(desc[:300])}</i>"

        text += (
            f'\n\n🔗 <a href="https://www.roblox.com/catalog/{aid}">'
            "Открыть на Roblox</a>"
        )

        kb = None
        if type_id in DOWNLOADABLE:
            kb = types.InlineKeyboardMarkup()
            kb.add(
                types.InlineKeyboardButton(
                    "📥 Скачать", callback_data=f"dl:{aid}"
                )
            )

        if thumb:
            bot.send_photo(
                m.chat.id, thumb, caption=text,
                parse_mode="HTML", reply_markup=kb,
            )
        else:
            bot.send_message(
                m.chat.id, text, parse_mode="HTML",
                disable_web_page_preview=True, reply_markup=kb,
            )
        bot.delete_message(m.chat.id, w.message_id)

    except RBXError as e:
        bot.edit_message_text(f"❌ {e}", m.chat.id, w.message_id)
    except Exception as e:
        log.exception("/asset error")
        bot.edit_message_text(f"❌ {e}", m.chat.id, w.message_id)


# ─────────────────────────────────────────────
# /download
# ─────────────────────────────────────────────
@bot.message_handler(commands=["download"], func=private_only)
def cmd_download(m):
    args = m.text.split(maxsplit=1)
    if len(args) < 2 or not args[1].strip().isdigit():
        return bot.reply_to(
            m, "❌ <code>/download 607785314</code>", parse_mode="HTML"
        )
    cd = check_cooldown(m.from_user.id)
    if cd:
        return bot.reply_to(m, f"⏳ Подожди {cd} сек.")

    do_download(m.chat.id, int(args[1].strip()))


def do_download(chat_id: int, asset_id: int):
    """Скачать и отправить ассет."""
    w = bot.send_message(chat_id, f"📥 Скачиваю ассет {asset_id}...")
    try:
        try:
            info = get_asset_info(asset_id)
            type_id = info.get("AssetTypeId", 0)
            name = info.get("Name", str(asset_id))
        except Exception:
            type_id = 0
            name = str(asset_id)

        data = download_asset(asset_id)
        if not data:
            return bot.edit_message_text(
                "❌ Не удалось скачать ассет", chat_id, w.message_id
            )

        ext = ASSET_EXT.get(type_id, ".bin")
        type_name = ASSET_TYPES.get(type_id, "Unknown")

        # Clothing: извлечь текстуру
        if type_id in (2, 11, 12):
            text_content = data.decode("utf-8", errors="ignore")
            url_match = re.search(r"<url>([^<]+)</url>", text_content)
            if not url_match:
                url_match = re.search(
                    r'(https?://[^"<>\s]+)', text_content
                )
            if url_match:
                texture_url = url_match.group(1)
                texture_url = texture_url.replace("http://", "https://")
                if "rbxassetid://" in texture_url:
                    tex_id = re.search(r"(\d+)", texture_url)
                    if tex_id:
                        texture_url = (
                            "https://assetdelivery.roblox.com"
                            f"/v1/asset/?id={tex_id.group(1)}"
                        )
                try:
                    tr = S.get(
                        texture_url, timeout=30, allow_redirects=True
                    )
                    if tr.status_code == 200 and len(tr.content) > 100:
                        data = tr.content
                        ext = ".png"
                except Exception:
                    pass

        # Decal: извлечь изображение
        if type_id == 13:
            text_content = data.decode("utf-8", errors="ignore")
            url_match = re.search(r"<url>([^<]+)</url>", text_content)
            if not url_match:
                url_match = re.search(
                    r'(https?://[^"<>\s]+)', text_content
                )
            if url_match:
                texture_url = url_match.group(1)
                texture_url = texture_url.replace("http://", "https://")
                if "rbxassetid://" in texture_url:
                    tex_id = re.search(r"(\d+)", texture_url)
                    if tex_id:
                        texture_url = (
                            "https://assetdelivery.roblox.com"
                            f"/v1/asset/?id={tex_id.group(1)}"
                        )
                try:
                    tr = S.get(
                        texture_url, timeout=30, allow_redirects=True
                    )
                    if tr.status_code == 200:
                        data = tr.content
                        ext = ".png"
                except Exception:
                    pass

        # Audio: определить расширение
        if type_id == 3:
            if data[:4] == b"OggS":
                ext = ".ogg"
            elif data[:3] == b"ID3" or data[:2] == b"\xff\xfb":
                ext = ".mp3"
            elif data[:4] == b"fLaC":
                ext = ".flac"
            elif data[:4] == b"RIFF":
                ext = ".wav"

        safe_name = re.sub(r"[^a-zA-Z0-9_-]", "_", name)[:50]
        filename = f"{safe_name}_{asset_id}{ext}"

        file_obj = io.BytesIO(data)
        file_obj.name = filename

        size_kb = len(data) // 1024
        caption = (
            f"📥 <b>{safe_html(name)}</b>\n"
            f"├ Тип: {type_name}\n"
            f"├ ID: <code>{asset_id}</code>\n"
            f"└ Размер: {size_kb} КБ"
        )

        if type_id == 3 and ext == ".ogg":
            bot.send_audio(
                chat_id, file_obj, caption=caption, parse_mode="HTML"
            )
        elif ext == ".png" and len(data) < 10 * 1024 * 1024:
            try:
                bot.send_photo(
                    chat_id, io.BytesIO(data),
                    caption=caption, parse_mode="HTML",
                )
            except Exception:
                file_obj.seek(0)
                bot.send_document(
                    chat_id, file_obj,
                    caption=caption, parse_mode="HTML",
                )
        else:
            bot.send_document(
                chat_id, file_obj, caption=caption, parse_mode="HTML"
            )

        bot.delete_message(chat_id, w.message_id)

    except RBXError as e:
        bot.edit_message_text(f"❌ {e}", chat_id, w.message_id)
    except Exception as e:
        log.exception("/download error")
        bot.edit_message_text(f"❌ Ошибка: {e}", chat_id, w.message_id)


# ─────────────────────────────────────────────
# CALLBACK QUERIES
# ─────────────────────────────────────────────
@bot.callback_query_handler(func=lambda call: True)
def handle_callback(call):
    data = call.data

    if data == "noop":
        bot.answer_callback_query(call.id)
        return

    parts = data.split(":")

    # Скачивание
    if parts[0] == "dl" and len(parts) == 2:
        bot.answer_callback_query(call.id, "📥 Скачиваю...")
        asset_id = int(parts[1])
        threading.Thread(
            target=do_download,
            args=(call.message.chat.id, asset_id),
            daemon=True,
        ).start()
        return

    # Пагинация: section:USER_ID:PAGE
    if len(parts) == 3:
        section = parts[0]
        try:
            user_id = int(parts[1])
            page = int(parts[2])
        except ValueError:
            bot.answer_callback_query(call.id, "❌ Ошибка")
            return

        try:
            if section == "friends":
                text, kb = build_friends_text(user_id, page)
            elif section == "groups":
                text, kb = build_groups_text(user_id, page)
            elif section == "badges":
                text, kb = build_badges_text(user_id, page)
            else:
                bot.answer_callback_query(
                    call.id, "❌ Неизвестное действие"
                )
                return

            # Проверяем: если сообщение — фото (профиль с аватаром),
            # нельзя edit_message_text. Удаляем и шлём новое.
            msg = call.message
            if msg.content_type != "text":
                # Это фото/документ — удаляем и шлём новое текстовое
                try:
                    bot.delete_message(msg.chat.id, msg.message_id)
                except Exception:
                    pass
                bot.send_message(
                    msg.chat.id, text,
                    parse_mode="HTML", reply_markup=kb,
                )
            else:
                # Обычное текстовое — редактируем
                bot.edit_message_text(
                    text, msg.chat.id, msg.message_id,
                    parse_mode="HTML", reply_markup=kb,
                )

            bot.answer_callback_query(call.id)

        except telebot.apihelper.ApiTelegramException as e:
            if "message is not modified" in str(e):
                bot.answer_callback_query(
                    call.id, "Уже на этой странице"
                )
            else:
                bot.answer_callback_query(call.id, f"❌ {e}")
        except RBXError as e:
            bot.answer_callback_query(
                call.id, f"❌ {e}", show_alert=True
            )
        except Exception as e:
            log.exception("Callback error")
            bot.answer_callback_query(call.id, f"❌ {e}")


# ─────────────────────────────────────────────
# Неизвестные команды
# ─────────────────────────────────────────────
@bot.message_handler(
    func=lambda m: m.text and m.text.startswith("/") and private_only(m)
)
def cmd_unknown(m):
    bot.reply_to(m, "❓ Неизвестная команда. /help")


# ─────────────────────────────────────────────
# ЗАПУСК
# ─────────────────────────────────────────────
if __name__ == "__main__":
    log.info("=" * 40)
    log.info("  RBX Info Bot v3 запущен!")
    log.info("  Cooldown: %ds | Page: %d", COOLDOWN_SEC, PAGE_SIZE)
    if PROXY_URL:
        display = PROXY_URL.split("@")[-1] if "@" in PROXY_URL else "yes"
        log.info("  Proxy: %s", display)
    else:
        log.info("  Proxy: нет (прямое подключение)")
    log.info("=" * 40)
    bot.infinity_polling(
        timeout=30,
        long_polling_timeout=25,
        allowed_updates=["message", "callback_query"],
    )
