import asyncio
import random
import time
import logging

import aiosqlite
from aiogram import Bot, Dispatcher, Router
from aiogram.filters import Command
from aiogram.types import Message
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties

# ======================== НАСТРОЙКИ ========================

BOT_TOKEN = "8693997731:AAH9h0QzXksHXnCEX9a01g3q6R7S0X8bjB4"
COOLDOWN_SECONDS = 300  # 5 минут
MIN_LEVEL_UP = 1
MAX_LEVEL_UP = 10
DB_FILE = "curator.db"

# ======================== РАНГИ ========================

RANKS = [
    (0,    "🌱 Новичок"),
    (10,   "📝 Стажёр"),
    (30,   "📎 Младший куратор"),
    (75,   "📋 Куратор"),
    (150,  "⭐ Старший куратор"),
    (300,  "🔥 Мастер-куратор"),
    (500,  "👑 Легендарный куратор"),
    (1000, "💎 Мифический куратор"),
]


def get_rank(level: int) -> str:
    rank = RANKS[0][1]
    for threshold, name in RANKS:
        if level >= threshold:
            rank = name
    return rank


def get_next_rank(level: int):
    """Возвращает (название, порог) следующего ранга или None"""
    for threshold, name in RANKS:
        if level < threshold:
            return name, threshold
    return None


def get_prev_threshold(level: int) -> int:
    prev = 0
    for threshold, _ in RANKS:
        if level >= threshold:
            prev = threshold
    return prev


def progress_bar(current: int, target: int, length: int = 15) -> str:
    if target <= 0:
        return "▓" * length
    filled = min(int(current / target * length), length)
    return "▓" * filled + "░" * (length - filled)


# ======================== БАЗА ДАННЫХ (SQLite) ========================

