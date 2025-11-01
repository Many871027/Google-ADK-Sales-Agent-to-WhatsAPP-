# =============================================================================
# Dockerfile Simplificado y Robusto para Producción - WHSP-AI
# =============================================================================

# 1. Usar una imagen base de Python oficial y ligera.
FROM python:3.11-slim

# 2. Establecer variables de entorno para buenas prácticas de Python en contenedores.
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# 3. Establecer el directorio de trabajo. Todos los comandos se ejecutarán desde aquí.
WORKDIR /app

# 4. Copiar solo el archivo de requerimientos para aprovechar el caché de Docker.
COPY backend/requirements.txt .

# 5. Instalar las dependencias. Se instalan en el entorno global de Python del contenedor.
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# 6. Copiar todo el código de la aplicación.
COPY backend/ .

# 7. Crear un usuario y grupo sin privilegios por seguridad.
RUN addgroup --system app && adduser --system --group app

# 8. Cambiar al usuario sin privilegios.
USER app

# 9. Exponer el puerto en el que correrá la aplicación.
EXPOSE 8080

# 10. Comando para ejecutar la aplicación. Gunicorn está en el PATH global y encontrará "main:app".
CMD ["gunicorn", "-w", "4", "-k", "uvicorn.workers.UvicornWorker", "main:app", "--bind", "0.0.0.0:8080"]