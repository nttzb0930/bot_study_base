import asyncio
import html
import json
import logging
import os
import time
from pathlib import Path

from dotenv import load_dotenv
from telegram import BotCommand, ForceReply, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import TelegramError
from telegram.ext import (
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from check_scores import format_gpa_summary_table, format_score_row_detail_table, get_gpa_records, get_scores
from login import get_existing_login_status, login as uneti_login, save_login
from db import load_user_settings, save_user_settings, delete_user_login


logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

load_dotenv()

USER_CHECK_CONTEXT: dict[str, dict] = {}
USER_LOGIN_CONTEXT: dict[str, dict] = {}
USER_SETTING_CONTEXT: dict[str, dict] = {}
CHECK_CONTEXT_TTL_SECONDS = int(os.getenv("CHECK_CONTEXT_TTL_SECONDS", "3600"))
AUTO_DELETE_MESSAGES_SECONDS = int(os.getenv("AUTO_DELETE_MESSAGES_SECONDS", "300"))


def set_check_context(user_id: str, data: dict) -> None:
    USER_CHECK_CONTEXT[user_id] = {**data, "updated_at": time.time()}


def get_check_context(user_id: str) -> dict:
    data = USER_CHECK_CONTEXT.get(user_id)
    if not data:
        return {}
    if time.time() - data.get("updated_at", 0) > CHECK_CONTEXT_TTL_SECONDS:
        USER_CHECK_CONTEXT.pop(user_id, None)
        return {}
    return data


def clear_check_context(user_id: str) -> None:
    USER_CHECK_CONTEXT.pop(user_id, None)


def set_login_context(user_id: str) -> None:
    USER_LOGIN_CONTEXT[user_id] = {"updated_at": time.time()}


def get_login_context(user_id: str) -> dict:
    data = USER_LOGIN_CONTEXT.get(user_id)
    if not data:
        return {}
    if time.time() - data.get("updated_at", 0) > CHECK_CONTEXT_TTL_SECONDS:
        USER_LOGIN_CONTEXT.pop(user_id, None)
        return {}
    return data


def clear_login_context(user_id: str) -> None:
    USER_LOGIN_CONTEXT.pop(user_id, None)


def set_setting_context(user_id: str, data: dict) -> None:
    USER_SETTING_CONTEXT[user_id] = {**data, "updated_at": time.time()}


def get_setting_context(user_id: str) -> dict:
    data = USER_SETTING_CONTEXT.get(user_id)
    if not data:
        return {}
    if time.time() - data.get("updated_at", 0) > CHECK_CONTEXT_TTL_SECONDS:
        USER_SETTING_CONTEXT.pop(user_id, None)
        return {}
    return data


def clear_setting_context(user_id: str) -> None:
    USER_SETTING_CONTEXT.pop(user_id, None)


def clear_all_contexts(user_id: str) -> None:
    clear_login_context(user_id)
    clear_check_context(user_id)
    clear_setting_context(user_id)


def is_reply_to_message(update: Update, message_id: int | None) -> bool:
    if not update.message or not update.message.reply_to_message or message_id is None:
        return False
    return update.message.reply_to_message.message_id == message_id




def get_user_settings(user_id: str) -> dict:
    settings = load_user_settings()
    user_settings = settings.get(str(user_id), {})
    return {
        "auto_delete_enabled": user_settings.get(
            "auto_delete_enabled",
            AUTO_DELETE_MESSAGES_SECONDS > 0,
        ),
        "auto_delete_seconds": int(
            user_settings.get("auto_delete_seconds", AUTO_DELETE_MESSAGES_SECONDS)
        ),
    }


def update_user_settings(user_id: str, **values) -> dict:
    settings = load_user_settings()
    current = get_user_settings(user_id)
    current.update(values)
    settings[str(user_id)] = current
    save_user_settings(settings)
    return current


def get_auto_delete_seconds(user_id: str | None = None) -> int:
    if user_id is None:
        return AUTO_DELETE_MESSAGES_SECONDS

    settings = get_user_settings(user_id)
    if not settings["auto_delete_enabled"]:
        return 0
    return max(0, int(settings["auto_delete_seconds"]))


async def delete_message_later(bot, chat_id: int, message_id: int, seconds: int) -> None:
    if seconds <= 0:
        return

    await asyncio.sleep(seconds)
    try:
        await bot.delete_message(chat_id=chat_id, message_id=message_id)
    except TelegramError:
        logger.debug("Auto-delete skipped for message %s in chat %s", message_id, chat_id)


def schedule_auto_delete(message, user_id: str | None = None) -> None:
    seconds = get_auto_delete_seconds(user_id)
    if seconds <= 0 or not message:
        return

    asyncio.create_task(
        delete_message_later(message.get_bot(), message.chat_id, message.message_id, seconds)
    )


def score_id(row: dict) -> str:
    return str(row.get("TC_SV_KetQuaHocTap_MaLopHocPhan") or "")


def subject_code(row: dict) -> str:
    return str(row.get("TC_SV_KetQuaHocTap_MaMonHoc") or "")


def course_code(row: dict) -> str:
    return str(row.get("TC_SV_KetQuaHocTap_MaHocPhan") or "")


def subject_name(row: dict) -> str:
    return str(row.get("TC_SV_KetQuaHocTap_TenMonHoc") or "Không rõ môn")


def term_name(row: dict) -> str:
    return str(row.get("TC_SV_KetQuaHocTap_HocKy") or "-")


def final_score(row: dict):
    return row.get("TC_SV_KetQuaHocTap_DiemTongKet")


def academic_year(row: dict):
    return row.get("TC_SV_KetQuaHocTap_NamHoc")


def available_years(rows: list[dict]) -> list[int]:
    years = {academic_year(row) for row in rows if isinstance(academic_year(row), int)}
    return sorted(years, reverse=True)


def rows_for_year(rows: list[dict], year: int) -> list[dict]:
    return [row for row in rows if academic_year(row) == year]


def latest_year_rows(rows: list[dict]) -> list[dict]:
    years = available_years(rows)
    return rows_for_year(rows, years[0]) if years else rows


def semester_number(row: dict) -> int:
    first_part = term_name(row).split(" ", 1)[0]
    try:
        return int(first_part)
    except ValueError:
        return 999


def available_semesters(rows: list[dict], year: int) -> list[int]:
    semesters = {
        semester_number(row)
        for row in rows_for_year(rows, year)
        if semester_number(row) != 999
    }
    return sorted(semesters)


def rows_for_semester(rows: list[dict], year: int, semester: int) -> list[dict]:
    return [
        row
        for row in rows
        if academic_year(row) == year and semester_number(row) == semester
    ]


def find_rows(rows: list[dict], raw_id: str) -> list[dict]:
    requested_id = raw_id.strip()
    if requested_id.isdigit():
        index = int(requested_id)
        if 1 <= index <= len(rows):
            return [rows[index - 1]]

    return [
        row
        for row in rows
        if requested_id in {score_id(row), subject_code(row), course_code(row)}
    ]


def truncate(text: str, max_width: int) -> str:
    if len(text) <= max_width:
        return text
    return text[: max_width - 1] + "…"


def format_years(rows: list[dict]) -> str:
    years = available_years(rows)
    if not years:
        return "Không tìm thấy năm học nào."

    lines = ["Chọn năm học:", ""]
    for index, year in enumerate(years, start=1):
        count = len(rows_for_year(rows, year))
        lines.append(f"{index}. {year}-{year + 1} ({count} môn)")
    return "\n".join(lines)


def format_semesters(rows: list[dict], year: int) -> str:
    semesters = available_semesters(rows, year)
    if not semesters:
        return f"Năm {year}-{year + 1} không có học kỳ nào."

    lines = [f"Năm học {year}-{year + 1}", "Chọn học kỳ:", ""]
    for index, semester in enumerate(semesters, start=1):
        count = len(rows_for_semester(rows, year, semester))
        lines.append(f"{index}. Học kỳ {semester} ({count} môn)")
    return "\n".join(lines)


def format_score_list(rows: list[dict], title: str | None = None) -> str:
    lines = [
        title or f"Tìm thấy {len(rows)} môn",
        "Chọn môn bằng nút bên dưới hoặc nhập số thứ tự.",
        "",
    ]
    for index, row in enumerate(rows, start=1):
        score = final_score(row)
        score_text = "-" if score is None else str(score)
        lines.extend(
            [
                f"{index}. {subject_name(row)}",
                f"   HK: {term_name(row)} | TK: {score_text}",
                f"   Mã HP: {score_id(row)} | Mã môn: {subject_code(row)}",
            ]
        )
    return "\n".join(lines)


def format_score_table(rows: list[dict], title: str | None = None) -> str:
    header = title or f"Tìm thấy {len(rows)} môn"
    table_lines = [
        f"{'#':>2}  {'Môn học':<26} {'HK':<2} {'TK':>4}",
        "-" * 39,
    ]
    for index, row in enumerate(rows, start=1):
        score = final_score(row)
        score_text = "-" if score is None else str(score)
        table_lines.append(
            f"{index:>2}  {truncate(subject_name(row), 26):<26} "
            f"{semester_number(row):<2} {score_text:>4}"
        )

    text = "\n".join([header, "Chọn môn bằng nút hoặc nhập số.", "", "\n".join(table_lines)])
    return f"<pre>{html.escape(text)}</pre>"


def format_detail_table_html(row: dict) -> str:
    return f"<pre>{html.escape(format_score_row_detail_table(row))}</pre>"


def format_gpa_table_html(records: list[dict], title: str) -> str:
    return f"<pre>{html.escape(format_gpa_summary_table(records, title))}</pre>"


def year_keyboard(rows: list[dict]) -> InlineKeyboardMarkup:
    buttons = []
    for year in available_years(rows):
        count = len(rows_for_year(rows, year))
        buttons.append([InlineKeyboardButton(f"{year}-{year + 1} ({count})", callback_data=f"year:{year}")])
    buttons.append([InlineKeyboardButton("Tất cả môn", callback_data="all")])
    return InlineKeyboardMarkup(buttons)


def semester_keyboard(rows: list[dict], year: int) -> InlineKeyboardMarkup:
    buttons = []
    for semester in available_semesters(rows, year):
        count = len(rows_for_semester(rows, year, semester))
        buttons.append([InlineKeyboardButton(f"Học kỳ {semester} ({count})", callback_data=f"semester:{year}:{semester}")])
    buttons.append([InlineKeyboardButton("Quay lại", callback_data="back:years")])
    return InlineKeyboardMarkup(buttons)


def subjects_keyboard(rows: list[dict], year: int | None = None) -> InlineKeyboardMarkup:
    buttons = []
    for index, row in enumerate(rows, start=1):
        score = final_score(row)
        score_text = "-" if score is None else str(score)
        buttons.append(
            [
                InlineKeyboardButton(
                    f"{index}. {truncate(subject_name(row), 28)} | TK {score_text}",
                    callback_data=f"subject:{score_id(row)}",
                )
            ]
        )

    back_callback = f"back:semesters:{year}" if year is not None else "back:years"
    buttons.append([InlineKeyboardButton("Quay lại", callback_data=back_callback)])
    return InlineKeyboardMarkup(buttons)


def gpa_year(record: dict) -> int | None:
    term = str(record.get("TC_SV_KetQuaHocTap_TenDot") or "")
    if "(" not in term or "-" not in term:
        return None
    year_text = term.split("(", 1)[1].split("-", 1)[0].strip()
    try:
        return int(year_text)
    except ValueError:
        return None


def available_gpa_years(records: list[dict]) -> list[int]:
    years = {year for record in records if (year := gpa_year(record)) is not None}
    return sorted(years, reverse=True)


def gpa_records_for_year(records: list[dict], year: int) -> list[dict]:
    return [record for record in records if gpa_year(record) == year]


def gpa_menu_keyboard(records: list[dict]) -> InlineKeyboardMarkup:
    buttons = [[InlineKeyboardButton("GPA hiện tại", callback_data="gpa:current")]]
    for year in available_gpa_years(records):
        count = len(gpa_records_for_year(records, year))
        buttons.append([InlineKeyboardButton(f"{year}-{year + 1} ({count} học kỳ)", callback_data=f"gpa:year:{year}")])
    buttons.append([InlineKeyboardButton("Quay lại", callback_data="back:years")])
    return InlineKeyboardMarkup(buttons)


def gpa_back_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("Quay lại", callback_data="gpa:menu")]])