class Database:
    def __init__(self, path: str):
        self.path = path
        self.db: aiosqlite.Connection | None = None

    async def connect(self):
        self.db = await aiosqlite.connect(self.path)
        self.db.row_factory = aiosqlite.Row
        await self.db.execute("PRAGMA journal_mode=WAL")
        await self.db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id    INTEGER NOT NULL,
                chat_id    INTEGER NOT NULL,
                name       TEXT    NOT NULL,
                level      INTEGER DEFAULT 0,
                rolls      INTEGER DEFAULT 0,
                best_roll  INTEGER DEFAULT 0,
                last_roll  REAL    DEFAULT 0,
                created_at TEXT    DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (user_id, chat_id)
            )
        """)
        await self.db.commit()

    async def close(self):
        if self.db:
            await self.db.close()

    # --- пользователь ---

    async def get_user(self, uid: int, cid: int) -> dict | None:
        cur = await self.db.execute(
            "SELECT * FROM users WHERE user_id=? AND chat_id=?", (uid, cid)
        )
        row = await cur.fetchone()
        return dict(row) if row else None

    async def add_level(self, uid: int, cid: int, name: str, amount: int) -> tuple[int, dict]:
        user = await self.get_user(uid, cid)
        now = time.time()

        if user:
            new_level = user["level"] + amount
            best = max(user["best_roll"], amount)
            await self.db.execute(
                """UPDATE users
                   SET name=?, level=?, rolls=rolls+1, best_roll=?, last_roll=?
                   WHERE user_id=? AND chat_id=?""",
                (name, new_level, best, now, uid, cid),
            )
        else:
            new_level = amount
            await self.db.execute(
                """INSERT INTO users (user_id,chat_id,name,level,rolls,best_roll,last_roll)
                   VALUES (?,?,?,?,1,?,?)""",
                (uid, cid, name, amount, amount, now),
            )

        await self.db.commit()
        return new_level, await self.get_user(uid, cid)

    # --- топ ---

    async def get_top(self, cid: int, limit: int = 10) -> list[dict]:
        cur = await self.db.execute(
            "SELECT name,level,rolls,best_roll FROM users WHERE chat_id=? ORDER BY level DESC LIMIT ?",
            (cid, limit),
        )
        return [dict(r) for r in await cur.fetchall()]

    # --- позиция ---

    async def get_position(self, uid: int, cid: int) -> tuple[int, int]:
        """Возвращает (место, всего игроков)"""
        user = await self.get_user(uid, cid)
        lvl = user["level"] if user else 0

        cur = await self.db.execute(
            "SELECT COUNT(*) as c FROM users WHERE chat_id=? AND level>?", (cid, lvl)
        )
        pos = (await cur.fetchone())["c"] + 1

        cur2 = await self.db.execute(
            "SELECT COUNT(*) as c FROM users WHERE chat_id=?", (cid,)
        )
        total = (await cur2.fetchone())["c"]
        return pos, total

    # --- статистика чата ---

    async def chat_stats(self, cid: int) -> dict:
        cur = await self.db.execute(
            """SELECT
                 COUNT(*)          AS players,
                 COALESCE(SUM(level),0)  AS total_lvl,
                 COALESCE(MAX(level),0)  AS max_lvl,
                 COALESCE(SUM(rolls),0)  AS total_rolls,
                 COALESCE(MAX(best_roll),0) AS best_ever
               FROM users WHERE chat_id=?""",
            (cid,),
        )
        return dict(await cur.fetchone())


# ======================== БОТ ========================

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("curator")

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()
router = Router()
db = Database(DB_FILE)

cooldowns: dict[tuple[int, int], float] = {}  # (user_id, chat_id) → timestamp


def remaining_cd(uid: int, cid: int) -> int:
    last = cooldowns.get((uid, cid), 0)
    diff = time.time() - last
    return 0 if diff >= COOLDOWN_SECONDS else int(COOLDOWN_SECONDS - diff)


def only_group(msg: Message) -> bool:
    return msg.chat.type in ("group", "supergroup")


# ---------- /curator ----------

@router.message(Command("curator"))
async def cmd_curator(message: Message):
    if not only_group(message):
        await message.reply("🚫 Команда работает только в группах!")
        return

    uid = message.from_user.id
    cid = message.chat.id
    name = message.from_user.full_name

    wait = remaining_cd(uid, cid)
    if wait > 0:
        m, s = divmod(wait, 60)
        await message.reply(
            f"⏳ <b>{name}</b>, подождите <b>{m}м {s}с</b> до следующей прокачки!"
        )
        return

    increase = random.randint(MIN_LEVEL_UP, MAX_LEVEL_UP)
    new_level, user = await db.add_level(uid, cid, name, increase)
    cooldowns[(uid, cid)] = time.time()

    rank = get_rank(new_level)
    nxt = get_next_rank(new_level)
    prev_t = get_prev_threshold(new_level)

    text = (
        f"💼 <b>{name}</b>, уровень кураторства повышен! <b>(+{increase})</b>\n\n"
        f"⭐ Уровень: <b>{new_level}</b>\n"
        f"🏅 Ранг: <b>{rank}</b>\n"
        f"🎲 Прокачек: <b>{user['rolls']}</b>\n"
        f"🎯 Лучший бросок: <b>+{user['best_roll']}</b>"
    )

    if nxt:
        nxt_name, nxt_thresh = nxt
        done = new_level - prev_t
        size = nxt_thresh - prev_t
        left = nxt_thresh - new_level
        bar = progress_bar(done, size)
        text += f"\n\n📊 До «{nxt_name}»:\n{bar}  осталось <b>{left}</b>"
    else:
        text += "\n\n🌟 Максимальный ранг достигнут!"

    if increase == MAX_LEVEL_UP:
        text += "\n\n🍀 Критический бросок! Максимум!"
    elif increase >= 8:
        text += "\n\n🍀 Отличный бросок!"

    await message.reply(text)


# ---------- /top ----------

@router.message(Command("top"))
async def cmd_top(message: Message):
    if not only_group(message):
        await message.reply("🚫 Команда работает только в группах!")
        return

    top = await db.get_top(message.chat.id, 10)
    if not top:
        await message.reply("📊 Пока никто не прокачивался. Напишите /curator!")
        return

    medals = ["🥇", "🥈", "🥉"]
    lines = []
    for i, u in enumerate(top):
        prefix = medals[i] if i < 3 else f"  <b>{i + 1}.</b>"
        rank = get_rank(u["level"])
        lines.append(
            f'{prefix} <b>{u["name"]}</b> — ур. <b>{u["level"]}</b>\n'
            f'       {rank} · прокачек: {u["rolls"]} · рекорд: +{u["best_roll"]}'
        )

    st = await db.chat_stats(message.chat.id)
    avg = st["total_lvl"] / st["players"] if st["players"] else 0

    text = (
        "🏆 <b>Топ-10 кураторов чата</b>\n"
        + "━" * 28 + "\n\n"
        + "\n\n".join(lines)
        + "\n\n" + "━" * 28
        + f"\n👥 <b>{st['players']}</b> игроков · "
        + f"средний ур. <b>{avg:.1f}</b> · "
        + f"рекорд чата <b>+{st['best_ever']}</b>"
    )

    await message.reply(text)


# ---------- /my_level ----------

@router.message(Command("my_level", "mylevel", "me"))
async def cmd_my_level(message: Message):
    if not only_group(message):
        await message.reply("🚫 Команда работает только в группах!")
        return

    uid = message.from_user.id
    cid = message.chat.id
    name = message.from_user.full_name
    user = await db.get_user(uid, cid)

    if not user:
        await message.reply(f"🚀 <b>{name}</b>, вы ещё не начали! Напишите /curator")
        return

    level = user["level"]
    rank = get_rank(level)
    pos, total = await db.get_position(uid, cid)
    prev_t = get_prev_threshold(level)
    nxt = get_next_rank(level)

    text = (
        f"👤 <b>{name}</b>\n"
        + "━" * 28 + "\n\n"
        + f"⭐ Уровень: <b>{level}</b>\n"
        + f"🏅 Ранг: <b>{rank}</b>\n"
        + f"🏆 Место: <b>{pos}/{total}</b>\n"
        + f"🎲 Прокачек: <b>{user['rolls']}</b>\n"
        + f"🎯 Лучший бросок: <b>+{user['best_roll']}</b>"
    )

    if nxt:
        nxt_name, nxt_thresh = nxt
        done = level - prev_t
        size = nxt_thresh - prev_t
        left = nxt_thresh - level
        bar = progress_bar(done, size)
        text += f"\n\n📊 До «{nxt_name}»:\n{bar}  осталось <b>{left}</b>"
    else:
        text += "\n\n🌟 Вы на вершине! Максимальный ранг!"

    await message.reply(text)


# ---------- /stats ----------

@router.message(Command("stats"))
async def cmd_stats(message: Message):
    if not only_group(message):
        await message.reply("🚫 Команда работает только в группах!")
        return

    st = await db.chat_stats(message.chat.id)
    if st["players"] == 0:
        await message.reply("📊 Статистика пуста. Напишите /curator!")
        return

    avg = st["total_lvl"] / st["players"]
    await message.reply(
        "📊 <b>Статистика чата</b>\n"
        + "━" * 28 + "\n\n"
        + f"👥 Игроков: <b>{st['players']}</b>\n"
        + f"📈 Общий уровень: <b>{st['total_lvl']}</b>\n"
        + f"📉 Средний уровень: <b>{avg:.1f}</b>\n"
        + f"🏔 Максимальный: <b>{st['max_lvl']}</b>\n"
        + f"🎲 Всего прокачек: <b>{st['total_rolls']}</b>\n"
        + f"🎯 Лучший бросок: <b>+{st['best_ever']}</b>"
    )


# ---------- /help ----------

@router.message(Command("help", "start"))
async def cmd_help(message: Message):
    await message.reply(
        "💼 <b>Бот прокачки кураторства</b>\n"
        + "━" * 28 + "\n\n"
        + "🎲 /curator — прокачать уровень (+1‑10, кд 5 мин)\n"
        + "🏆 /top — топ-10 кураторов чата\n"
        + "👤 /my_level — ваш профиль\n"
        + "📊 /stats — статистика чата\n"
        + "❓ /help — это сообщение\n\n"
        + "<b>Как играть:</b>\n"
        + "1. Добавьте бота в группу\n"
        + "2. Пишите /curator каждые 5 минут\n"
        + "3. Получайте от 1 до 10 очков за раз\n"
        + "4. Поднимайтесь в рангах и боритесь за топ! 🚀"
    )


# ======================== ЗАПУСК ========================

dp.include_router(router)


async def main():
    await db.connect()
    log.info("SQLite подключена: %s", DB_FILE)
    log.info("Бот запущен, работает во всех группах")
    try:
        await bot.delete_webhook(drop_pending_updates=True)
        await dp.start_polling(bot)
    finally:
        await db.close()
        log.info("БД закрыта")


if __name__ == "__main__":
    asyncio.run(main())