# https://developers.home-assistant.io/docs/apps/configuration#app-dockerfile
ARG BUILD_FROM=ghcr.io/home-assistant/base:3.23
FROM ${BUILD_FROM}

# Home Assistant add-on base images are Alpine-based.

RUN apk add --no-cache python3 py3-pip \
    && pip3 install --no-cache-dir asyncua websockets aiohttp

WORKDIR /app
COPY rootfs/ /

RUN chmod +x /etc/services.d/opcua-server/run

CMD [ "/init" ]
