import asyncio
import json
import os
import random
import time
import logging

from aiogram import Bot, Dispatcher, Router, F
from aiogram.filters import Command
from aiogram.types import Message
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties

# ====================== НАСТРОЙКИ ======================

BOT_TOKEN = "8693997731:AAH9h0QzXksHXnCEX9a01g3q6R7S0X8bjB4"
ALLOWED_GROUP_ID = -1002707885564  # ID твоей группы
COOLDOWN_SECONDS = 300             # 5 минут
MIN_LEVEL_UP = 1
MAX_LEVEL_UP = 5
DATA_FILE = "curator_data.json"

# ====================== ХРАНИЛИЩЕ ======================

class Storage:
    def __init__(self, filepath: str = DATA_FILE):
        self.filepath = filepath
        self.data: dict = {}
        self._load()

    def _load(self):
        if os.path.exists(self.filepath):
            with open(self.filepath, "r", encoding="utf-8") as f:
                self.data = json.load(f)

    def _save(self):
        with open(self.filepath, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=2)

    def get_level(self, user_id: int) -> int:
        user = self.data.get(str(user_id))
        return user["level"] if user else 0

    def add_level(self, user_id: int, name: str, amount: int) -> int:
        uid = str(user_id)
        if uid not in self.data:
            self.data[uid] = {"name": name, "level": 0}
        self.data[uid]["level"] += amount
        self.data[uid]["name"] = name
        self._save()
        return self.data[uid]["level"]

    def get_top(self, limit: int = 10) -> list[tuple[str, int]]:
        users = [(info["name"], info["level"]) for info in self.data.values()]
        users.sort(key=lambda x: x[1], reverse=True)
        return users[:limit]

# ====================== РАНГИ ======================

def get_rank(level: int) -> str:
    if level >= 500:
        return "👑 Легендарный куратор"
    elif level >= 300:
        return "🔥 Мастер-куратор"
    elif level >= 150:
        return "⭐ Старший куратор"
    elif level >= 75:
        return "📋 Куратор"
    elif level >= 30:
        return "📎 Младший куратор"
    elif level >= 10:
        return "📝 Стажёр"
    return "🌱 Новичок"

# ====================== БОТ ======================

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("curator_bot")

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()
router = Router()

storage = Storage()
cooldowns: dict[int, float] = {}


def is_allowed(msg: Message) -> bool:
    return msg.chat.id == ALLOWED_GROUP_ID


def remaining_cd(user_id: int) -> int:
    last = cooldowns.get(user_id, 0)
    diff = time.time() - last
    return 0 if diff >= COOLDOWN_SECONDS else int(COOLDOWN_SECONDS - diff)


# ---------- /curator ----------

@router.message(Command("curator"))
async def cmd_curator(message: Message):
    if not is_allowed(message):
        return

    user_id = message.from_user.id
    name = message.from_user.full_name

    wait = remaining_cd(user_id)
    if wait > 0:
        m, s = divmod(wait, 60)
        await message.reply(
            f"⏳ <b>{name}</b>, подождите <b>{m}м {s}с</b> перед следующей прокачкой!"
        )
        return

    increase = random.randint(MIN_LEVEL_UP, MAX_LEVEL_UP)
    new_level = storage.add_level(user_id, name, increase)
    cooldowns[user_id] = time.time()

    rank = get_rank(new_level)

    await message.reply(
        f"💼 <b>{name}</b>, уровень кураторства повышен! (+{increase})\n\n"
        f"⭐ Новый уровень: <b>{new_level}</b>\n"
        f"🏅 Ранг: <b>{rank}</b>"
    )


# ---------- /top ----------

@router.message(Command("top"))
async def cmd_top(message: Message):
    if not is_allowed(message):
        return

    top = storage.get_top(10)

    if not top:
        await message.reply("📊 Таблица пока пуста. Напишите /curator чтобы начать!")
        return

    medals = ["🥇", "🥈", "🥉"]
    lines = []

    for i, (name, level) in enumerate(top):
        prefix = medals[i] if i < 3 else f"  {i + 1}."
        rank = get_rank(level)
        lines.append(f"{prefix} <b>{name}</b> — ур. <b>{level}</b> ({rank})")

    text = "🏆 <b>Топ-10 кураторов</b>\n" + "━" * 26 + "\n\n" + "\n".join(lines)
    await message.reply(text)


# ---------- /my_level ----------

@router.message(Command("my_level", "mylevel", "me"))
async def cmd_my_level(message: Message):
    if not is_allowed(message):
        return

    user_id = message.from_user.id
    name = message.from_user.full_name
    level = storage.get_level(user_id)

    if level == 0:
        await message.reply(
            f"🚀 <b>{name}</b>, вы ещё не начали прокачку!\nНапишите /curator"
        )
        return

    rank = get_rank(level)
    await message.reply(
        f"👤 <b>{name}</b>\n\n"
        f"⭐ Уровень: <b>{level}</b>\n"
        f"🏅 Ранг: <b>{rank}</b>"
    )


# ---------- /help ----------

@router.message(Command("help", "start"))
async def cmd_help(message: Message):
    if not is_allowed(message):
        return

    await message.reply(
        "💼 <b>Бот прокачки кураторства</b>\n"
        "━" * 28 + "\n\n"
        "/curator — прокачать уровень (кд 5 мин)\n"
        "/top — топ-10 кураторов\n"
        "/my_level — ваш уровень и ранг\n"
        "/help — список команд"
    )


# ====================== ЗАПУСК ======================

dp.include_router(router)


async def main():
    log.info("Бот запущен. Группа: %s", ALLOWED_GROUP_ID)
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())