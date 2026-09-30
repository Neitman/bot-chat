"""SQLAlchemy ORM models for Telegram Shop Bot.

Includes:
- User: Telegram user profiles and shipping information.
- Product: Catalog items, pricing (in VND), and inventory.
- Order: Customer orders, statuses, and total amounts.
- OrderItem: Individual line items within an order.
"""

from datetime import datetime
from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import relationship

from database.database import Base


class User(Base):
    """Telegram User model."""

    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    telegram_id = Column(BigInteger, unique=True, nullable=False, index=True)
    full_name = Column(String(255), nullable=True)
    phone = Column(String(50), nullable=True)
    address = Column(String(500), nullable=True)
    language = Column(String(10), default=None, nullable=True)  # 'vi' or 'en'
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    # Relationships
    orders = relationship("Order", back_populates="user", cascade="all, delete-orphan")

    def __repr__(self) -> str:
        return f"<User(id={self.id}, telegram_id={self.telegram_id}, name='{self.full_name}', lang='{self.language}')>"


class Product(Base):
    """Shop Product model."""

    __tablename__ = "products"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=False)
    price = Column(Integer, nullable=False)  # Price in VND
    stock_quantity = Column(Integer, default=0, nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)

    # Relationships
    order_items = relationship("OrderItem", back_populates="product")
    accounts = relationship("ProductAccount", back_populates="product", cascade="all, delete-orphan")

    def __repr__(self) -> str:
        return f"<Product(id={self.id}, name='{self.name}', price={self.price:,} VND, stock={self.stock_quantity})>"


class Order(Base):
    """Customer Order model."""

    __tablename__ = "orders"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    total_amount = Column(Integer, default=0, nullable=False)  # Total in VND
    status = Column(String(50), default="PENDING", nullable=False)  # PENDING, PAID, CANCELLED, SHIPPED
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    # Relationships
    user = relationship("User", back_populates="orders")
    items = relationship("OrderItem", back_populates="order", cascade="all, delete-orphan")
    delivered_accounts = relationship("ProductAccount", back_populates="order")

    def __repr__(self) -> str:
        return f"<Order(id={self.id}, user_id={self.user_id}, total={self.total_amount:,} VND, status='{self.status}')>"


class OrderItem(Base):
    """Line item in an Order."""

    __tablename__ = "order_items"

    id = Column(Integer, primary_key=True, index=True)
    order_id = Column(Integer, ForeignKey("orders.id"), nullable=False, index=True)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False, index=True)
    quantity = Column(Integer, default=1, nullable=False)
    price_at_buy = Column(Integer, nullable=False)  # Price per unit at purchase time in VND

    # Relationships
    order = relationship("Order", back_populates="items")
    product = relationship("Product", back_populates="order_items")

    def __repr__(self) -> str:
        return f"<OrderItem(id={self.id}, order_id={self.order_id}, product_id={self.product_id}, qty={self.quantity})>"


class ProductAccount(Base):
    """Digital account / inventory item for a Product.

    Stores credentials (account/email, password, 2FA secret key) and
    lifecycle status (AVAILABLE, SOLD, ERROR).
    """

    __tablename__ = "product_accounts"

    id = Column(Integer, primary_key=True, index=True)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False, index=True)

    # Credentials
    account = Column(String(255), nullable=True, index=True)  # Email or username (e.g. biradarguru37@googlemail.com)
    password = Column(String(255), nullable=True)  # Password (e.g. CHATLGBT9999)
    two_factor = Column(String(255), nullable=True)  # 2FA Secret Key (e.g. E6M7ATQ7QHEALOH7BU2RN6YZRQNBMBE6)
    raw_data = Column(Text, nullable=False)  # Full raw line formatted as entered

    # State tracking
    status = Column(String(50), default="AVAILABLE", nullable=False, index=True)  # AVAILABLE, SOLD, ERROR
    order_id = Column(Integer, ForeignKey("orders.id"), nullable=True, index=True)

    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    sold_at = Column(DateTime(timezone=True), nullable=True)
    note = Column(String(500), nullable=True)

    # Relationships
    product = relationship("Product", back_populates="accounts")
    order = relationship("Order", back_populates="delivered_accounts")

    def __repr__(self) -> str:
        return f"<ProductAccount(id={self.id}, product_id={self.product_id}, account='{self.account}', status='{self.status}')>"
