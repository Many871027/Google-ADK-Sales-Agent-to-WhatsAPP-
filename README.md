🤖 WHSP-AI: Agente de Ventas Conversacional
Un motor de comercio conversacional multi-negocio, impulsado por IA y diseñado para escalar.

💡 Concepto Principal
WHSP-AI transforma la manera en que los pequeños y medianos negocios interactúan con sus clientes. Es una plataforma multi-tenant que permite a cualquier negocio desplegar un agente de ventas autónomo sobre WhatsApp. Cada agente es personalizado con la personalidad y el catálogo del negocio, capaz de entender el lenguaje natural para buscar productos, gestionar un carrito de compras y aprender del inventario en tiempo real.

El sistema está diseñado bajo la filosofía de LLMOps, conectando la experimentación de los LLMs con la ingeniería de software industrializada para crear un servicio robusto, escalable y en constante mejora.

🏗️ Arquitectura Técnica
El sistema sigue una arquitectura de microservicios desacoplada, optimizada para un rendimiento asíncrono y un despliegue en contenedores.

Fragmento de código

graph TD
    subgraph "Cliente (WhatsApp)"
        A[Usuario Final]
    end

    subgraph "Infraestructura Cloud (VPS + Dokploy)"
        B(Reverse Proxy - Traefik) --> C{API Backend};

        subgraph C [FastAPI en Contenedor Docker]
            C1[Endpoint /webhook] --> C2(Agent Handler);
            C2 --> C3{Agente Dinámico (ADK)};
            C3 -- Usa --> C4[Tools: buscar, agregar, ver];
            C4 -- Accede --> D;
            C3 -- Inferencia --> E;
        end

        subgraph D [Base de Datos PostgreSQL]
            D1[Tablas: businesses, products, orders, ...]
        end

        subgraph "Google Cloud Platform"
             E[Vertex AI - Gemini LLM]
        end
    end

    A -- Envía mensaje --> B;
    C -- Devuelve respuesta --> B;
    B -- Envía respuesta --> A;
Backend: Construido con FastAPI por su alto rendimiento asíncrono. Se ejecuta como un contenedor Docker serverless, orquestado por Gunicorn y Uvicorn.

Base de Datos: PostgreSQL, gestionado como un contenedor en Dokploy, con acceso asíncrono a través de SQLAlchemy. Diseñado para ser multi-tenant desde el núcleo.

Núcleo de IA: El corazón del sistema es un agente dinámico por petición construido con el Google Agent Development Kit (ADK).

Orquestación: Se utiliza un agent_handler que instancia un agente con un prompt personalizado para cada negocio en cada petición.

Herramientas (Tools): El agente está equipado con herramientas (buscar_producto, agregar_al_carrito, ver_carrito) que actúan como su interfaz con la base de datos, permitiéndole realizar acciones concretas.

Inferencia: Las decisiones del agente y la generación de lenguaje natural son impulsadas por los modelos de Vertex AI (Gemini).

✨ Alcances Actuales (Funcionalidades Implementadas)
A día de hoy, el sistema es completamente funcional y cuenta con:

✅ Servidor Multi-Tenant: Capaz de gestionar conversaciones para múltiples negocios de forma aislada.

✅ Agente Conversacional con Herramientas: El agente puede:

Buscar productos en el inventario de un negocio específico.

Añadir productos al carrito de compras de un cliente.

Consultar y mostrar el contenido actual del carrito.

✅ Personalización Dinámica: El prompt_generator ajusta la personalidad y las reglas del agente basándose en el perfil de cada negocio.

✅ Ciclo de Aprendizaje "Human-in-the-Loop":

Cuando un producto es unconfirmed, el sistema notifica al dueño del negocio.

Un endpoint de gestión (/management/inventory_response) permite al dueño confirmar o rechazar el producto, actualizando la base de datos en tiempo real. Esto crea un "Data Flywheel" que enriquece el inventario de forma orgánica.

✅ Infraestructura como Código: Un Dockerfile optimizado para producción asegura un despliegue consistente y repetible.

✅ Autenticación Segura: Integración con Google Cloud Service Accounts para un acceso seguro a Vertex AI, con gestión de secretos a través de "File Mounts" en el entorno de despliegue.

🚀 Alcances Futuros (Roadmap)
La arquitectura actual es la base para un crecimiento exponencial. Los siguientes pasos en nuestro roadmap son:

Expansión de Capacidades del Agente
Gestión Completa del Carrito: Implementar herramientas para remover_del_carrito y modificar_cantidad.

Flujo de Checkout: Crear un SequentialAgent que guíe al cliente a través del proceso de finalización de la compra, confirmando dirección y método de pago.

Memoria Persistente: Integrar una memoria a largo plazo (ej. usando un vector store como pgvector) para que el agente recuerde preferencias de clientes pasados.

Plataforma de Gestión para Negocios
Desarrollar un frontend de administración (Dashboard) donde los dueños de los negocios puedan:

Ver las notificaciones "Human-in-the-Loop" y responder a ellas.

Gestionar su inventario directamente.

Personalizar la personalidad de su agente.

Ver analíticas de ventas.

Integraciones de Ecosistema
Pasarelas de Pago: Conectar el flujo de checkout con Stripe, Mercado Pago, etc., para procesar pagos reales.

Sistemas de Entrega: Integrar con APIs de servicios de delivery para cotizar y programar envíos.

Optimización LLMOps
Data Flywheel Avanzado: Capturar conversaciones y feedback del usuario para crear "eval sets" y realizar fine-tuning periódico de los modelos o prompts.

Monitoreo y Evaluación Continua: Implementar un sistema de evaluación basado en modelos para medir la calidad y la precisión de las respuestas del agente de forma automática.












Herramientas

