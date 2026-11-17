# syntax=docker/dockerfile:1.7
#
# Build from the PyShield repository root:
#   docker compose -f license-portal/docker/compose.yaml build
#
# The web stage builds the React console; the runtime stage contains only
# Python, the portal, the shared pyshield package and the built SPA.

ARG NODE_IMAGE=node:22-bookworm-slim
ARG PYTHON_IMAGE=python:3.12-slim-bookworm

FROM ${NODE_IMAGE} AS web
WORKDIR /build/license-portal/web

ARG NPM_REGISTRY=
COPY license-portal/web/package.json license-portal/web/package-lock.json ./
RUN if [ -n "$NPM_REGISTRY" ]; then npm config set registry "$NPM_REGISTRY"; fi \
    && npm ci --no-audit --no-fund

COPY license-portal/web/index.html license-portal/web/tsconfig.json \
     license-portal/web/vite.config.ts ./
COPY license-portal/web/src ./src
COPY license-portal/web/public ./public
RUN npm run build


FROM ${PYTHON_IMAGE} AS runtime

ARG APP_UID=10001
ARG APP_GID=10001
ARG APP_PORT=8091

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/license-portal:/app \
    HOME=/home/pyshield \
    PYSHELD_PORTAL_DATA_DIR=/etc/pyshield-portal \
    PYSHELD_PORTAL_HOST=0.0.0.0 \
    PYSHELD_PORTAL_PORT=${APP_PORT} \
    PYSHELD_PORTAL_WEB_DIR=/app/license-portal/web/dist

# gosu lets the root entrypoint prepare the host-mounted data directory and
# then run the application as an unprivileged user. ca-certificates is needed
# for SMTP STARTTLS and HTTPS calls to Turnstile.
RUN apt-get update \
    && apt-get install -y --no-install-recommends gosu ca-certificates passwd \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid "${APP_GID}" pyshield \
    && useradd --uid "${APP_UID}" --gid pyshield --home-dir /home/pyshield \
               --create-home --shell /usr/sbin/nologin pyshield

WORKDIR /app

COPY pyshield ./pyshield
COPY license-portal/license_portal ./license-portal/license_portal
COPY --from=web /build/license-portal/web/dist ./license-portal/web/dist
COPY license-portal/docker/entrypoint.sh /usr/local/bin/pyshield-portal-entrypoint

RUN sed -i 's/\r$//' /usr/local/bin/pyshield-portal-entrypoint \
    && chmod 0755 /usr/local/bin/pyshield-portal-entrypoint

EXPOSE ${APP_PORT}
STOPSIGNAL SIGTERM

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import urllib.request,sys,os; r=urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('PYSHELD_PORTAL_PORT','8091')+'/health', timeout=3); sys.exit(0 if r.status == 200 else 1)"

ENTRYPOINT ["/usr/local/bin/pyshield-portal-entrypoint"]
