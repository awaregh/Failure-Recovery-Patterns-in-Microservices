FROM python:3.11-slim

WORKDIR /app

COPY services/payments/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

COPY libs/ /app/libs/
COPY services/payments/ /app/services/payments/

ENV PYTHONPATH=/app
ENV SERVICE_NAME=payments

EXPOSE 8002

CMD ["uvicorn", "services.payments.main:app", "--host", "0.0.0.0", "--port", "8002", "--workers", "1"]
