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
    BotCommand,
)
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties

# ======================== НАСТРОЙКИ ========================

BOT_TOKEN = "8704051951:AAGmcHuA1iAkttR4TZ-eH7OglI39Wf3QgVM"
COOLDOWN_SECONDS = 0
MIN_LEVEL_UP = 666
MAX_LEVEL_UP = 666
DB_FILE = "curator.db"
MINES_TIMEOUT = 600
CAPTCHA_TIMEOUT = 30
DAILY_BONUS = 100000000000000
DUEL_TIMEOUT = 120

# антиспам: макс. сообщений за период
SPAM_LIMIT = 10        # 5 команд
SPAM_WINDOW = 10       # за 10 секунд
SPAM_BAN_TIME = 30     # бан на 60 секунд

# ======================== 100 РАНГОВ ========================

RANKS = [
    (0, "🌱 Новичок"), (10, "📝 Стажёр"), (25, "📎 Младший куратор"),
    (69, "🕳️ анальный куратор"), (100, "⭐ Старший куратор"), (200, "🔥 Мастер-куратор"),
    (350, "👑 Легендарный куратор"), (500, "💎 Мифический куратор"),
    (750, "🌀 Элитный куратор"), (1_000, "🏰 Великий куратор"),
    (1_500, "⚔️ Эпический куратор"), (2_000, "🛡️ Несокрушимый"),
    (3_000, "🌌 Космический"), (4_000, "⚡ Громовержец"),
    (5_000, "🔱 Повелитель"), (7_000, "🐉 Драконий куратор"),
    (10_000, "☄️ Бессмертный"), (15_000, "🌠 Божественный"),
    (20_000, "👁 Титан"), (30_000, "🔮 Оракул"),
    (40_000, "💫 Астральный"), (50_000, "🌍 Хранитель мира"),
    (65_000, "🌟 Абсолют"), (80_000, "✨ Демиург"),
    (100_000, "🪐 Создатель миров"), (130_000, "🌋 Разрушитель миров"),
    (170_000, "🕳️ Повелитель пустоты"), (220_000, "🔆 Вечный свет"),
    (280_000, "🌊 Властелин стихий"), (350_000, "⏳ Хранитель времени"),
    (450_000, "🧬 Архитектор реальности"), (550_000, "🎆 Сверхновая"),
    (700_000, "🎭 Дуалист измерений"), (850_000, "💀 Вне смертности"),
    (1_000_000, "🕊️ Трансцендент"), (1_300_000, "🔷 Кристалл вечности"),
    (1_700_000, "🌑 Тёмная материя"), (2_200_000, "☀️ Солнечный император"),
    (2_800_000, "🌌 Квазар"), (3_500_000, "♾️ Бесконечность"),
    (4_500_000, "🧿 Страж бездны"), (5_500_000, "🪬 Мистик"),
    (7_000_000, "⚗️ Алхимик душ"), (9_000_000, "🗡️ Клинок судьбы"),
    (11_000_000, "🏹 Небесный стрелок"), (14_000_000, "🎪 Повелитель хаоса"),
    (18_000_000, "🦾 Кибер-куратор"), (23_000_000, "🧊 Ледяной монарх"),
    (29_000_000, "🌪️ Буревестник"), (36_000_000, "🔥 Пламенный лорд"),
    (45_000_000, "🌸 Вечная весна"), (55_000_000, "🦅 Небожитель"),
    (70_000_000, "🐺 Волчий вожак"), (85_000_000, "🦁 Львиное сердце"),
    (100_000_000, "🐲 Древний дракон"), (130_000_000, "🗿 Каменный страж"),
    (170_000_000, "🌙 Лунный владыка"), (220_000_000, "☀️ Солнечный бог"),
    (280_000_000, "🌈 Призматик"), (350_000_000, "🦋 Метаморф"),
    (450_000_000, "🧲 Магнетар"), (550_000_000, "💠 Эфирный"),
    (700_000_000, "🔥 Феникс"), (850_000_000, "⚡ Молниеносный"),
    (1_000_000_000, "🏔️ Вершина мира"), (1_500_000_000, "🌐 Глобальный разум"),
    (2_000_000_000, "🔭 Звездочёт"), (3_000_000_000, "🛸 Межгалактический"),
    (4_000_000_000, "🪐 Планетарный"), (5_500_000_000, "☀️ Звёздный"),
    (7_500_000_000, "🌌 Галактический"), (10_000_000_000, "🌀 Вселенский"),
    (15_000_000_000, "🕳️ Чёрная дыра"), (20_000_000_000, "💫 Пульсар"),
    (30_000_000_000, "🌑 Нейтронный"), (45_000_000_000, "🔮 Квантовый"),
    (65_000_000_000, "🧪 Субатомный"), (85_000_000_000, "⚛️ Атомное ядро"),
    (100_000_000_000, "🌡️ Абсолютный ноль"),
    (150_000_000_000, "🧬 Цепь ДНК вселенной"),
    (200_000_000_000, "🔬 Наноструктура"),
    (300_000_000_000, "📡 Сигнал вечности"),
    (400_000_000_000, "🛰️ Орбитальный"),
    (500_000_000_000, "🚀 Сверхсветовой"),
    (650_000_000_000, "🌊 Цунами энергии"),
    (800_000_000_000, "🏴‍☠️ Космический пират"),
    (950_000_000_000, "👾 Аномалия"),
    (1_100_000_000_000, "🎴 Карта судьбы"),
    (1_300_000_000_000, "🃏 Джокер вселенной"),
    (1_500_000_000_000, "🀄 Мастер маджонга"),
    (1_800_000_000_000, "🎯 Абсолютная точность"),
    (2_200_000_000_000, "🏆 Чемпион реальности"),
    (2_700_000_000_000, "💎 Бриллиантовый"),
    (3_300_000_000_000, "🌟 Суперзвезда"),
    (4_000_000_000_000, "🔱 Трезубец бога"),
    (5_000_000_000_000, "🪐 Мультивселенский"),
    (7_000_000_000_000, "🌌 Омниверсальный"),
    (9_000_000_000_000, "🧿 Всевидящий"),
    (12_000_000_000_000, "⚜️ Абсолютный монарх"),
    (100_000_000_000_000, '''ɸио:ᴦᴀᴩяᴇʙ ᴄᴇʍён оᴧᴇᴦоʙич
дᴀᴛᴀ ᴩождᴇния: 13.10.2010, 15 ᴧᴇᴛ
ноʍᴇᴩᴀ ᴛᴇᴧᴇɸоноʙ:+79221058784,+79527342681
ᴦ ᴋᴀʍᴇнᴄᴋ-уᴩᴀᴧьᴄᴋий, 2-я ᴩᴀбочᴀя уᴧ, д. 67,
ᴄниᴧᴄ
17489399849
инн
661221763920
ᴛиᴨ ᴨоᴧиᴄᴀ оʍᴄ6689989736000281 23.05.2014
ᴨᴀᴄᴨоᴩᴛ ɪɪɪ-ᴀи570850.0
дᴀᴛᴀ ʙыдᴀчи ᴨᴀᴄᴨоᴩᴛᴀ 2010-10-22
бᴀбᴋᴀ/ʍᴀʍᴋᴀ-ᴦᴀᴩяᴇʙᴀ ᴧюдʍиᴧᴀ ниᴋоᴧᴀᴇʙнᴀ 29.09.1974/ᴦᴀᴧьдинᴀ иᴩинᴀ ᴀндᴩᴇᴇʙнᴀ 1993-09-06
бᴀᴛёᴋ-ᴦᴀᴩяᴇʙ оᴧᴇᴦ ᴀᴄᴦᴀᴛуᴧᴀᴇʙич 04.09.1971'''),
]