def format_seconds(seconds: int) -> str:
    if seconds <= 0:
        return "tắt"
    if seconds % 3600 == 0:
        return f"{seconds // 3600} giờ"
    if seconds % 60 == 0:
        return f"{seconds // 60} phút"
    return f"{seconds} giây"


def settings_text(user_id: str) -> str:
    settings = get_user_settings(user_id)
    status = "Bật" if settings["auto_delete_enabled"] else "Tắt"
    seconds = int(settings["auto_delete_seconds"])
    return "\n".join(
        [
            "Cài đặt bot",
            f"Auto xóa message: {status}",
            f"Thời gian xóa: {format_seconds(seconds)}",
            "",
            "Chọn nút bên dưới để đổi cài đặt.",
        ]
    )


def is_logged_in(user_id: str) -> bool:
    try:
        return get_existing_login_status(user_id) in {"valid", "refreshed"}
    except Exception:
        return False


def settings_keyboard(user_id: str) -> InlineKeyboardMarkup:
    settings = get_user_settings(user_id)
    toggle_label = "Tắt auto-delete" if settings["auto_delete_enabled"] else "Bật auto-delete"
    back_callback = "back:years" if is_logged_in(user_id) else "close_menu"
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton(toggle_label, callback_data="setting:toggle")],
            [
                InlineKeyboardButton("1 phút", callback_data="setting:time:60"),
                InlineKeyboardButton("5 phút", callback_data="setting:time:300"),
                InlineKeyboardButton("10 phút", callback_data="setting:time:600"),
            ],
            [
                InlineKeyboardButton("30 phút", callback_data="setting:time:1800"),
                InlineKeyboardButton("1 giờ", callback_data="setting:time:3600"),
            ],
            [InlineKeyboardButton("Nhập số giây", callback_data="setting:custom")],
            [InlineKeyboardButton("Quay lại", callback_data=back_callback)],
        ]
    )


