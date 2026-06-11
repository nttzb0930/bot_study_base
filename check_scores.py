import json
import os
import sys
from pathlib import Path
from urllib.parse import quote

import requests
from dotenv import load_dotenv
from requests import Response


load_dotenv()

API_BASE_URL = os.getenv("API_BASE_URL", "https://apiv3.uneti.edu.vn/api")
from db import TOKEN_FILE, load_tokens, save_tokens

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")




class ApiResponseError(RuntimeError):
    pass


def parse_json_response(response: Response, action: str) -> dict:
    content_type = response.headers.get("content-type", "")
    body_preview = response.text.strip()[:300]

    if not response.text.strip():
        raise ApiResponseError(
            f"{action} thất bại: API trả về body rỗng "
            f"(HTTP {response.status_code}, content-type: {content_type or '-'})"
        )

    try:
        return response.json()
    except ValueError as exc:
        raise ApiResponseError(
            f"{action} thất bại: API không trả về JSON "
            f"(HTTP {response.status_code}, content-type: {content_type or '-'}). "
            f"Body: {body_preview or '-'}"
        ) from exc


def refresh_token(refresh_token_value: str) -> dict:
    response = requests.post(
        f"{API_BASE_URL}/jwt/RefreshToken",
        json={"refreshToken": refresh_token_value},
        timeout=15,
    )
    response.raise_for_status()
    return parse_json_response(response, "Refresh token")


def request_with_saved_token(method: str, url: str, token_info: dict, **kwargs):
    headers = kwargs.pop("headers", {})
    headers["Authorization"] = f"Bearer {token_info['token']}"

    response = requests.request(
        method,
        url,
        headers=headers,
        timeout=15,
        **kwargs,
    )

    if response.status_code not in (401, 403):
        response.raise_for_status()
        return response

    new_token = refresh_token(token_info["refreshToken"])
    token_info["token"] = new_token["token"]
    if new_token.get("refreshToken"):
        token_info["refreshToken"] = new_token["refreshToken"]

    headers["Authorization"] = f"Bearer {token_info['token']}"
    response = requests.request(
        method,
        url,
        headers=headers,
        timeout=15,
        **kwargs,
    )
    response.raise_for_status()
    return response


def get_scores(telegram_user_id: str = "default") -> list[dict]:
    tokens = load_tokens()
    token_info = tokens[str(telegram_user_id)]
    student_id = token_info["username"]
    encoded_student_id = quote(student_id)

    url = (
        f"{API_BASE_URL}/SP_TC_SV_KetQuaHocTap_TiepNhan/"
        "EDU_Load_Para_MaSinhVien_ChiTiet"
        f"?TC_SV_KetQuaHocTap_MaSinhVien={encoded_student_id}"
    )

    response = request_with_saved_token("GET", url, token_info)
    save_tokens(tokens)

    data = parse_json_response(response, "Lấy điểm")
    return data.get("body", [])


def get_gpa_records(telegram_user_id: str = "default") -> list[dict]:
    tokens = load_tokens()
    token_info = tokens[str(telegram_user_id)]
    student_id = token_info["username"]
    encoded_student_id = quote(student_id)

    url = (
        f"{API_BASE_URL}/SP_TC_SV_KetQuaHocTap_TiepNhan/"
        "EDU_Load_Para_MaSinhVien_DiemTrungBinhHocKy"
        f"?TC_SV_KetQuaHocTap_MaSinhVien={encoded_student_id}"
    )

    response = request_with_saved_token("GET", url, token_info)
    save_tokens(tokens)

    data = parse_json_response(response, "Lấy GPA")
    return data.get("body", [])


