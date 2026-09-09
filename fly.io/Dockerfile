# Attestra — container image for Fly.io
#
# Built from the project root, so both halves of the project come in: the
# protocol implementation in code/ and the console in web/. Nothing is compiled
# and nothing is fetched at boot; py_ecc is pure Python, which is why this
# image builds on any architecture without a toolchain.

FROM python:3.12-slim

# Python that behaves in a container: no .pyc litter, unbuffered logs so
# `fly logs` shows what is happening as it happens.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Dependencies first, so a code change does not reinstall the world.
COPY web/requirements.txt /app/web/requirements.txt
COPY code/requirements.txt /app/code/requirements.txt
RUN pip install --no-cache-dir -r /app/web/requirements.txt \
    && pip install --no-cache-dir py_ecc>=7.0.0

# The application: the protocol, the console, and the consortium catalog the
# console offers to protect.
COPY code/ /app/code/
COPY web/ /app/web/
COPY fly.io/gunicorn.conf.py /app/gunicorn.conf.py
COPY fly.io/entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

# Everything that must outlive a deploy lives on the mounted volume: the
# database, the uploaded files, the registry's issuing key, the session secret.
ENV ATTESTRA_INSTANCE_DIR=/data/instance \
    PORT=8080 \
    PYTHONPATH=/app/code

WORKDIR /app/web
EXPOSE 8080
ENTRYPOINT ["/app/entrypoint.sh"]
