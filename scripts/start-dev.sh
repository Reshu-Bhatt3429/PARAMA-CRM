#!/usr/bin/env bash

set -Eeuo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
default_bench_dir="$(cd "${repo_dir}/.." && pwd)/crm-bench"
bench_dir="${CRM_BENCH_DIR:-${default_bench_dir}}"
frontend_host="${CRM_FRONTEND_HOST:-127.0.0.1}"

if [[ ! -f "${bench_dir}/Procfile" ]]; then
	echo "Frappe Bench was not found at ${bench_dir}." >&2
	echo "Set CRM_BENCH_DIR to the Bench directory and try again." >&2
	exit 1
fi

if [[ ! -d "${repo_dir}/frontend/node_modules" ]]; then
	echo "Frontend dependencies are missing. Run: cd ${repo_dir}/frontend && yarn install" >&2
	exit 1
fi

bench_pid=""
frontend_pid=""

cleanup() {
	local exit_code=$?
	trap - EXIT INT TERM

	for pid in "${frontend_pid}" "${bench_pid}"; do
		if [[ -n "${pid}" ]] && kill -0 "${pid}" 2>/dev/null; then
			kill "${pid}" 2>/dev/null || true
		fi
	done

	wait 2>/dev/null || true
	exit "${exit_code}"
}

trap cleanup EXIT INT TERM

echo "Starting Frappe services from ${bench_dir}"
(cd "${bench_dir}" && bench start) &
bench_pid=$!

echo "Starting CRM frontend at http://crm.localhost:8080/crm (bind: ${frontend_host})"
(cd "${repo_dir}/frontend" && yarn dev --host "${frontend_host}") &
frontend_pid=$!

wait -n "${bench_pid}" "${frontend_pid}"
