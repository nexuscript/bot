import asyncio
import json
import random
import time
import os
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command, CommandObject
from aiogram.types import Message
from aiogram.enums import ParseMode

# === НАСТРОЙКИ ===
TOKEN = "8693997731:AAH9h0QzXksHXnCEX9a01g3q6R7S0X8bjB4"                 # Токен бота
ALLOWED_CHAT_ID = -1002707885564          # ID вашей группы (отрицательное число)
COOLDOWN_SECONDS = 300                     # 5 минут
DATA_FILE = "curator_levels.json"          # Файл для хранения данных
# =================

# Инициализация бота и диспетчера
bot = Bot(token=TOKEN, parse_mode=ParseMode.HTML)  # можно использовать HTML для форматирования
dp = Dispatcher()

# Загрузка данных
def load_data():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

# Сохранение данных
def save_data(data):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

# Глобальный словарь с данными пользователей
user_data = load_data()

@dp.message(Command("start"))
async def cmd_start(message: Message):
    if message.chat.id != ALLOWED_CHAT_ID:
        return
    await message.answer(
        "👋 Бот работает!\n"
        "Напишите «куратор» в чат, чтобы повысить свой уровень (раз в 5 минут).\n"
        "Команда /top покажет лучших кураторов группы."
    )

@dp.message(Command("top"))
async def cmd_top(message: Message):
    if message.chat.id != ALLOWED_CHAT_ID:
        return

    # Сортируем пользователей по убыванию уровня
    sorted_users = sorted(user_data.items(), key=lambda item: item[1]['level'], reverse=True)

    if not sorted_users:
        await message.answer("📊 Пока нет данных о кураторах.")
        return

    top_count = min(10, len(sorted_users))
    lines = ["🏆 <b>Топ кураторов:</b>"]
    for i in range(top_count):
        user_id, data = sorted_users[i]
        name = data.get('name', f"Пользователь {user_id}")
        level = data['level']
        lines.append(f"{i+1}. {name} — {level}")

    await message.answer("\n".join(lines), parse_mode=ParseMode.HTML)

@dp.message()
async def handle_message(message: Message):
    # Проверяем, что сообщение из разрешённой группы и содержит текст
    if message.chat.id != ALLOWED_CHAT_ID or not message.text:
        return

    # Проверяем наличие слова "куратор" (регистронезависимо)
    if "куратор" not in message.text.lower():
        return

    user = message.from_user
    user_id = str(user.id)
    now = time.time()

    # Проверка кулдауна
    if user_id in user_data:
        last = user_data[user_id].get('last_increase', 0)
        if now - last < COOLDOWN_SECONDS:
            return  # ещё не прошло 5 минут – игнорируем

    # Генерируем прирост
    increase = random.randint(1, 10)

    # Если пользователя нет в базе – создаём запись
    if user_id not in user_data:
        user_data[user_id] = {'level': 0, 'last_increase': 0, 'name': ''}

    # Обновляем уровень и время
    user_data[user_id]['level'] += increase
    user_data[user_id]['last_increase'] = now

    # Сохраняем имя пользователя (для топа)
    full_name = user.full_name  # Имя + фамилия, если есть
    user_data[user_id]['name'] = full_name

    # Сохраняем данные в файл
    save_data(user_data)

    # Формируем ответ
    new_level = user_data[user_id]['level']
    # Используем красивые эмодзи (можно заменить на другие)
    response = f"{full_name} уровень кураторства повышен💼, новый уровень: {new_level}"
    await message.reply(response)

async def main():
    print("Бот запущен и слушает сообщения...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
