#!/usr/bin/env python3
"""
rbx_bot.py — Telegram-бот для Roblox (v4).
pyTeleBot (telebot). БЕЗ aiogram.

v4:
  - До 10 прокси одновременно (round-robin + auto-failover)
  - Исправлен вывод ошибки PySocks (нормальные переносы строк)
  - Друзья, группы, значки с кэшем и пагинацией
  - Инлайн-кнопки на фото: удаляется фото, шлётся новое сообщение
  - Скачивание ассетов (Shirt, Pants, Decal, Audio, Model)

Установка:
    pip install pyTelegramBotAPI requests PySocks

Запуск:
    export BOT_TOKEN="ваш-токен"
    export PROXY_URL="socks5://u:p@ip1:1080,socks5://u:p@ip2:1080"
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
    print()
    print("=" * 50)
    print("  BOT_TOKEN не найден!")
    print('  export BOT_TOKEN="ваш-токен"')
    print("  python3 rbx_bot.py")
    print("=" * 50)
    sys.exit(1)

PROXY_RAW = os.getenv("PROXY_URL", "").strip()
COOLDOWN_SEC = int(os.getenv("COOLDOWN_SEC", "2"))
REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "15"))
PAGE_SIZE = 10
CACHE_TTL = 120

# ─────────────────────────────────────────────
# МУЛЬТИ-ПРОКСИ С РОТАЦИЕЙ (до 10 штук)
# ─────────────────────────────────────────────
class ProxyPool:
    """
    Пул прокси с round-robin ротацией и auto-failover.
    Поддерживает до 10 прокси одновременно.
    PROXY_URL может содержать несколько прокси через запятую.
    """

    def __init__(self, raw: str, max_proxies: int = 10):
        self._lock = threading.Lock()
        self._index = 0
        self._errors: dict[int, int] = {}  # index -> error count
        self._proxies: list[str] = []

        if not raw:
            return

        parts = [p.strip() for p in raw.split(",") if p.strip()]
        self._proxies = parts[:max_proxies]

        # Проверяем PySocks для SOCKS прокси
        has_socks = any(p.startswith("socks") for p in self._proxies)
        if has_socks:
            try:
                import socks  # noqa: F401
            except ImportError:
                print()
                print("=" * 50)
                print("  PySocks не установлен!")
                print("  SOCKS5 прокси требует PySocks:")
                print("  pip install PySocks")
                print("=" * 50)
                sys.exit(1)

        log.info("Загружено прокси: %d", len(self._proxies))
        for i, p in enumerate(self._proxies):
            display = p.split("@")[-1] if "@" in p else p
            log.info("  [%d] %s", i + 1, display)

    @property
    def count(self) -> int:
        return len(self._proxies)

    @property
    def active(self) -> bool:
        return len(self._proxies) > 0

    def _display(self, proxy: str) -> str:
        return proxy.split("@")[-1] if "@" in proxy else proxy

    def get_session(self) -> requests.Session:
        """
        Возвращает сессию с текущим прокси.
        Если прокси нет — возвращает обычную сессию.
        """
        s = requests.Session()
        s.headers.update({
            "Accept": "application/json",
            "User-Agent": "RBXInfoBot/4.0",
        })
        if self._proxies:
            with self._lock:
                proxy = self._proxies[self._index % len(self._proxies)]
            s.proxies = {"http": proxy, "https": proxy}
        return s

    def rotate(self):
        """Переключиться на следующий прокси."""
        if not self._proxies:
            return
        with self._lock:
            old = self._index
            self._index = (self._index + 1) % len(self._proxies)
            # Считаем ошибки
            self._errors[old] = self._errors.get(old, 0) + 1
            new_proxy = self._proxies[self._index]
            log.warning(
                "Прокси ротация: [%d] -> [%d] %s",
                old + 1, self._index + 1, self._display(new_proxy)
            )

    def advance(self):
        """Просто перейти к следующему (без ошибки, round-robin)."""
        if not self._proxies:
            return
        with self._lock:
            self._index = (self._index + 1) % len(self._proxies)

    def status_text(self) -> str:
        """Текст статуса для команды /proxy."""
        if not self._proxies:
            return (
                "Все запросы идут напрямую.\n"
                "Если сервер в РФ — задай PROXY_URL"
            )
        lines = []
        with self._lock:
            for i, p in enumerate(self._proxies):
                display = self._display(p)
                proto = p.split("://")[0] if "://" in p else "?"
                errs = self._errors.get(i, 0)
                active = "▶️" if i == self._index else "  "
                err_str = f" | ❌ {errs} ошибок" if errs else ""
                lines.append(
                    f"{active} [{i+1}] {proto}://{display}{err_str}"
                )
        return "\n".join(lines)


proxy_pool = ProxyPool(PROXY_RAW)

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
# HTTP-ЗАПРОСЫ С АВТО-FAILOVER
# ─────────────────────────────────────────────
class RBXError(Exception):
    pass


def _do_request(method: str, url: str, **kwargs):
    """
    Выполнить HTTP-запрос с auto-failover.
    Если прокси упал — пробует следующий (до 3 попыток).
    """
    kwargs.setdefault("timeout", REQUEST_TIMEOUT)
    max_tries = min(proxy_pool.count, 3) if proxy_pool.active else 1
    last_err = None

    for attempt in range(max(max_tries, 1)):
        s = proxy_pool.get_session()
        try:
            if method == "GET":
                r = s.get(url, **kwargs)
            else:
                r = s.post(url, **kwargs)

            if r.status_code == 404:
                raise RBXError("Не найдено (404)")
            if r.status_code == 429:
                raise RBXError("Слишком много запросов, подожди")
            if r.status_code not in (200, 201):
                raise RBXError(f"HTTP {r.status_code}")

            # Успех — прокручиваем round-robin для следующего запроса
            proxy_pool.advance()
            return r.json()

        except (requests.exceptions.ProxyError,
                requests.exceptions.ConnectionError,
                requests.exceptions.Timeout) as e:
            last_err = e
            err_str = str(e)
            if "Missing dependencies for SOCKS" in err_str:
                raise RBXError("PySocks не установлен! pip install PySocks")
            log.warning(
                "Прокси ошибка (attempt %d/%d): %s",
                attempt + 1, max_tries, err_str[:120]
            )
            if proxy_pool.active and attempt < max_tries - 1:
                proxy_pool.rotate()
                continue
            break
        except requests.RequestException as e:
            raise RBXError(f"Ошибка сети: {e}")

    # Все попытки исчерпаны
    if isinstance(last_err, requests.exceptions.Timeout):
        raise RBXError("Таймаут. Если сервер в РФ — задай PROXY_URL")
    if isinstance(last_err, requests.exceptions.ProxyError):
        raise RBXError(f"Все прокси недоступны: {last_err}")
    raise RBXError(f"Ошибка подключения: {last_err}")


def api_get(url, params=None):
    return _do_request("GET", url, params=params)


def api_post(url, data):
    return _do_request("POST", url, json=data)


def raw_get(url, **kwargs):
    """GET запрос с прокси (возвращает Response, не JSON)."""
    kwargs.setdefault("timeout", REQUEST_TIMEOUT)
    max_tries = min(proxy_pool.count, 3) if proxy_pool.active else 1
    for attempt in range(max(max_tries, 1)):
        s = proxy_pool.get_session()
        try:
            r = s.get(url, **kwargs)
            proxy_pool.advance()
            return r
        except (requests.exceptions.ProxyError,
                requests.exceptions.ConnectionError,
                requests.exceptions.Timeout):
            if proxy_pool.active and attempt < max_tries - 1:
                proxy_pool.rotate()
                continue
        except Exception:
            break
    return None


# ─────────────────────────────────────────────
# КЭШ
# ─────────────────────────────────────────────
class SimpleCache:
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
# ROBLOX API
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


def get_avatar_url(user_id: int, size="420x420") -> Optional[str]:
    try:
        data = api_get(
            "https://thumbnails.roblox.com/v1/users/avatar",
            {"userIds": user_id, "size": size,
             "format": "Png", "isCircular": "false"},
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
            {"userIds": user_id, "size": size,
             "format": "Png", "isCircular": "false"},
        )
        items = data.get("data", [])
        if items and items[0].get("imageUrl"):
            return items[0]["imageUrl"]
    except Exception:
        pass
    return None


def get_friends_count(uid: int) -> int:
    try:
        return api_get(
            f"https://friends.roblox.com/v1/users/{uid}/friends/count"
        ).get("count", 0)
    except Exception:
        return 0


def get_followers_count(uid: int) -> int:
    try:
        return api_get(
            f"https://friends.roblox.com/v1/users/{uid}/followers/count"
        ).get("count", 0)
    except Exception:
        return 0


def get_followings_count(uid: int) -> int:
    try:
        return api_get(
            f"https://friends.roblox.com/v1/users/{uid}/followings/count"
        ).get("count", 0)
    except Exception:
        return 0


def get_friends_list(user_id: int) -> list:
    cached = _friends_cache.get(user_id)
    if cached is not None:
        return cached
    data = api_get(
        f"https://friends.roblox.com/v1/users/{user_id}/friends"
    )
    friends = data.get("data", [])
    _friends_cache.set(user_id, friends)
    return friends


def get_user_groups(user_id: int) -> list:
    cached = _groups_cache.get(user_id)
    if cached is not None:
        return cached
    data = api_get(
        f"https://groups.roblox.com/v1/users/{user_id}/groups/roles"
    )
    groups = data.get("data", [])
    _groups_cache.set(user_id, groups)
    return groups


def get_group_info(group_id: int) -> dict:
    return api_get(f"https://groups.roblox.com/v1/groups/{group_id}")


def get_user_badges(user_id: int) -> list:
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
            f"https://badges.roblox.com/v1/users/{user_id}/badges",
            params,
        )
        all_badges.extend(data.get("data", []))
        cursor = data.get("nextPageCursor")
        if not cursor:
            break
    _badges_cache.set(user_id, all_badges)
    return all_badges


def get_presence(user_ids: list) -> list:
    data = api_post(
        "https://presence.roblox.com/v1/presence/users",
        {"userIds": user_ids},
    )
    return data.get("userPresences", [])


def place_to_universe(place_id: int) -> int:
    data = api_get(
        f"https://apis.roblox.com/universes/v1/places/{place_id}/universe"
    )
    uid = data.get("universeId")
    if not uid:
        raise RBXError("Не удалось найти universeId")
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
            {"universeIds": str(universe_id), "size": "512x512",
             "format": "Png", "isCircular": "false"},
        )
        items = data.get("data", [])
        if items and items[0].get("imageUrl"):
            return items[0]["imageUrl"]
    except Exception:
        pass
    return None


def search_games(keyword: str, limit: int = 10) -> list:
    try:
        data = api_get(
            "https://games.roblox.com/v1/games/list",
            {"model.keyword": keyword,
             "model.startRows": 0,
             "model.maxRows": limit},
        )
        games = data.get("games", [])
        if games:
            return games
    except Exception:
        pass
    try:
        r = raw_get(
            "https://apis.roblox.com/search-api/omni-search",
            params={"searchQuery": keyword, "searchType": "games",
                    "pageToken": "", "sessionId": ""},
        )
        if r and r.status_code == 200:
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


def get_asset_info(asset_id: int) -> dict:
    return api_get(
        f"https://economy.roblox.com/v2/assets/{asset_id}/details"
    )


def get_asset_thumbnail(asset_id: int) -> Optional[str]:
    try:
        data = api_get(
            "https://thumbnails.roblox.com/v1/assets",
            {"assetIds": str(asset_id), "size": "420x420",
             "format": "Png"},
        )
        items = data.get("data", [])
        if items and items[0].get("imageUrl"):
            return items[0]["imageUrl"]
    except Exception:
        pass
    return None


def download_asset(asset_id: int) -> Optional[bytes]:
    urls = [
        f"https://assetdelivery.roblox.com/v1/asset/?id={asset_id}",
        f"https://assetdelivery.roblox.com/v1/assetId/{asset_id}",
    ]
    for url in urls:
        r = raw_get(url, timeout=30, allow_redirects=True)
        if r and r.status_code == 200 and len(r.content) > 0:
            return r.content
    return None


ASSET_TYPES = {
    1: "Image", 2: "T-Shirt", 3: "Audio", 4: "Mesh",
    5: "Lua", 8: "Hat", 9: "Place", 10: "Model",
    11: "Shirt", 12: "Pants", 13: "Decal", 17: "Head",
    18: "Face", 19: "Gear", 21: "Badge", 24: "Animation",
    27: "Torso", 28: "Right Arm", 29: "Left Arm",
    30: "Left Leg", 31: "Right Leg", 32: "Package",
    34: "GamePass", 38: "Plugin", 40: "MeshPart",
    41: "Hair", 42: "Face Acc", 43: "Neck Acc",
    44: "Shoulder", 45: "Front Acc", 46: "Back Acc",
    47: "Waist Acc",
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
def make_page_kb(prefix, user_id, page, total):
    kb = types.InlineKeyboardMarkup(row_width=3)
    btns = []
    if page > 0:
        btns.append(types.InlineKeyboardButton(
            "◀", callback_data=f"{prefix}:{user_id}:{page-1}"))
    else:
        btns.append(types.InlineKeyboardButton(
            "·", callback_data="noop"))
    btns.append(types.InlineKeyboardButton(
        f"{page+1}/{total}", callback_data="noop"))
    if page < total - 1:
        btns.append(types.InlineKeyboardButton(
            "▶", callback_data=f"{prefix}:{user_id}:{page+1}"))
    else:
        btns.append(types.InlineKeyboardButton(
            "·", callback_data="noop"))
    kb.row(*btns)
    return kb


def paginate(items, page, per_page=PAGE_SIZE):
    total = max(1, (len(items) + per_page - 1) // per_page)
    page = max(0, min(page, total - 1))
    start = page * per_page
    return items[start:start + per_page], page, total


# ─────────────────────────────────────────────
# ПОСТРОЕНИЕ СТРАНИЦ
# ─────────────────────────────────────────────
def build_friends_text(user_id, page):
    friends = get_friends_list(user_id)
    if not friends:
        return "👤 Нет друзей", None
    page_items, page, total = paginate(friends, page)
    lines = [
        f"👥 <b>Друзья</b> (всего {fmt_num(len(friends))}), "
        f"стр. {page+1}/{total}:\n"
    ]
    for i, fr in enumerate(page_items, start=page * PAGE_SIZE + 1):
        display = fr.get("displayName") or fr.get("name") or "—"
        uname = fr.get("name") or "—"
        fid = fr.get("id", "?")
        online = fr.get("isOnline", False)
        verified = " ✅" if fr.get("hasVerifiedBadge") else ""
        dot = "🟢" if online else "⚫"
        lines.append(
            f"  {i}. {dot} <b>{safe_html(display)}</b> "
            f"(<code>{safe_html(uname)}</code>) "
            f"ID:{fid}{verified}"
        )
    return "\n".join(lines), make_page_kb("friends", user_id, page, total)


def build_groups_text(user_id, page):
    groups = get_user_groups(user_id)
    if not groups:
        return "🏰 Не состоит в группах", None
    page_items, page, total = paginate(groups, page)
    lines = [
        f"🏰 <b>Группы</b> (всего {len(groups)}), "
        f"стр. {page+1}/{total}:\n"
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
    return "\n".join(lines), make_page_kb("groups", user_id, page, total)


def build_badges_text(user_id, page):
    badges = get_user_badges(user_id)
    if not badges:
        return "🏅 Нет значков", None
    page_items, page, total = paginate(badges, page)
    lines = [
        f"🏅 <b>Значки</b> (всего {len(badges)}), "
        f"стр. {page+1}/{total}:\n"
    ]
    for i, b in enumerate(page_items, start=page * PAGE_SIZE + 1):
        bname = safe_html(b.get("displayName") or b.get("name", "?"))
        bdesc = safe_html((b.get("description") or "—")[:80])
        lines.append(f"  {i}. <b>{bname}</b>\n      └ {bdesc}")
    return "\n".join(lines), make_page_kb("badges", user_id, page, total)


# ─────────────────────────────────────────────
# КОМАНДЫ
# ─────────────────────────────────────────────
HELP_TEXT = (
    "🎮 <b>RBX Info Bot v4</b>\n\n"
    "<b>👤 Игроки:</b>\n"
    "  /profile &lt;ник&gt; — профиль\n"
    "  /avatar &lt;ник&gt; — аватар\n"
    "  /headshot &lt;ник&gt; — хедшот\n"
    "  /friends &lt;ник&gt; — друзья (◀▶)\n"
    "  /groups &lt;ник&gt; — группы (◀▶)\n"
    "  /badges &lt;ник&gt; — значки (◀▶)\n"
    "  /online &lt;ник&gt; — статус\n"
    "  /search &lt;запрос&gt; — поиск\n\n"
    "<b>🎮 Игры:</b>\n"
    "  /game &lt;id&gt; — инфо (placeId/universeId)\n"
    "  /searchgame &lt;название&gt; — поиск\n\n"
    "<b>👥 Группы:</b>\n"
    "  /group &lt;id&gt; — инфо\n\n"
    "<b>🎒 Ассеты:</b>\n"
    "  /asset &lt;id&gt; — инфо\n"
    "  /download &lt;id&gt; — скачать\n\n"
    "<b>⚙️ Прочее:</b>\n"
    "  /help — это сообщение\n"
    "  /ping — проверка\n"
    "  /proxy — статус прокси (всех)\n\n"
    "💡 Можно писать username или userId\n"
    "📥 Скачивание: Shirt, Pants, Decal, Audio, Model\n"
    "🇷🇺 До 10 прокси с ротацией и failover"
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
    if not proxy_pool.active:
        text = (
            "🔀 <b>Прокси не задан</b>\n\n"
            "Запросы идут напрямую.\n"
            "Если сервер в РФ — задай PROXY_URL"
        )
    else:
        status = proxy_pool.status_text()
        text = (
            f"🔀 <b>Прокси пул ({proxy_pool.count} шт.)</b>\n\n"
            f"<pre>{status}</pre>\n\n"
            "✅ Round-robin ротация + auto-failover"
        )
    bot.send_message(m.chat.id, text, parse_mode="HTML")


# ─── /profile ───
@bot.message_handler(commands=["profile"], func=private_only)
def cmd_profile(m):
    args = m.text.split(maxsplit=1)
    if len(args) < 2:
        return bot.reply_to(m, "❌ <code>/profile Roblox</code>",
                            parse_mode="HTML")
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
        verified = "✅" if user.get("hasVerifiedBadge") else "❌"

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
            d = safe_html(desc[:300])
            if len(desc) > 300:
                d += "..."
            text += f"\n\n📝 <i>{d}</i>"
        text += (
            f'\n\n🔗 <a href="https://www.roblox.com/users/{uid}/profile">'
            "Открыть на Roblox</a>"
        )
        kb = types.InlineKeyboardMarkup(row_width=3)
        kb.add(
            types.InlineKeyboardButton("👥 Друзья",
                                       callback_data=f"friends:{uid}:0"),
            types.InlineKeyboardButton("🏰 Группы",
                                       callback_data=f"groups:{uid}:0"),
            types.InlineKeyboardButton("🏅 Значки",
                                       callback_data=f"badges:{uid}:0"),
        )
        if av:
            bot.send_photo(m.chat.id, av, caption=text,
                          parse_mode="HTML", reply_markup=kb)
        else:
            bot.send_message(m.chat.id, text, parse_mode="HTML",
                            disable_web_page_preview=True, reply_markup=kb)
        bot.delete_message(m.chat.id, w.message_id)
    except RBXError as e:
        bot.edit_message_text(f"❌ {e}", m.chat.id, w.message_id)
    except Exception as e:
        log.exception("/profile error")
        bot.edit_message_text(f"❌ {e}", m.chat.id, w.message_id)


# ─── /avatar, /headshot ───
@bot.message_handler(commands=["avatar"], func=private_only)
def cmd_avatar(m):
    args = m.text.split(maxsplit=1)
    if len(args) < 2:
        return bot.reply_to(m, "❌ <code>/avatar Roblox</code>",
                            parse_mode="HTML")
    cd = check_cooldown(m.from_user.id)
    if cd:
        return bot.reply_to(m, f"⏳ Подожди {cd} сек.")
    try:
        user = get_user_by_input(args[1])
        url = get_avatar_url(user["id"])
        if url:
            bot.send_photo(m.chat.id, url,
                          caption=f"🖼 Аватар <b>{safe_html(user.get('displayName','?'))}</b>",
                          parse_mode="HTML")
        else:
            bot.reply_to(m, "❌ Аватар недоступен")
    except RBXError as e:
        bot.reply_to(m, f"❌ {e}")


@bot.message_handler(commands=["headshot"], func=private_only)
def cmd_headshot(m):
    args = m.text.split(maxsplit=1)
    if len(args) < 2:
        return bot.reply_to(m, "❌ <code>/headshot Roblox</code>",
                            parse_mode="HTML")
    cd = check_cooldown(m.from_user.id)
    if cd:
        return bot.reply_to(m, f"⏳ Подожди {cd} сек.")
    try:
        user = get_user_by_input(args[1])
        url = get_headshot_url(user["id"])
        if url:
            bot.send_photo(m.chat.id, url,
                          caption=f"👤 Хедшот <b>{safe_html(user.get('displayName','?'))}</b>",
                          parse_mode="HTML")
        else:
            bot.reply_to(m, "❌ Хедшот недоступен")
    except RBXError as e:
        bot.reply_to(m, f"❌ {e}")


# ─── /friends, /groups, /badges ───
@bot.message_handler(commands=["friends"], func=private_only)
def cmd_friends(m):
    args = m.text.split(maxsplit=1)
    if len(args) < 2:
        return bot.reply_to(m, "❌ <code>/friends Roblox</code>",
                            parse_mode="HTML")
    cd = check_cooldown(m.from_user.id)
    if cd:
        return bot.reply_to(m, f"⏳ Подожди {cd} сек.")
    try:
        user = get_user_by_input(args[1])
        text, kb = build_friends_text(user["id"], 0)
        bot.send_message(m.chat.id, text, parse_mode="HTML",
                        reply_markup=kb)
    except RBXError as e:
        bot.reply_to(m, f"❌ {e}")


@bot.message_handler(commands=["groups"], func=private_only)
def cmd_groups(m):
    args = m.text.split(maxsplit=1)
    if len(args) < 2:
        return bot.reply_to(m, "❌ <code>/groups Roblox</code>",
                            parse_mode="HTML")
    cd = check_cooldown(m.from_user.id)
    if cd:
        return bot.reply_to(m, f"⏳ Подожди {cd} сек.")
    try:
        user = get_user_by_input(args[1])
        text, kb = build_groups_text(user["id"], 0)
        bot.send_message(m.chat.id, text, parse_mode="HTML",
                        reply_markup=kb)
    except RBXError as e:
        bot.reply_to(m, f"❌ {e}")


@bot.message_handler(commands=["badges"], func=private_only)
def cmd_badges(m):
    args = m.text.split(maxsplit=1)
    if len(args) < 2:
        return bot.reply_to(m, "❌ <code>/badges Roblox</code>",
                            parse_mode="HTML")
    cd = check_cooldown(m.from_user.id)
    if cd:
        return bot.reply_to(m, f"⏳ Подожди {cd} сек.")
    try:
        user = get_user_by_input(args[1])
        text, kb = build_badges_text(user["id"], 0)
        bot.send_message(m.chat.id, text, parse_mode="HTML",
                        reply_markup=kb)
    except RBXError as e:
        bot.reply_to(m, f"❌ {e}")


# ─── /online ───
@bot.message_handler(commands=["online"], func=private_only)
def cmd_online(m):
    args = m.text.split(maxsplit=1)
    if len(args) < 2:
        return bot.reply_to(m, "❌ <code>/online Roblox</code>",
                            parse_mode="HTML")
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
        bot.send_message(m.chat.id, text, parse_mode="HTML",
                        disable_web_page_preview=True)
    except RBXError as e:
        bot.reply_to(m, f"❌ {e}")


# ─── /search ───
@bot.message_handler(commands=["search"], func=private_only)
def cmd_search(m):
    args = m.text.split(maxsplit=1)
    if len(args) < 2:
        return bot.reply_to(m, "❌ <code>/search Builderman</code>",
                            parse_mode="HTML")
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
        bot.send_message(m.chat.id, "\n".join(lines),
                        parse_mode="HTML")
    except RBXError as e:
        bot.reply_to(m, f"❌ {e}")


# ─── /game ───
@bot.message_handler(commands=["game"], func=private_only)
def cmd_game(m):
    args = m.text.split(maxsplit=1)
    if len(args) < 2 or not args[1].strip().isdigit():
        return bot.reply_to(m,
            "❌ <code>/game 286090429</code> (placeId или universeId)",
            parse_mode="HTML")
    cd = check_cooldown(m.from_user.id)
    if cd:
        return bot.reply_to(m, f"⏳ Подожди {cd} сек.")
    w = bot.reply_to(m, "⏳ Загрузка...")
    try:
        gid = int(args[1].strip())
        game = None
        try:
            game = get_game_info(gid)
        except RBXError:
            pass
        if not game:
            try:
                uid = place_to_universe(gid)
                game = get_game_info(uid)
            except RBXError:
                raise RBXError(f"Игра {gid} не найдена. /searchgame")
        uid = game.get("id", gid)
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
                "Открыть</a>"
            )
        if thumb:
            bot.send_photo(m.chat.id, thumb, caption=text,
                          parse_mode="HTML")
        else:
            bot.send_message(m.chat.id, text, parse_mode="HTML",
                            disable_web_page_preview=True)
        bot.delete_message(m.chat.id, w.message_id)
    except RBXError as e:
        bot.edit_message_text(f"❌ {e}", m.chat.id, w.message_id)
    except Exception as e:
        log.exception("/game error")
        bot.edit_message_text(f"❌ {e}", m.chat.id, w.message_id)


# ─── /searchgame ───
@bot.message_handler(commands=["searchgame"], func=private_only)
def cmd_searchgame(m):
    args = m.text.split(maxsplit=1)
    if len(args) < 2:
        return bot.reply_to(m, "❌ <code>/searchgame Adopt Me</code>",
                            parse_mode="HTML")
    cd = check_cooldown(m.from_user.id)
    if cd:
        return bot.reply_to(m, f"⏳ Подожди {cd} сек.")
    w = bot.reply_to(m, "🔍 Ищу...")
    try:
        games = search_games(args[1], limit=10)
        if not games:
            return bot.edit_message_text("🔍 Ничего",
                                        m.chat.id, w.message_id)
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
        bot.edit_message_text("\n".join(lines), m.chat.id,
                             w.message_id, parse_mode="HTML")
    except RBXError as e:
        bot.edit_message_text(f"❌ {e}", m.chat.id, w.message_id)


# ─── /group ───
@bot.message_handler(commands=["group"], func=private_only)
def cmd_group(m):
    args = m.text.split(maxsplit=1)
    if len(args) < 2 or not args[1].strip().isdigit():
        return bot.reply_to(m, "❌ <code>/group 1</code>",
                            parse_mode="HTML")
    cd = check_cooldown(m.from_user.id)
    if cd:
        return bot.reply_to(m, f"⏳ Подожди {cd} сек.")
    try:
        gid = int(args[1].strip())
        g = get_group_info(gid)
        owner = g.get("owner") or {}
        verified = " ✅" if g.get("hasVerifiedBadge") else ""
        public = "✅" if g.get("publicEntryAllowed") else "❌"
        locked = "🔒 Да" if g.get("isLocked") else "✅ Нет"
        text = (
            f"👥 <b>{safe_html(g.get('name', '?'))}</b>{verified}\n\n"
            f"├ ID: <code>{gid}</code>\n"
            f"├ 👑 Владелец: <b>{safe_html(owner.get('username', '—'))}</b>\n"
            f"├ 👤 Участников: <b>{fmt_num(g.get('memberCount', 0))}</b>\n"
            f"├ 🔓 Открытая: {public}\n"
            f"└ 🔒 Заблокирована: {locked}"
        )
        desc = g.get("description", "")
        if desc:
            text += f"\n\n📝 <i>{safe_html(desc[:300])}</i>"
        text += (
            f'\n\n🔗 <a href="https://www.roblox.com/groups/{gid}">'
            "Открыть</a>"
        )
        bot.send_message(m.chat.id, text, parse_mode="HTML",
                        disable_web_page_preview=True)
    except RBXError as e:
        bot.reply_to(m, f"❌ {e}")


# ─── /asset ───
@bot.message_handler(commands=["asset"], func=private_only)
def cmd_asset(m):
    args = m.text.split(maxsplit=1)
    if len(args) < 2 or not args[1].strip().isdigit():
        return bot.reply_to(m, "❌ <code>/asset 607785314</code>",
                            parse_mode="HTML")
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
            "Открыть</a>"
        )
        kb = None
        if type_id in DOWNLOADABLE:
            kb = types.InlineKeyboardMarkup()
            kb.add(types.InlineKeyboardButton(
                "📥 Скачать", callback_data=f"dl:{aid}"))
        if thumb:
            bot.send_photo(m.chat.id, thumb, caption=text,
                          parse_mode="HTML", reply_markup=kb)
        else:
            bot.send_message(m.chat.id, text, parse_mode="HTML",
                            disable_web_page_preview=True,
                            reply_markup=kb)
        bot.delete_message(m.chat.id, w.message_id)
    except RBXError as e:
        bot.edit_message_text(f"❌ {e}", m.chat.id, w.message_id)


# ─── /download ───
@bot.message_handler(commands=["download"], func=private_only)
def cmd_download(m):
    args = m.text.split(maxsplit=1)
    if len(args) < 2 or not args[1].strip().isdigit():
        return bot.reply_to(m, "❌ <code>/download 607785314</code>",
                            parse_mode="HTML")
    cd = check_cooldown(m.from_user.id)
    if cd:
        return bot.reply_to(m, f"⏳ Подожди {cd} сек.")
    do_download(m.chat.id, int(args[1].strip()))


def do_download(chat_id, asset_id):
    w = bot.send_message(chat_id, f"📥 Скачиваю {asset_id}...")
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
                "❌ Не удалось скачать", chat_id, w.message_id)

        ext = ASSET_EXT.get(type_id, ".bin")
        type_name = ASSET_TYPES.get(type_id, "Unknown")

        # Clothing / Decal: извлечь текстуру
        if type_id in (2, 11, 12, 13):
            txt = data.decode("utf-8", errors="ignore")
            url_match = re.search(r"<url>([^<]+)</url>", txt)
            if not url_match:
                url_match = re.search(r'(https?://[^"<>\s]+)', txt)
            if url_match:
                tex_url = url_match.group(1).replace("http://", "https://")
                if "rbxassetid://" in tex_url:
                    tid = re.search(r"(\d+)", tex_url)
                    if tid:
                        tex_url = (
                            "https://assetdelivery.roblox.com"
                            f"/v1/asset/?id={tid.group(1)}"
                        )
                tr = raw_get(tex_url, timeout=30, allow_redirects=True)
                if tr and tr.status_code == 200 and len(tr.content) > 100:
                    data = tr.content
                    ext = ".png"

        # Audio
        if type_id == 3:
            if data[:4] == b"OggS":
                ext = ".ogg"
            elif data[:3] == b"ID3" or data[:2] == b"\xff\xfb":
                ext = ".mp3"
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
            bot.send_audio(chat_id, file_obj, caption=caption,
                          parse_mode="HTML")
        elif ext == ".png" and len(data) < 10 * 1024 * 1024:
            try:
                bot.send_photo(chat_id, io.BytesIO(data),
                              caption=caption, parse_mode="HTML")
            except Exception:
                file_obj.seek(0)
                bot.send_document(chat_id, file_obj,
                                 caption=caption, parse_mode="HTML")
        else:
            bot.send_document(chat_id, file_obj, caption=caption,
                             parse_mode="HTML")
        bot.delete_message(chat_id, w.message_id)
    except RBXError as e:
        bot.edit_message_text(f"❌ {e}", chat_id, w.message_id)
    except Exception as e:
        log.exception("download error")
        bot.edit_message_text(f"❌ {e}", chat_id, w.message_id)


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
        threading.Thread(target=do_download,
                        args=(call.message.chat.id, int(parts[1])),
                        daemon=True).start()
        return

    # Пагинация
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
                bot.answer_callback_query(call.id, "❌ ?")
                return

            msg = call.message
            if msg.content_type != "text":
                try:
                    bot.delete_message(msg.chat.id, msg.message_id)
                except Exception:
                    pass
                bot.send_message(msg.chat.id, text,
                                parse_mode="HTML", reply_markup=kb)
            else:
                bot.edit_message_text(text, msg.chat.id,
                                    msg.message_id,
                                    parse_mode="HTML",
                                    reply_markup=kb)
            bot.answer_callback_query(call.id)
        except telebot.apihelper.ApiTelegramException as e:
            if "message is not modified" in str(e):
                bot.answer_callback_query(call.id,
                                         "Уже на этой странице")
            else:
                bot.answer_callback_query(call.id, f"❌ {e}")
        except RBXError as e:
            bot.answer_callback_query(call.id, f"❌ {e}",
                                     show_alert=True)
        except Exception as e:
            log.exception("Callback error")
            bot.answer_callback_query(call.id, f"❌ {e}")


# ─── Неизвестные команды ───
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
    log.info("  RBX Info Bot v4")
    log.info("  Cooldown: %ds | Page: %d", COOLDOWN_SEC, PAGE_SIZE)
    if proxy_pool.active:
        log.info("  Proxies: %d (ротация)", proxy_pool.count)
    else:
        log.info("  Proxy: нет")
    log.info("=" * 40)
    bot.infinity_polling(
        timeout=30,
        long_polling_timeout=25,
        allowed_updates=["message", "callback_query"],
    )
