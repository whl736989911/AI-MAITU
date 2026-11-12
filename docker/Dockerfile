# syntax=docker/dockerfile:1
# =============================================================================
# 麦途智造 — 多阶段 Docker 构建
#
# 阶段 1: 构建 React/TypeScript 前端
# 阶段 2: Python 运行时 + 预构建前端
#
# 用法（在仓库根目录执行，构建上下文为根目录）:
#   docker build -f docker/Dockerfile -t maitu:latest .
#   docker build -f docker/Dockerfile --build-arg MAITU_IMAGE_VARIANT=full -t maitu:latest-full .
#   docker compose -f docker/docker-compose.yml up -d
# =============================================================================


# ---------------------------------------------------------------------------
# 阶段 1 — 前端构建
# ---------------------------------------------------------------------------
FROM node:20-slim AS frontend-builder

ARG NPM_REGISTRY=
# Node heap ceiling for the vite build. Lower this (e.g. 1024) on low-memory
# build hosts (< 4G RAM) to avoid OOM kills — the value only caps the max,
# it is not pre-allocated.
ARG NODE_MAX_OLD_SPACE_SIZE=2048
WORKDIR /build/dashboard

COPY dashboard/package.json dashboard/package-lock.json ./
RUN --mount=type=cache,target=/root/.npm,sharing=locked \
    set -eu; \
    if [ -n "$NPM_REGISTRY" ]; then npm config set registry "$NPM_REGISTRY"; fi; \
    npm ci --no-audit \
    || { echo "npm ci failed — retrying clean"; rm -rf node_modules; npm ci --no-audit; }; \
    test -x node_modules/.bin/vite

COPY dashboard/ ./

RUN mkdir -p ../src/octop/dashboard

# 镜像构建跳过完整 tsc；生产打包由 vite/esbuild 完成（使用 lockfile 中的 vite，不用 npx）
RUN NODE_ENV=production NODE_OPTIONS="--max-old-space-size=${NODE_MAX_OLD_SPACE_SIZE}" npm run build:docker


# ---------------------------------------------------------------------------
# 阶段 2 — Python 运行时
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS runtime

LABEL maintainer="Suzhou Maitou"
LABEL org.opencontainers.image.title="AIWAY"
LABEL org.opencontainers.image.description="AIWAY AI tools platform."
LABEL org.opencontainers.image.licenses="MIT"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOME=/data \
    OCTOP_BIND_HOST=0.0.0.0 \
    OCTOP_PORT=8088 \
    OCTOP_LOG_LEVEL=info \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/usr/local/maitu-venv \
    UV_PYTHON_DOWNLOADS=never \
    MAITU_BRAND_CONFIG_FILE=/app/brand.config.json \
    MAITU_LICENSE_TRUSTED_KEYS_FILE=/run/maitu-trust/license-trusted-keys.json \
    MAITU_LICENSE_STATE_DIR=/var/lib/maitu-license \
    MAITU_UPDATE_TRUSTED_KEYS_FILE=/run/maitu-trust/release-trusted-keys.json \
    MAITU_UPDATE_QUEUE_DIR=/var/lib/maitu-update-queue

ARG PIP_INDEX_URL=
ARG PIP_TRUSTED_HOST=
ARG APT_MIRROR=

RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    if [ -n "$APT_MIRROR" ]; then \
        printf 'Types: deb\nURIs: https://%s/debian\nSuites: trixie trixie-updates\nComponents: main contrib non-free non-free-firmware\n\nTypes: deb\nURIs: https://%s/debian-security\nSuites: trixie-security\nComponents: main contrib non-free non-free-firmware\n' \
            "$APT_MIRROR" "$APT_MIRROR" > /etc/apt/sources.list.d/debian.sources; \
    fi \
    && apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        libffi-dev \
        curl \
        git \
        bubblewrap \
        libreoffice-writer \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.7 /uv /uvx /bin/

WORKDIR /app

