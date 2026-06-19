import base64
import getpass
import hashlib
import json
import os
import time
from pathlib import Path

import requests
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad
from dotenv import load_dotenv


load_dotenv()

API_BASE_URL = os.getenv("API_BASE_URL", "https://apiv3.uneti.edu.vn/api")
CRYPTOJS_KEY = os.getenv("CRYPTOJS_KEY", "fd85b494-Uneti")
from db import TOKEN_FILE, load_tokens, save_tokens


def evp_bytes_to_key(password: bytes, salt: bytes, key_len: int = 32, iv_len: int = 16):
    derived = b""
    block = b""

    while len(derived) < key_len + iv_len:
        block = hashlib.md5(block + password + salt).digest()
        derived += block

    return derived[:key_len], derived[key_len:key_len + iv_len]


def cryptojs_aes_encrypt(text: str, passphrase: str = CRYPTOJS_KEY) -> str:
    salt = os.urandom(8)
    key, iv = evp_bytes_to_key(passphrase.encode("utf-8"), salt)
    cipher = AES.new(key, AES.MODE_CBC, iv)
    ciphertext = cipher.encrypt(pad(text.encode("utf-8"), AES.block_size))
    return base64.b64encode(b"Salted__" + salt + ciphertext).decode("utf-8")


def login(username: str, password: str) -> dict:
    response = requests.post(
        f"{API_BASE_URL}/jwt_NguoiDung/Login_NguoiDung",
        json={
            "TenDangNhap": cryptojs_aes_encrypt(username),
            "MatKhau": cryptojs_aes_encrypt(password),
        },
        timeout=15,
    )
    response.raise_for_status()
    return response.json()


def decode_jwt_payload(token: str) -> dict:
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload.encode("utf-8")))
    except Exception:
        return {}


def is_jwt_expired(token: str, leeway_seconds: int = 60) -> bool:
    payload = decode_jwt_payload(token)
    exp = payload.get("exp")
    if not isinstance(exp, (int, float)):
        return True

    return exp <= time.time() + leeway_seconds


def refresh_token(refresh_token_value: str) -> dict:
    response = requests.post(
        f"{API_BASE_URL}/jwt/RefreshToken",
        json={"refreshToken": refresh_token_value},
        timeout=15,
    )
    response.raise_for_status()
    return response.json()




def save_login(username: str, token_data: dict, telegram_user_id: str = "default") -> None:
    token = token_data.get("token")
    refresh_token = token_data.get("refreshToken")

    if not token or not refresh_token:
        raise ValueError("Login response does not contain token and refreshToken.")

    tokens = load_tokens()
    tokens[str(telegram_user_id)] = {
        "username": username,
        "token": token,
        "refreshToken": refresh_token,
    }
    save_tokens(tokens)


def get_existing_login_status(telegram_user_id: str) -> str | None:
    tokens = load_tokens()
    token_info = tokens.get(str(telegram_user_id))
    if not token_info:
        return None

    token = token_info.get("token")
    refresh_token_value = token_info.get("refreshToken")
    if not token or not refresh_token_value:
        return None

    if not is_jwt_expired(token):
        return "valid"

    # Do not locally check if the refresh token is expired (e.g. using is_jwt_expired).
    # The school API's refresh token may contain an 'exp' claim of 24h, but the school's
    # server actually accepts it for a much longer period. We let the server determine
    # if the refresh token is valid by making the API call directly.
    try:
        new_token_data = refresh_token(refresh_token_value)
        token_info["token"] = new_token_data["token"]
        if new_token_data.get("refreshToken"):
            token_info["refreshToken"] = new_token_data["refreshToken"]

        tokens[str(telegram_user_id)] = token_info
        save_tokens(tokens)
        return "refreshed"
    except Exception as e:
        print(f"Failed to refresh token for {telegram_user_id}: {e}")
        return None


def main() -> None:
    telegram_user_id = input("Telegram user id (blank = default): ").strip() or "default"
    username = input("Username/MSSV: ").strip()
    password = getpass.getpass("Password: ")

    token_data = login(username, password)
    save_login(username, token_data, telegram_user_id)

    print(f"Saved token for {telegram_user_id} to {TOKEN_FILE}")


if __name__ == "__main__":
    main()
