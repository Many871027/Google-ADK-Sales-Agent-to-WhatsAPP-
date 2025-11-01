# =============================================================================
# Módulo de Herramientas del Carrito de Compras (Versión con Aislamiento Multi-Tenencia)
#
# Contiene la lógica de negocio para crear, modificar y visualizar
# el carrito de compras (órdenes pendientes) de un cliente, asegurando
# que cada carrito está aislado por negocio.
# =============================================================================

import logging
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, delete
from sqlalchemy.orm import selectinload

import models
from agents.tools.product_tools import buscar_producto

logger = logging.getLogger(__name__)

async def _get_or_create_pending_order_and_customer(
    business_id: int, customer_phone: str, db: AsyncSession
) -> models.Order:
    """
    Función de utilidad interna para encontrar o crear un cliente y su orden pendiente.
    Esta es la lógica central para la persistencia del carrito, ahora con aislamiento por negocio.
    """
    # 1. Encuentra o crea al cliente (esta lógica no cambia)
    customer_stmt = select(models.Customer).where(models.Customer.phone_number == customer_phone)
    customer_result = await db.execute(customer_stmt)
    customer = customer_result.scalars().first()

    if not customer:
        customer = models.Customer(phone_number=customer_phone)
        db.add(customer)
        await db.flush()

    # --- INICIO DE LA CORRECCIÓN ---
    # 2. Encuentra una orden pendiente para este cliente EN ESTE NEGOCIO ESPECÍFICO.
    order_stmt = select(models.Order).where(
        models.Order.customer_id == customer.id,
        models.Order.business_id == business_id, # <-- FILTRO CRÍTICO AÑADIDO
        models.Order.status == 'pending'
    )
    # --- FIN DE LA CORRECCIÓN ---
    order_result = await db.execute(order_stmt)
    order = order_result.scalars().first()

    if not order:
        order = models.Order(
            customer_id=customer.id,
            business_id=business_id, # <-- AÑADIDO AL CREAR LA ORDEN
            status='pending',
            total_price=0.0
        )
        db.add(order)
        await db.flush()

    return order

# El resto de las funciones ('agregar_al_carrito' y 'ver_carrito') no necesitan
# cambios, ya que dependen de la función corregida de arriba para obtener el
# contexto correcto de la orden.

async def agregar_al_carrito(
    nombre_producto: str,
    cantidad: float, # Permitimos float para manejar kilos (ej: 0.5)
    business_id: int,
    customer_phone: str,
    db: AsyncSession,
) -> dict:
    """
    Agrega una cantidad específica de un producto al carrito de compras
    (orden pendiente) del cliente para un negocio específico.
    """
    logger.info(f"Intentando agregar {cantidad} de '{nombre_producto}' al carrito.")

    product_search_result = await buscar_producto(
        nombre_producto=nombre_producto, business_id=business_id, db=db
    )

    if product_search_result.get("status") != "success":
        return product_search_result

    product_details = product_search_result.get("product_details")

    if not product_details or "id" not in product_details or "price" not in product_details:
        logger.error(f"La búsqueda de '{nombre_producto}' devolvió detalles incompletos: {product_details}")
        return {"status": "error", "message": "No pude obtener los detalles completos del producto para agregarlo."}

    product_id = product_details["id"]
    price = product_details["price"]
    product_name = product_details["name"]

    try:
        order = await _get_or_create_pending_order_and_customer(business_id, customer_phone, db)

        item_stmt = select(models.OrderItem).where(
            models.OrderItem.order_id == order.id,
            models.OrderItem.product_id == product_id
        )
        item_result = await db.execute(item_stmt)
        existing_item = item_result.scalars().first()

        if existing_item:
            existing_item.quantity += cantidad
        else:
            new_item = models.OrderItem(
                order_id=order.id,
                product_id=product_id,
                quantity=cantidad,
                price_at_purchase=price
            )
            db.add(new_item)

        await db.flush()
        total_stmt = select(func.sum(models.OrderItem.quantity * models.OrderItem.price_at_purchase)).where(
            models.OrderItem.order_id == order.id
        )
        total_result = await db.execute(total_stmt)
        new_total = total_result.scalar() or 0.0
        order.total_price = new_total
        
        await db.commit()

        return {
            "status": "success",
            "message": f"¡Listo! Agregué {cantidad}x '{product_name}' a tu carrito. El total de tu pedido ahora es de ${new_total:.2f}."
        }
    except Exception as e:
        await db.rollback()
        logger.error(f"Error de base de datos al agregar al carrito: {e}", exc_info=True)
        return {"status": "error", "message": "Tuve un problema al intentar agregar el producto a tu pedido."}

