"""Asynchronous Webhook Server for Bank Payment Notifications.

Listens for HTTP POST webhook events from payment gateways (e.g. SePay, PayOS)
or local testing endpoints, matches incoming transfers to customer orders,
updates the database status to PAID, and immediately delivers the ChatGPT Plus
package to the customer via Telegram.
"""

import json
import logging
from aiohttp import web
from telegram import Bot

from config import ADMIN_CHAT_ID, SEPAY_API_KEY
from database.database import get_db
from services.payment_service import PaymentService

logger = logging.getLogger(__name__)


class WebhookServer:
    """Manages the aiohttp webhook server."""

    def __init__(self, bot: Bot) -> None:
        self.bot = bot
        self.app = web.Application()
        self._setup_routes()

    def _setup_routes(self) -> None:
        """Register HTTP endpoints."""
        self.app.router.add_get("/", self.health_check)
        self.app.router.add_get("/health", self.health_check)
        self.app.router.add_post("/webhook/payment", self.handle_payment_webhook)
        self.app.router.add_post("/webhook/fake-payment", self.handle_fake_payment)

    async def health_check(self, request: web.Request) -> web.Response:
        """Health check endpoint."""
        logger.info(">>> Nhận request kiểm tra health_check từ: %s", request.remote)
        return web.json_response({
            "status": "online",
            "service": "Telegram Shop Bot Payment Webhook",
            "supported_gateways": ["SePay", "PayOS", "VietQR"],
        })

    async def handle_payment_webhook(self, request: web.Request) -> web.Response:
        """Handle incoming bank transaction webhooks (SePay / PayOS format)."""
        # 1. Verify Authorization Header if SEPAY_API_KEY is configured
        if SEPAY_API_KEY:
            auth_header = request.headers.get("Authorization", "").strip()
            # Compatible with "Apikey <KEY>", "Bearer <KEY>", or exact key
            if not auth_header or SEPAY_API_KEY not in auth_header:
                logger.warning("Unauthorized webhook request rejected. Provided auth header: %s", auth_header)
                return web.json_response({"error": "Unauthorized"}, status=401)

        try:
            payload = await request.json()
            logger.info("Received payment webhook payload: %s", json.dumps(payload, ensure_ascii=False))
        except Exception as exc:
            logger.error("Failed to parse JSON body: %s", exc)
            return web.json_response({"error": "Invalid JSON"}, status=400)

        # 2. Extract transfer fields (compatible with SePay, PayOS, or generic)
        content = ""
        amount = 0
        ref_code = ""
        gateway = "Bank"

        # Check SePay standard payload
        if "transferAmount" in payload or "content" in payload:
            content = str(payload.get("content") or payload.get("description") or "")
            amount = int(payload.get("transferAmount") or 0)
            ref_code = str(payload.get("referenceCode") or payload.get("id") or "")
            gateway = str(payload.get("gateway") or "SePay")

        # Check PayOS payload format
        elif "data" in payload and isinstance(payload["data"], dict):
            payos_data = payload["data"]
            content = str(payos_data.get("description") or "")
            amount = int(payos_data.get("amount") or 0)
            ref_code = str(payos_data.get("reference") or payos_data.get("orderCode") or "")
            gateway = "PayOS"

        # Fallback to direct keys
        else:
            content = str(payload.get("content") or payload.get("description") or payload.get("order_id") or "")
            amount = int(payload.get("amount") or 0)
            ref_code = str(payload.get("ref") or payload.get("referenceCode") or "")

        # 3. Extract order ID from content (e.g. 'DH 1' -> 1)
        order_id = PaymentService.extract_order_id(content)
        if not order_id and "order_id" in payload:
            try:
                order_id = int(payload["order_id"])
            except ValueError:
                pass

        if not order_id:
            logger.warning("Could not identify order ID from webhook content: '%s'", content)
            return web.json_response({
                "success": False,
                "message": f"Không tìm thấy mã đơn hàng trong nội dung '{content}'",
            }, status=200)

        # 4. Process payment and fulfill order
        with get_db() as db:
            success, message, order, delivery_text = PaymentService.process_payment(
                db=db,
                order_id=order_id,
                transfer_amount=amount,
                transaction_ref=ref_code,
                gateway=gateway,
            )

            # If payment succeeded and we have delivery credentials to send
            if success and delivery_text and order and order.user:
                customer_telegram_id = order.user.telegram_id
                customer_name = order.user.full_name or "Khách hàng"

                # Send package delivery message directly to customer via Telegram
                try:
                    await self.bot.send_message(
                        chat_id=customer_telegram_id,
                        text=delivery_text,
                        parse_mode="HTML",
                        disable_web_page_preview=True,
                    )
                    logger.info("Delivered ChatGPT Plus package to user %s for Order #%s", customer_telegram_id, order_id)
                except Exception as send_err:
                    logger.error("Failed to send Telegram delivery message to %s: %s", customer_telegram_id, send_err)

                # Send alert to Admin if configured
                if ADMIN_CHAT_ID:
                    admin_alert = (
                        f"🔔 <b>THÔNG BÁO TIỀN VỀ THÀNH CÔNG!</b>\n\n"
                        f"• Đơn hàng: <b>#{order_id}</b>\n"
                        f"• Khách hàng: <b>{customer_name}</b> (ID: <code>{customer_telegram_id}</code>)\n"
                        f"• Số tiền: <b>{amount:,.0f} VND</b>\n"
                        f"• Cổng: <b>{gateway}</b> | Mã GD: <code>{ref_code}</code>\n"
                        f"• Trạng thái: <i>Hệ thống đã tự động gửi tài khoản cho khách.</i>"
                    )
                    try:
                        await self.bot.send_message(
                            chat_id=ADMIN_CHAT_ID,
                            text=admin_alert,
                            parse_mode="HTML",
                        )
                    except Exception as admin_err:
                        logger.warning("Failed to notify admin: %s", admin_err)

        return web.json_response({
            "success": success,
            "order_id": order_id,
            "message": message,
        })

    async def handle_fake_payment(self, request: web.Request) -> web.Response:
        """Endpoint to simulate a bank payment for testing without real money.

        Accepts: {"order_id": 1, "amount": 275000} (amount is optional, defaults to order total)
        """
        try:
            payload = await request.json()
        except Exception:
            payload = {}

        order_id = payload.get("order_id")
        if not order_id:
            return web.json_response({"error": "Vui lòng truyền order_id (ví dụ: {'order_id': 1})"}, status=400)

        with get_db() as db:
            from database.models import Order
            target_order = db.query(Order).filter(Order.id == order_id).first()
            if not target_order:
                return web.json_response({"error": f"Không tìm thấy đơn hàng #{order_id}"}, status=404)

            amount = payload.get("amount") or target_order.total_amount

            success, message, order, delivery_text = PaymentService.process_payment(
                db=db,
                order_id=order_id,
                transfer_amount=amount,
                transaction_ref="TEST_SIMULATION_2026",
                gateway="TestGateway",
            )

            if success and delivery_text and order and order.user:
                await self.bot.send_message(
                    chat_id=order.user.telegram_id,
                    text=delivery_text,
                    parse_mode="HTML",
                    disable_web_page_preview=True,
                )

        return web.json_response({
            "success": success,
            "order_id": order_id,
            "simulated_amount": amount,
            "message": message,
        })
