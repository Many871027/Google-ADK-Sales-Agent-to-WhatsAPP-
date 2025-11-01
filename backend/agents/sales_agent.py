# =============================================================================
# Módulo del Agente Base (Plantilla)
#
# Define la configuración genérica y el conjunto completo de herramientas 
# disponibles para cualquier agente de ventas en el sistema.
# ===============================================================

from google.adk.agents import Agent

# Importamos TODAS las herramientas que el agente podría llegar a usar
from .tools.product_tools import buscar_producto
from .tools.cart_tools import agregar_al_carrito, ver_carrito

# Este es nuestro agente 'plantilla'.
# Su propósito es inicializar el Runner en el lifespan con una configuración base
# que incluye el registro de todas las capacidades del sistema.
root_agent = Agent(
    name="sales_agent_template",
    model="gemini-2.5-flash-lite",
    description="Agente de ventas base para la plataforma WHSP-AI.",
    
    # La instrucción puede ser genérica, ya que será sobreescrita en cada petición.
    instruction="Eres un asistente de ventas conversacional.",
    
    # La lista de tools define TODAS las capacidades que el sistema puede tener.
    # El agent_handler se encargará de crear wrappers contextualizados para estas tools.
    tools=[
        buscar_producto,
        agregar_al_carrito,
        ver_carrito,
        # Aquí añadiremos futuras herramientas como 'remover_del_carrito', etc.
    ],
)