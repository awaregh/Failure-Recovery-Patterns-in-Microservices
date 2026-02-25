FROM python:3.11-slim

WORKDIR /app

COPY services/inventory/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

COPY libs/ /app/libs/
COPY services/inventory/ /app/services/inventory/

ENV PYTHONPATH=/app
ENV SERVICE_NAME=inventory

EXPOSE 8003

CMD ["uvicorn", "services.inventory.main:app", "--host", "0.0.0.0", "--port", "8003", "--workers", "1"]
