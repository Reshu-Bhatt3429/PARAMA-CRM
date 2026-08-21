#!/usr/bin/env bash

# Run CRM's fixture-generating backend tests on a disposable Frappe site.
# The reference site is read only: it supplies the installed-app list and an
# integrity snapshot, but no data or credentials are copied into the test site.

set -Eeuo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
default_bench_dir="$(cd "${repo_dir}/.." && pwd)/crm-bench"
bench_dir="${CRM_BENCH_DIR:-${default_bench_dir}}"
reference_site="${1:-${CRM_REFERENCE_SITE:-}}"

if [[ ! -f "${bench_dir}/sites/common_site_config.json" ]]; then
	echo "Frappe Bench was not found at ${bench_dir}." >&2
	echo "Set CRM_BENCH_DIR to the Bench directory and try again." >&2
	exit 1
fi

if [[ -z "${reference_site}" ]]; then
	reference_site="$(${bench_dir}/env/bin/python - "${bench_dir}/sites/common_site_config.json" <<'PY'
import json
import sys

with open(sys.argv[1]) as handle:
    print(json.load(handle).get("default_site", ""))
PY
)"
fi

if [[ -z "${reference_site}" || ! "${reference_site}" =~ ^[A-Za-z0-9.-]+$ ]]; then
	echo "Usage: $0 <reference-site>" >&2
	exit 2
fi

if [[ ! -f "${bench_dir}/sites/${reference_site}/site_config.json" ]]; then
	echo "Reference site ${reference_site} was not found in ${bench_dir}/sites." >&2
	exit 1
fi

timestamp="$(date -u +%Y%m%d%H%M%S)"
test_site="${CRM_TEST_SITE_NAME:-crm-test-${timestamp}-$$.localhost}"
if [[ ! "${test_site}" =~ ^crm-test-[A-Za-z0-9-]+\.localhost$ ]]; then
	echo "CRM_TEST_SITE_NAME must match crm-test-<unique-name>.localhost." >&2
	exit 2
fi

site_dir="${bench_dir}/sites/${test_site}"
if [[ -e "${site_dir}" ]]; then
	echo "Refusing to reuse existing disposable site ${test_site}." >&2
	exit 1
fi

temp_dir="$(mktemp -d "${TMPDIR:-/tmp}/parama-crm-tests.XXXXXX")"
db_suffix="$(${bench_dir}/env/bin/python - <<'PY'
import secrets

print(secrets.token_hex(6))
PY
)"
db_name="_crmtest_${db_suffix}"
db_user="${db_name}"
db_password="$(${bench_dir}/env/bin/python - <<'PY'
import secrets

print(secrets.token_hex(24))
PY
)"
admin_password="$(${bench_dir}/env/bin/python - <<'PY'
import secrets

print(secrets.token_urlsafe(32))
PY
)"

db_root_user="${CRM_TEST_DB_ROOT_USER:-root}"
db_root_password="${CRM_TEST_DB_ROOT_PASSWORD:-}"
if [[ -n "${CRM_TEST_DB_ROOT_PASSWORD_FILE:-}" ]]; then
	if [[ ! -s "${CRM_TEST_DB_ROOT_PASSWORD_FILE}" ]]; then
		echo "CRM_TEST_DB_ROOT_PASSWORD_FILE is missing or empty." >&2
		exit 1
	fi
	db_root_password="$(tr -d '\r\n' < "${CRM_TEST_DB_ROOT_PASSWORD_FILE}")"
fi

db_host="${CRM_TEST_DB_HOST:-}"
db_port="${CRM_TEST_DB_PORT:-}"
db_socket="${CRM_TEST_DB_SOCKET:-}"
if [[ -z "${db_host}" || -z "${db_port}" ]]; then
	readarray -t reference_db < <(${bench_dir}/env/bin/python - "${bench_dir}/sites/${reference_site}/site_config.json" <<'PY'
import json
import sys

with open(sys.argv[1]) as handle:
    config = json.load(handle)
print(config.get("db_host", "127.0.0.1"))
print(config.get("db_port", 3306))
PY
)
	db_host="${db_host:-${reference_db[0]}}"
	db_port="${db_port:-${reference_db[1]}}"
fi

if [[ -z "${db_socket}" && -f "${bench_dir}/config/mariadb.cnf" ]]; then
	db_socket="$(sed -n 's/^[[:space:]]*socket[[:space:]]*=[[:space:]]*//p' "${bench_dir}/config/mariadb.cnf" | head -n 1)"
fi

db_mode="tcp"
db_user_host="%"
if [[ -z "${db_root_password}" && -n "${db_socket}" && -S "${db_socket}" ]]; then
	db_mode="socket"
	db_user_host="localhost"
fi

run_admin_sql() {
	local statement="$1"
	if [[ "${db_mode}" == "socket" ]]; then
		mariadb --batch --skip-column-names --socket="${db_socket}" --user="${db_root_user}" \
			--execute="${statement}"
	else
		if [[ -z "${db_root_password}" ]]; then
			echo "A MariaDB administrator password is required for TCP test-database setup." >&2
			echo "Set CRM_TEST_DB_ROOT_PASSWORD_FILE (preferred) or CRM_TEST_DB_ROOT_PASSWORD." >&2
			return 1
		fi
		MYSQL_PWD="${db_root_password}" mariadb --batch --skip-column-names --protocol=tcp \
			--host="${db_host}" --port="${db_port}" --user="${db_root_user}" \
			--execute="${statement}"
	fi
}

