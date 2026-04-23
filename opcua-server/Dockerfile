ARG BUILD_FROM
FROM ${BUILD_FROM}

# Home Assistant add-on base images are Alpine-based.

RUN apk add --no-cache python3 py3-pip \
    && pip3 install --no-cache-dir asyncua websockets aiohttp

WORKDIR /app
COPY rootfs/ /

RUN chmod +x /etc/services.d/opcua-server/run

CMD [ "/init" ]
