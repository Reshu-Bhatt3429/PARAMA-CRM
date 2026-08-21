#!/usr/bin/env bash

set -Eeuo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
default_bench_dir="$(cd "${repo_dir}/.." && pwd)/crm-bench"
bench_dir="${CRM_BENCH_DIR:-${default_bench_dir}}"
site="${1:-}"

if [[ -z "${site}" || ! "${site}" =~ ^[A-Za-z0-9.-]+$ ]]; then
	echo "Usage: $0 <site-name>" >&2
	exit 2
fi

if [[ ! -f "${bench_dir}/sites/${site}/site_config.json" ]]; then
	echo "Site ${site} was not found in ${bench_dir}/sites." >&2
	exit 1
fi

echo "Linting and testing the application..."
(cd "${repo_dir}" && uvx ruff check crm)
(
	cd "${repo_dir}/frontend"
	yarn install --frozen-lockfile
	yarn eslint .
	yarn test:run
	yarn audit --groups dependencies --level low
	yarn build
)

echo "Taking a database and file backup before migration..."
(cd "${bench_dir}" && bench --site "${site}" backup --with-files)

echo "Migrating ${site}..."
(cd "${bench_dir}" && bench --site "${site}" migrate)

echo "Applying secure production site settings..."
for setting in ignore_csrf allow_tests developer_mode server_script_enabled live_reload; do
	(cd "${bench_dir}" && bench --site "${site}" set-config "${setting}" 0)
done

(cd "${bench_dir}" && bench --site "${site}" enable-scheduler)
(cd "${bench_dir}" && bench --site "${site}" clear-cache)

echo "Running application-level production checks..."
(cd "${bench_dir}" && bench --site "${site}" execute crm.security.assert_production_configuration)

if [[ "${CRM_RESTART_AFTER_HARDENING:-0}" == "1" ]]; then
	(cd "${bench_dir}" && bench restart)
else
	echo "Configuration is ready. Restart the production processes to load it."
	echo "Set CRM_RESTART_AFTER_HARDENING=1 to let this script run 'bench restart'."
fi
