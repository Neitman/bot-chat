"""Account service module for digital goods and inventory management.

Handles parsing, bulk import, FIFO stock allocation, 2FA OTP generation,
and credential formatting for digital products (e.g., ChatGPT Plus accounts).
"""

import base64
import hashlib
import hmac
import logging
import struct
import time
from typing import Optional, Tuple
from sqlalchemy import func
from sqlalchemy.orm import Session

from config import PATO_NETFLIX_PRODUCT_ID
from database.models import Order, OrderItem, Product, ProductAccount

logger = logging.getLogger(__name__)


class AccountService:
    """Service handling digital account inventory, import, and fulfillment."""

    @staticmethod
    def get_totp_code(secret: Optional[str]) -> Optional[str]:
        """Generate a 6-digit Time-based One-Time Password (TOTP) code.

        Uses standard RFC 6238 implementation with pure Python standard library
        (HMAC-SHA1, 30s interval, 6-digit output).

        Args:
            secret: Base32 encoded 2FA secret key (e.g. E6M7ATQ7QHEALOH7BU2RN6YZRQNBMBE6).

        Returns:
            6-digit OTP code string, or None if secret is empty or invalid.
        """
        if not secret:
            return None

        try:
            clean_secret = secret.replace(" ", "").strip().upper()
            missing_padding = len(clean_secret) % 8
            if missing_padding != 0:
                clean_secret += "=" * (8 - missing_padding)

            key = base64.b32decode(clean_secret, casefold=True)
            interval_no = int(time.time()) // 30
            msg = struct.pack(">Q", interval_no)
            h = hmac.new(key, msg, hashlib.sha1).digest()
            offset = h[19] & 0x0F
            code = (struct.unpack(">I", h[offset : offset + 4])[0] & 0x7FFFFFFF) % 1000000
            return f"{code:06d}"
        except Exception as exc:
            logger.debug("Failed to calculate TOTP for secret '%s': %s", secret, exc)
            return None

    @staticmethod
    def parse_account_line(line: str) -> Optional[dict]:
        """Parse a single raw string line into account credential fields.

        Supported formats:
        - email | password | 2fa_secret
        - email | password
        - email:password:2fa_secret
        - or raw text

        Returns:
            Dict containing parsed fields, or None if line is blank.
        """
        raw = line.strip()
        if not raw or raw.startswith(("#", "//")):
            return None

        # Check if line is a direct URL (e.g. Netflix login token link)
        if raw.startswith(("http://", "https://")):
            return {
                "raw_data": raw,
                "account": raw,
                "password": "",
                "two_factor": "",
                "note": "Link URL",
            }

        delimiter = "|" if "|" in raw else (":" if ":" in raw else None)

        if delimiter:
            parts = [p.strip() for p in raw.split(delimiter)]
        else:
            parts = [raw]

        account = parts[0] if len(parts) > 0 else ""
        password = parts[1] if len(parts) > 1 else ""
        two_factor = parts[2] if len(parts) > 2 else ""
        extra = parts[3:] if len(parts) > 3 else []

        return {
            "raw_data": raw,
            "account": account,
            "password": password,
            "two_factor": two_factor,
            "note": " | ".join(extra) if extra else None,
        }

    @staticmethod
    def validate_account_item(parsed: Optional[dict]) -> Tuple[bool, str]:
        """Validate parsed account fields to prevent junk/corrupted entries from entering database."""
        if not parsed:
            return False, "Dữ liệu trống."

        acc = parsed.get("account", "").strip()
        pwd = parsed.get("password", "").strip()
        two_fa = parsed.get("two_factor", "").strip()

        # URL items (e.g. Netflix login token links)
        if acc.startswith(("http://", "https://")):
            if len(acc) < 10:
                return False, "Đường link URL không hợp lệ (quá ngắn)."
            return True, "Hợp lệ"

        # Account / Email check
        if not acc or len(acc) < 3:
            return False, "Tài khoản/Email không hợp lệ (quá ngắn hoặc để trống)."

        if "@" in acc:
            parts = acc.split("@")
            if len(parts) != 2 or not parts[0] or "." not in parts[1]:
                return False, f"Email '{acc}' không đúng định dạng (thiếu @ hoặc domain hợp lệ)."

        # Password check
        if not pwd or len(pwd) < 4:
            return False, "Mật khẩu quá ngắn (yêu cầu tối thiểu 4 ký tự)."

        # 2FA Secret Key check
        if two_fa:
            import re
            clean_2fa = two_fa.replace(" ", "").upper()
            if not re.match(r"^[A-Z2-7=]{10,}$", clean_2fa):
                return False, f"Mã 2FA Secret '{two_fa}' không đúng chuẩn Base32 (chỉ gồm chữ hoa A-Z và số 2-7, tối thiểu 10 ký tự)."

        return True, "Hợp lệ"

    @staticmethod
    def add_single_account(
        db: Session,
        product_id: int,
        raw_line: str,
        note: Optional[str] = None,
    ) -> Tuple[bool, str, Optional[ProductAccount]]:
        """Add a single digital account into inventory for a product."""
        parsed = AccountService.parse_account_line(raw_line)
        is_valid, err_msg = AccountService.validate_account_item(parsed)
        if not is_valid:
            return False, f"Dữ liệu không hợp lệ: {err_msg}", None

        product = db.query(Product).filter(Product.id == product_id).first()
        if not product:
            return False, f"Không tìm thấy sản phẩm #{product_id}.", None

        # Check for duplicate available account for this product
        existing = (
            db.query(ProductAccount)
            .filter(
                ProductAccount.product_id == product_id,
                ProductAccount.account == parsed["account"],
                ProductAccount.status == "AVAILABLE",
            )
            .first()
        )
        if existing:
            return (
                False,
                f"Tài khoản '{parsed['account']}' đã tồn tại trong kho (ID #{existing.id}).",
                existing,
            )

        new_acc = ProductAccount(
            product_id=product_id,
            account=parsed["account"],
            password=parsed["password"],
            two_factor=parsed["two_factor"],
            raw_data=parsed["raw_data"],
            status="AVAILABLE",
            note=note or parsed.get("note"),
        )
        db.add(new_acc)
        db.flush()

        # Update product stock quantity
        AccountService.sync_product_stock(db, product_id)

        return True, "Thêm tài khoản vào kho thành công.", new_acc

    @staticmethod
    def add_accounts_bulk(
        db: Session,
        product_id: int,
        text_content: str,
        note: Optional[str] = None,
    ) -> dict:
        """Add multiple digital accounts from multiline text.

        Args:
            db: Database session.
            product_id: Target product ID.
            text_content: Multiline string containing accounts.
            note: Optional note to attach to added items.

        Returns:
            Dict containing operation summary (total, added, duplicates, errors).
        """
        product = db.query(Product).filter(Product.id == product_id).first()
        if not product:
            raise ValueError(f"Sản phẩm với ID #{product_id} không tồn tại.")

        lines = [
            line.strip()
            for line in text_content.strip().splitlines()
            if line.strip() and not line.strip().startswith(("#", "//"))
        ]
        added_count = 0
        duplicate_count = 0
        error_lines = []
        batch_accounts = set()

        for idx, line in enumerate(lines, start=1):
            parsed = AccountService.parse_account_line(line)
            is_valid, err_msg = AccountService.validate_account_item(parsed)
            if not is_valid:
                error_lines.append(f"Dòng {idx}: {err_msg}")
                continue

            acc_key = parsed["account"].lower()
            if acc_key in batch_accounts:
                duplicate_count += 1
                continue

            # Check existing available account in DB
            exists_in_db = (
                db.query(ProductAccount)
                .filter(
                    ProductAccount.product_id == product_id,
                    ProductAccount.account == parsed["account"],
                    ProductAccount.status == "AVAILABLE",
                )
                .first()
            )
            if exists_in_db:
                duplicate_count += 1
                continue

            batch_accounts.add(acc_key)
            new_acc = ProductAccount(
                product_id=product_id,
                account=parsed["account"],
                password=parsed["password"],
                two_factor=parsed["two_factor"],
                raw_data=parsed["raw_data"],
                status="AVAILABLE",
                note=note or parsed.get("note"),
            )
            db.add(new_acc)
            added_count += 1

        db.flush()
        # Update product stock
        AccountService.sync_product_stock(db, product_id)

        return {
            "total_lines": len(lines),
            "added": added_count,
            "duplicates": duplicate_count,
            "errors": error_lines,
            "new_stock": product.stock_quantity,
        }

    @staticmethod
    def get_available_accounts(
        db: Session,
        product_id: int,
        limit: int = 1,
    ) -> list[ProductAccount]:
        """Retrieve available accounts in FIFO order (oldest first)."""
        return (
            db.query(ProductAccount)
            .filter(
                ProductAccount.product_id == product_id,
                ProductAccount.status == "AVAILABLE",
            )
            .order_by(ProductAccount.id.asc())
            .limit(limit)
            .all()
        )

    @staticmethod
    def count_available_accounts(db: Session, product_id: int) -> int:
        """Count total available accounts in stock for a product."""
        return (
            db.query(func.count(ProductAccount.id))
            .filter(
                ProductAccount.product_id == product_id,
                ProductAccount.status == "AVAILABLE",
            )
            .scalar()
            or 0
        )

    @staticmethod
    def reserve_accounts_for_order(db: Session, order: Order) -> list[ProductAccount]:
        """Reserve available accounts for a pending order to prevent race conditions."""
        reserved = []
        for item in order.items:
            needed = item.quantity
            avail = AccountService.get_available_accounts(db, item.product_id, limit=needed)
            for acc in avail:
                acc.status = "RESERVED"
                acc.order_id = order.id
                reserved.append(acc)
            db.flush()
            AccountService.sync_product_stock(db, item.product_id)
        return reserved

    @staticmethod
    def release_reserved_order(db: Session, order_id: int) -> int:
        """Release any reserved accounts if an order is cancelled or timed out."""
        reserved_accs = (
            db.query(ProductAccount)
            .filter(ProductAccount.order_id == order_id, ProductAccount.status == "RESERVED")
            .all()
        )
        count = len(reserved_accs)
        prod_ids = set()
        for acc in reserved_accs:
            acc.status = "AVAILABLE"
            acc.order_id = None
            prod_ids.add(acc.product_id)
        db.flush()
        for pid in prod_ids:
            AccountService.sync_product_stock(db, pid)
        return count

    @staticmethod
    def allocate_accounts_for_order(
        db: Session,
        order: Order,
    ) -> list[ProductAccount]:
        """Allocate accounts to a paid order in FIFO order.

        Prioritizes accounts already reserved for this order, then grabs from AVAILABLE.
        Updates status of allocated accounts to 'SOLD' and records order_id and sold_at.
        """
        allocated: list[ProductAccount] = []

        for item in order.items:
            product_id = item.product_id
            needed_qty = item.quantity
            product = db.query(Product).filter(Product.id == product_id).first()

            # For Pato Netflix: stock is managed manually as integer, deduct directly on purchase
            if product_id == PATO_NETFLIX_PRODUCT_ID:
                if product:
                    product.stock_quantity = max(0, product.stock_quantity - needed_qty)
                    db.flush()
                continue

            has_managed = (
                db.query(func.count(ProductAccount.id))
                .filter(
                    ProductAccount.product_id == product_id,
                    ProductAccount.status.in_(["AVAILABLE", "RESERVED"]),
                )
                .scalar()
                > 0
            )

            if has_managed:
                # Check if accounts were already reserved for this order
                reserved_accounts = (
                    db.query(ProductAccount)
                    .filter(
                        ProductAccount.order_id == order.id,
                        ProductAccount.product_id == product_id,
                        ProductAccount.status == "RESERVED",
                    )
                    .all()
                )

                accounts_to_fulfill = list(reserved_accounts)

                # If not enough reserved, pull from AVAILABLE
                if len(accounts_to_fulfill) < needed_qty:
                    extra_needed = needed_qty - len(accounts_to_fulfill)
                    extra_accounts = AccountService.get_available_accounts(
                        db=db, product_id=product_id, limit=extra_needed
                    )
                    accounts_to_fulfill.extend(extra_accounts)

                for acc in accounts_to_fulfill:
                    acc.status = "SOLD"
                    acc.order_id = order.id
                    acc.sold_at = func.now()
                    allocated.append(acc)

                # Flush status updates to database before counting remaining stock
                db.flush()

                # Sync stock quantity for this product
                AccountService.sync_product_stock(db, product_id)
            else:
                if product:
                    product.stock_quantity = max(0, product.stock_quantity - needed_qty)
                    db.flush()

        db.flush()
        return allocated

    @staticmethod
    def sync_product_stock(db: Session, product_id: Optional[int] = None) -> None:
        """Sync Product.stock_quantity with available ProductAccount count.

        If product_id is None, syncs all products that have items in product_accounts.
        """
        query = db.query(Product)
        if product_id is not None:
            query = query.filter(Product.id == product_id)

        products = query.all()
        for p in products:
            # Exempt PATO_NETFLIX_PRODUCT_ID from auto-syncing with ProductAccount
            if p.id == PATO_NETFLIX_PRODUCT_ID:
                continue

            # Check if this product has accounts managed in product_accounts
            has_managed_accounts = (
                db.query(func.count(ProductAccount.id))
                .filter(
                    ProductAccount.product_id == p.id,
                    ProductAccount.status.in_(["AVAILABLE", "RESERVED"]),
                )
                .scalar()
                > 0
            )
            if has_managed_accounts:
                avail_count = (
                    db.query(func.count(ProductAccount.id))
                    .filter(
                        ProductAccount.product_id == p.id,
                        ProductAccount.status == "AVAILABLE",
                    )
                    .scalar()
                    or 0
                )
                p.stock_quantity = avail_count
        db.flush()

    @staticmethod
    def get_product_stats(db: Session, product_id: int) -> dict:
        """Get real-time stock and sales statistics for a specific product.
        
        Calculates:
        - available_stock: Count of AVAILABLE accounts in product_accounts or Product.stock_quantity.
        - sold_count: Total units sold through completed orders (Order.status='PAID') or SOLD accounts.
        - revenue: Estimated total revenue from sold units.
        """
        product = db.query(Product).filter(Product.id == product_id).first()
        if not product:
            return {
                "product_id": product_id,
                "product_name": "Unknown",
                "price": 0,
                "available_stock": 0,
                "sold_count": 0,
                "revenue": 0,
                "is_active": False,
            }

        # 1. Available Stock
        if product_id == PATO_NETFLIX_PRODUCT_ID:
            avail = product.stock_quantity
        else:
            has_managed = (
                db.query(func.count(ProductAccount.id))
                .filter(
                    ProductAccount.product_id == product_id,
                    ProductAccount.status.in_(["AVAILABLE", "RESERVED"]),
                )
                .scalar()
                or 0
            )
            if has_managed > 0:
                avail = (
                    db.query(func.count(ProductAccount.id))
                    .filter(ProductAccount.product_id == product_id, ProductAccount.status == "AVAILABLE")
                    .scalar()
                    or 0
                )
            else:
                avail = product.stock_quantity

        # 2. Sold Count (accurate count from actual PAID orders and SOLD accounts)
        sold_orders = (
            db.query(func.coalesce(func.sum(OrderItem.quantity), 0))
            .join(Order, Order.id == OrderItem.order_id)
            .filter(OrderItem.product_id == product_id, Order.status == "PAID")
            .scalar()
            or 0
        )
        sold_accounts = (
            db.query(func.count(ProductAccount.id))
            .filter(ProductAccount.product_id == product_id, ProductAccount.status == "SOLD")
            .scalar()
            or 0
        )
        sold = max(int(sold_orders), int(sold_accounts))
        revenue = sold * product.price

        return {
            "product_id": product_id,
            "product_name": product.name,
            "price": product.price,
            "available_stock": avail,
            "sold_count": sold,
            "revenue": revenue,
            "is_active": product.is_active,
        }

    @staticmethod
    def get_stock_summary(db: Session) -> list[dict]:
        """Get inventory overview for all active and registered products."""
        products = db.query(Product).order_by(Product.id.asc()).all()
        summary = []
        for p in products:
            summary.append(AccountService.get_product_stats(db, p.id))
        return summary

    @staticmethod
    def format_delivery_message(
        order: Order,
        accounts: list[ProductAccount],
        item_name: str,
    ) -> str:
        """Format customer delivery message containing real account credentials in user's language."""
        order_id = order.id
        total_amount = order.total_amount
        lang = getattr(order.user, "language", "vi") or "vi"
        is_en = lang == "en"

        p_name_lower = item_name.lower()
        is_netflix = "netflix" in p_name_lower
        if is_netflix:
            from datetime import datetime, timedelta
            order_date = getattr(order, "created_at", None) or datetime.now()
            expiry_date = order_date + timedelta(days=30)
            expiry_str = expiry_date.strftime("%d/%m/%Y")

            if is_en:
                if accounts:
                    link_blocks = []
                    for i, acc in enumerate(accounts, start=1):
                        url = acc.account or acc.raw_data
                        prefix = f"🔹 <b>LINK #{i}:</b>\n" if len(accounts) > 1 else ""
                        link_blocks.append(
                            f"{prefix}"
                            f"• 🔗 <b>New Login Link:</b> <a href=\"{url}\">{url}</a>\n"
                            f"• 📋 <b>Quick copy link:</b>\n<code>{url}</code>"
                        )
                    links_text = "\n\n".join(link_blocks)
                else:
                    links_text = "⚠️ <i>Your Netflix link is being generated by support. Please send your order ID to @Neitman275 or @Huyneko!</i>"

                return (
                    f"🍿 <b>ADMIN HAS ISSUED NEW NETFLIX LINK!</b>\n\n"
                    f"• 📋 <b>Order:</b> <b>#{order_id}</b>\n"
                    f"• 💵 <b>Amount Received:</b> <b>{total_amount:,.0f} VND</b>\n\n"
                    f"{links_text}\n\n"
                    f"━━━━━━━━━━━━━━━━━━\n"
                    f"⚠️ <b>Please access the link within 15 minutes of receipt.</b>\n"
                    f"⏳ <b>Remaining validity:</b> 30 days\n"
                    f"📅 <b>Expires on:</b> <b>{expiry_str}</b>\n"
                    f"ℹ️ <i>Validity is calculated from initial purchase date, not Next Billing Date.</i>\n\n"
                    f"Thank you for choosing our store! Enjoy your movies."
                )

            # Vietnamese (Default)
            if accounts:
                link_blocks = []
                for i, acc in enumerate(accounts, start=1):
                    url = acc.account or acc.raw_data
                    prefix = f"🔹 <b>LINK #{i}:</b>\n" if len(accounts) > 1 else ""
                    link_blocks.append(
                        f"{prefix}"
                        f"• 🔗 <b>Link đăng nhập mới:</b> <a href=\"{url}\">{url}</a>\n"
                        f"• 📋 <b>Sao chép link:</b>\n<code>{url}</code>"
                    )
                links_text = "\n\n".join(link_blocks)
            else:
                links_text = "⚠️ <i>Link Netflix đang được kỹ thuật viên tạo mới. Vui lòng gửi mã đơn #{order_id} cho @Neitman275 hoặc @Huyneko!</i>"

            return (
                f"🍿 <b>ADMIN ĐÃ CẤP LINK NETFLIX MỚI</b>\n\n"
                f"• 📋 <b>Đơn:</b> <b>#{order_id}</b>\n"
                f"• 💵 <b>Số tiền đã nhận:</b> <b>{total_amount:,.0f} VND</b>\n\n"
                f"{links_text}\n\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"⚠️ <b>Vui lòng truy cập link trong vòng 15 phút sau khi nhận.</b>\n"
                f"⏳ <b>HSD còn lại:</b> 30 ngày\n"
                f"📅 <b>Hết hạn:</b> <b>{expiry_str}</b>\n"
                f"ℹ️ <i>HSD được tính từ ngày mua ban đầu, không theo Next Billing Date.</i>\n\n"
                f"Cảm ơn bạn đã tin tưởng ủng hộ shop! Chúc bạn xem phim vui vẻ."
            )

        is_gmail = "gmail" in p_name_lower
        is_warranty_full = "bảo hành full" in p_name_lower or "full" in p_name_lower
        is_offer_trial = "offer" in p_name_lower or "trial" in p_name_lower
        if is_en:
            if is_gmail:
                login_block = "🌐 <b>LOGIN AT:</b> https://accounts.google.com\n\n"
                warranty_text = (
                    "⚠️ <b>IMPORTANT GMAIL USAGE NOTICE:</b>\n"
                    "• Multi-country, add card + wallet (verified).\n"
                    "• Live 15m - 48h from initial login.\n"
                    "• Buy 1 account to test before buying bulk quantities.\n"
                    "• <b>ONLY PURCHASE IF YOU KNOW HOW TO USE x10!</b>\n"
                    "• Format: <code>account | password</code> (no 2FA).\n"
                    "• 24/7 technical support: @Neitman275 or @Huyneko\n"
                )
            elif is_warranty_full:
                login_block = (
                    "🌐 <b>LOGIN AT:</b> https://chatgpt.com\n"
                    "📁 <b>GUIDE & RESOURCES:</b> https://drive.google.com/file/d/1DLAi2HqQCXiuDaHXC6lmafeHMdbCPiVx/view?pli=1\n\n"
                )
                warranty_text = (
                    "🛡️ <b>30-DAY FULL REPLACEMENT WARRANTY:</b>\n"
                    "• 1-to-1 immediate replacement guaranteed for 30 full days.\n"
                    "• 24/7 technical support: @Neitman275 or @Huyneko\n"
                )
            elif is_offer_trial:
                login_block = (
                    "🌐 <b>LOGIN AT:</b> https://chatgpt.com\n"
                    "📁 <b>GUIDE & RESOURCES:</b> https://drive.google.com/file/d/1DLAi2HqQCXiuDaHXC6lmafeHMdbCPiVx/view?pli=1\n\n"
                )
                warranty_text = (
                    "🎁 <b>OFFER TRIAL PLUS ACTIVATION GUIDE:</b>\n"
                    "• Account has a pre-qualified 1-month free trial offer for ChatGPT Plus.\n"
                    "• Log in to https://chatgpt.com and click the upgrade/trial banner to claim your free month.\n"
                    "• 24/7 technical support: @Neitman275 or @Huyneko\n"
                )
            else:
                login_block = (
                    "🌐 <b>LOGIN AT:</b> https://chatgpt.com\n"
                    "📁 <b>GUIDE & RESOURCES:</b> https://drive.google.com/file/d/1DLAi2HqQCXiuDaHXC6lmafeHMdbCPiVx/view?pli=1\n\n"
                )
                warranty_text = (
                    "⚠️ <b>USAGE NOTICE:</b>\n"
                    "• Dedicated private account. Please do not modify the original email.\n"
                    "• Contact support if you need login assistance.\n"
                )

            if accounts:
                account_blocks = []
                for i, acc in enumerate(accounts, start=1):
                    prefix = f"🔹 <b>ACCOUNT #{i}:</b>\n" if len(accounts) > 1 else ""
                    acc_lines = [prefix] if prefix else []
                    acc_lines.append(f"• 📧 <b>Email:</b> <code>{acc.account}</code>")
                    if acc.password:
                        acc_lines.append(f"• 🔑 <b>Password:</b> <code>{acc.password}</code>")
                    if acc.two_factor:
                        acc_lines.append(f"• 🔐 <b>2FA Secret Key:</b> <code>{acc.two_factor}</code>")

                    # Dòng copy nhanh định dạng: tài khoản | mật khẩu | 2FA
                    copy_parts = [acc.account or ""]
                    if acc.password:
                        copy_parts.append(acc.password)
                    if acc.two_factor:
                        copy_parts.append(acc.two_factor)
                    copy_string = " | ".join(copy_parts)
                    acc_lines.append(f"• 📋 <b>Quick copy:</b> <code>{copy_string}</code>")

                    account_blocks.append("\n".join(acc_lines))
                credentials_text = "\n\n".join(account_blocks)
            else:
                credentials_text = (
                    "⚠️ <i>Your account is being prepared by our technical staff.\n"
                    "Please send your order ID to @Neitman275 or @Huyneko to receive your account immediately!</i>"
                )

            return (
                f"🎉 <b>PAYMENT CONFIRMED FOR ORDER #{order_id}!</b>\n\n"
                f"📦 <b>Product:</b> <b>{item_name}</b>\n"
                f"💵 <b>Amount Received:</b> <b>{total_amount:,.0f} VND</b>\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"📦 <b>YOUR ACCOUNT CREDENTIALS:</b>\n\n"
                f"{credentials_text}\n\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"{login_block}"
                f"{warranty_text}\n"
                f"Thank you for choosing our store! Enjoy your premium experience."
            )

        # Vietnamese (Default)
        if is_gmail:
            login_block = "🌐 <b>ĐĂNG NHẬP TẠI:</b> https://accounts.google.com\n\n"
            warranty_text = (
                "⚠️ <b>LƯU Ý QUAN TRỌNG VỀ SẢN PHẨM GMAIL:</b>\n"
                "• Đa quốc gia, add thẻ + ví (có ver).\n"
                "• Live 15p - 48h tính từ lúc login.\n"
                "• Mua 1 tài khoản để test trước khi mua số lượng lớn (SLL).\n"
                "• <b>BIẾT DÙNG HẴNG MUA x10!</b>\n"
                "• Định dạng: <code>tài khoản | mật khẩu</code> (không có 2FA).\n"
                "• Kênh hỗ trợ kỹ thuật: @Neitman275 hoặc @Huyneko\n"
            )
        elif is_warranty_full:
            login_block = (
                "🌐 <b>ĐĂNG NHẬP TẠI:</b> https://chatgpt.com\n"
                "📁 <b>TÀI LIỆU & HƯỚNG DẪN:</b> https://drive.google.com/file/d/1DLAi2HqQCXiuDaHXC6lmafeHMdbCPiVx/view?pli=1\n\n"
            )
            warranty_text = (
                "🛡️ <b>CHÍNH SÁCH BẢO HÀNH FULL 30 NGÀY:</b>\n"
                "• Bảo hành 1 đổi 1 trong 30 ngày nếu xảy ra sự cố từ hệ thống OpenAI.\n"
                "• Kênh hỗ trợ kỹ thuật: @Neitman275 hoặc @Huyneko\n"
            )
        elif is_offer_trial:
            login_block = (
                "🌐 <b>ĐĂNG NHẬP TẠI:</b> https://chatgpt.com\n"
                "📁 <b>TÀI LIỆU & HƯỚNG DẪN:</b> https://drive.google.com/file/d/1DLAi2HqQCXiuDaHXC6lmafeHMdbCPiVx/view?pli=1\n\n"
            )
            warranty_text = (
                "🎁 <b>HƯỚNG DẪN KÍCH HOẠT TRIAL PLUS:</b>\n"
                "• Tài khoản có sẵn lời mời dùng thử (Offer) gói ChatGPT Plus miễn phí 1 tháng.\n"
                "• Đăng nhập tại https://chatgpt.com để xác nhận ưu đãi dùng thử.\n"
                "• Kênh hỗ trợ kỹ thuật: @Neitman275 hoặc @Huyneko\n"
            )
        else:
            login_block = (
                "🌐 <b>ĐĂNG NHẬP TẠI:</b> https://chatgpt.com\n"
                "📁 <b>TÀI LIỆU & HƯỚNG DẪN:</b> https://drive.google.com/file/d/1DLAi2HqQCXiuDaHXC6lmafeHMdbCPiVx/view?pli=1\n\n"
            )
            warranty_text = (
                "⚠️ <b>LƯU Ý SỬ DỤNG:</b>\n"
                "• Tài khoản kích hoạt sử dụng riêng biệt, vui lòng không đổi email gốc.\n"
                "• Liên hệ hỗ trợ nếu cần hướng dẫn đăng nhập.\n"
            )

        if accounts:
            account_blocks = []
            for i, acc in enumerate(accounts, start=1):
                prefix = f"🔹 <b>TÀI KHOẢN #{i}:</b>\n" if len(accounts) > 1 else ""
                acc_lines = [prefix] if prefix else []
                acc_lines.append(f"• 📧 <b>Email:</b> <code>{acc.account}</code>")
                if acc.password:
                    acc_lines.append(f"• 🔑 <b>Mật khẩu:</b> <code>{acc.password}</code>")
                if acc.two_factor:
                    acc_lines.append(f"• 🔐 <b>Mã 2FA Secret:</b> <code>{acc.two_factor}</code>")

                # Dòng copy nhanh định dạng: tài khoản | mật khẩu | 2FA
                copy_parts = [acc.account or ""]
                if acc.password:
                    copy_parts.append(acc.password)
                if acc.two_factor:
                    copy_parts.append(acc.two_factor)
                copy_string = " | ".join(copy_parts)
                acc_lines.append(f"• 📋 <b>Sao chép nhanh:</b> <code>{copy_string}</code>")

                account_blocks.append("\n".join(acc_lines))
            credentials_text = "\n\n".join(account_blocks)
        else:
            credentials_text = (
                "⚠️ <i>Tài khoản của bạn đang được kỹ thuật viên chuẩn bị kích hoạt thủ công.\n"
                "Vui lòng gửi mã đơn hàng cho @Neitman275 hoặc @Huyneko để nhận tài khoản ngay lập tức!</i>"
            )

        return (
            f"🎉 <b>THANH TOÁN THÀNH CÔNG ĐƠN HÀNG #{order_id}!</b>\n\n"
            f"📦 <b>Sản phẩm:</b> <b>{item_name}</b>\n"
            f"💵 <b>Số tiền đã nhận:</b> <b>{total_amount:,.0f} VND</b>\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"📦 <b>THÔNG TIN TÀI KHOẢN CỦA BẠN:</b>\n\n"
            f"{credentials_text}\n\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"{login_block}"
            f"{warranty_text}\n"
            f"Cảm ơn bạn đã tin tưởng ủng hộ shop! Chúc bạn có trải nghiệm tuyệt vời cùng dịch vụ."
        )
