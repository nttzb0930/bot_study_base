import os
import asyncio
import sys
from pathlib import Path

# Add root folder to path so we can import modules
sys.path.append(str(Path(__file__).parent.parent))

from fastapi import FastAPI, Request, Response
from telegram import Update, BotCommand
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, MessageHandler, filters
from dotenv import load_dotenv

from bot import (
    start,
    login_command,
    check_command,
    gpa_command,
    setting_command,
    callback_selection,
    text_selection,
)

load_dotenv()
bot_token = os.environ.get("TELEGRAM_BOT_TOKEN")
if not bot_token:
    raise RuntimeError("Missing environment variable: TELEGRAM_BOT_TOKEN")

async def webhook_post_init(application) -> None:
    await application.bot.set_my_commands(
        [
            BotCommand("start", "Hướng dẫn sử dụng"),
            BotCommand("login", "Đăng nhập UNETI"),
            BotCommand("check", "Xem điểm"),
            BotCommand("gpa", "Xem GPA"),
            BotCommand("setting", "Cài đặt bot"),
        ]
    )

# Build application without running polling
telegram_app = ApplicationBuilder().token(bot_token).post_init(webhook_post_init).build()

# Register handlers
telegram_app.add_handler(CommandHandler("start", start))
telegram_app.add_handler(CommandHandler("login", login_command))
telegram_app.add_handler(CommandHandler("check", check_command))
telegram_app.add_handler(CommandHandler("gpa", gpa_command))
telegram_app.add_handler(CommandHandler("setting", setting_command))
telegram_app.add_handler(CallbackQueryHandler(callback_selection))
telegram_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_selection))

app = FastAPI()

# Cache to ensure we only initialize once
is_initialized = False

async def initialize_telegram():
    global is_initialized
    if not is_initialized:
        await telegram_app.initialize()
        await telegram_app.start()
        is_initialized = True

@app.post("/webhook")
async def webhook(request: Request):
    await initialize_telegram()
    try:
        data = await request.json()
        update = Update.de_json(data, telegram_app.bot)
        await telegram_app.process_update(update)
    except Exception as e:
        print(f"Webhook processing error: {e}")
    return Response(content="OK", status_code=200)

@app.get("/")
async def index():
    return {"status": "ok", "message": "Telegram Bot is running"}
