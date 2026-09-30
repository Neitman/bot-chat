"""Start handler module.

Handles the `/start` command and navigation menu for ChatGPT Plus Shop Bot.
"""

import asyncio
from functools import wraps
import logging
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from config import ADMIN_CHAT_ID, get_store_banner, is_admin_user
from database.database import get_db
from services.account_service import AccountService
from services.broadcast_service import BroadcastService
from services.i18n import get_user_lang, is_user_lang_set, set_user_lang, t
from services.order_service import OrderService
from services.payment_service import PaymentService

logger = logging.getLogger(__name__)


def admin_required(func):
    """Decorator to enforce admin-only access on commands."""

    @wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        user = update.effective_user
        if not user:
            return

        if not ADMIN_CHAT_ID:
            msg = (
                "⚠️ <b>CHƯA CẤU HÌNH ADMIN_CHAT_ID!</b>\n\n"
                f"ID Telegram của bạn là: <code>{user.id}</code>\n\n"
                "Để sử dụng lệnh quản trị và bảo mật bot, vui lòng thêm dòng sau vào file <code>.env</code>:\n"
                f"<code>ADMIN_CHAT_ID={user.id}</code>\n\n"
                "Sau khi lưu, khởi động lại bot để kích hoạt bảo vệ an toàn."
            )
            if update.message:
                await update.message.reply_html(msg)
            return

        if not is_admin_user(user.id):
            logger.warning(
                "Unauthorized access to '%s' attempted by user %s (%s)",
                func.__name__,
                user.id,
                user.full_name,
            )
            if update.message:
                await update.message.reply_html(
                    "⛔ <b>TRUY CẬP BỊ TỪ CHỐI!</b>\n\n"
                    "Lệnh này chỉ dành riêng cho <b>Quản trị viên (Chủ shop)</b>.\n"
                    f"ID Telegram của bạn: <code>{user.id}</code>"
                )
            return

        return await func(update, context, *args, **kwargs)

    return wrapper


def get_language_selection_keyboard() -> InlineKeyboardMarkup:
    """Build inline keyboard for language selection."""
    keyboard = [
        [
            InlineKeyboardButton("🇻🇳 Tiếng Việt", callback_data="set_lang_vi"),
            InlineKeyboardButton("🇬🇧 English", callback_data="set_lang_en"),
        ]
    ]
    return InlineKeyboardMarkup(keyboard)


