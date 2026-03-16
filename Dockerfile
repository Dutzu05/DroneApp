FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=5174 \
    DRONE_BIND_HOST=0.0.0.0 \
    DRONE_DB_NAME=drone_app \
    DRONE_ANEXA1_TEMPLATE_PATH=/app/assets/templates/ANEXA1.pdf

RUN apt-get update \
    && apt-get install -y --no-install-recommends postgresql-client curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN chmod +x scripts/init-db.sh docker/entrypoint.sh

EXPOSE 5174
VOLUME ["/app/.data"]

ENTRYPOINT ["./docker/entrypoint.sh"]
CMD ["python3", "scripts/visualise_zones.py", "--no-browser"]
