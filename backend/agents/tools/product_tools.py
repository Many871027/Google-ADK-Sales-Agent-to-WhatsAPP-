# =============================================================================
# Módulo de Herramientas de Producto (Versión con Sintaxis Asíncrona Correcta)
# =============================================================================

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select  # <--- Importación clave para la nueva sintaxis
from difflib import get_close_matches
import models
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', datefmt='%Y-%m-%d %H:%M:%S',)
logger = logging.getLogger(__name__)

async def get_all_product_names(db: AsyncSession, business_id: int) -> list[str]:
    """Obtiene todos los nombres de productos para un negocio específico."""
    
    # --- CORRECCIÓN: Usar la sintaxis select() y await db.execute() ---
    stmt = select(models.Product.name).where(models.Product.business_id == business_id)
    result = await db.execute(stmt)
    # .scalars().all() extrae todos los resultados de la primera columna en una lista
    product_names = result.scalars().all()
    
    return [name.lower() for name in product_names]

async def buscar_producto(nombre_producto: str, business_id: int, db: AsyncSession) -> dict:
    """
    Busca un producto en la base de datos combinando búsqueda exacta y flexible.
    """
    # --- INICIO DE LA OPTIMIZACIÓN ---
    # Estrategia de búsqueda en dos pasos para máxima fiabilidad.

    # 1. Búsqueda con ILIKE: Potente para encontrar subcadenas.
    #    Ej: "jamon fud" encontrará "Jamón de Pavo Fud 250g".
    search_term = f"%{nombre_producto.replace(' ', '%')}%"
    stmt_ilike = select(models.Product).where(
        models.Product.business_id == business_id,
        models.Product.name.ilike(search_term)
    )
    result_ilike = await db.execute(stmt_ilike)
    producto = result_ilike.scalars().first()

    # 2. Búsqueda Fuzzy (si ILIKE falla): Buena para errores de tipeo.
    if not producto:
        product_names = await get_all_product_names(db, business_id=business_id)
        matches = get_close_matches(nombre_producto.lower(), product_names, n=1, cutoff=0.4)
        if matches:
            matched_name = matches[0]
            stmt_fuzzy = select(models.Product).where(
                models.Product.business_id == business_id,
                models.Product.name.ilike(matched_name)
            )
            result_fuzzy = await db.execute(stmt_fuzzy)
            producto = result_fuzzy.scalars().first()

    if not producto:
        return {"status": "error", "message": "Error interno al buscar el producto."}
    
    # --- INICIO DE LA CORRECCIÓN ---
    # Se reestructura la lógica para asegurar que el 'id' del producto
    # se incluya en 'product_details' en todos los casos donde el producto es encontrado.

    base_product_details = {
    "id": producto.id,
    "name": producto.name,
    "description": producto.description, 
    "status": producto.availability_status,
    "unit": producto.unit
}

    if producto.availability_status == 'CONFIRMED':
        # Añadir precio solo si está confirmado y es válido
        if producto.price is not None and producto.price > 0: # <-- Más robusto que solo <= 0
            base_product_details["price"] = float(producto.price)
            return {
                "status": "success",
                "message": f"¡Sí tenemos {producto.name}! Cuesta ${producto.price:.2f}. Descripción: {producto.description}", # <-- Añadir descripción al mensaje
                "product_details": base_product_details
            }
        else:
            # Producto confirmado pero sin precio válido (Error de datos o ingrediente $0)
            return {
                "status": "price_not_found", # Mantener este status
                "message": f"Encontré '{producto.name}' ({producto.description}), pero no tengo su precio ahora mismo.", # Mensaje actualizado
                "product_details": base_product_details # <-- Devolver detalles base SIN precio
            }

    elif producto.availability_status == 'OUT_OF_STOCK':
        return {
            "status": "out_of_stock",
            "message": f"Lo siento, por el momento se nos agotó '{producto.name}' ({producto.description}).", # Añadir descripción
            "product_details": base_product_details # Devolver detalles base
        }

    elif producto.availability_status == 'UNCONFIRMED':
        # La lógica de HITL que tenías en el wrapper ahora puede vivir aquí directamente
        logger.info(
            f"[HITL] Notificación para Negocio: Cliente preguntó por '{producto.name}' (ID: {producto.id}, Status: UNCONFIRMED). Revisar precio/disponibilidad."
        )
        return {
            "status": "unconfirmed",
            "message": f"Encontré '{producto.name}' ({producto.description}). Permíteme un momento para confirmar disponibilidad y precio.", # Añadir descripción
            "product_details": base_product_details # Devolver detalles base
        }

    else: # REJECTED u otro estado
        return {
            "status": "not_available",
            "message": f"Lo siento, no manejamos el producto '{producto.name}'.",
            "product_details": base_product_details # Incluir detalles por si acaso
        }