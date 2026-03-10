import asyncio
import random
import time
import logging
import math

import aiosqlite
from aiogram import Bot, Dispatcher, Router, F
from aiogram.filters import Command, CommandObject
from aiogram.types import (
    Message, CallbackQuery,
    InlineKeyboardMarkup, InlineKeyboardButton,
)
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties

# ======================== НАСТРОЙКИ ========================

BOT_TOKEN = "8693997731:AAH9h0QzXksHXnCEX9a01g3q6R7S0X8bjB4"
COOLDOWN_SECONDS = 150       # 2.5 минуты
MIN_LEVEL_UP = 1
MAX_LEVEL_UP = 10
DB_FILE = "curator.db"
MINES_TIMEOUT = 600          # 10 мин — мины сгорают
CAPTCHA_CHANCE = 0.25        # 25 % шанс капчи
CAPTCHA_TIMEOUT = 30         # 30 с на ответ
DAILY_BONUS = 100
DUEL_TIMEOUT = 120           # 2 мин на принятие

# ======================== 40 РАНГОВ ========================

RANKS = [
    (0,                    "🌱 Новичок"),
    (10,                   "📝 Стажёр"),
    (30,                   "📎 Младший куратор"),
    (75,                   "📋 Куратор"),
    (150,                  "⭐ Старший куратор"),
    (300,                  "🔥 Мастер-куратор"),
    (500,                  "👑 Легендарный куратор"),
    (1_000,                "💎 Мифический куратор"),
    (2_000,                "🌀 Элитный куратор"),
    (3_500,                "🏰 Великий куратор"),
    (5_000,                "⚔️ Эпический куратор"),
    (7_500,                "🛡️ Несокрушимый куратор"),
    (10_000,               "🌌 Космический куратор"),
    (15_000,               "⚡ Громовержец"),
    (20_000,               "🔱 Повелитель"),
    (30_000,               "🐉 Драконий куратор"),
    (50_000,               "☄️ Бессмертный"),
    (75_000,               "🌠 Божественный куратор"),
    (100_000,              "👁 Титан кураторства"),
    (150_000,              "🔮 Оракул"),
    (200_000,              "💫 Астральный куратор"),
    (300_000,              "🌍 Хранитель мира"),
    (500_000,              "🌟 Абсолют"),
    (750_000,              "✨ Демиург"),
    (1_000_000,            "🪐 Создатель миров"),
    (2_000_000,            "🌋 Разрушитель миров"),
    (5_000_000,            "🕳️ Повелитель пустоты"),
    (10_000_000,           "🔆 Вечный свет"),
    (25_000_000,           "🌊 Властелин стихий"),
    (50_000_000,           "⏳ Хранитель времени"),
    (100_000_000,          "🧬 Архитектор реальности"),
    (250_000_000,          "🎆 Сверхновая"),
    (500_000_000,          "🎭 Дуалист измерений"),
    (1_000_000_000,        "💀 Вне смертности"),
    (5_000_000_000,        "🕊️ Трансцендент"),
    (10_000_000_000,       "🔷 Кристалл вечности"),
    (50_000_000_000,       "🌑 Тёмная материя"),
    (100_000_000_000,      "☀️ Солнечный император"),
    (500_000_000_000,      "🌌 Квазар"),
    (1_000_000_000_000,    "♾️ Бесконечность"),
]


def get_rank(level: int) -> str:
    r = RANKS[0][1]
    for t, n in RANKS:
        if level >= t:
            r = n
    return r


def get_next_rank(level: int):
    for t, n in RANKS:
        if level < t:
            return n, t
    return None


def prev_threshold(level: int) -> int:
    p = 0
    for t, _ in RANKS:
        if level >= t:
            p = t
    return p


def progress_bar(cur: int, total: int, ln: int = 15) -> str:
    if total <= 0:
        return "▓" * ln
    f_ = min(int(cur / total * ln), ln)
    return "▓" * f_ + "░" * (ln - f_)


def fmt(n: int) -> str:
    if n < 0:
        return "-" + fmt(-n)
    return f"{n:,}".replace(",", " ")


# ======================== МИНЫ ========================

class MinesGame:
    def __init__(self, uid, cid, bet, mines):
        self.uid, self.cid, self.bet = uid, cid, bet
        self.mines_count, self.size = mines, 5
        self.total = self.size ** 2
        self.grid = [[False]*self.size for _ in range(self.size)]
        self.revealed = [[False]*self.size for _ in range(self.size)]
        self.safe_found, self.active, self.exploded = 0, True, False
        self.created = time.time()
        for r, c in random.sample(
            [(r, c) for r in range(self.size) for c in range(self.size)], mines
        ):
            self.grid[r][c] = True

    @property
    def expired(self):
        return time.time() - self.created > MINES_TIMEOUT

    def reveal(self, r, c):
        if self.revealed[r][c]:
            return "already"
        self.revealed[r][c] = True
        if self.grid[r][c]:
            self.active, self.exploded = False, True
            return "mine"
        self.safe_found += 1
        if self.safe_found == self.total - self.mines_count:
            self.active = False
        return "safe"

    @property
    def multiplier(self):
        if self.safe_found == 0:
            return 1.0
        prob = 1.0
        safe = self.total - self.mines_count
        for i in range(self.safe_found):
            prob *= (safe - i) / (self.total - i)
        return min(round(0.97 / prob, 2), 999.0) if prob > 0 else 999.0

    @property
    def winnings(self):
        return max(int(self.bet * self.multiplier), 1)

    @property
    def profit(self):
        return self.winnings - self.bet

    def keyboard(self, show_all=False):
        rows = []
        for r in range(self.size):
            row = []
            for c in range(self.size):
                if show_all or self.revealed[r][c]:
                    t = "💣" if self.grid[r][c] else ("💚" if self.revealed[r][c] else "⬜")
                    row.append(InlineKeyboardButton(text=t, callback_data="noop"))
                else:
                    row.append(InlineKeyboardButton(
                        text="⬛", callback_data=f"m:{self.uid}:{r}:{c}"))
            rows.append(row)
        if self.active and self.safe_found > 0:
            rows.append([InlineKeyboardButton(
                text=f"💰 Забрать {fmt(self.winnings)} (x{self.multiplier})",
                callback_data=f"mc:{self.uid}")])
        return InlineKeyboardMarkup(inline_keyboard=rows)


