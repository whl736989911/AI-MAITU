FROM node:22-bookworm-slim AS dashboard
WORKDIR /build/dashboard
COPY dashboard/package.json dashboard/package-lock.json ./
RUN npm ci
COPY dashboard/ ./
RUN npm run build

FROM python:3.12-slim-bookworm AS runtime
COPY --from=ghcr.io/astral-sh/uv:0.9.18 /uv /uvx /bin/
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_LINK_MODE=copy \
    LICENSE_CENTER_DATA=/data \
    LICENSE_CENTER_DASHBOARD_DIR=/app/license_center/dashboard \
    LICENSE_CENTER_COOKIE_SECURE=1
WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY license_center/ ./license_center/
RUN uv sync --frozen --no-dev && \
    groupadd --gid 10001 center && useradd --uid 10001 --gid center --no-create-home center && \
    mkdir -p /data && chown center:center /data && chmod 700 /data
COPY --from=dashboard /build/license_center/dashboard ./license_center/dashboard
USER center
EXPOSE 8090
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["/app/.venv/bin/python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8090/health', timeout=4).read()"]
ENTRYPOINT ["/app/.venv/bin/license-center"]
CMD ["run", "--host", "0.0.0.0", "--port", "8090"]