def format_gpa_summary(records: list[dict], title: str = "GPA / Điểm trung bình") -> str:
    if not records:
        return "Không tìm thấy dữ liệu GPA."

    lines = [title]
    for record in records:
        term = record.get("TC_SV_KetQuaHocTap_TenDot") or "-"
        semester_10 = record.get("TC_SV_KetQuaHocTap_DiemTrungBinhHocKy")
        semester_4 = record.get("TC_SV_KetQuaHocTap_DiemTrungBinhHocKy_He4")
        cumulative_10 = record.get("TC_SV_KetQuaHocTap_DiemTrungBinhTichLuy")
        cumulative_4 = record.get("TC_SV_KetQuaHocTap_DiemTrungBinhTichLuy_He4")
        registered_credits = record.get("TC_SV_KetQuaHocTap_TongTinChi_DangKy")
        accumulated_credits = record.get("TC_SV_KetQuaHocTap_TongTinChi_TichLuy")
        debt_credits = record.get("TC_SV_KetQuaHocTap_TongTinChi_No")
        rank = record.get("TC_SV_KetQuaHocTap_XepLoaiHocLuc_TichLuy")

        lines.extend(
            [
                "",
                f"Học kỳ: {term}",
                f"TB học kỳ hệ 10: {semester_10 if semester_10 is not None else '-'}",
                f"TB học kỳ hệ 4: {semester_4 if semester_4 is not None else '-'}",
                f"TB tích lũy hệ 10: {cumulative_10 if cumulative_10 is not None else '-'}",
                f"TB tích lũy hệ 4: {cumulative_4 if cumulative_4 is not None else '-'}",
                f"Tín chỉ đăng ký: {registered_credits if registered_credits is not None else '-'}",
                f"Tín chỉ tích lũy: {accumulated_credits if accumulated_credits is not None else '-'}",
                f"Tín chỉ nợ: {debt_credits if debt_credits is not None else '-'}",
                f"Xếp loại tích lũy: {rank or '-'}",
            ]
        )

    return "\n".join(lines)


def format_gpa_summary_table(records: list[dict], title: str = "GPA / Điểm trung bình") -> str:
    if not records:
        return "Không tìm thấy dữ liệu GPA."

    lines = [title]
    for record in records:
        term = record.get("TC_SV_KetQuaHocTap_TenDot") or "-"
        rows = [
            ("Học kỳ", term),
            ("TB học kỳ hệ 10", record.get("TC_SV_KetQuaHocTap_DiemTrungBinhHocKy")),
            ("TB học kỳ hệ 4", record.get("TC_SV_KetQuaHocTap_DiemTrungBinhHocKy_He4")),
            ("TB tích lũy hệ 10", record.get("TC_SV_KetQuaHocTap_DiemTrungBinhTichLuy")),
            ("TB tích lũy hệ 4", record.get("TC_SV_KetQuaHocTap_DiemTrungBinhTichLuy_He4")),
            ("Tín chỉ đăng ký", record.get("TC_SV_KetQuaHocTap_TongTinChi_DangKy")),
            ("Tín chỉ tích lũy", record.get("TC_SV_KetQuaHocTap_TongTinChi_TichLuy")),
            ("Tín chỉ nợ", record.get("TC_SV_KetQuaHocTap_TongTinChi_No")),
            ("Xếp loại tích lũy", record.get("TC_SV_KetQuaHocTap_XepLoaiHocLuc_TichLuy")),
        ]

        lines.extend(["", "-" * min(len(str(term)) + 8, 32)])
        for label, value in rows:
            display_value = "-" if value is None or value == "" else str(value)
            lines.append(f"{label:<18}: {display_value}")

    return "\n".join(lines)


def get_gpa_summary(telegram_user_id: str = "default") -> str:
    return format_gpa_summary(get_gpa_records(telegram_user_id))


def format_score_row(row: dict) -> str:
    subject = row.get("TC_SV_KetQuaHocTap_TenMonHoc") or "Không rõ môn"
    term = row.get("TC_SV_KetQuaHocTap_HocKy") or ""
    final_score = row.get("TC_SV_KetQuaHocTap_DiemTongKet")
    exam_score = row.get("TC_SV_KetQuaHocTap_DiemThi")
    letter = row.get("TC_SV_KetQuaHocTap_DiemChu")

    parts = [subject]
    if term:
        parts.append(f"HK: {term}")
    if final_score is not None:
        parts.append(f"TK: {final_score}")
    if exam_score is not None:
        parts.append(f"Thi: {exam_score}")
    if letter:
        parts.append(f"Chữ: {letter.strip()}")

    return " | ".join(parts)


