import logging
import os
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker, declarative_base
from typing import AsyncGenerator

logger = logging.getLogger(__name__)

# --- INICIO DE LA SOLUCIÓN ROBUSTA ---

# 1. Leer la variable de entorno.
raw_database_url = os.getenv("DATABASE_URL")

# 2. Imprimir el valor crudo para depuración.
print(f"DEBUG: DATABASE_URL (Cruda): '{raw_database_url}' (Tipo: {type(raw_database_url)})")

# 3. Sanear la entrada:
#    - Primero, verificamos si la variable existe.
#    - Luego, eliminamos espacios en blanco al inicio/final.
#    - Finalmente, eliminamos cualquier comilla simple o doble que la rodee.
if raw_database_url:
    DATABASE_URL = raw_database_url.strip().strip('"\'')
else:
    DATABASE_URL = None

# Imprimir el valor saneado para confirmar.
print(f"DEBUG: DATABASE_URL (Saneada): '{DATABASE_URL}' (Tipo: {type(DATABASE_URL)})")


# 4. Validar la variable saneada de forma estricta.
if not DATABASE_URL or not DATABASE_URL.startswith("postgresql+asyncpg://"):
    logger.critical(
        f"Configuración inválida: La variable de entorno DATABASE_URL es incorrecta o no está definida. "
        f"Valor final: '{DATABASE_URL}'"
    )
    raise ValueError("Configuración de base de datos inválida.")

# --- Inicialización de SQLAlchemy ---

try:
    # Creamos el motor asíncrono usando la URL correcta
    engine = create_async_engine(DATABASE_URL, echo=False) # echo=True para ver las queries SQL en la terminal

    # Creamos una fábrica de sesiones asíncronas
    AsyncSessionLocal = sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False
    )

    # Base declarativa de la que heredarán nuestros modelos
    Base = declarative_base()

except Exception as e:
    logger.critical(f"No se pudo configurar el motor de la base de datos: {e}", exc_info=True)
    # Salimos si no podemos conectar, ya que la app no puede funcionar.
    raise

# --- Dependencia para FastAPI ---

async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """
    Generador de sesiones de base de datos que se inyectará en los endpoints.
    """
    async with AsyncSessionLocal() as session:
        yield session
