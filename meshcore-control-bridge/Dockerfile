FROM ghcr.io/home-assistant/base:latest

ARG BUILD_VERSION=0.1.13
ARG BUILD_ARCH=amd64

SHELL ["/bin/bash", "-o", "pipefail", "-c"]
WORKDIR /app

LABEL \
  io.hass.version="${BUILD_VERSION}" \
  io.hass.type="app" \
  io.hass.arch="${BUILD_ARCH}" \
  org.opencontainers.image.source="https://github.com/j3udiel/meshcore-control-bridge" \
  org.opencontainers.image.licenses="Apache-2.0" \
  org.opencontainers.image.description="Offline command bridge between MeshCore and Home Assistant"

RUN apk add --no-cache python3 py3-pip

COPY pyproject.toml README.md /app/package/
COPY src /app/package/src
RUN pip3 install --break-system-packages --no-cache-dir /app/package

COPY meshcore-control-bridge/run.sh /run.sh
RUN chmod a+x /run.sh

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD python3 -m meshcore_control.homeassistant_app_health

CMD ["/run.sh"]
