"""
🎰 Casino Telegram Bot
Рулетка + Мины | Ежедневный бонус | Перевод денег | Админ-панель

Установка: pip install aiogram
Запуск:    python bot.py
"""

import asyncio
import logging
import random
import sqlite3
from datetime import datetime

from aiogram import Bot, Dispatcher, Router, F
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    Message, CallbackQuery,
    InlineKeyboardMarkup, InlineKeyboardButton
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage

# ===================== КОНФИГУРАЦИЯ =====================
BOT_TOKEN = "7414162488:AAGMey2evupPTVOh4XqjvMA1hvumlZReFKI"          # <-- Вставь токен бота
ADMIN_ID = 6974971620                       # Секретный владелец
START_BALANCE = 5000
DAILY_BONUS = 1000
DEFAULT_ADMIN_CHANCE = 80                   # % шанс победы для админа

# ===================== БАЗА ДАННЫХ =====================
DB = "casino.db"

def _conn():
    return sqlite3.connect(DB)

def init_db():
    with _conn() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS users (
            user_id   INTEGER PRIMARY KEY,
            username  TEXT    DEFAULT '',
            balance   INTEGER DEFAULT 5000,
            last_daily TEXT   DEFAULT '',
            win_chance INTEGER DEFAULT 0,
            total_won  INTEGER DEFAULT 0,
            total_lost INTEGER DEFAULT 0,
            games      INTEGER DEFAULT 0
        )""")

def ensure_user(uid, name=""):
    with _conn() as c:
        c.execute("INSERT OR IGNORE INTO users(user_id,username,balance) VALUES(?,?,?)",
                  (uid, name, START_BALANCE))
        if name:
            c.execute("UPDATE users SET username=? WHERE user_id=? AND username!=?",
                      (name, uid, name))

def get_bal(uid):
    with _conn() as c:
        r = c.execute("SELECT balance FROM users WHERE user_id=?", (uid,)).fetchone()
        return r[0] if r else 0

def add_bal(uid, amt):
    with _conn() as c:
        c.execute("UPDATE users SET balance=balance+? WHERE user_id=?", (amt, uid))

def set_bal(uid, amt):
    with _conn() as c:
        c.execute("UPDATE users SET balance=? WHERE user_id=?", (amt, uid))

def get_user(uid):
    with _conn() as c:
        return c.execute("SELECT * FROM users WHERE user_id=?", (uid,)).fetchone()

def set_daily(uid):
    with _conn() as c:
        c.execute("UPDATE users SET last_daily=? WHERE user_id=?",
                  (datetime.now().isoformat(), uid))

def get_daily(uid):
    u = get_user(uid)
    if u and u[3]:
        try: return datetime.fromisoformat(u[3])
        except: pass
    return None

def set_chance(uid, val):
    with _conn() as c:
        c.execute("UPDATE users SET win_chance=? WHERE user_id=?", (val, uid))

def get_chance(uid):
    u = get_user(uid)
    if not u:
        return 0
    ch = u[4]
    if uid == ADMIN_ID and ch == 0:
        return DEFAULT_ADMIN_CHANCE
    return ch

def stat(uid, won, amt):
    with _conn() as c:
        if won:
            c.execute("UPDATE users SET total_won=total_won+?, games=games+1 WHERE user_id=?",
                      (amt, uid))
        else:
            c.execute("UPDATE users SET total_lost=total_lost+?, games=games+1 WHERE user_id=?",
                      (amt, uid))

def all_users():
    with _conn() as c:
        return c.execute(
            "SELECT user_id,username,balance,games,total_won,total_lost FROM users ORDER BY balance DESC"
        ).fetchall()

# ===================== ПОДКРУТКА =====================
def rigged(uid):
    """Вернёт True/False если подкрутка, None если честная игра."""
    ch = get_chance(uid)
    if ch > 0:
        return random.randint(1, 100) <= ch
    return None

# ===================== РУЛЕТКА =====================
REDS = {1,3,5,7,9,12,14,16,18,19,21,23,25,27,30,32,34,36}
BLACKS = {2,4,6,8,10,11,13,15,17,20,22,24,26,28,29,31,33,35}

def color_emoji(n):
    if n == 0: return "🟢"
    return "🔴" if n in REDS else "⚫"

def win_number(choice):
    m = {
        "red":  lambda: random.choice(list(REDS)),
        "black":lambda: random.choice(list(BLACKS)),
        "green":lambda: 0,
        "even": lambda: random.choice([x for x in range(2,37,2)]),
        "odd":  lambda: random.choice([x for x in range(1,37,2)]),
        "low":  lambda: random.randint(1,18),
        "high": lambda: random.randint(19,36),
    }
    return m.get(choice, lambda: random.randint(0,36))()

def lose_number(choice):
    m = {
        "red":  lambda: random.choice(list(BLACKS)|{0}),
        "black":lambda: random.choice(list(REDS)|{0}),
        "green":lambda: random.randint(1,36),
        "even": lambda: random.choice([x for x in range(1,37,2)]),
        "odd":  lambda: random.choice([x for x in range(2,37,2)]),
        "low":  lambda: random.randint(19,36),
        "high": lambda: random.randint(1,18),
    }
    return m.get(choice, lambda: random.randint(0,36))()

# ===================== МИНЫ =====================
def mines_multi(mines, opened):
    if opened == 0:
        return 1.0
    p = 1.0
    for i in range(opened):
        p *= (25 - mines - i) / (25 - i)
    m = (1.0 / p) * 0.97
    return round(max(m, 1.01), 2)

# ===================== FSM СОСТОЯНИЯ =====================
class RoulSt(StatesGroup):
    bet = State()

class MineSt(StatesGroup):
    bet = State()

class AdminGive(StatesGroup):
    uid = State()
    amt = State()

class AdminChance(StatesGroup):
    uid = State()
    val = State()

class AdminInfo(StatesGroup):
    uid = State()

# ===================== ГЛОБАЛЫ =====================
roul_bets = {}        # uid -> int
mine_games = {}       # uid -> dict
admin_action = {}     # uid -> str  ("give"/"take"/"setbal")

# ===================== БОТ =====================
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())
r = Router()
dp.include_router(r)

# ---------- /start ----------
@r.message(CommandStart())
async def cmd_start(msg: Message):
    uid = msg.from_user.id
    ensure_user(uid, msg.from_user.first_name)
    txt = (
        "🎰 <b>Добро пожаловать в Casino Bot!</b>\n\n"
        f"💰 Баланс: <b>{get_bal(uid)}$</b>\n\n"
        "🎡 /roulette — Рулетка\n"
        "💣 /mines — Мины\n"
        "💰 /balance — Баланс\n"
        "🎁 /daily — Ежедневный бонус\n"
        "💸 /transfer — Перевод\n"
        "🏆 /top — Топ игроков\n"
        "📊 /profile — Профиль\n"
        "ℹ️ /help — Помощь\n"
    )
    if uid == ADMIN_ID:
        txt += "\n🔐 /admin — Секретная панель"
    await msg.answer(txt, parse_mode="HTML")

# ---------- /help ----------
@r.message(Command("help"))
async def cmd_help(msg: Message):
    await msg.answer(
        "📖 <b>Помощь</b>\n\n"
        "<b>🎡 Рулетка</b>\n"
        "Ставьте на цвет (красное/чёрное/зелёное),\n"
        "чётное/нечётное, 1-18/19-36.\n"
        "Зелёное (0) — x14, остальное — x2.\n\n"
        "<b>💣 Мины</b>\n"
        "Поле 5×5. Выбираете кол-во мин.\n"
        "Открывайте клетки — каждая увеличивает множитель.\n"
        "Нажмите «Забрать» чтобы вывести выигрыш.\n"
        "Попали на мину — всё пропало!\n\n"
        "<b>💸 Перевод</b>\n"
        "/transfer &lt;ID&gt; &lt;сумма&gt;\n\n"
        "<b>🎁 Ежедневный бонус</b>\n"
        f"Каждые 24 часа — {DAILY_BONUS}$",
        parse_mode="HTML"
    )

# ---------- /balance ----------
@r.message(Command("balance"))
async def cmd_bal(msg: Message):
    uid = msg.from_user.id
    ensure_user(uid, msg.from_user.first_name)
    await msg.answer(f"💰 Ваш баланс: <b>{get_bal(uid)}$</b>", parse_mode="HTML")

# ---------- /daily ----------
@r.message(Command("daily"))
async def cmd_daily(msg: Message):
    uid = msg.from_user.id
    ensure_user(uid, msg.from_user.first_name)
    last = get_daily(uid)
    now = datetime.now()
    if last and (now - last).total_seconds() < 86400:
        left = 86400 - (now - last).total_seconds()
        h, m2 = int(left // 3600), int(left % 3600 // 60)
        await msg.answer(f"⏳ Бонус уже получен!\nСледующий через: <b>{h}ч {m2}мин</b>",
                         parse_mode="HTML")
        return
    add_bal(uid, DAILY_BONUS)
    set_daily(uid)
    await msg.answer(
        f"🎁 Ежедневный бонус: <b>+{DAILY_BONUS}$</b>\n"
        f"💰 Баланс: <b>{get_bal(uid)}$</b>", parse_mode="HTML")

# ---------- /profile ----------
@r.message(Command("profile"))
async def cmd_profile(msg: Message):
    uid = msg.from_user.id
    ensure_user(uid, msg.from_user.first_name)
    u = get_user(uid)
    await msg.answer(
        f"👤 <b>{u[1]}</b>\n\n"
        f"💰 Баланс: <b>{u[2]}$</b>\n"
        f"🎮 Игр: <b>{u[7]}</b>\n"
        f"✅ Выиграно: <b>{u[5]}$</b>\n"
        f"❌ Проиграно: <b>{u[6]}$</b>\n"
        f"📊 Профит: <b>{u[5]-u[6]}$</b>",
        parse_mode="HTML")

# ---------- /top ----------
@r.message(Command("top"))
async def cmd_top(msg: Message):
    users = all_users()[:10]
    medals = ["🥇","🥈","🥉"]
    txt = "🏆 <b>Топ игроков</b>\n\n"
    for i, u in enumerate(users):
        m = medals[i] if i < 3 else f"<b>{i+1}.</b>"
        txt += f"{m} {u[1]} — <b>{u[2]}$</b>\n"
    if not users:
        txt += "Пока пусто."
    await msg.answer(txt, parse_mode="HTML")

# ---------- /transfer ----------
@r.message(Command("transfer"))
async def cmd_transfer(msg: Message):
    uid = msg.from_user.id
    ensure_user(uid, msg.from_user.first_name)
    parts = msg.text.split()
    if len(parts) != 3:
        await msg.answer("💸 Формат: /transfer &lt;ID&gt; &lt;сумма&gt;", parse_mode="HTML")
        return
    try:
        tid, amt = int(parts[1]), int(parts[2])
    except:
        await msg.answer("❌ Неверный формат!")
        return
    if amt <= 0:
        await msg.answer("❌ Сумма должна быть > 0!")
        return
    if tid == uid:
        await msg.answer("❌ Нельзя перевести себе!")
        return
    bal = get_bal(uid)
    if bal < amt:
        await msg.answer(f"❌ Не хватает средств! Баланс: {bal}$")
        return
    t = get_user(tid)
    if not t:
        await msg.answer("❌ Пользователь не найден!")
        return
    add_bal(uid, -amt)
    add_bal(tid, amt)
    await msg.answer(
        f"✅ Переведено <b>{amt}$</b> → {t[1]}\n"
        f"💰 Баланс: <b>{get_bal(uid)}$</b>", parse_mode="HTML")
    try:
        await bot.send_message(tid,
            f"💸 Вам перевели <b>{amt}$</b> от {msg.from_user.first_name}\n"
            f"💰 Баланс: <b>{get_bal(tid)}$</b>", parse_mode="HTML")
    except:
        pass

# ==================== РУЛЕТКА ====================
@r.message(Command("roulette"))
async def cmd_roul(msg: Message, state: FSMContext):
    uid = msg.from_user.id
    ensure_user(uid, msg.from_user.first_name)
    bal = get_bal(uid)
    if bal <= 0:
        await msg.answer("❌ Нет денег! Используй /daily")
        return
    await state.set_state(RoulSt.bet)
    await msg.answer(
        f"🎡 <b>Рулетка</b>\n💰 Баланс: <b>{bal}$</b>\n\nВведите ставку:",
        parse_mode="HTML")

@r.message(RoulSt.bet)
async def roul_bet(msg: Message, state: FSMContext):
    uid = msg.from_user.id
    try:
        bet = int(msg.text)
    except:
        await msg.answer("❌ Введите число!")
        return
    if bet <= 0:
        await msg.answer("❌ Ставка > 0!")
        return
    bal = get_bal(uid)
    if bet > bal:
        await msg.answer(f"❌ Недостаточно! Баланс: {bal}$")
        return
    roul_bets[uid] = bet
    await state.clear()
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔴 Красное x2", callback_data="rl_red"),
         InlineKeyboardButton(text="⚫ Чёрное x2", callback_data="rl_black")],
        [InlineKeyboardButton(text="🟢 Зелёное x14", callback_data="rl_green")],
        [InlineKeyboardButton(text="Чётное x2", callback_data="rl_even"),
         InlineKeyboardButton(text="Нечётное x2", callback_data="rl_odd")],
        [InlineKeyboardButton(text="1-18 x2", callback_data="rl_low"),
         InlineKeyboardButton(text="19-36 x2", callback_data="rl_high")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="rl_cancel")],
    ])
    await msg.answer(f"🎡 Ставка: <b>{bet}$</b>\nВыберите:", reply_markup=kb, parse_mode="HTML")

@r.callback_query(F.data.startswith("rl_"))
async def roul_play(cb: CallbackQuery):
    uid = cb.from_user.id
    ch = cb.data[3:]
    if ch == "cancel":
        roul_bets.pop(uid, None)
        await cb.message.edit_text("❌ Отменено.")
        return
    if uid not in roul_bets:
        await cb.answer("Ставка не найдена! /roulette", show_alert=True)
        return
    bet = roul_bets.pop(uid)
    bal = get_bal(uid)
    if bet > bal:
        await cb.message.edit_text("❌ Недостаточно средств!")
        return

    # Снимаем ставку
    add_bal(uid, -bet)

    # Подкрутка
    rig = rigged(uid)
    if rig is True:
        num = win_number(ch)
    elif rig is False:
        num = lose_number(ch)
    else:
        num = random.randint(0, 36)

    ce = color_emoji(num)
    won = False
    mult = 0
    checks = {
        "red":   num in REDS,
        "black": num in BLACKS,
        "green": num == 0,
        "even":  num != 0 and num % 2 == 0,
        "odd":   num != 0 and num % 2 == 1,
        "low":   1 <= num <= 18,
        "high":  19 <= num <= 36,
    }
    mults = {"red":2,"black":2,"green":14,"even":2,"odd":2,"low":2,"high":2}
    if checks.get(ch, False):
        won, mult = True, mults[ch]

    if won:
        winnings = bet * mult
        add_bal(uid, winnings)
        profit = winnings - bet
        stat(uid, True, profit)
        txt = (f"🎡 Выпало: {ce} <b>{num}</b>\n\n"
               f"🎉 <b>Победа!</b> +{profit}$ (x{mult})\n"
               f"💰 Баланс: <b>{get_bal(uid)}$</b>")
    else:
        stat(uid, False, bet)
        txt = (f"🎡 Выпало: {ce} <b>{num}</b>\n\n"
               f"😔 <b>Проигрыш:</b> -{bet}$\n"
               f"💰 Баланс: <b>{get_bal(uid)}$</b>")

    await cb.message.edit_text(txt, parse_mode="HTML")

# ==================== МИНЫ ====================
@r.message(Command("mines"))
async def cmd_mines(msg: Message, state: FSMContext):
    uid = msg.from_user.id
    ensure_user(uid, msg.from_user.first_name)
    if uid in mine_games:
        await msg.answer("❌ У вас уже идёт игра! Завершите её.")
        return
    bal = get_bal(uid)
    if bal <= 0:
        await msg.answer("❌ Нет денег! Используй /daily")
        return
    await state.set_state(MineSt.bet)
    await msg.answer(
        f"💣 <b>Мины</b>\n💰 Баланс: <b>{bal}$</b>\n\nВведите ставку:",
        parse_mode="HTML")

@r.message(MineSt.bet)
async def mine_bet(msg: Message, state: FSMContext):
    uid = msg.from_user.id
    try:
        bet = int(msg.text)
    except:
        await msg.answer("❌ Введите число!")
        return
    if bet <= 0:
        await msg.answer("❌ Ставка > 0!")
        return
    bal = get_bal(uid)
    if bet > bal:
        await msg.answer(f"❌ Недостаточно! Баланс: {bal}$")
        return
    await state.clear()
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"{x} 💣", callback_data=f"mc_{x}") for x in [1,3,5]],
        [InlineKeyboardButton(text=f"{x} 💣", callback_data=f"mc_{x}") for x in [7,10,15]],
        [InlineKeyboardButton(text=f"{x} 💣", callback_data=f"mc_{x}") for x in [20,24]],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="mc_0")],
    ])
    roul_bets[uid] = bet  # temp storage
    await msg.answer(
        f"💣 Ставка: <b>{bet}$</b>\nВыберите кол-во мин (поле 5×5):",
        reply_markup=kb, parse_mode="HTML")

@r.callback_query(F.data.startswith("mc_"))
async def mine_start(cb: CallbackQuery):
    uid = cb.from_user.id
    cnt = int(cb.data[3:])
    if cnt == 0:
        roul_bets.pop(uid, None)
        await cb.message.edit_text("❌ Отменено.")
        return
    bet = roul_bets.pop(uid, None)
    if not bet:
        await cb.answer("Ошибка! /mines", show_alert=True)
        return
    bal = get_bal(uid)
    if bet > bal:
        await cb.message.edit_text("❌ Недостаточно средств!")
        return

    add_bal(uid, -bet)
    mines_pos = set(random.sample(range(25), cnt))

    mine_games[uid] = {
        "bet": bet, "cnt": cnt,
        "mines": mines_pos, "open": set(), "over": False
    }
    kb = _mines_kb(uid)
    m = mines_multi(cnt, 0)
    await cb.message.edit_text(
        f"💣 <b>Мины</b> | Ставка: <b>{bet}$</b>\n"
        f"💣 Мин: <b>{cnt}</b> | 💎 Открыто: <b>0</b>\n"
        f"📈 x{m} | 💰 0$",
        reply_markup=kb, parse_mode="HTML")

def _mines_kb(uid):
    g = mine_games[uid]
    rows = []
    for row in range(5):
        btns = []
        for col in range(5):
            p = row * 5 + col
            if p in g["open"]:
                t = "💥" if p in g["mines"] else "💎"
                btns.append(InlineKeyboardButton(text=t, callback_data=f"mn_{p}"))
            elif g["over"]:
                t = "💣" if p in g["mines"] else "⬜"
                btns.append(InlineKeyboardButton(text=t, callback_data=f"mn_{p}"))
            else:
                btns.append(InlineKeyboardButton(text="⬛", callback_data=f"mn_{p}"))
        rows.append(btns)
    if not g["over"] and len(g["open"]) > 0:
        m = mines_multi(g["cnt"], len(g["open"]))
        w = int(g["bet"] * m)
        rows.append([InlineKeyboardButton(
            text=f"💰 Забрать {w}$ (x{m})", callback_data="mn_cash")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

@r.callback_query(F.data == "mn_cash")
async def mine_cash(cb: CallbackQuery):
    uid = cb.from_user.id
    if uid not in mine_games:
        await cb.answer("Нет игры!", show_alert=True)
        return
    g = mine_games[uid]
    if g["over"] or len(g["open"]) == 0:
        await cb.answer("Невозможно!")
        return
    m = mines_multi(g["cnt"], len(g["open"]))
    w = int(g["bet"] * m)
    profit = w - g["bet"]
    add_bal(uid, w)
    stat(uid, True, profit)
    g["over"] = True
    kb = _mines_kb(uid)
    del mine_games[uid]
    await cb.message.edit_text(
        f"💰 <b>Вы забрали выигрыш!</b>\n\n"
        f"💎 Открыто: <b>{len(g['open'])}</b>\n"
        f"📈 Множитель: <b>x{m}</b>\n"
        f"💰 Профит: <b>+{profit}$</b>\n"
        f"💰 Баланс: <b>{get_bal(uid)}$</b>",
        reply_markup=kb, parse_mode="HTML")

@r.callback_query(F.data.startswith("mn_"))
async def mine_click(cb: CallbackQuery):
    uid = cb.from_user.id
    if uid not in mine_games:
        await cb.answer()
        return
    g = mine_games[uid]
    if g["over"]:
        await cb.answer()
        return
    pos = int(cb.data[3:])
    if pos in g["open"]:
        await cb.answer()
        return

    # === ПОДКРУТКА ===
    rig = rigged(uid)
    if rig is True:
        # Должен выиграть — убираем мину если она тут
        if pos in g["mines"]:
            safe = [p for p in range(25) if p not in g["open"] and p != pos and p not in g["mines"]]
            if safe:
                g["mines"].discard(pos)
                g["mines"].add(random.choice(safe))
    elif rig is False:
        # Должен проиграть — ставим мину сюда
        if pos not in g["mines"]:
            old = [p for p in g["mines"] if p not in g["open"]]
            if old:
                rm = random.choice(list(old))
                g["mines"].discard(rm)
                g["mines"].add(pos)

    g["open"].add(pos)

    if pos in g["mines"]:
        # ВЗРЫВ
        g["over"] = True
        stat(uid, False, g["bet"])
        kb = _mines_kb(uid)
        del mine_games[uid]
        await cb.message.edit_text(
            f"💥 <b>БУМ! Мина!</b>\n\n"
            f"💸 Проигрыш: <b>-{g['bet']}$</b>\n"
            f"💰 Баланс: <b>{get_bal(uid)}$</b>",
            reply_markup=kb, parse_mode="HTML")
    else:
        opened = len(g["open"])
        safe_total = 25 - g["cnt"]
        m = mines_multi(g["cnt"], opened)
        w = int(g["bet"] * m)

        if opened >= safe_total:
            # Все безопасные открыты
            g["over"] = True
            profit = w - g["bet"]
            add_bal(uid, w)
            stat(uid, True, profit)
            kb = _mines_kb(uid)
            del mine_games[uid]
            await cb.message.edit_text(
                f"🎉 <b>ВСЕ КЛЕТКИ ОТКРЫТЫ!</b>\n\n"
                f"📈 x{m} | 💰 +{profit}$\n"
                f"💰 Баланс: <b>{get_bal(uid)}$</b>",
                reply_markup=kb, parse_mode="HTML")
        else:
            kb = _mines_kb(uid)
            await cb.message.edit_text(
                f"💣 <b>Мины</b> | Ставка: <b>{g['bet']}$</b>\n"
                f"💣 Мин: <b>{g['cnt']}</b> | 💎 Открыто: <b>{opened}</b>\n"
                f"📈 x{m} | 💰 {w}$",
                reply_markup=kb, parse_mode="HTML")
    await cb.answer()

# ==================== АДМИН-ПАНЕЛЬ ====================
@r.message(Command("admin"))
async def cmd_admin(msg: Message):
    if msg.from_user.id != ADMIN_ID:
        return
    ch = get_chance(ADMIN_ID)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💰 Выдать деньги", callback_data="ap_give"),
         InlineKeyboardButton(text="💸 Забрать деньги", callback_data="ap_take")],
        [InlineKeyboardButton(text="💰 Установить баланс", callback_data="ap_setbal")],
        [InlineKeyboardButton(text="🎯 Установить шанс", callback_data="ap_chance"),
         InlineKeyboardButton(text="📊 Мой шанс", callback_data="ap_myc")],
        [InlineKeyboardButton(text="👥 Все пользователи", callback_data="ap_users"),
         InlineKeyboardButton(text="🔍 Инфо о юзере", callback_data="ap_info")],
        [InlineKeyboardButton(text="📢 Рассылка", callback_data="ap_broadcast")],
    ])
    await msg.answer(
        f"🔐 <b>Секретная панель</b>\n\n🎯 Ваш шанс: <b>{ch}%</b>",
        reply_markup=kb, parse_mode="HTML")

@r.callback_query(F.data == "ap_myc")
async def ap_myc(cb: CallbackQuery):
    if cb.from_user.id != ADMIN_ID: return
    await cb.answer(f"Ваш шанс: {get_chance(ADMIN_ID)}%", show_alert=True)

@r.callback_query(F.data == "ap_users")
async def ap_users(cb: CallbackQuery):
    if cb.from_user.id != ADMIN_ID: return
    users = all_users()
    txt = "👥 <b>Пользователи:</b>\n\n"
    for u in users[:30]:
        ch = get_chance(u[0])
        txt += f"<code>{u[0]}</code> | {u[1]} | {u[2]}$ | 🎮{u[3]} | 🎯{ch}%\n"
    if not users:
        txt += "Пусто"
    await cb.message.edit_text(txt[:4000], parse_mode="HTML")

# --- Выдать / Забрать / Установить баланс ---
@r.callback_query(F.data.in_({"ap_give", "ap_take", "ap_setbal"}))
async def ap_money_start(cb: CallbackQuery, state: FSMContext):
    if cb.from_user.id != ADMIN_ID: return
    act = cb.data[3:]  # give / take / setbal
    admin_action[cb.from_user.id] = act
    await state.set_state(AdminGive.uid)
    labels = {"give": "выдачи", "take": "списания", "setbal": "установки баланса"}
    await cb.message.edit_text(f"Введите ID пользователя для {labels[act]}:")

@r.message(AdminGive.uid)
async def ap_money_uid(msg: Message, state: FSMContext):
    if msg.from_user.id != ADMIN_ID: return
    try:
        tid = int(msg.text)
    except:
        await msg.answer("❌ Числовой ID!")
        return
    u = get_user(tid)
    if not u:
        await msg.answer("❌ Не найден!")
        return
    await state.update_data(tid=tid)
    await state.set_state(AdminGive.amt)
    act = admin_action.get(msg.from_user.id, "give")
    if act == "setbal":
        await msg.answer(f"Юзер: {u[1]} (баланс: {u[2]}$)\nВведите новый баланс:")
    else:
        await msg.answer(f"Юзер: {u[1]} (баланс: {u[2]}$)\nВведите сумму:")

@r.message(AdminGive.amt)
async def ap_money_amt(msg: Message, state: FSMContext):
    if msg.from_user.id != ADMIN_ID: return
    try:
        amt = int(msg.text)
    except:
        await msg.answer("❌ Число!")
        return
    data = await state.get_data()
    tid = data["tid"]
    act = admin_action.get(msg.from_user.id, "give")
    if act == "give":
        add_bal(tid, amt)
        await msg.answer(f"✅ Выдано {amt}$. Баланс: {get_bal(tid)}$")
    elif act == "take":
        add_bal(tid, -amt)
        await msg.answer(f"✅ Списано {amt}$. Баланс: {get_bal(tid)}$")
    elif act == "setbal":
        set_bal(tid, amt)
        await msg.answer(f"✅ Баланс = {amt}$")
    await state.clear()

# --- Установить шанс ---
@r.callback_query(F.data == "ap_chance")
async def ap_chance_start(cb: CallbackQuery, state: FSMContext):
    if cb.from_user.id != ADMIN_ID: return
    await state.set_state(AdminChance.uid)
    await cb.message.edit_text("Введите ID (или <code>me</code> для себя):", parse_mode="HTML")

@r.message(AdminChance.uid)
async def ap_chance_uid(msg: Message, state: FSMContext):
    if msg.from_user.id != ADMIN_ID: return
    if msg.text.lower() == "me":
        tid = ADMIN_ID
    else:
        try:
            tid = int(msg.text)
        except:
            await msg.answer("❌ Число или 'me'!")
            return
    cur = get_chance(tid)
    await state.update_data(tid=tid)
    await state.set_state(AdminChance.val)
    await msg.answer(f"Текущий шанс: {cur}%\nВведите новый (0-100, 0 = честная игра):")

@r.message(AdminChance.val)
async def ap_chance_val(msg: Message, state: FSMContext):
    if msg.from_user.id != ADMIN_ID: return
    try:
        v = int(msg.text)
    except:
        await msg.answer("❌ Число 0-100!")
        return
    if not 0 <= v <= 100:
        await msg.answer("❌ 0-100!")
        return
    data = await state.get_data()
    set_chance(data["tid"], v)
    await msg.answer(f"✅ Шанс = {v}%")
    await state.clear()

# --- Инфо о юзере ---
@r.callback_query(F.data == "ap_info")
async def ap_info_start(cb: CallbackQuery, state: FSMContext):
    if cb.from_user.id != ADMIN_ID: return
    await state.set_state(AdminInfo.uid)
    await cb.message.edit_text("Введите ID пользователя:")

@r.message(AdminInfo.uid)
async def ap_info_show(msg: Message, state: FSMContext):
    if msg.from_user.id != ADMIN_ID: return
    try:
        tid = int(msg.text)
    except:
        await msg.answer("❌ Числовой ID!")
        await state.clear()
        return
    u = get_user(tid)
    if not u:
        await msg.answer("❌ Не найден!")
        await state.clear()
        return
    ch = get_chance(tid)
    await msg.answer(
        f"🔍 <b>Юзер</b>\n\n"
        f"👤 {u[1]}\n🆔 <code>{u[0]}</code>\n"
        f"💰 {u[2]}$\n🎮 Игр: {u[7]}\n"
        f"✅ Выиграно: {u[5]}$\n❌ Проиграно: {u[6]}$\n"
        f"🎯 Шанс: {ch}%", parse_mode="HTML")
    await state.clear()

# --- Рассылка ---
class BrState(StatesGroup):
    txt = State()

@r.callback_query(F.data == "ap_broadcast")
async def ap_br(cb: CallbackQuery, state: FSMContext):
    if cb.from_user.id != ADMIN_ID: return
    await state.set_state(BrState.txt)
    await cb.message.edit_text("📢 Введите текст рассылки:")

@r.message(BrState.txt)
async def ap_br_send(msg: Message, state: FSMContext):
    if msg.from_user.id != ADMIN_ID: return
    users = all_users()
    ok, fail = 0, 0
    for u in users:
        try:
            await bot.send_message(u[0], f"📢 <b>Рассылка</b>\n\n{msg.text}", parse_mode="HTML")
            ok += 1
        except:
            fail += 1
    await msg.answer(f"✅ Отправлено: {ok} | ❌ Ошибки: {fail}")
    await state.clear()

# ==================== ЗАПУСК ====================
async def main():
    init_db()
    ensure_user(ADMIN_ID, "Owner")
    if get_chance(ADMIN_ID) == 0:
        set_chance(ADMIN_ID, DEFAULT_ADMIN_CHANCE)
    logging.basicConfig(level=logging.INFO)
    print("🎰 Casino Bot запущен!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
