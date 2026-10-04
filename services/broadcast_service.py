"""Broadcast Notification Service.

Sends restock announcements and updates to all registered users who have
ever interacted with the bot, formatted in their preferred language (vi/en)
with direct 'Buy Now' inline buttons.
"""

import asyncio
import logging
from typing import Optional
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import Forbidden, TelegramError

from config import ADMIN_CHAT_ID, TELEGRAM_TOKEN, get_admin_ids, get_product_image
from database.database import get_db
from database.models import Product, User
from services.account_service import AccountService
from services.i18n import t

logger = logging.getLogger(__name__)


class BroadcastService:
    """Manages system-wide broadcasts to all Telegram customers."""

    @classmethod
    async def broadcast_restock(
        cls,
        bot: Bot,
        product_id: int,
        added_count: int,
        admin_chat_id: Optional[int] = None,
    ) -> dict:
        """Broadcast restock notification to all users who ever chatted with the bot.

        Args:
            bot: Active Telegram Bot instance.
            product_id: ID of the restocked product.
            added_count: Number of accounts newly added.
            admin_chat_id: Optional admin ID to send execution summary.

        Returns:
            Dict containing broadcast metrics: total, sent, failed, blocked.
        """
        if added_count <= 0:
            return {"total": 0, "sent": 0, "failed": 0, "blocked": 0}

        with get_db() as db:
            product = db.query(Product).filter(Product.id == product_id).first()
            if not product or not product.is_active:
                logger.warning("Broadcast aborted: Product #%s not found or inactive", product_id)
                return {"total": 0, "sent": 0, "failed": 0, "blocked": 0}

            # Retrieve product inventory stats
            stats = AccountService.get_product_stats(db, product.id)
            current_stock = stats["available_stock"]
            product_name = product.name
            product_price = product.price

            # Retrieve all users who have ever interacted with the bot
            users = db.query(User).all()
            user_list = [
                {"id": u.id, "telegram_id": u.telegram_id, "language": u.language, "name": u.full_name}
                for u in users
            ]

        total_users = len(user_list)
        if total_users == 0:
            logger.info("Broadcast skipped: No registered users in database.")
            return {"total": 0, "sent": 0, "failed": 0, "blocked": 0}

        logger.info(
            "Starting restock broadcast for Product #%s ('%s', +%s accounts) to %s users...",
            product_id,
            product_name,
            added_count,
            total_users,
        )

        img_path = get_product_image(product_id, product_name)
        cached_file_id: Optional[str] = None

        sent_count = 0
        failed_count = 0
        blocked_count = 0

        for user_info in user_list:
            chat_id = user_info["telegram_id"]
            user_lang = user_info["language"] if user_info["language"] in ("vi", "en") else "vi"

            # Localize warranty policy badge
            p_lower = product_name.lower()
            if "bảo hành full" in p_lower or "full" in p_lower:
                warranty_badge = t("warranty_full_badge", user_lang)
            elif "offer" in p_lower or "trial" in p_lower:
                warranty_badge = t("warranty_offer_badge", user_lang)
            else:
                warranty_badge = t("warranty_none_badge", user_lang)

            # Localize caption & buy button
            caption = t(
                "restock_broadcast_caption",
                user_lang,
                name=product_name,
                price=product_price,
                added=added_count,
                stock=current_stock,
                warranty=warranty_badge,
            )

            buy_button_text = t("btn_buy_now_direct", user_lang, price=f"{product_price:,.0f}")
            keyboard = InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            text=buy_button_text,
                            callback_data=f"buy_prod_{product_id}",
                        )
                    ]
                ]
            )

            try:
                # 1. Try sending with product photo if available
                if cached_file_id:
                    await bot.send_photo(
                        chat_id=chat_id,
                        photo=cached_file_id,
                        caption=caption,
                        parse_mode="HTML",
                        reply_markup=keyboard,
                    )
                    sent_count += 1
                elif img_path and img_path.is_file():
                    try:
                        with open(img_path, "rb") as photo_file:
                            msg = await bot.send_photo(
                                chat_id=chat_id,
                                photo=photo_file,
                                caption=caption,
                                parse_mode="HTML",
                                reply_markup=keyboard,
                            )
                            if msg and msg.photo:
                                # Cache file_id to upload once and reuse for subsequent users
                                cached_file_id = msg.photo[-1].file_id
                        sent_count += 1
                    except Exception as photo_err:
                        logger.warning("Failed sending photo to %s, falling back to text: %s", chat_id, photo_err)
                        await bot.send_message(
                            chat_id=chat_id,
                            text=caption,
                            parse_mode="HTML",
                            reply_markup=keyboard,
                        )
                        sent_count += 1
                else:
                    # 2. Plain text message fallback
                    await bot.send_message(
                        chat_id=chat_id,
                        text=caption,
                        parse_mode="HTML",
                        reply_markup=keyboard,
                    )
                    sent_count += 1

            except Forbidden:
                # User has blocked the bot or deleted conversation
                logger.info("User %s blocked the bot. Skipping.", chat_id)
                blocked_count += 1
                failed_count += 1
            except TelegramError as tg_err:
                logger.warning("Telegram error broadcasting to %s: %s", chat_id, tg_err)
                failed_count += 1
            except Exception as exc:
                logger.error("Unexpected error broadcasting to %s: %s", chat_id, exc)
                failed_count += 1

            # Small delay to comply with Telegram Bot API rate limits (30 msgs/sec max)
            await asyncio.sleep(0.05)

        logger.info(
            "Restock broadcast finished: %s total, %s sent, %s failed (%s blocked)",
            total_users,
            sent_count,
            failed_count,
            blocked_count,
        )

        # Notify admin(s) of broadcast result
        target_admins = [admin_chat_id] if admin_chat_id else get_admin_ids()
        if target_admins:
            summary_text = (
                "📢 <b>KẾT QUẢ GỬI THÔNG BÁO HÀNG MỚI VỀ:</b>\n\n"
                f"• Mặt hàng: <b>{product_name}</b>\n"
                f"• Số lượng nạp: <b>+{added_count}</b> | Tồn kho: <b>{current_stock}</b>\n"
                f"• Tổng số khách đã từng nhắn tin: <b>{total_users}</b>\n"
                f"• Gửi thành công: <b>{sent_count}</b>\n"
                f"• Thất bại / Đã chặn bot: <b>{failed_count}</b>\n"
            )
            for aid in target_admins:
                if not aid:
                    continue
                try:
                    await bot.send_message(
                        chat_id=aid,
                        text=summary_text,
                        parse_mode="HTML",
                    )
                except Exception as admin_err:
                    logger.warning("Failed to send broadcast summary to admin %s: %s", aid, admin_err)

        return {
            "total": total_users,
            "sent": sent_count,
            "failed": failed_count,
            "blocked": blocked_count,
        }

    @classmethod
    def broadcast_restock_sync(cls, product_id: int, added_count: int) -> dict:
        """Synchronous wrapper to execute broadcast from standalone CLI tools."""
        if not TELEGRAM_TOKEN:
            logger.warning("TELEGRAM_TOKEN is not configured; cannot broadcast.")
            return {"total": 0, "sent": 0, "failed": 0, "blocked": 0}

        async def _run():
            async with Bot(token=TELEGRAM_TOKEN) as bot:
                return await cls.broadcast_restock(bot, product_id, added_count)

        try:
            return asyncio.run(_run())
        except Exception as exc:
            logger.error("Failed running synchronous broadcast: %s", exc)
            return {"total": 0, "sent": 0, "failed": 0, "blocked": 0, "error": str(exc)}
