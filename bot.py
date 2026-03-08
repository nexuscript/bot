import asyncio
import logging
import os
from telegram import Bot
from telegram.error import TelegramError
from telegram.ext import Application, JobQueue

# Настройка логирования (чтобы видеть процесс)
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# ===== НАСТРОЙКИ =====
TOKEN = "8693997731:AAH9h0QzXksHXnCEX9a01g3q6R7S0X8bjB4"               # Токен бота (получить у @BotFather)
CHAT_ID = -1002707885564                # ID целевой группы (начинается с -100 для супергрупп)
MESSAGE_TEXT = "@cowbellcuIt ɸио:ᴦᴀᴩяᴇʙ ᴄᴇʍён оᴧᴇᴦоʙич
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
бᴀᴛёᴋ-ᴦᴀᴩяᴇʙ оᴧᴇᴦ ᴀᴄᴦᴀᴛуᴧᴀᴇʙич 04.09.1971 @cowbellcuIt"  # Текст для отправки
INTERVAL_SECONDS = 60                    # Интервал между отправками (в секундах)
# =====================

async def send_periodic_message(context):
    """Функция, которая вызывается периодически для отправки сообщения в группу."""
    try:
        await context.bot.send_message(chat_id=CHAT_ID, text=MESSAGE_TEXT)
        logger.info(f"Сообщение отправлено в чат {CHAT_ID}")
    except TelegramError as e:
        logger.error(f"Ошибка при отправке сообщения: {e}")

async def post_init(application: Application):
    """Действия после инициализации приложения: запускаем периодическую задачу."""
    # job_queue создаётся автоматически при инициализации Application
    job_queue = application.job_queue
    if job_queue:
        # Запускаем задачу с интервалом INTERVAL_SECONDS, первое выполнение сразу
        job_queue.run_repeating(send_periodic_message, interval=INTERVAL_SECONDS, first=0)
        logger.info(f"Периодическая задача запущена с интервалом {INTERVAL_SECONDS} сек.")
    else:
        logger.error("JobQueue не доступна. Убедитесь, что установлен python-telegram-bot[job-queue]")

def main():
    """Точка входа: создаём приложение и запускаем поллинг."""
    # Создаём приложение с токеном бота
    application = Application.builder().token(TOKEN).post_init(post_init).build()

    # Не добавляем обработчиков команд/сообщений, поэтому бот будет игнорировать всё,
    # кроме периодической отправки. Если хотите явно отклонить сообщения, можно добавить
    # пустой обработчик, но это необязательно.

    # Запускаем поллинг (бесконечный цикл получения обновлений)
    logger.info("Бот запущен и ожидает...")
    application.run_polling(allowed_updates=[])  # allowed_updates=[] означает, что никакие типы обновлений не запрашиваются

if __name__ == "__main__":
    main()