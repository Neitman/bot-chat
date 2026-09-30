"""Application configuration module.

Loads environment variables from `.env` and exports typed configuration
constants. Validates that essential configuration variables (e.g., TELEGRAM_TOKEN)
are properly set before the application boots.
"""

import os
from pathlib import Path
import re
from dotenv import load_dotenv

# Base project directory
BASE_DIR: Path = Path(__file__).resolve().parent

# Load environment variables from .env file located at the project root
ENV_FILE: Path = BASE_DIR / ".env"
load_dotenv(dotenv_path=ENV_FILE)

# Telegram Bot Token
TELEGRAM_TOKEN: str = os.getenv("TELEGRAM_TOKEN", "").strip()

# Database Connection URL (Defaults to SQLite in project directory)
DATABASE_URL: str = os.getenv("DATABASE_URL", f"sqlite:///{BASE_DIR / 'shop_bot.db'}").strip()

# Bank Configuration for VietQR Payments
# Popular bank IDs: MB, VCB (Vietcombank), TCB (Techcombank), ICB (VietinBank), ACB, VPB, TPB, etc.
BANK_ID: str = os.getenv("BANK_ID", "MB").strip().upper()
BANK_ACCOUNT: str = os.getenv("BANK_ACCOUNT", "0123456789").strip()
BANK_ACCOUNT_NAME: str = os.getenv("BANK_ACCOUNT_NAME", "CHU SHOP").strip().upper()

# Webhook Server Configuration
WEBHOOK_HOST: str = os.getenv("WEBHOOK_HOST", "0.0.0.0").strip()
WEBHOOK_PORT: int = int(os.getenv("WEBHOOK_PORT", "8000").strip())
SEPAY_API_KEY: str = os.getenv("SEPAY_API_KEY", "").strip()


def get_admin_ids() -> list[int]:
    """Return list of allowed admin Telegram user IDs parsed from environment variables.

    Supports:
    - Numbered environment variables: ADMIN_CHAT_ID_1, ADMIN_CHAT_ID_2, ...
    - Standard variables (single or comma-separated): ADMIN_CHAT_ID, ADMIN_CHAT_IDS
    """
    raw_ids: list[str] = []

    # 1. Numbered variables: ADMIN_CHAT_ID_1, ADMIN_CHAT_ID_2, ...
    for key, value in os.environ.items():
        if key.startswith("ADMIN_CHAT_ID_"):
            val = value.strip()
            if val:
                raw_ids.extend(re.split(r"[,;\s|]+", val))

    # 2. Standard variables: ADMIN_CHAT_ID, ADMIN_CHAT_IDS
    for var_name in ("ADMIN_CHAT_ID", "ADMIN_CHAT_IDS"):
        val = os.getenv(var_name, "").strip()
        if val:
            raw_ids.extend(re.split(r"[,;\s|]+", val))

    ids: list[int] = []
    for part in raw_ids:
        part = part.strip()
        if part.isdigit() or (part.startswith("-") and part[1:].isdigit()):
            int_id = int(part)
            if int_id not in ids:
                ids.append(int_id)
    return ids


# Export ADMIN_CHAT_ID as comma-separated string for backwards-compatibility
_loaded_admin_ids = get_admin_ids()
ADMIN_CHAT_ID: str = ",".join(str(i) for i in _loaded_admin_ids)


def is_admin_user(user_id: int) -> bool:
    """Check if a given Telegram user ID has administrator privileges."""
    return user_id in get_admin_ids()


# Product Images Directories
IMAGES_DIR: Path = BASE_DIR / "images" / "products"
IMAGES_DIR.mkdir(parents=True, exist_ok=True)
IMAGES_ROOT_DIR: Path = BASE_DIR / "images"


def get_product_image(product_id: int, product_name: str = "") -> Path | None:
    """Find a corresponding product image by ID or filename stem.
    
    Searches both `images/products/` and `images/`.
    Supports formats: .png, .jpg, .jpeg, .webp
    Priority:
    1. By ID: e.g. '1.png', '2.jpg'
    2. By keywords:
       - 'bảo hành full' -> 'chatgpt_plus_warranty.png'
       - 'không bảo hành' -> 'chatgpt_plus_no_warranty.png'
    """
    search_dirs = [IMAGES_DIR, IMAGES_ROOT_DIR]
    extensions = (".png", ".jpg", ".jpeg", ".webp")

    # 1. Match by product ID
    for folder in search_dirs:
        for ext in extensions:
            candidate = folder / f"{product_id}{ext}"
            if candidate.is_file():
                return candidate

    # 2. Match by keyword in product name
    norm_name = product_name.lower()
    keyword_map = {
        "bảo hành full": "chatgpt_plus_warranty",
        "không bảo hành": "chatgpt_plus_no_warranty",
        "full": "chatgpt_plus_warranty",
        "plus": "chatgpt_plus_warranty",
    }
    for kw, stem in keyword_map.items():
        if kw in norm_name:
            for folder in search_dirs:
                for ext in extensions:
                    candidate = folder / f"{stem}{ext}"
                    if candidate.is_file():
                        return candidate

    # 3. Default product fallback
    for folder in search_dirs:
        for ext in extensions:
            candidate = folder / f"default{ext}"
            if candidate.is_file():
                return candidate

    return None


def get_store_banner() -> Path | None:
    """Find the general store banner if provided."""
    search_dirs = [IMAGES_DIR, IMAGES_ROOT_DIR]
    for folder in search_dirs:
        for ext in (".png", ".jpg", ".jpeg", ".webp"):
            for name in ("banner", "store_banner", "default"):
                candidate = folder / f"{name}{ext}"
                if candidate.is_file():
                    return candidate
    return None


# Configuration validation
if not TELEGRAM_TOKEN:
    raise ValueError(
        "CRITICAL CONFIGURATION ERROR: TELEGRAM_TOKEN is not configured.\n"
        "Please create a `.env` file in the project root (see `.env.example`) "
        "and define your Telegram bot token obtained from @BotFather."
    )

