"""Asynchronous Webhook Server for Bank Payment Notifications.

Listens for HTTP POST webhook events from payment gateways (e.g. SePay, PayOS)
or local testing endpoints, matches incoming transfers to customer orders,
updates the database status to PAID, and immediately delivers the ChatGPT Plus
package to the customer via Telegram.
"""

from datetime import datetime
import json
import logging
import time
from aiohttp import web
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup

from config import ADMIN_CHAT_ID, PATO_NETFLIX_PRODUCT_ID, SEPAY_API_KEY, get_admin_ids
from database.database import get_db
from database.models import ProductAccount
from services.pato_service import PatoService
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
        try:
            payload = await request.json()
            logger.info("Received payment webhook payload: %s", json.dumps(payload, ensure_ascii=False))
        except Exception as exc:
            logger.error("Failed to parse JSON body: %s", exc)
            return web.json_response({"error": "Invalid JSON"}, status=400)

        # Handle PayOS webhook URL confirmation test
        if "webhookUrl" in payload or payload.get("desc") == "Confirm webhook":
            logger.info("PayOS Webhook URL confirmed successfully: %s", payload)
            return web.json_response({"success": True, "message": "PayOS Webhook confirmed"}, status=200)

        # 1. Determine gateway & authenticate
        content = ""
        amount = 0
        ref_code = ""
        gateway = "Bank"

        # Check PayOS payload format (contains 'data' object and 'signature')
        if "data" in payload and isinstance(payload["data"], dict) and "signature" in payload:
            from services.payos_service import PayOSService
            if PayOSService.is_configured():
                is_valid, verified_data = PayOSService.verify_webhook(payload)
                if not is_valid or not verified_data:
                    logger.warning("PayOS webhook signature verification failed.")
                    return web.json_response({"error": "Invalid PayOS signature"}, status=400)
                payos_data = verified_data
            else:
                payos_data = payload["data"]

            content = str(payos_data.get("description") or "")
            amount = int(payos_data.get("amount") or 0)
            ref_code = str(payos_data.get("reference") or payos_data.get("orderCode") or "")
            gateway = "PayOS"

        # Check SePay standard payload
        elif "transferAmount" in payload or "content" in payload:
            # Verify SePay API key if configured
            if SEPAY_API_KEY:
                auth_header = request.headers.get("Authorization", "").strip()
                if not auth_header or SEPAY_API_KEY not in auth_header:
                    logger.warning("Unauthorized SePay webhook rejected. Header: %s", auth_header)
                    return web.json_response({"error": "Unauthorized"}, status=401)

            content = str(payload.get("content") or payload.get("description") or "")
            amount = int(payload.get("transferAmount") or 0)
            ref_code = str(payload.get("referenceCode") or payload.get("id") or "")
            gateway = str(payload.get("gateway") or "SePay")

        # Fallback to direct keys
        else:
            if SEPAY_API_KEY:
                auth_header = request.headers.get("Authorization", "").strip()
                if not auth_header or SEPAY_API_KEY not in auth_header:
                    logger.warning("Unauthorized generic webhook rejected. Header: %s", auth_header)
                    return web.json_response({"error": "Unauthorized"}, status=401)

            content = str(payload.get("content") or payload.get("description") or payload.get("order_id") or "")
            amount = int(payload.get("amount") or 0)
            ref_code = str(payload.get("ref") or payload.get("referenceCode") or "")

        # 3. Extract order ID from content (e.g. 'DH 1' -> 1 or from PayOS orderCode)
        order_id = PaymentService.extract_order_id(content)
        if not order_id and gateway == "PayOS":
            try:
                order_id = int(payload.get("data", {}).get("orderCode", 0))
            except (ValueError, TypeError):
                pass
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

            if success and order and order.user:
                await self._fulfill_and_deliver(
                    db=db,
                    order=order,
                    amount=amount,
                    ref_code=ref_code,
                    gateway=gateway,
                    initial_delivery_text=delivery_text or "",
                )

        return web.json_response({
            "success": success,
            "order_id": order_id,
            "message": message,
        })

    async def handle_fake_payment(self, request: web.Request) -> web.Response:
        """Endpoint to simulate a bank payment for testing without real money.

        Accepts: {"order_id": 1, "amount": 270000} (amount is optional, defaults to order total)
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

            if success and order and order.user:
                await self._fulfill_and_deliver(
                    db=db,
                    order=order,
                    amount=amount,
                    ref_code="TEST_SIMULATION_2026",
                    gateway="TestGateway",
                    initial_delivery_text=delivery_text or "",
                )

        return web.json_response({
            "success": success,
            "order_id": order_id,
            "simulated_amount": amount,
            "message": message,
        })

    async def _fulfill_and_deliver(
        self,
        db,
        order,
        amount: int,
        ref_code: str,
        gateway: str,
        initial_delivery_text: str,
    ) -> None:
        """Handle on-demand Pato fulfillment for Netflix and deliver credentials to customer."""
        if not order or not order.user:
            return

        customer_telegram_id = order.user.telegram_id
        customer_name = order.user.full_name or "Khách hàng"
        first_item = order.items[0] if order.items else None
        is_net = bool(
            first_item
            and first_item.product
            and (
                "netflix" in first_item.product.name.lower()
                or first_item.product_id == PATO_NETFLIX_PRODUCT_ID
            )
        )

        delivery_text = initial_delivery_text
        pato_order_id = None

        # If this is Netflix, Pato is configured, and no stock was previously allocated from local DB:
        if is_net and PatoService.is_configured() and not order.delivered_accounts:
            # 1. Send waiting notification to customer
            waiting_msg = None
            try:
                waiting_msg = await self.bot.send_message(
                    chat_id=customer_telegram_id,
                    text=(
                        f"✅ <b>ĐÃ NHẬN THANH TOÁN ĐƠN HÀNG #{order.id}!</b>\n\n"
                        f"⏳ <i>Vui lòng đợi trong khi shop lấy link...</i>"
                    ),
                    parse_mode="HTML",
                )
            except Exception as w_err:
                logger.debug("Could not send waiting message: %s", w_err)

            # 2. Call Pato API to get login links
            try:
                item_qty = first_item.quantity if first_item else 1
                delivered_accs = []
                last_pato_order_id = ""

                for idx in range(item_qty):
                    pato_req_id = f"DH{order.id}-{int(time.time())}-{idx+1}"
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
                            order_id=order.id,
                            sold_at=datetime.now(),
                            note=f"PATO:{p_ord_id}",
                        )
                        db.add(new_acc)
                        delivered_accs.append(new_acc)

                db.commit()
                db.refresh(order)

                if last_pato_order_id:
                    pato_order_id = last_pato_order_id

                if delivered_accs:
                    delivery_text = PaymentService.generate_delivery_message(
                        order, first_item.product.name, delivered_accs
                    )

                if waiting_msg:
                    try:
                        await waiting_msg.delete()
                    except Exception:
                        pass

            except Exception as p_err:
                logger.error("Failed to fulfill Pato Netflix order #%s: %s", order.id, p_err)
                if waiting_msg:
                    try:
                        await waiting_msg.edit_text(
                            f"⚠️ <b>LƯU Ý VỀ ĐƠN HÀNG #{order.id}:</b>\n\n"
                            f"Hệ thống tạo link tự động đang bận hoặc bảo trì nhẹ.\n"
                            f"Đừng lo lắng, kỹ thuật viên shop đã nhận được thông báo và sẽ gửi link thủ công cho bạn trong ít phút!\n"
                            f"Hỗ trợ: @Neitman275 hoặc @Huyneko",
                            parse_mode="HTML",
                        )
                    except Exception:
                        pass

        # Check if order has Pato warranty
        has_pato_warranty = any(
            (acc.note and "PATO:" in acc.note) for acc in order.delivered_accounts
        ) or bool(pato_order_id)

        keyboard_buttons = []
        if is_net:
            keyboard_buttons.append([InlineKeyboardButton("🍿 Hướng dẫn đăng nhập Netflix", callback_data="menu_guide_netflix")])
            if has_pato_warranty:
                keyboard_buttons.append([InlineKeyboardButton("🔄 Đổi link / Bảo hành (1 Giờ)", callback_data=f"pato_warranty_{order.id}")])

        delivery_markup = InlineKeyboardMarkup(keyboard_buttons) if keyboard_buttons else None

        # Send delivery message to customer
        try:
            await self.bot.send_message(
                chat_id=customer_telegram_id,
                text=delivery_text,
                parse_mode="HTML",
                disable_web_page_preview=True,
                reply_markup=delivery_markup,
            )
            logger.info("Delivered order #%s package to user %s", order.id, customer_telegram_id)
        except Exception as send_err:
            logger.error("Failed to send Telegram delivery message to %s: %s", customer_telegram_id, send_err)

        # Send alert to Admins
        admin_ids = get_admin_ids()
        if admin_ids:
            pato_extra = f"\n• Phiên Pato: <code>{pato_order_id}</code>" if pato_order_id else ""
            admin_alert = (
                f"🔔 <b>THÔNG BÁO TIỀN VỀ THÀNH CÔNG!</b>\n\n"
                f"• Đơn hàng: <b>#{order.id}</b>\n"
                f"• Khách hàng: <b>{customer_name}</b> (ID: <code>{customer_telegram_id}</code>)\n"
                f"• Số tiền: <b>{amount:,.0f} VND</b>\n"
                f"• Cổng: <b>{gateway}</b> | Mã GD: <code>{ref_code}</code>{pato_extra}\n"
                f"• Trạng thái: <i>Hệ thống đã tự động xuất link / tài khoản cho khách.</i>"
            )
            for aid in admin_ids:
                try:
                    await self.bot.send_message(chat_id=aid, text=admin_alert, parse_mode="HTML")
                except Exception as admin_err:
                    logger.warning("Failed to notify admin %s: %s", aid, admin_err)

