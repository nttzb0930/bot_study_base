import os
import json
from pathlib import Path

MONGODB_URI = os.getenv("MONGODB_URI")
db = None

if MONGODB_URI:
    try:
        from pymongo import MongoClient
        import certifi
        client = MongoClient(MONGODB_URI, tlsCAFile=certifi.where())
        # Get the default database (specified in URI or falls back to 'telegram_bot')
        db = client.get_default_database("telegram_bot")
        print("Connected to MongoDB successfully.")
    except ImportError:
        print("pymongo or certifi is not installed. Falling back to local JSON storage.")
        db = None
    except Exception as e:
        print(f"Error connecting to MongoDB: {e}. Falling back to local JSON storage.")
        db = None

TOKEN_FILE = Path(os.getenv("TOKEN_FILE", "tokens.json"))
SETTINGS_FILE = Path(os.getenv("SETTINGS_FILE", "user_settings.json"))

def load_tokens() -> dict:
    if db is not None:
        try:
            tokens_col = db["tokens"]
            result = {}
            for doc in tokens_col.find():
                user_id = doc.get("telegram_user_id")
                if user_id:
                    result[str(user_id)] = {
                        "username": doc.get("username"),
                        "token": doc.get("token"),
                        "refreshToken": doc.get("refreshToken"),
                    }
            return result
        except Exception as e:
            print(f"MongoDB load_tokens error: {e}. Falling back to file.")
            
    if not TOKEN_FILE.exists():
        return {}
    content = TOKEN_FILE.read_text(encoding="utf-8").strip()
    if not content:
        return {}
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        return {}

def save_tokens(tokens: dict) -> None:
    if db is not None:
        try:
            tokens_col = db["tokens"]
            for user_id, info in tokens.items():
                tokens_col.update_one(
                    {"telegram_user_id": str(user_id)},
                    {"$set": {
                        "username": info.get("username"),
                        "token": info.get("token"),
                        "refreshToken": info.get("refreshToken"),
                    }},
                    upsert=True
                )
            return
        except Exception as e:
            print(f"MongoDB save_tokens error: {e}. Falling back to file.")

    TOKEN_FILE.write_text(
        json.dumps(tokens, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

def load_user_settings() -> dict:
    if db is not None:
        try:
            settings_col = db["settings"]
            result = {}
            for doc in settings_col.find():
                user_id = doc.get("telegram_user_id")
                if user_id:
                    result[str(user_id)] = {
                        "auto_delete_enabled": doc.get("auto_delete_enabled"),
                        "auto_delete_seconds": doc.get("auto_delete_seconds"),
                    }
            return result
        except Exception as e:
            print(f"MongoDB load_user_settings error: {e}. Falling back to file.")

    if not SETTINGS_FILE.exists():
        return {}
    content = SETTINGS_FILE.read_text(encoding="utf-8").strip()
    if not content:
        return {}
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        return {}

def save_user_settings(settings: dict) -> None:
    if db is not None:
        try:
            settings_col = db["settings"]
            for user_id, info in settings.items():
                settings_col.update_one(
                    {"telegram_user_id": str(user_id)},
                    {"$set": {
                        "auto_delete_enabled": info.get("auto_delete_enabled"),
                        "auto_delete_seconds": info.get("auto_delete_seconds"),
                    }},
                    upsert=True
                )
            return
        except Exception as e:
            print(f"MongoDB save_user_settings error: {e}. Falling back to file.")

    SETTINGS_FILE.write_text(
        json.dumps(settings, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
