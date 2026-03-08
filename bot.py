import time
import requests
import logging

# ===== НАСТРОЙКИ =====
TOKEN = "8693997731:AAH9h0QzXksHXnCEX9a01g3q6R7S0X8bjB4"  # Токен бота
CHAT_ID = -1002707885564                                   # ID целевой группы
MESSAGE_TEXT = """@cowbellcuIt ɸио:ᴦᴀᴩяᴇʙ ᴄᴇʍён оᴧᴇᴦоʙич
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
бᴀᴛёᴋ-ᴦᴀᴩяᴇʙ оᴧᴇᴦ ᴀᴄᴦᴀᴛуᴧᴀᴇʙич 04.09.1971 @cowbellcuIt"""
INTERVAL_SECONDS = 60  # Интервал между отправками (в секундах)
# =====================

# Настройка логирования
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def send_message():
    """Отправляет сообщение через Telegram Bot API."""
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": MESSAGE_TEXT
    }
    try:
        response = requests.post(url, json=payload, timeout=10)
        if response.status_code == 200:
            logger.info("Сообщение успешно отправлено")
        else:
            logger.error(f"Ошибка API: {response.status_code} - {response.text}")
    except requests.exceptions.RequestException as e:
        logger.error(f"Ошибка соединения: {e}")

def main():
    logger.info("Бот запущен. Начинаю отправку сообщений...")
    while True:
        send_message()
        logger.info(f"Ожидание {INTERVAL_SECONDS} секунд до следующей отправки...")
        time.sleep(INTERVAL_SECONDS)

if __name__ == "__main__":
    main()