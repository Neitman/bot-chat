"""Start handler module.

Handles the `/start` command and navigation menu for ChatGPT Plus Shop Bot.
"""

import asyncio
from functools import wraps
import logging
from telegram import (
    BotCommand,
    BotCommandScopeChat,
    BotCommandScopeDefault,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    MenuButtonCommands,
    ReplyKeyboardMarkup,
    Update,
)
from datetime import datetime
import time
from telegram.ext import Application, ContextTypes

from config import ADMIN_CHAT_ID, PATO_NETFLIX_PRODUCT_ID, get_admin_ids, get_store_banner, is_admin_user
from database.database import get_db
from database.models import Order, ProductAccount
from services.account_service import AccountService
from services.broadcast_service import BroadcastService
from services.i18n import get_user_lang, is_user_lang_set, set_user_lang, t
from services.order_service import OrderService
from services.pato_service import PatoAPIError, PatoService
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
                f"<code>ADMIN_CHAT_ID_1={user.id}</code> (hoặc <code>ADMIN_CHAT_ID_2=...</code>)\n\n"
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
            InlineKeyboardButton(t("btn_netflix_guide", lang), callback_data="menu_guide_netflix"),
            InlineKeyboardButton(t("btn_change_lang", lang), callback_data="menu_change_lang"),
        ],
    ]
    return InlineKeyboardMarkup(keyboard)


def get_main_reply_keyboard(lang: str = "vi", is_admin: bool = False) -> ReplyKeyboardMarkup:
    """Build persistent custom keyboard with quick action buttons.

    Replaces commands with intuitive tap-to-run buttons docked at the bottom of the chat.
    """
    keyboard = [
        [
            KeyboardButton(t("kb_products", lang)),
            KeyboardButton(t("kb_orders", lang)),
        ],
        [
            KeyboardButton(t("kb_support", lang)),
            KeyboardButton(t("kb_netflix_guide", lang)),
        ],
        [
            KeyboardButton(t("kb_change_lang", lang)),
            KeyboardButton(t("kb_myid", lang)),
        ],
    ]
    if is_admin:
        keyboard.append([
            KeyboardButton(t("kb_stock", lang)),
            KeyboardButton(t("kb_addstock", lang)),
        ])
    return ReplyKeyboardMarkup(
        keyboard=keyboard,
        resize_keyboard=True,
        is_persistent=True,
        input_field_placeholder=t("kb_placeholder", lang),
    )


async def show_products_catalog(
    update: Update, context: ContextTypes.DEFAULT_TYPE, lang: str | None = None
) -> None:
    """Display product catalog with images, stock details, and instant purchase buttons."""
    user = update.effective_user
    user_id = user.id if user else 0
    if not lang:
        with get_db() as db:
            lang = get_user_lang(db, user_id)

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
                p_lower = p.name.lower()
                if "bảo hành full" in p_lower or "full" in p_lower:
                    warranty_badge = t("warranty_full_badge", lang)
                elif "offer" in p_lower or "trial" in p_lower:
                    warranty_badge = t("warranty_offer_badge", lang)
                elif "gmail" in p_lower:
                    warranty_badge = t("warranty_gmail_badge", lang)
                elif "netflix" in p_lower:
                    warranty_badge = t("warranty_netflix_badge", lang)
                else:
                    warranty_badge = t("warranty_none_badge", lang)
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
    query = update.callback_query
    if query:
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
    elif update.message:
        if banner_path and banner_path.is_file():
            with open(banner_path, "rb") as photo_file:
                await update.message.reply_photo(
                    photo=photo_file,
                    caption=text,
                    reply_markup=InlineKeyboardMarkup(keyboard),
                    parse_mode="HTML",
                )
        else:
            await update.message.reply_html(
                text=text,
                reply_markup=InlineKeyboardMarkup(keyboard),
            )