async def remover_del_carrito(
    nombre_producto: str,
    business_id: int,
    customer_phone: str,
    db: AsyncSession,
) -> dict:
    """
    Elimina un producto por completo del carrito de compras del cliente.
    """
    logger.info(f"Intentando remover '{nombre_producto}' del carrito.")
    
    try:
        order = await _get_or_create_pending_order_and_customer(business_id, customer_phone, db)

        # Usamos la herramienta 'buscar_producto' para encontrar el ID del producto de forma flexible.
        product_search = await buscar_producto(nombre_producto, business_id, db)
        if product_search.get("status") != "success":
            return {"status": "error", "message": f"No encontré el producto '{nombre_producto}' en tu carrito."}

        product_id = product_search["product_details"]["id"]
        product_name = product_search["product_details"]["name"]

        # Buscamos el ítem en la orden actual
        stmt = select(models.OrderItem).where(
            models.OrderItem.order_id == order.id,
            models.OrderItem.product_id == product_id
        )
        result = await db.execute(stmt)
        item_to_delete = result.scalars().first()

        if not item_to_delete:
            return {"status": "error", "message": f"El producto '{product_name}' no se encuentra en tu carrito."}

        # Eliminamos el ítem
        await db.delete(item_to_delete)
        await db.flush()

        # Recalculamos el total
        total_stmt = select(func.sum(models.OrderItem.quantity * models.OrderItem.price_at_purchase)).where(
            models.OrderItem.order_id == order.id
        )
        total_result = await db.execute(total_stmt)
        new_total = total_result.scalar() or 0.0
        order.total_price = new_total

        await db.commit()
        return {"status": "success", "message": f"He eliminado '{product_name}' de tu carrito. El nuevo total es de ${new_total:.2f}."}

    except Exception as e:
        await db.rollback()
        logger.error(f"Error al remover del carrito: {e}", exc_info=True)
        return {"status": "error", "message": "Tuve un problema al intentar remover el producto de tu pedido."}


async def modificar_cantidad(
    nombre_producto: str,
    nueva_cantidad: float,
    business_id: int,
    customer_phone: str,
    db: AsyncSession,
) -> dict:
    """
    Modifica la cantidad de un producto existente en el carrito. Si la cantidad es 0, lo elimina.
    """
    logger.info(f"Intentando modificar la cantidad de '{nombre_producto}' a {nueva_cantidad}.")

    # Si la nueva cantidad es cero o menos, simplemente removemos el producto.
    if nueva_cantidad <= 0:
        return await remover_del_carrito(nombre_producto, business_id, customer_phone, db)

    try:
        order = await _get_or_create_pending_order_and_customer(business_id, customer_phone, db)

        product_search = await buscar_producto(nombre_producto, business_id, db)
        if product_search.get("status") != "success":
            return {"status": "error", "message": f"No encontré el producto '{nombre_producto}' en tu carrito para modificarlo."}

        product_id = product_search["product_details"]["id"]
        product_name = product_search["product_details"]["name"]
        
        stmt = select(models.OrderItem).where(
            models.OrderItem.order_id == order.id,
            models.OrderItem.product_id == product_id
        )
        result = await db.execute(stmt)
        item_to_modify = result.scalars().first()

        if not item_to_modify:
            return {"status": "error", "message": f"El producto '{product_name}' no se encuentra en tu carrito."}

        # Modificamos la cantidad
        item_to_modify.quantity = nueva_cantidad
        await db.flush()

        # Recalculamos el total
        total_stmt = select(func.sum(models.OrderItem.quantity * models.OrderItem.price_at_purchase)).where(
            models.OrderItem.order_id == order.id
        )
        total_result = await db.execute(total_stmt)
        new_total = total_result.scalar() or 0.0
        order.total_price = new_total
        
        await db.commit()
        return {"status": "success", "message": f"Actualicé la cantidad de '{product_name}' a {nueva_cantidad}. El nuevo total de tu pedido es de ${new_total:.2f}."}

    except Exception as e:
        await db.rollback()
        logger.error(f"Error al modificar la cantidad: {e}", exc_info=True)
        return {"status": "error", "message": "Tuve un problema al intentar modificar la cantidad del producto."}


async def ver_carrito(
    business_id: int,
    customer_phone: str,
    db: AsyncSession,
) -> dict:
    """
    Muestra el contenido actual del carrito de compras (orden pendiente) del cliente.
    """
    try:
        order = await _get_or_create_pending_order_and_customer(business_id, customer_phone, db)

        stmt = select(models.Order).where(models.Order.id == order.id).options(
            selectinload(models.Order.items).selectinload(models.OrderItem.product)
        )
        result = await db.execute(stmt)
        order_with_items = result.scalars().first()

        if not order_with_items or not order_with_items.items:
            return {
                "status": "empty",
                "message": "Tu carrito de compras está vacío en este momento."
            }

        items_summary = []
        for item in order_with_items.items:
            items_summary.append(f"{item.quantity}x {item.product.name} (${item.price_at_purchase:.2f} c/u)")

        summary_text = "\n- ".join(items_summary)
        total = order_with_items.total_price or 0.0

        return {
            "status": "success",
            "message": f"En tu carrito tienes:\n- {summary_text}\n\nEl total es de ${total:.2f}."
        }
    except Exception as e:
        logger.error(f"Error de base de datos al ver el carrito: {e}", exc_info=True)
        return {"status": "error", "message": "Tuve un problema al intentar ver tu pedido."}
