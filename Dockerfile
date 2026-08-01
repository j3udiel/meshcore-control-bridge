FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1
WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src

RUN pip install --no-cache-dir .

RUN useradd --system --create-home bridge
USER bridge

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -m meshcore_control.healthcheck

CMD ["meshcore-control-bridge", "--config", "/config/config.yaml"]