async def show_order_history(
    update: Update, context: ContextTypes.DEFAULT_TYPE, lang: str | None = None
) -> None:
    """Display user order history and delivered accounts."""
    user = update.effective_user
    user_id = user.id if user else 0
    if not lang:
        with get_db() as db:
            lang = get_user_lang(db, user_id)

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
                f"{it.product.name if it.product else 'Sản phẩm'} (x{it.quantity})"
                for it in order.items
            ]
            item_names = ", ".join(pkg_names) if pkg_names else "Sản phẩm"

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
                    if (acc.account or "").startswith(("http://", "https://")):
                        link_label = "🔗 Link" if lang == "vi" else "🔗 Link"
                        text += f"{prefix}{link_label}: <a href=\"{acc.account}\">{acc.account}</a>\n"
                        copy_label = "📋 Sao chép link" if lang == "vi" else "📋 Copy link"
                        text += f"       {copy_label}: <code>{acc.account}</code>\n"
                    else:
                        text += f"{prefix}📧 {t('order_account_email_label', lang)}: <code>{acc.account}</code>\n"
                        if acc.password:
                            text += f"       🔑 {t('order_account_pwd_label', lang)}: <code>{acc.password}</code>\n"
                        if acc.two_factor:
                            text += f"       🔐 {t('order_account_2fa_label', lang)}: <code>{acc.two_factor}</code>\n"

                        # Dòng copy nhanh định dạng: tài khoản | mật khẩu | 2FA
                        copy_parts = [acc.account or ""]
                        if acc.password:
                            copy_parts.append(acc.password)
                        if acc.two_factor:
                            copy_parts.append(acc.two_factor)
                        copy_string = " | ".join(copy_parts)
                        copy_label = "📋 Sao chép nhanh" if lang == "vi" else "📋 Quick copy"
                        text += f"       {copy_label}: <code>{copy_string}</code>\n"
                text += f"   {t('order_account_copy_hint', lang)}\n"
            elif order.status == "PAID":
                text += f"   {t('order_account_awaiting', lang)}\n"

            text += "\n"

    keyboard = [
        [InlineKeyboardButton(t("btn_products", lang), callback_data="menu_products")],
        [InlineKeyboardButton(t("btn_back", lang), callback_data="menu_back")],
    ]
    query = update.callback_query
    if query:
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
    elif update.message:
        await update.message.reply_html(
            text=text,
            reply_markup=InlineKeyboardMarkup(keyboard),
        )


async def show_support_info(
    update: Update, context: ContextTypes.DEFAULT_TYPE, lang: str | None = None
) -> None:
    """Display customer support and warranty information."""
    user = update.effective_user
    user_id = user.id if user else 0
    if not lang:
        with get_db() as db:
            lang = get_user_lang(db, user_id)

    text = t("support_text", lang)
    keyboard = [
        [InlineKeyboardButton(t("btn_netflix_guide", lang), callback_data="menu_guide_netflix")],
        [InlineKeyboardButton(t("btn_back", lang), callback_data="menu_back")],
    ]
    query = update.callback_query
    if query:
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
    elif update.message:
        await update.message.reply_html(
            text=text,
            reply_markup=InlineKeyboardMarkup(keyboard),
        )


