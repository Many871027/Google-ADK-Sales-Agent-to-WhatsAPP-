# En backend/agents/agent_handler.py

import logging
import time
import json # Importar json para serializar argumentos/respuestas si es necesario
from typing import Optional, Dict, Any, List
from copy import deepcopy
from datetime import datetime
import traceback # Importar traceback para log de errores
import asyncio # Importar asyncio para el delay (aunque no lo usaremos directamente en el callback)
import random # Importar random para el jitter del retry

# Importaciones de SQLAlchemy y modelos (sin cambios)
from sqlalchemy.ext.asyncio import AsyncSession
import models

# Importaciones de ADK (sin cambios + FunctionTool)
from google.adk.agents import Agent
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types
from google.adk.agents.callback_context import CallbackContext
from google.adk.tools.tool_context import ToolContext
from google.adk.models import LlmResponse, LlmRequest
from google.adk.tools.base_tool import BaseTool
#from google.adk.tools import FunctionTool # Importar FunctionTool

# Importaciones de herramientas y prompt (sin cambios)
from agents.prompt_generator import generate_prompt_for_business
from agents.tools.product_tools import buscar_producto as buscar_producto_impl
from agents.tools.cart_tools import (
    agregar_al_carrito as agregar_al_carrito_impl,
    ver_carrito as ver_carrito_impl,
    remover_del_carrito as remover_del_carrito_impl,
    modificar_cantidad as modificar_cantidad_impl,
)

# Configuración del logger estándar de Python (sin cambios)
logger = logging.getLogger(__name__)

