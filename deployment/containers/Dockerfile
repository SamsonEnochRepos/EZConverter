# EZConverter converter service (remote runtime or desktop sidecar base).
# Built and run for the 0.1.0 release audit (read-only root, dropped capabilities): see docs/security/RELEASE_AUDIT.md.
FROM ubuntu:24.04 AS runtime
ENV DEBIAN_FRONTEND=noninteractive PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
# Engines run as separate processes. NOTE: Ubuntu's ffmpeg is a GPL build; see docs/security/license-inventory.md before distributing this image.
RUN apt-get update && apt-get install -y --no-install-recommends \
      python3 python3-venv python3-pip ffmpeg pandoc poppler-utils util-linux fonts-dejavu fonts-liberation \
      libreoffice-core libreoffice-writer libreoffice-calc libreoffice-impress libreoffice-draw \
    && rm -rf /var/lib/apt/lists/*
RUN useradd --system --create-home --uid 10001 ez && mkdir -p /data && chown ez:ez /data
WORKDIR /app
COPY backend /app/backend
# Ubuntu's venv ships pip 24.0, which has known advisories: upgrade it before installing anything.
RUN python3 -m venv /opt/venv && /opt/venv/bin/pip install --no-cache-dir --upgrade pip \
    && /opt/venv/bin/pip install --no-cache-dir "/app/backend[sevenzip]"
ENV PATH=/opt/venv/bin:$PATH EZ_DATA_DIR=/data EZ_LOCATION=remote EZ_AUTH_MODE=token EZ_NETWORK_ISOLATION=auto
USER ez
VOLUME ["/data"]
EXPOSE 8787
HEALTHCHECK --interval=30s --timeout=5s CMD python3 -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8787/v1/health', timeout=4).status==200 else 1)"
CMD ["python3", "-m", "ezconverter", "serve", "--host", "0.0.0.0", "--port", "8787"]
