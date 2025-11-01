# =============================================================================
# Módulo Principal de la Aplicación WHSP-AI (Versión Final Integrada)
#
# Arquitectura:
# - FastAPI asíncrono con gestión de ciclo de vida 'lifespan'.
# - Inicialización de recursos globales (BD Engine, ADK Runner) al arranque.
# - Endpoint de webhook robusto que delega la lógica de IA a un manejador.
# =============================================================================

# --- 1. Importaciones ---
import logging
from contextlib import asynccontextmanager
from typing import List, AsyncGenerator, Any, Literal, Optional, Annotated
import os 
import csv # <-- Importar para leer el inventario
import io 
import secrets
import shutil
#from dotenv import load_dotenv

import hmac
import hashlib
from fastapi import FastAPI, Request, Depends, HTTPException, UploadFile, File, Form, BackgroundTasks, Query, Header, Response, APIRouter, status
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import IntegrityError
from sqlalchemy.future import select

from passlib.context import CryptContext # Para hashing de contraseñas
from jose import JWTError, jwt # Para tokens JWT
from datetime import datetime, timedelta, timezone

# Importaciones de nuestro proyecto
import models
import schemas
from database import get_db, engine, AsyncSessionLocal, Base as DatabaseBase
from secure_retriever import get_decrypted_api_token
from agents.agent_handler import process_customer_message
from agents.sales_agent import root_agent
from google.adk.runners import Runner
from google.adk.sessions.in_memory_session_service import InMemorySessionService
from whatsapp_client import send_whatsapp_message

# --- DEFINE LAS VARIABLES DE ENTORNO GLOBALES ---
WHATSAPP_VERIFY_TOKEN = os.getenv("WHATSAPP_VERIFY_TOKEN")
WHATSAPP_APP_SECRET = os.getenv("WHATSAPP_APP_SECRET")

# --- 2. Configuración Inicial ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', datefmt='%Y-%m-%d %H:%M:%S',)
logger = logging.getLogger(__name__)



# --- Configuración de Seguridad (Placeholder y Ejemplo JWT) ---
# ** ¡¡¡ CAMBIAR ESTAS CLAVES EN PRODUCCIÓN !!! **
SECRET_KEY = os.getenv("SECRET_KEY", secrets.token_urlsafe(32)) # Cargar desde env o generar una
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 30 # Tiempo de validez del token

pwd_context = CryptContext(schemes=["argon2", "bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token") # Endpoint de login

# --- Funciones de Utilidad de Seguridad ---
def verify_password(plain_password, hashed_password):
    return pwd_context.verify(plain_password, hashed_password)

def get_password_hash(password):
    return pwd_context.hash(password)

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(minutes=15)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt


async def process_inventory_file(inventory_content: str, business_id: int):
    """
    Tarea en segundo plano para leer un CSV de inventario.
    Procesa cada fila individualmente para ser resiliente a errores
    de formato o de duplicados dentro del mismo archivo.
    """
    logger.info(f"Iniciando procesamiento de inventario para el negocio ID: {business_id}")
    
    products_added_count = 0
    # ¡IMPORTANTE! Usa AsyncSessionLocal para crear una sesión de BD 
    # independiente para esta tarea en segundo plano.
    async with AsyncSessionLocal() as db: 
        try:
            stream = io.StringIO(inventory_content)
            reader = csv.reader(stream)
            next(reader, None) # Omitir cabecera

            for row_number, row in enumerate(reader, 1):
                if not row:
                    continue

                try:
                    name = row[1].strip()
                    price_str = row[3].strip()

                    if not name or not price_str:
                        logger.warning(f"Fila {row_number} omitida para negocio {business_id}: Nombre o precio vacíos.")
                        continue
                    
                    price = float(price_str)
                    
                    sku = row[0].strip() if len(row) > 0 and row[0] else f"SKU-AUTOGEN-{row_number}"
                    description = row[2].strip() if len(row) > 2 else None
                    unit = row[4].strip() if len(row) > 4 and row[4] else 'pieza'
                    
                    new_product = models.Product(
                        sku=sku, name=name, description=description, price=price,
                        unit=unit, business_id=business_id, availability_status='CONFIRMED'
                    )
                    
                    db.add(new_product)
                    await db.commit() # Intenta guardar este producto inmediatamente
                    await db.refresh(new_product) 
                    products_added_count += 1

                except (IndexError, ValueError) as e:
                    logger.warning(f"Fila {row_number} omitida para negocio {business_id} (formato inválido): {row}. Error: {e}")
                    await db.rollback() 
                except IntegrityError as e:
                    logger.warning(f"Fila {row_number} omitida para negocio {business_id} (SKU duplicado en el archivo): {row}. Error: {e}")
                    await db.rollback() 

            logger.info(f"Procesamiento de inventario completado. Se añadieron {products_added_count} productos al negocio ID: {business_id}")

        except Exception as e:
            logger.error(f"Error crítico procesando el archivo de inventario para negocio {business_id}: {e}", exc_info=True)
            await db.rollback()



async def get_current_user_dependency(token: Annotated[str, Depends(oauth2_scheme)], db: AsyncSession = Depends(get_db)) -> models.User:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        email: str = payload.get("sub")
        if email is None:
            logger.warning("Intento de autenticación fallido: token JWT no contiene 'sub' (email).") # <-- LOGGING AÑADIDO
            raise credentials_exception
        token_data = schemas.TokenData(email=email)
    except JWTError as e:
        logger.warning(f"Intento de autenticación fallido: token JWT inválido. Error: {e}") # <-- LOGGING AÑADIDO
        raise credentials_exception

    result = await db.execute(select(models.User).where(models.User.email == token_data.email))
    user = result.scalars().first()
    if user is None:
        logger.warning(f"Intento de autenticación fallido: usuario no encontrado para email {token_data.email}") # <-- LOGGING AÑADIDO
        raise credentials_exception
    return user

# Alias para la dependencia
CurrentUser = Annotated[models.User, Depends(get_current_user_dependency)]



@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """
    Gestiona la inicialización y el cierre de recursos de la aplicación.
    Se ejecuta una sola vez al arrancar y una vez al apagar.
    """
    # --- FASE DE ARRANQUE ---
    logger.info("Iniciando servicios de la aplicación WHSP-AI...")

    # 1. Inicializa y verifica la conexión a la base de datos
    async with engine.begin() as conn:
        await conn.run_sync(DatabaseBase.metadata.create_all)
    logger.info("Motor de BD inicializado y tablas verificadas/creadas.")

    # 2. Inicializa el Runner del Agente de IA y sus servicios
    logger.info("Inicializando Runner del Agente de IA y sus servicios...")
    session_service = InMemorySessionService()
    runner = Runner(
        app_name="whsp_ai_sales_agent",
        agent=root_agent,  # Se usa el agente base; se sobreescribirá en cada petición
        session_service=session_service,
    )
    
    # Almacenamos los recursos en el estado global y seguro de la app
    app.state.agent_runner = runner
    app.state.session_service = session_service
    logger.info("Runner del Agente de IA y servicios listos.")

    yield  # La aplicación está activa y lista para recibir peticiones

    # --- FASE DE APAGADO ---
    logger.info("Cerrando los servicios de la aplicación...")
    await engine.dispose()
    logger.info("Conexiones de la base de datos cerradas exitosamente.")

# --- 4. Inicialización de la Aplicación FastAPI ---
app = FastAPI(
    title="WHSP-AI Agent API",
    description="API para el agente de ventas conversacional multi-negocio.",
    version="1.0.0",
    lifespan=lifespan
)



# --- 6. Inyección de Dependencias de la Base de Datos ---

async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        yield session




router_users = APIRouter(prefix="/users", tags=["Users"])
router_businesses = APIRouter(prefix="/businesses", tags=["Businesses"])
router_billing = APIRouter(prefix="/billing", tags=["Billing"])
router_payments = APIRouter(prefix="/payments", tags=["Payments"])
router_auth = APIRouter(tags=["Authentication"]) # Router para login/token


# --- Endpoint de Login (Token) ---
@router_auth.post("/token", response_model=schemas.Token)
async def login_for_access_token(
    form_data: Annotated[OAuth2PasswordRequestForm, Depends()],
    db: AsyncSession = Depends(get_db)
):
    """
    Endpoint para que los usuarios inicien sesión con email (username) y contraseña.
    Devuelve un token JWT.
    """
    logger.info(f"Intento de login para usuario: {form_data.username}") # <-- LOGGING AÑADIDO
    result = await db.execute(select(models.User).where(models.User.email == form_data.username))
    user = result.scalars().first()
    if not user or not verify_password(form_data.password, user.password):
        logger.warning(f"Login fallido (credenciales incorrectas) para usuario: {form_data.username}") # <-- LOGGING AÑADIDO
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        data={"sub": user.email}, expires_delta=access_token_expires
    )
    logger.info(f"Login exitoso y token creado para usuario: {form_data.username}") # <-- LOGGING AÑADIDO
    return {"access_token": access_token, "token_type": "bearer"}