# --- Clase AgentExecutionLogger ---
class AgentExecutionLogger:
    """Clase para logging estructurado y métricas de ejecución del agente."""

    def __init__(self):
        self._metrics = {
            'total_calls': 0,
            'llm_calls': 0,
            'tool_calls': 0,
            'tool_cache_hits': 0, # Métrica nueva para cache
            'errors': 0,
            'total_agent_duration_ms': 0.0,
        }
        self._timing_stack = []

    def _log(self, event_type: str, data: Dict, level: str = 'INFO'):
        """Registra un evento estructurado usando el logger estándar."""
        log_entry = {
            'event_type': event_type,
            'level': level,
            'timestamp': datetime.now().isoformat(),
            **data
        }
        log_func = getattr(logger, level.lower(), logger.info)
        try:
            log_func(json.dumps(log_entry, default=str))
        except TypeError as e:
            logger.error(f"Error al serializar log {event_type}: {e}. Datos: {log_entry}")


    def start_timing(self, operation_key: str):
        """Inicia un temporizador para una operación."""
        self._timing_stack.append({'key': operation_key, 'start': time.time()})

    def end_timing(self, operation_key: str) -> float:
        """Finaliza un temporizador, registra la duración y la retorna en ms."""
        duration_ms = 0.0
        if self._timing_stack and self._timing_stack[-1]['key'] == operation_key:
            timing_info = self._timing_stack.pop()
            duration_s = time.time() - timing_info['start']
            duration_ms = round(duration_s * 1000, 2)
            self._log('TIMING', {'operation': operation_key, 'duration_ms': duration_ms})
        else:
            # No loguear como warning si es esperado (ej. error antes de agent_end)
            pass # logger.warning(f"Error de temporización: No se encontró inicio para '{operation_key}'")
        return duration_ms

    def update_metric(self, metric_name: str, value: float = 1):
        """Actualiza una métrica."""
        if metric_name in self._metrics:
            if metric_name == 'total_agent_duration_ms':
                 self._metrics[metric_name] += value
            else:
                self._metrics[metric_name] += value
        else:
            logger.warning(f"Métrica desconocida: {metric_name}")

    # --- Métodos de log específicos (log_agent_start, log_llm_request, etc.) ---
    # (Estos métodos usan _log, start_timing, end_timing, update_metric)
    def log_agent_start(self, context: CallbackContext):
        self.start_timing('agent_execution')
        self.update_metric('total_calls')
        session_id_from_state = context.state.get('session_id', 'unknown_session') # <-- LEER DESDE ESTADO
        user_id_from_state = context.state.get('user_id', 'unknown_user')
        self._log('AGENT_START', {
        'agent_name': context.agent_name,
        'invocation_id': context.invocation_id,
        'session_id': session_id_from_state, # <-- Usar valor del estado
        'user_id': user_id_from_state,
        'state_keys': list(context.state.to_dict().keys()) if context.state else []
        })

    def log_agent_end(self, context: CallbackContext):
        duration_ms = self.end_timing('agent_execution')
        self.update_metric('total_agent_duration_ms', duration_ms)
        session_id_from_state = context.state.get('session_id', 'unknown_session') # <-- LEER DESDE ESTADO
        user_id_from_state = context.state.get('user_id', 'unknown_user')
        self._log('AGENT_END', {
            'agent_name': context.agent_name,
            'invocation_id': context.invocation_id,
            'session_id': session_id_from_state, # <-- Usar valor del estado
            'user_id': user_id_from_state,
            'execution_time_ms': duration_ms
        }, 'INFO')

    def log_llm_request(self, context: CallbackContext, llm_request: LlmRequest):
        self.start_timing('llm_call')
        self.update_metric('llm_calls')
        message_count = len(llm_request.contents) if llm_request.contents else 0
        estimated_chars = sum(len(str(part.text)) for content in llm_request.contents if content.parts for part in content.parts if hasattr(part, 'text'))
        session_id_from_state = context.state.get('session_id', 'unknown_session')
        user_id_from_state = context.state.get('user_id', 'unknown_user') # <-- LEER DESDE ESTADO
        self._log('LLM_REQUEST', {
            'agent_name': context.agent_name,
            'invocation_id': context.invocation_id,
            'session_id': session_id_from_state, # <-- Usar valor del estado
            'user_id': user_id_from_state,
            'message_count': message_count,
            'estimated_chars': estimated_chars,
            'model_config': {
                'temperature': getattr(llm_request.config, 'temperature', None),
                'max_output_tokens': getattr(llm_request.config, 'max_output_tokens', None)
            }
        })

    def log_llm_response(self, context: CallbackContext, llm_response: LlmResponse):
        duration_ms = self.end_timing('llm_call')
        response_text_preview = ""
        function_calls = []
        response_length = 0
        if llm_response.content and llm_response.content.parts:
            for part in llm_response.content.parts:
                if hasattr(part, 'text') and part.text:
                    response_text_preview = part.text[:80] + "..." if len(part.text) > 80 else part.text
                    response_length += len(part.text)
                if hasattr(part, 'function_call') and part.function_call:
                    function_calls.append(part.function_call.name)
        session_id_from_state = context.state.get('session_id', 'unknown_session') # <-- LEER DESDE ESTADO
        user_id_from_state = context.state.get('user_id', 'unknown_user')
        self._log('LLM_RESPONSE', {
            'agent_name': context.agent_name,
            'invocation_id': context.invocation_id,
            'session_id': session_id_from_state, # <-- Usar valor del estado
            'user_id': user_id_from_state,
            'response_length': response_length,
            'response_preview': response_text_preview,
            'function_calls': function_calls,
            'response_time_ms': duration_ms
        })

    def log_tool_start(self, tool: BaseTool, args: Dict[str, Any], tool_context: ToolContext):
        self.start_timing(f'tool_{tool.name}')
        self.update_metric('tool_calls')
        session_id_from_state = tool_context.state.get('session_id', 'unknown_tool_session')
        user_id_from_state = tool_context.state.get('user_id', 'unknown_tool_user')
        self._log('TOOL_START', {
            'agent_name': tool_context.agent_name,
            'invocation_id': tool_context.invocation_id,
            'session_id': session_id_from_state,
            'user_id': user_id_from_state,
            'tool_name': tool.name,
            'args': args, # Considerar truncar
            'state_keys': list(tool_context.state.to_dict().keys()) if tool_context.state else []
        })

    def log_tool_end(self, tool: BaseTool, args: Dict[str, Any], tool_context: ToolContext, tool_response: Dict):
        duration_ms = self.end_timing(f'tool_{tool.name}')
        is_success = isinstance(tool_response, dict) and tool_response.get("status", "success") not in ["error"] # Simplificado
        level = 'INFO' if is_success else 'ERROR'
        if not is_success:
            self.update_metric('errors')
        session_id_from_state = tool_context.state.get('session_id', 'unknown_tool_session')
        user_id_from_state = tool_context.state.get('user_id', 'unknown_tool_user')
        self._log('TOOL_END', {
            'agent_name': tool_context.agent_name,
            'invocation_id': tool_context.invocation_id,
            'session_id': session_id_from_state,
            'user_id': user_id_from_state,
            'tool_name': tool.name,
            'success': is_success,
            'response_status': tool_response.get("status", "unknown") if isinstance(tool_response, dict) else "unknown",
            'response_size': len(str(tool_response)),
            'execution_time_ms': duration_ms
        }, level)

    def log_cache_hit(self, tool_name: str, args: Dict[str, Any], tool_context: ToolContext):
        self.update_metric('tool_cache_hits') # Incrementar cache hits
        session_id_from_state = tool_context.state.get('session_id', 'unknown_tool_session')
        user_id_from_state = tool_context.state.get('user_id', 'unknown_tool_user')
        self._log('TOOL_CACHE_HIT', {
            'agent_name': tool_context.agent_name,
            'invocation_id': tool_context.invocation_id,
            'session_id': session_id_from_state,
            'user_id': user_id_from_state,
            'tool_name': tool_name,
            'args': args,
        })


    def get_metrics(self) -> Dict:
        """Retorna una copia de las métricas actuales."""
        metrics_copy = self._metrics.copy()
        if metrics_copy['total_calls'] > 0:
             metrics_copy['avg_agent_duration_ms'] = round(
                 metrics_copy['total_agent_duration_ms'] / metrics_copy['total_calls'], 2
             )
        else:
             metrics_copy['avg_agent_duration_ms'] = 0.0
        return metrics_copy