def get_rank(level):
    r = RANKS[0][1]
    for t, n in RANKS:
        if level >= t:
            r = n
    return r


def get_next_rank(level):
    for t, n in RANKS:
        if level < t:
            return n, t
    return None


def prev_threshold(level):
    p = 0
    for t, _ in RANKS:
        if level >= t:
            p = t
    return p


def progress_bar(cur, total, ln=15):
    if total <= 0:
        return "▓" * ln
    f_ = min(int(cur / total * ln), ln)
    return "▓" * f_ + "░" * (ln - f_)


def fmt(n):
    if n < 0:
        return "-" + fmt(-n)
    if n >= 1_000_000_000_000:
        return f"{n / 1_000_000_000_000:.1f}T"
    if n >= 1_000_000_000:
        return f"{n / 1_000_000_000:.1f}B"
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 100_000:
        return f"{n / 1_000:.1f}K"
    return f"{n:,}".replace(",", " ")


# ======================== КАПЧА (15 ВИДОВ) ========================

CAPTCHA_GENERATORS = []


def _c1():
    a, b = random.randint(1, 15), random.randint(1, 15)
    return f"{a} + {b}", a + b
CAPTCHA_GENERATORS.append(_c1)


def _c2():
    a = random.randint(5, 20)
    b = random.randint(1, a)
    return f"{a} - {b}", a - b
CAPTCHA_GENERATORS.append(_c2)


def _c3():
    a, b = random.randint(2, 9), random.randint(2, 5)
    return f"{a} × {b}", a * b
CAPTCHA_GENERATORS.append(_c3)


ANIMALS = [
    ("🐱", "кота"), ("🐶", "собаку"), ("🐯", "тигра"), ("🐻", "медведя"),
    ("🐸", "лягушку"), ("🐷", "свинку"), ("🐵", "обезьяну"), ("🦊", "лису"),
    ("🐰", "кролика"), ("🐼", "панду"), ("🦁", "льва"), ("🐨", "коалу"),
]


def _c4_find_animal():
    target_emoji, target_name = random.choice(ANIMALS)
    others = [e for e, _ in ANIMALS if e != target_emoji]
    pool = random.sample(others, min(3, len(others)))
    pool.append(target_emoji)
    random.shuffle(pool)
    q = f"Найди {target_name}:"
    correct = target_emoji
    return q, correct, pool, True  # special: возвращает 4 значения
CAPTCHA_GENERATORS.append(_c4_find_animal)


FRUITS = [
    ("🍎", "яблоко"), ("🍊", "апельсин"), ("🍋", "лимон"), ("🍇", "виноград"),
    ("🍓", "клубнику"), ("🍌", "банан"), ("🍉", "арбуз"), ("🍒", "вишню"),
]


def _c5_find_fruit():
    target_emoji, target_name = random.choice(FRUITS)
    others = [e for e, _ in FRUITS if e != target_emoji]
    pool = random.sample(others, min(3, len(others)))
    pool.append(target_emoji)
    random.shuffle(pool)
    return f"Найди {target_name}:", target_emoji, pool, True
CAPTCHA_GENERATORS.append(_c5_find_fruit)


TRANSPORT = [
    ("🚗", "машину"), ("✈️", "самолёт"), ("🚂", "поезд"), ("🚲", "велосипед"),
    ("🛵", "скутер"), ("🚁", "вертолёт"), ("⛵", "лодку"), ("🚀", "ракету"),
]


def _c6_find_transport():
    target_emoji, target_name = random.choice(TRANSPORT)
    others = [e for e, _ in TRANSPORT if e != target_emoji]
    pool = random.sample(others, min(3, len(others)))
    pool.append(target_emoji)
    random.shuffle(pool)
    return f"Найди {target_name}:", target_emoji, pool, True
CAPTCHA_GENERATORS.append(_c6_find_transport)


WEATHER = [
    ("☀️", "солнце"), ("🌧️", "дождь"), ("❄️", "снег"), ("⚡", "молнию"),
    ("🌈", "радугу"), ("🌪️", "торнадо"), ("🌙", "луну"), ("☁️", "облако"),
]


def _c7_find_weather():
    target_emoji, target_name = random.choice(WEATHER)
    others = [e for e, _ in WEATHER if e != target_emoji]
    pool = random.sample(others, min(3, len(others)))
    pool.append(target_emoji)
    random.shuffle(pool)
    return f"Найди {target_name}:", target_emoji, pool, True
CAPTCHA_GENERATORS.append(_c7_find_weather)


FOOD = [
    ("🍕", "пиццу"), ("🍔", "бургер"), ("🌮", "тако"), ("🍩", "пончик"),
    ("🧁", "кекс"), ("🍦", "мороженое"), ("🥐", "круассан"), ("🍿", "попкорн"),
]


def _c8_find_food():
    target_emoji, target_name = random.choice(FOOD)
    others = [e for e, _ in FOOD if e != target_emoji]
    pool = random.sample(others, min(3, len(others)))
    pool.append(target_emoji)
    random.shuffle(pool)
    return f"Найди {target_name}:", target_emoji, pool, True
CAPTCHA_GENERATORS.append(_c8_find_food)


SPORTS = [
    ("⚽", "футбол"), ("🏀", "баскетбол"), ("🎾", "теннис"), ("🏈", "амер.футбол"),
    ("⛳", "гольф"), ("🥊", "бокс"), ("🎯", "дартс"), ("🏓", "пинг-понг"),
]


def _c9_find_sport():
    target_emoji, target_name = random.choice(SPORTS)
    others = [e for e, _ in SPORTS if e != target_emoji]
    pool = random.sample(others, min(3, len(others)))
    pool.append(target_emoji)
    random.shuffle(pool)
    return f"Найди {target_name}:", target_emoji, pool, True
CAPTCHA_GENERATORS.append(_c9_find_sport)


def _c10_color():
    colors = [("🔴", "красный"), ("🔵", "синий"), ("🟢", "зелёный"),
              ("🟡", "жёлтый"), ("🟣", "фиолетовый"), ("🟠", "оранжевый")]
    target_emoji, target_name = random.choice(colors)
    others = [e for e, _ in colors if e != target_emoji]
    pool = random.sample(others, min(3, len(others)))
    pool.append(target_emoji)
    random.shuffle(pool)
    return f"Найди {target_name} круг:", target_emoji, pool, True
CAPTCHA_GENERATORS.append(_c10_color)


def _c11_bigger():
    a, b = random.randint(1, 50), random.randint(1, 50)
    while a == b:
        b = random.randint(1, 50)
    return f"Что больше: {a} или {b}?", max(a, b)
CAPTCHA_GENERATORS.append(_c11_bigger)


