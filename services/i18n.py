"""Internationalization (i18n) module for Vietnamese and English bilingual support.

Manages user language preferences ('vi' / 'en'), localized strings,
and dynamic formatting across menus, checkout, and automated fulfillment.
"""

from typing import Optional
from sqlalchemy.orm import Session
from database.models import User

# Available languages
LANG_VI = "vi"
LANG_EN = "en"
DEFAULT_LANG = "vi"

STRINGS = {
    "vi": {
        # Language Selection
        "choose_lang_title": "👋 <b>Chào mừng bạn đến với AI Store!</b>\n\nVui lòng chọn ngôn ngữ để tiếp tục:\nPlease choose your language to continue:",
        "lang_vi_btn": "🇻🇳 Tiếng Việt",
        "lang_en_btn": "🇬🇧 English",
        "lang_switched": "✅ Đã chuyển sang <b>Tiếng Việt</b> thành công!",
        
        # Main Menu
        "welcome": (
            "Xin chào, <b>{name}</b>! 👋\n\n"
            "Chào mừng bạn đến với <b>AI Store</b> 🤖\n"
            "Chuyên cung cấp gói tài khoản AI uy tín, tự động giao hàng 24/7.\n\n"
            "Vui lòng chọn chức năng bên dưới để bắt đầu:"
        ),
        "btn_products": "🤖 Xem các gói AI",
        "btn_cart": "🛒 Đơn hàng của tôi",
        "btn_support": "📞 Hỗ trợ & Bảo hành",
        "btn_change_lang": "🌐 Đổi ngôn ngữ (Language)",
        "btn_back": "🔙 Quay lại menu",
        
        # Product Catalog
        "catalog_title": "🤖 <b>BẢNG GIÁ DỊCH VỤ AI:</b>\n\n",
        "price_label": "Đơn giá",
        "policy_label": "Chính sách",
        "stock_label": "Còn lại",
        "sold_label": "Đã bán",
        "warranty_full_badge": "🛡️ Bảo hành 1 đổi 1 trong 30 ngày",
        "warranty_none_badge": "⚡ Giá tiết kiệm, tài khoản dùng riêng",
        "select_pkg_prompt": "<i>Chọn gói bạn muốn mua bên dưới:</i>\n",
        "btn_buy_pkg": "👉 Mua {name} ({price}đ)",
        "catalog_empty": "Hiện tại cửa hàng đang cập nhật thêm tài khoản. Vui lòng quay lại sau!",
        
        # Support
        "support_text": (
            "📞 <b>TRUNG TÂM HỖ TRỢ & BẢO HÀNH</b>\n\n"
            "Nếu bạn cần hỗ trợ kích hoạt hoặc bảo hành tài khoản:\n"
            "• Hỗ trợ Telegram: <code>@Neitman275</code> hoặc <code>@Huyneko</code>\n"
            "• Thời gian hỗ trợ: 08:00 - 23:00 hàng ngày\n"
            "• Cam kết hỗ trợ 1 đổi 1 nhanh chóng đối với gói Bảo hành full."
        ),
        
        # Orders / Cart
        "no_orders": "🛒 Bạn chưa có đơn hàng nào.\nHãy chọn mua gói AI để bắt đầu trải nghiệm nhé!",
        "order_history_title": "📋 <b>LỊCH SỬ ĐƠN HÀNG CỦA BẠN:</b>\n\n",
        "order_item_header": "Đơn",
        "order_total": "Tổng tiền",
        "order_status_label": "Trạng thái",
        "status_pending": "Chờ thanh toán",
        "status_waiting": "Đang xác nhận",
        "status_paid": "Đã thanh toán",
        "status_cancelled": "Đã hủy",
        "order_pkg_label": "Gói",
        "order_delivered_accounts_title": "📦 <b>Tài khoản đã nhận:</b>",
        "order_account_email_label": "Email",
        "order_account_pwd_label": "Mật khẩu",
        "order_account_2fa_label": "2FA Secret",
        "order_account_awaiting": "⚠️ <i>Đang chờ cấp tài khoản (liên hệ @Neitman275 hoặc @Huyneko)</i>",
        "order_account_copy_hint": "<i>(Bấm vào từng thông tin để tự động sao chép)</i>",
        
        # Checkout FSM
        "checkout_selected": (
            "🛒 Bạn đã chọn: <b>{name}</b>\n"
            "💰 Giá đơn vị: <b>{price:,.0f} VND</b>\n"
            "📦 Tồn kho sẵn sàng: <b>{stock}</b> gói\n"
            "🔥 Đã bán: <b>{sold}</b> gói\n\n"
            "Vui lòng nhập <b>số lượng</b> bạn muốn mua (hoặc gõ /cancel để hủy):"
        ),
        "invalid_quantity": "⚠️ Số lượng không hợp lệ. Vui lòng nhập số nguyên lớn hơn 0 (hoặc gõ /cancel để hủy):",
        "quantity_exceed": "⚠️ Kho chỉ còn {stock} gói. Vui lòng nhập số lượng nhỏ hơn hoặc bằng {stock}:",
        "confirm_order_summary": (
            "📋 <b>XÁC NHẬN ĐƠN HÀNG:</b>\n\n"
            "• Gói dịch vụ: <b>{name}</b>\n"
            "• Số lượng: <b>{qty}</b>\n"
            "• Đơn giá: <b>{price:,.0f} VND</b>\n"
            "• Tổng thanh toán: <b>{total:,.0f} VND</b>\n\n"
            "Bạn có chắc chắn muốn đặt mua đơn hàng này không?"
        ),
        "btn_confirm_yes": "✅ Xác nhận đặt hàng",
        "btn_confirm_no": "❌ Hủy đơn",
        "order_cancelled": "❌ Đã hủy đặt hàng. Gõ /start để quay lại trang chủ.",
        "order_cancelled_prompt": "🚫 Bạn đã hủy quy trình đặt hàng thành công.\nGõ /start để trở về menu chính hoặc /buy để mua hàng.",
        "order_create_failed": "⚠️ Không thể tạo đơn hàng: {err}",
        "system_error": "❌ Đã xảy ra lỗi hệ thống khi xử lý đơn hàng. Vui lòng thử lại sau.",
        
        # Payment Instructions
        "order_created_caption": (
            "🎉 <b>ĐẶT HÀNG THÀNH CÔNG!</b>\n\n"
            "• Mã đơn hàng: <b>#{order_id}</b>\n"
            "• Sản phẩm: <b>{name}</b> x {qty}\n"
            "• Tổng số tiền: <b>{total:,.0f} VND</b>\n"
            "• Trạng thái: <i>Chờ thanh toán</i>\n\n"
            "📲 <b>HƯỚNG DẪN THANH TOÁN QUA VIETQR:</b>\n"
            "1. Mở ứng dụng ngân hàng hoặc ví điện tử bất kỳ.\n"
            "2. Quét mã QR trên để tự động điền STK, số tiền và nội dung chuyển khoản.\n"
            "3. Sau khi chuyển tiền, hệ thống sẽ tự động giao tài khoản sau 1-3 giây!"
        ),
        "btn_paid_confirm": "✅ Tôi đã chuyển khoản xong",
        "paid_notification_received": "⏳ Đã ghi nhận thông báo chuyển khoản của bạn cho đơn #{order_id}.\nBộ phận đối soát sẽ duyệt ngay khi tiền nổi vào tài khoản!",
        
        # Fulfillment / Delivery
        "delivery_title": "🎉 <b>THANH TOÁN THÀNH CÔNG ĐƠN HÀNG #{order_id}!</b>\n\n",
        "delivery_plan": "🤖 <b>Gói dịch vụ:</b> <b>{item_name}</b>\n",
        "delivery_amount": "💵 <b>Số tiền đã nhận:</b> <b>{total_amount:,.0f} VND</b>\n━━━━━━━━━━━━━━━━━━\n",
        "delivery_box_title": "📦 <b>THÔNG TIN TÀI KHOẢN AI CỦA BẠN:</b>\n\n",
        "delivery_email": "• 📧 Email đăng nhập: <code>{email}</code>\n",
        "delivery_password": "• 🔑 Mật khẩu: <code>{password}</code>\n",
        "delivery_2fa": "• 🛡️ Mã 2FA Secret: <code>{two_factor}</code>\n",
        "delivery_link": (
            "• 🌐 Link đăng nhập: https://chatgpt.com\n"
            "• 📁 Link tài liệu & hướng dẫn: https://drive.google.com/file/d/1DLAi2HqQCXiuDaHXC6lmafeHMdbCPiVx/view?pli=1\n\n"
        ),
        "delivery_warranty_full": (
            "🛡️ <b>CHÍNH SÁCH BẢO HÀNH FULL 30 NGÀY:</b>\n"
            "• Bảo hành 1 đổi 1 trọn vẹn 30 ngày nếu phát sinh lỗi từ hệ thống.\n"
            "• Hỗ trợ kỹ thuật 24/7 qua admin @Neitman275 hoặc @Huyneko.\n"
        ),
        "delivery_warranty_none": (
            "⚠️ <b>LƯU Ý SỬ DỤNG:</b>\n"
            "• Gói không bảo hành, vui lòng không đổi thông tin email gốc.\n"
            "• Tài khoản dùng riêng biệt, kích hoạt sử dụng ngay.\n"
        ),
        "delivery_footer": "Cảm ơn bạn đã lựa chọn <b>AI Store</b>! Chúc bạn có trải nghiệm tuyệt vời cùng AI.",
        
        # Restock Notification
        "restock_broadcast_caption": (
            "🎉 <b>HÀNG MỚI ĐÃ VỀ KHO!</b> 📦\n\n"
            "Shop vừa nạp thêm tài khoản cho gói:\n"
            "👉 <b>{name}</b>\n\n"
            "• 💰 Đơn giá: <b>{price:,.0f} VND</b>\n"
            "• 📦 Vừa nạp thêm: <b>+{added}</b> tài khoản\n"
            "• ⚡ Tồn kho hiện có: <b>{stock}</b> gói sẵn sàng\n"
            "• 🛡️ Chính sách: <i>{warranty}</i>\n\n"
            "<i>Số lượng có hạn, bạn hãy bấm nút bên dưới để đặt mua ngay nhé!</i>"
        ),
        "btn_buy_now_direct": "⚡ Mua ngay ({price}đ)",
    },
    
    "en": {
        # Language Selection
        "choose_lang_title": "👋 <b>Welcome to AI Store!</b>\n\nPlease choose your preferred language:\nVui lòng chọn ngôn ngữ để tiếp tục:",
        "lang_vi_btn": "🇻🇳 Tiếng Việt",
        "lang_en_btn": "🇬🇧 English",
        "lang_switched": "✅ Language changed to <b>English</b> successfully!",
        
        # Main Menu
        "welcome": (
            "Hello, <b>{name}</b>! 👋\n\n"
            "Welcome to <b>AI Store</b> 🤖\n"
            "Providing genuine AI accounts with automated 24/7 instant delivery.\n\n"
            "Please choose an option below to get started:"
        ),
        "btn_products": "🤖 View AI Plans",
        "btn_cart": "🛒 My Orders",
        "btn_support": "📞 Support & Warranty",
        "btn_change_lang": "🌐 Change Language",
        "btn_back": "🔙 Back to Menu",
        
        # Product Catalog
        "catalog_title": "🤖 <b>AI PRICE LIST:</b>\n\n",
        "price_label": "Price",
        "policy_label": "Warranty",
        "stock_label": "In Stock",
        "sold_label": "Sold",
        "warranty_full_badge": "🛡️ 30-Day 1-to-1 Full Warranty",
        "warranty_none_badge": "⚡ Budget Plan, Private Account",
        "select_pkg_prompt": "<i>Select the plan you wish to purchase below:</i>\n",
        "btn_buy_pkg": "👉 Buy {name} ({price} VND)",
        "catalog_empty": "Account stock is currently being updated. Please check back later!",
        
        # Support
        "support_text": (
            "📞 <b>SUPPORT & WARRANTY CENTER</b>\n\n"
            "If you need assistance with account activation or warranty:\n"
            "• Telegram Support: <code>@Neitman275</code> or <code>@Huyneko</code>\n"
            "• Operating Hours: 08:00 - 23:00 (GMT+7) Daily\n"
            "• Fast 1-to-1 replacement guaranteed for Full Warranty plans."
        ),
        
        # Orders / Cart
        "no_orders": "🛒 You don't have any orders yet.\nChoose an AI plan to get started!",
        "order_history_title": "📋 <b>YOUR ORDER HISTORY:</b>\n\n",
        "order_item_header": "Order",
        "order_total": "Total",
        "order_status_label": "Status",
        "status_pending": "Pending Payment",
        "status_waiting": "Awaiting Confirmation",
        "status_paid": "Paid",
        "status_cancelled": "Cancelled",
        "order_pkg_label": "Plan",
        "order_delivered_accounts_title": "📦 <b>Delivered Account(s):</b>",
        "order_account_email_label": "Email",
        "order_account_pwd_label": "Password",
        "order_account_2fa_label": "2FA Secret",
        "order_account_awaiting": "⚠️ <i>Awaiting account assignment (contact @Neitman275 or @Huyneko)</i>",
        "order_account_copy_hint": "<i>(Tap any value above to copy)</i>",
        
        # Checkout FSM
        "checkout_selected": (
            "🛒 You selected: <b>{name}</b>\n"
            "💰 Unit price: <b>{price:,.0f} VND</b>\n"
            "📦 Available stock: <b>{stock}</b> units\n"
            "🔥 Sold: <b>{sold}</b> units\n\n"
            "Please enter the <b>quantity</b> you want to purchase (or type /cancel to abort):"
        ),
        "invalid_quantity": "⚠️ Invalid quantity. Please enter a positive whole number (or type /cancel to abort):",
        "quantity_exceed": "⚠️ Only {stock} units available in stock. Please enter a quantity up to {stock}:",
        "confirm_order_summary": (
            "📋 <b>ORDER CONFIRMATION:</b>\n\n"
            "• Plan: <b>{name}</b>\n"
            "• Quantity: <b>{qty}</b>\n"
            "• Unit price: <b>{price:,.0f} VND</b>\n"
            "• Total amount: <b>{total:,.0f} VND</b>\n\n"
            "Are you sure you want to place this order?"
        ),
        "btn_confirm_yes": "✅ Confirm Order",
        "btn_confirm_no": "❌ Cancel Order",
        "order_cancelled": "❌ Order cancelled. Type /start to return to home menu.",
        "order_cancelled_prompt": "🚫 Order process cancelled successfully.\nType /start to return to the main menu or /buy to purchase.",
        "order_create_failed": "⚠️ Could not create order: {err}",
        "system_error": "❌ System error while processing order. Please try again later.",
        
        # Payment Instructions
        "order_created_caption": (
            "🎉 <b>ORDER CREATED SUCCESSFULLY!</b>\n\n"
            "• Order ID: <b>#{order_id}</b>\n"
            "• Plan: <b>{name}</b> x {qty}\n"
            "• Total Amount: <b>{total:,.0f} VND</b>\n"
            "• Status: <i>Pending Payment</i>\n\n"
            "📲 <b>PAYMENT INSTRUCTIONS VIA VIETQR:</b>\n"
            "1. Open any Vietnamese banking app or MoMo.\n"
            "2. Scan the VietQR code above (account number, exact amount & memo are pre-filled).\n"
            "3. Once transferred, your account credentials will be automatically delivered in 1-3 seconds!"
        ),
        "btn_paid_confirm": "✅ I Have Paid",
        "paid_notification_received": "⏳ Payment notification received for order #{order_id}.\nOur system will confirm your order as soon as the bank transfer is detected!",
        
        # Fulfillment / Delivery
        "delivery_title": "🎉 <b>PAYMENT CONFIRMED FOR ORDER #{order_id}!</b>\n\n",
        "delivery_plan": "🤖 <b>Service Plan:</b> <b>{item_name}</b>\n",
        "delivery_amount": "💵 <b>Amount Received:</b> <b>{total_amount:,.0f} VND</b>\n━━━━━━━━━━━━━━━━━━\n",
        "delivery_box_title": "📦 <b>YOUR AI ACCOUNT CREDENTIALS:</b>\n\n",
        "delivery_email": "• 📧 Email Login: <code>{email}</code>\n",
        "delivery_password": "• 🔑 Password: <code>{password}</code>\n",
        "delivery_2fa": "• 🛡️ 2FA Secret Key: <code>{two_factor}</code>\n",
        "delivery_link": (
            "• 🌐 Login URL: https://chatgpt.com\n"
            "• 📁 Guide & Resources: https://drive.google.com/file/d/1DLAi2HqQCXiuDaHXC6lmafeHMdbCPiVx/view?pli=1\n\n"
        ),
        "delivery_warranty_full": (
            "🛡️ <b>30-DAY FULL WARRANTY POLICY:</b>\n"
            "• 1-to-1 immediate replacement guaranteed for 30 full days.\n"
            "• 24/7 technical support via admin @Neitman275 or @Huyneko.\n"
        ),
        "delivery_warranty_none": (
            "⚠️ <b>USAGE NOTICE:</b>\n"
            "• Economy plan without warranty. Please do not modify original email.\n"
            "• Dedicated account ready for immediate personal use.\n"
        ),
        "delivery_footer": "Thank you for choosing <b>AI Store</b>! Have an amazing AI experience.",
        
        # Restock Notification
        "restock_broadcast_caption": (
            "🎉 <b>RESTOCK ALERT: NEW ACCOUNTS AVAILABLE!</b> 📦\n\n"
            "We have just restocked accounts for:\n"
            "👉 <b>{name}</b>\n\n"
            "• 💰 Price: <b>{price:,.0f} VND</b>\n"
            "• 📦 Restocked: <b>+{added}</b> accounts\n"
            "• ⚡ Available in stock: <b>{stock}</b> units ready\n"
            "• 🛡️ Policy: <i>{warranty}</i>\n\n"
            "<i>Limited stock available! Tap the button below to purchase immediately.</i>"
        ),
        "btn_buy_now_direct": "⚡ Buy Now ({price} VND)",
    }
}