# 默认 lite 保持原有镜像依赖；full 仅预装 Python 组件，不下载模型或启用功能。
ARG MAITU_IMAGE_VARIANT=lite
COPY pyproject.toml uv.lock README.md LICENSE ./
RUN --mount=type=cache,target=/root/.cache/uv \
    if [ -n "$PIP_INDEX_URL" ]; then \
        export UV_INDEX_URL="$PIP_INDEX_URL"; \
        if [ -n "$PIP_TRUSTED_HOST" ]; then export UV_INSECURE_HOST="$PIP_TRUSTED_HOST"; fi; \
    fi \
    && case "$MAITU_IMAGE_VARIANT" in \
         lite|full) ;; \
         *) echo "MAITU_IMAGE_VARIANT must be 'lite' or 'full', got '$MAITU_IMAGE_VARIANT'" >&2; exit 1 ;; \
       esac \
    && if [ "$MAITU_IMAGE_VARIANT" = "full" ]; then \
         uv sync --frozen --no-install-project --no-dev --extra browser --extra desktop --extra local-embedding --extra knowledge-ocr; \
       else \
         uv sync --frozen --no-install-project --no-dev --extra browser; \
       fi


ENV PATH="/usr/local/maitu-venv/bin:$PATH" \
    PLAYWRIGHT_BROWSERS_PATH=/opt/maitu-browser \
    BROWSER_USE_NO_SANDBOX=0 \
    BROWSER_USE_CHROME_BIN=/usr/local/bin/maitu-chromium

COPY src/ ./src/
COPY --from=frontend-builder /build/src/octop/dashboard/ ./src/octop/dashboard/

COPY .env.example ./
COPY brand.config.json ./
COPY docker/docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN sed -i 's/\r$//' /usr/local/bin/docker-entrypoint.sh \
    && chmod +x /usr/local/bin/docker-entrypoint.sh

RUN --mount=type=cache,target=/root/.cache/uv \
    if [ -n "$PIP_INDEX_URL" ]; then \
        export UV_INDEX_URL="$PIP_INDEX_URL"; \
        if [ -n "$PIP_TRUSTED_HOST" ]; then export UV_INSECURE_HOST="$PIP_TRUSTED_HOST"; fi; \
    fi \
    && if [ "$MAITU_IMAGE_VARIANT" = "full" ]; then \
         apt-get update && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 \
         && uv sync --frozen --no-dev --extra browser --extra desktop --extra local-embedding --extra knowledge-ocr \
         && python -c "import cv2, fastembed, huggingface_hub, mss, onnxruntime, pymupdf, rapidocr; import importlib.util; assert importlib.util.find_spec('pynput')"; \
       else \
         uv sync --frozen --no-dev --extra browser; \
       fi \
    && apt-get update && apt-get install -y --no-install-recommends fonts-noto-cjk \
    && apt-get purge -y --auto-remove build-essential \
    && rm -rf /var/lib/apt/lists/* /tmp/*

# Install the trusted browser and shared libraries before dropping privileges.
# Its user-namespace and seccomp sandboxes remain enabled at runtime.
RUN python -m playwright install --with-deps chromium \
    && python -c 'from pathlib import Path; from playwright.sync_api import sync_playwright; p = sync_playwright().start(); Path("/usr/local/bin/maitu-chromium").symlink_to(p.chromium.executable_path); p.stop()' \
    && chmod -R a+rX /opt/maitu-browser \
    && useradd --uid 1000 --user-group --home-dir /data --no-create-home maitu \
    && mkdir -p /data/.octop /var/lib/maitu-license /var/lib/maitu-update-queue \
    && chown -R maitu:maitu /data /var/lib/maitu-license /var/lib/maitu-update-queue

EXPOSE 8088

USER 1000:1000

HEALTHCHECK --interval=30s --timeout=10s --start-period=90s --retries=3 \
    CMD ["sh", "-c", "curl -f http://localhost:${OCTOP_PORT:-8088}/api/health || exit 1"]

ENTRYPOINT ["docker-entrypoint.sh"]
CMD []
