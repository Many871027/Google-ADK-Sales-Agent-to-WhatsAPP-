# En schemas.py (o main.py)

from pydantic import BaseModel, EmailStr, Field, ConfigDict
from typing import Optional, List, Literal
from datetime import datetime
from fastapi import Form # Para datos de formulario

# --- Autenticación ---
class Token(BaseModel):
    access_token: str
    token_type: str

class TokenData(BaseModel):
    email: Optional[str] = None

# --- Usuarios ---
class UserBase(BaseModel):
    first_name: str = Field(..., min_length=1, max_length=255)
    last_name: str = Field(..., min_length=1, max_length=255)
    mothers_last_name: Optional[str] = Field(None, max_length=255)
    age: Optional[int] = Field(None, gt=0)
    rfc: Optional[str] = Field(None, max_length=20)
    address: Optional[str] = None
    email: EmailStr

class UserCreate(UserBase):
    password: str = Field(..., min_length=8, max_length=256)

class UserRead(UserBase):
    id: int
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)

# --- Negocios ---
class BusinessBase(BaseModel):
    name: str = Field(..., min_length=1)
    business_type: str
    personality_description: Optional[str] = None
    whatsapp_number: str = Field(..., min_length=10) # Asumiendo validación básica
    whatsapp_number_id: Optional[str] = None # Puede ser asignado después por Meta

class BusinessCreate(BusinessBase):
    pass # No requiere campos adicionales para la creación inicial

class BusinessRead(BusinessBase):
    id: int
    user_id: Optional[int] # ID del dueño
    model_config = ConfigDict(from_attributes=True)

# --- Facturación (Billing) ---
class BillingBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    email: EmailStr
    business_address: Optional[str] = None
    city: Optional[str] = Field(None, max_length=100)
    street: Optional[str] = Field(None, max_length=255)
    postal_code: Optional[str] = Field(None, max_length=20)
    country: Optional[str] = Field(None, max_length=100)
    rfc: Optional[str] = Field(None, max_length=20)

class BillingCreate(BillingBase):
    pass

class BillingRead(BillingBase):
    id: int
    created_at: datetime
    customer_id: Optional[int] = None
    user_id: Optional[int] = None
    model_config = ConfigDict(from_attributes=True)

class BillingUpdate(BaseModel): # Para PATCH
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    email: Optional[EmailStr] = None
    business_address: Optional[str] = None
    city: Optional[str] = Field(None, max_length=100)
    street: Optional[str] = Field(None, max_length=255)
    postal_code: Optional[str] = Field(None, max_length=20)
    country: Optional[str] = Field(None, max_length=100)
    rfc: Optional[str] = Field(None, max_length=20)


# --- Pagos (Payments) ---
class PaymentBase(BaseModel):
    total_amount: float = Field(..., gt=0)
    tax_amount: float = Field(0.0, ge=0)
    discount: float = Field(0.0, ge=0)
    currency: str = Field('MXN', max_length=10)
    status: str = Field('pending', max_length=50)
    payment_method: Optional[str] = Field(None, max_length=100)
    payment_description: Optional[str] = Field(None, max_length=255)
    

class PaymentCreate(PaymentBase):
    pass

# Esquema para crear un pago de SUSCRIPCIÓN (Fase 1)
class SubscriptionPaymentCreate(PaymentBase):
    billing_id: int

# Esquema para crear un pago de PEDIDO (Fase 2)
class OrderPaymentCreate(PaymentBase):
    order_id: int
    billing_id: int

class PaymentRead(PaymentBase):
    id: int
    created_at: datetime
    order_id: Optional[int] = None
    customer_id: Optional[int] = None
    user_id: Optional[int] = None
    billing_id: int
    model_config = ConfigDict(from_attributes=True)

class PaymentUpdate(BaseModel): # Para PATCH (ej. actualizar status)
    status: Optional[str] = Field(None, max_length=50)
    payment_method: Optional[str] = Field(None, max_length=100)
    

# --- Inventario ---
class InventoryUploadResponse(BaseModel):
    message: str
    filename: str
    task_id: Optional[str] = None # Si se procesa en background
    errors: Optional[List[str]] = None
    warnings: Optional[List[str]] = None



# --- 5. Modelos de Datos Pydantic (Validación de Entradas/Salidas) ---

class WebhookPayload(BaseModel):
    business_phone: str
    customer_phone: str
    message: str

class ProductSchema(BaseModel):
    id: int
    sku: str
    name: str
    price: float
    availability_status: str

    class Config:
        from_attributes = True

class InventoryResponsePayload(BaseModel):
    product_id: int
    decision: Literal["SI", "NO"]
    price: Optional[float] = None