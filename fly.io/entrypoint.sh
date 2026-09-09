#!/bin/sh
# Prepare the volume, then hand over to gunicorn.
#
# The volume starts empty on a brand-new machine. Everything the app needs to
# keep is created under it on first boot: the SQLite database, the uploads
# directory, the registry's issuing key and the session secret.
set -e

INSTANCE_DIR="${ATTESTRA_INSTANCE_DIR:-/data/instance}"
mkdir -p "$INSTANCE_DIR/uploads"

echo "Attestra starting"
echo "  instance directory : $INSTANCE_DIR"
echo "  database           : ${DATABASE_URL:-sqlite (on the volume)}"
echo "  uploads            : $INSTANCE_DIR/uploads"

exec gunicorn --config /app/gunicorn.conf.py app:app