execution_logger = AgentExecutionLogger()

# --- Clase IntelligentCache ---
class IntelligentCache:
    """Cache inteligente con TTL y generación de claves basada en args."""
    def __init__(self, default_ttl=300):
        self._cache = {}
        self._default_ttl = default_ttl
        self._hit_count = 0
        self._miss_count = 0

    def _generate_key(self, tool_name: str, args: dict) -> str:
        try:
            sorted_args = json.dumps(args, sort_keys=True, default=str)
        except TypeError:
            sorted_args = str(sorted(args.items()))
        return f"{tool_name}:{sorted_args}"

    def get(self, tool_name: str, args: dict) -> Optional[dict]:
        key = self._generate_key(tool_name, args)
        entry = self._cache.get(key)
        if entry and time.time() < entry['expires_at']:
            self._hit_count += 1
            # Log separado del hit hecho en before_tool_prod
            # logger.info(f"CACHE HIT para key: {key}")
            return deepcopy(entry['value'])
        elif entry:
            # logger.info(f"CACHE EXPIRED para key: {key}")
            self._cache.pop(key, None)
            self._miss_count += 1
            return None
        else:
             self._miss_count += 1
             # logger.info(f"CACHE MISS para key: {key}")
             return None

    def set(self, tool_name: str, args: dict, value: dict, ttl: Optional[int] = None):
        if not isinstance(value, dict):
             logger.warning(f"Intento de cachear valor no diccionario para {tool_name}. Ignorando.")
             return
        key = self._generate_key(tool_name, args)
        ttl_to_use = ttl if ttl is not None else self._default_ttl
        expires_at = time.time() + ttl_to_use
        self._cache[key] = {'value': deepcopy(value), 'expires_at': expires_at}
        logger.info(f"CACHE SET para key: {key} con TTL: {ttl_to_use}s")

    def get_stats(self) -> dict:
        total_requests = self._hit_count + self._miss_count
        hit_rate = (self._hit_count / total_requests * 100) if total_requests > 0 else 0
        return {
            'hit_count': self._hit_count, 'miss_count': self._miss_count,
            'hit_rate_percent': round(hit_rate, 2), 'current_size': len(self._cache),
        }

