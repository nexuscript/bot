import asyncio
import random
import time
import logging

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
COOLDOWN_SECONDS = 300
MIN_LEVEL_UP = 1
MAX_LEVEL_UP = 10
DB_FILE = "curator.db"
MINES_TIMEOUT = 600  # 10 мин — игра сгорает

# ======================== РАНГИ (до 1 000 000) ========================

RANKS = [
    (0,         "🌱 Новичок"),
    (10,        "📝 Стажёр"),
    (30,        "📎 Младший куратор"),
    (75,        "📋 Куратор"),
    (150,       "⭐ Старший куратор"),
    (300,       "🔥 Мастер-куратор"),
    (500,       "👑 Легендарный куратор"),
    (1_000,     "💎 Мифический куратор"),
    (2_000,     "🌀 Элитный куратор"),
    (3_500,     "🏰 Великий куратор"),
    (5_000,     "⚔️ Эпический куратор"),
    (7_500,     "🛡️ Несокрушимый куратор"),
    (10_000,    "🌌 Космический куратор"),
    (15_000,    "⚡ Громовержец"),
    (20_000,    "🔱 Повелитель"),
    (30_000,    "🐉 Драконий куратор"),
    (50_000,    "☄️ Бессмертный"),
    (75_000,    "🌠 Божественный куратор"),
    (100_000,   "👁 Титан кураторства"),
    (150_000,   "🔮 Оракул"),
    (200_000,   "💫 Астральный куратор"),
    (300_000,   "🌍 Хранитель мира"),
    (500_000,   "🌟 Абсолют"),
    (750_000,   "✨ Демиург"),
    (1_000_000, "🪐 Создатель миров"),
]


def get_rank(level: int) -> str:
    rank = RANKS[0][1]
    for t, n in RANKS:
        if level >= t:
            rank = n
    return rank


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
    f = min(int(cur / total * ln), ln)
    return "▓" * f + "░" * (ln - f)


def fmt(n: int) -> str:
    return f"{n:,}".replace(",", " ")


# ======================== МИНЫ ========================

class MinesGame:
    def __init__(self, uid: int, cid: int, bet: int, mines: int):
        self.uid = uid
        self.cid = cid
        self.bet = bet
        self.mines_count = mines
        self.size = 5
        self.total = self.size ** 2
        self.grid = [[False] * self.size for _ in range(self.size)]
        self.revealed = [[False] * self.size for _ in range(self.size)]
        self.safe_found = 0
        self.active = True
        self.exploded = False
        self.created = time.time()

        for r, c in random.sample(
            [(r, c) for r in range(self.size) for c in range(self.size)],
            mines,
        ):
            self.grid[r][c] = True

    @property
    def expired(self) -> bool:
        return time.time() - self.created > MINES_TIMEOUT

    def reveal(self, r: int, c: int) -> str:
        if self.revealed[r][c]:
            return "already"
        self.revealed[r][c] = True
        if self.grid[r][c]:
            self.active = False
            self.exploded = True
            return "mine"
        self.safe_found += 1
        if self.safe_found == self.total - self.mines_count:
            self.active = False
        return "safe"

    @property
    def multiplier(self) -> float:
        if self.safe_found == 0:
            return 1.0
        prob = 1.0
        safe = self.total - self.mines_count
        for i in range(self.safe_found):
            prob *= (safe - i) / (self.total - i)
        return min(round(0.97 / prob, 2), 999.0) if prob > 0 else 999.0

    @property
    def winnings(self) -> int:
        return max(int(self.bet * self.multiplier), 1)

    @property
    def profit(self) -> int:
        return self.winnings - self.bet

    def keyboard(self, show_all: bool = False) -> InlineKeyboardMarkup:
        rows = []
        for r in range(self.size):
            row = []
            for c in range(self.size):
                if show_all or self.revealed[r][c]:
                    if self.grid[r][c]:
                        txt = "💣"
                    elif self.revealed[r][c]:
                        txt = "💚"
                    else:
                        txt = "⬜"
                    row.append(InlineKeyboardButton(text=txt, callback_data="noop"))
                else:
                    row.append(InlineKeyboardButton(
                        text="⬛", callback_data=f"m:{self.uid}:{r}:{c}"
                    ))
            rows.append(row)

        if self.active and self.safe_found > 0:
            rows.append([InlineKeyboardButton(
                text=f"💰 Забрать {fmt(self.winnings)} (x{self.multiplier})",
                callback_data=f"mc:{self.uid}",
            )])
        return InlineKeyboardMarkup(inline_keyboard=rows)