def get_main_menu_keyboard(lang: str = "vi") -> InlineKeyboardMarkup:
    """Build the main menu inline keyboard localized to user's language."""
    keyboard = [
        [
            InlineKeyboardButton(t("btn_products", lang), callback_data="menu_products"),
        ],
        [
            InlineKeyboardButton(t("btn_cart", lang), callback_data="menu_cart"),
            InlineKeyboardButton(t("btn_support", lang), callback_data="menu_support"),
        ],
        [
            InlineKeyboardButton(t("btn_change_lang", lang), callback_data="menu_change_lang"),
        ],
    ]
    return InlineKeyboardMarkup(keyboard)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle the `/start` command.

    Checks user language preference:
    - If not chosen yet: prompts to choose Vietnamese vs English.
    - If chosen: presents the localized interactive menu.
    """
    telegram_user = update.effective_user
    if not telegram_user:
        return

    full_name = telegram_user.full_name or telegram_user.first_name or "Khách hàng"

    # Save / Update user record in database
    with get_db() as db:
        user = OrderService.get_or_create_user(
            db=db,
            telegram_id=telegram_user.id,
            full_name=full_name,
        )
        lang_set = is_user_lang_set(db, telegram_user.id)
        current_lang = get_user_lang(db, telegram_user.id)

    # 1. Prompt language selection on first visit
    if not lang_set:
        prompt_text = t("choose_lang_title")
        keyboard = get_language_selection_keyboard()
        if update.message:
            await update.message.reply_html(
                text=prompt_text,
                reply_markup=keyboard,
            )
        elif update.callback_query:
            try:
                await update.callback_query.answer()
            except Exception:
                pass
            try:
                await update.callback_query.edit_message_text(
                    text=prompt_text,
                    reply_markup=keyboard,
                    parse_mode="HTML",
                )
            except Exception:
                try:
                    await update.callback_query.message.delete()
                except Exception:
                    pass
                if update.effective_chat:
                    await context.bot.send_message(
                        chat_id=update.effective_chat.id,
                        text=prompt_text,
                        reply_markup=keyboard,
                        parse_mode="HTML",
                    )
        return

    # 2. Main menu localized in user's saved language
    welcome_message = t("welcome", current_lang, name=full_name)
    menu_keyboard = get_main_menu_keyboard(current_lang)

    if update.message:
        await update.message.reply_html(
            text=welcome_message,
            reply_markup=menu_keyboard,
        )
    elif update.callback_query:
        try:
            await update.callback_query.answer()
        except Exception:
            pass
        try:
            await update.callback_query.edit_message_text(
                text=welcome_message,
                reply_markup=menu_keyboard,
                parse_mode="HTML",
            )
        except Exception:
            try:
                await update.callback_query.message.delete()
            except Exception:
                pass
            if update.effective_chat:
                await context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    text=welcome_message,
                    reply_markup=menu_keyboard,
                    parse_mode="HTML",
                )


async def menu_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle callback queries from the main menu buttons."""
    query = update.callback_query
    if not query or not query.data:
        return

    try:
        await query.answer()
    except Exception:
        pass

    data = query.data
    user_id = update.effective_user.id if update.effective_user else 0

    # 1. Language switching callbacks
    if data in ("set_lang_vi", "set_lang_en", "menu_lang_vi", "menu_lang_en"):
        new_lang = "vi" if "vi" in data else "en"
        with get_db() as db:
            set_user_lang(db, user_id, new_lang)
        await start(update, context)
        return

    if data == "menu_change_lang":
        prompt_text = t("choose_lang_title")
        keyboard = get_language_selection_keyboard()
        try:
            await query.edit_message_text(
                text=prompt_text,
                reply_markup=keyboard,
                parse_mode="HTML",
            )
        except Exception:
            try:
                await query.message.delete()
            except Exception:
                pass
            if update.effective_chat:
                await context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    text=prompt_text,
                    reply_markup=keyboard,
                    parse_mode="HTML",
                )
        return

    # Fetch current user language
    with get_db() as db:
        lang = get_user_lang(db, user_id)

    # 2. Product Catalog
    if data == "menu_products":
        with get_db() as db:
            products = OrderService.get_active_products(db)

        if not products:
            text = t("catalog_empty", lang)
            keyboard = [[InlineKeyboardButton(t("btn_back", lang), callback_data="menu_back")]]
        else:
            text = t("catalog_title", lang)
            keyboard_buttons = []
            for p in products:
                stats = AccountService.get_product_stats(db, p.id)
                warranty_badge = (
                    t("warranty_full_badge", lang)
                    if "bảo hành full" in p.name.lower() or "full" in p.name.lower()
                    else t("warranty_none_badge", lang)
                )
                text += (
                    f"🔹 <b>{p.name}</b>\n"
                    f"   • {t('price_label', lang)}: <b>{p.price:,.0f} VND</b>\n"
                    f"   • {t('policy_label', lang)}: <i>{warranty_badge}</i>\n"
                    f"   • 📦 {t('stock_label', lang)}: <b>{stats['available_stock']}</b> | 🔥 {t('sold_label', lang)}: <b>{stats['sold_count']}</b>\n\n"
                )
                keyboard_buttons.append(
                    [
                        InlineKeyboardButton(
                            t("btn_buy_pkg", lang, name=p.name, price=f"{p.price:,.0f}"),
                            callback_data=f"buy_prod_{p.id}",
                        )
                    ]
                )
            text += t("select_pkg_prompt", lang)
            keyboard_buttons.append([InlineKeyboardButton(t("btn_back", lang), callback_data="menu_back")])
            keyboard = keyboard_buttons

        banner_path = get_store_banner()
        if banner_path and banner_path.is_file():
            try:
                await query.message.delete()
            except Exception:
                pass
            with open(banner_path, "rb") as photo_file:
                await context.bot.send_photo(
                    chat_id=query.message.chat_id,
                    photo=photo_file,
                    caption=text,
                    reply_markup=InlineKeyboardMarkup(keyboard),
                    parse_mode="HTML",
                )
        else:
            await query.edit_message_text(
                text=text,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="HTML",
            )

    # 3. Orders history
    elif data == "menu_cart":
        with get_db() as db:
            orders = OrderService.get_user_orders(db, user_id)

        if not orders:
            text = t("no_orders", lang)
        else:
            text = t("order_history_title", lang)
            for order in orders[:5]:
                status_icon = (
                    "⏳" if order.status == "PENDING"
                    else ("⌛" if order.status == "WAITING_CONFIRMATION" else "✅")
                )
                date_str = order.created_at.strftime("%d/%m/%Y %H:%M") if order.created_at else ""
                status_localized = t(f"status_{order.status.lower()}", lang)

                pkg_names = [
                    f"{it.product.name if it.product else 'ChatGPT Plus'} (x{it.quantity})"
                    for it in order.items
                ]
                item_names = ", ".join(pkg_names) if pkg_names else "ChatGPT Plus"

                text += (
                    f"{status_icon} <b>{t('order_item_header', lang)} #{order.id}</b> - {date_str}\n"
                    f"   • {t('order_pkg_label', lang)}: <b>{item_names}</b>\n"
                    f"   • {t('order_total', lang)}: <b>{order.total_amount:,.0f} VND</b>\n"
                    f"   • {t('order_status_label', lang)}: <i>{status_localized}</i>\n"
                )

                if order.delivered_accounts:
                    text += f"   {t('order_delivered_accounts_title', lang)}\n"
                    for idx, acc in enumerate(order.delivered_accounts, start=1):
                        prefix = f"     [#{idx}] " if len(order.delivered_accounts) > 1 else "     • "
                        text += f"{prefix}📧 {t('order_account_email_label', lang)}: <code>{acc.account}</code>\n"
                        if acc.password:
                            text += f"       🔑 {t('order_account_pwd_label', lang)}: <code>{acc.password}</code>\n"
                        if acc.two_factor:
                            text += f"       🔐 {t('order_account_2fa_label', lang)}: <code>{acc.two_factor}</code>\n"
                    text += f"   {t('order_account_copy_hint', lang)}\n"
                elif order.status == "PAID":
                    text += f"   {t('order_account_awaiting', lang)}\n"

                text += "\n"

        keyboard = [
            [InlineKeyboardButton(t("btn_products", lang), callback_data="menu_products")],
            [InlineKeyboardButton(t("btn_back", lang), callback_data="menu_back")],
        ]
        try:
            await query.edit_message_text(
                text=text,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="HTML",
            )
        except Exception:
            try:
                await query.message.delete()
            except Exception:
                pass
            if update.effective_chat:
                await context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    text=text,
                    reply_markup=InlineKeyboardMarkup(keyboard),
                    parse_mode="HTML",
                )

    # 4. Support and Warranty
    elif data == "menu_support":
        text = t("support_text", lang)
        keyboard = [[InlineKeyboardButton(t("btn_back", lang), callback_data="menu_back")]]
        try:
            await query.edit_message_text(
                text=text,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="HTML",
            )
        except Exception:
            try:
                await query.message.delete()
            except Exception:
                pass
            if update.effective_chat:
                await context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    text=text,
                    reply_markup=InlineKeyboardMarkup(keyboard),
                    parse_mode="HTML",
                )

    # 5. Back to Main Menu
    elif data == "menu_back":
        await start(update, context)