def _format_values(row: dict, keys: list[str]) -> str:
    values = []

    for key in keys:
        value = row.get(key)
        if value is not None and value != "":
            values.append(str(value).strip())

    return ", ".join(values) if values else "-"


def format_score_row_detail(row: dict) -> str:
    subject = row.get("TC_SV_KetQuaHocTap_TenMonHoc") or "Không rõ môn"
    subject_code = row.get("TC_SV_KetQuaHocTap_MaMonHoc") or "-"
    class_code = row.get("TC_SV_KetQuaHocTap_MaLopHocPhan") or "-"
    term = row.get("TC_SV_KetQuaHocTap_HocKy") or "-"
    credits = row.get("TC_SV_KetQuaHocTap_SoTinChi")
    attendance = row.get("TC_SV_KetQuaHocTap_DiemDanh")
    allowed_exam = row.get("TC_SV_KetQuaHocTap_XetDuThi")
    not_counted = row.get("TC_SV_KetQuaHocTap_KhongTinhDiemTBC")
    subject_type = row.get("TC_SV_KetQuaHocTap_TenLoaiMonHoc") or "-"

    practice_scores = _format_values(
        row,
        [
            "TC_SV_KetQuaHocTap_DiemChuyenCan_ThucHanh",
            "TC_SV_KetQuaHocTap_DiemThucHanh1",
            "TC_SV_KetQuaHocTap_DiemThucHanh2",
            "TC_SV_KetQuaHocTap_DiemThucHanh3",
            "TC_SV_KetQuaHocTap_DiemThucHanh4",
            "TC_SV_KetQuaHocTap_DiemThucHanh5",
            "TC_SV_KetQuaHocTap_DiemThucHanh6",
            "TC_SV_KetQuaHocTap_DiemThucHanh7",
            "TC_SV_KetQuaHocTap_DiemThucHanh8",
            "TC_SV_KetQuaHocTap_DiemThucHanh9",
        ],
    )
    coefficient_1_scores = _format_values(
        row,
        [
            "TC_SV_KetQuaHocTap_DiemChuyenCan_LyThuyet",
            "TC_SV_KetQuaHocTap_DiemHeSo11",
            "TC_SV_KetQuaHocTap_DiemHeSo12",
            "TC_SV_KetQuaHocTap_DiemHeSo13",
            "TC_SV_KetQuaHocTap_DiemHeSo14",
            "TC_SV_KetQuaHocTap_DiemHeSo15",
            "TC_SV_KetQuaHocTap_DiemHeSo16",
            "TC_SV_KetQuaHocTap_DiemHeSo17",
            "TC_SV_KetQuaHocTap_DiemHeSo18",
            "TC_SV_KetQuaHocTap_DiemHeSo19",
        ],
    )
    coefficient_2_scores = _format_values(
        row,
        [
            "TC_SV_KetQuaHocTap_DiemHeSo21",
            "TC_SV_KetQuaHocTap_DiemHeSo22",
            "TC_SV_KetQuaHocTap_DiemHeSo23",
            "TC_SV_KetQuaHocTap_DiemHeSo24",
            "TC_SV_KetQuaHocTap_DiemHeSo25",
            "TC_SV_KetQuaHocTap_DiemHeSo26",
            "TC_SV_KetQuaHocTap_DiemHeSo27",
            "TC_SV_KetQuaHocTap_DiemHeSo28",
            "TC_SV_KetQuaHocTap_DiemHeSo29",
        ],
    )
    skill_exam_scores = _format_values(
        row,
        [
            "TC_SV_KetQuaHocTap_DiemThiKyNang1",
            "TC_SV_KetQuaHocTap_DiemThiKyNang2",
            "TC_SV_KetQuaHocTap_DiemThiKyNang3",
            "TC_SV_KetQuaHocTap_DiemThiKyNang4",
        ],
    )

    regular_average = row.get("TC_SV_KetQuaHocTap_DiemTBThuongKy")
    practice_average = row.get("TC_SV_KetQuaHocTap_DiemTBThucHanh")
    exam_score = row.get("TC_SV_KetQuaHocTap_DiemThi")
    exam_score_1 = row.get("TC_SV_KetQuaHocTap_DiemThi1")
    exam_score_2 = row.get("TC_SV_KetQuaHocTap_DiemThi2")
    final_score = row.get("TC_SV_KetQuaHocTap_DiemTongKet")
    final_score_1 = row.get("TC_SV_KetQuaHocTap_DiemTongKet1")
    final_score_2 = row.get("TC_SV_KetQuaHocTap_DiemTongKet2")
    credit_score = row.get("TC_SV_KetQuaHocTap_DiemTinChi")
    letter_score = (row.get("TC_SV_KetQuaHocTap_DiemChu") or "").strip() or "-"
    rank = row.get("TC_SV_KetQuaHocTap_XepLoai") or "-"
    absent_exam = row.get("TC_SV_KetQuaHocTap_VangThi")
    note_1 = row.get("TC_SV_KetQuaHocTap_GhiChu1") or "-"
    note_2 = row.get("TC_SV_KetQuaHocTap_GhiChu2") or "-"

    return "\n".join(
        [
            f"{subject}",
            f"  Mã môn: {subject_code} | Lớp HP: {class_code}",
            f"  Học kỳ: {term} | Tín chỉ: {credits if credits is not None else '-'} | Loại: {subject_type}",
            f"  Điểm danh: {attendance if attendance is not None else '-'} | Xét dự thi: {allowed_exam if allowed_exam is not None else '-'} | Không tính TBC: {not_counted}",
            f"  Thực hành: {practice_scores} | TB TH: {practice_average if practice_average is not None else '-'}",
            f"  Hệ số 1: {coefficient_1_scores}",
            f"  Hệ số 2: {coefficient_2_scores}",
            f"  TB thường kỳ: {regular_average if regular_average is not None else '-'}",
            f"  Thi: {exam_score if exam_score is not None else '-'} | Thi 1: {exam_score_1 if exam_score_1 is not None else '-'} | Thi 2: {exam_score_2 if exam_score_2 is not None else '-'} | Kỹ năng: {skill_exam_scores}",
            f"  Tổng kết: {final_score if final_score is not None else '-'} | TK1: {final_score_1 if final_score_1 is not None else '-'} | TK2: {final_score_2 if final_score_2 is not None else '-'}",
            f"  Tín chỉ: {credit_score if credit_score is not None else '-'} | Chữ: {letter_score} | Xếp loại: {rank} | Vắng thi: {absent_exam}",
            f"  Ghi chú: {note_1} {note_2}",
        ]
    )