# ======================== БАЗА ДАННЫХ ========================

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
                games_won  INTEGER DEFAULT 0,
                games_lost INTEGER DEFAULT 0,
                total_bet  INTEGER DEFAULT 0,
                total_won  INTEGER DEFAULT 0,
                PRIMARY KEY (user_id, chat_id)
            )
        """)
        await self.db.commit()

    async def close(self):
        if self.db:
            await self.db.close()

    async def ensure(self, uid: int, cid: int, name: str):
        cur = await self.db.execute(
            "SELECT 1 FROM users WHERE user_id=? AND chat_id=?", (uid, cid)
        )
        if not await cur.fetchone():
            await self.db.execute(
                "INSERT INTO users(user_id,chat_id,name) VALUES(?,?,?)",
                (uid, cid, name),
            )
            await self.db.commit()
        else:
            await self.db.execute(
                "UPDATE users SET name=? WHERE user_id=? AND chat_id=?",
                (name, uid, cid),
            )
            await self.db.commit()

    async def get_user(self, uid: int, cid: int) -> dict | None:
        cur = await self.db.execute(
            "SELECT * FROM users WHERE user_id=? AND chat_id=?", (uid, cid)
        )
        row = await cur.fetchone()
        return dict(row) if row else None

    async def get_level(self, uid: int, cid: int) -> int:
        u = await self.get_user(uid, cid)
        return u["level"] if u else 0

    async def curator_roll(self, uid: int, cid: int, name: str, amount: int) -> tuple[int, dict]:
        await self.ensure(uid, cid, name)
        u = await self.get_user(uid, cid)
        nl = u["level"] + amount
        br = max(u["best_roll"], amount)
        await self.db.execute(
            """UPDATE users SET level=?, rolls=rolls+1, best_roll=?, last_roll=?
               WHERE user_id=? AND chat_id=?""",
            (nl, br, time.time(), uid, cid),
        )
        await self.db.commit()
        return nl, await self.get_user(uid, cid)

    async def change_level(self, uid: int, cid: int, delta: int) -> int:
        u = await self.get_user(uid, cid)
        if not u:
            return 0
        nl = max(u["level"] + delta, 0)
        await self.db.execute(
            "UPDATE users SET level=? WHERE user_id=? AND chat_id=?",
            (nl, uid, cid),
        )
        await self.db.commit()
        return nl

    async def record_game(self, uid: int, cid: int, bet: int, won: int, win: bool):
        col = "games_won" if win else "games_lost"
        await self.db.execute(
            f"""UPDATE users SET {col}={col}+1,
                total_bet=total_bet+?, total_won=total_won+?
                WHERE user_id=? AND chat_id=?""",
            (bet, won, uid, cid),
        )
        await self.db.commit()

    async def top(self, cid: int, limit: int = 10) -> list[dict]:
        cur = await self.db.execute(
            """SELECT name,level,rolls,best_roll
               FROM users WHERE chat_id=? ORDER BY level DESC LIMIT ?""",
            (cid, limit),
        )
        return [dict(r) for r in await cur.fetchall()]

    async def global_top(self, limit: int = 15) -> list[dict]:
        cur = await self.db.execute(
            """SELECT name, user_id,
                      SUM(level) AS total_level,
                      SUM(rolls) AS total_rolls,
                      SUM(games_won) AS gw,
                      SUM(games_lost) AS gl
               FROM users GROUP BY user_id
               ORDER BY total_level DESC LIMIT ?""",
            (limit,),
        )
        return [dict(r) for r in await cur.fetchall()]

    async def position(self, uid: int, cid: int) -> tuple[int, int]:
        lvl = await self.get_level(uid, cid)
        cur = await self.db.execute(
            "SELECT COUNT(*) AS c FROM users WHERE chat_id=? AND level>?",
            (cid, lvl),
        )
        pos = (await cur.fetchone())["c"] + 1
        cur2 = await self.db.execute(
            "SELECT COUNT(*) AS c FROM users WHERE chat_id=?", (cid,)
        )
        total = (await cur2.fetchone())["c"]
        return pos, total

    async def chat_stats(self, cid: int) -> dict:
        cur = await self.db.execute(
            """SELECT COUNT(*) AS players,
                      COALESCE(SUM(level),0) AS total_lvl,
                      COALESCE(MAX(level),0) AS max_lvl,
                      COALESCE(SUM(rolls),0) AS total_rolls,
                      COALESCE(MAX(best_roll),0) AS best_ever,
                      COALESCE(SUM(games_won),0) AS gw,
                      COALESCE(SUM(games_lost),0) AS gl
               FROM users WHERE chat_id=?""",
            (cid,),
        )
        return dict(await cur.fetchone())


# ======================== ИНИЦИАЛИЗАЦИЯ ========================

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("curator")

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()
router = Router()
db = Database(DB_FILE)

cooldowns: dict[tuple[int, int], float] = {}
active_mines: dict[tuple[int, int], MinesGame] = {}


def remaining_cd(uid: int, cid: int) -> int:
    last = cooldowns.get((uid, cid), 0)
    diff = time.time() - last
    return 0 if diff >= COOLDOWN_SECONDS else int(COOLDOWN_SECONDS - diff)


def only_group(msg: Message) -> bool:
    return msg.chat.type in ("group", "supergroup")


async def parse_bet(msg: Message, raw: str | None) -> int | None:
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
            await msg.reply("❌ Ставка — число, <b>all</b> или <b>half</b>!")
            return None

    if bet < 1:
        await msg.reply("❌ Минимальная ставка: <b>1</b>")
        return None
    if bet > level:
        await msg.reply(f"❌ Недостаточно! Уровень: <b>{fmt(level)}</b>")
        return None
    return bet


# ======================== /curator ========================

@router.message(Command("curator"))
async def cmd_curator(message: Message):
    if not only_group(message):
        await message.reply("🚫 Только в группах!")
        return

    uid, cid = message.from_user.id, message.chat.id
    name = message.from_user.full_name

    wait = remaining_cd(uid, cid)
    if wait > 0:
        m, s = divmod(wait, 60)
        await message.reply(f"⏳ <b>{name}</b>, подождите <b>{m}м {s}с</b>!")
        return

    inc = random.randint(MIN_LEVEL_UP, MAX_LEVEL_UP)
    nl, user = await db.curator_roll(uid, cid, name, inc)
    cooldowns[(uid, cid)] = time.time()

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
        await message.reply("❌ У вас уже есть активная игра! Завершите её или подождите 10 мин.")
        return
    if old and old.expired:
        del active_mines[key]

    if not command.args:
        await message.reply(
            "💣 <b>Мины</b>\n\n"
            "Формат: <code>/mines [ставка] [мин 1-24]</code>\n"
            "Пример: <code>/mines 50 5</code>\n"
            "Ставка: число / <b>all</b> / <b>half</b>"
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

    txt = (
        f"💣 <b>Мины</b> — {name}\n\n"
        f"💰 Ставка: <b>{fmt(bet)}</b> · 💣 Мин: <b>{mc}</b>\n"
        f"🎯 x{game.multiplier}\n\n"
        f"Открывайте ячейки, не попадите на мину!"
    )
    msg = await message.reply(txt, reply_markup=game.keyboard())
    game.msg_id = msg.message_id


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
        await cb.answer("⏰ Игра истекла!", show_alert=True)
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
        txt = (
            f"💥 <b>ВЗРЫВ!</b> — {name}\n\n"
            f"💸 Потеряно: <b>{fmt(game.bet)}</b>\n"
            f"⭐ Уровень: <b>{fmt(nl)}</b> · {get_rank(nl)}"
        )
        await cb.message.edit_text(txt, reply_markup=game.keyboard(show_all=True))
        await cb.answer("💥 Мина!")

    elif res == "safe":
        if not game.active:  # все безопасные открыты
            w = game.winnings
            nl = await db.change_level(uid, cid, w)
            await db.record_game(uid, cid, game.bet, w, True)
            del active_mines[(uid, cid)]
            txt = (
                f"🎉 <b>ВСЕ ОТКРЫТО!</b> — {name}\n\n"
                f"💵 Выигрыш: <b>{fmt(w)}</b> (x{game.multiplier})\n"
                f"📈 Профит: <b>+{fmt(game.profit)}</b>\n"
                f"⭐ Уровень: <b>{fmt(nl)}</b> · {get_rank(nl)}"
            )
            await cb.message.edit_text(txt, reply_markup=game.keyboard(show_all=True))
            await cb.answer("🎉 Максимум!")
        else:
            txt = (
                f"💣 <b>Мины</b> — {name}\n\n"
                f"💰 Ставка: <b>{fmt(game.bet)}</b> · 💣 Мин: <b>{game.mines_count}</b>\n"
                f"✅ Открыто: <b>{game.safe_found}</b> · 🎯 x{game.multiplier}\n"
                f"💵 К выплате: <b>{fmt(game.winnings)}</b>"
            )
            await cb.message.edit_text(txt, reply_markup=game.keyboard())
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

    name = cb.from_user.full_name
    txt = (
        f"💰 <b>КЕШАУТ!</b> — {name}\n\n"
        f"🎯 x{game.multiplier}\n"
        f"💵 Выигрыш: <b>{fmt(w)}</b>\n"
        f"📈 Профит: <b>+{fmt(game.profit)}</b>\n"
        f"⭐ Уровень: <b>{fmt(nl)}</b> · {get_rank(nl)}"
    )
    await cb.message.edit_text(txt, reply_markup=game.keyboard(show_all=True))
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
                "🎰 <b>Слоты</b>\n\n"
                "Формат: <code>/slots [ставка]</code>\n"
                "3 совпадения = большой выигрыш!\n"
                "2 совпадения = x1.5"
            )
        return

    reels = [random.choices(SYMBOLS, weights=SYM_W, k=1)[0] for _ in range(3)]
    display = f"⟦ {reels[0]} ┃ {reels[1]} ┃ {reels[2]} ⟧"

    if reels[0] == reels[1] == reels[2]:
        idx = SYMBOLS.index(reels[0])
        mult = TRIPLE_MULT[idx]
        win = int(bet * mult)
        profit = win - bet
        nl = await db.change_level(uid, cid, profit)
        await db.record_game(uid, cid, bet, win, True)
        txt = (
            f"🎰 <b>Слоты</b> — {name}\n\n"
            f"{display}\n\n"
            f"🎉 <b>ДЖЕКПОТ!</b> x{mult}\n"
            f"💵 +{fmt(profit)}\n"
            f"⭐ Уровень: <b>{fmt(nl)}</b> · {get_rank(nl)}"
        )
    elif reels[0] == reels[1] or reels[1] == reels[2] or reels[0] == reels[2]:
        mult = 1.5
        win = int(bet * mult)
        profit = win - bet
        nl = await db.change_level(uid, cid, profit)
        await db.record_game(uid, cid, bet, win, True)
        txt = (
            f"🎰 <b>Слоты</b> — {name}\n\n"
            f"{display}\n\n"
            f"✅ <b>Два совпадения!</b> x{mult}\n"
            f"💵 +{fmt(profit)}\n"
            f"⭐ Уровень: <b>{fmt(nl)}</b>"
        )
    else:
        nl = await db.change_level(uid, cid, -bet)
        await db.record_game(uid, cid, bet, 0, False)
        txt = (
            f"🎰 <b>Слоты</b> — {name}\n\n"
            f"{display}\n\n"
            f"❌ Мимо!\n"
            f"💸 -{fmt(bet)}\n"
            f"⭐ Уровень: <b>{fmt(nl)}</b>"
        )
    await message.reply(txt)


# ======================== МОНЕТКА ========================

@router.message(Command("coinflip", "cf"))
async def cmd_coinflip(message: Message, command: CommandObject):
    if not only_group(message):
        await message.reply("🚫 Только в группах!")
        return

    uid, cid = message.from_user.id, message.chat.id
    name = message.from_user.full_name
    await db.ensure(uid, cid, name)

    bet = await parse_bet(message, command.args)
    if bet is None:
        if not command.args:
            await message.reply("🪙 Формат: <code>/cf [ставка]</code>\n50/50, выигрыш x2")
        return

    win = random.random() < 0.48
    side = random.choice(["Орёл 🦅", "Решка 👑"])

    if win:
        nl = await db.change_level(uid, cid, bet)
        await db.record_game(uid, cid, bet, bet * 2, True)
        txt = (
            f"🪙 {side}\n\n"
            f"✅ <b>{name}</b> выиграл!\n"
            f"💵 +{fmt(bet)}\n"
            f"⭐ Уровень: <b>{fmt(nl)}</b>"
        )
    else:
        nl = await db.change_level(uid, cid, -bet)
        await db.record_game(uid, cid, bet, 0, False)
        txt = (
            f"🪙 {side}\n\n"
            f"❌ <b>{name}</b> проиграл!\n"
            f"💸 -{fmt(bet)}\n"
            f"⭐ Уровень: <b>{fmt(nl)}</b>"
        )
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
                "🎲 Формат: <code>/dice [ставка]</code>\n"
                "1-2 = проигрыш · 3 = ничья\n"
                "4 = x1.5 · 5 = x2 · 6 = x3"
            )
        return

    roll = random.randint(1, 6)
    face = DICE_FACE[roll - 1]

    if roll <= 2:
        nl = await db.change_level(uid, cid, -bet)
        await db.record_game(uid, cid, bet, 0, False)
        txt = (
            f"🎲 {face} <b>({roll})</b>\n\n"
            f"❌ <b>{name}</b> проиграл!\n"
            f"💸 -{fmt(bet)}\n"
            f"⭐ Уровень: <b>{fmt(nl)}</b>"
        )
    elif roll == 3:
        level = await db.get_level(uid, cid)
        txt = (
            f"🎲 {face} <b>({roll})</b>\n\n"
            f"🔄 <b>Ничья!</b> Ставка возвращена.\n"
            f"⭐ Уровень: <b>{fmt(level)}</b>"
        )
    else:
        payouts = {4: 1.5, 5: 2.0, 6: 3.0}
        mult = payouts[roll]
        win = int(bet * mult)
        profit = win - bet
        nl = await db.change_level(uid, cid, profit)
        await db.record_game(uid, cid, bet, win, True)
        e = "🎉 " if roll == 6 else ""
        txt = (
            f"🎲 {face} <b>({roll})</b>\n\n"
            f"{e}✅ <b>{name}</b> выиграл! x{mult}\n"
            f"💵 +{fmt(profit)}\n"
            f"⭐ Уровень: <b>{fmt(nl)}</b>"
        )
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
            f'{p} <b>{u["name"]}</b> — ур. <b>{fmt(u["level"])}</b>\n'
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
        await message.reply("🌍 Глобальная таблица пуста!")
        return

    medals = ["🥇", "🥈", "🥉"]
    lines = []
    for i, u in enumerate(top):
        p = medals[i] if i < 3 else f"  <b>{i+1}.</b>"
        tl = u["total_level"]
        gw = u["gw"] or 0
        gl = u["gl"] or 0
        gt = gw + gl
        wr = f"{gw/gt*100:.0f}%" if gt else "—"
        lines.append(
            f'{p} <b>{u["name"]}</b>\n'
            f'       ур. <b>{fmt(tl)}</b> · {get_rank(tl)}\n'
            f'       🎲 {u["total_rolls"]} прокачек · 🎮 {gt} игр ({wr})'
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
    gt = gw + gl
    wr = f"{gw/gt*100:.0f}%" if gt else "—"
    net = u["total_won"] - u["total_bet"]
    net_s = f"+{fmt(net)}" if net >= 0 else f"{fmt(net)}"
    nxt = get_next_rank(lv)
    pt = prev_threshold(lv)

    txt = (
        f"👤 <b>{name}</b>\n" + "━" * 28 + "\n\n"
        f"⭐ Уровень: <b>{fmt(lv)}</b>\n"
        f"🏅 Ранг: <b>{rank}</b>\n"
        f"🏆 Место: <b>{pos}/{total}</b>\n"
        f"🎲 Прокачек: <b>{u['rolls']}</b> · рекорд +{u['best_roll']}\n\n"
        f"🎮 Игры: <b>{gw}W / {gl}L</b> ({wr})\n"
        f"💰 Поставлено: <b>{fmt(u['total_bet'])}</b>\n"
        f"💵 Выиграно: <b>{fmt(u['total_won'])}</b>\n"
        f"📊 Баланс игр: <b>{net_s}</b>"
    )
    if nxt:
        nn, nt = nxt
        bar = progress_bar(lv - pt, nt - pt)
        txt += f"\n\n📊 До «{nn}»:\n{bar}  осталось <b>{fmt(nt - lv)}</b>"
    else:
        txt += "\n\n🪐 <b>Максимальный ранг достигнут!</b>"

    await message.reply(txt)


# ======================== СТАТИСТИКА ЧАТА ========================

@router.message(Command("stats"))
async def cmd_stats(message: Message):
    if not only_group(message):
        await message.reply("🚫 Только в группах!")
        return

    st = await db.chat_stats(message.chat.id)
    if st["players"] == 0:
        await message.reply("📊 Пусто! /curator")
        return

    avg = st["total_lvl"] / st["players"]
    tg = st["gw"] + st["gl"]
    await message.reply(
        "📊 <b>Статистика чата</b>\n" + "━" * 28 + "\n\n"
        f"👥 Игроков: <b>{st['players']}</b>\n"
        f"📈 Общий уровень: <b>{fmt(st['total_lvl'])}</b>\n"
        f"📉 Средний: <b>{fmt(int(avg))}</b>\n"
        f"🏔 Макс: <b>{fmt(st['max_lvl'])}</b>\n"
        f"🎲 Прокачек: <b>{fmt(st['total_rolls'])}</b>\n"
        f"🎮 Игр: <b>{fmt(tg)}</b>\n"
        f"🎯 Рекорд: <b>+{st['best_ever']}</b>"
    )


# ======================== РАНГИ ========================

@router.message(Command("ranks"))
async def cmd_ranks(message: Message):
    lines = [f"<b>{fmt(t)}+</b> — {n}" for t, n in RANKS]
    await message.reply(
        "📜 <b>Все ранги</b>\n" + "━" * 28 + "\n\n" + "\n".join(lines)
    )


# ======================== ПОМОЩЬ ========================

@router.message(Command("help", "start"))
async def cmd_help(message: Message):
    await message.reply(
        "💼 <b>Бот прокачки кураторства</b>\n" + "━" * 30 + "\n\n"
        "<b>📈 Прокачка:</b>\n"
        "/curator — +1‑10 к уровню (кд 5 мин)\n\n"
        "<b>🎮 Игры (ставка уровнем):</b>\n"
        "/mines [ставка] [мины 1‑24] — мины 5×5\n"
        "/slots [ставка] — слоты 🎰\n"
        "/cf [ставка] — монетка 🪙\n"
        "/dice [ставка] — кости 🎲\n"
        "Ставка: число / <b>all</b> / <b>half</b>\n\n"
        "<b>📊 Топ и профиль:</b>\n"
        "/top — топ чата\n"
        "/gtop — глобальный топ\n"
        "/me — ваш профиль\n"
        "/stats — статистика чата\n"
        "/ranks — все ранги (25 шт.)\n"
        "/help — это сообщение\n\n"
        "<b>25 рангов от 🌱 Новичка до 🪐 Создателя миров!</b>"
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