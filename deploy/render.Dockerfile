# Single-container image for hosting the whole app on one web service (e.g. Render free plan):
# Next.js serves the UI on $PORT and proxies /api/v1 to FastAPI on 127.0.0.1:8000 inside the container.

FROM node:24-slim AS web-build
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
ENV BACKEND_URL=http://127.0.0.1:8000 NEXT_TELEMETRY_DISABLED=1
RUN npm run build

FROM node:24-slim AS node-bin

FROM python:3.11-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 \
    NODE_ENV=production NEXT_TELEMETRY_DISABLED=1 \
    TRACEMIND_STORAGE_LOCAL_DIR=/tmp/uploads
COPY --from=node-bin /usr/local/bin/node /usr/local/bin/node

WORKDIR /app/backend
COPY backend/requirements.txt .
RUN pip install -r requirements.txt
COPY backend/alembic.ini ./
COPY backend/alembic ./alembic
COPY backend/app ./app

WORKDIR /app/web
COPY --from=web-build /web/.next/standalone ./
COPY --from=web-build /web/.next/static ./.next/static
COPY --from=web-build /web/public ./public

COPY deploy/start.sh /app/start.sh
RUN useradd --create-home --uid 10001 tracemind && chmod +x /app/start.sh
USER tracemind
EXPOSE 10000
CMD ["/app/start.sh"]
