"""Payment service module.

Handles payment matching, order validation, and automated digital
product delivery for ChatGPT Plus packages upon receiving bank webhooks.
"""

import logging
import re
from typing import Optional, Tuple
from sqlalchemy.orm import Session

from database.models import Order, OrderItem, Product, ProductAccount, User
from services.account_service import AccountService

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
    def generate_delivery_message(order: Order, item_name: str, accounts: Optional[list[ProductAccount]] = None) -> str:
        """Format a rich delivery message delivering the ChatGPT Plus credentials to customer."""
        if accounts is None:
            accounts = list(order.delivered_accounts) if order.delivered_accounts else []
        return AccountService.format_delivery_message(order, accounts, item_name)

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

        # Determine item name
        first_item = order.items[0] if order.items else None
        item_name = first_item.product.name if first_item and first_item.product else "ChatGPT Plus 1 Tháng"

        if order.status == "PAID":
            # Idempotency: already paid, retrieve already delivered accounts
            delivered_accounts = list(order.delivered_accounts)
            delivery_message = AccountService.format_delivery_message(order, delivered_accounts, item_name)
            return True, f"Đơn hàng #{order_id} đã được thanh toán và kích hoạt trước đó.", order, delivery_message

        if transfer_amount < order.total_amount:
            msg = (
                f"Đơn #{order_id}: Số tiền nhận được ({transfer_amount:,.0f}đ) "
                f"ít hơn tổng đơn ({order.total_amount:,.0f}đ)."
            )
            logger.warning(msg)
            return False, msg, order, None

        # Mark order as PAID
        order.status = "PAID"
        db.flush()

        # Allocate real accounts from inventory
        delivered_accounts = AccountService.allocate_accounts_for_order(db, order)

        delivery_message = AccountService.format_delivery_message(order, delivered_accounts, item_name)
        logger.info(
            "Order #%s successfully marked as PAID (Ref: %s, Gateway: %s, Accounts delivered: %s)",
            order_id,
            transaction_ref,
            gateway,
            len(delivered_accounts),
        )

        return True, "Thanh toán thành công và đã xuất kho tài khoản.", order, delivery_message
