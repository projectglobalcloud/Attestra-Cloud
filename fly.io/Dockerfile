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

# The application: the protocol and the console.
COPY code/ /app/code/
COPY web/ /app/web/
# The consortium catalog is generated here rather than carried in the
# repository: it is 125 MB of synthetic CSV that `datasets/generate.py`
# reproduces exactly from a fixed seed, so shipping the generator instead of
# the output keeps the repository small and the image self-contained.
RUN cd /app/code && python -m datasets.generate --dataset all --size medium --quiet \
    && du -sh /app/code/datasets/data

# Litestream replicates the SQLite database to S3-compatible storage
# (Supabase, R2, S3). It is what makes a host with no persistent disk viable:
# the database is restored on boot and streamed back up as it changes. Safe
# here only because this app runs exactly one instance — see gunicorn.conf.py.
ARG TARGETARCH
RUN set -eux; \
    case "${TARGETARCH:-amd64}" in \
      amd64) LS_ARCH=x86_64 ;; \
      arm64) LS_ARCH=arm64 ;; \
      *) echo "unsupported architecture: ${TARGETARCH}" >&2; exit 1 ;; \
    esac; \
    apt-get update; \
    apt-get install -y --no-install-recommends curl ca-certificates; \
    curl -fsSL -o /tmp/litestream.tar.gz \
      "https://github.com/benbjohnson/litestream/releases/download/v0.5.17/litestream-0.5.17-linux-${LS_ARCH}.tar.gz"; \
    tar -C /usr/local/bin -xzf /tmp/litestream.tar.gz litestream; \
    rm -f /tmp/litestream.tar.gz; \
    apt-get purge -y --auto-remove curl; \
    rm -rf /var/lib/apt/lists/*; \
    litestream version

COPY fly.io/gunicorn.conf.py /app/gunicorn.conf.py
COPY fly.io/entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

# Everything that must outlive a deploy lives under the instance directory.
# On a host with a volume that directory is the volume. Without one, the
# database is kept alive by Litestream and the issuing key by
# ATTESTRA_ISSUER_KEY; uploaded files do not yet survive (see render/README).
ENV ATTESTRA_INSTANCE_DIR=/data/instance \
    PORT=8080 \
    PYTHONPATH=/app/code

WORKDIR /app/web
EXPOSE 8080
ENTRYPOINT ["/app/entrypoint.sh"]
