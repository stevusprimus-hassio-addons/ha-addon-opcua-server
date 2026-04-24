# https://developers.home-assistant.io/docs/apps/configuration#app-dockerfile
ARG BUILD_FROM=ghcr.io/home-assistant/base:3.23
FROM ${BUILD_FROM}

# Home Assistant add-on base images are Alpine-based.

# PEP 668: Alpine marks system Python as externally managed; install Python deps in a venv.
RUN apk add --no-cache python3 py3-pip \
    && python3 -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir --upgrade pip \
    && /opt/venv/bin/pip install --no-cache-dir asyncua websockets aiohttp

# Ensure the venv is used by default
ENV PATH="/opt/venv/bin:${PATH}"

WORKDIR /app
COPY rootfs/ /

# Fallback: if the add-on is (mis)configured to run with s6 services, ensure the run script is executable.
RUN chmod +x /etc/services.d/opcua-server/run || true

# Run as a simple application (no s6-overlay services.d)
CMD [ "python", "-u", "/app/opcua_server.py" ]
