# The gateway, API, console and pipeline CLI in one image.
FROM node:22-slim AS console
WORKDIR /console
COPY console/package.json console/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY console/ ./
RUN npm run build

FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 APR_ROOT=/app APR_STATIC_ROOT=/app/console/dist
WORKDIR /app
COPY pyproject.toml README.md ./
COPY src/ src/
RUN pip install --no-cache-dir ".[spark,dbt]"
COPY config/ config/
COPY transform/ transform/
COPY --from=console /console/dist console/dist
RUN useradd --uid 10001 --create-home apr && mkdir -p /data && chown apr /data
USER 10001
ENV APR_WAREHOUSE=/data
EXPOSE 8000
HEALTHCHECK CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health')"
CMD ["apr", "serve", "--host", "0.0.0.0", "--port", "8000"]