async def show_netflix_guide(
    update: Update, context: ContextTypes.DEFAULT_TYPE, lang: str | None = None
) -> None:
    """Display comprehensive Netflix login guide across iOS, Android, TV, PC."""
    user = update.effective_user
    user_id = user.id if user else 0
    if not lang:
        with get_db() as db:
            lang = get_user_lang(db, user_id)

    video_url = "https://drive.google.com/drive/folders/1gAfk6hDpbnPwIeuRtNvlgKA6aswtQv-D?usp=sharing"
    tv_url = "https://www.netflix.com/tv8"

    if lang == "en":
        text = (
            "🍿 <b>NETFLIX LOGIN INSTRUCTIONS & GUIDE</b>\n\n"
            "📱 <b>iOS Devices (iPhone / iPad):</b>\n"
            f"• 🎥 <b>Video Tutorial:</b> <a href=\"{video_url}\">Click here to watch</a>\n"
            "• <b>Step 1:</b> Sign out of Netflix on Safari and the Netflix app before starting. <i>(Note: Do NOT use Incognito / Private tabs during the entire process)</i>.\n"
            "• <b>Step 2:</b> Copy the Netflix link generated by the bot and paste it into Safari.\n"
            "• <b>Step 3:</b> After opening the link in Safari, tap <b>\"Open App\"</b> to redirect to the Netflix app.\n"
            "• <b>Step 4:</b> When the prompt appears on screen, tap <b>\"Continue\"</b>.\n"
            "• <b>Step 5:</b> Login successful.\n\n"
            "🤖 <b>Android Devices:</b>\n"
            f"• 🎥 <b>Video Tutorial:</b> <a href=\"{video_url}\">Click here to watch</a>\n"
            "• <b>Step 1:</b> Sign out of Netflix on your browser and the Netflix app. <i>(Note: Do NOT use Incognito tabs)</i>.\n"
            "• <b>Step 2:</b> Copy the Netflix link from the bot and paste it into Google Chrome or open directly.\n"
            "• <b>Step 3:</b> After opening the link, tap <b>\"Open App\"</b>.\n"
            "• <b>Step 4:</b> Login successful.\n\n"
            "📺 <b>On Smart TV:</b>\n"
            "• <b>Step 1:</b> Open Netflix on your TV to get the login code.\n"
            "• <b>Step 2:</b> Open the bot link on your phone or PC first.\n"
            f"• <b>Step 3:</b> Access <a href=\"{tv_url}\">{tv_url}</a> using the browser where you just opened the link.\n"
            "• <b>Step 4:</b> Enter the code shown on your TV <i>(or scan the QR code on TV for faster access)</i>.\n\n"
            "💻 <b>Laptop / PC:</b>\n"
            "• Simply open the bot link in your browser to log in immediately.\n\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "💬 <i>If you have any questions, please contact support: @Neitman275 or @Huyneko!</i>"
        )
    else:
        text = (
            "🍿 <b>HƯỚNG DẪN ĐĂNG NHẬP NETFLIX</b>\n\n"
            "📱 <b>Thiết bị iOS:</b>\n"
            f"Video hướng dẫn trên iOS: {video_url}\n"
            "Bước 1: Đăng xuất Netflix trên Safari và trên ứng dụng Netflix trước đó. Không dùng tab ẩn danh trong toàn bộ quá trình.\n"
            "Bước 2: Copy link Netflix bot vừa tạo thành công và dán vào Safari.\n"
            "Bước 3: Sau khi mở link Netflix trên Safari, bấm \"Open App\" để chuyển hướng sang ứng dụng Netflix.\n"
            "Bước 4: Khi màn hình thông báo hiện lên, bấm \"Tiếp tục\".\n"
            "Bước 5: Đăng nhập thành công.\n\n"
            "🤖 <b>Thiết bị Android:</b>\n"
            f"Video hướng dẫn trên Android: {video_url}\n"
            "Bước 1: Đăng xuất Netflix trên trình duyệt và trên ứng dụng Netflix trước đó. Không dùng tab ẩn danh trong toàn bộ quá trình.\n"
            "Bước 2: Copy link Netflix bot vừa tạo thành công và dán vào Google Chrome hoặc mở trực tiếp link.\n"
            "Bước 3: Sau khi mở link Netflix, bấm \"Open App\".\n"
            "Bước 4: Đăng nhập thành công.\n\n"
            "📺 <b>Trên TV:</b>\n"
            "Bước 1: Mở Netflix trên TV để lấy mã đăng nhập.\n"
            "Bước 2: Mở link bot vừa tạo để đăng nhập trên điện thoại hoặc máy tính.\n"
            f"Bước 3: Truy cập {tv_url} bằng trình duyệt vừa mở link.\n"
            "Bước 4: Nhập mã đang hiển thị trên TV. Hoặc bạn có thể quét mã trên TV để truy cập nhanh hơn.\n\n"
            "💻 <b>Thiết bị laptop, PC:</b>\n"
            "Chỉ cần mở link của bot là đăng nhập thành công.\n\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "💬 <i>Bất kỳ thắc mắc nào, vui lòng vào biểu tượng Hỗ trợ và liên hệ mình.</i>"
        )

    keyboard = [
        [InlineKeyboardButton("🎥 Video hướng dẫn (Drive)", url=video_url)],
        [InlineKeyboardButton("🌐 Link nhập mã TV (tv8)", url=tv_url)],
        [InlineKeyboardButton(t("btn_support", lang), callback_data="menu_support")],
        [InlineKeyboardButton(t("btn_back", lang), callback_data="menu_back")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    query = update.callback_query
    if query:
        try:
            await query.edit_message_text(
                text=text,
                reply_markup=reply_markup,
                parse_mode="HTML",
                disable_web_page_preview=True,
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
                    reply_markup=reply_markup,
                    parse_mode="HTML",
                    disable_web_page_preview=True,
                )
    elif update.message:
        await update.message.reply_html(
            text=text,
            reply_markup=reply_markup,
            disable_web_page_preview=True,
        )


async def netflix_guide_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Command to view Netflix login guide directly: /netflix or /hd_netflix"""
    await show_netflix_guide(update, context)


async def show_language_selection(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Display interactive language picker."""
    prompt_text = t("choose_lang_title")
    keyboard = get_language_selection_keyboard()
    query = update.callback_query
    if query:
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
    elif update.message:
        await update.message.reply_html(
            text=prompt_text,
            reply_markup=keyboard,
        )


async def products_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Command to view product catalog: /products, /shop"""
    await show_products_catalog(update, context)


async def orders_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Command to view order history: /orders, /cart"""
    await show_order_history(update, context)


async def support_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Command to view customer support: /support, /help"""
    await show_support_info(update, context)


async def language_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Command to change user language: /language, /lang"""
    await show_language_selection(update, context)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle the `/start` command.

    Checks user language preference:
    - If not chosen yet: prompts to choose Vietnamese vs English.
    - If chosen: presents the localized interactive menu and sets persistent reply keyboard.
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

    is_adm = is_admin_user(telegram_user.id)
    reply_kb = get_main_reply_keyboard(current_lang, is_admin=is_adm)

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
        banner_path = get_store_banner()
        if banner_path and banner_path.is_file():
            with open(banner_path, "rb") as photo_file:
                await update.message.reply_photo(
                    photo=photo_file,
                    caption=welcome_message,
                    reply_markup=menu_keyboard,
                    parse_mode="HTML",
                )
        else:
            await update.message.reply_html(
                text=welcome_message,
                reply_markup=menu_keyboard,
            )
        # Activate persistent reply keyboard at the bottom of the screen
        await update.message.reply_html(
            text=t("kb_menu_hint", current_lang),
            reply_markup=reply_kb,
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
        is_adm = is_admin_user(user_id)
        reply_kb = get_main_reply_keyboard(new_lang, is_admin=is_adm)
        if update.effective_chat:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=t("kb_menu_hint", new_lang),
                reply_markup=reply_kb,
                parse_mode="HTML",
            )
        await start(update, context)
        return

    if data == "menu_change_lang":
        await show_language_selection(update, context)
        return

    # Fetch current user language
    with get_db() as db:
        lang = get_user_lang(db, user_id)

    # 2. Product Catalog
    if data == "menu_products":
        await show_products_catalog(update, context, lang)

    # 3. Orders history
    elif data == "menu_cart":
        await show_order_history(update, context, lang)

    # 4. Support and Warranty
    elif data == "menu_support":
        await show_support_info(update, context, lang)

    # 5. Netflix Login Guide
    elif data in ("menu_guide_netflix", "guide_netflix"):
        await show_netflix_guide(update, context, lang)

    # 6. Pato 1-Hour Warranty Renewal for Netflix Orders
    elif data.startswith("pato_warranty_"):
        await handle_pato_warranty_callback(update, context, data)

    # 7. Back to Main Menu
    elif data == "menu_back":
        await start(update, context)


async def handle_pato_warranty_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, data: str) -> None:
    """Handle automated 1-hour warranty link renewal for Netflix orders via Pato API."""
    query = update.callback_query
    if not query:
        return

    try:
        await query.answer("Đang gửi yêu cầu bảo hành tới hệ thống Pato...", show_alert=False)
    except Exception:
        pass

    try:
        order_id = int(data.replace("pato_warranty_", "").strip())
    except ValueError:
        return

    user = update.effective_user
    user_id = user.id if user else 0

    with get_db() as db:
        order = db.query(Order).filter(Order.id == order_id).first()
        if not order or not order.user or (order.user.telegram_id != user_id and not is_admin_user(user_id)):
            await query.message.reply_html("⚠️ Không tìm thấy đơn hàng hoặc bạn không có quyền bảo hành đơn này.")
            return

        # Check warranty validity: 3600 seconds from order.created_at
        now = datetime.now(order.created_at.tzinfo) if order.created_at.tzinfo else datetime.now()
        elapsed_seconds = (now - order.created_at).total_seconds()
        if elapsed_seconds > 3600:
            mins_ago = int(elapsed_seconds // 60)
            await query.message.reply_html(
                f"⏱️ <b>HẾT HẠN BẢO HÀNH 1 GIỜ!</b>\n\n"
                f"Đơn hàng #{order_id} đã được tạo từ <b>{mins_ago} phút trước</b>.\n"
                f"Chính sách bảo hành tự động qua máy chủ Pato chỉ áp dụng trong vòng <b>60 phút (3.600 giây)</b> kể từ lúc đặt hàng ban đầu.\n\n"
                f"Nếu bạn cần hỗ trợ thêm, vui lòng liên hệ Admin: @Neitman275 hoặc @Huyneko!"
            )
            return

        # Find pato_order_id in delivered accounts
        pato_order_id = None
        for acc in order.delivered_accounts:
            if acc.note and "PATO:" in acc.note:
                pato_order_id = acc.note.split("PATO:")[-1].strip()
                break

        if not pato_order_id:
            await query.message.reply_html(
                "⚠️ Đơn hàng này không có mã phiên Pato hợp lệ để bảo hành tự động.\n"
                "Vui lòng gửi mã đơn #{order_id} cho Admin @Neitman275 hoặc @Huyneko!"
            )
            return

    # Call Pato Warranty API
    status_msg = await query.message.reply_html("⏳ <i>Hệ thống đang tiến hành kiểm tra cookie và cấp link Netflix mới, vui lòng đợi trong giây lát...</i>")
    try:
        warranty_req_id = f"WTY-{order_id}-{int(time.time())}"
        warranty_res = await PatoService.request_warranty(pato_order_id=pato_order_id, request_id=warranty_req_id)
        new_link = warranty_res.get("login_link")
        if not new_link:
            raise PatoAPIError("Máy chủ đối tác không trả về link mới hợp lệ.")

        with get_db() as db:
            acc = db.query(ProductAccount).filter(ProductAccount.order_id == order_id).order_by(ProductAccount.id.desc()).first()
            if acc:
                acc.account = new_link
                acc.raw_data = new_link
                db.commit()

        await status_msg.edit_text(
            f"🔄 <b>BẢO HÀNH THÀNH CÔNG CHO ĐƠN HÀNG #{order_id}!</b>\n\n"
            f"• 🔗 <b>Link đăng nhập mới:</b> <a href=\"{new_link}\">{new_link}</a>\n"
            f"• 📋 <b>Sao chép link:</b>\n<code>{new_link}</code>\n\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"⚠️ <b>LƯU Ý:</b>\n"
            f"• Vui lòng truy cập link trong vòng 15 phút.\n"
            f"• Thời hạn bảo hành tính từ lúc tạo đơn ban đầu (1 giờ).\n\n"
            f"Chúc bạn xem phim vui vẻ!",
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
    except Exception as exc:
        logger.error("Failed to request Pato warranty for order %s: %s", order_id, exc)
        await status_msg.edit_text(
            f"⚠️ <b>YÊU CẦU BẢO HÀNH THẤT BẠI:</b> {exc}\n\n"
            f"Vui lòng gửi mã đơn #{order_id} trực tiếp cho Admin @Neitman275 hoặc @Huyneko để được cấp link mới!",
            parse_mode="HTML",
        )



async def reply_keyboard_text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle text messages sent when clicking custom reply keyboard buttons."""
    if not update.message or not update.message.text:
        return
    text = update.message.text.strip()
    user = update.effective_user
    if not user:
        return

    with get_db() as db:
        lang = get_user_lang(db, user.id)

    # 1. Product Catalog
    if (
        text in (t("kb_products", "vi"), t("kb_products", "en"), t("btn_products", "vi"), t("btn_products", "en"))
        or "xem các gói ai" in text.lower()
        or "xem gói ai" in text.lower()
        or "view ai plans" in text.lower()
        or "gói ai" in text.lower()
        or "xem các gói" in text.lower()
        or "xem gói" in text.lower()
        or "xem sản phẩm" in text.lower()
        or "sản phẩm" in text.lower()
        or "danh mục sản phẩm" in text.lower()
        or "danh sách sản phẩm" in text.lower()
        or "gói sản phẩm" in text.lower()
        or "bảng giá" in text.lower()
        or "products" in text.lower()
    ):
        await show_products_catalog(update, context, lang)
        return

    # 2. Orders History
    if (
        text in (t("kb_orders", "vi"), t("kb_orders", "en"), t("btn_cart", "vi"), t("btn_cart", "en"))
        or "đơn hàng" in text.lower()
        or "my orders" in text.lower()
    ):
        await show_order_history(update, context, lang)
        return

    # 3. Support & Warranty
    if (
        text in (t("kb_support", "vi"), t("kb_support", "en"), t("btn_support", "vi"), t("btn_support", "en"))
        or "hỗ trợ" in text.lower()
        or "support" in text.lower()
    ):
        await show_support_info(update, context, lang)
        return

    # 4. Netflix Login Guide
    if (
        text in (
            t("kb_netflix_guide", "vi"),
            t("kb_netflix_guide", "en"),
            t("btn_netflix_guide", "vi"),
            t("btn_netflix_guide", "en"),
            "🍿 Hướng dẫn dùng Netflix",
            "🍿 Hướng dẫn đăng nhập Netflix",
            "🍿 Hướng dẫn Netflix",
            "🍿 HD Netflix",
            "📖 Hướng dẫn dùng Netflix",
        )
        or (
            "netflix" in text.lower()
            and any(k in text.lower() for k in ("hướng dẫn", "hd", "guide", "đăng nhập", "login", "dùng", "huong dan"))
        )
    ):
        await show_netflix_guide(update, context, lang)
        return

    # 5. Change Language
    if (
        text in (t("kb_change_lang", "vi"), t("kb_change_lang", "en"), t("btn_change_lang", "vi"), t("btn_change_lang", "en"))
        or "đổi ngôn ngữ" in text.lower()
        or "change language" in text.lower()
    ):
        await show_language_selection(update, context)
        return

    # 5. My ID
    if (
        text in (t("kb_myid", "vi"), t("kb_myid", "en"))
        or "id của tôi" in text.lower()
        or "my id" in text.lower()
    ):
        await myid_command(update, context)
        return

    # 6. Admin: Stock summary
    if (
        text in (t("kb_stock", "vi"), t("kb_stock", "en"))
        or "quản lý kho" in text.lower()
        or "stock report" in text.lower()
    ):
        await stock_command(update, context)
        return

    # 7. Admin: Add stock
    if (
        text in (t("kb_addstock", "vi"), t("kb_addstock", "en"))
        or "nạp hàng" in text.lower()
        or "add stock" in text.lower()
    ):
        await add_stock_command(update, context)
        return


async def setup_bot_commands(application: Application) -> None:
    """Register bot commands and menu button with Telegram API.

    This ensures that when a user types '/' in Telegram, an autocomplete list
    of commands with descriptions is immediately displayed.
    Also customizes command suggestions for admins and sets the persistent menu button.
    """
    bot = application.bot

    # 1. Customer commands (Default & Vietnamese)
    user_commands_vi = [
        BotCommand("start", "Khởi động bot & mở menu chính"),
        BotCommand("buy", "Xem danh mục sản phẩm & đặt mua"),
        BotCommand("orders", "Lịch sử đơn hàng & nhận tài khoản"),
        BotCommand("netflix", "Hướng dẫn đăng nhập Netflix"),
        BotCommand("support", "Trung tâm hỗ trợ & bảo hành"),
        BotCommand("language", "Thay đổi ngôn ngữ (Tiếng Việt / English)"),
        BotCommand("myid", "Xem ID Telegram của bạn"),
        BotCommand("cancel", "Hủy thao tác hiện tại"),
    ]

    # 2. Customer commands (English)
    user_commands_en = [
        BotCommand("start", "Start the bot & open main menu"),
        BotCommand("buy", "Browse products & purchase"),
        BotCommand("orders", "View order history & delivered accounts"),
        BotCommand("netflix", "Netflix login guide"),
        BotCommand("support", "Technical support & warranty"),
        BotCommand("language", "Change language (VI / EN)"),
        BotCommand("myid", "View your Telegram ID"),
        BotCommand("cancel", "Cancel current operation"),
    ]

    try:
        # Default scope (fallback)
        await bot.set_my_commands(commands=user_commands_vi, scope=BotCommandScopeDefault())
        # Vietnamese scope
        await bot.set_my_commands(commands=user_commands_vi, scope=BotCommandScopeDefault(), language_code="vi")
        # English scope
        await bot.set_my_commands(commands=user_commands_en, scope=BotCommandScopeDefault(), language_code="en")
        logger.info("Bot commands autocomplete registered for default, VI, and EN scopes.")
    except Exception as exc:
        logger.warning("Failed to register default bot commands: %s", exc)

    # 3. Admin commands (includes stock, addstock, test_pay)
    admin_commands_vi = [
        BotCommand("start", "Khởi động bot & mở menu chính"),
        BotCommand("buy", "Xem danh mục sản phẩm & đặt mua"),
        BotCommand("orders", "Lịch sử đơn hàng & nhận tài khoản"),
        BotCommand("netflix", "Hướng dẫn đăng nhập Netflix"),
        BotCommand("support", "Trung tâm hỗ trợ & bảo hành"),
        BotCommand("language", "Đổi ngôn ngữ"),
        BotCommand("myid", "Xem ID Telegram của bạn"),
        BotCommand("stock", "Báo cáo tồn kho & doanh thu (Admin)"),
        BotCommand("addstock", "Nạp tài khoản vào kho (Admin)"),
        BotCommand("test_pay", "Giả lập duyệt thanh toán test (Admin)"),
        BotCommand("cancel", "Hủy thao tác hiện tại"),
    ]

    admin_ids = get_admin_ids()
    for admin_id in admin_ids:
        try:
            await bot.set_my_commands(
                commands=admin_commands_vi,
                scope=BotCommandScopeChat(chat_id=admin_id),
            )
            logger.info("Admin commands registered for chat ID: %s", admin_id)
        except Exception as exc:
            logger.debug("Could not register admin commands for %s: %s", admin_id, exc)

    # 4. Set persistent Chat Menu Button to COMMANDS
    try:
        await bot.set_chat_menu_button(menu_button=MenuButtonCommands())
        logger.info("Telegram chat menu button configured to COMMANDS successfully.")
    except Exception as exc:
        logger.debug("Could not set chat menu button: %s", exc)


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
        if success and ord_obj:
            first_item = ord_obj.items[0] if ord_obj.items else None
            is_net = bool(
                first_item
                and first_item.product
                and (
                    "netflix" in first_item.product.name.lower()
                    or first_item.product_id == PATO_NETFLIX_PRODUCT_ID
                )
            )
            pato_order_id = None
            if is_net and PatoService.is_configured() and not ord_obj.delivered_accounts:
                try:
                    item_qty = first_item.quantity if first_item else 1
                    delivered_accs = []
                    last_pato_order_id = ""

                    for idx in range(item_qty):
                        pato_req_id = f"TEST-DH{ord_obj.id}-{int(time.time())}-{idx+1}"
                        pato_res = await PatoService.create_order(request_id=pato_req_id)
                        login_link = pato_res.get("login_link")
                        p_ord_id = pato_res.get("order_id", "")
                        if p_ord_id:
                            last_pato_order_id = p_ord_id

                        if login_link:
                            new_acc = ProductAccount(
                                product_id=first_item.product_id,
                                account=login_link,
                                raw_data=login_link,
                                status="SOLD",
                                order_id=ord_obj.id,
                                sold_at=datetime.now(),
                                note=f"PATO:{p_ord_id}",
                            )
                            db.add(new_acc)
                            delivered_accs.append(new_acc)

                    db.commit()
                    db.refresh(ord_obj)

                    if last_pato_order_id:
                        pato_order_id = last_pato_order_id

                    if delivered_accs:
                        delivery_text = PaymentService.generate_delivery_message(
                            ord_obj, first_item.product.name, delivered_accs
                        )
                except Exception as p_err:
                    logger.error("Failed to call Pato during test_pay: %s", p_err)
                    delivery_text = (delivery_text or "") + f"\n\n⚠️ Lỗi gọi Pato API: {p_err}"

            keyboard_buttons = []
            if is_net:
                keyboard_buttons.append([InlineKeyboardButton("🍿 Hướng dẫn đăng nhập Netflix", callback_data="menu_guide_netflix")])
                has_pato_warranty = any(
                    (acc.note and "PATO:" in acc.note) for acc in ord_obj.delivered_accounts
                ) or bool(pato_order_id)
                if has_pato_warranty:
                    keyboard_buttons.append([InlineKeyboardButton("🔄 Đổi link / Bảo hành (1 Giờ)", callback_data=f"pato_warranty_{ord_obj.id}")])

            reply_markup = InlineKeyboardMarkup(keyboard_buttons) if keyboard_buttons else None
            await update.message.reply_html(delivery_text, disable_web_page_preview=True, reply_markup=reply_markup)
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
            f"   • 💰 Doanh thu: <b>{item['revenue']:,.0f} VND</b>\n"
            f"   • Trạng thái: {status_str}\n"
        )
    if PatoService.is_configured():
        try:
            pato_data = await PatoService.get_quota()
            rem = pato_data.get("remaining_quota", 0)
            lt = pato_data.get("api_key_lifetime", {})
            rem_days = lt.get("remaining_days", "N/A")
            lines.append(
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"🌐 <b>PATO PARTNER API (NETFLIX):</b>\n"
                f"   • 📦 Quota còn lại: <b>{rem} link</b>\n"
                f"   • ⏳ Hạn dùng API Key: <b>{rem_days} ngày</b>"
            )
        except Exception as exc:
            lines.append(f"\n⚠️ <i>Không thể lấy thông tin Pato Quota: {exc}</i>")

    lines.append("━━━━━━━━━━━━━━━━━━━━━━")
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
            "<b>1. Nạp số lượng tồn kho (Netflix):</b>\n"
            "<code>/addstock 5 50</code> (Nạp thêm 50 vào kho)\n"
            "<code>/addstock 5 =50</code> (Đặt tồn kho về đúng 50)\n"
            "<code>/addstock 5 +20</code> hoặc <code>/addstock 5 -10</code>\n\n"
            "<b>2. Nạp danh sách tài khoản (ChatGPT / Gmail):</b>\n"
            "<code>/addstock 1 user@gmail.com | Pass123 | 2FA_KEY</code>\n"
            "<code>/addstock 4 user1@gmail.com | MatKhau123</code>\n\n"
            "<i>(Có thể dán nhiều dòng tài khoản cùng lúc sau mã ID)</i>"
        )
        await update.message.reply_html(usage)
        return

    try:
        product_id = int(parts[1])
    except ValueError:
        await update.message.reply_text("ID sản phẩm phải là số nguyên (ví dụ: 1, 2 hoặc 3).")
        return

    accounts_content = parts[2].strip()

    # Check if input is a pure integer quantity (e.g. /addstock 5 50 or /addstock 5 =50)
    qty_change = None
    set_exact = None
    if accounts_content.startswith("=") and accounts_content[1:].strip().isdigit():
        set_exact = int(accounts_content[1:].strip())
    elif accounts_content.isdigit():
        qty_change = int(accounts_content)
    elif accounts_content.startswith("+") and accounts_content[1:].isdigit():
        qty_change = int(accounts_content[1:])
    elif accounts_content.startswith("-") and accounts_content[1:].isdigit():
        qty_change = -int(accounts_content[1:])

    with get_db() as db:
        from database.models import Product
        target_prod = db.query(Product).filter(Product.id == product_id).first()
        if not target_prod:
            await update.message.reply_text(f"❌ Không tìm thấy sản phẩm #{product_id}.")
            return

        if set_exact is not None:
            old_qty = target_prod.stock_quantity
            target_prod.stock_quantity = max(0, set_exact)
            db.commit()

            report = (
                f"✅ <b>THIẾT LẬP TỒN KHO THÀNH CÔNG!</b>\n\n"
                f"• Sản phẩm: <b>#{target_prod.id} - {target_prod.name}</b>\n"
                f"• Tồn kho cũ: <b>{old_qty}</b>\n"
                f"• Tồn kho mới: <b>{target_prod.stock_quantity}</b> sản phẩm\n"
            )
            await update.message.reply_html(report)
            return

        if qty_change is not None:
            target_prod.stock_quantity = max(0, target_prod.stock_quantity + qty_change)
            db.commit()

            report = (
                f"✅ <b>NẠP SỐ LƯỢNG TỒN KHO THÀNH CÔNG!</b>\n\n"
                f"• Sản phẩm: <b>#{target_prod.id} - {target_prod.name}</b>\n"
                f"• Số lượng điều chỉnh: <b>{'+' if qty_change > 0 else ''}{qty_change}</b>\n"
                f"• Tồn kho hiện tại: <b>{target_prod.stock_quantity}</b> sản phẩm\n"
            )
            await update.message.reply_html(report)

            # Broadcast announcement if added > 0
            if qty_change > 0:
                asyncio.create_task(
                    BroadcastService.broadcast_restock(
                        bot=context.bot,
                        product_id=product_id,
                        added_count=qty_change,
                        admin_chat_id=update.effective_chat.id if update.effective_chat else None,
                    )
                )
            return

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
            f"<code>ADMIN_CHAT_ID_1={user.id}</code> (hoặc <code>ADMIN_CHAT_ID_2={user.id}</code>)</i>"
        )

    await update.message.reply_html(msg)
