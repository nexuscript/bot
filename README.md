# DistroKid → Roblox → Telegram логгер (Python)

Скрипт непрерывно мониторит новые аудио DistroKid в Roblox
(https://create.roblox.com/store/audio/discoverNewAudio/distrokid-hits),
анализирует громкость каждого трека и постит в Telegram-канал только
bypassed-аудио (громче -3 LUFS или пик выше +4 dB): минималистичная ч/б
карточка (обложка + название + артист + waveform) + данные + mp3-файл ниже.

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
export CHECK_INTERVAL=0                     # пауза между проверками, сек; 0 = непрерывно (по умолчанию)
export ONLY_BYPASSED=1                      # 1 = постить только bypassed (по умолчанию), 0 = все треки
export BYPASS_LUFS=-3                       # порог bypass по громкости LUFS (опционально)
export BYPASS_PEAK_DB=4                     # порог bypass по пику dB (опционально)
export MAX_ATTEMPTS=3                       # попыток на трек до пропуска (опционально)
```

## Запуск

```bash
python bot.py
```

- Первый запуск: скрипт запоминает текущие треки и НЕ постит их (чтобы не заспамить канал).
- Дальше два потока: поллер непрерывно (запрос за запросом) проверяет новинки
  и ставит их в очередь, воркер разбирает очередь по одному (FIFO, от старых к новым).
- Каждый трек скачивается и анализируется; в канал уходят только bypassed
  (громче -3 LUFS или пик выше +4 dB). Остальные логируются и пропускаются.
- Rate limit: если Roblox отвечает 429, поллер выжидает ровно Retry-After
  из ответа сервера и продолжает — бан исключён при максимальной частоте.
- Очередь персистентная (SQLite): при наплыве треков или рестарте ничего не теряется.
- Дедупликация хранится в `posted.db` (SQLite) рядом со скриптом.
- Шрифты Inter скачиваются автоматически при первом запуске в `fonts/`.
- Требования к железу: ~1 vCPU и ~512 МБ RAM достаточно (потоковая обработка,
  ~200-300 МБ пик даже на длинных треках 96 кГц).

## Формат поста

Фото-карточка, в подписи:

- `#bypassed` первой строкой (трек громче -3 LUFS или пик выше +4 dB —
  обошёл лимиты громкости Roblox; по умолчанию постятся только такие)
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