reference_counts() {
	local doctype
	for doctype in "CRM Lead" "CRM Deal" "CRM Itinerary"; do
		printf '%s=' "${doctype}"
		(
			cd "${bench_dir}"
			bench --site "${reference_site}" execute frappe.db.count --args "[\"${doctype}\"]"
		)
	done
}

reference_before="$(reference_counts)"
db_created=0

cleanup() {
	local exit_code=$?
	local cleanup_failed=0
	local reference_after=""
	trap - EXIT INT TERM
	set +e

	if [[ "${CRM_KEEP_TEST_SITE:-0}" == "1" ]]; then
		echo "Keeping disposable site ${test_site} because CRM_KEEP_TEST_SITE=1."
	else
		if [[ "${db_created}" == "1" ]]; then
			run_admin_sql "DROP DATABASE IF EXISTS \`${db_name}\`; DROP USER IF EXISTS '${db_user}'@'localhost'; DROP USER IF EXISTS '${db_user}'@'%';" || cleanup_failed=1
		fi

		if [[ -d "${site_dir}" ]]; then
			case "${site_dir}" in
				"${bench_dir}/sites/"crm-test-*.localhost)
					rm -rf -- "${site_dir}" || cleanup_failed=1
					;;
				*)
					echo "Refusing unsafe disposable-site cleanup target: ${site_dir}" >&2
					cleanup_failed=1
					;;
			esac
		fi
	fi

	reference_after="$(reference_counts)" || cleanup_failed=1
	if [[ "${reference_after}" != "${reference_before}" ]]; then
		echo "CRITICAL: reference-site CRM counts changed during isolated tests." >&2
		echo "Before: ${reference_before}" >&2
		echo "After:  ${reference_after}" >&2
		cleanup_failed=1
	fi

	rm -rf -- "${temp_dir}"
	if [[ "${cleanup_failed}" == "1" ]]; then
		exit_code=1
	fi
	exit "${exit_code}"
}

trap cleanup EXIT INT TERM

echo "Checking MariaDB administrator access for disposable test setup..."
run_admin_sql "SELECT 1" >/dev/null

echo "Creating isolated database ${db_name} for ${test_site}..."
db_created=1
run_admin_sql "CREATE DATABASE \`${db_name}\` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci; CREATE USER '${db_user}'@'${db_user_host}' IDENTIFIED BY '${db_password}'; GRANT ALL PRIVILEGES ON \`${db_name}\`.* TO '${db_user}'@'${db_user_host}'; FLUSH PRIVILEGES;"

apps_json="$(cd "${bench_dir}" && bench --site "${reference_site}" list-apps --format json)"
readarray -t install_apps < <(APPS_JSON="${apps_json}" ${bench_dir}/env/bin/python - "${reference_site}" <<'PY'
import json
import os
import sys

site = sys.argv[1]
apps = json.loads(os.environ["APPS_JSON"]).get(site, [])
for app in apps:
    if app != "frappe":
        print(app)
PY
)

if [[ ! " ${install_apps[*]} " =~ [[:space:]]crm[[:space:]] ]]; then
	echo "Reference site ${reference_site} does not have the crm app installed." >&2
	exit 1
fi

new_site_args=(
	bench new-site "${test_site}"
	--no-setup-db
	--db-type mariadb
	--db-name "${db_name}"
	--db-user "${db_user}"
	--db-password "${db_password}"
	--admin-password "${admin_password}"
)

if [[ "${db_mode}" == "socket" ]]; then
	new_site_args+=(--db-socket "${db_socket}")
else
	new_site_args+=(--db-host "${db_host}" --db-port "${db_port}")
fi

for app in "${install_apps[@]}"; do
	new_site_args+=(--install-app "${app}")
done

echo "Installing ${install_apps[*]} on disposable site ${test_site}..."
(cd "${bench_dir}" && "${new_site_args[@]}")

(
	cd "${bench_dir}"
	bench --site "${test_site}" set-config allow_tests 1 >/dev/null
	bench --site "${test_site}" set-config developer_mode 0 >/dev/null
	bench --site "${test_site}" set-config ignore_csrf 0 >/dev/null
	bench --site "${test_site}" set-config maintenance_mode 0 >/dev/null
	bench --site "${test_site}" set-config mute_emails 1 >/dev/null
)

test_args=(bench --site "${test_site}" run-tests --app crm)
if [[ "${CRM_TEST_COVERAGE:-0}" == "1" ]]; then
	test_args+=(--coverage)
fi
if [[ "${CRM_TEST_FAILFAST:-0}" == "1" ]]; then
	test_args+=(--failfast)
fi

echo "Running the complete CRM backend suite on ${test_site}..."
(cd "${bench_dir}" && "${test_args[@]}")

echo "Backend tests passed on disposable site ${test_site}."
