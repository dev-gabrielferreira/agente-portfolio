# Contrato de deploy: porta 8000, /health, dados em /data, usuário não-root.
# 1) build do frontend (Vite)
FROM node:22-slim AS web
WORKDIR /web
COPY frontend/package*.json ./
RUN if [ -f package-lock.json ]; then npm ci; else npm install; fi
COPY frontend/ ./
RUN npm run build

# 2) backend Python servindo a API e o build estático
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 DATA_DIR=/data
WORKDIR /app
RUN useradd --create-home --uid 10001 app && mkdir -p /data && chown app:app /data
COPY pyproject.toml ./
COPY app ./app
RUN pip install .
COPY --from=web /web/dist ./frontend/dist
USER app
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2).status==200 else 1)"
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
