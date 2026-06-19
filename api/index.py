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
async def cron_check_scores(request: Request, secret: str = None):
    cron_secret = os.environ.get("CRON_SECRET")
    if cron_secret:
        auth_header = request.headers.get("Authorization")
        header_ok = (auth_header == f"Bearer {cron_secret}")
        query_ok = (secret == cron_secret)
        if not (header_ok or query_ok):
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
                try:
                    from db import delete_user_login
                    await asyncio.to_thread(delete_user_login, user_id)
                    await telegram_app.bot.send_message(
                        chat_id=int(user_id),
                        text="⚠️ <b>Phiên đăng nhập của bạn đã hết hạn</b> do đổi mật khẩu hoặc hết hạn lâu ngày.\n"
                             "Bot không thể tiếp tục quét điểm mới cho bạn. Vui lòng gõ /login để đăng nhập lại.",
                        parse_mode="HTML"
                    )
                except Exception as notify_exc:
                    print(f"Failed to notify and logout user {user_id}: {notify_exc}")
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
            
            key_fields = {
                "Chuyên cần LT": "TC_SV_KetQuaHocTap_DiemChuyenCan_LyThuyet",
                "TB thường kỳ": "TC_SV_KetQuaHocTap_DiemTBThuongKy",
                "Điểm thi": "TC_SV_KetQuaHocTap_DiemThi",
                "Điểm tổng kết": "TC_SV_KetQuaHocTap_DiemTongKet"
            }

            for row in current_scores:
                class_id = row.get("TC_SV_KetQuaHocTap_MaLopHocPhan")
                if not class_id:
                    continue

                sub_name = row.get("TC_SV_KetQuaHocTap_TenMonHoc") or "Không rõ môn"

                if class_id not in old_map:
                    changes = []
                    for label, field in key_fields.items():
                        val = row.get(field)
                        if val is not None and val != "":
                            changes.append(f"{label}: <code>{val}</code>")
                    
                    if changes:
                        new_notifications.append({
                            "subject_name": sub_name,
                            "is_new": True,
                            "changes": changes
                        })
                else:
                    old_row = old_map[class_id]
                    changes = []
                    for label, field in key_fields.items():
                        old_val = old_row.get(field)
                        new_val = row.get(field)
                        
                        old_norm = None if old_val is None or old_val == "" else str(old_val).strip()
                        new_norm = None if new_val is None or new_val == "" else str(new_val).strip()
                        
                        if new_norm != old_norm:
                            old_display = "Chưa có" if old_norm is None else old_norm
                            new_display = "Chưa có" if new_norm is None else new_norm
                            changes.append(f"{label}: <code>{old_display}</code> ➔ <code>{new_display}</code>")
                    
                    if changes:
                        new_notifications.append({
                            "subject_name": sub_name,
                            "is_new": False,
                            "changes": changes
                        })

            if new_notifications:
                msg_lines = ["<b>📣 CÓ ĐIỂM MỚI TRÊN UNETI!</b>\n"]
                for notif in new_notifications:
                    sub = notif["subject_name"]
                    if notif.get("is_new"):
                        msg_lines.append(f"📚 <b>Môn mới: {sub}</b>")
                    else:
                        msg_lines.append(f"📝 <b>Cập nhật môn: {sub}</b>")
                    for change in notif["changes"]:
                        msg_lines.append(f"  • {change}")
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
            if isinstance(exc, KeyError) or "đăng nhập" in str(exc).lower() or "auth" in str(exc).lower():
                try:
                    await telegram_app.bot.send_message(
                        chat_id=int(user_id),
                        text="⚠️ <b>Phiên đăng nhập của bạn đã hết hạn</b> hoặc bị lỗi xác thực.\n"
                             "Vui lòng gõ /login để đăng nhập lại để tiếp tục nhận thông báo điểm.",
                        parse_mode="HTML"
                    )
                except Exception as notify_exc:
                    print(f"Failed to send relogin notice for {user_id}: {notify_exc}")
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
