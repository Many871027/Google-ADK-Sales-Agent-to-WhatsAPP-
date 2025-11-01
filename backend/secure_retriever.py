# En backend/secure_retriever.py
import os
import asyncpg
import logging

logger = logging.getLogger(__name__)

ENCRYPTION_KEY = os.getenv("WHATSAPP_CREDENTIALS_SECRET_KEY")
SECURE_DB_URL_RAW = os.getenv("SECURE_DATABASE_URL")

async def get_decrypted_api_token(business_phone_id: str) -> str | None:
    """
    Se conecta a la base de datos segura y devuelve el API token descifrado
    buscando directamente por el phone_number_id.
    """
    if not all([ENCRYPTION_KEY, SECURE_DB_URL_RAW]):
        logger.error("Las variables de entorno para la base de datos segura no están configuradas.")
        return None

    compatible_db_url = SECURE_DB_URL_RAW.replace("+asyncpg", "")
    conn = None
    try:
        conn = await asyncpg.connect(compatible_db_url)
        
        # --- CÓDIGO OPTIMIZADO ---
        # Llamamos a la nueva función que solo requiere el phone_id y la clave.
        decrypted_token = await conn.fetchval(
            "SELECT secure_storage.get_decrypted_whatsapp_token($1, $2)",
            business_phone_id,
            ENCRYPTION_KEY
        )
        # --- FIN DE LA OPTIMIZACIÓN ---
        
        return decrypted_token
    except Exception as e:
        logger.error(f"No se pudo obtener el token descifrado para {business_phone_id}: {e}")
        return None
    finally:
        if conn:
            await conn.close()