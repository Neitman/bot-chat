"""Application Entry Point for Telegram Shop Bot.

Initializes database tables, runs the payment webhook server (for SePay/PayOS),
registers Telegram command & conversation handlers, and manages polling.
"""

import asyncio
import logging
import sys
from aiohttp import web
from telegram import Update
from telegram.error import NetworkError, TelegramError, TimedOut
from telegram.request import HTTPXRequest
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    TypeHandler,
    filters,
)

from config import TELEGRAM_TOKEN, WEBHOOK_HOST, WEBHOOK_PORT
from database.database import Base, engine, get_db
from handlers.checkout_handler import (
    checkout_conversation_handler,
    payment_confirm_callback_handler,
)
from handlers.start_handler import (
    account_detail_command,
    accounts_callback_handler,
    accounts_command,
    add_stock_command,
    delete_account_command,
    language_command,
    maintenance_command,
    menu_callback_handler,
    myid_command,
    netflix_guide_command,
    orders_command,
    products_command,
    reply_keyboard_text_handler,
    setup_bot_commands,
    start,
    stock_command,
    support_command,
    test_pay_command,
)
from services.order_service import OrderService
from webhook_server import WebhookServer

# Ensure UTF-8 logging on Windows consoles
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# Configure logging format according to best practices
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
    handlers=[
        logging.StreamHandler(sys.stdout),
    ],
)
# Suppress noisy HTTP polling logs so interaction logs stand out clearly in terminal
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("telegram.ext.Updater").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)


