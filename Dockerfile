FROM node:24-alpine AS frontend
WORKDIR /web
COPY web/package*.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

FROM python:3.11-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 TG_DATA_DIR=/app/data
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt \
    && useradd --uid 10001 --create-home app \
    && mkdir -p /app/data && chown app:app /app/data
COPY app/ ./app/
COPY --from=frontend /web/dist/ ./web/dist/
USER app
EXPOSE 8787
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s \
  CMD python -c "from urllib.request import urlopen; urlopen('http://127.0.0.1:8787/healthz',timeout=3)" || exit 1
CMD ["python", "-m", "app.main", "serve", "--host", "0.0.0.0", "--port", "8787"]
