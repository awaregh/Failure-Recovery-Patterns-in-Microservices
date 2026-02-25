FROM python:3.11-slim

WORKDIR /app

COPY services/orders/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

COPY libs/ /app/libs/
COPY services/orders/ /app/services/orders/

ENV PYTHONPATH=/app
ENV SERVICE_NAME=orders

EXPOSE 8001

CMD ["uvicorn", "services.orders.main:app", "--host", "0.0.0.0", "--port", "8001", "--workers", "1"]
