import os
import asyncio
import sys
from pathlib import Path

# Add root folder to path so we can import modules
sys.path.append(str(Path(__file__).parent.parent))

from fastapi import FastAPI, Request, Response, HTTPException
from telegram import Update, BotCommand
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, MessageHandler, filters
from dotenv import load_dotenv

from bot import (
    start,
    login_command,
    logout_command,
    check_command,
    gpa_command,
    find_command,
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
            BotCommand("find", "Tra cứu MSSV bất kỳ"),
            BotCommand("setting", "Cài đặt bot"),
            BotCommand("logout", "Đăng xuất tài khoản"),
        ]
    )

# Build application without running polling
telegram_app = ApplicationBuilder().token(bot_token).post_init(webhook_post_init).build()

# Register handlers
telegram_app.add_handler(CommandHandler("start", start))
telegram_app.add_handler(CommandHandler("login", login_command))
telegram_app.add_handler(CommandHandler("logout", logout_command))
telegram_app.add_handler(CommandHandler("check", check_command))
telegram_app.add_handler(CommandHandler("gpa", gpa_command))
telegram_app.add_handler(CommandHandler("find", find_command))
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
        try:
            await webhook_post_init(telegram_app)
        except Exception as e:
            print(f"Error running webhook_post_init: {e}")
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


@app.get("/api/cron")
async def cron_check_scores(request: Request):
    cron_secret = os.environ.get("CRON_SECRET")
    auth_header = request.headers.get("Authorization")
    if cron_secret and auth_header != f"Bearer {cron_secret}":
        raise HTTPException(status_code=401, detail="Unauthorized")

    await initialize_telegram()

    from db import load_tokens, load_user_scores, save_user_scores
    from login import get_existing_login_status
    from check_scores import get_scores
    from bot import get_user_settings

    tokens = load_tokens()
    if not tokens:
        return {"status": "ok", "message": "No users to check"}

    async def check_user_cron(user_id: str) -> dict:
        settings = get_user_settings(user_id)
        if not settings.get("score_notifications_enabled", True):
            return {"user_id": user_id, "status": "skipped_by_settings"}

        try:
            status = await asyncio.to_thread(get_existing_login_status, user_id)
            if status not in {"valid", "refreshed"}:
                return {"user_id": user_id, "status": "expired_login"}

            current_scores = await asyncio.to_thread(get_scores, user_id)
            old_scores = await asyncio.to_thread(load_user_scores, user_id)

            if not old_scores:
                await asyncio.to_thread(save_user_scores, user_id, current_scores)
                return {"user_id": user_id, "status": "initialized_scores"}

            old_map = {
                row.get("TC_SV_KetQuaHocTap_MaLopHocPhan"): row 
                for row in old_scores 
                if row.get("TC_SV_KetQuaHocTap_MaLopHocPhan")
            }
            new_notifications = []

            for row in current_scores:
                class_id = row.get("TC_SV_KetQuaHocTap_MaLopHocPhan")
                if not class_id:
                    continue

                sub_name = row.get("TC_SV_KetQuaHocTap_TenMonHoc") or "Không rõ môn"
                new_score = row.get("TC_SV_KetQuaHocTap_DiemTongKet")

                if class_id not in old_map:
                    new_notifications.append({
                        "subject_name": sub_name,
                        "old_score": None,
                        "new_score": new_score,
                    })
                else:
                    old_row = old_map[class_id]
                    old_score = old_row.get("TC_SV_KetQuaHocTap_DiemTongKet")
                    if new_score != old_score:
                        new_notifications.append({
                            "subject_name": sub_name,
                            "old_score": old_score,
                            "new_score": new_score,
                        })

            if new_notifications:
                msg_lines = ["<b>📣 CÓ ĐIỂM MỚI TRÊN UNETI!</b>\n"]
                for notif in new_notifications:
                    sub = notif["subject_name"]
                    old_val = "Chưa có" if notif["old_score"] is None else str(notif["old_score"])
                    new_val = "Chưa có" if notif["new_score"] is None else str(notif["new_score"])
                    if notif["old_score"] is None:
                        msg_lines.append(f"• <b>{sub}</b>: <code>{new_val}</code>")
                    else:
                        msg_lines.append(f"• <b>{sub}</b>: <code>{old_val}</code> ➔ <code>{new_val}</code>")
                msg_lines.append("\n<i>Dùng /check để xem chi tiết điểm của bạn.</i>")
                msg_text = "\n".join(msg_lines)

                await telegram_app.bot.send_message(
                    chat_id=int(user_id),
                    text=msg_text,
                    parse_mode="HTML"
                )
                
                await asyncio.to_thread(save_user_scores, user_id, current_scores)
                return {"user_id": user_id, "status": "notified", "count": len(new_notifications)}
            else:
                if len(current_scores) != len(old_scores):
                    await asyncio.to_thread(save_user_scores, user_id, current_scores)
                    return {"user_id": user_id, "status": "updated_without_notification"}
                else:
                    return {"user_id": user_id, "status": "no_changes"}

        except Exception as exc:
            return {"user_id": user_id, "status": "error", "error": str(exc)}

    tasks = [check_user_cron(uid) for uid in tokens.keys()]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    processed_results = []
    for r in results:
        if isinstance(r, Exception):
            processed_results.append({"status": "exception", "error": str(r)})
        else:
            processed_results.append(r)

    return {"status": "ok", "processed": processed_results}