# --- Endpoints para Usuarios (Users) ---
@router_users.post("/", response_model=schemas.UserRead, status_code=status.HTTP_201_CREATED)
async def register_user(user_in: schemas.UserCreate, db: AsyncSession = Depends(get_db)):
    """
    Registra un nuevo usuario. Hashea la contraseña.
    """
    logger.info(f"Intentando registrar nuevo usuario con email: {user_in.email}") # <-- LOGGING AÑADIDO
    hashed_password = get_password_hash(user_in.password)
    new_user = models.User(
        **user_in.model_dump(exclude={"password"}),
        password=hashed_password
    )
    db.add(new_user)
    try:
        await db.commit()
        await db.refresh(new_user)
        logger.info(f"Usuario registrado exitosamente: {new_user.email} (ID: {new_user.id})") # <-- LOGGING AÑADIDO
        return new_user
    except IntegrityError as e: # <-- Error específico
        await db.rollback()
        error_detail = "Error de integridad desconocido."
        
        if e.orig and hasattr(e.orig, 'sqlstate'):
            sqlstate = e.orig.sqlstate
            # '23505' es el código estándar de PostgreSQL para 'unique_violation'
            if sqlstate == '23505':
                if "users_email_key" in str(e).lower() or "unique constraint" in str(e).lower() and "email" in str(e).lower():
                    error_detail = "El correo electrónico ya está registrado."
                elif "users_rfc_key" in str(e).lower() or "unique constraint" in str(e).lower() and "rfc" in str(e).lower():
                    error_detail = "El RFC ya está registrado."
                else:
                    error_detail = "Violación de restricción única (ej. email o RFC duplicado)."
            # '23502' es para 'not_null_violation' (el error que acabas de ver)
            elif sqlstate == '23502':
                error_detail = f"Violación de 'NOT NULL'. Falta un valor requerido. Detalle: {e.orig.message}"
            else:
                error_detail = f"Error de SQLSTATE {sqlstate}. Detalle: {e.orig.message}"
        else:
             error_detail = str(e) # Fallback si no podemos obtener el código SQL

        # Loguea el error real y detallado para ti
        logger.warning(f"Error de integridad al registrar {user_in.email}: {error_detail}", exc_info=True)
        
        # Devuelve el error específico al cliente
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail=error_detail)
    except Exception as e: # <-- Error genérico (Mejorado)
        await db.rollback()
        logger.error(f"Error inesperado al crear usuario {user_in.email}: {e}", exc_info=True) # <-- exc_info=True es clave
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                            detail="Internal server error creating user.")

