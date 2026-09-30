"""Application Entry Point for Telegram Shop Bot.

Initializes database tables, runs the payment webhook server (for SePay/PayOS),
registers Telegram command & conversation handlers, and manages polling.
"""

import asyncio
import logging
import sys
from aiohttp import web
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes

from config import TELEGRAM_TOKEN, WEBHOOK_HOST, WEBHOOK_PORT
from database.database import Base, engine, get_db
from handlers.checkout_handler import (
    checkout_conversation_handler,
    payment_confirm_callback_handler,
)
from handlers.start_handler import (
    add_stock_command,
    menu_callback_handler,
    myid_command,
    start,
    stock_command,
    test_pay_command,
)
from services.order_service import OrderService
from webhook_server import WebhookServer

# Configure logging format according to best practices
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
    handlers=[
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger(__name__)


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Log the error causing updates to fail."""
    logger.error("Exception while handling an update: %s", context.error, exc_info=context.error)


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

    # 2. Build python-telegram-bot Application
    logger.info("Building Telegram Bot application...")
    application = Application.builder().token(TELEGRAM_TOKEN).build()

    # Register error handler
    application.add_error_handler(error_handler)

    # 3. Register Command and Menu Handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("myid", myid_command))
    application.add_handler(CommandHandler("stock", stock_command))
    application.add_handler(CommandHandler("addstock", add_stock_command))
    application.add_handler(CommandHandler("test_pay", test_pay_command))

    # Main menu & Language inline buttons callback handler
    application.add_handler(CallbackQueryHandler(menu_callback_handler, pattern=r"^(menu_|set_lang_)"))

    # Payment confirmation callback handler ("paid_order_<id>")
    application.add_handler(
        CallbackQueryHandler(payment_confirm_callback_handler, pattern=r"^paid_order_\d+$")
    )

    # 4. Register Checkout FSM ConversationHandler
    application.add_handler(checkout_conversation_handler)

    # 5. Initialize Telegram Application
    await application.initialize()
    await application.start()
    await application.updater.start_polling(drop_pending_updates=True)
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