async def log_user_interaction(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Log every user interaction (commands, messages, button clicks) clearly in the terminal."""
    user = update.effective_user
    if not user:
        return

    full_name = user.full_name or user.first_name or "Unknown"
    user_handle = f"@{user.username}" if user.username else f"ID: {user.id}"
    user_tag = f"{full_name} ({user_handle})"

    # 1. Callback query (inline button clicks)
    if update.callback_query:
        query_data = update.callback_query.data or ""
        logger.info("🔘 [BẤM NÚT INLINE] %s -> Callback: '%s'", user_tag, query_data)
        return

    # 2. Text message or commands
    if update.message and update.message.text:
        text = update.message.text.strip()
        if text.startswith("/"):
            logger.info("⚡ [LỆNH TELEGRAM] %s -> Lệnh: '%s'", user_tag, text)
        elif text.startswith(("🤖", "🛒", "📞", "🌐", "🆔", "📊", "➕")):
            logger.info("⌨️ [PHÍM BẤM MENU] %s -> Nút: '%s'", user_tag, text)
        else:
            logger.info("💬 [TIN NHẮN ĐẾN] %s -> Nội dung: '%s'", user_tag, text)
        return

    # 3. Photo or document
    if update.message and update.message.photo:
        caption_info = f" (Chú thích: '{update.message.caption}')" if update.message.caption else ""
        logger.info("📷 [GỬI ẢNH] %s%s", user_tag, caption_info)
        return

    # 4. Other types of interaction
    if update.message:
        logger.info("📩 [TƯƠNG TÁC] %s -> Gửi nội dung khác", user_tag)


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Log the error causing updates to fail without dumping huge tracebacks for transient network drops."""
    err = context.error
    if isinstance(err, (NetworkError, TimedOut)):
        logger.warning("🌐 Kết nối mạng bị chập chờn (%s). Đang tự động thử lại...", err)
        return
    logger.error("Exception while handling an update: %s", err, exc_info=err)


def polling_error_callback(exc: TelegramError) -> None:
    """Handle polling network errors cleanly without dumping ugly tracebacks."""
    if isinstance(exc, (NetworkError, TimedOut)):
        logger.warning("🌐 Kết nối Telegram bị chập chờn (%s). Bot đang tự động thử lại...", exc)
    else:
        logger.error("Lỗi khi polling Telegram: %s", exc, exc_info=exc)


def init_database() -> None:
    """Initialize database tables and seed ChatGPT Plus packages if empty."""
    logger.info("Initializing database schema...")
    Base.metadata.create_all(bind=engine)

    # Auto-migration for SQLite schema updates
    try:
        with engine.connect() as conn:
            cursor = conn.exec_driver_sql("PRAGMA table_info(users)")
            columns = [row[1] for row in cursor.fetchall()]
            if "language" not in columns:
                logger.info("Auto-migrating: adding 'language' column to users table...")
                conn.exec_driver_sql("ALTER TABLE users ADD COLUMN language VARCHAR(10)")
                conn.commit()
    except Exception as exc:
        logger.warning("Database schema auto-migration check: %s", exc)

    try:
        with get_db() as db:
            OrderService.seed_initial_products(db)
        logger.info("Database schema initialized and ChatGPT Plus plans verified.")
    except Exception as exc:
        logger.warning("Database seeding skipped or failed: %s", exc)


async def run_application() -> None:
    """Run Telegram Bot polling and Payment Webhook server concurrently."""
    # 1. Initialize SQLite Database & Tables
    init_database()

    # 1.1 Check Pato Partner API connectivity if configured
    try:
        from services.pato_service import PatoService
        if PatoService.is_configured():
            pato_info = await PatoService.get_quota()
            logger.info("Pato Partner API connected (Remaining Quota: %s).", pato_info.get("remaining_quota"))
    except Exception as pato_init_err:
        logger.warning("Could not connect to Pato API on startup: %s", pato_init_err)

    # 2. Build python-telegram-bot Application with robust timeouts
    logger.info("Building Telegram Bot application...")
    request_config = HTTPXRequest(
        connection_pool_size=16,
        connect_timeout=20.0,
        read_timeout=30.0,
        write_timeout=20.0,
        pool_timeout=5.0,
    )
    application = (
        Application.builder()
        .token(TELEGRAM_TOKEN)
        .request(request_config)
        .build()
    )

    # Register error handler
    application.add_error_handler(error_handler)

    # Global user interaction logger (group=-1 processes before all handlers)
    application.add_handler(TypeHandler(Update, log_user_interaction), group=-1)

    # 3. Register Command and Menu Handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler(["orders", "cart", "history"], orders_command))
    application.add_handler(CommandHandler(["support", "help"], support_command))
    application.add_handler(CommandHandler(["language", "lang"], language_command))
    application.add_handler(CommandHandler(["products", "shop"], products_command))
    application.add_handler(CommandHandler("myid", myid_command))
    application.add_handler(CommandHandler("stock", stock_command))
    application.add_handler(CommandHandler(["accounts", "accs", "view_accounts", "product_accounts"], accounts_command))
    application.add_handler(CommandHandler(["account", "acc"], account_detail_command))
    application.add_handler(CommandHandler(["delete", "delacc", "del_account"], delete_account_command))
    application.add_handler(CommandHandler("addstock", add_stock_command))
    application.add_handler(CommandHandler("test_pay", test_pay_command))
    application.add_handler(CommandHandler(["baotri", "maintenance", "broadcast_maintenance"], maintenance_command))
    application.add_handler(CommandHandler(["netflix", "hd_netflix", "netflix_guide"], netflix_guide_command))

    # Main menu, Netflix guide & Language inline buttons callback handler
    application.add_handler(CallbackQueryHandler(menu_callback_handler, pattern=r"^(menu_|set_lang_|guide_)"))

    # Accounts management and deletion callback handler
    application.add_handler(CallbackQueryHandler(accounts_callback_handler, pattern=r"^(acc_|del_acc_|admin_)"))

    # Payment confirmation callback handler ("paid_order_<id>")
    application.add_handler(
        CallbackQueryHandler(payment_confirm_callback_handler, pattern=r"^paid_order_\d+$")
    )

    # 4. Register Checkout FSM ConversationHandler
    application.add_handler(checkout_conversation_handler)

    # 5. Register Reply Keyboard Text Handler (for persistent bottom button taps)
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, reply_keyboard_text_handler)
    )

    # 6. Initialize Telegram Application & Register Command suggestions for autocomplete
    await application.initialize()
    await setup_bot_commands(application)
    await application.start()
    await application.updater.start_polling(
        drop_pending_updates=True,
        error_callback=polling_error_callback,
    )
    logger.info("Telegram Shop Bot polling is active!")

    # 6. Start aiohttp Webhook Server for Bank Payment Notifications
    webhook = WebhookServer(bot=application.bot)
    runner = web.AppRunner(webhook.app)
    await runner.setup()
    # Using host=None binds to all interfaces on both IPv4 (0.0.0.0) and IPv6 (::)
    # ensuring compatibility with localhost, 127.0.0.1, and [::1] on Windows
    listen_host = None if WEBHOOK_HOST in ("0.0.0.0", "") else WEBHOOK_HOST
    site = web.TCPSite(runner, host=listen_host, port=WEBHOOK_PORT)
    await site.start()
    logger.info("Payment Webhook server listening on port %s (IPv4 & IPv6)", WEBHOOK_PORT)
    logger.info("Ready to receive bank transfer notifications from SePay / PayOS / local test.")

    # 7. Keep running until termination signal
    stop_signal = asyncio.Event()
    try:
        await stop_signal.wait()
    except (asyncio.CancelledError, KeyboardInterrupt):
        logger.info("Shutting down application...")
    finally:
        logger.info("Stopping polling and web server...")
        await application.updater.stop()
        await application.stop()
        await application.shutdown()
        await runner.cleanup()
        logger.info("Application cleanly stopped.")


def main() -> None:
    """Entry point."""
    try:
        asyncio.run(run_application())
    except KeyboardInterrupt:
        logger.info("Exited by user.")


if __name__ == "__main__":
    main()