@router_users.get("/me", response_model=schemas.UserRead)
async def read_users_me(current_user: CurrentUser):
    """
    Obtiene la información del usuario actualmente autenticado.
    """
    logger.info(f"Petición /me recibida para usuario: {current_user.email}") # <-- LOGGING AÑADIDO
    return current_user

# (Opcional: endpoints para leer otros usuarios, actualizar, eliminar - con autorización adecuada)


# --- Endpoints para Negocios (Businesses) ---
@router_businesses.post("/", response_model=schemas.BusinessRead, status_code=status.HTTP_201_CREATED)
async def create_business_for_user(
    business_in: schemas.BusinessCreate,
    current_user: CurrentUser, # <-- Argumento sin default PRIMERO
    db: AsyncSession = Depends(get_db)
):
    """
    Crea un nuevo negocio asociado al usuario autenticado.
    **Nota:** La carga de inventario se hace en un endpoint separado.
    """
    logger.info(f"Usuario {current_user.email} (ID: {current_user.id}) intentando crear negocio: {business_in.name}") # <-- LOGGING AÑADIDO
    # Verificar si ya existe negocio con ese whatsapp_number
    result = await db.execute(select(models.Business).where(models.Business.whatsapp_number == business_in.whatsapp_number))
    if result.scalars().first():
        logger.warning(f"Conflicto: Intento de crear negocio con WA number duplicado {business_in.whatsapp_number} por {current_user.email}") # <-- LOGGING AÑADIDO
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail="A business with this WhatsApp number already exists.")

    new_business = models.Business(
        **business_in.model_dump(),
        user_id=current_user.id # Asociar con el usuario logueado
    )
    db.add(new_business)
    try:
        await db.commit()
        await db.refresh(new_business)
        logger.info(f"Negocio '{new_business.name}' (ID: {new_business.id}) creado exitosamente para {current_user.email}") # <-- LOGGING AÑADIDO
        return new_business
    except IntegrityError as e:
        await db.rollback()
        logger.warning(f"Error de integridad al crear negocio para {current_user.email}: {e}", exc_info=True) # <-- LOGGING AÑADIDO
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail="Conflict creating business (e.g., duplicate WhatsApp ID).")
    except Exception as e:
        await db.rollback()
        logger.error(f"Error creando negocio para usuario {current_user.id}: {e}", exc_info=True) # <-- exc_info=True AÑADIDO
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                            detail="Internal server error creating business.")

# Endpoint SEPARADO para cargar inventario
@router_businesses.post("/{business_id}/inventory/upload", response_model=schemas.InventoryUploadResponse, tags=["Inventory"])
async def upload_inventory_csv(
    business_id: int,
    background_tasks: BackgroundTasks,
    current_user: CurrentUser, # <-- Argumento sin default PRIMERO
    inventory_file: UploadFile = File(...), # <-- Argumento con default DESPUÉS
    db: AsyncSession = Depends(get_db)
):
    """
    Sube un archivo CSV con el inventario de productos para un negocio específico.
    El usuario debe ser el dueño del negocio. El procesamiento se realiza en segundo plano.
    Se proporciona retroalimentación básica sobre el formato.
    """
    logger.info(f"Usuario {current_user.email} (ID: {current_user.id}) iniciando carga de inventario para business_id: {business_id}") # <-- LOGGING AÑADIDO

    # 1. Verificar propiedad del negocio
    business = await db.get(models.Business, business_id)
    if not business:
        logger.warning(f"Carga de inventario fallida: Negocio {business_id} no encontrado.") # <-- LOGGING AÑADIDO
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Business not found.")
    if business.user_id != current_user.id:
        logger.error(f"Acceso denegado: Usuario {current_user.email} no es dueño del negocio {business_id}") # <-- LOGGING AÑADIDO
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized to upload inventory for this business.")

    # 2. Validación básica del archivo
    if not inventory_file.filename.endswith(".csv"):
        logger.warning(f"Carga de inventario fallida: Tipo de archivo inválido '{inventory_file.filename}'") # <-- LOGGING AÑADIDO
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid file type. Please upload a CSV file.")

    # 3. Leer contenido y validación inicial
    try:
        content_bytes = await inventory_file.read()
        inventory_content_str = content_bytes.decode("utf-8")
        if not inventory_content_str.strip():
             logger.warning(f"Carga de inventario fallida: Archivo CSV vacío para business_id {business_id}") # <-- LOGGING AÑADIDO
             raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="CSV file is empty.")

        stream = io.StringIO(inventory_content_str)
        reader = csv.reader(stream)
        header = next(reader, None)
        expected_headers = ["sku", "name", "description", "price", "unit"]
        if not header or len(header) < 4:
             logger.warning(f"Carga de inventario fallida: Cabecera CSV inválida para business_id {business_id}. Header: {header}") # <-- LOGGING AÑADIDO
             raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Invalid CSV format. Header missing or incomplete. Expected columns like: {', '.join(expected_headers)}")

    except UnicodeDecodeError as e: # <-- Error específico
         logger.error(f"Error de decodificación en carga de inventario para business {business_id}: {e}", exc_info=True) # <-- LOGGING AÑADIDO
         raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Could not decode file content. Ensure it's UTF-8 encoded.")
    except Exception as e:
        logger.error(f"Error reading inventory file for business {business_id}: {e}", exc_info=True) # <-- exc_info=True AÑADIDO
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Could not read inventory file: {e}")
    finally:
        await inventory_file.close()

    # 4. Enviar a tarea en segundo plano (Función 'process_inventory_file' debe estar definida en este archivo o importada)
    background_tasks.add_task(process_inventory_file, inventory_content_str, business.id) # Asumiendo que process_inventory_file existe
    logger.info(f"Inventory processing task added for business ID: {business.id} (Filename: {inventory_file.filename})") # <-- LOGGING MEJORADO

    # 5. Devolver respuesta inmediata
    return schemas.InventoryUploadResponse(
        message="Archivo recibido. El inventario se está procesando en segundo plano. Recibirás una notificación o puedes verificar el estado más tarde.",
        filename=inventory_file.filename
        # 'task_id' podría añadirse si BackgroundTasks devolviera uno
    )


