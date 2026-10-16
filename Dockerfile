# pyPLCHMI – web HMI for Siemens S7, Modbus and BACnet
#
#   docker build -t pyplchmi .
#   docker compose up -d            (see docker-compose.yml and docs/DOCKER.md)
#
# One container = one HMI server process (waitress, several threads).  Do not
# scale it to several replicas: every process opens its own PLC connections
# and runs its own alarm scanner and historian.

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    FLASK_APP=wsgi.py \
    FLASK_CONFIG=production \
    HMI_PORT=8000 \
    HMI_THREADS=8

WORKDIR /app

# Python packages first (cached layer).  python-snap7, pymodbus and BAC0 ship
# as ready-made wheels, so no compiler and no native snap7 library are needed.
COPY requirements.txt .
RUN pip install -r requirements.txt

# Application code (see .dockerignore for what stays out)
COPY . .

# Unprivileged user; "dialout" gives access to serial ports (Modbus RTU).
RUN useradd --system --uid 1000 --gid 0 --groups dialout --home-dir /app hmi \
    && mkdir -p /app/instance \
    && chown -R hmi:0 /app/instance \
    && chmod -R g=u /app/instance \
    && chmod +x /app/docker/entrypoint.sh
USER hmi

# Database (SQLite), uploaded images, library files and language files
VOLUME ["/app/instance"]

# Web UI.  BACnet/IP also uses UDP 47808 (see docs/DOCKER.md).
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/api/health' % os.environ.get('HMI_PORT', '8000'), timeout=4)" || exit 1

ENTRYPOINT ["/app/docker/entrypoint.sh"]
CMD ["sh", "-c", "exec waitress-serve --host 0.0.0.0 --port ${HMI_PORT} --threads ${HMI_THREADS} wsgi:app"]