@admin_required
async def test_pay_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Test command to simulate a successful payment: /test_pay <order_id> (Admin only)."""
    if not update.message:
        return

    if not context.args:
        await update.message.reply_text("Cách dùng: /test_pay <mã_đơn> (Ví dụ: /test_pay 1)")
        return

    try:
        order_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("Mã đơn hàng phải là số. Ví dụ: /test_pay 1")
        return

    with get_db() as db:
        order = OrderService.get_order_by_id(db, order_id)
        if not order:
            await update.message.reply_text(f"Không tìm thấy đơn hàng #{order_id}.")
            return

        success, msg, ord_obj, delivery_text = PaymentService.process_payment(
            db=db,
            order_id=order_id,
            transfer_amount=order.total_amount,
            transaction_ref="MANUAL_TEST_TG",
            gateway="TelegramTest",
        )
        if success and delivery_text:
            await update.message.reply_html(delivery_text, disable_web_page_preview=True)
        else:
            await update.message.reply_text(f"Kết quả xử lý: {msg}")


@admin_required
async def stock_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Admin command to check digital inventory: /stock (Admin only)."""
    if not update.message:
        return

    with get_db() as db:
        summary = AccountService.get_stock_summary(db)

    total_avail = sum(item["available_stock"] for item in summary)
    total_sold = sum(item["sold_count"] for item in summary)
    total_rev = sum(item["revenue"] for item in summary)

    lines = [
        "📊 <b>BÁO CÁO THỐNG KÊ KHO & DOANH SỐ (ADMIN):</b>\n",
        f"• 📦 <b>Tổng tồn kho sẵn sàng:</b> <b>{total_avail}</b> tài khoản",
        f"• 🔥 <b>Tổng số lượng đã bán:</b> <b>{total_sold}</b> tài khoản",
        f"• 💵 <b>Doanh thu ước tính:</b> <b>{total_rev:,.0f} VND</b>\n",
        "━━━━━━━━━━━━━━━━━━━━━━",
    ]
    for item in summary:
        status_str = "🟢 Đang mở bán" if item["is_active"] else "🔴 Tạm dừng"
        lines.append(
            f"🔹 <b>[{item['product_id']}] {item['product_name']}</b>\n"
            f"   • Giá niêm yết: <b>{item['price']:,.0f} VND</b>\n"
            f"   • 📦 Tồn kho sẵn sàng: <b>{item['available_stock']}</b> tài khoản\n"
            f"   • 🔥 Đã bán thành công: <b>{item['sold_count']}</b> tài khoản\n"
            f"   • 💰 Doanh thu gói: <b>{item['revenue']:,.0f} VND</b>\n"
            f"   • Trạng thái: {status_str}\n"
        )
    lines.append("<i>Dùng lệnh <code>/addstock &lt;id&gt; &lt;email|pass|2fa&gt;</code> để nạp thêm tài khoản.</i>")

    await update.message.reply_html("\n".join(lines))


