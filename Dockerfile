# ArkAngel website. Build: docker compose build. See docs/deploy.md.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    ARK_ANGEL_DATA_DIR=/data/cases

WORKDIR /build
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install ".[web]" && rm -rf /build

# Run as an unprivileged user that owns only /data.
RUN useradd --system --uid 10001 --home-dir /data --shell /usr/sbin/nologin arkangel \
    && mkdir -p /data && chown arkangel:arkangel /data
USER arkangel
WORKDIR /data
VOLUME /data
EXPOSE 8765

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/privacy', timeout=4)"

# The port is only reachable by the Caddy container (it isn't published), so trusting any proxy peer is safe here.
CMD ["ark-angel", "web", "serve", "--host", "0.0.0.0", "--behind-proxy", "--proxy-ip", "*", "--wait-for-admin"]