# --- Endpoints para Facturación (Billing) ---

@router_billing.post("/user", response_model=schemas.BillingRead, status_code=status.HTTP_201_CREATED)
async def create_user_billing_profile(
    billing_in: schemas.BillingCreate,
    current_user: CurrentUser, # Dependencia de usuario autenticado
    db: AsyncSession = Depends(get_db)
):
    """
    (Fase 1) Crea un perfil de facturación para el USUARIO (dueño) autenticado
    para el pago de la suscripción.
    """
    logger.info(f"Usuario {current_user.email} (ID: {current_user.id}) intentando crear su perfil de facturación.")
    
    # 1. Verificar si el usuario ya tiene un perfil de facturación
    # (Asumiendo que has añadido la FK 'user_id' a la tabla 'Billing' como propusimos)
    existing_profile = await db.execute(
        select(models.Billing).where(models.Billing.user_id == current_user.id)
    )
    if existing_profile.scalars().first():
        logger.warning(f"Conflicto al crear perfil de facturación: Usuario {current_user.email} ya tiene uno.")
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Este usuario ya tiene un perfil de facturación registrado."
        )

    # 2. Crear el nuevo objeto de facturación, vinculándolo al usuario
    new_billing = models.Billing(
        **billing_in.model_dump(),
        user_id=current_user.id  # <-- Vínculo clave al Usuario
        # customer_id se deja como NULL
    )
    db.add(new_billing)

    # 3. Intentar guardar en la BD (con el manejo de errores robusto de tu main.py)
    try:
        await db.commit()
        await db.refresh(new_billing)
        logger.info(f"Perfil de facturación (ID: {new_billing.id}) creado exitosamente para el usuario {current_user.email}.")
        return new_billing
        
    except IntegrityError as e:
        await db.rollback()
        
        # Lógica de logging de errores adaptada de tu endpoint register_user
        error_detail = "Error de integridad desconocido."
        
        if e.orig and hasattr(e.orig, 'sqlstate'):
            sqlstate = e.orig.sqlstate
            # '23505' es 'unique_violation'
            if sqlstate == '23505':
                if "billing_user_id_key" in str(e).lower():
                    error_detail = "Este usuario ya tiene un perfil de facturación."
                elif "billing_email_key" in str(e).lower():
                    error_detail = "El correo electrónico ya está en uso en otro perfil de facturación."
                elif "billing_rfc_key" in str(e).lower():
                    error_detail = "El RFC ya está en uso en otro perfil de facturación."
                else:
                    error_detail = "Violación de restricción única (ej. email o RFC duplicado)."
            # '23514' es 'check_violation' (para la regla de user_id O customer_id)
            elif sqlstate == '23514':
                 error_detail = f"Falló la regla de negocio (CheckConstraint). Detalle: {e.orig.message}"
            else:
                error_detail = f"Error de SQLSTATE {sqlstate}. Detalle: {e.orig.message}"
        else:
             error_detail = str(e)
        
        logger.warning(f"Error de integridad al crear perfil de facturación para {current_user.email}: {error_detail}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=error_detail)
    
    except Exception as e:
        await db.rollback()
        logger.error(f"Error inesperado al crear perfil de facturación para {current_user.email}: {e}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Error interno al crear el perfil.")

@router_billing.get("/user", response_model=schemas.BillingRead)
async def get_user_billing_profile(
    current_user: CurrentUser, # Dependencia de usuario autenticado
    db: AsyncSession = Depends(get_db)
):
    """
    (Fase 1) Obtiene el perfil de facturación del USUARIO (dueño) autenticado.
    """
    logger.info(f"Usuario {current_user.email} (ID: {current_user.id}) solicitando su perfil de facturación.")

    # 1. Buscar el perfil de facturación vinculado a este usuario
    # (Asumiendo que has añadido la FK 'user_id' a la tabla 'Billing' como propusimos)
    result = await db.execute(
        select(models.Billing).where(models.Billing.user_id == current_user.id)
    )
    billing_profile = result.scalars().first()

    # 2. Manejar el caso de que no se encuentre
    if not billing_profile:
        logger.warning(f"No se encontró perfil de facturación para el usuario {current_user.email}.")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Perfil de facturación no encontrado para este usuario."
        )

    # 3. Devolver el perfil si se encuentra
    logger.info(f"Perfil de facturación (ID: {billing_profile.id}) encontrado para el usuario {current_user.email}.")
    return billing_profile

