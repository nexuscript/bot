# DistroKid → Roblox → Telegram логгер (Python)

Скрипт мониторит новые аудио DistroKid в Roblox
(https://create.roblox.com/store/audio/discoverNewAudio/distrokid-hits),
рисует минималистичную ч/б карточку (обложка + название + артист + waveform)
и постит в Telegram-канал: фото с данными + mp3-файл ниже.

## Установка

```bash
cd python-bot
pip install -r requirements.txt
```

## Настройка

Задай переменные окружения:

```bash
export TELEGRAM_BOT_TOKEN="123456:ABC..."   # токен от @BotFather
export TELEGRAM_CHANNEL_ID="@my_channel"    # или числовой ID (-100...); бот должен быть админом
export CHECK_INTERVAL=300                   # интервал проверки в секундах (опционально)
```

## Запуск

```bash
python bot.py
```

- Первый запуск: скрипт запоминает текущие треки и НЕ постит их (чтобы не заспамить канал).
- Дальше каждые 5 минут проверяет новые и постит каждый: карточка + подпись + mp3.
- Дедупликация хранится в `posted.db` (SQLite) рядом со скриптом.
- Шрифты Inter скачиваются автоматически при первом запуске в `fonts/`.

## Формат поста

Фото-карточка, в подписи:

- Название (жирным)
- Длительность
- Громкость: LUFS / dB peak
- Стерео/Моно · частота Hz
- ID (кликабельная ссылка на страницу ассета)
- Артист (кликабельная ссылка на его треки)

Ниже, ответом на фото — mp3 для прослушивания прямо в Telegram.

## Хостинг 24/7

Любой VPS / Railway / PythonAnywhere. Например, через systemd:

```ini
[Unit]
Description=DistroKid Roblox Telegram logger
After=network.target

[Service]
WorkingDirectory=/opt/distrokid-bot
Environment=TELEGRAM_BOT_TOKEN=...
Environment=TELEGRAM_CHANNEL_ID=@my_channel
ExecStart=/usr/bin/python3 bot.py
Restart=always

[Install]
WantedBy=multi-user.target
```
