#!/bin/sh
# Prepare the instance directory, then hand over to gunicorn.
#
# Two modes, decided by whether S3 credentials are present:
#
#   With a mounted volume (Fly, Render Starter) and no S3 credentials, the
#   database simply lives on the volume and survives on its own.
#
#   Without a volume (Render free), the filesystem is rebuilt on every restart,
#   so Litestream restores the database from S3-compatible storage on boot and
#   streams every change back up while the app runs. The registry's issuing key
#   comes from ATTESTRA_ISSUER_KEY for the same reason.
#
# Note what is NOT covered in the second mode: the bytes of files a client
# uploads. Their database rows survive; the files themselves do not, so such a
# dataset cannot be re-protected after a restart. The bundled catalog is in the
# image and is always available.
set -e

INSTANCE_DIR="${ATTESTRA_INSTANCE_DIR:-/data/instance}"
mkdir -p "$INSTANCE_DIR/uploads"

DB_PATH="$INSTANCE_DIR/attestra.db"
APP_CMD="gunicorn --config /app/gunicorn.conf.py app:app"

echo "Attestra starting"
echo "  instance directory : $INSTANCE_DIR"
echo "  database           : ${DATABASE_URL:-$DB_PATH}"
echo "  uploads            : $INSTANCE_DIR/uploads"

if [ -z "$S3_BUCKET" ] || [ -z "$S3_ACCESS_KEY_ID" ]; then
    echo "  replication        : off (no S3 credentials set)"
    exec $APP_CMD
fi

# Written at boot rather than baked into the image, so the credentials stay in
# the host's secret store and never reach the repository or a layer.
cat > /etc/litestream.yml <<EOF
dbs:
  - path: ${DB_PATH}
    replicas:
      - type: s3
        bucket: ${S3_BUCKET}
        path: ${S3_PATH:-attestra}
        endpoint: ${S3_ENDPOINT}
        region: ${S3_REGION}
        access-key-id: ${S3_ACCESS_KEY_ID}
        secret-access-key: ${S3_SECRET_ACCESS_KEY}
        force-path-style: true
EOF
chmod 600 /etc/litestream.yml

echo "  replication        : $S3_BUCKET/${S3_PATH:-attestra} (Litestream)"

# -if-db-not-exists leaves a database already on disk alone; -if-replica-exists
# makes the very first boot, when nothing has been replicated yet, a no-op
# rather than an error.
litestream restore -if-db-not-exists -if-replica-exists -config /etc/litestream.yml "$DB_PATH"

if [ -f "$DB_PATH" ]; then
    echo "  restored           : $(wc -c < "$DB_PATH" | tr -d ' ') bytes from the replica"
else
    echo "  restored           : nothing yet — this is a first boot"
fi

# Litestream becomes the parent process: it replicates continuously and exits
# when gunicorn does, so the platform still sees one process to supervise.
exec litestream replicate -config /etc/litestream.yml -exec "$APP_CMD"
