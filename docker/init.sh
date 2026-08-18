#!/bin/bash

if [ -d "/home/frappe/frappe-bench/apps/frappe" ]; then
    echo "Bench already exists, skipping init"
    cd frappe-bench
    bench start
else
    echo "Creating new bench..."
fi

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

bench get-app crm --branch main
# Unpinned: this tracks whatever frappe_whatsapp's default branch holds today.
# Add --branch <tag-or-branch> to pin it before using this on a client box.
bench get-app https://github.com/shridarpatil/frappe_whatsapp.git

# Set ADMIN_PASSWORD in the environment before running this on any box someone
# else can reach. The fallback below is a demo-only credential.
bench new-site crm.localhost \
    --force \
    --mariadb-root-password 123 \
    --admin-password "${ADMIN_PASSWORD:-admin}" \
    --no-mariadb-socket

bench --site crm.localhost install-app crm
bench --site crm.localhost install-app frappe_whatsapp
# developer_mode leaks the doctype metadata to guests; server_script_enabled lets
# a System Manager run arbitrary Python. Both stay off.
bench --site crm.localhost set-config developer_mode 0
bench --site crm.localhost set-config mute_emails 1
bench --site crm.localhost set-config server_script_enabled 0
bench --site crm.localhost clear-cache
bench use crm.localhost

bench start
