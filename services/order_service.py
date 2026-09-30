"""Order service module.

Encapsulates business logic for user management, product inventory,
and order processing adhering to clean architecture principles.
"""

import urllib.parse
from typing import Optional
from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload

from config import BANK_ACCOUNT, BANK_ACCOUNT_NAME, BANK_ID
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
    def get_active_products(db: Session) -> list[Product]:
        """Retrieve all currently active products available in stock."""
        # Sync stock for products managed via ProductAccount first
        AccountService.sync_product_stock(db)
        return (
            db.query(Product)
            .filter(Product.is_active == True, Product.stock_quantity > 0)  # noqa: E712
            .order_by(Product.id.asc())
            .all()
        )

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

        # Check if product inventory is managed via ProductAccount table
        has_managed_accounts = (
            db.query(func.count(ProductAccount.id))
            .filter(ProductAccount.product_id == product_id)
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
                    f"Sản phẩm '{product.name}' chỉ còn {product.stock_quantity} gói trong kho."
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

        # Reserve accounts or deduct inventory
        if has_managed_accounts:
            AccountService.reserve_accounts_for_order(db, order)
        else:
            product.stock_quantity -= quantity
            db.flush()

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
    def generate_vietqr_url(order_id: int, total_amount: int) -> str:
        """Generate a VietQR payment image URL for NAPAS 24/7 banking transfer.

        When scanned with any Vietnamese banking app, it automatically fills:
        - Bank and Account Number
        - Account Name
        - Exact order amount
        - Transfer memo / description: 'DH {order_id}'
        """
        description = f"DH {order_id}"
        encoded_desc = urllib.parse.quote(description)
        encoded_name = urllib.parse.quote(BANK_ACCOUNT_NAME)
        return (
            f"https://img.vietqr.io/image/{BANK_ID}-{BANK_ACCOUNT}-compact2.png"
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

        if len(existing_products) == 0:
            chatgpt_plans = [
                Product(
                    name="ChatGPT Plus 1 Tháng (Bảo hành full)",
                    price=275000,
                    stock_quantity=999,
                    is_active=True,
                ),
                Product(
                    name="ChatGPT Plus 1 Tháng (Không bảo hành)",
                    price=145000,
                    stock_quantity=999,
                    is_active=True,
                ),
            ]
            db.add_all(chatgpt_plans)
            db.flush()
        else:
            # Deactivate any test products so they are no longer displayed in shop
            db.query(Product).filter(
                (Product.name.like("%Test%")) | (Product.price == 2000)
            ).update({"is_active": False}, synchronize_session=False)
            db.flush()