async def send_long_message(update: Update, text: str, max_length: int = 3900) -> None:
    if not update.message:
        return
    user_id = str(update.effective_user.id) if update.effective_user else None
    chunks = []
    current = ""
    for line in text.splitlines():
        next_current = f"{current}\n{line}" if current else line
        if len(next_current) > max_length:
            chunks.append(current)
            current = line
        else:
            current = next_current
    if current:
        chunks.append(current)
    for chunk in chunks:
        message = await update.message.reply_text(chunk)
        schedule_auto_delete(message, user_id)


async def send_long_html_message(update: Update, html_text: str, max_length: int = 3900) -> None:
    if not update.message:
        return
    user_id = str(update.effective_user.id) if update.effective_user else None
    for index in range(0, len(html_text), max_length):
        message = await update.message.reply_text(html_text[index:index + max_length], parse_mode="HTML")
        schedule_auto_delete(message, user_id)


async def send_or_edit_text(update: Update, text: str, reply_markup=None) -> None:
    user_id = str(update.effective_user.id) if update.effective_user else None
    if update.callback_query:
        message = await update.callback_query.edit_message_text(text, reply_markup=reply_markup)
        if message is True:
            message = update.callback_query.message
        schedule_auto_delete(message, user_id)
    elif update.message:
        message = await update.message.reply_text(text, reply_markup=reply_markup)
        schedule_auto_delete(message, user_id)


