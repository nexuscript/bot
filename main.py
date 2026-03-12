import json
import random
import os
from typing import Dict, List

from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# Токен вашего бота (получите у @BotFather)
TOKEN = "8704051951:AAGmcHuA1iAkttR4TZ-eH7OglI39Wf3QgVM"

# Файл для хранения истории сообщений
HISTORY_FILE = "message_history.json"

def load_history() -> Dict[str, List[str]]:
    """Загружает историю сообщений из JSON-файла."""
    if os.path.exists(HISTORY_FILE):
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_history(history: Dict[str, List[str]]) -> None:
    """Сохраняет историю сообщений в JSON-файл."""
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Отправляет приветственное сообщение при команде /start."""
    await update.message.reply_text(
        "Привет! Я буду запоминать все текстовые сообщения в этом чате "
        "и при каждом новом сообщении отправлять случайное из ранее сказанного."
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Основной обработчик текстовых сообщений."""
    # Игнорируем сообщения от самого бота, чтобы избежать циклов
    if update.effective_user and update.effective_user.id == context.bot.id:
        return

    # Проверяем, есть ли текст в сообщении
    if not update.message or not update.message.text:
        return

    chat_id = str(update.effective_chat.id)
    text = update.message.text

    # Загружаем историю
    history = load_history()
    # Получаем список сообщений для данного чата (или создаём новый)
    chat_messages = history.get(chat_id, [])

    # Добавляем новое сообщение в историю
    chat_messages.append(text)
    history[chat_id] = chat_messages
    save_history(history)

    # Если это первое сообщение в чате, история до него пуста – ничего не отправляем
    if len(chat_messages) == 1:
        return

    # Выбираем случайное сообщение из всех, кроме последнего (ранее отправленных)
    random_message = random.choice(chat_messages[:-1])

    # Отправляем выбранное сообщение обратно в чат
    await update.message.reply_text(random_message)

def main() -> None:
    """Запуск бота."""
    # Создаём приложение
    application = Application.builder().token(TOKEN).build()

    # Регистрируем обработчики
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # Запускаем бота
    application.run_polling()

if __name__ == "__main__":
    main()