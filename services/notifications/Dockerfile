FROM python:3.11-slim

WORKDIR /app

COPY services/notifications/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

COPY libs/ /app/libs/
COPY services/notifications/ /app/services/notifications/

ENV PYTHONPATH=/app
ENV SERVICE_NAME=notifications

EXPOSE 8004

CMD ["uvicorn", "services.notifications.main:app", "--host", "0.0.0.0", "--port", "8004", "--workers", "1"]