def format_score_row_detail_table(row: dict) -> str:
    subject = row.get("TC_SV_KetQuaHocTap_TenMonHoc") or "Không rõ môn"
    subject_code = row.get("TC_SV_KetQuaHocTap_MaMonHoc") or "-"
    class_code = row.get("TC_SV_KetQuaHocTap_MaLopHocPhan") or "-"
    term = row.get("TC_SV_KetQuaHocTap_HocKy") or "-"
    credits = row.get("TC_SV_KetQuaHocTap_SoTinChi")
    subject_type = row.get("TC_SV_KetQuaHocTap_TenLoaiMonHoc") or "-"

    practice_scores = _format_values(
        row,
        [
            "TC_SV_KetQuaHocTap_DiemChuyenCan_ThucHanh",
            "TC_SV_KetQuaHocTap_DiemThucHanh1",
            "TC_SV_KetQuaHocTap_DiemThucHanh2",
            "TC_SV_KetQuaHocTap_DiemThucHanh3",
            "TC_SV_KetQuaHocTap_DiemThucHanh4",
            "TC_SV_KetQuaHocTap_DiemThucHanh5",
            "TC_SV_KetQuaHocTap_DiemThucHanh6",
            "TC_SV_KetQuaHocTap_DiemThucHanh7",
            "TC_SV_KetQuaHocTap_DiemThucHanh8",
            "TC_SV_KetQuaHocTap_DiemThucHanh9",
        ],
    )
    coefficient_1_scores = _format_values(
        row,
        [
            "TC_SV_KetQuaHocTap_DiemChuyenCan_LyThuyet",
            "TC_SV_KetQuaHocTap_DiemHeSo11",
            "TC_SV_KetQuaHocTap_DiemHeSo12",
            "TC_SV_KetQuaHocTap_DiemHeSo13",
            "TC_SV_KetQuaHocTap_DiemHeSo14",
            "TC_SV_KetQuaHocTap_DiemHeSo15",
            "TC_SV_KetQuaHocTap_DiemHeSo16",
            "TC_SV_KetQuaHocTap_DiemHeSo17",
            "TC_SV_KetQuaHocTap_DiemHeSo18",
            "TC_SV_KetQuaHocTap_DiemHeSo19",
        ],
    )
    coefficient_2_scores = _format_values(
        row,
        [
            "TC_SV_KetQuaHocTap_DiemHeSo21",
            "TC_SV_KetQuaHocTap_DiemHeSo22",
            "TC_SV_KetQuaHocTap_DiemHeSo23",
            "TC_SV_KetQuaHocTap_DiemHeSo24",
            "TC_SV_KetQuaHocTap_DiemHeSo25",
            "TC_SV_KetQuaHocTap_DiemHeSo26",
            "TC_SV_KetQuaHocTap_DiemHeSo27",
            "TC_SV_KetQuaHocTap_DiemHeSo28",
            "TC_SV_KetQuaHocTap_DiemHeSo29",
        ],
    )

    rows = [
        ("Mã môn", subject_code),
        ("Lớp HP", class_code),
        ("Học kỳ", term),
        ("Tín chỉ", "-" if credits is None else credits),
        ("Loại", subject_type),
        ("Điểm danh", row.get("TC_SV_KetQuaHocTap_DiemDanh")),
        ("Xét dự thi", row.get("TC_SV_KetQuaHocTap_XetDuThi")),
        ("Không TBC", row.get("TC_SV_KetQuaHocTap_KhongTinhDiemTBC")),
        ("Thực hành", practice_scores),
        ("TB TH", row.get("TC_SV_KetQuaHocTap_DiemTBThucHanh")),
        ("Hệ số 1", coefficient_1_scores),
        ("Hệ số 2", coefficient_2_scores),
        ("TB thường kỳ", row.get("TC_SV_KetQuaHocTap_DiemTBThuongKy")),
        ("Thi", row.get("TC_SV_KetQuaHocTap_DiemThi")),
        ("Tổng kết", row.get("TC_SV_KetQuaHocTap_DiemTongKet")),
        ("Tín chỉ điểm", row.get("TC_SV_KetQuaHocTap_DiemTinChi")),
        ("Điểm chữ", (row.get("TC_SV_KetQuaHocTap_DiemChu") or "").strip() or "-"),
        ("Xếp loại", row.get("TC_SV_KetQuaHocTap_XepLoai")),
        ("Vắng thi", row.get("TC_SV_KetQuaHocTap_VangThi")),
        ("Ghi chú", f"{row.get('TC_SV_KetQuaHocTap_GhiChu1') or '-'} {row.get('TC_SV_KetQuaHocTap_GhiChu2') or '-'}"),
    ]

    lines = [subject, "-" * min(len(subject), 32)]
    for label, value in rows:
        display_value = "-" if value is None or value == "" else str(value)
        lines.append(f"{label:<12}: {display_value}")

    return "\n".join(lines)


def main() -> None:
    rows = get_scores()
    print(f"Tổng số dòng điểm: {len(rows)}")

    for index, row in enumerate(rows, start=1):
        print()
        print(f"#{index}")
        print(format_score_row_detail(row))


if __name__ == "__main__":
    main()
