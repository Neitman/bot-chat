"""Start handler module.

Handles the `/start` command and navigation menu for ChatGPT Plus Shop Bot.
"""

from functools import wraps
import logging
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from config import ADMIN_CHAT_ID, is_admin_user
from database.database import get_db
from services.account_service import AccountService
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


def get_main_menu_keyboard() -> InlineKeyboardMarkup:
    """Build the main menu inline keyboard."""
    keyboard = [
        [
            InlineKeyboardButton("🤖 Xem các gói ChatGPT Plus", callback_data="menu_products"),
        ],
        [
            InlineKeyboardButton("🛒 Đơn hàng của tôi", callback_data="menu_cart"),
            InlineKeyboardButton("📞 Hỗ trợ & Bảo hành", callback_data="menu_support"),
        ],
    ]
    return InlineKeyboardMarkup(keyboard)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle the `/start` command.

    Greets the user, syncs their profile in the database, and presents
    the main interactive inline keyboard.
    """
    telegram_user = update.effective_user
    if not telegram_user:
        return

    full_name = telegram_user.full_name or telegram_user.first_name or "Khách hàng"

    # Save / Update user record in database
    try:
        with get_db() as db:
            OrderService.get_or_create_user(
                db=db,
                telegram_id=telegram_user.id,
                full_name=full_name,
            )
    except Exception as exc:
        logger.error("Error creating/updating user %s: %s", telegram_user.id, exc)

    welcome_message = (
        f"Xin chào, <b>{full_name}</b>! 👋\n\n"
        "Chào mừng bạn đến với <b>ChatGPT Plus Store</b> 🤖\n"
        "Chuyên cung cấp gói tài khoản / nâng cấp ChatGPT Plus uy tín, giá tốt nhất thị trường.\n\n"
        "Vui lòng chọn chức năng bên dưới để bắt đầu:"
    )

    if update.message:
        await update.message.reply_html(
            text=welcome_message,
            reply_markup=get_main_menu_keyboard(),
        )
    elif update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(
            text=welcome_message,
            reply_markup=get_main_menu_keyboard(),
            parse_mode="HTML",
        )


async def menu_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle callback queries from the main menu buttons."""
    query = update.callback_query
    if not query or not query.data:
        return

    await query.answer()
    data = query.data

    if data == "menu_products":
        with get_db() as db:
            products = OrderService.get_active_products(db)

        if not products:
            text = "Hiện tại cửa hàng đang cập nhật thêm tài khoản. Vui lòng quay lại sau!"
            keyboard = [[InlineKeyboardButton("🔙 Quay lại menu", callback_data="menu_back")]]
        else:
            text = (
                "🤖 <b>BẢNG GIÁ DỊCH VỤ:</b>\n\n"
                "• <b>🧪 [TEST] Gói Test Webhook (2.000đ)</b>: Dành cho test nạp tự động.\n"
                "• <b>Gói Bảo Hành Full (275.000đ)</b>: Bảo hành 1 đổi 1 trọn vẹn 30 ngày.\n"
                "• <b>Gói Không Bảo Hành (145.000đ)</b>: Giá siêu tiết kiệm, tài khoản dùng riêng.\n\n"
                "<i>Chọn gói bạn muốn mua bên dưới:</i>\n"
            )
            keyboard_buttons = []
            for p in products:
                keyboard_buttons.append(
                    [
                        InlineKeyboardButton(
                            f"👉 Mua {p.name} ({p.price:,.0f}đ)",
                            callback_data=f"buy_prod_{p.id}",
                        )
                    ]
                )
            keyboard_buttons.append([InlineKeyboardButton("🔙 Quay lại menu", callback_data="menu_back")])
            keyboard = keyboard_buttons

        await query.edit_message_text(
            text=text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="HTML",
        )

    elif data == "menu_cart":
        telegram_id = update.effective_user.id if update.effective_user else 0
        with get_db() as db:
            orders = OrderService.get_user_orders(db, telegram_id)

        if not orders:
            text = "🛒 Bạn chưa có đơn hàng nào.\nHãy chọn mua gói ChatGPT Plus để bắt đầu trải nghiệm nhé!"
        else:
            text = "📋 <b>LỊCH SỬ ĐƠN HÀNG CỦA BẠN:</b>\n\n"
            for order in orders[:5]:
                status_icon = "⏳" if order.status == "PENDING" else ("⌛" if order.status == "WAITING_CONFIRMATION" else "✅")
                date_str = order.created_at.strftime("%d/%m/%Y %H:%M") if order.created_at else ""
                text += (
                    f"{status_icon} <b>Đơn #{order.id}</b> - {date_str}\n"
                    f"   Tổng tiền: <b>{order.total_amount:,.0f} VND</b>\n"
                    f"   Trạng thái: <i>{order.status}</i>\n\n"
                )

        keyboard = [
            [InlineKeyboardButton("🤖 Mua gói ChatGPT Plus", callback_data="menu_products")],
            [InlineKeyboardButton("🔙 Quay lại menu", callback_data="menu_back")],
        ]
        await query.edit_message_text(
            text=text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="HTML",
        )

    elif data == "menu_support":
        text = (
            "📞 <b>TRUNG TÂM HỖ TRỢ & BẢO HÀNH</b>\n\n"
            "Nếu bạn cần hỗ trợ kích hoạt hoặc bảo hành tài khoản:\n"
            "• Hỗ trợ Telegram: <code>@AdminSupport</code>\n"
            "• Thời gian hỗ trợ: 08:00 - 23:00 hàng ngày\n"
            "• Cam kết hỗ trợ 1 đổi 1 nhanh chóng đối với gói Bảo hành full.\n"
        )
        keyboard = [[InlineKeyboardButton("🔙 Quay lại menu", callback_data="menu_back")]]
        await query.edit_message_text(
            text=text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="HTML",
        )

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

    lines = ["📦 <b>BÁO CÁO TỒN KHO TÀI KHOẢN (ADMIN ONLY):</b>\n"]
    for item in summary:
        status_str = "🟢 Đang bán" if item["is_active"] else "🔴 Tạm dừng"
        lines.append(
            f"🔹 <b>[{item['product_id']}] {item['product_name']}</b>\n"
            f"   • Giá: <b>{item['price']:,.0f} VND</b>\n"
            f"   • Sẵn sàng bán: <b>{item['available_stock']}</b> tài khoản\n"
            f"   • Đã bán: <b>{item['sold_count']}</b> tài khoản\n"
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
        except Exception as exc:
            await update.message.reply_text(f"❌ Lỗi khi nạp hàng: {exc}")


async def myid_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Command to view caller's Telegram Chat ID: /myid"""
    user = update.effective_user
    if not user or not update.message:
        return

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