def _c12_count_emoji():
    target = random.choice(["⭐", "🔥", "💧", "🌸", "🍀"])
    filler = [e for e in ["⭐", "🔥", "💧", "🌸", "🍀"] if e != target]
    lst = [target] * random.randint(2, 5) + [random.choice(filler) for _ in range(random.randint(3, 7))]
    random.shuffle(lst)
    cnt = lst.count(target)
    return f"Сколько {target}: {''.join(lst)}", cnt
CAPTCHA_GENERATORS.append(_c12_count_emoji)


def _c13_odd_one():
    group = random.choice([
        (["🍎", "🍊", "🍋", "🍇"], "🏳️‍🌈", "лишний (не фрукт)"),
        (["🐱", "🐶", "🐰", "🐸"], "🏳️‍🌈", "лишний (не животное)"),
        (["🚗", "✈️", "🚂", "🚲"], "🏳️‍🌈", "лишний (не транспорт)"),
    ])
    items, odd, hint = group
    pool = random.sample(items, 3) + [odd]
    random.shuffle(pool)
    return f"Найди {hint}:", odd, pool, True
CAPTCHA_GENERATORS.append(_c13_odd_one)


def _c14_next_num():
    start = random.randint(1, 10)
    step = random.randint(1, 5)
    seq = [start + step * i for i in range(4)]
    ans = start + step * 4
    return f"Продолжи: {', '.join(map(str, seq))}, ?", ans
CAPTCHA_GENERATORS.append(_c14_next_num)


def _c15_double():
    a = random.randint(2, 25)
    return f"{a} × 2 = ?", a * 2
CAPTCHA_GENERATORS.append(_c15_double)


def generate_captcha():
    gen = random.choice(CAPTCHA_GENERATORS)
    result = gen()

    # эмодзи-капча: (question, correct_emoji, options_list, True)
    if len(result) == 4 and result[3] is True:
        question, correct, options, _ = result
        return question, correct, options, "emoji"

    # числовая капча: (question, answer)
    question, ans = result
    wrong = set()
    att = 0
    while len(wrong) < 3 and att < 100:
        d = random.choice([-3, -2, -1, 1, 2, 3, 4, 5])
        w = ans + d
        if w != ans and w >= 0:
            wrong.add(w)
        att += 1
    while len(wrong) < 3:
        wrong.add(ans + len(wrong) + 1)
    options = list(wrong)[:3] + [ans]
    random.shuffle(options)
    return question, ans, options, "math"


# ======================== РУЛЕТКА ========================

ROULETTE_REDS = {1,3,5,7,9,12,14,16,18,19,21,23,25,27,30,32,34,36}
ROULETTE_BLACKS = {2,4,6,8,10,11,13,15,17,20,22,24,26,28,29,31,33,35}


def parse_roulette_bet(text):
    t = text.lower().strip()
    try:
        n = int(t)
        if 0 <= n <= 36:
            return "number", n, 36
    except ValueError:
        pass
    m = {
        "red": ("color","red",2), "красное": ("color","red",2), "к": ("color","red",2),
        "black": ("color","black",2), "чёрное": ("color","black",2), "ч": ("color","black",2),
        "green": ("number",0,36), "зеро": ("number",0,36), "з": ("number",0,36),
        "even": ("parity","even",2), "чёт": ("parity","even",2), "чет": ("parity","even",2),
        "odd": ("parity","odd",2), "нечёт": ("parity","odd",2), "нечет": ("parity","odd",2),
        "low": ("half","low",2), "малые": ("half","low",2), "1-18": ("half","low",2),
        "high": ("half","high",2), "большие": ("half","high",2), "19-36": ("half","high",2),
        "1d": ("dozen",1,3), "1-12": ("dozen",1,3), "д1": ("dozen",1,3),
        "2d": ("dozen",2,3), "13-24": ("dozen",2,3), "д2": ("dozen",2,3),
        "3d": ("dozen",3,3), "25-36": ("dozen",3,3), "д3": ("dozen",3,3),
    }
    return m.get(t)


def roulette_result(number, bt, bv):
    if bt == "number": return number == bv
    if bt == "color":
        if number == 0: return False
        return number in (ROULETTE_REDS if bv == "red" else ROULETTE_BLACKS)
    if bt == "parity":
        if number == 0: return False
        return (number % 2 == 0) if bv == "even" else (number % 2 == 1)
    if bt == "half":
        if number == 0: return False
        return (1 <= number <= 18) if bv == "low" else (19 <= number <= 36)
    if bt == "dozen":
        if number == 0: return False
        return ((bv-1)*12 < number <= bv*12)
    return False


def number_color_emoji(n):
    if n == 0: return "🇷🇺"
    return "🏳️‍🌈" if n in ROULETTE_REDS else "🇺🇦"


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
            [(r, c) for r in range(self.size) for c in range(self.size)], mines):
            self.grid[r][c] = True

    @property
    def expired(self):
        return time.time() - self.created > MINES_TIMEOUT

    def reveal(self, r, c):
        if self.revealed[r][c]: return "already"
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
        if self.safe_found == 0: return 1.0
        prob = 1.0
        safe = self.total - self.mines_count
        for i in range(self.safe_found):
            prob *= (safe - i) / (self.total - i)
        return min(round(0.97 / prob, 2), 999.0) if prob > 0 else 999.0

    @property
    def winnings(self): return max(int(self.bet * self.multiplier), 1)

    @property
    def profit(self): return self.winnings - self.bet

    def keyboard(self, show_all=False):
        rows = []
        for r in range(self.size):
            row = []
            for c in range(self.size):
                if show_all or self.revealed[r][c]:
                    t = "🇷🇺" if self.grid[r][c] else ("🏳️‍🌈" if self.revealed[r][c] else "🇺🇦")
                    row.append(InlineKeyboardButton(text=t, callback_data="noop"))
                else:
                    row.append(InlineKeyboardButton(text="⬛", callback_data=f"m:{self.uid}:{r}:{c}"))
            rows.append(row)
        if self.active and self.safe_found > 0:
            rows.append([InlineKeyboardButton(
                text=f"💰 Забрать {fmt(self.winnings)} (x{self.multiplier})",
                callback_data=f"mc:{self.uid}")])
        return InlineKeyboardMarkup(inline_keyboard=rows)


# ======================== БАЗА ДАННЫХ ========================

