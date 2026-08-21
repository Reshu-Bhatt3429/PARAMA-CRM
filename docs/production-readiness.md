# Production readiness

The repository now separates local development from production hardening. Do
not expose `bench start`, Werkzeug, or Vite directly to the internet. Serve the
site with the Frappe production processes behind an HTTPS reverse proxy and
restrict MariaDB and Redis to the private application network.

Read the companion [security report](../security_best_practices_report.md) for
the completed remediations, accepted risks, and release gate.

## Before the first production deployment

1. Rotate any credential that has been pasted into chat, source code, shell
   history, or an issue tracker. In particular, replace the previously shared
   Gemini API key before using AI itinerary generation.
2. In each active **WhatsApp Account**, enter the **Meta App Secret**. This is
   encrypted at rest and is required to authenticate Meta webhook POSTs.
3. Ensure Exotel has a long random **Webhook Verify Token**. Migration moves an
   existing value into Frappe's encrypted password store.
4. Use a public `https://` host name. TLS certificates and renewal belong at
   the reverse proxy/load balancer.
5. Put database, Redis, site files, and the site encryption key on persistent,
   backed-up storage. Test a restore before accepting customer data.
6. Treat the current Frappe 17 framework as a pinned development-line release.
   Promote only the tested commit; do not update from the moving `develop`
   branch during a production deploy.

## Harden and validate a site

From the CRM repository:

```bash
cd /path/to/frappe-bench
bench --site your-crm.example.com set-config host_name https://your-crm.example.com

cd /path/to/crm
scripts/harden-production.sh your-crm.example.com
```

Set `CRM_BENCH_DIR` if the Bench directory is not the sibling `crm-bench`.
The script installs the frozen frontend dependencies, runs backend/frontend
lint, unit tests, and the production dependency audit, creates a database/file
backup, builds production assets, migrates the schema, enables CSRF, disables
developer/test/server-script/live-reload modes, enables the scheduler, and runs
application-level readiness checks. It does not restart processes unless
`CRM_RESTART_AFTER_HARDENING=1` is set.

The readiness check deliberately stops the release if `host_name` is absent or
does not use HTTPS, if live-demo credentials remain, or if an enabled WhatsApp
or Exotel integration is missing its encrypted verification secret.

## Current validation snapshot (2026-08-20)

The checked-out release has passed:

- frontend ESLint, all 597 Vitest tests, the production Vite/PWA build, and a
  production dependency audit of 720 packages with zero advisories;
- Ruff and the official Frappe Semgrep rules, with zero Semgrep findings and
  zero scan errors in application source;
- 621 focused backend tests covering itinerary/PDF security, quotes, invoices,
  workflows, email sequences, form responses, invitations, realtime isolation,
  HTTP method guards, AI, and Frappe 17 controller composition;
- schema migration, cache clear, and restored business-data counts of 71 leads,
  64 deals, and 2 itineraries;
- an authenticated live smoke test: WhatsApp shows the connected `BOTTOMSUP`
  inbox, Itineraries shows both restored records and opens the **Create an
  itinerary** dialog, and the dashboard renders nine chart canvases.

Do not repeat those backend modules on this persistent site: Frappe test
fixtures commit business-looking records even in targeted runs. The polluted
test state was preserved in the `20260820_233158` recovery backup, then the
known-good `20260820_213106` database/files backup was restored and migrated.
Future backend suites must use a disposable site.

The local site is intentionally not production-configured. Its current release
check still reports three blockers: developer mode is enabled, public HTTPS
`host_name` is absent, and the active WhatsApp account `BOTTOMSUP` has no Meta
App Secret. The Gemini key previously pasted into chat must also be rotated in
Google's console before AI is enabled; repository scans found no committed copy.

## Required infrastructure controls

- Run web, websocket, workers, scheduler, Redis, and MariaDB as supervised
  services. `bench start` and the Docker files in `docker/` are development
  environments, not production deployment definitions.
- Terminate TLS at the reverse proxy and forward the original scheme/host.
  HTTPS responses receive HSTS; `/crm` and `/api` receive nosniff and strict
  referrer headers, while `/crm` also prevents cross-origin framing.
- Limit request bodies at the proxy consistently with Frappe's configured file
  upload limit. Apply network-level throttling to login and public webhook
  routes in addition to Frappe's rate limiter.
- Back up the database, public/private files, and `site_config.json` encryption
  key together. Encrypt backups and keep at least one off-host copy.
- Send worker/web/proxy logs to a monitored store, alert on sustained 5xx rates
  and failed jobs, and redact query strings because Exotel can only return its
  callback token in the URL.
- Run all fixture-generating backend suites, including targeted modules, on a
  fresh disposable test site. They must never run against the customer-data
  site.
- Keep the dashboard's chart imports modular and asynchronous. A release build
  should retain the approximately 155 KB dashboard shell and must not regress
  to importing the full ECharts package in the initial route.

## Restore data from an earlier Docker instance

Back up the source site with files, copy the database dump and both file
archives to the target Bench, and keep the source `site_config.json` encryption
key available. Then restore into the already-created target site:

```bash
cd /path/to/frappe-bench
bench --site your-crm.example.com backup --with-files
bench --site your-crm.example.com restore /path/to/database.sql.gz \
  --with-public-files /path/to/public-files.tar \
  --with-private-files /path/to/private-files.tar
bench --site your-crm.example.com migrate
bench --site your-crm.example.com clear-cache
```

Use a privileged MariaDB account only for the restore operation and do not put
its password in shell history. Restore the matching encryption key before
testing encrypted integration credentials. Verify record counts, attachments,
WhatsApp accounts, itineraries, and invoices before opening the target site to
users.

## Release verification

After every deploy:

```bash
cd /path/to/frappe-bench
bench --site your-crm.example.com execute crm.security.assert_production_configuration
curl -I https://your-crm.example.com/crm
```

Confirm the response contains `X-Content-Type-Options`, `Referrer-Policy`,
`X-Frame-Options`, `Content-Security-Policy`, and `Strict-Transport-Security`.
Then smoke-test login, lead/deal access controls, itinerary PDF generation,
WhatsApp inbound/outbound messages, background jobs, email delivery, and a
backup restore in the same release environment.
