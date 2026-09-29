"""Application configuration module.

Loads environment variables from `.env` and exports typed configuration
constants. Validates that essential configuration variables (e.g., TELEGRAM_TOKEN)
are properly set before the application boots.
"""

import os
from pathlib import Path
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
ADMIN_CHAT_ID: str = os.getenv("ADMIN_CHAT_ID", "").strip()

# Configuration validation
if not TELEGRAM_TOKEN:
    raise ValueError(
        "CRITICAL CONFIGURATION ERROR: TELEGRAM_TOKEN is not configured.\n"
        "Please create a `.env` file in the project root (see `.env.example`) "
        "and define your Telegram bot token obtained from @BotFather."
    )
