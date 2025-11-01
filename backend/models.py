from sqlalchemy import (Column, Integer, String, Float, ForeignKey, 
                        create_engine, DateTime, UniqueConstraint, TEXT, CheckConstraint)
from sqlalchemy.orm import relationship, declarative_base
from sqlalchemy.sql import func

Base = declarative_base()

class Customer(Base):
    __tablename__ = 'customers'
    id = Column(Integer, primary_key=True)
    phone_number = Column(String, unique=True, nullable=False)
    name = Column(String)
    address = Column(String)
    
    orders = relationship("Order", back_populates="customer")
    billing_profile = relationship("Billing", back_populates="customer", uselist=False)
    payments = relationship("Payment", back_populates="customer")
    billing_profile = relationship("Billing", back_populates="customer", uselist=False)
    payments = relationship("Payment", back_populates="customer")
    
class Product(Base):
    __tablename__ = 'products'
    id = Column(Integer, primary_key=True)
    sku = Column(String, nullable=False)
    name = Column(String, nullable=False)
    description = Column(String)
    price = Column(Float, nullable=False)
    stock = Column(Integer, default=0)
    availability_status = Column(String, nullable=False, default='UNCONFIRMED')
    unit = Column(String, nullable=False, default='pieza')
    business_id = Column(Integer, ForeignKey('businesses.id'), nullable=False)
    business = relationship("Business", back_populates="products")
    __table_args__ = (
        UniqueConstraint('sku', 'business_id', name='_sku_business_uc'),
    )

class Order(Base):
    __tablename__ = 'orders'
    id = Column(Integer, primary_key=True)
    customer_id = Column(Integer, ForeignKey('customers.id'), nullable=False)
    business_id = Column(Integer, ForeignKey('businesses.id'), nullable=False)
    status = Column(String, default='pending')
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    total_price = Column(Float)
    
    # --- RELACIONES ACTUALIZADAS ---
    customer = relationship("Customer", back_populates="orders")
    items = relationship("OrderItem", back_populates="order")
    business = relationship("Business", back_populates="orders")
    # Relación uno-a-muchos con los pagos (un pedido puede tener varios intentos de pago)
    payments = relationship("Payment", back_populates="order")
    # --- FIN DE ACTUALIZACIÓN ---

class OrderItem(Base):
    __tablename__ = 'order_items'
    id = Column(Integer, primary_key=True)
    order_id = Column(Integer, ForeignKey('orders.id'), nullable=False)
    product_id = Column(Integer, ForeignKey('products.id'), nullable=False)
    quantity = Column(Float, nullable=False)
    price_at_purchase = Column(Float, nullable=False)
    order = relationship("Order", back_populates="items")
    product = relationship("Product")

class Business(Base):
    __tablename__ = 'businesses'
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    whatsapp_number = Column(String, unique=True, nullable=False)
    whatsapp_number_id = Column(String, unique=True, nullable=True)
    business_type = Column(String, nullable=False)
    personality_description = Column(String)

    # --- INICIO DE LA CORRECCIÓN ROBUSTA ---
    user_id = Column(Integer, ForeignKey('users.id'), nullable=True)
    # Especificamos explícitamente qué columna usar para la relación 'owner'
    owner = relationship("User", back_populates="businesses", foreign_keys=[user_id])
    # --- FIN DE LA CORRECCIÓN ---

    products = relationship("Product", back_populates="business")
    orders = relationship("Order", back_populates="business")
# =============================================================================
# --- INICIO DE NUEVAS TABLAS (USERS, BILLING, PAYMENTS) ---
# =============================================================================

class User(Base):
    """
    Representa a un usuario (empleado o dueño) asociado a un negocio.
    """
    __tablename__ = 'users'
    id = Column(Integer, primary_key=True)
    first_name = Column(String(255), nullable=False)
    last_name = Column(String(255), nullable=False)
    mothers_last_name = Column(String(255))
    age = Column(Integer)
    rfc = Column(String(20), unique=True)
    address = Column(TEXT)
    email = Column(String(255), nullable=False, unique=True)
    password = Column(TEXT, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    businesses = relationship("Business", back_populates="owner", foreign_keys="[Business.user_id]")
    billing_profile = relationship("Billing", back_populates="user", uselist=False)
    payments = relationship("Payment", back_populates="user")

class Billing(Base):
    """
    Almacena la información de facturación (datos fiscales) de un cliente.
    """
    __tablename__ = 'billing'
    id = Column(Integer, primary_key=True)
    customer_id = Column(Integer, ForeignKey('customers.id'), nullable=True, unique=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=True, unique=True)
    name = Column(String(255), nullable=False)
    email = Column(String(255), nullable=False, unique=True)
    business_address = Column(String)
    city = Column(String(100))
    street = Column(String(255))
    postal_code = Column(String(20))
    country = Column(String(100))
    rfc = Column(String(20), unique=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    
    customer = relationship("Customer", back_populates="billing_profile")
    user = relationship("User", back_populates="billing_profile") # <--- AÑADIR RELACIÓN
    payments = relationship("Payment", back_populates="billing_profile")

    __table_args__ = (
        CheckConstraint(
            '(customer_id IS NOT NULL AND user_id IS NULL) OR (customer_id IS NULL AND user_id IS NOT NULL)',
            name='_billing_owner_check'
        ),
    )
    



class Payment(Base):
    """
    Almacena el registro de una transacción financiera para un pedido.
    """
    __tablename__ = 'payments'
    id = Column(Integer, primary_key=True)
    order_id = Column(Integer, ForeignKey('orders.id'), nullable=True)
    customer_id = Column(Integer, ForeignKey('customers.id'), nullable=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=True)
    billing_id = Column(Integer, ForeignKey('billing.id'), nullable=False)
    
    total_amount = Column(Float, nullable=False) # Usamos Float por consistencia con 'price'
    tax_amount = Column(Float, default=0.0)
    discount = Column(Float, default=0.0)
    currency = Column(String(10), nullable=False, default='MXN')
    
    status = Column(String(50), nullable=False, default='pending')
    payment_description = Column(String(255), nullable=True)
    payment_method = Column(String(100))
    bank_reference = Column(String(255))
    authorization_code = Column(String(255))
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    
    order = relationship("Order", back_populates="payments")
    customer = relationship("Customer", back_populates="payments")
    user = relationship("User", back_populates="payments") # <--- AÑADIR RELACIÓN
    billing_profile = relationship("Billing", back_populates="payments")

    __table_args__ = (
        CheckConstraint(
            # Pago de Pedido (requiere order_id y customer_id)
            '(order_id IS NOT NULL AND customer_id IS NOT NULL AND user_id IS NULL)'
            ' OR '
            # Pago de Suscripción (requiere user_id)
            '(order_id IS NULL AND customer_id IS NULL AND user_id IS NOT NULL)',
            name='_payment_type_check'
        ),
    )

# =============================================================================
# --- FIN DE NUEVAS TABLAS ---
# =============================================================================