tool_cache = IntelligentCache()

# --- Clase RetryManager ---
class RetryManager:
    """Gestor de reintentos con backoff exponencial."""
    def __init__(self, max_retries=2, base_delay=0.5, max_delay=5.0):
        self.max_retries = max_retries
        self.base_delay = base_delay
        self.max_delay = max_delay
        self.retry_counts = {}

    def _generate_key(self, tool_name: str, args: dict) -> str:
        try:
            sorted_args = json.dumps(args, sort_keys=True, default=str)
        except TypeError:
            sorted_args = str(sorted(args.items()))
        return f"{tool_name}:{sorted_args}"

    def should_retry(self, tool_name: str, args: dict, error_message: str) -> bool:
        key = self._generate_key(tool_name, args)
        retries = self.retry_counts.get(key, 0)
        non_retryable_markers = [
            'inválido', 'no encontrado', 'not found', 'cannot divide',
            'no es válida', 'no puede ser negativa', 'agotado', 'out_of_stock',
             'no manejamos'
        ]
        error_lower = error_message.lower()
        if any(marker in error_lower for marker in non_retryable_markers):
            # logger.info(f"RETRY: Error no reintentable detectado para {key}: {error_message}")
            return False
        should = retries < self.max_retries
        # logger.info(f"RETRY: Evaluación para {key}. Intentos: {retries}/{self.max_retries}. Reintentable: {should}. Error: {error_message}")
        return should

    async def get_delay(self, tool_name: str, args: dict) -> float:
        key = self._generate_key(tool_name, args)
        retries = self.retry_counts.get(key, 0)
        delay = min(self.base_delay * (2 ** retries), self.max_delay)
        jitter = random.uniform(0, delay * 0.1)
        calculated_delay = delay + jitter
        # logger.info(f"RETRY: Delay calculado para {key} (intento {retries+1}): {calculated_delay:.2f}s")
        return calculated_delay

    def increment_retry(self, tool_name: str, args: dict):
        key = self._generate_key(tool_name, args)
        self.retry_counts[key] = self.retry_counts.get(key, 0) + 1
        logger.warning(f"RETRY: Contador incrementado para {key} a {self.retry_counts[key]}")

    def reset_retry(self, tool_name: str, args: dict):
        key = self._generate_key(tool_name, args)
        if key in self.retry_counts:
            # logger.info(f"RETRY: Reseteando contador para {key}")
            self.retry_counts.pop(key, None)

retry_manager = RetryManager()

# --- Callbacks Refactorizados (Integrando Cache y Retry) ---

def before_agent_prod(callback_context: CallbackContext) -> Optional[types.Content]:
    execution_logger.log_agent_start(callback_context)
    return None

def after_agent_prod(callback_context: CallbackContext) -> Optional[types.Content]:
    execution_logger.log_agent_end(callback_context)
    # Opcional: Loguear métricas o stats de caché al final
    # logger.info(f"Métricas finales: {execution_logger.get_metrics()}")
    # logger.info(f"Estadísticas Cache: {tool_cache.get_stats()}")
    return None

def before_model_prod(callback_context: CallbackContext, llm_request: LlmRequest) -> Optional[LlmResponse]:
    execution_logger.log_llm_request(callback_context, llm_request)
    return None

def after_model_prod(callback_context: CallbackContext, llm_response: LlmResponse) -> Optional[LlmResponse]:
    execution_logger.log_llm_response(callback_context, llm_response)
    return None