class Database:
    def __init__(self, path):
        self.path, self.db = path, None

    async def connect(self):
        self.db = await aiosqlite.connect(self.path)
        self.db.row_factory = aiosqlite.Row
        await self.db.execute("PRAGMA journal_mode=WAL")
        await self.db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER NOT NULL, chat_id INTEGER NOT NULL,
                name TEXT NOT NULL, level INTEGER DEFAULT 0,
                rolls INTEGER DEFAULT 0, best_roll INTEGER DEFAULT 0,
                last_roll REAL DEFAULT 0, games_won INTEGER DEFAULT 0,
                games_lost INTEGER DEFAULT 0, total_bet INTEGER DEFAULT 0,
                total_won INTEGER DEFAULT 0, last_daily REAL DEFAULT 0,
                duels_won INTEGER DEFAULT 0, duels_lost INTEGER DEFAULT 0,
                captchas_ok INTEGER DEFAULT 0,
                PRIMARY KEY (user_id, chat_id))""")
        await self.db.commit()
        for n, td in [("last_daily","REAL DEFAULT 0"),("duels_won","INTEGER DEFAULT 0"),
                       ("duels_lost","INTEGER DEFAULT 0"),("captchas_ok","INTEGER DEFAULT 0")]:
            try: await self.db.execute(f"ALTER TABLE users ADD COLUMN {n} {td}")
            except: pass
        await self.db.commit()

    async def close(self):
        if self.db: await self.db.close()

    async def ensure(self, uid, cid, name):
        cur = await self.db.execute("SELECT 1 FROM users WHERE user_id=? AND chat_id=?", (uid, cid))
        if not await cur.fetchone():
            await self.db.execute("INSERT INTO users(user_id,chat_id,name) VALUES(?,?,?)", (uid, cid, name))
        else:
            await self.db.execute("UPDATE users SET name=? WHERE user_id=? AND chat_id=?", (name, uid, cid))
        await self.db.commit()

    async def get_user(self, uid, cid):
        cur = await self.db.execute("SELECT * FROM users WHERE user_id=? AND chat_id=?", (uid, cid))
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
            "UPDATE users SET level=?, rolls=rolls+1, best_roll=?, last_roll=? WHERE user_id=? AND chat_id=?",
            (nl, br, time.time(), uid, cid))
        await self.db.commit()
        return nl, await self.get_user(uid, cid)

    async def change_level(self, uid, cid, delta):
        u = await self.get_user(uid, cid)
        if not u: return 0
        nl = max(u["level"] + delta, 0)
        await self.db.execute("UPDATE users SET level=? WHERE user_id=? AND chat_id=?", (nl, uid, cid))
        await self.db.commit()
        return nl

    async def record_game(self, uid, cid, bet, won, win):
        col = "games_won" if win else "games_lost"
        await self.db.execute(
            f"UPDATE users SET {col}={col}+1, total_bet=total_bet+?, total_won=total_won+? WHERE user_id=? AND chat_id=?",
            (bet, won, uid, cid))
        await self.db.commit()

    async def record_duel(self, uid, cid, win):
        col = "duels_won" if win else "duels_lost"
        await self.db.execute(f"UPDATE users SET {col}={col}+1 WHERE user_id=? AND chat_id=?", (uid, cid))
        await self.db.commit()

    async def inc_captcha(self, uid, cid):
        await self.db.execute("UPDATE users SET captchas_ok=captchas_ok+1 WHERE user_id=? AND chat_id=?", (uid, cid))
        await self.db.commit()

    async def set_daily(self, uid, cid):
        await self.db.execute("UPDATE users SET last_daily=? WHERE user_id=? AND chat_id=?", (time.time(), uid, cid))
        await self.db.commit()

    async def top_page(self, cid, page, per=10):
        off = page * per
        cur = await self.db.execute(
            "SELECT name,level FROM users WHERE chat_id=? ORDER BY level DESC LIMIT ? OFFSET ?", (cid, per, off))
        rows = [dict(r) for r in await cur.fetchall()]
        cur2 = await self.db.execute("SELECT COUNT(*) AS c FROM users WHERE chat_id=?", (cid,))
        return rows, (await cur2.fetchone())["c"]

    async def global_top_page(self, page, per=10):
        off = page * per
        cur = await self.db.execute(
            """SELECT name, user_id, SUM(level) AS total_level, SUM(rolls) AS total_rolls,
                      SUM(games_won) AS gw, SUM(games_lost) AS gl,
                      SUM(duels_won) AS dw, SUM(duels_lost) AS dl
               FROM users GROUP BY user_id ORDER BY total_level DESC LIMIT ? OFFSET ?""", (per, off))
        rows = [dict(r) for r in await cur.fetchall()]
        cur2 = await self.db.execute("SELECT COUNT(DISTINCT user_id) AS c FROM users")
        return rows, (await cur2.fetchone())["c"]

    async def position(self, uid, cid):
        lvl = await self.get_level(uid, cid)
        cur = await self.db.execute("SELECT COUNT(*) AS c FROM users WHERE chat_id=? AND level>?", (cid, lvl))
        pos = (await cur.fetchone())["c"] + 1
        cur2 = await self.db.execute("SELECT COUNT(*) AS c FROM users WHERE chat_id=?", (cid,))
        return pos, (await cur2.fetchone())["c"]

    async def chat_stats(self, cid):
        cur = await self.db.execute(
            """SELECT COUNT(*) AS players, COALESCE(SUM(level),0) AS total_lvl,
                      COALESCE(MAX(level),0) AS max_lvl, COALESCE(SUM(rolls),0) AS total_rolls,
                      COALESCE(MAX(best_roll),0) AS best_ever, COALESCE(SUM(games_won),0) AS gw,
                      COALESCE(SUM(games_lost),0) AS gl, COALESCE(SUM(duels_won),0) AS dw,
                      COALESCE(SUM(duels_lost),0) AS dl FROM users WHERE chat_id=?""", (cid,))
        return dict(await cur.fetchone())


# ======================== ИНИТ ========================

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("curator")

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()
router = Router()
db = Database(DB_FILE)

cooldowns: dict[tuple, float] = {}
active_mines: dict[tuple, MinesGame] = {}
pending_captchas: dict[tuple, dict] = {}
active_duels: dict[tuple, dict] = {}

# антиспам: uid → [timestamps]
spam_tracker: dict[int, list] = {}
spam_bans: dict[int, float] = {}

PER_PAGE = 10


def set_cd(uid, cid, s=COOLDOWN_SECONDS):
    cooldowns[(uid, cid)] = time.time() + s


def remaining_cd(uid, cid):
    return max(0, int(cooldowns.get((uid, cid), 0) - time.time()))


def only_group(msg):
    return msg.chat.type in ("group", "supergroup")


def check_spam(uid) -> bool:
    """True = спамер, заблокировать"""
    now = time.time()

    # проверяем бан
    ban_until = spam_bans.get(uid, 0)
    if now < ban_until:
        return True

    # трекаем
    if uid not in spam_tracker:
        spam_tracker[uid] = []
    stamps = spam_tracker[uid]
    stamps.append(now)
    # очищаем старые
    spam_tracker[uid] = [t for t in stamps if now - t < SPAM_WINDOW]

    if len(spam_tracker[uid]) > SPAM_LIMIT:
        spam_bans[uid] = now + SPAM_BAN_TIME
        spam_tracker[uid] = []
        return True
    return False


async def parse_bet(msg, raw):
    if not raw: return None
    token = raw.strip().split()[0].lower()
    uid, cid = msg.from_user.id, msg.chat.id
    level = await db.get_level(uid, cid)
    if token in ("all", "все", "олл"): bet = level
    elif token in ("half", "половина"): bet = level // 2
    else:
        try: bet = int(token)
        except ValueError:
            await msg.reply("❌ Ставка — число, <b>all</b> или <b>half</b>")
            return None
    if bet < 1:
        await msg.reply("❌ Мин. ставка: <b>1</b>")
        return None
    if bet > level:
        await msg.reply(f"❌ Недостаточно! Ваш уровень: <b>{fmt(level)}</b>")
        return None
    return bet


def top_kb(prefix, page, total, per=PER_PAGE):
    pages = math.ceil(total / per)
    if pages <= 1: return None
    btns = []
    if page > 0:
        btns.append(InlineKeyboardButton(text="◀️", callback_data=f"{prefix}:{page-1}"))
    btns.append(InlineKeyboardButton(text=f"{page+1}/{pages}", callback_data="noop"))
    if page < pages - 1:
        btns.append(InlineKeyboardButton(text="▶️", callback_data=f"{prefix}:{page+1}"))
    return InlineKeyboardMarkup(inline_keyboard=[btns])


# ======================== АНТИСПАМ МИДЛВАРЬ ========================

from aiogram import BaseMiddleware

class AntiSpamMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        uid = None
        if isinstance(event, Message) and event.from_user:
            uid = event.from_user.id
        elif isinstance(event, CallbackQuery) and event.from_user:
            uid = event.from_user.id

        if uid and check_spam(uid):
            if isinstance(event, Message):
                ban_left = int(spam_bans.get(uid, 0) - time.time())
                if ban_left > 0:
                    try:
                        await event.reply(f"🚫 ты че ахуел ракбот <b>{ban_left}с</b>")
                    except:
                        pass
            elif isinstance(event, CallbackQuery):
                try:
                    await event.answer("🚫 пошол нахуй ракбот ебаный!", show_alert=True)
                except:
                    pass
            return
        return await handler(event, data)


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

    if key in pending_captchas:
        if time.time() - pending_captchas[key]["created"] < CAPTCHA_TIMEOUT:
            await message.reply("⚠️ Решите хуйню выше!")
            return
        del pending_captchas[key]

    wait = remaining_cd(uid, cid)
    if wait > 0:
        m, s = divmod(wait, 60)
        t = f"{m}м {s}с" if m else f"{s}с"
        await message.reply(f"⏳ <b>{name}</b>, подождите <b>{t}</b>!")
        return

    inc = random.randint(MIN_LEVEL_UP, MAX_LEVEL_UP)
    question, correct, options, ctype = generate_captcha()

    if ctype == "emoji":
        btns = [InlineKeyboardButton(text=o, callback_data=f"cap:{uid}:{o}") for o in options]
        kb = InlineKeyboardMarkup(inline_keyboard=[btns])
    else:
        btns = [InlineKeyboardButton(text=str(o), callback_data=f"cap:{uid}:{o}") for o in options]
        kb = InlineKeyboardMarkup(inline_keyboard=[btns])

    msg = await message.reply(f"🤖 <b>Проверка!</b>\n\n{question}\n⏱ {CAPTCHA_TIMEOUT}с", reply_markup=kb)
    pending_captchas[key] = {
        "answer": str(correct), "increase": inc,
        "created": time.time(), "msg_id": msg.message_id}


@router.callback_query(F.data.startswith("cap:"))
async def captcha_answer(cb: CallbackQuery):
    parts = cb.data.split(":", 2)
    cap_uid, selected = int(parts[1]), parts[2]
    uid, cid = cb.from_user.id, cb.message.chat.id

    if uid != cap_uid:
        await cb.answer("❌ Не ваша!", show_alert=True)
        return

    key = (uid, cid)
    cap = pending_captchas.get(key)
    if not cap:
        await cb.answer("⏰ вствтоыекла!", show_alert=True)
        return
    if time.time() - cap["created"] > CAPTCHA_TIMEOUT:
        del pending_captchas[key]
        await cb.message.edit_text("⏰ Время вышло! /curator")
        await cb.answer()
        return

    inc = cap["increase"]
    del pending_captchas[key]
    name = cb.from_user.full_name

    if selected == cap["answer"]:
        await db.inc_captcha(uid, cid)
        nl, user = await db.curator_roll(uid, cid, name, inc)
        set_cd(uid, cid)
        rank = get_rank(nl)
        nxt = get_next_rank(nl)
        pt = prev_threshold(nl)
        txt = f"✅ Верно!\n\n💼 <b>{name}</b> +{inc}\n⭐ {fmt(nl)}\n🏅 {rank}"
        if nxt:
            nn, nt = nxt
            bar = progress_bar(nl - pt, nt - pt)
            txt += f"\n\n{bar} до «{nn}» ({fmt(nt - nl)})"
        if inc == MAX_LEVEL_UP:
            txt += "\n🍀 Крит!"
        await cb.message.edit_text(txt)
        await cb.answer("✅")
    else:
        set_cd(uid, cid, 15)
        await cb.message.edit_text(f"❌ Неверно! Ответ: <b>{cap['answer']}</b>\nПопробуйте через 15с")
        await cb.answer("❌")


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
    if time.time() - last < 86400:
        rem = 86400 - (time.time() - last)
        h, r2 = divmod(int(rem), 3600)
        m, _ = divmod(r2, 60)
        await message.reply(f"⏰ <b>{name}</b>, бонус получен!\nСледующий: <b>{h}ч {m}м</b>")
        return
    nl = await db.change_level(uid, cid, DAILY_BONUS)
    await db.set_daily(uid, cid)
    await message.reply(f"🎁 <b>{name}</b> +{DAILY_BONUS}!\n⭐ {fmt(nl)} · {get_rank(nl)}")


# ======================== /pay ========================

@router.message(Command("pay"))
async def cmd_pay(message: Message, command: CommandObject):
    if not only_group(message):
        await message.reply("🚫 Только в группах!")
        return
    if not message.reply_to_message or not message.reply_to_message.from_user:
        await message.reply("💸 Ответьте на сообщение:\n<code>/pay [сумма]</code>")
        return
    target = message.reply_to_message.from_user
    uid, cid = message.from_user.id, message.chat.id
    if target.id == uid:
        await message.reply("❌ Себе нельзя!")
        return
    if target.is_bot:
        await message.reply("❌ Боту нельзя!")
        return
    await db.ensure(uid, cid, message.from_user.full_name)
    await db.ensure(target.id, cid, target.full_name)
    bet = await parse_bet(message, command.args)
    if bet is None: return
    snl = await db.change_level(uid, cid, -bet)
    tnl = await db.change_level(target.id, cid, bet)
    await message.reply(
        f"💸 <b>{message.from_user.full_name}</b> → <b>{target.full_name}</b>\n"
        f"💰 {fmt(bet)}\n📤 {fmt(snl)} · 📥 {fmt(tnl)}")


# ======================== ДУЭЛИ ========================

@router.message(Command("duel"))
async def cmd_duel(message: Message, command: CommandObject):
    if not only_group(message):
        await message.reply("🚫 Только в группах!")
        return
    if not message.reply_to_message or not message.reply_to_message.from_user:
        await message.reply("⚔️ Ответьте на сообщение:\n<code>/duel [ставка]</code>")
        return
    target = message.reply_to_message.from_user
    uid, cid = message.from_user.id, message.chat.id
    name = message.from_user.full_name
    if target.id == uid:
        await message.reply("❌ Себя нельзя!")
        return
    if target.is_bot:
        await message.reply("❌ Бота нельзя!")
        return
    await db.ensure(uid, cid, name)
    await db.ensure(target.id, cid, target.full_name)
    bet = await parse_bet(message, command.args)
    if bet is None: return
    tl = await db.get_level(target.id, cid)
    if tl < bet:
        await message.reply(f"❌ У <b>{target.full_name}</b> мало ({fmt(tl)})")
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Принять", callback_data="duel:a"),
        InlineKeyboardButton(text="❌ Отклонить", callback_data="duel:d")]])
    msg = await message.reply(
        f"⚔️ <b>Дуэль!</b>\n🔴 <b>{name}</b> 🆚 🔵 <b>{target.full_name}</b>\n"
        f"💰 {fmt(bet)} · ⏱ {DUEL_TIMEOUT//60} мин", reply_markup=kb)
    active_duels[(cid, msg.message_id)] = {
        "c_id": uid, "c_name": name, "t_id": target.id,
        "t_name": target.full_name, "bet": bet, "created": time.time()}


@router.callback_query(F.data == "duel:a")
async def duel_accept(cb: CallbackQuery):
    key = (cb.message.chat.id, cb.message.message_id)
    d = active_duels.get(key)
    if not d: await cb.answer("❌!", show_alert=True); return
    if cb.from_user.id != d["t_id"]: await cb.answer("❌ Не ваша!", show_alert=True); return
    if time.time() - d["created"] > DUEL_TIMEOUT:
        del active_duels[key]; await cb.message.edit_text("⏰ Истекла!"); await cb.answer(); return
    cid, bet = cb.message.chat.id, d["bet"]
    cl, tl = await db.get_level(d["c_id"], cid), await db.get_level(d["t_id"], cid)
    if cl < bet or tl < bet:
        del active_duels[key]; await cb.message.edit_text("❌ Недостаточно!"); await cb.answer(); return
    w = random.choice([True, False])
    if w: wi, wn, li, ln = d["c_id"], d["c_name"], d["t_id"], d["t_name"]
    else: wi, wn, li, ln = d["t_id"], d["t_name"], d["c_id"], d["c_name"]
    wnl = await db.change_level(wi, cid, bet)
    lnl = await db.change_level(li, cid, -bet)
    await db.record_duel(wi, cid, True); await db.record_duel(li, cid, False)
    del active_duels[key]
    await cb.message.edit_text(
        f"⚔️ <b>Итог!</b>\n🏆 <b>{wn}</b> +{fmt(bet)} → {fmt(wnl)}\n💀 <b>{ln}</b> -{fmt(bet)} → {fmt(lnl)}")
    await cb.answer(f"🏆 {wn}!")


@router.callback_query(F.data == "duel:d")
async def duel_decline(cb: CallbackQuery):
    key = (cb.message.chat.id, cb.message.message_id)
    d = active_duels.get(key)
    if not d: await cb.answer("❌!", show_alert=True); return
    if cb.from_user.id != d["t_id"]: await cb.answer("❌ Не ваша!", show_alert=True); return
    del active_duels[key]
    await cb.message.edit_text(f"🏳️ <b>{d['t_name']}</b> отклонил дуэль.")
    await cb.answer()


# ======================== МИНЫ ========================

@router.message(Command("mines"))
async def cmd_mines(message: Message, command: CommandObject):
    if not only_group(message): await message.reply("🚫 Только в группах!"); return
    uid, cid = message.from_user.id, message.chat.id
    await db.ensure(uid, cid, message.from_user.full_name)
    key = (uid, cid)
    old = active_mines.get(key)
    if old and old.active and not old.expired:
        await message.reply("❌ У вас уже есть игра!"); return
    if old: del active_mines[key]
    if not command.args:
        await message.reply("💣 <code>/mines [ставка] [мины 1-24]</code>"); return
    bet = await parse_bet(message, command.args)
    if bet is None: return
    parts = command.args.strip().split(); mc = 5
    if len(parts) > 1:
        try: mc = max(1, min(int(parts[1]), 24))
        except: pass
    await db.change_level(uid, cid, -bet)
    game = MinesGame(uid, cid, bet, mc)
    active_mines[key] = game
    await message.reply(f"💣 Мины — {message.from_user.full_name}\n💰 {fmt(bet)} · 💣 {mc}",
                        reply_markup=game.keyboard())


@router.callback_query(F.data.startswith("m:"))
async def mines_cell(cb: CallbackQuery):
    _, oid, row, col = cb.data.split(":"); oid, row, col = int(oid), int(row), int(col)
    uid, cid = cb.from_user.id, cb.message.chat.id
    if uid != oid: await cb.answer("❌ Не ваша!", show_alert=True); return
    game = active_mines.get((uid, cid))
    if not game or not game.active: await cb.answer("Завершена!"); return
    if game.expired: del active_mines[(uid, cid)]; await cb.answer("⏰!", show_alert=True); return
    res = game.reveal(row, col)
    if res == "already": await cb.answer("Уже!"); return
    name = cb.from_user.full_name
    if res == "mine":
        await db.record_game(uid, cid, game.bet, 0, False)
        nl = await db.get_level(uid, cid); del active_mines[(uid, cid)]
        await cb.message.edit_text(f"💥 ВЗРЫВ! — {name}\n💸 -{fmt(game.bet)}\n⭐ {fmt(nl)}",
                                   reply_markup=game.keyboard(show_all=True))
        await cb.answer("💥")
    elif not game.active:
        w = game.winnings; nl = await db.change_level(uid, cid, w)
        await db.record_game(uid, cid, game.bet, w, True); del active_mines[(uid, cid)]
        await cb.message.edit_text(f"🎉 ВСЁ! — {name}\n💵 +{fmt(w)} (x{game.multiplier})\n⭐ {fmt(nl)}",
                                   reply_markup=game.keyboard(show_all=True))
        await cb.answer("🎉")
    else:
        await cb.message.edit_text(
            f"💣 {name}\n💰 {fmt(game.bet)} · ✅ {game.safe_found} · x{game.multiplier}\n💵 → {fmt(game.winnings)}",
            reply_markup=game.keyboard())
        await cb.answer(f"x{game.multiplier}")


@router.callback_query(F.data.startswith("mc:"))
async def mines_cashout(cb: CallbackQuery):
    oid = int(cb.data.split(":")[1]); uid, cid = cb.from_user.id, cb.message.chat.id
    if uid != oid: await cb.answer("❌!", show_alert=True); return
    game = active_mines.get((uid, cid))
    if not game or not game.active: await cb.answer("Завершена!"); return
    w = game.winnings; game.active = False
    nl = await db.change_level(uid, cid, w)
    await db.record_game(uid, cid, game.bet, w, True); del active_mines[(uid, cid)]
    await cb.message.edit_text(
        f"💰 КЕШАУТ! — {cb.from_user.full_name}\nx{game.multiplier} · +{fmt(game.profit)}\n⭐ {fmt(nl)} · {get_rank(nl)}",
        reply_markup=game.keyboard(show_all=True))
    await cb.answer(f"💰 {fmt(w)}")


@router.callback_query(F.data == "noop")
async def noop(cb: CallbackQuery): await cb.answer()


# ======================== СЛОТЫ ========================

SYMBOLS = ["🏳️‍🌈", "🇺🇦", "🇷🇺", "🏴‍☠️", "🇩🇪", "🇵🇱"]
SYM_W = [30, 25, 20, 12, 8, 5]
TRIPLE_MULT = [3, 5, 7, 15, 30, 50]


@router.message(Command("slots"))
async def cmd_slots(message: Message, command: CommandObject):
    if not only_group(message): await message.reply("🚫 Только в группах!"); return
    uid, cid = message.from_user.id, message.chat.id
    await db.ensure(uid, cid, message.from_user.full_name)
    bet = await parse_bet(message, command.args)
    if bet is None:
        if not command.args: await message.reply("🎰 <code>/slots [ставка]</code>")
        return
    reels = [random.choices(SYMBOLS, weights=SYM_W, k=1)[0] for _ in range(3)]
    d = f"⟦ {reels[0]} ┃ {reels[1]} ┃ {reels[2]} ⟧"
    if reels[0] == reels[1] == reels[2]:
        mult = TRIPLE_MULT[SYMBOLS.index(reels[0])]; p = int(bet*mult)-bet
        nl = await db.change_level(uid, cid, p); await db.record_game(uid, cid, bet, int(bet*mult), True)
        await message.reply(f"🎰 {d}\n🎉 ДЖЕКПОТ x{mult}! +{fmt(p)}\n⭐ {fmt(nl)}")
    elif reels[0]==reels[1] or reels[1]==reels[2] or reels[0]==reels[2]:
        p = int(bet*1.5)-bet; nl = await db.change_level(uid, cid, p)
        await db.record_game(uid, cid, bet, int(bet*1.5), True)
        await message.reply(f"🎰 {d}\n✅ x1.5 +{fmt(p)}\n⭐ {fmt(nl)}")
    else:
        nl = await db.change_level(uid, cid, -bet); await db.record_game(uid, cid, bet, 0, False)
        await message.reply(f"🎰 {d}\n❌ -{fmt(bet)}\n⭐ {fmt(nl)}")


# ======================== МОНЕТКА ========================

@router.message(Command("coinflip", "cf"))
async def cmd_cf(message: Message, command: CommandObject):
    if not only_group(message): await message.reply("🚫 Только в группах!"); return
    uid, cid = message.from_user.id, message.chat.id
    await db.ensure(uid, cid, message.from_user.full_name)
    bet = await parse_bet(message, command.args)
    if bet is None:
        if not command.args: await message.reply("🪙 <code>/cf [ставка]</code>")
        return
    side = random.choice(["Орёл 🇷🇺", "Решка 🇺🇦"])
    if random.random() < 0.48:
        nl = await db.change_level(uid, cid, bet); await db.record_game(uid, cid, bet, bet*2, True)
        await message.reply(f"🪙 {side}\n✅ +{fmt(bet)}\n⭐ {fmt(nl)}")
    else:
        nl = await db.change_level(uid, cid, -bet); await db.record_game(uid, cid, bet, 0, False)
        await message.reply(f"🪙 {side}\n❌ -{fmt(bet)}\n⭐ {fmt(nl)}")


# ======================== КОСТИ ========================

@router.message(Command("dice"))
async def cmd_dice(message: Message, command: CommandObject):
    if not only_group(message): await message.reply("🚫 Только в группах!"); return
    uid, cid = message.from_user.id, message.chat.id
    await db.ensure(uid, cid, message.from_user.full_name)
    bet = await parse_bet(message, command.args)
    if bet is None:
        if not command.args: await message.reply("🎲 <code>/dice [ставка]</code>")
        return
    roll = random.randint(1, 6); faces = ["⚀","⚁","⚂","⚃","⚄","⚅"]
    if roll <= 2:
        nl = await db.change_level(uid, cid, -bet); await db.record_game(uid, cid, bet, 0, False)
        await message.reply(f"🎲 {faces[roll-1]} ({roll})\n❌ -{fmt(bet)}\n⭐ {fmt(nl)}")
    elif roll == 3:
        nl = await db.get_level(uid, cid)
        await message.reply(f"🎲 {faces[roll-1]} ({roll})\n🔄 Ничья!\n⭐ {fmt(nl)}")
    else:
        mult = {4:1.5,5:2.0,6:3.0}[roll]; p = int(bet*mult)-bet
        nl = await db.change_level(uid, cid, p); await db.record_game(uid, cid, bet, int(bet*mult), True)
        await message.reply(f"🎲 {faces[roll-1]} ({roll}) x{mult}\n🏳️‍🌈 +{fmt(p)}\n⭐ {fmt(nl)}")


# ======================== РУЛЕТКА ========================

@router.message(Command("roulette", "rl"))
async def cmd_rl(message: Message, command: CommandObject):
    if not only_group(message): await message.reply("🚫 Только в группах!"); return
    uid, cid = message.from_user.id, message.chat.id
    await db.ensure(uid, cid, message.from_user.full_name)
    if not command.args:
        await message.reply(
            "🎡 <b>Рулетка</b>\n\n<code>/rl [ставка] [тип]</code>\n\n"
            "Число 0-36 → x36\nred/black → x2\neven/odd → x2\n"
            "low(1-18)/high(19-36) → x2\n1d/2d/3d → x3\n\n"
            "Пример: <code>/rl 100 red</code>")
        return
    parts = command.args.strip().split(maxsplit=1)
    if len(parts) < 2: await message.reply("❌ <code>/rl [ставка] [тип]</code>"); return
    bet = await parse_bet(message, parts[0])
    if bet is None: return
    rb = parse_roulette_bet(parts[1])
    if not rb: await message.reply("❌ Неизвестный тип! /rl"); return
    bt, bv, mult = rb; num = random.randint(0, 36); ce = number_color_emoji(num)
    win = roulette_result(num, bt, bv)
    if win:
        w = int(bet*mult); p = w - bet
        nl = await db.change_level(uid, cid, p); await db.record_game(uid, cid, bet, w, True)
        await message.reply(f"🎡 {ce} <b>{num}</b>\n✅ x{mult} +{fmt(p)}\n⭐ {fmt(nl)}")
    else:
        nl = await db.change_level(uid, cid, -bet); await db.record_game(uid, cid, bet, 0, False)
        await message.reply(f"🎡 {ce} <b>{num}</b>\n❌ -{fmt(bet)}\n⭐ {fmt(nl)}")


# ======================== ТОП ЧАТА ========================

@router.message(Command("top"))
async def cmd_top(message: Message):
    if not only_group(message): await message.reply("🚫!"); return
    await send_top(message.chat.id, 0, message=message)


@router.callback_query(F.data.startswith("tp:"))
async def top_cb(cb: CallbackQuery):
    await send_top(cb.message.chat.id, int(cb.data.split(":")[1]), cb=cb)


async def send_top(cid, page, message=None, cb=None):
    rows, total = await db.top_page(cid, page)
    if not rows and page == 0:
        t = "📊 Пусто! /curator"
        if message: await message.reply(t)
        return
    medals = ["🥇","🥈","🥉"]; lines = []
    for i, u in enumerate(rows):
        pos = page*PER_PAGE+i
        p = medals[pos] if pos < 3 else f"{pos+1}."
        lines.append(f'{p} <b>{u["name"]}</b> — {fmt(u["level"])} ({get_rank(u["level"])})')
    txt = f"🏆 <b>Топ чата</b> ({total})\n\n" + "\n".join(lines)
    kb = top_kb("tp", page, total)
    if cb: await cb.message.edit_text(txt, reply_markup=kb); await cb.answer()
    elif message: await message.reply(txt, reply_markup=kb)


# ======================== ГЛОБАЛЬНЫЙ ТОП ========================

@router.message(Command("globaltop", "gtop"))
async def cmd_gtop(message: Message):
    await send_gtop(0, message=message)


@router.callback_query(F.data.startswith("gtp:"))
async def gtop_cb(cb: CallbackQuery):
    await send_gtop(int(cb.data.split(":")[1]), cb=cb)


async def send_gtop(page, message=None, cb=None):
    rows, total = await db.global_top_page(page)
    if not rows and page == 0:
        t = "🌍 Пусто!"
        if message: await message.reply(t)
        return
    medals = ["🥇","🥈","🥉"]; lines = []
    for i, u in enumerate(rows):
        pos = page*PER_PAGE+i; p = medals[pos] if pos < 3 else f"{pos+1}."
        tl = u["total_level"]
        lines.append(f'{p} <b>{u["name"]}</b> — {fmt(tl)} ({get_rank(tl)})')
    txt = f"🌍 <b>Глобальный топ</b> ({total})\n\n" + "\n".join(lines)
    kb = top_kb("gtp", page, total)
    if cb: await cb.message.edit_text(txt, reply_markup=kb); await cb.answer()
    elif message: await message.reply(txt, reply_markup=kb)


# ======================== ПРОФИЛЬ ========================

@router.message(Command("my_level", "mylevel", "me"))
async def cmd_me(message: Message):
    if not only_group(message): await message.reply("🚫!"); return
    uid, cid = message.from_user.id, message.chat.id
    u = await db.get_user(uid, cid)
    if not u: await message.reply(f"🚀 Начните с /curator!"); return
    lv = u["level"]; rank = get_rank(lv)
    pos, total = await db.position(uid, cid)
    gw, gl = u["games_won"], u["games_lost"]; gt = gw+gl
    dw, dl = u.get("duels_won",0), u.get("duels_lost",0); dt = dw+dl
    wr = f"{gw/gt*100:.0f}%" if gt else "—"
    dwr = f"{dw/dt*100:.0f}%" if dt else "—"
    net = u["total_won"]-u["total_bet"]; net_s = f"+{fmt(net)}" if net >= 0 else fmt(net)
    nxt = get_next_rank(lv); pt = prev_threshold(lv)
    txt = (f"👤 <b>{u['name']}</b>\n\n⭐ {fmt(lv)}\n🏅 {rank}\n🏆 {pos}/{total}\n"
           f"🎲 Прокачек: {u['rolls']} · рекорд +{u['best_roll']}\n\n"
           f"🎮 {gw}W/{gl}L ({wr})\n⚔️ {dw}W/{dl}L ({dwr})\n"
           f"💰 {fmt(u['total_bet'])} · 💵 {fmt(u['total_won'])}\n📊 {net_s}")
    if nxt:
        nn, nt = nxt; bar = progress_bar(lv-pt, nt-pt)
        txt += f"\n\n{bar} до «{nn}» ({fmt(nt-lv)})"
    else: txt += "\n\n♾️ Макс. ранг!"
    await message.reply(txt)


# ======================== СТАТИСТИКА ========================

@router.message(Command("stats"))
async def cmd_stats(message: Message):
    if not only_group(message): await message.reply("🚫!"); return
    st = await db.chat_stats(message.chat.id)
    if st["players"] == 0: await message.reply("📊 Пусто!"); return
    avg = st["total_lvl"]/st["players"]
    await message.reply(
        f"📊 <b>Статистика</b>\n\n👥 {st['players']}\n📈 {fmt(st['total_lvl'])}\n"
        f"📉 {fmt(int(avg))}\n🏔 {fmt(st['max_lvl'])}\n🎲 {fmt(st['total_rolls'])}\n"
        f"🎮 {fmt(st['gw']+st['gl'])}\n⚔️ {fmt(st['dw']+st['dl'])}")


# ======================== РАНГИ ========================

@router.message(Command("ranks"))
async def cmd_ranks(message: Message):
    await send_ranks(0, message=message)


@router.callback_query(F.data.startswith("rp:"))
async def ranks_cb(cb: CallbackQuery):
    await send_ranks(int(cb.data.split(":")[1]), cb=cb)


async def send_ranks(page, message=None, cb=None):
    per = 20; start = page*per; chunk = RANKS[start:start+per]
    tp = math.ceil(len(RANKS)/per)
    if not chunk: return
    lines = [f"<b>{fmt(t)}+</b> → {n}" for t, n in chunk]
    txt = f"📜 <b>Ранги</b> ({len(RANKS)}) стр.{page+1}/{tp}\n\n" + "\n".join(lines)
    btns = []
    if page > 0: btns.append(InlineKeyboardButton(text="◀️", callback_data=f"rp:{page-1}"))
    btns.append(InlineKeyboardButton(text=f"{page+1}/{tp}", callback_data="noop"))
    if page < tp-1: btns.append(InlineKeyboardButton(text="▶️", callback_data=f"rp:{page+1}"))
    kb = InlineKeyboardMarkup(inline_keyboard=[btns]) if tp > 1 else None
    if cb: await cb.message.edit_text(txt, reply_markup=kb); await cb.answer()
    elif message: await message.reply(txt, reply_markup=kb)


# ======================== ПОМОЩЬ ========================

@router.message(Command("help", "start"))
async def cmd_help(message: Message):
    await message.reply(
        "💼 <b>Бот кураторства</b>\n\n"
        "📈 /curator — +1‑10 (кд 1м, капча)\n"
        "🎁 /daily — +100 в сутки\n\n"
        "🎮 <b>Игры:</b>\n"
        "/mines · /slots · /cf · /dice · /rl\n\n"
        "⚔️ /duel [ставка] — дуэль (ответом)\n"
        "💸 /pay [сумма] — перевод (ответом)\n\n"
        "📊 /top · /gtop · /me · /stats · /ranks\n"
        "❓ /help — справка")


# ======================== ЗАПУСК ========================

dp.include_router(router)


async def set_bot_commands():
    commands = [
        BotCommand(command="curator", description="⭐ Прокачать уровень (+1-10)"),
        BotCommand(command="daily", description="🎁 Ежедневный бонус +100"),
        BotCommand(command="mines", description="💣 Мины [ставка] [мины]"),
        BotCommand(command="slots", description="🎰 Слоты [ставка]"),
        BotCommand(command="cf", description="🪙 Монетка [ставка]"),
        BotCommand(command="dice", description="🎲 Кости [ставка]"),
        BotCommand(command="rl", description="🎡 Рулетка [ставка] [тип]"),
        BotCommand(command="duel", description="⚔️ Дуэль [ставка] (ответом)"),
        BotCommand(command="pay", description="💸 Перевод [сумма] (ответом)"),
        BotCommand(command="top", description="🏆 Топ чата"),
        BotCommand(command="gtop", description="🌍 Глобальный топ"),
        BotCommand(command="me", description="👤 Мой профиль"),
        BotCommand(command="stats", description="📊 Статистика чата"),
        BotCommand(command="ranks", description="📜 Все ранги"),
        BotCommand(command="help", description="❓ Справка"),
    ]
    await bot.set_my_commands(commands)


async def main():
    await db.connect()

    # мидлварь антиспама
    dp.message.middleware(AntiSpamMiddleware())
    dp.callback_query.middleware(AntiSpamMiddleware())

    await set_bot_commands()
    log.info("Команды установлены, бот запущен")

    try:
        await bot.delete_webhook(drop_pending_updates=True)
        await dp.start_polling(bot, allowed_updates=["message", "callback_query"])
    finally:
        await db.close()


if __name__ == "__main__":
    asyncio.run(main())