# ======================== БАЗА ДАННЫХ ========================

class Database:
    def __init__(self, path):
        self.path = path
        self.db = None

    async def connect(self):
        self.db = await aiosqlite.connect(self.path)
        self.db.row_factory = aiosqlite.Row
        await self.db.execute("PRAGMA journal_mode=WAL")
        await self.db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id     INTEGER NOT NULL,
                chat_id     INTEGER NOT NULL,
                name        TEXT    NOT NULL,
                level       INTEGER DEFAULT 0,
                rolls       INTEGER DEFAULT 0,
                best_roll   INTEGER DEFAULT 0,
                last_roll   REAL    DEFAULT 0,
                games_won   INTEGER DEFAULT 0,
                games_lost  INTEGER DEFAULT 0,
                total_bet   INTEGER DEFAULT 0,
                total_won   INTEGER DEFAULT 0,
                last_daily  REAL    DEFAULT 0,
                duels_won   INTEGER DEFAULT 0,
                duels_lost  INTEGER DEFAULT 0,
                captchas_ok INTEGER DEFAULT 0,
                PRIMARY KEY (user_id, chat_id)
            )
        """)
        await self.db.commit()
        await self._migrate()

    async def _migrate(self):
        cols = [
            ("last_daily",  "REAL DEFAULT 0"),
            ("duels_won",   "INTEGER DEFAULT 0"),
            ("duels_lost",  "INTEGER DEFAULT 0"),
            ("captchas_ok", "INTEGER DEFAULT 0"),
        ]
        for name, td in cols:
            try:
                await self.db.execute(f"ALTER TABLE users ADD COLUMN {name} {td}")
            except Exception:
                pass
        await self.db.commit()

    async def close(self):
        if self.db:
            await self.db.close()

    async def ensure(self, uid, cid, name):
        cur = await self.db.execute(
            "SELECT 1 FROM users WHERE user_id=? AND chat_id=?", (uid, cid))
        if not await cur.fetchone():
            await self.db.execute(
                "INSERT INTO users(user_id,chat_id,name) VALUES(?,?,?)", (uid, cid, name))
        else:
            await self.db.execute(
                "UPDATE users SET name=? WHERE user_id=? AND chat_id=?", (name, uid, cid))
        await self.db.commit()

    async def get_user(self, uid, cid):
        cur = await self.db.execute(
            "SELECT * FROM users WHERE user_id=? AND chat_id=?", (uid, cid))
        row = await cur.fetchone()
        return dict(row) if row else None

    async def get_level(self, uid, cid):
        u = await self.get_user(uid, cid)
        return u["level"] if u else 0

    async def curator_roll(self, uid, cid, name, amount):
        await self.ensure(uid, cid, name)
        u = await self.get_user(uid, cid)
        nl = u["level"] + amount
        br = max(u["best_roll"], amount)
        await self.db.execute(
            """UPDATE users SET level=?, rolls=rolls+1, best_roll=?, last_roll=?
               WHERE user_id=? AND chat_id=?""",
            (nl, br, time.time(), uid, cid))
        await self.db.commit()
        return nl, await self.get_user(uid, cid)

    async def change_level(self, uid, cid, delta):
        u = await self.get_user(uid, cid)
        if not u:
            return 0
        nl = max(u["level"] + delta, 0)
        await self.db.execute(
            "UPDATE users SET level=? WHERE user_id=? AND chat_id=?", (nl, uid, cid))
        await self.db.commit()
        return nl

    async def record_game(self, uid, cid, bet, won, win):
        col = "games_won" if win else "games_lost"
        await self.db.execute(
            f"""UPDATE users SET {col}={col}+1,
                total_bet=total_bet+?, total_won=total_won+?
                WHERE user_id=? AND chat_id=?""",
            (bet, won, uid, cid))
        await self.db.commit()

    async def record_duel(self, uid, cid, win):
        col = "duels_won" if win else "duels_lost"
        await self.db.execute(
            f"UPDATE users SET {col}={col}+1 WHERE user_id=? AND chat_id=?",
            (uid, cid))
        await self.db.commit()

    async def inc_captcha(self, uid, cid):
        await self.db.execute(
            "UPDATE users SET captchas_ok=captchas_ok+1 WHERE user_id=? AND chat_id=?",
            (uid, cid))
        await self.db.commit()

    async def set_daily(self, uid, cid):
        await self.db.execute(
            "UPDATE users SET last_daily=? WHERE user_id=? AND chat_id=?",
            (time.time(), uid, cid))
        await self.db.commit()

    async def top(self, cid, limit=10):
        cur = await self.db.execute(
            """SELECT name,level,rolls,best_roll FROM users
               WHERE chat_id=? ORDER BY level DESC LIMIT ?""", (cid, limit))
        return [dict(r) for r in await cur.fetchall()]

    async def global_top(self, limit=15):
        cur = await self.db.execute(
            """SELECT name, user_id,
                      SUM(level) AS total_level,
                      SUM(rolls) AS total_rolls,
                      SUM(games_won) AS gw, SUM(games_lost) AS gl,
                      SUM(duels_won) AS dw, SUM(duels_lost) AS dl
               FROM users GROUP BY user_id
               ORDER BY total_level DESC LIMIT ?""", (limit,))
        return [dict(r) for r in await cur.fetchall()]

    async def position(self, uid, cid):
        lvl = await self.get_level(uid, cid)
        cur = await self.db.execute(
            "SELECT COUNT(*) AS c FROM users WHERE chat_id=? AND level>?", (cid, lvl))
        pos = (await cur.fetchone())["c"] + 1
        cur2 = await self.db.execute(
            "SELECT COUNT(*) AS c FROM users WHERE chat_id=?", (cid,))
        total = (await cur2.fetchone())["c"]
        return pos, total

    async def chat_stats(self, cid):
        cur = await self.db.execute(
            """SELECT COUNT(*) AS players,
                      COALESCE(SUM(level),0) AS total_lvl,
                      COALESCE(MAX(level),0) AS max_lvl,
                      COALESCE(SUM(rolls),0) AS total_rolls,
                      COALESCE(MAX(best_roll),0) AS best_ever,
                      COALESCE(SUM(games_won),0) AS gw,
                      COALESCE(SUM(games_lost),0) AS gl,
                      COALESCE(SUM(duels_won),0) AS dw,
                      COALESCE(SUM(duels_lost),0) AS dl
               FROM users WHERE chat_id=?""", (cid,))
        return dict(await cur.fetchone())


# ======================== ИНИЦИАЛИЗАЦИЯ ========================

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("curator")

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()
router = Router()
db = Database(DB_FILE)

# uid,cid → timestamp окончания кулдауна
cooldowns: dict[tuple[int, int], float] = {}
# (uid,cid) → MinesGame
active_mines: dict[tuple[int, int], MinesGame] = {}
# (uid,cid) → captcha_info
pending_captchas: dict[tuple[int, int], dict] = {}
# (chat_id, msg_id) → duel_info
active_duels: dict[tuple[int, int], dict] = {}


def set_cd(uid, cid, seconds=COOLDOWN_SECONDS):
    cooldowns[(uid, cid)] = time.time() + seconds


def remaining_cd(uid, cid):
    end = cooldowns.get((uid, cid), 0)
    r = end - time.time()
    return max(0, int(r))


def only_group(msg):
    return msg.chat.type in ("group", "supergroup")


async def parse_bet(msg, raw):
    if not raw:
        return None
    token = raw.strip().split()[0].lower()
    uid, cid = msg.from_user.id, msg.chat.id
    level = await db.get_level(uid, cid)
    if token in ("all", "все", "олл"):
        bet = level
    elif token in ("half", "половина"):
        bet = level // 2
    else:
        try:
            bet = int(token)
        except ValueError:
            await msg.reply("❌ Ставка — число, <b>all</b> или <b>half</b>")
            return None
    if bet < 1:
        await msg.reply("❌ Минимальная ставка: <b>1</b>")
        return None
    if bet > level:
        await msg.reply(f"❌ Недостаточно! Уровень: <b>{fmt(level)}</b>")
        return None
    return bet


# ======================== КАПЧА ========================

def generate_captcha():
    op = random.choice(["+", "-", "×"])
    if op == "+":
        a, b = random.randint(10, 99), random.randint(1, 99)
        ans = a + b
    elif op == "-":
        a = random.randint(20, 99)
        b = random.randint(1, a - 1)
        ans = a - b
    else:
        a, b = random.randint(2, 12), random.randint(2, 12)
        ans = a * b
    question = f"{a} {op} {b}"
    wrong = set()
    while len(wrong) < 3:
        w = ans + random.choice([-3, -2, -1, 1, 2, 3, 5, -5, 7, -7, 10, -10])
        if w != ans and w > 0:
            wrong.add(w)
    options = list(wrong) + [ans]
    random.shuffle(options)
    return question, ans, options


# ======================== /curator ========================

@router.message(Command("curator"))
async def cmd_curator(message: Message):
    if not only_group(message):
        await message.reply("🚫 Только в группах!")
        return

    uid, cid = message.from_user.id, message.chat.id
    name = message.from_user.full_name
    await db.ensure(uid, cid, name)

    key = (uid, cid)

    # если есть неотвеченная капча
    if key in pending_captchas:
        cap = pending_captchas[key]
        if time.time() - cap["created"] < CAPTCHA_TIMEOUT:
            await message.reply("⚠️ Сначала решите капчу выше!")
            return
        else:
            del pending_captchas[key]

    wait = remaining_cd(uid, cid)
    if wait > 0:
        m, s = divmod(wait, 60)
        await message.reply(f"⏳ <b>{name}</b>, подождите <b>{m}м {s}с</b>!")
        return

    inc = random.randint(MIN_LEVEL_UP, MAX_LEVEL_UP)

    # 25 % шанс капчи
    if random.random() < CAPTCHA_CHANCE:
        question, ans, options = generate_captcha()
        btns = [
            InlineKeyboardButton(text=str(o), callback_data=f"cap:{uid}:{o}")
            for o in options
        ]
        kb = InlineKeyboardMarkup(inline_keyboard=[btns])
        msg = await message.reply(
            f"🤖 <b>Антибот-проверка!</b>\n\n"
            f"Решите: <b>{question} = ?</b>\n"
            f"⏱ {CAPTCHA_TIMEOUT} секунд",
            reply_markup=kb,
        )
        pending_captchas[key] = {
            "answer": ans, "increase": inc,
            "created": time.time(), "msg_id": msg.message_id,
        }
        return

    # без капчи — сразу выдаём
    await _give_curator(message, uid, cid, name, inc)


async def _give_curator(message, uid, cid, name, inc):
    nl, user = await db.curator_roll(uid, cid, name, inc)
    set_cd(uid, cid)
    rank = get_rank(nl)
    nxt = get_next_rank(nl)
    pt = prev_threshold(nl)
    txt = (
        f"💼 <b>{name}</b>, уровень повышен! <b>(+{inc})</b>\n\n"
        f"⭐ Уровень: <b>{fmt(nl)}</b>\n"
        f"🏅 Ранг: <b>{rank}</b>\n"
        f"🎲 Прокачек: <b>{user['rolls']}</b>"
    )
    if nxt:
        nn, nt = nxt
        bar = progress_bar(nl - pt, nt - pt)
        txt += f"\n\n📊 До «{nn}»:\n{bar}  осталось <b>{fmt(nt - nl)}</b>"
    if inc == MAX_LEVEL_UP:
        txt += "\n\n🍀 <b>Крит!</b> Максимальный бросок!"
    await message.reply(txt)


@router.callback_query(F.data.startswith("cap:"))
async def captcha_answer(cb: CallbackQuery):
    parts = cb.data.split(":")
    cap_uid, selected = int(parts[1]), int(parts[2])
    uid, cid = cb.from_user.id, cb.message.chat.id

    if uid != cap_uid:
        await cb.answer("❌ Не ваша проверка!", show_alert=True)
        return

    key = (uid, cid)
    cap = pending_captchas.get(key)
    if not cap:
        await cb.answer("⏰ Проверка истекла!", show_alert=True)
        return
    if time.time() - cap["created"] > CAPTCHA_TIMEOUT:
        del pending_captchas[key]
        await cb.message.edit_text("⏰ Время истекло! Попробуйте /curator снова.")
        await cb.answer()
        return

    inc = cap["increase"]
    correct = cap["answer"]
    del pending_captchas[key]

    name = cb.from_user.full_name

    if selected == correct:
        await db.inc_captcha(uid, cid)
        nl, user = await db.curator_roll(uid, cid, name, inc)
        set_cd(uid, cid)
        rank = get_rank(nl)
        await cb.message.edit_text(
            f"✅ <b>Верно!</b>\n\n"
            f"💼 <b>{name}</b>, уровень повышен! <b>(+{inc})</b>\n"
            f"⭐ Уровень: <b>{fmt(nl)}</b>\n"
            f"🏅 Ранг: <b>{rank}</b>"
        )
        await cb.answer("✅ Правильно!")
    else:
        set_cd(uid, cid, 15)  # мини-кулдаун 15 сек
        await cb.message.edit_text(
            f"❌ <b>Неверно!</b> Было: <b>{correct}</b>\n"
            f"Попробуйте /curator через 15 секунд."
        )
        await cb.answer("❌ Неправильно!")


# ======================== /daily ========================

@router.message(Command("daily"))
async def cmd_daily(message: Message):
    if not only_group(message):
        await message.reply("🚫 Только в группах!")
        return

    uid, cid = message.from_user.id, message.chat.id
    name = message.from_user.full_name
    await db.ensure(uid, cid, name)

    user = await db.get_user(uid, cid)
    last = user.get("last_daily", 0) or 0
    elapsed = time.time() - last

    if elapsed < 86400:
        rem = 86400 - elapsed
        h, rem2 = divmod(int(rem), 3600)
        m, _ = divmod(rem2, 60)
        await message.reply(
            f"⏰ <b>{name}</b>, бонус уже получен!\n"
            f"Следующий через: <b>{h}ч {m}м</b>"
        )
        return

    nl = await db.change_level(uid, cid, DAILY_BONUS)
    await db.set_daily(uid, cid)

    await message.reply(
        f"🎁 <b>{name}</b>, ежедневный бонус получен!\n\n"
        f"💰 <b>+{DAILY_BONUS}</b> к уровню\n"
        f"⭐ Уровень: <b>{fmt(nl)}</b>\n"
        f"🏅 Ранг: <b>{get_rank(nl)}</b>"
    )


# ======================== ДУЭЛИ ========================

@router.message(Command("duel"))
async def cmd_duel(message: Message, command: CommandObject):
    if not only_group(message):
        await message.reply("🚫 Только в группах!")
        return

    if not message.reply_to_message or not message.reply_to_message.from_user:
        await message.reply(
            "⚔️ <b>Дуэль</b>\n\n"
            "Ответьте на сообщение соперника:\n"
            "<code>/duel [ставка]</code>\n"
            "Ставка: число / <b>all</b> / <b>half</b>"
        )
        return

    target = message.reply_to_message.from_user
    uid, cid = message.from_user.id, message.chat.id
    name = message.from_user.full_name

    if target.id == uid:
        await message.reply("❌ Нельзя вызвать себя!")
        return
    if target.is_bot:
        await message.reply("❌ Нельзя вызвать бота!")
        return

    await db.ensure(uid, cid, name)
    await db.ensure(target.id, cid, target.full_name)

    bet = await parse_bet(message, command.args)
    if bet is None:
        return

    target_level = await db.get_level(target.id, cid)
    if target_level < bet:
        await message.reply(
            f"❌ У <b>{target.full_name}</b> недостаточно уровня! "
            f"(есть {fmt(target_level)})"
        )
        return

    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Принять", callback_data="duel:a"),
        InlineKeyboardButton(text="❌ Отклонить", callback_data="duel:d"),
    ]])

    msg = await message.reply(
        f"⚔️ <b>Дуэль!</b>\n\n"
        f"🔴 <b>{name}</b>\n"
        f"🆚\n"
        f"🔵 <b>{target.full_name}</b>\n\n"
        f"💰 Ставка: <b>{fmt(bet)}</b>\n"
        f"⏱ {DUEL_TIMEOUT // 60} мин на принятие\n\n"
        f"<i>{target.full_name}, примите или отклоните!</i>",
        reply_markup=kb,
    )

    active_duels[(cid, msg.message_id)] = {
        "challenger_id": uid,
        "challenger_name": name,
        "challenged_id": target.id,
        "challenged_name": target.full_name,
        "bet": bet,
        "created": time.time(),
    }


@router.callback_query(F.data == "duel:a")
async def duel_accept(cb: CallbackQuery):
    key = (cb.message.chat.id, cb.message.message_id)
    duel = active_duels.get(key)

    if not duel:
        await cb.answer("❌ Дуэль не найдена!", show_alert=True)
        return
    if cb.from_user.id != duel["challenged_id"]:
        await cb.answer("❌ Не ваша дуэль!", show_alert=True)
        return
    if time.time() - duel["created"] > DUEL_TIMEOUT:
        del active_duels[key]
        await cb.message.edit_text("⏰ Дуэль истекла!")
        await cb.answer()
        return

    cid = cb.message.chat.id
    bet = duel["bet"]
    c_id, c_name = duel["challenger_id"], duel["challenger_name"]
    t_id, t_name = duel["challenged_id"], duel["challenged_name"]

    # проверяем уровни обоих
    c_lvl = await db.get_level(c_id, cid)
    t_lvl = await db.get_level(t_id, cid)

    if c_lvl < bet:
        del active_duels[key]
        await cb.message.edit_text(f"❌ У {c_name} больше нет {fmt(bet)} уровня!")
        await cb.answer()
        return
    if t_lvl < bet:
        del active_duels[key]
        await cb.message.edit_text(f"❌ У {t_name} больше нет {fmt(bet)} уровня!")
        await cb.answer()
        return

    # рандомный победитель
    winner_is_challenger = random.random() < 0.5

    if winner_is_challenger:
        w_id, w_name, l_id, l_name = c_id, c_name, t_id, t_name
    else:
        w_id, w_name, l_id, l_name = t_id, t_name, c_id, c_name

    w_nl = await db.change_level(w_id, cid, bet)
    l_nl = await db.change_level(l_id, cid, -bet)
    await db.record_duel(w_id, cid, True)
    await db.record_duel(l_id, cid, False)
    del active_duels[key]

    await cb.message.edit_text(
        f"⚔️ <b>Дуэль завершена!</b>\n\n"
        f"🏆 Победитель: <b>{w_name}</b>\n"
        f"💰 +{fmt(bet)} → уровень <b>{fmt(w_nl)}</b> ({get_rank(w_nl)})\n\n"
        f"💀 Проигравший: <b>{l_name}</b>\n"
        f"💸 -{fmt(bet)} → уровень <b>{fmt(l_nl)}</b>"
    )
    await cb.answer(f"🏆 {w_name} победил!")


@router.callback_query(F.data == "duel:d")
async def duel_decline(cb: CallbackQuery):
    key = (cb.message.chat.id, cb.message.message_id)
    duel = active_duels.get(key)

    if not duel:
        await cb.answer("❌ Дуэль не найдена!", show_alert=True)
        return
    if cb.from_user.id != duel["challenged_id"]:
        await cb.answer("❌ Не ваша дуэль!", show_alert=True)
        return

    del active_duels[key]
    await cb.message.edit_text(
        f"🏳️ <b>{duel['challenged_name']}</b> отклонил дуэль с "
        f"<b>{duel['challenger_name']}</b>."
    )
    await cb.answer("Дуэль отклонена")


# ======================== МИНЫ ========================

@router.message(Command("mines"))
async def cmd_mines(message: Message, command: CommandObject):
    if not only_group(message):
        await message.reply("🚫 Только в группах!")
        return

    uid, cid = message.from_user.id, message.chat.id
    name = message.from_user.full_name
    await db.ensure(uid, cid, name)

    key = (uid, cid)
    old = active_mines.get(key)
    if old and old.active and not old.expired:
        await message.reply("❌ У вас уже есть активная игра!")
        return
    if old and old.expired:
        del active_mines[key]

    if not command.args:
        await message.reply(
            "💣 <b>Мины</b>\n\n"
            "<code>/mines [ставка] [мины 1-24]</code>\n"
            "Пример: <code>/mines 50 5</code>"
        )
        return

    bet = await parse_bet(message, command.args)
    if bet is None:
        return

    parts = command.args.strip().split()
    mc = 5
    if len(parts) > 1:
        try:
            mc = max(1, min(int(parts[1]), 24))
        except ValueError:
            pass

    await db.change_level(uid, cid, -bet)
    game = MinesGame(uid, cid, bet, mc)
    active_mines[key] = game

    msg = await message.reply(
        f"💣 <b>Мины</b> — {name}\n\n"
        f"💰 Ставка: <b>{fmt(bet)}</b> · 💣 Мин: <b>{mc}</b>\n"
        f"Открывайте ячейки!",
        reply_markup=game.keyboard(),
    )


@router.callback_query(F.data.startswith("m:"))
async def mines_cell(cb: CallbackQuery):
    _, oid, row, col = cb.data.split(":")
    oid, row, col = int(oid), int(row), int(col)
    uid, cid = cb.from_user.id, cb.message.chat.id

    if uid != oid:
        await cb.answer("❌ Не ваша игра!", show_alert=True)
        return

    game = active_mines.get((uid, cid))
    if not game or not game.active:
        await cb.answer("Игра завершена!")
        return
    if game.expired:
        del active_mines[(uid, cid)]
        await cb.answer("⏰ Истекла!", show_alert=True)
        return

    res = game.reveal(row, col)
    if res == "already":
        await cb.answer("Уже открыто!")
        return

    name = cb.from_user.full_name

    if res == "mine":
        await db.record_game(uid, cid, game.bet, 0, False)
        nl = await db.get_level(uid, cid)
        del active_mines[(uid, cid)]
        await cb.message.edit_text(
            f"💥 <b>ВЗРЫВ!</b> — {name}\n\n"
            f"💸 Потеряно: <b>{fmt(game.bet)}</b>\n"
            f"⭐ Уровень: <b>{fmt(nl)}</b>",
            reply_markup=game.keyboard(show_all=True),
        )
        await cb.answer("💥 Мина!")
    elif not game.active:
        w = game.winnings
        nl = await db.change_level(uid, cid, w)
        await db.record_game(uid, cid, game.bet, w, True)
        del active_mines[(uid, cid)]
        await cb.message.edit_text(
            f"🎉 <b>ВСЕ ОТКРЫТО!</b> — {name}\n\n"
            f"💵 +{fmt(w)} (x{game.multiplier})\n"
            f"⭐ Уровень: <b>{fmt(nl)}</b>",
            reply_markup=game.keyboard(show_all=True),
        )
        await cb.answer("🎉 Максимум!")
    else:
        await cb.message.edit_text(
            f"💣 <b>Мины</b> — {name}\n\n"
            f"💰 {fmt(game.bet)} · 💣 {game.mines_count}\n"
            f"✅ Открыто: <b>{game.safe_found}</b> · 🎯 x{game.multiplier}\n"
            f"💵 К выплате: <b>{fmt(game.winnings)}</b>",
            reply_markup=game.keyboard(),
        )
        await cb.answer(f"✅ x{game.multiplier}")


@router.callback_query(F.data.startswith("mc:"))
async def mines_cashout(cb: CallbackQuery):
    oid = int(cb.data.split(":")[1])
    uid, cid = cb.from_user.id, cb.message.chat.id
    if uid != oid:
        await cb.answer("❌ Не ваша игра!", show_alert=True)
        return

    game = active_mines.get((uid, cid))
    if not game or not game.active:
        await cb.answer("Игра завершена!")
        return

    w = game.winnings
    game.active = False
    nl = await db.change_level(uid, cid, w)
    await db.record_game(uid, cid, game.bet, w, True)
    del active_mines[(uid, cid)]

    await cb.message.edit_text(
        f"💰 <b>КЕШАУТ!</b> — {cb.from_user.full_name}\n\n"
        f"🎯 x{game.multiplier} · 💵 +{fmt(game.profit)}\n"
        f"⭐ Уровень: <b>{fmt(nl)}</b> · {get_rank(nl)}",
        reply_markup=game.keyboard(show_all=True),
    )
    await cb.answer(f"💰 {fmt(w)}!")


@router.callback_query(F.data == "noop")
async def noop(cb: CallbackQuery):
    await cb.answer()


# ======================== СЛОТЫ ========================

SYMBOLS = ["🍋", "🍒", "🍇", "💎", "7️⃣", "🍀"]
SYM_W = [30, 25, 20, 12, 8, 5]
TRIPLE_MULT = [3, 5, 7, 15, 30, 50]


@router.message(Command("slots"))
async def cmd_slots(message: Message, command: CommandObject):
    if not only_group(message):
        await message.reply("🚫 Только в группах!")
        return

    uid, cid = message.from_user.id, message.chat.id
    name = message.from_user.full_name
    await db.ensure(uid, cid, name)

    bet = await parse_bet(message, command.args)
    if bet is None:
        if not command.args:
            await message.reply(
                "🎰 <b>Слоты</b>\n<code>/slots [ставка]</code>\n"
                "3 одинаковых = джекпот · 2 = x1.5"
            )
        return

    reels = [random.choices(SYMBOLS, weights=SYM_W, k=1)[0] for _ in range(3)]
    display = f"⟦ {reels[0]} ┃ {reels[1]} ┃ {reels[2]} ⟧"

    if reels[0] == reels[1] == reels[2]:
        mult = TRIPLE_MULT[SYMBOLS.index(reels[0])]
        profit = int(bet * mult) - bet
        nl = await db.change_level(uid, cid, profit)
        await db.record_game(uid, cid, bet, int(bet * mult), True)
        txt = (f"🎰 {display}\n\n🎉 <b>ДЖЕКПОТ x{mult}!</b>\n"
               f"💵 +{fmt(profit)}\n⭐ {fmt(nl)}")
    elif reels[0] == reels[1] or reels[1] == reels[2] or reels[0] == reels[2]:
        profit = int(bet * 1.5) - bet
        nl = await db.change_level(uid, cid, profit)
        await db.record_game(uid, cid, bet, int(bet * 1.5), True)
        txt = (f"🎰 {display}\n\n✅ Два! x1.5\n"
               f"💵 +{fmt(profit)}\n⭐ {fmt(nl)}")
    else:
        nl = await db.change_level(uid, cid, -bet)
        await db.record_game(uid, cid, bet, 0, False)
        txt = f"🎰 {display}\n\n❌ Мимо!\n💸 -{fmt(bet)}\n⭐ {fmt(nl)}"

    await message.reply(txt)


# ======================== МОНЕТКА ========================

@router.message(Command("coinflip", "cf"))
async def cmd_cf(message: Message, command: CommandObject):
    if not only_group(message):
        await message.reply("🚫 Только в группах!")
        return

    uid, cid = message.from_user.id, message.chat.id
    name = message.from_user.full_name
    await db.ensure(uid, cid, name)

    bet = await parse_bet(message, command.args)
    if bet is None:
        if not command.args:
            await message.reply("🪙 <code>/cf [ставка]</code> — 48/52, выигрыш x2")
        return

    side = random.choice(["Орёл 🦅", "Решка 👑"])

    if random.random() < 0.48:
        nl = await db.change_level(uid, cid, bet)
        await db.record_game(uid, cid, bet, bet * 2, True)
        txt = f"🪙 {side}\n\n✅ <b>{name}</b> +{fmt(bet)}\n⭐ {fmt(nl)}"
    else:
        nl = await db.change_level(uid, cid, -bet)
        await db.record_game(uid, cid, bet, 0, False)
        txt = f"🪙 {side}\n\n❌ <b>{name}</b> -{fmt(bet)}\n⭐ {fmt(nl)}"

    await message.reply(txt)


# ======================== КОСТИ ========================

DICE_FACE = ["⚀", "⚁", "⚂", "⚃", "⚄", "⚅"]


@router.message(Command("dice"))
async def cmd_dice(message: Message, command: CommandObject):
    if not only_group(message):
        await message.reply("🚫 Только в группах!")
        return

    uid, cid = message.from_user.id, message.chat.id
    name = message.from_user.full_name
    await db.ensure(uid, cid, name)

    bet = await parse_bet(message, command.args)
    if bet is None:
        if not command.args:
            await message.reply(
                "🎲 <code>/dice [ставка]</code>\n"
                "1-2 проигр · 3 ничья · 4 x1.5 · 5 x2 · 6 x3"
            )
        return

    roll = random.randint(1, 6)
    face = DICE_FACE[roll - 1]

    if roll <= 2:
        nl = await db.change_level(uid, cid, -bet)
        await db.record_game(uid, cid, bet, 0, False)
        txt = f"🎲 {face} ({roll})\n\n❌ -{fmt(bet)}\n⭐ {fmt(nl)}"
    elif roll == 3:
        nl = await db.get_level(uid, cid)
        txt = f"🎲 {face} ({roll})\n\n🔄 Ничья!\n⭐ {fmt(nl)}"
    else:
        mult = {4: 1.5, 5: 2.0, 6: 3.0}[roll]
        profit = int(bet * mult) - bet
        nl = await db.change_level(uid, cid, profit)
        await db.record_game(uid, cid, bet, int(bet * mult), True)
        txt = f"🎲 {face} ({roll}) x{mult}\n\n✅ +{fmt(profit)}\n⭐ {fmt(nl)}"

    await message.reply(txt)


# ======================== ТОП ЧАТА ========================

@router.message(Command("top"))
async def cmd_top(message: Message):
    if not only_group(message):
        await message.reply("🚫 Только в группах!")
        return

    top = await db.top(message.chat.id, 10)
    if not top:
        await message.reply("📊 Пусто! /curator")
        return

    medals = ["🥇", "🥈", "🥉"]
    lines = []
    for i, u in enumerate(top):
        p = medals[i] if i < 3 else f"  <b>{i+1}.</b>"
        lines.append(
            f'{p} <b>{u["name"]}</b> — {fmt(u["level"])}\n'
            f'       {get_rank(u["level"])}'
        )

    st = await db.chat_stats(message.chat.id)
    avg = st["total_lvl"] / st["players"] if st["players"] else 0

    txt = (
        "🏆 <b>Топ-10 чата</b>\n" + "━" * 28 + "\n\n"
        + "\n\n".join(lines) + "\n\n" + "━" * 28
        + f"\n👥 {st['players']} · сред. {fmt(int(avg))}"
    )
    await message.reply(txt)


# ======================== ГЛОБАЛЬНЫЙ ТОП ========================

@router.message(Command("globaltop", "gtop"))
async def cmd_gtop(message: Message):
    top = await db.global_top(15)
    if not top:
        await message.reply("🌍 Пусто!")
        return

    medals = ["🥇", "🥈", "🥉"]
    lines = []
    for i, u in enumerate(top):
        p = medals[i] if i < 3 else f"  <b>{i+1}.</b>"
        tl = u["total_level"]
        gw = u["gw"] or 0
        gl = u["gl"] or 0
        dw = u["dw"] or 0
        dl = u["dl"] or 0
        gt = gw + gl
        dt = dw + dl
        lines.append(
            f'{p} <b>{u["name"]}</b>\n'
            f'       ур. <b>{fmt(tl)}</b> · {get_rank(tl)}\n'
            f'       🎮 {gt} игр · ⚔️ {dt} дуэлей'
        )

    txt = "🌍 <b>Глобальный топ-15</b>\n" + "━" * 28 + "\n\n" + "\n\n".join(lines)
    await message.reply(txt)


# ======================== ПРОФИЛЬ ========================

@router.message(Command("my_level", "mylevel", "me"))
async def cmd_me(message: Message):
    if not only_group(message):
        await message.reply("🚫 Только в группах!")
        return

    uid, cid = message.from_user.id, message.chat.id
    name = message.from_user.full_name
    u = await db.get_user(uid, cid)

    if not u:
        await message.reply(f"🚀 <b>{name}</b>, начните с /curator!")
        return

    lv = u["level"]
    rank = get_rank(lv)
    pos, total = await db.position(uid, cid)
    gw, gl = u["games_won"], u["games_lost"]
    dw, dl = u.get("duels_won", 0), u.get("duels_lost", 0)
    gt, dt = gw + gl, dw + dl
    wr = f"{gw/gt*100:.0f}%" if gt else "—"
    dwr = f"{dw/dt*100:.0f}%" if dt else "—"
    net = u["total_won"] - u["total_bet"]
    net_s = f"+{fmt(net)}" if net >= 0 else fmt(net)
    nxt = get_next_rank(lv)
    pt = prev_threshold(lv)

    txt = (
        f"👤 <b>{name}</b>\n" + "━" * 28 + "\n\n"
        f"⭐ Уровень: <b>{fmt(lv)}</b>\n"
        f"🏅 Ранг: <b>{rank}</b>\n"
        f"🏆 Место: <b>{pos}/{total}</b>\n"
        f"🎲 Прокачек: <b>{u['rolls']}</b> · рекорд +{u['best_roll']}\n\n"
        f"🎮 Игры: <b>{gw}W/{gl}L</b> ({wr})\n"
        f"⚔️ Дуэли: <b>{dw}W/{dl}L</b> ({dwr})\n"
        f"💰 Ставки: <b>{fmt(u['total_bet'])}</b>\n"
        f"💵 Выигрыши: <b>{fmt(u['total_won'])}</b>\n"
        f"📊 Баланс: <b>{net_s}</b>"
    )

    if nxt:
        nn, nt = nxt
        bar = progress_bar(lv - pt, nt - pt)
        txt += f"\n\n📊 До «{nn}»:\n{bar}  осталось <b>{fmt(nt - lv)}</b>"
    else:
        txt += "\n\n♾️ <b>Максимальный ранг!</b>"

    await message.reply(txt)


# ======================== СТАТИСТИКА ========================

@router.message(Command("stats"))
async def cmd_stats(message: Message):
    if not only_group(message):
        await message.reply("🚫 Только в группах!")
        return

    st = await db.chat_stats(message.chat.id)
    if st["players"] == 0:
        await message.reply("📊 Пусто!")
        return

    avg = st["total_lvl"] / st["players"]
    tg = st["gw"] + st["gl"]
    td = st["dw"] + st["dl"]
    await message.reply(
        "📊 <b>Статистика чата</b>\n" + "━" * 28 + "\n\n"
        f"👥 Игроков: <b>{st['players']}</b>\n"
        f"📈 Общий уровень: <b>{fmt(st['total_lvl'])}</b>\n"
        f"📉 Средний: <b>{fmt(int(avg))}</b>\n"
        f"🏔 Макс: <b>{fmt(st['max_lvl'])}</b>\n"
        f"🎲 Прокачек: <b>{fmt(st['total_rolls'])}</b>\n"
        f"🎮 Игр: <b>{fmt(tg)}</b>\n"
        f"⚔️ Дуэлей: <b>{fmt(td)}</b>\n"
        f"🎯 Рекорд: <b>+{st['best_ever']}</b>"
    )


# ======================== РАНГИ ========================

@router.message(Command("ranks"))
async def cmd_ranks(message: Message):
    lines = [f"<b>{fmt(t)}+</b> → {n}" for t, n in RANKS]
    await message.reply(
        f"📜 <b>Все ранги ({len(RANKS)} шт.)</b>\n"
        + "━" * 28 + "\n\n"
        + "\n".join(lines)
    )


# ======================== ПОМОЩЬ ========================

@router.message(Command("help", "start"))
async def cmd_help(message: Message):
    await message.reply(
        "💼 <b>Бот прокачки кураторства</b>\n" + "━" * 30 + "\n\n"
        "<b>📈 Прокачка:</b>\n"
        "/curator — +1‑10 (кд 2.5 мин, иногда капча)\n"
        "/daily — +100 раз в сутки\n\n"
        "<b>🎮 Игры (ставка уровнем):</b>\n"
        "/mines [ставка] [мины] — мины 5×5\n"
        "/slots [ставка] — слоты 🎰\n"
        "/cf [ставка] — монетка 🪙\n"
        "/dice [ставка] — кости 🎲\n"
        "Ставка: число / <b>all</b> / <b>half</b>\n\n"
        "<b>⚔️ PvP:</b>\n"
        "/duel [ставка] — дуэль (ответом на сообщение)\n\n"
        "<b>📊 Инфо:</b>\n"
        "/top — топ чата\n"
        "/gtop — глобальный топ\n"
        "/me — профиль\n"
        "/stats — статистика\n"
        "/ranks — все 40 рангов\n"
        "/help — справка"
    )


# ======================== ЗАПУСК ========================

dp.include_router(router)


async def main():
    await db.connect()
    log.info("БД: %s | Бот запущен", DB_FILE)
    try:
        await bot.delete_webhook(drop_pending_updates=True)
        await dp.start_polling(bot)
    finally:
        await db.close()


if __name__ == "__main__":
    asyncio.run(main())