import os
import logging

# --- Configuración del Modelo ---
# Proveemos un valor por defecto, pero permitimos que se sobreescriba desde el entorno.
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash-lite")

# --- Configuración de Vertex AI (Cargada desde el Entorno) ---
# Estas variables son obligatorias para producción y no tienen valor por defecto.
VERTEX_PROJECT_ID = os.environ.get("VERTEX_PROJECT_ID")
VERTEX_LOCATION = os.environ.get("VERTEX_LOCATION")

# --- Validación de Variables Críticas ---
# Hacemos que la aplicación falle rápido si la configuración esencial no está presente.
if not all([VERTEX_PROJECT_ID, VERTEX_LOCATION]):
    raise ValueError(
        "Faltan variables de entorno críticas para Vertex AI. "
        "Asegúrate de que VERTEX_PROJECT_ID y VERTEX_LOCATION están definidos."
    ) 

# --- Configuración de Logging (Opcional, pero buena práctica) ---
LOG_LEVEL = logging.INFO