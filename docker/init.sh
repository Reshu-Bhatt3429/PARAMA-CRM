#!/usr/bin/env bash

# `set -e` plus the `exec` below are load bearing: without them a failing
# `bench start` fell through to `bench new-site --force`, which DROPS the site
# database of an existing bench.
set -Eeuo pipefail

read_secret() {
    local secret_path="$1"
    if [[ ! -s "${secret_path}" ]]; then
        echo "FATAL: required Docker secret ${secret_path} is missing or empty." >&2
        exit 1
    fi
    tr -d '\r\n' < "${secret_path}"
}

mariadb_root_password="$(read_secret /run/secrets/mariadb_root_password)"
crm_admin_password="$(read_secret /run/secrets/crm_admin_password)"
frappe_branch="${FRAPPE_BRANCH:-develop}"
frappe_commit="${FRAPPE_COMMIT:-c30e0e6de2ca93bb8fc603f84a496d4d0e02ddf5}"
crm_commit="${CRM_COMMIT:-a8e6482e235d949cdddf7f42d738bcdee8a4990b}"
frappe_whatsapp_commit="${FRAPPE_WHATSAPP_COMMIT:-08bc1f6af2e36022d7a5f77641c8ba2ef8c16aaa}"

if [ -d "/home/frappe/frappe-bench/apps/frappe" ]; then
    echo "Bench already exists, skipping init"
    cd frappe-bench
    exec bench start
fi

echo "Creating new bench..."

bench init --skip-redis-config-generation frappe-bench --version "${frappe_branch}"

cd frappe-bench

# Frappe 17 currently lives on the develop branch. Detach at the tested commit
# so a future branch update cannot silently change a rebuilt container.
git -C apps/frappe fetch --depth 1 origin "${frappe_commit}"
git -C apps/frappe checkout --detach "${frappe_commit}"
bench setup requirements frappe
bench build --app frappe

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

bench get-app --branch main https://github.com/nissu99/PARAMA-CRM.git
git -C apps/crm fetch --depth 1 origin "${crm_commit}"
git -C apps/crm checkout --detach "${crm_commit}"
bench setup requirements crm
bench build --app crm
# The tested companion-app commit is detached after cloning for reproducibility.
bench get-app --branch master https://github.com/shridarpatil/frappe_whatsapp.git
git -C apps/frappe_whatsapp fetch --depth 1 origin "${frappe_whatsapp_commit}"
git -C apps/frappe_whatsapp checkout --detach "${frappe_whatsapp_commit}"
bench setup requirements frappe_whatsapp
bench build --app frappe_whatsapp

bench new-site crm.localhost \
    --force \
    --mariadb-root-password "${mariadb_root_password}" \
    --admin-password "${crm_admin_password}" \
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

exec bench start
