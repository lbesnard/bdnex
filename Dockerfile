# bdnex with its web page: `docker compose up`. See docs/guides/docker.md.
FROM python:3.12-slim

# bsdtar reads CBR (RAR) files; build tools are not needed, every dependency has a wheel.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libarchive-tools \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /src
COPY pyproject.toml README.md LICENSE ./
COPY bdnex ./bdnex
# EXTRAS=api,cover adds OpenCV for the cover check (about 100 MB more)
ARG EXTRAS=api
RUN pip install --no-cache-dir ".[${EXTRAS}]" && rm -rf /src

# The container runs as the user and group the compose file gives (PUID / PGID), so the files it writes in
# your comics folder belong to you. Nothing of bdnex lives under the home folder: it only has to be writable.
ENV HOME=/tmp/home BDNEX_CONFIG=/config/bdnex.yaml PYTHONUNBUFFERED=1
RUN mkdir -p /tmp/home /config /data /covers /comics && chmod 1777 /tmp/home
COPY docker/bdnex.yaml /usr/share/bdnex-docker.yaml
COPY docker/entrypoint.sh /usr/local/bin/bdnex-entrypoint
RUN chmod +x /usr/local/bin/bdnex-entrypoint

EXPOSE 8765
VOLUME ["/comics", "/config", "/data", "/covers"]
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
    CMD python -c "import urllib.request as u; u.urlopen('http://127.0.0.1:8765/healthz', timeout=4)"
ENTRYPOINT ["bdnex-entrypoint"]
CMD ["bdnex", "serve", "--host", "0.0.0.0", "--allow-changes"]
