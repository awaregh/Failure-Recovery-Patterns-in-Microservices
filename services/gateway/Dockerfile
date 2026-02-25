FROM python:3.11-slim

WORKDIR /app

# Install dependencies first (layer cache)
COPY services/gateway/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

# Copy shared libs
COPY libs/ /app/libs/

# Copy service code
COPY services/gateway/ /app/services/gateway/

ENV PYTHONPATH=/app
ENV SERVICE_NAME=gateway

EXPOSE 8000

CMD ["uvicorn", "services.gateway.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
