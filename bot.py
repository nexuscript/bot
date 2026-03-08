import logging
import random
import json
import os
import time
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# ===== НАСТРОЙКИ =====
ALLOWED_CHAT_ID = -1002707885564   # ID вашей группы (замените!)
COOLDOWN_SECONDS = 300              # 5 минут
DATA_FILE = 'curator_levels.json'   # файл для хранения данных
TOKEN = '8693997731:AAH9h0QzXksHXnCEX9a01g3q6R7S0X8bjB4'            # токен бота (замените!)
# =====================

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Загрузка данных из файла
def load_data():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {}

# Сохранение данных в файл
def save_data(data):
    with open(DATA_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

# Глобальный словарь с данными пользователей
user_data = load_data()

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Проверяем, что сообщение из разрешённого чата
    if update.effective_chat.id != ALLOWED_CHAT_ID:
        return

    # Игнорируем сообщения без текста
    if not update.message or not update.message.text:
        return

    # Проверяем наличие слова "куратор"
    text = update.message.text.lower()
    if 'куратор' not in text:
        return

    user = update.message.from_user
    user_id = str(user.id)
    now = time.time()

    # Проверка кулдауна
    if user_id in user_data:
        last = user_data[user_id].get('last_increase', 0)
        if now - last < COOLDOWN_SECONDS:
            return  # слишком рано, игнорируем

    # Генерируем прирост
    increase = random.randint(1, 10)

    # Обновляем или создаём запись
    if user_id not in user_data:
        user_data[user_id] = {'level': 0, 'last_increase': 0, 'name': ''}

    user_data[user_id]['level'] += increase
    user_data[user_id]['last_increase'] = now

    # Сохраняем имя пользователя (для топа)
    name = user.first_name
    if user.last_name:
        name += ' ' + user.last_name
    user_data[user_id]['name'] = name

    # Сохраняем данные в файл
    save_data(user_data)

    # Отвечаем в группу
    new_level = user_data[user_id]['level']
    response = f"{name} уровень кураторства повышен💼, новый уровень: {new_level}"
    await update.message.reply_text(response)

async def top_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Только для разрешённого чата
    if update.effective_chat.id != ALLOWED_CHAT_ID:
        return

    # Сортируем по убыванию уровня
    sorted_users = sorted(user_data.items(), key=lambda item: item[1]['level'], reverse=True)

    if not sorted_users:
        await update.message.reply_text("Пока нет данных о кураторах.")
        return

    # Топ-10
    top_count = min(10, len(sorted_users))
    lines = ["🏆 Топ кураторов:"]
    for i in range(top_count):
        user_id, data = sorted_users[i]
        name = data.get('name', f"Пользователь {user_id}")
        level = data['level']
        lines.append(f"{i+1}. {name} — {level}")

    await update.message.reply_text("\n".join(lines))

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != ALLOWED_CHAT_ID:
        return
    await update.message.reply_text(
        "Бот работает!\n"
        "Напишите «куратор» в чат, чтобы повысить свой уровень (раз в 5 минут).\n"
        "Команда /top покажет лучших кураторов группы."
    )

def main():
    # Создаём приложение
    application = Application.builder().token(TOKEN).build()

    # Регистрируем обработчики
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("top", top_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # Запускаем бота
    application.run_polling()

if __name__ == '__main__':
    main()
