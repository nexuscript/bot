import re
import requests
import time
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    ContextTypes, ConversationHandler, CallbackQueryHandler, filters
)

# States
SET_COOKIE, SET_PLACE_ID, DOWNLOAD_AUDIO = range(3)

# Per-user storage (uid -> {cookie, place_id})
user_data_store: dict[int, dict] = {}

def get_user(uid: int) -> dict:
    if uid not in user_data_store:
        user_data_store[uid] = {'cookie': None, 'place_id': None}
    return user_data_store[uid]

# ──────────────────────────────────────────────────────────────────────────
def sanitize_filename(name: str) -> str:
    name = re.sub(r'[/\\*?"<>|]', '', name)
    return name.replace(' ', '_')

def fetch_asset_name(asset_id: str):
    for _ in range(5):
        try:
            r = requests.get(
                f'https://economy.roproxy.com/v2/assets/{asset_id}/details',
                timeout=10
            )
            if r.status_code == 200:
                return r.json().get('Name')
        except Exception:
            pass
        time.sleep(0.5)
    return None

def fetch_audio_location(asset_id: str, place_id: str, cookie: str):
    body = [{'assetId': asset_id, 'assetType': 'Audio', 'requestId': '0'}]
    headers = {
        'User-Agent': 'Roblox/WinInet',
        'Content-Type': 'application/json',
        'Cookie': f'.ROBLOSECURITY={cookie}',
        'Roblox-Place-Id': place_id,
        'Accept': '*/*',
        'Roblox-Browser-Asset-Request': 'false',
    }
    for _ in range(5):
        try:
            r = requests.post(
                'https://assetdelivery.roblox.com/v2/assets/batch',
                headers=headers, json=body, timeout=10
            )
            if r.status_code == 200:
                locs = r.json()
                if locs and locs[0].get('locations'):
                    return locs[0]['locations'][0].get('location')
        except Exception:
            pass
        time.sleep(0.5)
    return None

def download_audio_bytes(url: str):
    try:
        r = requests.get(url, timeout=30)
        if r.status_code == 200:
            return r.content
    except Exception:
        pass
    return None

# ──────────────────────────────────────────────────────────────────────────
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    data = get_user(uid)
    c_ok = '✅ установлен' if data['cookie']   else '❌ не задан'
    p_ok = '✅ установлен' if data['place_id'] else '❌ не задан'
    kb = [
        [InlineKeyboardButton('🍪 Cookie',     callback_data='set_cookie')],
        [InlineKeyboardButton('🎮 Place ID',   callback_data='set_place')],
        [InlineKeyboardButton('🎵 Скачать',    callback_data='download')],
    ]
    text = (
        '🤖 *Roblox Audio Downloader Bot*\n\n'
        f'🍪 Cookie: {c_ok}\n'
        f'🎮 Place ID: {p_ok}\n\n'
        'Выберите действие:'
    )
    await update.message.reply_text(
        text, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup(kb)
    )

# ──────────────────────────────────────────────────────────────────────────
async def cmd_setcookie(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        '🍪 Отправьте ваш *.ROBLOSECURITY* cookie:',
        parse_mode='Markdown'
    )
    return SET_COOKIE