@admin_required
async def add_stock_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Admin command to add accounts into database (Admin only):
    Usage:
    /addstock <product_id> <email | pass | 2fa>
    or reply to a message containing list of accounts.
    """
    if not update.message:
        return

    text = update.message.text or ""
    parts = text.split(maxsplit=2)
    if len(parts) < 3:
        usage = (
            "⚠️ <b>CÁCH DÙNG LỆNH /addstock:</b>\n\n"
            "<code>/addstock &lt;ID_Sản_phẩm&gt; &lt;email | pass | 2fa&gt;</code>\n\n"
            "<b>Ví dụ:</b>\n"
            "<code>/addstock 1 biradarguru37@googlemail.com | CHATLGBT9999 | E6M7ATQ7QHEALOH7BU2RN6YZRQNBMBE6</code>\n\n"
            "<i>(Có thể dán nhiều dòng cùng lúc sau mã ID)</i>"
        )
        await update.message.reply_html(usage)
        return

    try:
        product_id = int(parts[1])
    except ValueError:
        await update.message.reply_text("ID sản phẩm phải là số nguyên (ví dụ: 1 hoặc 2).")
        return

    accounts_content = parts[2].strip()

    with get_db() as db:
        try:
            result = AccountService.add_accounts_bulk(
                db=db,
                product_id=product_id,
                text_content=accounts_content,
            )
            report = (
                f"✅ <b>NẠP HÀNG THÀNH CÔNG!</b>\n\n"
                f"• Sản phẩm ID: <b>#{product_id}</b>\n"
                f"• Đã thêm mới: <b>{result['added']}</b> tài khoản\n"
                f"• Bỏ qua (trùng lặp): <b>{result['duplicates']}</b>\n"
                f"• Tồn kho hiện tại: <b>{result['new_stock']}</b> tài khoản\n"
            )
            if result["errors"]:
                report += f"• ⚠️ Bỏ qua {len(result['errors'])} dòng lỗi định dạng:\n"
                for err in result["errors"][:5]:
                    report += f"   - {err}\n"
            await update.message.reply_html(report)

            # Broadcast announcement to all users who have ever chatted with the bot
            if result.get("added", 0) > 0:
                asyncio.create_task(
                    BroadcastService.broadcast_restock(
                        bot=context.bot,
                        product_id=product_id,
                        added_count=result["added"],
                        admin_chat_id=update.effective_chat.id if update.effective_chat else None,
                    )
                )
        except Exception as exc:
            await update.message.reply_text(f"❌ Lỗi khi nạp hàng: {exc}")


async def myid_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Command to view caller's Telegram Chat ID: /myid"""
    user = update.effective_user
    if not user or not update.message:
        return

    # Ensure user is recorded in database
    with get_db() as db:
        OrderService.get_or_create_user(
            db=db,
            telegram_id=user.id,
            full_name=user.full_name or user.first_name,
        )

    is_adm = is_admin_user(user.id)
    badge = "👑 <b>Quản trị viên (Admin)</b>" if is_adm else "👤 <b>Khách hàng (User)</b>"

    msg = (
        f"🆔 <b>THÔNG TIN TÀI KHOẢN TELEGRAM CỦA BẠN:</b>\n\n"
        f"• Telegram ID: <code>{user.id}</code>\n"
        f"• Họ tên: <b>{user.full_name}</b>\n"
        f"• Username: @{user.username or 'Không có'}\n"
        f"• Quyền hạn: {badge}\n"
    )

    if not is_adm:
        msg += (
            f"\n💡 <i>Nếu bạn là chủ shop, hãy sao chép ID <code>{user.id}</code> và thêm vào file <code>.env</code>:\n"
            f"<code>ADMIN_CHAT_ID={user.id}</code></i>"
        )

    await update.message.reply_html(msg)