def before_tool_prod(tool: BaseTool, args: Dict[str, Any], tool_context: ToolContext) -> Optional[Dict]:
    """Callback ANTES de la herramienta: Verifica caché y valida args."""
    # 1. Intentar obtener resultado del caché
    cached_result = tool_cache.get(tool.name, args)
    if cached_result:
        # ¡CACHE HIT! Loguear el hit y retornar el resultado cacheado
        execution_logger.log_cache_hit(tool.name, args, tool_context) # Log específico
        return cached_result # Evita ejecución

    # 2. CACHE MISS: Continuar con logging y validación normal
    execution_logger.log_tool_start(tool, args, tool_context) # Loguear inicio (miss)

    # 3. Validación de argumentos (ejemplo para cantidad)
    if tool.name == "agregar_al_carrito_wrapper" or tool.name == "modificar_cantidad_wrapper":
        # Usar nombres correctos según la definición del wrapper
        cantidad = None
        if tool.name == "agregar_al_carrito_wrapper":
            cantidad = args.get('cantidad')
        elif tool.name == "modificar_cantidad_wrapper":
            cantidad = args.get('nueva_cantidad') # Nombre de parámetro en el wrapper

        is_valid_number = isinstance(cantidad, (int, float))
        is_positive = cantidad > 0 if is_valid_number else False

        if tool.name == "agregar_al_carrito_wrapper" and (not is_valid_number or not is_positive):
             error_msg = f"Argumento 'cantidad' inválido: {cantidad}. Debe ser número positivo."
             logger.error(error_msg)
             return {"status": "error", "message": "La cantidad para agregar no es válida."}
        elif tool.name == "modificar_cantidad_wrapper":
             if not is_valid_number:
                 error_msg = f"Argumento 'nueva_cantidad' inválido: {cantidad}. Debe ser número."
                 logger.error(error_msg)
                 return {"status": "error", "message": "La nueva cantidad no es válida."}
             elif cantidad < 0:
                 error_msg = f"Argumento 'nueva_cantidad' negativo: {cantidad}."
                 logger.error(error_msg)
                 return {"status": "error", "message": "La nueva cantidad no puede ser negativa."}
    
    # 4. Permitir ejecución (los 'args' ahora contienen las dependencias inyectadas)
    logger.debug(f"Argumentos inyectados para {tool.name}: {args.keys()}")
    return None

def after_tool_prod(tool: BaseTool, args: Dict[str, Any], tool_context: ToolContext, tool_response: Dict) -> Optional[Dict]:
    """Callback DESPUÉS de la herramienta: Loguea, guarda en caché y maneja reintentos."""
    # 1. Loguear fin de ejecución y métricas
    execution_logger.log_tool_end(tool, args, tool_context, tool_response)

    # Crear clave única
    operation_key = retry_manager._generate_key(tool.name, args)

    # 2. Determinar si la ejecución fue exitosa
    # Considerar 'empty' y 'success' como éxito para caché/retry reset
    is_success = isinstance(tool_response, dict) and tool_response.get("status") in ["success", "empty"]

    if is_success:
        # 2a. Éxito: Resetear contador de reintentos y guardar en caché
        retry_manager.reset_retry(tool.name, args)

        # Guardar en caché si aplica
        ttl = None
        if tool.name == 'buscar_producto': ttl = 600
        elif tool.name == 'ver_carrito': ttl = 30 # TTL corto para ver carrito
        if ttl is not None:
             tool_cache.set(tool.name, args, tool_response, ttl=ttl)
        return None # Usar la respuesta original

    else:
        # 2b. Error: Verificar si es reintentable
        error_message = tool_response.get("message", "Error desconocido") if isinstance(tool_response, dict) else "Respuesta inválida"

        if retry_manager.should_retry(tool.name, args, error_message):
            # Sí reintentar: Incrementar contador y preparar respuesta para LLM
            retry_manager.increment_retry(tool.name, args)
            # Calculamos el delay pero no lo usamos para esperar aquí
            # delay = await retry_manager.get_delay(tool.name, args) # No podemos hacer await aquí

            retry_response = {
                "status": "error_temporal", # Nuevo status
                "message": f"Hubo un problema temporal al ejecutar {tool.name}. El sistema podría intentar de nuevo. Por favor, espera o intenta una acción diferente.",
                "original_error": error_message,
                "retry_attempt": retry_manager.retry_counts.get(operation_key, 0)
            }
            logger.warning(f"RETRY: Error reintentable en {tool.name}. Informando al LLM. Intento {retry_response['retry_attempt']}/{retry_manager.max_retries}.")
            return retry_response # Devolver respuesta modificada al LLM
        else:
            # No reintentar
            logger.error(f"RETRY: Error final no reintentable o límite alcanzado para {tool.name}. Error: {error_message}")
            if retry_manager.retry_counts.get(operation_key, 0) >= retry_manager.max_retries:
                 retry_manager.reset_retry(tool.name, args) # Resetear si fue por límite
            return None # Usar tool_response original con el error final