async def send_or_edit_html(update: Update, html_text: str, reply_markup=None) -> None:
    user_id = str(update.effective_user.id) if update.effective_user else None
    if update.callback_query:
        message = await update.callback_query.edit_message_text(
            html_text,
            parse_mode="HTML",
            reply_markup=reply_markup,
        )
        if message is True:
            message = update.callback_query.message
        schedule_auto_delete(message, user_id)
    elif update.message:
        message = await update.message.reply_text(html_text, parse_mode="HTML", reply_markup=reply_markup)
        schedule_auto_delete(message, user_id)


async def prompt_login_credentials(update: Update, user_id: str) -> None:
    set_login_context(user_id)
    text = (
        "Phiên đăng nhập chưa có hoặc refresh token đã hết hạn.\n"
        "Reply tin nhắn này theo dạng:\n"
        "<mssv> <password>"
    )
    markup = ForceReply(
        selective=True,
        input_field_placeholder="msv password",
    )
    if update.message:
        message = await update.message.reply_text(text, reply_markup=markup)
        schedule_auto_delete(message, user_id)
    elif update.callback_query:
        message = await update.callback_query.message.reply_text(text, reply_markup=markup)
        schedule_auto_delete(message, user_id)


async def perform_login(update: Update, username: str, password: str, telegram_user_id: str) -> None:
    await send_long_message(update, "Đang login, đợi một chút...")
    try:
        token_data = await asyncio.to_thread(uneti_login, username, password)
        await asyncio.to_thread(save_login, username, token_data, telegram_user_id)
    except Exception as exc:
        logger.exception("Login failed")
        await send_long_message(update, f"Login thất bại: {exc}")
        return

    clear_login_context(telegram_user_id)
    await send_long_message(update, "Login thành công. Dùng /check để xem danh sách môn.")


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user:
        clear_all_contexts(str(update.effective_user.id))
    text = "\n".join(
        [
            "Lệnh dùng:",
            "/login - bot sẽ hỏi mssv và mật khẩu bằng reply",
            "/login <mssv> <mật_khẩu>",
            "/check - chọn năm học, học kỳ, môn bằng nút",
            "",
            "Nếu đang ở màn chọn, có thể nhập số thứ tự thay vì bấm nút.",
        ]
    )
    await send_long_message(update, text)


