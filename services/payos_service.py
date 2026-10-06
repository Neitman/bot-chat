"""PayOS Payment Gateway Integration Service.

Handles automated VietQR payment link generation, webhook signature
verification, and instant order settlement via PayOS Open Banking API.
"""

import logging
from typing import Any, Optional, Tuple

from config import PAYOS_API_KEY, PAYOS_CHECKSUM_KEY, PAYOS_CLIENT_ID

logger = logging.getLogger(__name__)

_payos_instance = None


def get_payos_client():
    """Retrieve or initialize singleton PayOS client."""
    global _payos_instance
    if not (PAYOS_CLIENT_ID and PAYOS_API_KEY and PAYOS_CHECKSUM_KEY):
        return None

    if _payos_instance is None:
        try:
            from payos import PayOS
            _payos_instance = PayOS(
                client_id=PAYOS_CLIENT_ID,
                api_key=PAYOS_API_KEY,
                checksum_key=PAYOS_CHECKSUM_KEY,
            )
            logger.info("PayOS client initialized successfully.")
        except Exception as exc:
            logger.error("Failed to initialize PayOS client: %s", exc)
            return None

    return _payos_instance


class PayOSService:
    """Service handling PayOS payment links and webhooks."""

    @classmethod
    def is_configured(cls) -> bool:
        """Check if all PayOS required credentials are present."""
        return bool(PAYOS_CLIENT_ID and PAYOS_API_KEY and PAYOS_CHECKSUM_KEY)

    @classmethod
    def create_payment_link(
        cls,
        order_id: int,
        amount: int,
        description: str = "",
        item_name: str = "Tài khoản số",
        quantity: int = 1,
        return_url: str = "https://t.me",
        cancel_url: str = "https://t.me",
    ) -> Optional[dict]:
        """Create a PayOS payment link with VietQR for customer order.

        Args:
            order_id: Numeric shop order ID (mapped to orderCode).
            amount: Amount in VND.
            description: Transfer description (max 25 characters, e.g. 'DH 35').
            item_name: Name of product purchased.
            quantity: Quantity purchased.
            return_url: Redirect URL after successful payment.
            cancel_url: Redirect URL upon cancellation.

        Returns:
            Dict containing checkout_url, qr_code, account_number, etc. or None.
        """
        import time
        client = get_payos_client()
        if not client:
            logger.debug("PayOS is not configured; skipping payment link creation.")
            return None

        try:
            from payos.type import ItemData, PaymentData
        except ImportError:
            try:
                from payos.types import ItemData, PaymentData
            except ImportError:
                ItemData = None
                PaymentData = None

        clean_desc = (description or f"DH {order_id}").strip()[:25]
        item_price = int(amount // quantity) if quantity > 0 else int(amount)

        if ItemData and PaymentData:
            items = [ItemData(name=item_name[:40], quantity=quantity, price=item_price)]
            payment_data = PaymentData(
                orderCode=int(order_id),
                amount=int(amount),
                description=clean_desc,
                cancelUrl=cancel_url,
                returnUrl=return_url,
                items=items,
            )
        else:
            payment_data = {
                "orderCode": int(order_id),
                "amount": int(amount),
                "description": clean_desc,
                "cancelUrl": cancel_url,
                "returnUrl": return_url,
                "items": [{"name": item_name[:40], "quantity": quantity, "price": item_price}],
            }

        try:
            res = client.createPaymentLink(paymentData=payment_data)
        except Exception as exc:
            # If orderCode already exists on PayOS, retry with unique suffix
            err_msg = str(exc).lower()
            if "tồn tại" in err_msg or "exists" in err_msg or "already" in err_msg:
                fallback_code = int(f"{order_id}{int(time.time()) % 10000:04d}")
                logger.info("Order code %s already exists on PayOS, retrying with fallback code %s", order_id, fallback_code)
                try:
                    if hasattr(payment_data, "orderCode"):
                        payment_data.orderCode = fallback_code
                    elif isinstance(payment_data, dict):
                        payment_data["orderCode"] = fallback_code
                    res = client.createPaymentLink(paymentData=payment_data)
                except Exception as retry_exc:
                    logger.error("PayOS fallback createPaymentLink failed for order #%s: %s", order_id, retry_exc)
                    return None
            else:
                logger.error("PayOS createPaymentLink failed for order #%s: %s", order_id, exc)
                return None

        logger.info("PayOS payment link created for order #%s: %s", order_id, res.checkoutUrl)
        return {
            "checkout_url": res.checkoutUrl,
            "qr_code": res.qrCode,
            "account_number": res.accountNumber,
            "account_name": res.accountName,
            "bin": res.bin,
            "amount": res.amount,
            "order_code": res.orderCode,
        }

    @classmethod
    def confirm_webhook(cls, webhook_url: str) -> Tuple[bool, str]:
        """Confirm and register webhook URL with PayOS.

        Args:
            webhook_url: Full HTTPS public URL of webhook endpoint.

        Returns:
            Tuple of (success: bool, message: str).
        """
        client = get_payos_client()
        if not client:
            return False, "PayOS chưa được cấu hình trong .env"

        try:
            if hasattr(client, "webhooks") and hasattr(client.webhooks, "confirm"):
                res = client.webhooks.confirm(webhook_url)
            else:
                res = client.confirmWebhook(webhook_url)
            logger.info("PayOS webhook registered successfully: %s", res)
            return True, f"Xác nhận thành công Webhook URL: {webhook_url}"
        except Exception as exc:
            logger.error("Failed to confirm PayOS webhook: %s", exc)
            return False, str(exc)

    @classmethod
    def verify_webhook(cls, webhook_payload: dict) -> Tuple[bool, Optional[dict]]:
        """Verify webhook data authenticity using checksum key.

        Args:
            webhook_payload: Raw JSON body received from PayOS.

        Returns:
            Tuple of (is_valid, parsed_data_dict).
        """
        client = get_payos_client()
        if not client:
            return False, None

        try:
            if hasattr(client, "webhooks") and hasattr(client.webhooks, "verify"):
                webhook_data = client.webhooks.verify(webhook_payload)
            else:
                webhook_data = client.verifyPaymentWebhookData(webhook_payload)

            # Convert WebhookData model to dict
            data_dict = (
                webhook_data.model_dump()
                if hasattr(webhook_data, "model_dump")
                else (webhook_data.__dict__ if hasattr(webhook_data, "__dict__") else {})
            )
            return True, data_dict
        except Exception as exc:
            logger.warning("PayOS webhook verification failed: %s", exc)
            return False, None
