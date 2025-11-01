🤖 WHSP-AI: Agente de Ventas Conversacional (v1.1.7) Un motor de comercio conversacional multi-negocio, impulsado por IA y diseñado para escalar.

💡 Concepto Principal
WHSP-AI transforma la manera en que los pequeños y medianos negocios interactúan con sus clientes. Es una plataforma multi-tenant que permite a cualquier negocio desplegar un agente de ventas autónomo sobre WhatsApp. Cada agente es personalizado con la personalidad y el catálogo del negocio, capaz de entender el lenguaje natural para buscar productos (incluyendo personalizaciones), gestionar un carrito de compras completo y aprender del inventario en tiempo real.

El sistema está diseñado bajo la filosofía de LLMOps, conectando la experimentación de los LLMs con la ingeniería de software industrializada (Software 3.0) para crear un servicio robusto, escalable, observable y en constante mejora.

🏗️ Arquitectura Técnica
El sistema sigue una arquitectura robusta, optimizada para un rendimiento asíncrono y un despliegue en contenedores.

<img width="935" height="911" alt="image" src="https://github.com/user-attachments/assets/1661fad3-47ba-4fe1-8123-4e18d80090ad" />



Backend: Construido con FastAPI por su alto rendimiento asíncrono. Gestiona el ciclo de vida de la aplicación (@asynccontextmanager) para inicializar y cerrar recursos como pools de bases de datos.

Contenerización y Despliegue: La aplicación se empaqueta como una imagen Docker y se despliega en un VPS gestionado por Dokploy, que utiliza Traefik para la gestión automática de certificados SSL (Let's Encrypt).

Bases de Datos:

SQL (PostgreSQL): Base de datos principal para la lógica de negocio (usuarios, productos, pedidos, facturación), gestionada con SQLAlchemy 2.0 (async).

Vectorial (pgVector): Base de datos vectorial (adk_memory) para la memoria semántica a largo plazo del agente (RAG).

Núcleo de IA: Un agente dinámico por petición (Google ADK) instanciado por un agent_handler.

Herramientas (Tools): El agente está equipado con herramientas (buscar_producto, agregar_al_carrito, remover_del_carrito, modificar_cantidad, ver_carrito) que le dan acceso de lectura/escritura a la base de datos de negocio.

Inferencia: Impulsada por modelos de Vertex AI (Gemini).

✨ Alcances Actuales (Funcionalidades v1.1.7)
El sistema es completamente funcional y ha superado la prueba de concepto, implementando características robustas de nivel de producción:

✅ Sistema de Autenticación y API (SaaS):

Sistema completo de autenticación de usuarios (/users/, /token) basado en JWT (OAuth2) con hashing de contraseñas argon2 (passlib).

Fundamentos de un SaaS con endpoints polimórficos para facturación (/billing/user) y pagos (/payments/subscription) para gestionar suscripciones de usuarios.

✅ Agente de Comercio Conversacional Completo:

Gestión Total del Carrito: El agente utiliza herramientas para buscar_producto, agregar_al_carrito, remover_del_carrito y modificar_cantidad.

Manejo de Personalización: La herramienta buscar_producto se ha optimizado para leer la columna description de la base de datos, permitiendo al agente discutir ingredientes y opciones de personalización (ej. "¿quieres tu sándwich sin cebolla?").

Gestión de Inventario en Tiempo Real: El agente maneja correctamente los estados de productos (CONFIRMED, OUT_OF_STOCK, UNCONFIRMED) devueltos por las herramientas, informando al cliente si un producto está agotado.

✅ Ingeniería de Prompts Avanzada y LLMOps:

Prompts Dinámicos: El prompt_generator.py inyecta reglas base, mientras que la personality_description (almacenada en la BD) define el flujo conversacional.

Optimización Iterativa: Se ha depurado y refinado el personality_description (v1.1.7+) para resolver bucles de conversación, gestionar saludos repetitivos y forzar el uso correcto de herramientas (ej. diferenciar ingredientes de precio $0 de productos vendibles).

Observabilidad (Callbacks): Se implementó un AgentExecutionLogger que captura métricas de evaluación (latencia de LLM, latencia de herramientas, conteo de llamadas) y trazas de ejecución en cada turno, permitiendo el monitoreo y la depuración (ej. price_not_found).

✅ Memoria Persistente (RAG):

Se ha implementado un servicio de memoria vectorial (PgVectorMemoryService) que se inicializa en el lifespan de FastAPI, permitiendo al agente tener memoria a largo plazo entre sesiones.

✅ Integración Segura de Webhooks (WhatsApp):

Integración completa con la API de Meta, manejando la verificación GET (con token) y la seguridad de los mensajes POST (con validación de firma X-Hub-Signature-256).

✅ Infraestructura y Control de Versiones:

Todo el proyecto está gestionado con Git, incluyendo la resolución de conflictos de fusión (merge conflicts) complejos entre ramas de características (Feature/agent-long-term-memory) y develop.

🚀 Alcances Futuros (Roadmap)
La arquitectura actual es la base para un crecimiento exponencial. Los siguientes pasos son:

Plataforma de Gestión para Negocios (Dashboard): Desarrollar un frontend (React/Vue) donde los dueños puedan gestionar su inventario, ver analíticas de ventas y personalizar la personalidad de su agente.

Flujo de Checkout y Pagos (Fase 2): Expandir los endpoints de pago para gestionar pedidos de Clientes (no solo suscripciones de Usuarios) e integrarlos con pasarelas de pago (Stripe, Mercado Pago).

Optimización LLMOps (Evaluación Continua): Utilizar las trazas capturadas por el AgentExecutionLogger para crear evaluation sets (conjuntos de evaluación) y automatizar la calificación de la calidad de las respuestas (ej. LLM-as-a-judge).

Integraciones de Ecosistema: Conectar con APIs de servicios de delivery para cotizar y programar envíos.s


