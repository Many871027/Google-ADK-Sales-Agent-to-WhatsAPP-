import httpx
import os
import logging

logger = logging.getLogger(__name__)

async def send_whatsapp_message(to: str, message: str, api_token: str, phone_number_id: str): # <-- 1. AÑADE 'phone_number_id'
    """
    Envía un mensaje de texto a un número de WhatsApp usando credenciales específicas.
    """
    # 2. Valida los argumentos recibidos directamente.
    if not api_token or not phone_number_id:
        logger.error("Faltan el API Token o el Phone Number ID para enviar el mensaje.")
        return

    # 3. Construye la URL dinámicamente con el ID recibido.
    url = f"https://graph.facebook.com/v18.0/{phone_number_id}/messages"
    headers = {
        "Authorization": f"Bearer {api_token}",
        "Content-Type": "application/json",
    }
    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "text": {"body": message},
    }

    async with httpx.AsyncClient() as client:
        try:
            # Usa la URL dinámica.
            response = await client.post(url, json=payload, headers=headers)
            response.raise_for_status()
            logger.info(f"Mensaje enviado exitosamente a {to}. Respuesta de Meta: {response.json()}")
        except httpx.HTTPStatusError as e:
            logger.error(f"Error de API al enviar mensaje a {to}: {e.response.text}")
        except Exception as e:
            logger.error(f"Error inesperado al enviar mensaje a {to}: {e}")