# Blockhost + Mojang's Bedrock server in one container. The server itself is downloaded
# at first start (after you accept the Minecraft EULA), never baked into this image.
FROM python:3.12-slim-bookworm

# Bedrock Dedicated Server links against libcurl and needs CA certs for its own HTTPS calls.
RUN apt-get update \
 && apt-get install -y --no-install-recommends libcurl4 ca-certificates \
 && rm -rf /var/lib/apt/lists/* \
 && useradd --create-home --uid 1000 blockhost \
 && mkdir -p /data && chown blockhost:blockhost /data

WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY blockhost ./blockhost
RUN pip install --no-cache-dir . && rm -rf /app/build

ENV BLOCKHOST_DATA=/data \
    BLOCKHOST_HOST=0.0.0.0 \
    BLOCKHOST_PORT=8080 \
    BLOCKHOST_NO_BROWSER=1 \
    PYTHONUNBUFFERED=1

USER blockhost
VOLUME ["/data"]
EXPOSE 8080/tcp 19132/udp 19133/udp
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/auth/status', timeout=4)"
STOPSIGNAL SIGTERM
CMD ["blockhost"]
