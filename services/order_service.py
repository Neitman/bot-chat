"""Order service module.

Encapsulates business logic for user management, product inventory,
and order processing adhering to clean architecture principles.
"""

import urllib.parse
from typing import Optional
from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload

from config import BANK_ACCOUNT, BANK_ACCOUNT_NAME, BANK_ID, PATO_NETFLIX_PRODUCT_ID
from database.models import Order, OrderItem, Product, ProductAccount, User
from services.account_service import AccountService


class OrderService:
    """Service handling catalog, customer, and order operations."""

    @staticmethod
    def get_or_create_user(
        db: Session,
        telegram_id: int,
        full_name: Optional[str] = None,
        phone: Optional[str] = None,
        address: Optional[str] = None,
    ) -> User:
        """Retrieve existing user or create a new user record."""
        user = db.query(User).filter(User.telegram_id == telegram_id).first()
        if not user:
            user = User(
                telegram_id=telegram_id,
                full_name=full_name,
                phone=phone,
                address=address,
            )
            db.add(user)
            db.flush()
        else:
            if full_name and user.full_name != full_name:
                user.full_name = full_name
                db.flush()
        return user

    @staticmethod
    def update_user_shipping(
        db: Session,
        telegram_id: int,
        phone: Optional[str] = None,
        address: Optional[str] = None,
    ) -> Optional[User]:
        """Update shipping details (phone and address) for a user."""
        user = db.query(User).filter(User.telegram_id == telegram_id).first()
        if user:
            if phone is not None:
                user.phone = phone
            if address is not None:
                user.address = address
            db.flush()
        return user

    @staticmethod
    def get_active_products(db: Session, in_stock_only: bool = False) -> list[Product]:
        """Retrieve all currently active products (optionally filtered by available stock)."""
        # Sync stock for products managed via ProductAccount first
        AccountService.sync_product_stock(db)
        query = db.query(Product).filter(Product.is_active == True)  # noqa: E712
        if in_stock_only:
            query = query.filter(Product.stock_quantity > 0)
        return query.order_by(Product.id.asc()).all()

    @staticmethod
    def get_product_by_id(db: Session, product_id: int) -> Optional[Product]:
        """Retrieve a specific product by its primary key ID."""
        AccountService.sync_product_stock(db, product_id)
        return db.query(Product).filter(Product.id == product_id).first()

    @staticmethod
    def create_order(
        db: Session,
        telegram_id: int,
        product_id: int,
        quantity: int,
        full_name: Optional[str] = None,
    ) -> Order:
        """Create a new customer order and line item, reserving inventory stock."""
        if quantity <= 0:
            raise ValueError("Số lượng đặt hàng phải lớn hơn 0.")

        product = db.query(Product).filter(Product.id == product_id).with_for_update().first()
        if not product or not product.is_active:
            raise ValueError("Sản phẩm không tồn tại hoặc đã ngừng kinh doanh.")

        # Check if product is Pato Netflix (managed manually as integer) or managed via ProductAccount table
        if product_id == PATO_NETFLIX_PRODUCT_ID:
            if product.stock_quantity < quantity:
                raise ValueError(
                    f"Sản phẩm '{product.name}' chỉ còn {product.stock_quantity} trong kho."
                )
        else:
            has_managed_accounts = (
                db.query(func.count(ProductAccount.id))
                .filter(
                    ProductAccount.product_id == product_id,
                    ProductAccount.status.in_(["AVAILABLE", "RESERVED"]),
                )
                .scalar()
                > 0
            )

            if has_managed_accounts:
                avail_accounts = AccountService.count_available_accounts(db, product_id)
                if avail_accounts < quantity:
                    raise ValueError(
                        f"Sản phẩm '{product.name}' chỉ còn {avail_accounts} tài khoản trong kho."
                    )
            else:
                if product.stock_quantity < quantity:
                    raise ValueError(
                        f"Sản phẩm '{product.name}' chỉ còn {product.stock_quantity} trong kho."
                    )

        # Ensure user exists
        user = OrderService.get_or_create_user(db=db, telegram_id=telegram_id, full_name=full_name)

        # Calculate totals
        total_amount = product.price * quantity

        # Create Order record
        order = Order(
            user_id=user.id,
            total_amount=total_amount,
            status="PENDING",
        )
        db.add(order)
        db.flush()

        # Create OrderItem record
        order_item = OrderItem(
            order_id=order.id,
            product_id=product.id,
            quantity=quantity,
            price_at_buy=product.price,
        )
        db.add(order_item)
        db.flush()

        # NOTE: Do NOT deduct inventory or reserve accounts here.
        # Stock deduction and account allocation happen strictly upon payment confirmation (PAID).

        return order

    @staticmethod
    def get_order_by_id(db: Session, order_id: int) -> Optional[Order]:
        """Retrieve order by its ID."""
        return db.query(Order).filter(Order.id == order_id).first()

    @staticmethod
    def update_order_status(db: Session, order_id: int, new_status: str) -> Optional[Order]:
        """Update status of an order."""
        order = db.query(Order).filter(Order.id == order_id).first()
        if order:
            order.status = new_status
            db.flush()
        return order

    @staticmethod
    def get_user_orders(db: Session, telegram_id: int) -> list[Order]:
        """Retrieve all orders placed by a specific Telegram user with items and accounts."""
        user = db.query(User).filter(User.telegram_id == telegram_id).first()
        if not user:
            return []
        return (
            db.query(Order)
            .options(
                joinedload(Order.items).joinedload(OrderItem.product),
                joinedload(Order.delivered_accounts),
            )
            .filter(Order.user_id == user.id)
            .order_by(Order.created_at.desc())
            .all()
        )

    @staticmethod
    def generate_vietqr_url(
        order_id: int,
        total_amount: int,
        bank_id: Optional[str] = None,
        account_no: Optional[str] = None,
        account_name: Optional[str] = None,
    ) -> str:
        """Generate a VietQR payment image URL for NAPAS 24/7 banking transfer.

        When scanned with any Vietnamese banking app, it automatically fills:
        - Bank and Account Number
        - Account Name
        - Exact order amount
        - Transfer memo / description: 'DH {order_id}'
        """
        b_id = bank_id or BANK_ID
        acc_no = account_no or BANK_ACCOUNT
        acc_name = account_name or BANK_ACCOUNT_NAME
        description = f"DH {order_id}"
        encoded_desc = urllib.parse.quote(description)
        encoded_name = urllib.parse.quote(acc_name)
        return (
            f"https://img.vietqr.io/image/{b_id}-{acc_no}-compact2.png"
            f"?amount={total_amount}&addInfo={encoded_desc}&accountName={encoded_name}"
        )

    @staticmethod
    def seed_initial_products(db: Session, reset_if_legacy: bool = True) -> None:
        """Seed ChatGPT Plus packages into database.

        If reset_if_legacy is True and older test products exist, replaces them
        with the requested ChatGPT Plus plans.
        """
        existing_products = db.query(Product).all()
        is_legacy = any("Áo" in p.name or "Quần" in p.name for p in existing_products)

        if is_legacy:
            # Delete order items and orders that reference legacy products
            db.query(OrderItem).delete()
            db.query(Order).delete()
            db.query(Product).delete()
            db.flush()
            existing_products = []

        default_plans = [
            {
                "name": "ChatGPT Plus 1 Tháng (Bảo hành full)",
                "price": 270000,
                "is_active": False,
            },
            {
                "name": "ChatGPT Plus 1 Tháng (Không bảo hành)",
                "price": 150000,
                "is_active": False,
            },
            {
                "name": "Tài khoản ChatGPT có offer trial Plus free 1 tháng",
                "price": 30000,
                "is_active": True,
            },
            {
                "name": "Gmail Đa Quốc Gia (Add thẻ + ví, Live 15p - 48h)",
                "price": 5000,
                "is_active": True,
            },
            {
                "name": "Link netflix 4k + HDR 30 ngày  (KBH)",
                "price": 4000,
                "is_active": True,
            },
        ]

        existing_map = {p.name.lower(): p for p in existing_products}
        for plan in default_plans:
            plan_name = plan["name"]
            plan_price = plan["price"]
            target_active = plan.get("is_active", True)
            matched_prod = existing_map.get(plan_name.lower())

            if not matched_prod:
                for exist_p in existing_products:
                    p_lower = exist_p.name.lower()
                    if ("offer" in plan_name.lower() and "offer" in p_lower) or \
                       ("bảo hành full" in plan_name.lower() and "bảo hành full" in p_lower) or \
                       ("không bảo hành" in plan_name.lower() and "không bảo hành" in p_lower) or \
                       ("gmail" in plan_name.lower() and "gmail" in p_lower) or \
                       ("netflix" in plan_name.lower() and "netflix" in p_lower):
                        matched_prod = exist_p
                        break

            if matched_prod:
                if matched_prod.name != plan_name:
                    matched_prod.name = plan_name
                if matched_prod.price != plan_price:
                    matched_prod.price = plan_price
                if matched_prod.is_active != target_active:
                    matched_prod.is_active = target_active
            else:
                new_prod = Product(
                    name=plan_name,
                    price=plan_price,
                    stock_quantity=0,
                    is_active=target_active,
                )
                db.add(new_prod)

        # Deactivate any test products so they are no longer displayed in shop
        db.query(Product).filter(
            (Product.name.like("%Test%")) | (Product.price == 2000)
        ).update({"is_active": False}, synchronize_session=False)
        db.flush()