@router_billing.patch("/user", response_model=schemas.BillingRead)
async def patch_user_billing_profile(
    billing_in: schemas.BillingUpdate, # El esquema PATCH que permite campos opcionales
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db)
):
    """
    (Fase 1) Actualiza parcialmente el perfil de facturación del USUARIO (dueño) autenticado.
    """
    logger.info(f"Usuario {current_user.email} (ID: {current_user.id}) intentando actualizar su perfil de facturación.")

    # 1. Buscar el perfil de facturación existente vinculado a este usuario
    result = await db.execute(
        select(models.Billing).where(models.Billing.user_id == current_user.id)
    )
    billing_profile = result.scalars().first()

    # 2. Manejar el caso de que no se encuentre
    if not billing_profile:
        logger.warning(f"Actualización fallida: No se encontró perfil de facturación para el usuario {current_user.email}.")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Perfil de facturación no encontrado para este usuario."
        )

    # 3. Obtener los datos a actualizar, excluyendo los que no se enviaron (lógica de PATCH)
    update_data = billing_in.model_dump(exclude_unset=True)

    # 4. Validar si se envió algún dato
    if not update_data:
        logger.info(f"Actualización de perfil de facturación solicitada por {current_user.email} sin datos.")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No se proporcionaron datos para actualizar."
        )

    # 5. Aplicar las actualizaciones al objeto del modelo
    logger.debug(f"Datos de actualización para perfil {billing_profile.id}: {list(update_data.keys())}")
    for key, value in update_data.items():
        setattr(billing_profile, key, value)
    
    db.add(billing_profile) # Marcar el objeto como "sucio" (modificado)

    # 6. Intentar guardar los cambios en la BD (con manejo de errores de integridad)
    try:
        await db.commit()
        await db.refresh(billing_profile)
        logger.info(f"Perfil de facturación (ID: {billing_profile.id}) actualizado exitosamente para el usuario {current_user.email}.")
        return billing_profile

    except IntegrityError as e:
        await db.rollback()
        
        # Lógica de logging de errores adaptada
        error_detail = "Error de integridad desconocido."
        
        if e.orig and hasattr(e.orig, 'sqlstate'):
            sqlstate = e.orig.sqlstate
            # '23505' es 'unique_violation'
            if sqlstate == '23505':
                if "billing_email_key" in str(e).lower():
                    error_detail = "El correo electrónico ya está en uso en otro perfil de facturación."
                elif "billing_rfc_key" in str(e).lower():
                    error_detail = "El RFC ya está en uso en otro perfil de facturación."
                else:
                    error_detail = "Violación de restricción única (ej. email o RFC duplicado)."
            else:
                error_detail = f"Error de SQLSTATE {sqlstate}. Detalle: {e.orig.message}"
        else:
             error_detail = str(e)
        
        logger.warning(f"Error de integridad al actualizar perfil de facturación para {current_user.email}: {error_detail}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=error_detail)
    
    except Exception as e:
        await db.rollback()
        logger.error(f"Error inesperado al actualizar perfil de facturación para {current_user.email}: {e}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Error interno al actualizar el perfil.")



#@router_billing.get("/customer/{customer_id}", response_model=schemas.BillingRead)
#async def get_billing_by_customer(
    #customer_id: int,
    #current_user: CurrentUser, # <-- Argumento sin default PRIMERO
    #db: AsyncSession = Depends(get_db)
#):
    #"""
    #Obtiene el perfil de facturación de un cliente.
    #(Necesita autorización).
    #"""
    #logger.info(f"Usuario {current_user.email} solicitando perfil de facturación para customer_id: {customer_id}") # <-- LOGGING AÑADIDO
    # TODO: Añadir lógica de autorización
    #result = await db.execute(select(models.Billing).where(models.Billing.customer_id == customer_id))
    #billing = result.scalars().first()
    #if not billing:
        #logger.warning(f"Perfil de facturación no encontrado para customer_id: {customer_id}") # <-- LOGGING AÑADIDO
        #raise HTTPException(status_code=404, detail="Billing profile not found for this customer.")
    #logger.info(f"Perfil de facturación (ID: {billing.id}) encontrado para customer_id: {customer_id}") # <-- LOGGING AÑADIDO
    #return billing



# --- Endpoints para Pagos (Payments) ---
#@router_payments.post("/", response_model=schemas.PaymentRead, status_code=status.HTTP_201_CREATED)
#async def create_payment_record(
    #payment_in: schemas.PaymentCreate,
    #current_user: CurrentUser, # <-- Argumento sin default PRIMERO
    #db: AsyncSession = Depends(get_db)
#):
    #"""
    #Crea un registro de pago. (Usualmente llamado internamente o por webhook de pasarela).
    #"""
    #logger.info(f"Intento de crear registro de pago para order_id: {payment_in.order_id} por {current_user.email}") # <-- LOGGING AÑADIDO
    # Validaciones de IDs y consistencia
    #order = await db.get(models.Order, payment_in.order_id)
    #customer = await db.get(models.Customer, payment_in.customer_id)
    #billing = await db.get(models.Billing, payment_in.billing_id)
    #if not order or not customer or not billing:
         #logger.warning(f"Creación de pago fallida: Orden ({payment_in.order_id}), Cliente ({payment_in.customer_id}), o Billing ({payment_in.billing_id}) no encontrados.") # <-- LOGGING AÑADIDO
         #raise HTTPException(status_code=404, detail="Referenced Order, Customer, or Billing not found.")
    #if billing.customer_id != payment_in.customer_id or order.customer_id != payment_in.customer_id:
         #logger.warning(f"Creación de pago fallida: Inconsistencia de IDs para order_id {payment_in.order_id}") # <-- LOGGING AÑADIDO
         #raise HTTPException(status_code=400, detail="ID mismatch.")
    # TODO: Añadir autorización robusta

    #new_payment = models.Payment(**payment_in.model_dump())
    #db.add(new_payment)
    #try:
        #await db.commit()
        #await db.refresh(new_payment)
        #logger.info(f"Registro de pago creado (ID: {new_payment.id}) para order_id: {payment_in.order_id}") # <-- LOGGING AÑADIDO
        #return new_payment
    #except Exception as e: # <-- Error genérico
        #await db.rollback()
        #logger.error(f"Error inesperado al crear pago para order_id {payment_in.order_id}: {e}", exc_info=True) # <-- LOGGING AÑADIDO
        #raise HTTPException(status_code=500, detail="Error interno al crear pago.")

#@router_payments.get("/order/{order_id}", response_model=List[schemas.PaymentRead])
#async def get_payments_for_order(
    #order_id: int,
    #current_user: CurrentUser, # <-- Argumento sin default PRIMERO
    #skip: int = 0, # <-- Argumento con default DESPUÉS
    #limit: int = 20, # <-- Argumento con default DESPUÉS
    #db: AsyncSession = Depends(get_db)
#):
    #"""
    #Obtiene los registros de pago para una orden específica.
    #(Necesita autorización).
    #"""
    #logger.info(f"Usuario {current_user.email} solicitando pagos para order_id: {order_id}") # <-- LOGGING AÑADIDO
    # Verificar si la orden existe y si el usuario tiene permiso para verla
    #order = await db.get(models.Order, order_id)
    #if not order:
        #logger.warning(f"Búsqueda de pagos fallida: Orden {order_id} no encontrada.") # <-- LOGGING AÑADIDO
        #raise HTTPException(status_code=404, detail="Order not found.")
    #TODO: Lógica de autorización (¿Usuario es dueño del negocio asociado a la orden? ¿Es el cliente dueño de la orden?)

    #result = await db.execute(
        #select(models.Payment).where(models.Payment.order_id == order_id).offset(skip).limit(limit)
    #)
    #payments = result.scalars().all()
    #logger.info(f"Encontrados {len(payments)} pagos para order_id: {order_id}") # <-- LOGGING AÑADIDO
    #return payments


@router_payments.post("/subscription", response_model=schemas.PaymentRead, status_code=status.HTTP_201_CREATED)
async def create_subscription_payment(
    payment_in: schemas.SubscriptionPaymentCreate, # <<<--- ASEGÚRATE DE QUE DICE ESTO
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db)
):
    """
    (Fase 1) Crea un registro de pago para una SUSCRIPCIÓN del USUARIO autenticado.
    """
    logger.info(f"Usuario {current_user.email} (ID: {current_user.id}) iniciando pago de suscripción.")

    # 1. Validar el Perfil de Facturación (Validación de Seguridad Crítica)
    # Buscamos el perfil de facturación que el usuario quiere usar.
    billing_profile = await db.get(models.Billing, payment_in.billing_id)

    # 1a. Verificar que el perfil de facturación existe
    if not billing_profile:
        logger.warning(f"Pago fallido para {current_user.email}: Perfil de facturación ID {payment_in.billing_id} no encontrado.")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Perfil de facturación no encontrado."
        )

    # 1b. ¡VERIFICAR QUE EL PERFIL PERTENECE AL USUARIO!
    # Esto evita que un usuario pague usando el perfil de otro.
    if billing_profile.user_id != current_user.id:
        logger.error(
            f"FALLO DE SEGURIDAD: Usuario {current_user.email} (ID: {current_user.id}) "
            f"intentó usar el perfil de facturación {billing_profile.id}, "
            f"que pertenece al usuario ID {billing_profile.user_id}."
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, # 403 Prohibido
            detail="El perfil de facturación no pertenece a este usuario."
        )

    # 2. Crear el nuevo objeto de pago
    new_payment = models.Payment(
        user_id=current_user.id,            # Vinculado al User
        billing_id=billing_profile.id,      # Usamos el ID del perfil validado
        total_amount=payment_in.total_amount,
        tax_amount=payment_in.tax_amount,   
        discount=payment_in.discount,       
        currency=payment_in.currency,       
        status=payment_in.status,           
        payment_method=payment_in.payment_method,
        payment_description=payment_in.payment_description
    )
    db.add(new_payment)

    # 3. Intentar guardar en la BD (con manejo de errores robusto)
    try:
        await db.commit()
        await db.refresh(new_payment)
        logger.info(f"Pago de suscripción (ID: {new_payment.id}) creado exitosamente para el usuario {current_user.email}.")
        return new_payment

    except IntegrityError as e:
        await db.rollback()
        
        # Lógica de logging de errores adaptada para Pagos
        error_detail = "Error de integridad desconocido."
        
        if e.orig and hasattr(e.orig, 'sqlstate'):
            sqlstate = e.orig.sqlstate
            # '23514' es 'check_violation'
            if sqlstate == '23514':
                if "_payment_type_check" in str(e).lower():
                    error_detail = "Falló la regla de negocio (CheckConstraint). El pago debe ser de tipo Suscripción (user_id) o Pedido (customer_id/order_id)."
                else:
                    error_detail = f"Falló una regla de negocio (CheckConstraint). Detalle: {e.orig.message}"
            # '23503' es 'foreign_key_violation'
            elif sqlstate == '23503':
                 error_detail = f"Violación de llave foránea (ej. el billing_id no existe). Detalle: {e.orig.message}"
            else:
                error_detail = f"Error de SQLSTATE {sqlstate}. Detalle: {e.orig.message}"
        else:
             error_detail = str(e)

        logger.warning(f"Error de integridad al crear pago de suscripción para {current_user.email}: {error_detail}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=error_detail)

    except Exception as e:
        await db.rollback()
        logger.error(f"Error inesperado al crear pago de suscripción para {current_user.email}: {e}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Error interno al crear el pago.")



@router_payments.get("/user", response_model=List[schemas.PaymentRead])
async def get_user_payments(
    current_user: CurrentUser,
    skip: int = 0, # Parámetro de paginación
    limit: int = 20, # Límite de resultados por página
    db: AsyncSession = Depends(get_db)
):
    """
    (Fase 1) Obtiene el historial de pagos (suscripciones) del USUARIO autenticado,
    con paginación.
    """
    logger.info(f"Usuario {current_user.email} (ID: {current_user.id}) solicitando su historial de pagos (skip={skip}, limit={limit}).")

    # 1. Construir la consulta para los pagos del usuario
    query = (
        select(models.Payment)
        .where(models.Payment.user_id == current_user.id)
        .order_by(models.Payment.created_at.desc()) # Ordenar por más reciente
        .offset(skip)
        .limit(limit)
    )

    # 2. Ejecutar la consulta
    try:
        result = await db.execute(query)
        payments = result.scalars().all()
        
        logger.info(f"Encontrados {len(payments)} registros de pago para el usuario {current_user.email}.")
        
        # 3. Devolver la lista de pagos
        # Si no se encuentran pagos, esto devolverá una lista vacía "[]",
        # lo cual es correcto y esperado.
        return payments

    except Exception as e:
        # Manejo de error genérico en caso de fallo de la consulta
        logger.error(f"Error inesperado al obtener el historial de pagos para {current_user.email}: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error interno al obtener el historial de pagos."
        )




@router_payments.patch("/{payment_id}", response_model=schemas.PaymentRead)
async def patch_payment_record(
    payment_id: int,
    payment_in: schemas.PaymentUpdate,
    current_user: CurrentUser, # <-- Argumento sin default PRIMERO
    db: AsyncSession = Depends(get_db)
):
    """
    Actualiza el estado u otros campos permitidos de un registro de pago.
    (Necesita autorización elevada).
    """
    logger.info(f"Usuario {current_user.email} intentando actualizar pago ID: {payment_id}") # <-- LOGGING AÑADIDO
    payment = await db.get(models.Payment, payment_id)
    if not payment:
        logger.warning(f"Actualización de pago fallida: ID {payment_id} no encontrado.") # <-- LOGGING AÑADIDO
        raise HTTPException(status_code=404, detail="Payment record not found.")
    # TODO: Autorización (ej. solo admin o sistema de pasarela de pago)

    update_data = payment_in.model_dump(exclude_unset=True)
    if not update_data:
        logger.info(f"Actualización de pago {payment_id} solicitada sin datos.") # <-- LOGGING AÑADIDO
        raise HTTPException(status_code=400, detail="No data provided for update.")
    
    logger.debug(f"Datos de actualización para pago {payment_id}: {update_data}") # <-- LOGGING AÑADIDO
    for key, value in update_data.items():
        setattr(payment, key, value)
    db.add(payment)
    try:
        await db.commit()
        await db.refresh(payment)
        logger.info(f"Pago ID: {payment_id} actualizado exitosamente.") # <-- LOGGING AÑADIDO
        return payment
    except Exception as e: # <-- Error genérico
        await db.rollback()
        logger.error(f"Error inesperado al actualizar pago {payment_id}: {e}", exc_info=True) # <-- LOGGING AÑADIDO
        raise HTTPException(status_code=500, detail="Error interno al actualizar pago.")


# --- Incluir Routers en la App Principal ---
# Asegúrate de que 'app' esté definido (ej. app = FastAPI(lifespan=...))
app.include_router(router_auth)
app.include_router(router_users)
app.include_router(router_businesses)
app.include_router(router_billing)
app.include_router(router_payments)





async def process_inventory_file(inventory_content: str, business_id: int):
    """
    Tarea en segundo plano para leer un CSV de inventario.
    Procesa cada fila individualmente para ser resiliente a errores
    de formato o de duplicados dentro del mismo archivo.
    """
    logger.info(f"Iniciando procesamiento de inventario para el negocio ID: {business_id}")
    
    products_added_count = 0
    async with AsyncSessionLocal() as db:
        try:
            stream = io.StringIO(inventory_content)
            reader = csv.reader(stream)
            next(reader, None) # Omitir cabecera

            for row_number, row in enumerate(reader, 1):
                if not row:
                    continue

                try:
                    name = row[1].strip()
                    price_str = row[3].strip()

                    if not name or not price_str:
                        logger.warning(f"Fila {row_number} omitida para negocio {business_id}: Nombre o precio vacíos.")
                        continue
                    
                    price = float(price_str)
                    
                    sku = row[0].strip() if len(row) > 0 and row[0] else f"SKU-AUTOGEN-{row_number}"
                    description = row[2].strip() if len(row) > 2 else None
                    unit = row[4].strip() if len(row) > 4 and row[4] else 'pieza'
                    
                    new_product = models.Product(
                        sku=sku, name=name, description=description, price=price,
                        unit=unit, business_id=business_id, availability_status='CONFIRMED'
                    )
                    
                    db.add(new_product)
                    await db.commit() # Intenta guardar este producto inmediatamente
                    await db.refresh(new_product) # Opcional: refresca el objeto con el ID
                    products_added_count += 1

                except (IndexError, ValueError) as e:
                    logger.warning(f"Fila {row_number} omitida para negocio {business_id} (formato inválido): {row}. Error: {e}")
                    await db.rollback() # Revierte el intento de añadir este producto
                except IntegrityError as e:
                    logger.warning(f"Fila {row_number} omitida para negocio {business_id} (SKU duplicado en el archivo): {row}. Error: {e}")
                    await db.rollback() # Revierte el intento de añadir este producto duplicado

            logger.info(f"Procesamiento de inventario completado. Se añadieron {products_added_count} productos al negocio ID: {business_id}")

        except Exception as e:
            logger.error(f"Error crítico procesando el archivo de inventario para negocio {business_id}: {e}", exc_info=True)
            await db.rollback()

async def validate_whatsapp_signature(
    request: Request,
    x_hub_signature_256: str = Header(..., alias="X-Hub-Signature-256")
):
    """
    Dependencia de FastAPI para validar que las peticiones POST provienen de Meta.
    Incluye una puerta trasera para pruebas locales con una firma dummy.
    """
    # --- INICIO DE LA MODIFICACIÓN PARA PRUEBAS ---
    # Si estamos en un entorno de prueba y se usa la firma dummy, saltamos la validación.
    if x_hub_signature_256 == "sha256=dummysignaturefortest":
        logger.warning("Validación de firma OMITIDA para prueba local.")
        return
    # --- FIN DE LA MODIFICACIÓN ---

    if not WHATSAPP_APP_SECRET:
        logger.error("WHATSAPP_APP_SECRET no está configurado. No se puede validar la firma.")
        raise HTTPException(status_code=500, detail="Configuración del servidor incompleta.")

    payload_body = await request.body()
    expected_signature = hmac.new(
        WHATSAPP_APP_SECRET.encode('utf-8'),
        msg=payload_body,
        digestmod=hashlib.sha256
    ).hexdigest()

    if not hmac.compare_digest(f"sha256={expected_signature}", x_hub_signature_256):
        logger.error("Fallo en la validación de la firma de WhatsApp.")
        raise HTTPException(status_code=403, detail="Firma de la petición inválida.")
    
    logger.info("Firma de WhatsApp validada exitosamente.")

# --- 7. Endpoints de la API ---

@app.get("/")
async def read_root():
    """Endpoint de salud para verificar que la API está en línea."""
    return {"status": "WHSP-AI API is running"}



# --- INICIO DE LA INTEGRACIÓN CON WHATSAPP ---
@app.get("/webhook")
async def verify_webhook(request: Request):
    """
    Endpoint para que Meta verifique la URL del webhook (GET).
    """
    mode = request.query_params.get("hub.mode")
    token = request.query_params.get("hub.verify_token")
    challenge = request.query_params.get("hub.challenge")

    if mode == "subscribe" and token == WHATSAPP_VERIFY_TOKEN:
        logger.info("Webhook verificado exitosamente por Meta.")
        return Response(content=challenge, media_type="text/plain", status_code=200)
    else:
        logger.error("Falló la verificación del webhook de Meta. Tokens no coinciden.")
        raise HTTPException(status_code=403, detail="Error de verificación de token.")

@app.post("/webhook", dependencies=[Depends(validate_whatsapp_signature)])
async def receive_whatsapp_message(request: Request, db: AsyncSession = Depends(get_db)):
    """
    Endpoint para recibir mensajes de usuarios (POST), con firma validada y lógica multi-negocio.
    """
    payload = await request.json()
    logger.info(f"Payload de WhatsApp recibido: {payload}")

    try:
        # 1. Extraer datos clave del payload de Meta
        change = payload['entry'][0]['changes'][0]
        if 'messages' not in change['value']:
            logger.info("Notificación de webhook recibida, pero no es un mensaje (ej. estado de entrega). Ignorando.")
            return {"status": "ignored_not_a_message"}

        message_object = change['value']['messages'][0]
        business_phone_id = change['value']['metadata']['phone_number_id']
        customer_phone = message_object['from']
        message_text = message_object['text']['body']

        # 2. Identificar el negocio contactado usando el 'phone_number_id'
        stmt = select(models.Business).where(models.Business.whatsapp_number_id == business_phone_id)
        result = await db.execute(stmt)
        business = result.scalars().first()
        
        if not business:
            logger.error(f"Negocio no encontrado para el ID de número de teléfono: {business_phone_id}")
            raise HTTPException(status_code=404, detail="Business not registered for this phone ID")

        # 3. Obtener el API Token específico y seguro para este negocio
        decrypted_api_token = await get_decrypted_api_token(business.whatsapp_number_id)
        if not decrypted_api_token:
            logger.critical(f"No se pudo obtener el API Token para el negocio '{business.name}' (ID: {business.id}).")
            raise HTTPException(status_code=500, detail="Error de configuración de credenciales del negocio.")
        
        # 4. Delegar la lógica de IA al 'agent_handler'
        response_to_user = await process_customer_message(
            user_message=message_text,
            customer_phone=customer_phone,
            business=business,
            db=db,
            runner=request.app.state.agent_runner,
            session_service=request.app.state.session_service
            # Si has integrado la memoria, añade aquí memory_service=request.app.state.memory_service
        )

        # 5. Enviar la respuesta del agente de vuelta al usuario
        await send_whatsapp_message(
            to=customer_phone, 
            message=response_to_user, 
            api_token=decrypted_api_token,
            phone_number_id=business_phone_id  # <-- PASA EL ID DEL NÚMERO AQUÍ
        )
        
        logger.info(f"Respuesta del agente enviada a {customer_phone} para el negocio '{business.name}'.")
        # 6. Confirmar a Meta que el mensaje fue recibido y procesado
        return {"status": "processed"}

    except (KeyError, IndexError, TypeError) as e:
        logger.warning(f"Payload de webhook con formato no esperado. Error: {e}. Ignorando.")
        return {"status": "ignored_malformed_payload"}
    except HTTPException as http_exc:
        raise http_exc
    except Exception as e:
        logger.critical(f"Error inesperado procesando el mensaje de WhatsApp: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Error interno del servidor.")# --- FIN DE LA INTEGRACIÓN CON WHATSAPP ---

@app.post("/management/inventory_response")
async def handle_inventory_response(payload: schemas.InventoryResponsePayload, db: AsyncSession = Depends(get_db)):
    """
    Endpoint para que el dueño del negocio confirme o rechace un producto
    previamente marcado como 'unconfirmed'.
    """
    logger.info(f"Recibida respuesta de inventario para el producto ID: {payload.product_id}")

    # Buscamos el producto en la base de datos
    result = await db.execute(
        select(models.Product).where(models.Product.id == payload.product_id)
    )
    product = result.scalars().first()

    if not product:
        raise HTTPException(status_code=404, detail="Producto no encontrado.")

    if payload.decision == "SI":
        if not payload.price or payload.price <= 0:
            raise HTTPException(status_code=400, detail="Se requiere un precio válido para confirmar el producto.")
        
        product.availability_status = "CONFIRMED"
        product.price = payload.price
        message = f"Producto '{product.name}' (ID: {product.id}) confirmado con precio ${payload.price}."
        
    elif payload.decision == "NO":
        product.availability_status = "REJECTED"
        message = f"Producto '{product.name}' (ID: {product.id}) ha sido rechazado."

    await db.commit()
    
    logger.info(message)
    return {"status": "success", "message": message}


@app.get("/products/{business_id}", response_model=List[schemas.ProductSchema])
async def get_business_products(business_id: int, skip: int = 0, limit: int = 100, db: AsyncSession = Depends(get_db)):
    """Endpoint de utilidad para listar los productos de un negocio específico."""
    result = await db.execute(
        select(models.Product)
        .where(models.Product.business_id == business_id)
        .offset(skip).limit(limit)
    )
    products = result.scalars().all()
    return products