def t(key: str, lang: Optional[str] = None, **kwargs) -> str:
    """Retrieve localized message string with parameter interpolation."""
    selected_lang = lang if lang in STRINGS else DEFAULT_LANG
    raw = STRINGS.get(selected_lang, {}).get(key)
    if raw is None:
        # Fallback to default language
        raw = STRINGS.get(DEFAULT_LANG, {}).get(key, key)
    if kwargs:
        try:
            return raw.format(**kwargs)
        except Exception:
            return raw
    return raw


def get_user_lang(db: Session, telegram_id: int) -> str:
    """Get language preference for a user. Defaults to 'vi'."""
    user = db.query(User).filter(User.telegram_id == telegram_id).first()
    if user and user.language in STRINGS:
        return user.language
    return DEFAULT_LANG


def is_user_lang_set(db: Session, telegram_id: int) -> bool:
    """Check if the user has explicitly selected a language."""
    user = db.query(User).filter(User.telegram_id == telegram_id).first()
    return bool(user and user.language in STRINGS)


def set_user_lang(db: Session, telegram_id: int, lang: str) -> None:
    """Set language preference for a user ('vi' or 'en')."""
    if lang not in STRINGS:
        lang = DEFAULT_LANG
    user = db.query(User).filter(User.telegram_id == telegram_id).first()
    if user:
        user.language = lang
        db.flush()