async def cb_set_cookie(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    await update.callback_query.message.reply_text(
        '🍪 Отправьте ваш *.ROBLOSECURITY* cookie:',
        parse_mode='Markdown'
    )
    return SET_COOKIE

async def save_cookie(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    get_user(update.effective_user.id)['cookie'] = update.message.text.strip()
    await update.message.reply_text('✅ Cookie сохранён!')
    return ConversationHandler.END

# ──────────────────────────────────────────────────────────────────────────
async def cmd_setplace(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text('🎮 Отправьте *Place ID*:', parse_mode='Markdown')
    return SET_PLACE_ID

async def cb_set_place(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    await update.callback_query.message.reply_text(
        '🎮 Отправьте *Place ID*:', parse_mode='Markdown'
    )
    return SET_PLACE_ID

async def save_place_id(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    place_id = update.message.text.strip()
    if not place_id.isdigit():
        await update.message.reply_text('❌ Только цифры! Попробуйте снова:')
        return SET_PLACE_ID
    get_user(uid)['place_id'] = place_id
    await update.message.reply_text(f'✅ Place ID {place_id} сохранён!')
    return ConversationHandler.END

# ──────────────────────────────────────────────────────────────────────────
async def cmd_download(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    data = get_user(update.effective_user.id)
    if not data['cookie'] or not data['place_id']:
        await update.message.reply_text('⚠️ Установите /setcookie и /setplace!')
        return ConversationHandler.END
    await update.message.reply_text(
        '🎵 Отправьте Asset ID через запятую:\n`123456789, 987654321`',
        parse_mode='Markdown'
    )
    return DOWNLOAD_AUDIO

async def cb_download(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    data = get_user(update.effective_user.id)
    if not data['cookie'] or not data['place_id']:
        await update.callback_query.message.reply_text('⚠️ Установите Cookie и Place ID!')
        return ConversationHandler.END
    await update.callback_query.message.reply_text(
        '🎵 Отправьте Asset ID через запятую:\n`123456789, 987654321`',
        parse_mode='Markdown'
    )
    return DOWNLOAD_AUDIO

async def process_download(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    data = get_user(uid)
    raw_ids = [x.strip() for x in update.message.text.split(',') if x.strip()]
    if not raw_ids:
        await update.message.reply_text('❌ Не найдено ни одного ID.')
        return DOWNLOAD_AUDIO
    msg = await update.message.reply_text(f'⏳ Обрабатываю {len(raw_ids)} аудио...')
    for asset_id in raw_ids:
        await msg.edit_text(f'⏳ Загружаю: {asset_id}...')
        try:
            name = fetch_asset_name(asset_id) or asset_id
            safe = sanitize_filename(name)
            url  = fetch_audio_location(asset_id, data['place_id'], data['cookie'])
            if not url:
                await update.message.reply_text(f'⚠️ URL не найден для `{asset_id}`', parse_mode='Markdown')
                continue
            audio = download_audio_bytes(url)
            if not audio:
                await update.message.reply_text(f'❌ Не удалось скачать `{asset_id}`', parse_mode='Markdown')
                continue
            await update.message.reply_audio(
                audio=audio,
                filename=f'{safe}.ogg',
                title=name,
                caption=f'🎵 *{name}*  |  ID: `{asset_id}`',
                parse_mode='Markdown'
            )
        except Exception as e:
            await update.message.reply_text(f'❌ Ошибка: {e}')
    await msg.edit_text('✅ Готово!')
    return ConversationHandler.END

async def cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text('❌ Отменено.')
    return ConversationHandler.END

async def cmd_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    data = get_user(uid)
    c_ok = '✅ есть' if data['cookie']   else '❌ нет'
    pid  = data['place_id'] or 'не задан'
    await update.message.reply_text(
        f'📊 *Настройки:*\n🍪 Cookie: {c_ok}\n🎮 Place ID: {pid}',
        parse_mode='Markdown'
    )

# ──────────────────────────────────────────────────────────────────────────
def main():
    TOKEN = '7414162488:AAGMey2evupPTVOh4XqjvMA1hvumlZReFKI'  # <-- токен от @BotFather

    app = ApplicationBuilder().token(TOKEN).build()

    conv = ConversationHandler(
        entry_points=[
            CommandHandler('setcookie', cmd_setcookie),
            CommandHandler('setplace',  cmd_setplace),
            CommandHandler('download',  cmd_download),
            CallbackQueryHandler(cb_set_cookie, pattern='^set_cookie$'),
            CallbackQueryHandler(cb_set_place,  pattern='^set_place$'),
            CallbackQueryHandler(cb_download,   pattern='^download$'),
        ],
        states={
            SET_COOKIE:     [MessageHandler(filters.TEXT & ~filters.COMMAND, save_cookie)],
            SET_PLACE_ID:   [MessageHandler(filters.TEXT & ~filters.COMMAND, save_place_id)],
            DOWNLOAD_AUDIO: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_download)],
        },
        fallbacks=[CommandHandler('cancel', cancel)],
        allow_reentry=True,
    )

    app.add_handler(CommandHandler('start',  cmd_start))
    app.add_handler(CommandHandler('status', cmd_status))
    app.add_handler(conv)

    print('Bot started!')
    app.run_polling()

if __name__ == '__main__':
    main()