async def login_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user:
        return

    user_id = str(update.effective_user.id)
    clear_all_contexts(user_id)
    try:
        status = await asyncio.to_thread(get_existing_login_status, user_id)
    except Exception:
        logger.exception("Existing login check failed")
        status = None

    if status == "valid":
        await send_long_message(update, "Bạn đã login rồi, token vẫn còn hạn. Dùng /check để xem điểm.")
        return
    if status == "refreshed":
        await send_long_message(update, "Token đã hết hạn và đã được refresh. Dùng /check để xem điểm.")
        return

    if len(context.args) < 2:
        await prompt_login_credentials(update, user_id)
        return

    await perform_login(update, context.args[0].strip(), " ".join(context.args[1:]).strip(), user_id)


async def logout_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user:
        return

    user_id = str(update.effective_user.id)
    clear_all_contexts(user_id)
    try:
        await asyncio.to_thread(delete_user_login, user_id)
        await send_long_message(update, "Đăng xuất thành công. Thông tin đăng nhập của bạn đã được xóa.")
    except Exception as exc:
        logger.exception("Logout failed")
        await send_long_message(update, f"Đăng xuất thất bại: {exc}")


async def check_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user:
        return

    user_id = str(update.effective_user.id)
    clear_all_contexts(user_id)
    try:
        rows = await asyncio.to_thread(get_scores, user_id)
    except KeyError:
        await prompt_login_credentials(update, user_id)
        return
    except Exception as exc:
        logger.exception("Check scores failed")
        await send_long_message(update, f"Không lấy được điểm: {exc}")
        return

    if not context.args:
        set_check_context(user_id, {"stage": "years"})
        await send_or_edit_text(update, "Chọn năm học:", reply_markup=year_keyboard(rows))
        return

    requested_id = context.args[0]
    current_context = get_check_context(user_id)

    if requested_id.lower() == "all":
        set_check_context(user_id, {"stage": "subjects", "year": None, "semester": None})
        await send_long_html_message(update, format_score_table(rows, f"Tất cả: {len(rows)} môn"))
        return

    if requested_id.isdigit() and len(requested_id) <= 2:
        selected_index = int(requested_id)
        stage = current_context.get("stage")

        if stage == "years":
            years = available_years(rows)
            if 1 <= selected_index <= len(years):
                selected_year = years[selected_index - 1]
                set_check_context(user_id, {"stage": "semesters", "year": selected_year})
                await send_long_message(update, format_semesters(rows, selected_year))
                return

        if stage == "semesters":
            selected_year = current_context.get("year")
            semesters = available_semesters(rows, selected_year)
            if 1 <= selected_index <= len(semesters):
                selected_semester = semesters[selected_index - 1]
                semester_rows = rows_for_semester(rows, selected_year, selected_semester)
                set_check_context(
                    user_id,
                    {"stage": "subjects", "year": selected_year, "semester": selected_semester},
                )
                await send_long_html_message(
                    update,
                    format_score_table(
                        semester_rows,
                        f"Năm {selected_year}-{selected_year + 1}, học kỳ {selected_semester}: {len(semester_rows)} môn",
                    ),
                )
                return

        if stage == "subjects":
            selected_year = current_context.get("year")
            selected_semester = current_context.get("semester")
            if selected_year is None:
                search_rows = rows
            elif selected_semester is None:
                search_rows = rows_for_year(rows, selected_year)
            else:
                search_rows = rows_for_semester(rows, selected_year, selected_semester)

            matched_rows = find_rows(search_rows, requested_id)
            if matched_rows:
                await send_long_html_message(update, format_detail_table_html(matched_rows[0]))
                return

    search_rows = latest_year_rows(rows) if requested_id.isdigit() else rows
    matched_rows = find_rows(search_rows, requested_id)
    if not matched_rows and requested_id.isdigit():
        matched_rows = find_rows(rows, requested_id)

    if not matched_rows:
        await send_long_message(update, f"Không tìm thấy môn có id: {requested_id}")
        return
    if len(matched_rows) > 1:
        await send_long_message(update, format_score_list(matched_rows, "Tìm thấy nhiều môn khớp id này"))
        return

    await send_long_html_message(update, format_detail_table_html(matched_rows[0]))


