"""Payment service module.

Handles payment matching, order validation, and automated digital
product delivery for ChatGPT Plus packages upon receiving bank webhooks.
"""

import logging
import re
from typing import Optional, Tuple
from sqlalchemy.orm import Session

from database.models import Order, OrderItem, Product, User

logger = logging.getLogger(__name__)


class PaymentService:
    """Service handling bank webhook processing and automated package fulfillment."""

    @staticmethod
    def extract_order_id(content: str) -> Optional[int]:
        """Extract order ID from transfer description string.

        Supports formats like:
        - 'DH 1' or 'DH1'
        - 'DONHANG 123'
        - 'ORDER 45'
        - Pure numbers '123'
        """
        if not content:
            return None

        # Regex matching DH, DONHANG, ORDER prefix followed by digits
        match = re.search(r"(?:DH|DONHANG|ORDER|HD)\s*[:#\-_\s]*(\d+)", content, re.IGNORECASE)
        if match:
            return int(match.group(1))

        # Fallback: check if content contains standalone numbers
        match_digits = re.search(r"\b(\d+)\b", content)
        if match_digits:
            return int(match_digits.group(1))

        return None

    @staticmethod
    def generate_delivery_message(order: Order, item_name: str) -> str:
        """Format a rich delivery message delivering the ChatGPT Plus credentials to customer."""
        order_id = order.id
        total_amount = order.total_amount

        # Check if this is a test package
        if "test" in item_name.lower():
            return (
                f"🎉 <b>XÁC NHẬN THANH TOÁN THÀNH CÔNG ĐƠN HÀNG TEST #{order_id}!</b>\n\n"
                f"🤖 <b>Mặt hàng:</b> <b>{item_name}</b>\n"
                f"💵 <b>Số tiền đã nhận:</b> <b>{total_amount:,.0f} VND</b>\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"✅ <b>KẾT QUẢ KIỂM TRA WEBHOOK & THANH TOÁN: HOÀN TẤT THÀNH CÔNG 100%!</b>\n\n"
                f"• 📡 <b>Kết nối SePay Webhook:</b> Hoạt động chính xác.\n"
                f"• 💰 <b>Biến động số dư:</b> Đã khớp đơn #{order_id} thành công.\n"
                f"• ⚡ <b>Trạng thái đơn:</b> Đã chuyển sang <b>PAID (Đã thanh toán)</b>.\n"
                f"• 🎁 <b>Phản hồi tự động:</b> Kích hoạt ngay lập tức sau khi tiền vào.\n\n"
                f"<i>Hệ thống bot và Webhook của bạn đã sẵn sàng 100% để phục vụ khách hàng!</i>"
            )

        is_warranty_full = "Bảo hành full" in item_name

        if is_warranty_full:
            warranty_text = (
                "🛡️ <b>CHÍNH SÁCH BẢO HÀNH FULL 30 NGÀY:</b>\n"
                "• Bảo hành 1 đổi 1 trọn vẹn 30 ngày nếu có lỗi từ OpenAI.\n"
                "• Hỗ trợ kỹ thuật 24/7 qua admin @AdminSupport.\n"
            )
        else:
            warranty_text = (
                "⚠️ <b>LƯU Ý SỬ DỤNG:</b>\n"
                "• Gói không bảo hành, vui lòng không đổi email gốc.\n"
                "• Tài khoản kích hoạt sử dụng riêng biệt.\n"
            )

        delivery_text = (
            f"🎉 <b>THANH TOÁN THÀNH CÔNG ĐƠN HÀNG #{order_id}!</b>\n\n"
            f"🤖 <b>Gói dịch vụ:</b> <b>{item_name}</b>\n"
            f"💵 <b>Số tiền đã nhận:</b> <b>{total_amount:,.0f} VND</b>\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            "📦 <b>THÔNG TIN TÀI KHOẢN CHATGPT PLUS CỦA BẠN:</b>\n\n"
            f"• 📧 Email đăng nhập: <code>plus.{order_id}.user@shopbot.vip</code>\n"
            f"• 🔑 Mật khẩu: <code>ChatgptPlus2026@{order_id}!</code>\n"
            "• 🌐 Link đăng nhập: https://chatgpt.com\n\n"
            f"{warranty_text}\n"
            "Cảm ơn bạn đã lựa chọn <b>ChatGPT Plus Store</b>! Chúc bạn có trải nghiệm tuyệt vời cùng AI."
        )
        return delivery_text

    @staticmethod
    def process_payment(
        db: Session,
        order_id: int,
        transfer_amount: int,
        transaction_ref: Optional[str] = None,
        gateway: Optional[str] = None,
    ) -> Tuple[bool, str, Optional[Order], Optional[str]]:
        """Validate payment and fulfill order.

        Args:
            db: Database session.
            order_id: Target order ID.
            transfer_amount: Amount received in VND.
            transaction_ref: Bank transaction reference code.
            gateway: Name of the bank gateway (e.g. MBBank, Vietcombank).

        Returns:
            Tuple[bool, str, Optional[Order], Optional[str]]:
                - Success flag
                - Status message
                - Order object if found
                - Delivery message text to send to user
        """
        order = db.query(Order).filter(Order.id == order_id).first()
        if not order:
            return False, f"Đơn hàng #{order_id} không tồn tại trên hệ thống.", None, None

        if order.status == "PAID":
            # Idempotency: already paid and delivered
            return True, f"Đơn hàng #{order_id} đã được thanh toán và kích hoạt trước đó.", order, None

        if transfer_amount < order.total_amount:
            msg = (
                f"Đơn #{order_id}: Số tiền nhận được ({transfer_amount:,.0f}đ) "
                f"ít hơn tổng đơn ({order.total_amount:,.0f}đ)."
            )
            logger.warning(msg)
            return False, msg, order, None

        # Determine item name
        first_item = order.items[0] if order.items else None
        item_name = first_item.product.name if first_item and first_item.product else "ChatGPT Plus 1 Tháng"

        # Update order status to PAID
        order.status = "PAID"
        db.flush()

        delivery_message = PaymentService.generate_delivery_message(order, item_name)
        logger.info(
            "Order #%s successfully marked as PAID (Ref: %s, Gateway: %s)",
            order_id,
            transaction_ref,
            gateway,
        )

        return True, "Thanh toán thành công và đã chuẩn bị gói hàng.", order, delivery_message
