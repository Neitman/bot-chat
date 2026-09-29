"""Checkout conversation handler module.

Implements a Finite State Machine (FSM) using python-telegram-bot's
ConversationHandler to guide customers smoothly through product selection,
quantity input, and final order confirmation with automated VietQR payment generation.
"""

import logging
import warnings
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ConversationHandler,
    ContextTypes,
    MessageHandler,
    filters,
)
from telegram.warnings import PTBUserWarning

# Filter PTB conversation warning for mixed callbacks and text handlers
warnings.filterwarnings("ignore", category=PTBUserWarning)

from database.database import get_db
from services.order_service import OrderService

logger = logging.getLogger(__name__)

# FSM States
SELECT_PRODUCT, ENTER_QUANTITY, CONFIRM_ORDER = range(3)


async def start_checkout(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Entry point for the checkout conversation.

    Triggered either by `/buy` command or clicking "👉 Mua [Sản phẩm]"
    from the product list inline buttons.
    """
    query = update.callback_query

    # If triggered via callback query (e.g. buy_prod_1)
    if query and query.data and query.data.startswith("buy_prod_"):
        await query.answer()
        product_id = int(query.data.split("_")[-1])

        with get_db() as db:
            product = OrderService.get_product_by_id(db, product_id)

        if not product or not product.is_active or product.stock_quantity <= 0:
            await query.edit_message_text(
                "Rất tiếc, sản phẩm này hiện đã hết hàng hoặc ngừng kinh doanh. "
                "Gõ /buy để chọn sản phẩm khác hoặc /start để về menu."
            )
            return ConversationHandler.END

        # Store product state in context.user_data
        context.user_data["checkout"] = {
            "product_id": product.id,
            "product_name": product.name,
            "price": product.price,
            "max_stock": product.stock_quantity,
        }

        await query.edit_message_text(
            f"Bạn đã chọn: <b>{product.name}</b>\n"
            f"💰 Giá đơn vị: <b>{product.price:,.0f} VND</b>\n"
            f"📦 Số lượng còn trong kho: <b>{product.stock_quantity}</b>\n\n"
            "Vui lòng nhập <b>số lượng</b> bạn muốn mua (hoặc gõ /cancel để hủy):",
            parse_mode="HTML",
        )
        return ENTER_QUANTITY

    # If triggered via /buy command: list available products
    with get_db() as db:
        products = OrderService.get_active_products(db)

    if not products:
        message = "Hiện tại không có sản phẩm nào trong kho. Vui lòng quay lại sau!"
        if update.message:
            await update.message.reply_text(message)
        return ConversationHandler.END

    keyboard = [
        [InlineKeyboardButton(f"{p.name} ({p.price:,.0f} VND)", callback_data=f"select_p_{p.id}")]
        for p in products
    ]
    keyboard.append([InlineKeyboardButton("❌ Hủy", callback_data="cancel_checkout")])

    text = "🛍️ <b>Vui lòng chọn sản phẩm bạn muốn đặt mua:</b>"
    if update.message:
        await update.message.reply_html(text=text, reply_markup=InlineKeyboardMarkup(keyboard))

    return SELECT_PRODUCT


async def select_product(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """State: SELECT_PRODUCT.

    Receives the product selection from the inline keyboard, validates
    availability, stores details into context.user_data, and asks for quantity.
    """
    query = update.callback_query
    if not query or not query.data:
        return SELECT_PRODUCT

    await query.answer()

    if query.data == "cancel_checkout":
        return await cancel_from_query(update, context)

    if not query.data.startswith("select_p_"):
        return SELECT_PRODUCT

    product_id = int(query.data.split("_")[-1])

    with get_db() as db:
        product = OrderService.get_product_by_id(db, product_id)

    if not product or not product.is_active or product.stock_quantity <= 0:
        await query.edit_message_text(
            "Sản phẩm này tạm thời hết hàng. Vui lòng chọn sản phẩm khác bằng lệnh /buy."
        )
        return ConversationHandler.END

    # Update state in user_data
    context.user_data["checkout"] = {
        "product_id": product.id,
        "product_name": product.name,
        "price": product.price,
        "max_stock": product.stock_quantity,
    }

    await query.edit_message_text(
        f"Bạn đã chọn: <b>{product.name}</b>\n"
        f"💰 Đơn giá: <b>{product.price:,.0f} VND</b>\n"
        f"📦 Kho còn: <b>{product.stock_quantity}</b>\n\n"
        "Vui lòng nhập <b>số lượng</b> bạn muốn mua (hoặc gõ /cancel để hủy):",
        parse_mode="HTML",
    )
    return ENTER_QUANTITY


async def enter_quantity(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """State: ENTER_QUANTITY.

    Validates numeric quantity, checks stock limits, computes total amount,
    and displays the order summary for customer confirmation.
    """
    if not update.message or not update.message.text:
        return ENTER_QUANTITY

    checkout_data = context.user_data.get("checkout")
    if not checkout_data:
        await update.message.reply_text("Phiên đặt hàng đã hết hạn. Vui lòng gõ /buy để thử lại.")
        return ConversationHandler.END

    text = update.message.text.strip()

    # Validate integer quantity
    try:
        quantity = int(text)
        if quantity <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text(
            "⚠️ Số lượng không hợp lệ! Vui lòng nhập một số nguyên dương lớn hơn 0 (ví dụ: 1, 2, 5):"
        )
        return ENTER_QUANTITY

    max_stock = checkout_data.get("max_stock", 0)
    if quantity > max_stock:
        await update.message.reply_text(
            f"⚠️ Số lượng bạn yêu cầu ({quantity}) vượt quá số lượng trong kho ({max_stock}).\n"
            f"Vui lòng nhập lại số lượng <= {max_stock}:"
        )
        return ENTER_QUANTITY

    # Update context with valid quantity and total amount
    price = checkout_data["price"]
    total_amount = price * quantity
    checkout_data["quantity"] = quantity
    checkout_data["total_amount"] = total_amount

    # Build confirmation UI
    confirm_keyboard = [
        [
            InlineKeyboardButton("✅ Xác nhận đặt hàng", callback_data="confirm_order_yes"),
            InlineKeyboardButton("❌ Hủy bỏ", callback_data="confirm_order_no"),
        ]
    ]

    summary_text = (
        "🧾 <b>XÁC NHẬN THÔNG TIN ĐƠN HÀNG:</b>\n\n"
        f"• Sản phẩm: <b>{checkout_data['product_name']}</b>\n"
        f"• Đơn giá: <b>{price:,.0f} VND</b>\n"
        f"• Số lượng: <b>{quantity}</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"💵 <b>Tổng thanh toán: {total_amount:,.0f} VND</b>\n\n"
        "Bạn có đồng ý tiến hành đặt đơn hàng này không?"
    )

    await update.message.reply_html(
        text=summary_text,
        reply_markup=InlineKeyboardMarkup(confirm_keyboard),
    )
    return CONFIRM_ORDER


async def confirm_order(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """State: CONFIRM_ORDER.

    Finalizes order creation in database, deducts inventory, generates
    an automatic VietQR payment code, and prompts the customer to scan and transfer.
    """
    query = update.callback_query
    if not query or not query.data:
        return CONFIRM_ORDER

    await query.answer()

    checkout_data = context.user_data.get("checkout")
    if not checkout_data:
        await query.edit_message_text(
            "Phiên đặt hàng không tìm thấy dữ liệu. Vui lòng bắt đầu lại bằng /buy."
        )
        return ConversationHandler.END

    if query.data == "confirm_order_no":
        context.user_data.pop("checkout", None)
        await query.edit_message_text("❌ Đã hủy đặt hàng. Gõ /start để quay lại trang chủ.")
        return ConversationHandler.END

    if query.data == "confirm_order_yes":
        telegram_user = update.effective_user
        chat_id = update.effective_chat.id if update.effective_chat else None
        if not telegram_user or not chat_id:
            return ConversationHandler.END

        try:
            with get_db() as db:
                order = OrderService.create_order(
                    db=db,
                    telegram_id=telegram_user.id,
                    product_id=checkout_data["product_id"],
                    quantity=checkout_data["quantity"],
                    full_name=telegram_user.full_name or telegram_user.first_name,
                )
                order_id = order.id
                total_amount = order.total_amount

            # Generate VietQR payment URL (compatible with all VN banking apps & MoMo)
            qr_url = OrderService.generate_vietqr_url(order_id=order_id, total_amount=total_amount)

            # Clear checkout session data
            context.user_data.pop("checkout", None)

            # Payment instruction buttons
            payment_keyboard = [
                [InlineKeyboardButton("✅ Tôi đã chuyển khoản xong", callback_data=f"paid_order_{order_id}")],
                [InlineKeyboardButton("🔙 Quay lại menu", callback_data="menu_back")],
            ]

            caption = (
                "🎉 <b>ĐẶT HÀNG THÀNH CÔNG!</b>\n\n"
                f"• Mã đơn hàng: <b>#{order_id}</b>\n"
                f"• Sản phẩm: <b>{checkout_data['product_name']}</b> x {checkout_data['quantity']}\n"
                f"• Tổng số tiền: <b>{total_amount:,.0f} VND</b>\n"
                f"• Trạng thái: <i>PENDING (Chờ thanh toán)</i>\n\n"
                "📲 <b>HƯỚNG DẪN THANH TOÁN QUA VIETQR:</b>\n"
                "1. Mở ứng dụng ngân hàng hoặc ví điện tử bất kỳ.\n"
                "2. Quét mã QR trên để tự động điền STK, số tiền và nội dung chuyển khoản.\n"
                "3. Sau khi chuyển xong, bấm nút <b>'Tôi đã chuyển khoản xong'</b> bên dưới để shop đối soát."
            )

            # Remove previous confirmation text message and send photo with QR
            try:
                await query.message.delete()
            except Exception:
                pass

            await context.bot.send_photo(
                chat_id=chat_id,
                photo=qr_url,
                caption=caption,
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(payment_keyboard),
            )

        except ValueError as err:
            logger.warning("Order validation failed: %s", err)
            await query.edit_message_text(f"⚠️ Không thể tạo đơn hàng: {err}")
        except Exception as exc:
            logger.error("Unexpected error during checkout: %s", exc)
            await query.edit_message_text("❌ Đã xảy ra lỗi hệ thống khi xử lý đơn hàng. Vui lòng thử lại sau.")

        return ConversationHandler.END

    return CONFIRM_ORDER


async def payment_confirm_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle the '✅ Tôi đã chuyển khoản xong' inline button."""
    query = update.callback_query
    if not query or not query.data:
        return

    await query.answer()
    order_id = int(query.data.split("_")[-1])

    with get_db() as db:
        OrderService.update_order_status(db, order_id=order_id, new_status="WAITING_CONFIRMATION")

    thank_you_text = (
        f"✅ <b>ĐÃ GHI NHẬN THÔNG BÁO THANH TOÁN CHO ĐƠN #{order_id}!</b>\n\n"
        "Cảm ơn bạn! Shop đã ghi nhận thông báo chuyển khoản của bạn.\n"
        "Nhân viên sẽ kiểm tra giao dịch và liên hệ giao hàng đến bạn trong thời gian sớm nhất.\n\n"
        "Gõ /start để tiếp tục mua sắm hoặc kiểm tra đơn hàng."
    )
    keyboard = [[InlineKeyboardButton("🔙 Về menu chính", callback_data="menu_back")]]

    if update.effective_chat:
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=thank_you_text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="HTML",
        )


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Fallback handler for `/cancel` command."""
    context.user_data.pop("checkout", None)
    if update.message:
        await update.message.reply_text(
            "🚫 Bạn đã hủy quy trình đặt hàng thành công.\nGõ /start để trở về menu chính hoặc /buy để mua hàng."
        )
    return ConversationHandler.END


async def cancel_from_query(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Cancel checkout triggered from an inline button."""
    context.user_data.pop("checkout", None)
    if update.callback_query:
        await update.callback_query.edit_message_text(
            "🚫 Bạn đã hủy quy trình đặt hàng.\nGõ /start để trở về menu chính hoặc /buy để mua lại."
        )
    return ConversationHandler.END


# Configure the ConversationHandler for checkout flow
checkout_conversation_handler = ConversationHandler(
    entry_points=[
        CommandHandler("buy", start_checkout),
        CommandHandler("checkout", start_checkout),
        CallbackQueryHandler(start_checkout, pattern=r"^buy_prod_\d+$"),
    ],
    states={
        SELECT_PRODUCT: [
            CallbackQueryHandler(select_product, pattern=r"^select_p_\d+$"),
            CallbackQueryHandler(cancel_from_query, pattern=r"^cancel_checkout$"),
        ],
        ENTER_QUANTITY: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, enter_quantity),
        ],
        CONFIRM_ORDER: [
            CallbackQueryHandler(confirm_order, pattern=r"^confirm_order_(yes|no)$"),
        ],
    },
    fallbacks=[
        CommandHandler("cancel", cancel),
    ],
    name="checkout_conversation",
    persistent=False,
    per_message=False,
)