async def gpa_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user:
        return

    user_id = str(update.effective_user.id)
    clear_all_contexts(user_id)
    try:
        records = await asyncio.to_thread(get_gpa_records, user_id)
    except KeyError:
        await prompt_login_credentials(update, user_id)
        return
    except Exception as exc:
        logger.exception("GPA check failed")
        await send_long_message(update, f"Không lấy được GPA: {exc}")
        return

    if not context.args:
        await send_or_edit_text(update, "Chọn kiểu xem GPA:", reply_markup=gpa_menu_keyboard(records))
        return

    arg = context.args[0].strip().lower()
    if arg in {"current", "now", "hientai", "hiện_tại"}:
        await send_long_html_message(update, format_gpa_table_html(records[:1], "GPA hiện tại"))
        return

    if arg.isdigit() and len(arg) == 4:
        year = int(arg)
        year_records = gpa_records_for_year(records, year)
        await send_long_html_message(update, format_gpa_table_html(year_records, f"GPA năm {year}-{year + 1}"))
        return

    await send_long_message(update, "Dùng /gpa, /gpa current hoặc /gpa 2025.")


async def setting_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user:
        return

    user_id = str(update.effective_user.id)
    clear_all_contexts(user_id)
    await send_or_edit_text(update, settings_text(user_id), reply_markup=settings_keyboard(user_id))