# --- Función principal process_customer_message ---

async def ensure_session(
    session_service: InMemorySessionService, app_name: str, user_id: str, session_id: str
):
    """Asegura que una sesión de conversación exista antes de ser utilizada."""
    existing = await session_service.list_sessions(app_name=app_name, user_id=user_id)
    if not any(s.id == session_id for s in existing.sessions):
        await session_service.create_session(
            app_name=app_name, user_id=user_id, session_id=session_id
        )

async def process_customer_message(
    user_message: str,
    customer_phone: str,
    business: models.Business,
    db: AsyncSession,
    runner: Runner,
    session_service: InMemorySessionService
    # memory_service: Optional[YourMemoryServiceClass] = None # Placeholder para memoria futura
) -> str:
    """
    Orquesta la lógica de IA usando Callbacks para monitorización, control,
    caching, reintentos e inyección de dependencias con functools.partial.
    """
    app_name = "whsp_ai_sales_agent"
    session_id = f"{business.whatsapp_number}-{customer_phone}"

    # Asegura la sesión exista en el servicio
    await ensure_session(session_service, app_name, customer_phone, session_id)

    # Genera el prompt base para el negocio
    final_instruction = generate_prompt_for_business(business)

    # --- Estado Persistente (AHORA INCLUYE DEPENDENCIAS PARA CALLBACKS) ---
    initial_state = {
        'business_id': business.id,
        'session_id': session_id,
        'user_id': customer_phone,
        }
    # Cargar y fusionar estado existente
    # Cargar y fusionar estado existente USANDO KEYWORD ARGUMENTS
    current_session_data = None # Inicializar por si falla
    try:
        current_session_data = await session_service.get_session(
            app_name=app_name,
            user_id=customer_phone,
            session_id=session_id
        )
    except Exception as e_get:
        # Loguear si get_session falla, pero continuar podría ser posible
        logger.error(f"Error al intentar obtener sesión existente {session_id}: {e_get}", exc_info=True)

    if current_session_data and current_session_data.state:
         initial_state.update(current_session_data.state)
         logger.debug(f"Estado existente cargado: {current_session_data.state.keys()}")

    initial_state['session_id'] = session_id
    initial_state['user_id'] = customer_phone

    state_to_save = {k: v for k, v in initial_state.items() if k != 'db_session'}
    await session_service.create_session(
        app_name=app_name, user_id=customer_phone, session_id=session_id,
        state=state_to_save
    )
    logger.debug(f"Estado serializable guardado: {state_to_save.keys()}")
    # --- Crear Herramientas Parciales con Dependencias (db, business_id, customer_phone) ---
    # Esto inyecta las dependencias necesarias sin usar el estado de sesión ADK para objetos no serializables
    # --- INICIO: Definir Funciones Wrapper Dinámicas ---
    async def buscar_producto_wrapper(nombre_producto: str) -> dict:
        """
        Busca un producto y maneja el caso 'unconfirmed' para Human-in-the-Loop.
        Args:
            nombre_producto: El nombre o descripción parcial del producto.
        Returns:
            Diccionario con el resultado de la búsqueda.
        """
        logger.info(f"Wrapper: Iniciando búsqueda para '{nombre_producto}'")
        # 1. Ejecutar la implementación real
        search_result = await buscar_producto_impl(
            nombre_producto=nombre_producto, business_id=business.id, db=db
        )
        logger.info(f"Wrapper: Resultado de búsqueda para '{nombre_producto}': {search_result.get('status')}")

        # 2. Interceptar y manejar el estado 'unconfirmed'
        if search_result.get("status") == "unconfirmed":
            product_details = search_result.get("product_details", {})
            product_id = product_details.get("id")
            product_name_found = product_details.get("name") # Usar nombre diferente para evitar conflicto

            if product_id and product_name_found:
                # 3. Simular notificación HITL (Human-in-the-Loop)
                logger.info(
                    f"[HUMAN-IN-THE-LOOP] Notificación para Negocio ID {business.id} ('{business.name}'): "
                    f"Cliente '{customer_phone}' preguntó por '{product_name_found}' (ID: {product_id}, Status: UNCONFIRMED). "
                    f"Se requiere acción en sistema de gestión."
                )
                # Aquí podrías añadir lógica para enviar una notificación real (ej. a través de otra función/servicio)

        # 4. Devolver siempre el resultado original de la búsqueda al agente/LLM
        return search_result

    async def agregar_al_carrito_wrapper(nombre_producto: str, cantidad: float) -> dict:
        """
        Wrapper simple para agregar un producto al carrito.
        Args:
            nombre_producto: Nombre del producto a agregar.
            cantidad: Cantidad a agregar (puede ser float).
        Returns:
            Diccionario con el resultado de la operación.
        """
        # Este wrapper por ahora solo llama a la implementación
        # Podría añadir lógica extra si fuera necesario (ej. verificar stock antes?)
        return await agregar_al_carrito_impl(
            nombre_producto=nombre_producto,
            cantidad=cantidad,
            db=db,
            business_id=business.id,
            customer_phone=customer_phone,
        )

    async def ver_carrito_wrapper() -> dict:
        """
        Wrapper simple para mostrar el contenido del carrito.
        Returns:
            Diccionario con el contenido del carrito o estado vacío/error.
        """
        # Podría añadir lógica aquí, ej. formatear la respuesta antes de devolverla al LLM
        return await ver_carrito_impl(
            db=db, business_id=business.id, customer_phone=customer_phone
        )

    async def remover_del_carrito_wrapper(nombre_producto: str) -> dict:
        """
        Wrapper simple para eliminar un producto del carrito.
        Args:
            nombre_producto: Nombre del producto a eliminar.
        Returns:
            Diccionario con el resultado de la operación.
        """
        return await remover_del_carrito_impl(
            nombre_producto=nombre_producto,
            business_id=business.id,
            customer_phone=customer_phone,
            db=db
        )

    async def modificar_cantidad_wrapper(nombre_producto: str, nueva_cantidad: float) -> dict:
        """
        Wrapper simple para modificar la cantidad de un producto en el carrito.
        Args:
            nombre_producto: Nombre del producto a modificar.
            nueva_cantidad: La nueva cantidad deseada (puede ser 0 para eliminar).
        Returns:
            Diccionario con el resultado de la operación.
        """
        return await modificar_cantidad_impl(
            nombre_producto=nombre_producto,
            nueva_cantidad=nueva_cantidad,
            business_id=business.id,
            customer_phone=customer_phone,
            db=db
        )
    # --- Instanciar el Agente con WRAPPERS y Callbacks ---
    request_agent = Agent(
        name=f"agent_for_{business.id}",
        model="gemini-2.5-flash-lite",
        instruction=final_instruction,
        tools=[ # Pasar las FUNCIONES WRAPPER directamente
            buscar_producto_wrapper,
            agregar_al_carrito_wrapper,
            ver_carrito_wrapper,
            remover_del_carrito_wrapper,
            modificar_cantidad_wrapper,
        ],
        # Callbacks (ahora before_tool_prod necesita inyectar desde state)
        before_agent_callback=before_agent_prod,
        after_agent_callback=after_agent_prod,
        before_model_callback=before_model_prod,
        after_model_callback=after_model_prod,
        before_tool_callback=before_tool_prod, # Necesita db_session del state
        after_tool_callback=after_tool_prod,
    )

    runner.agent = request_agent
    logger.debug(f"Agente dinámico '{request_agent.name}' con WRAPPERS asignado.")


    # --- Ejecución del Agente ---
    content = types.Content(role="user", parts=[types.Part(text=user_message)])
    final_response_text = "Lo siento, tuve un problema para procesar tu mensaje." # Valor por defecto

    try:
        # Llamar a run_async SIN el argumento initial_state
        events = runner.run_async(
            user_id=customer_phone,
            session_id=session_id,
            new_message=content
        )

        # Iterar sobre los eventos generados por el runner
        async for event in events:
            # Puedes descomentar para debugging intensivo de eventos:
            logger.debug(f"Evento recibido: Tipo={type(event).__name__}, Final={event.is_final_response()}, Autor={event.author}")
            logger.debug(f"Contenido Evento: {event.content}")

            # Buscar la respuesta final destinada al usuario
            if event.is_final_response() and event.content and event.content.parts:
                # Asegurarse de que la primera parte contenga texto
                if hasattr(event.content.parts[0], 'text'):
                    final_response_text = event.content.parts[0].text
                    logger.info(f"Respuesta final del agente obtenida para sesión {session_id}")
                else:
                    # Caso raro donde la respuesta final no tiene texto (ej. solo llamada a función fallida?)
                    logger.warning(f"Respuesta final del agente para {session_id} no contenía texto: {event.content.parts}")
                    # Mantener mensaje de error por defecto o definir uno específico
                break # Salir del bucle al encontrar la respuesta final
            elif event.is_final_response() and event.error_message:
                 # Manejar caso donde la respuesta final es un error del agente
                 logger.error(f"Agente finalizó con error para sesión {session_id}: {event.error_message}")
                 final_response_text = "Hubo un problema procesando tu solicitud. Por favor, intenta de nuevo."
                 execution_logger.update_metric('errors') # Contar como error
                 break


    except Exception as e:
        # Capturar cualquier excepción inesperada durante la ejecución del runner
        logger.critical(f"Excepción CRÍTICA durante runner.run_async para sesión {session_id}: {e}", exc_info=True)
        # Loguear error estructurado
        execution_logger._log('AGENT_CRITICAL_ERROR', {
            'agent_name': runner.agent.name if runner.agent else 'unknown',
            'invocation_id': 'unknown', # No disponible fácilmente en este punto
            'session_id': session_id,
            'user_id': customer_phone,
            'error_message': str(e),
            'traceback': traceback.format_exc()
        }, 'CRITICAL')
        execution_logger.update_metric('errors') # Contar como error
        # Devolver mensaje genérico al usuario
        return "Hubo un inconveniente técnico mayor, por favor intenta de nuevo más tarde."
    finally:
        # Asegurarse de que el temporizador principal del agente siempre se detenga
         if execution_logger._timing_stack and execution_logger._timing_stack[-1]['key'] == 'agent_execution':
             # Si after_agent_prod no se llamó (ej. error antes de finalizar), detenerlo aquí
             execution_logger.end_timing('agent_execution')


    # --- Lógica Post-Ejecución (Opcional: Guardado de Memoria a Largo Plazo) ---
    try:
        # Asegurarse que ESTA llamada a get_session también usa keyword arguments
        current_session = await session_service.get_session(
            app_name=app_name,
            user_id=customer_phone,
            session_id=session_id
        )
        if current_session:
            logger.debug(f"Sesión {session_id} recuperada exitosamente post-ejecución.")
            # if memory_service:
            #     try:
            #         await memory_service.add_session_to_memory(current_session)
            #         logger.info(f"Sesión {session_id} guardada en memoria a largo plazo.")
            #     except Exception as mem_e:
            #         logger.error(f"Error guardando sesión {session_id} en memoria: {mem_e}", exc_info=True)
        else:
            # Esto sería inesperado si la ejecución fue exitosa
            logger.warning(f"No se pudo recuperar la sesión {session_id} post-ejecución.")
    except Exception as e_get_post:
        logger.error(f"Error recuperando sesión {session_id} post-ejecución: {e_get_post}", exc_info=True)

    # Devolver la respuesta final al usuario
    logger.info(f"Retornando respuesta final para sesión {session_id}")
    return final_response_text

# --- FIN: Función principal process_customer_message ---