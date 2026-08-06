#!/bin/bash

# Local-dev variant of init.sh: installs the CRM app from the mounted local
# repo (/workspace/app) instead of cloning frappe/crm from GitHub, so local
# commits are what gets installed. Re-runs reuse the existing bench.

set -e

if [ -d "/home/frappe/frappe-bench/apps/frappe" ]; then
    echo "Bench already exists, skipping init"
    cd frappe-bench
    exec bench start
fi

echo "Creating new bench..."

bench init --skip-redis-config-generation frappe-bench --version version-15

cd frappe-bench

# Keep apps importable while bench installs editable packages. This is needed
# with the Python/uv combination used by the current frappe/bench image.
export PYTHONPATH="/home/frappe/frappe-bench/apps${PYTHONPATH:+:$PYTHONPATH}"

# Use containers instead of localhost
bench set-mariadb-host mariadb
bench set-redis-cache-host redis://redis:6379
bench set-redis-queue-host redis://redis:6379
bench set-redis-socketio-host redis://redis:6379

# Remove redis, watch from Procfile
sed -i '/redis/d' ./Procfile
sed -i '/watch/d' ./Procfile

# Install the local fork (clones committed state of the mounted repo)
bench get-app /workspace/app
bench get-app https://github.com/shridarpatil/frappe_whatsapp.git

bench new-site crm.localhost \
    --force \
    --mariadb-root-password 123 \
    --admin-password admin \
    --no-mariadb-socket

bench --site crm.localhost install-app crm
bench --site crm.localhost install-app frappe_whatsapp
bench --site crm.localhost set-config developer_mode 1
bench --site crm.localhost set-config mute_emails 1
bench --site crm.localhost set-config server_script_enabled 1
bench --site crm.localhost clear-cache
bench use crm.localhost

bench start