async def callback_selection(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not update.effective_user:
        return

    user_id = str(update.effective_user.id)
    data = query.data or ""

    if data.startswith("setting:"):
        clear_login_context(user_id)
        clear_check_context(user_id)
    elif data.startswith("gpa:"):
        clear_login_context(user_id)
        clear_check_context(user_id)
        clear_setting_context(user_id)
    elif any(
        data.startswith(p)
        for p in ["back:years", "all", "year:", "semester:", "subject:", "back:semesters:", "back:subjects"]
    ):
        clear_login_context(user_id)
        clear_setting_context(user_id)

    if data == "close_menu":
        await query.answer()
        try:
            await query.message.delete()
        except Exception:
            pass
        return

    if data.startswith("setting:"):
        if data == "setting:menu":
            await query.answer()
            await send_or_edit_text(update, settings_text(user_id), reply_markup=settings_keyboard(user_id))
            return

        if data == "setting:toggle":
            current = get_user_settings(user_id)
            new_enabled = not current["auto_delete_enabled"]
            update_user_settings(
                user_id,
                auto_delete_enabled=new_enabled,
            )
            status_str = "Bật" if new_enabled else "Tắt"
            await query.answer(text=f"Đã {status_str.lower()} tự động xóa tin nhắn!")
            await send_or_edit_text(update, settings_text(user_id), reply_markup=settings_keyboard(user_id))
            return

        if data.startswith("setting:time:"):
            seconds = int(data.split(":")[2])
            update_user_settings(
                user_id,
                auto_delete_enabled=True,
                auto_delete_seconds=seconds,
            )
            clear_setting_context(user_id)
            await query.answer(text=f"Đã đổi thời gian xóa thành {format_seconds(seconds)}!")
            await send_or_edit_text(update, settings_text(user_id), reply_markup=settings_keyboard(user_id))
            return

        if data == "setting:custom":
            await query.answer()
            markup = ForceReply(
                selective=True,
                input_field_placeholder="Ví dụ: 300",
            )
            message = await query.message.reply_text(
                "Nhập số giây muốn auto xóa message.\nVí dụ: 300",
                reply_markup=markup,
            )
            set_setting_context(
                user_id,
                {
                    "stage": "auto_delete_seconds",
                    "prompt_message_id": message.message_id,
                    "settings_message_id": query.message.message_id,
                },
            )
            schedule_auto_delete(message, user_id)
            return
    else:
        await query.answer()

    if data.startswith("gpa:"):
        try:
            records = await asyncio.to_thread(get_gpa_records, user_id)
        except KeyError:
            await prompt_login_credentials(update, user_id)
            return
        except Exception as exc:
            logger.exception("GPA callback failed")
            await send_or_edit_text(update, f"Không lấy được GPA: {exc}")
            return

        if data == "gpa:menu":
            await send_or_edit_text(update, "Chọn kiểu xem GPA:", reply_markup=gpa_menu_keyboard(records))
            return

        if data == "gpa:current":
            await send_or_edit_html(
                update,
                format_gpa_table_html(records[:1], "GPA hiện tại"),
                reply_markup=gpa_back_keyboard(),
            )
            return

        if data.startswith("gpa:year:"):
            year = int(data.split(":")[2])
            year_records = gpa_records_for_year(records, year)
            await send_or_edit_html(
                update,
                format_gpa_table_html(year_records, f"GPA năm {year}-{year + 1}"),
                reply_markup=gpa_back_keyboard(),
            )
            return

    try:
        rows = await asyncio.to_thread(get_scores, user_id)
    except KeyError:
        await prompt_login_credentials(update, user_id)
        return
    except Exception as exc:
        logger.exception("Callback scores failed")
        await send_or_edit_text(update, f"Không lấy được điểm: {exc}")
        return

    if data == "back:years":
        set_check_context(user_id, {"stage": "years"})
        await send_or_edit_text(update, "Chọn năm học:", reply_markup=year_keyboard(rows))
        return

    if data == "back:subjects":
        ctx = get_check_context(user_id)
        year = ctx.get("year")
        semester = ctx.get("semester")
        if year is None:
            set_check_context(user_id, {"stage": "subjects", "year": None, "semester": None})
            await send_or_edit_html(
                update,
                format_score_table(rows, f"Tất cả: {len(rows)} môn"),
                reply_markup=subjects_keyboard(rows),
            )
        elif semester is None:
            set_check_context(user_id, {"stage": "subjects", "year": year, "semester": None})
            year_rows = rows_for_year(rows, year)
            await send_or_edit_html(
                update,
                format_score_table(year_rows, f"Năm {year}-{year + 1}: {len(year_rows)} môn"),
                reply_markup=subjects_keyboard(year_rows, year),
            )
        else:
            set_check_context(user_id, {"stage": "subjects", "year": year, "semester": semester})
            semester_rows = rows_for_semester(rows, year, semester)
            await send_or_edit_html(
                update,
                format_score_table(
                    semester_rows,
                    f"Năm {year}-{year + 1}, học kỳ {semester}: {len(semester_rows)} môn",
                ),
                reply_markup=subjects_keyboard(semester_rows, year),
            )
        return

    if data.startswith("back:semesters:"):
        year = int(data.split(":")[2])
        set_check_context(user_id, {"stage": "semesters", "year": year})
        await send_or_edit_text(
            update,
            f"Năm học {year}-{year + 1}. Chọn học kỳ:",
            reply_markup=semester_keyboard(rows, year),
        )
        return

    if data == "all":
        set_check_context(user_id, {"stage": "subjects", "year": None, "semester": None})
        await send_or_edit_html(
            update,
            format_score_table(rows, f"Tất cả: {len(rows)} môn"),
            reply_markup=subjects_keyboard(rows),
        )
        return

    if data.startswith("year:"):
        year = int(data.split(":")[1])
        set_check_context(user_id, {"stage": "semesters", "year": year})
        await send_or_edit_text(
            update,
            f"Năm học {year}-{year + 1}. Chọn học kỳ:",
            reply_markup=semester_keyboard(rows, year),
        )
        return

    if data.startswith("semester:"):
        _, year_text, semester_text = data.split(":")
        year = int(year_text)
        semester = int(semester_text)
        semester_rows = rows_for_semester(rows, year, semester)
        set_check_context(user_id, {"stage": "subjects", "year": year, "semester": semester})
        await send_or_edit_html(
            update,
            format_score_table(
                semester_rows,
                f"Năm {year}-{year + 1}, học kỳ {semester}: {len(semester_rows)} môn",
            ),
            reply_markup=subjects_keyboard(semester_rows, year),
        )
        return

    if data.startswith("subject:"):
        selected_score_id = data.split(":", 1)[1]
        matched_rows = find_rows(rows, selected_score_id)
        if not matched_rows:
            await send_or_edit_text(update, "Không tìm thấy môn này.")
            return
        markup = InlineKeyboardMarkup([[InlineKeyboardButton("Quay lại", callback_data="back:subjects")]])
        await send_or_edit_html(update, format_detail_table_html(matched_rows[0]), reply_markup=markup)
        return


async def text_selection(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.effective_user:
        return

    user_id = str(update.effective_user.id)
    text = (update.message.text or "").strip()

    if get_setting_context(user_id):
        ctx = get_setting_context(user_id)
        try:
            await update.message.delete()
        except Exception:
            pass

        if "prompt_message_id" in ctx:
            try:
                await context.bot.delete_message(
                    chat_id=update.message.chat_id,
                    message_id=ctx["prompt_message_id"],
                )
            except Exception:
                pass

        if not text.isdigit():
            markup = ForceReply(
                selective=True,
                input_field_placeholder="Ví dụ: 300",
            )
            message = await update.message.reply_text(
                "Giá trị không hợp lệ. Vui lòng nhập số giây (phải là số nguyên dương).\nVí dụ: 300",
                reply_markup=markup,
            )
            set_setting_context(
                user_id,
                {
                    **ctx,
                    "prompt_message_id": message.message_id,
                },
            )
            schedule_auto_delete(message, user_id)
            return

        seconds = int(text)
        if seconds < 0:
            markup = ForceReply(
                selective=True,
                input_field_placeholder="Ví dụ: 300",
            )
            message = await update.message.reply_text(
                "Số giây phải lớn hơn hoặc bằng 0. Vui lòng nhập lại:\nVí dụ: 300",
                reply_markup=markup,
            )
            set_setting_context(
                user_id,
                {
                    **ctx,
                    "prompt_message_id": message.message_id,
                },
            )
            schedule_auto_delete(message, user_id)
            return

        update_user_settings(
            user_id,
            auto_delete_enabled=seconds > 0,
            auto_delete_seconds=seconds,
        )
        clear_setting_context(user_id)

        if "settings_message_id" in ctx:
            try:
                await context.bot.edit_message_text(
                    chat_id=update.message.chat_id,
                    message_id=ctx["settings_message_id"],
                    text=settings_text(user_id),
                    reply_markup=settings_keyboard(user_id),
                )
                return
            except Exception:
                pass

        await send_or_edit_text(update, settings_text(user_id), reply_markup=settings_keyboard(user_id))
        return

    if get_login_context(user_id):
        parts = text.split(maxsplit=1)
        if len(parts) < 2:
            await prompt_login_credentials(update, user_id)
            return
        await perform_login(update, parts[0], parts[1], user_id)
        return

    if text.isdigit() and get_check_context(user_id):
        context.args = [text]
        await check_command(update, context)


async def health_check_handler(reader, writer):
    response = "HTTP/1.1 200 OK\r\nContent-Length: 2\r\nContent-Type: text/plain\r\n\r\nOK"
    writer.write(response.encode("utf-8"))
    await writer.drain()
    writer.close()
    await writer.wait_closed()


async def start_health_check_server() -> None:
    port = int(os.environ.get("PORT", "8000"))
    try:
        server = await asyncio.start_server(health_check_handler, "0.0.0.0", port)
        logger.info("Health check server running on port %s", port)
        async with server:
            await server.serve_forever()
    except Exception as e:
        logger.exception("Failed to start health check server: %s", e)


async def post_init(application) -> None:
    await application.bot.set_my_commands(
        [
            BotCommand("start", "Hướng dẫn sử dụng"),
            BotCommand("login", "Đăng nhập UNETI"),
            BotCommand("check", "Xem điểm"),
            BotCommand("gpa", "Xem GPA"),
            BotCommand("setting", "Cài đặt bot"),
            BotCommand("logout", "Đăng xuất tài khoản"),
        ]
    )
    asyncio.create_task(start_health_check_server())


def main() -> None:
    load_dotenv()
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not bot_token:
        raise RuntimeError("Missing environment variable: TELEGRAM_BOT_TOKEN")

    app = ApplicationBuilder().token(bot_token).post_init(post_init).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("login", login_command))
    app.add_handler(CommandHandler("logout", logout_command))
    app.add_handler(CommandHandler("check", check_command))
    app.add_handler(CommandHandler("gpa", gpa_command))
    app.add_handler(CommandHandler("setting", setting_command))
    app.add_handler(CallbackQueryHandler(callback_selection))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_selection))
    app.run_polling()


if __name__ == "__main__":
    main()
