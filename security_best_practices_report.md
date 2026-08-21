# Security best-practices report

Reviewed on 2026-08-20 against the current PARAMA CRM repository and the live
Frappe 17 development site. This report distinguishes remediated application
issues from operator actions and upstream framework risks.

## Executive summary

No known critical, high, moderate, or low frontend dependency advisories remain
in the production dependency audit. The implemented controls cover webhook
authentication, encrypted integration credentials, POST-only mutation
boundaries, outbound-host validation, URL validation, chart tooltip injection,
response headers, source-map removal, safe local binding, and reproducible
Docker dependencies. The final Frappe-specific Semgrep scan reports zero
findings and zero scan errors after excluding generated assets and test code.

The site must not be called production-ready until the active WhatsApp account
has its Meta App Secret and the deployment has run the production hardening
script. Frappe 17 is also still a development-line dependency, so it requires a
pinned, tested commit and a controlled upgrade process.

## Findings

### 1. Meta WhatsApp webhook accepted unauthenticated POSTs — High — Fixed in code

- Affected area: `crm/integrations/whatsapp_security.py:10-86`,
  `crm/hooks.py:31-32`
- Risk: an internet client could forge inbound WhatsApp events if the public
  webhook endpoint trusted requests without Meta's HMAC signature.
- Remediation: POST bodies are now checked with `X-Hub-Signature-256` using
  constant-time comparison before delegating to the companion application.
  Production fails closed; only developer mode permits a temporarily missing
  secret.
- Verification: dedicated valid, invalid, missing-secret, and delegation tests
  pass.
- Operator action: enter the Meta App Secret in the active WhatsApp Account
  `BOTTOMSUP`. The readiness checker intentionally fails until this is done.

### 2. Integration credentials stored as ordinary fields — High — Fixed

- Affected area: `crm/patches/v1_0/harden_integration_credentials.py:12-77`,
  `crm/fcrm/doctype/crm_exotel_settings/crm_exotel_settings.json:34-40`
- Risk: secrets in ordinary database fields are easier to disclose through
  exports, permissions mistakes, backups, or metadata APIs.
- Remediation: the Exotel verification token and WhatsApp Meta App Secret use
  Frappe Password fields encrypted with the site's encryption key. The patch
  migrates the legacy Exotel value and clears the unused Facebook access-token
  field.
- Operator action: protect and back up `site_config.json` with the database;
  losing its encryption key makes encrypted credentials unrecoverable.

### 3. Unsafe dynamic URLs and reverse-tab control — High — Fixed

- Affected area: `frontend/src/utils/safeUrl.js:1-52` and its attachment,
  activity, WhatsApp, itinerary, invoice, ERP, SLA, quote, and user-menu callers
- Risk: database-controlled values could open `javascript:` or other unsafe
  schemes, or allow a newly opened page to control the originating window.
- Remediation: all relevant callers now use the shared `safeUrl` utility, allow
  only HTTP(S), resolve permitted relative URLs, and open with
  `noopener,noreferrer`. Unit tests cover unsafe schemes and opener isolation.

### 4. Database labels interpolated into ECharts HTML tooltips — High — Fixed

- Affected area: `frontend/src/utils/chartTooltips.js:1-66`,
  `frontend/src/components/Dashboard/SafeAxisChart.vue:1-165`, and
  `frontend/src/components/Dashboard/SafeDonutChart.vue:1-130`
- Risk: labels from CRM records could reach HTML tooltip rendering and become a
  stored cross-site-scripting vector.
- Remediation: safe chart adapters force ECharts `richText` rendering and
  escape all database-derived values. Unit tests cover malicious markup.

### 5. Production source maps and oversized CSS exposed internals — Medium — Fixed

- Affected area: `frontend/vite.config.js:123-137`,
  `frontend/tailwind.config.js:20-60`
- Risk: public source maps reveal implementation details and unnecessarily
  enlarge releases; the previous broad Tailwind safelist generated megabytes
  of unused CSS.
- Remediation: source maps are development-only and the safelist is explicit.
  The current production output contains zero `.map` files. CSS is about
  0.70 MB before compression, down from about 5.64 MB.

### 6. Missing browser security headers — Medium — Fixed

- Affected area: `crm/security.py:9-48`, `crm/hooks.py:31-32`
- Risk: MIME sniffing, permissive referrer behavior, cross-origin framing, and
  missing HTTPS transport policy weakened browser-side protection.
- Remediation: CRM/API responses set `X-Content-Type-Options`, a strict referrer
  policy, a restrictive Permissions Policy, and a cross-domain-policy denial.
  API responses are marked non-indexable. CRM pages add `SAMEORIGIN` framing
  and a baseline CSP. HTTPS responses add one-year HSTS. Live response checks
  and unit tests pass.
- Residual: the CSP cannot yet disallow dynamic evaluation because trusted Form
  Scripts are evaluated in the browser. Restrict Form Script creation and
  editing to trusted System Managers and audit those records as executable
  code.

### 7. Development configuration can bypass production controls — High — Mitigated

- Affected area: `crm/security.py:51-76`,
  `scripts/harden-production.sh:1-54`
- Risk: `ignore_csrf`, developer mode, test execution, server scripts, or live
  reload must not be enabled on an internet-facing site.
- Remediation: the hardening script disables all five flags after a verified
  backup and migration. `crm.security.assert_production_configuration` fails a
  deployment when unsafe flags, a missing encryption key, or integration-secret
  gaps remain.
- Current live site: remains a local development environment with developer
  mode enabled and no public HTTPS `host_name`. Test execution and CSRF bypass
  are disabled. This is intentional local state, not a production state.

### 8. Docker defaults exposed services and embedded weak passwords — High — Fixed for development

- Affected area: both Compose files and init scripts
- Risk: public `8000`/`9000` bindings, `admin`/`123` credentials, mutable
  `latest` images, and startup races made the development stack unsafe and
  unreliable.
- Remediation: ports bind to loopback, passwords are required Compose secrets,
  images use tested digests, MariaDB/Redis have persistence and health checks,
  dependencies wait for health, and Frappe/WhatsApp Git commits are pinned.
- Residual: these Compose files still run `bench start`; they are explicitly
  development-only and must not replace supervised production processes behind
  an HTTPS reverse proxy.

### 9. Frappe-pinned Python dependencies have upstream advisories — Medium — Open upstream risk

- Affected versions in the current Bench: `cryptography 48.0.1`, `pypdf
  6.14.2`, `sqlparse 0.5.5`, and `pdfkit 1.0.0`.
- Risk: the Python audit reports advisories that cannot safely be removed in
  this application alone because the framework currently pins the affected
  versions. `pdfkit` has no patched release in the advisory data.
- Action: track the pinned Frappe 17 commit, upgrade only through a staging test,
  and never render attacker-controlled HTML or attacker-controlled pdfkit
  options. Re-run the audit on every framework update.

### 10. Frappe 17 is a development-line framework — Medium — Accepted with controls

- Risk: the current framework reports `17.0.0-dev`; development-line changes can
  introduce breaking behavior or security regressions without stable-release
  guarantees.
- Control: Docker pins the exact tested Frappe commit rather than following the
  moving branch. Production promotion must use immutable application and
  framework revisions, a fresh test site, migration rehearsal, and rollback
  backup.
- Recommendation: move to the first supported stable Frappe 17 release after
  staging validation, or use the latest supported stable framework if the
  business does not require version 17-only behavior.

### 11. Shared AI provider credential — High — Operator action required

- Risk: an API key pasted into chat must be treated as compromised even when it
  is not present in Git.
- Verification: repository scans found no copy of the previously shared Gemini
  key.
- Action: revoke/rotate that key in the provider console, store the replacement
  only in encrypted configuration or a production secret manager, and apply
  quota/billing alerts. Never reuse the exposed value.

### 12. Tests on a reused business-data site contaminate fixtures — Medium — Process fix required

- Risk: integration/category modules create fixture records and assume a clean
  site. Even a targeted module set increased this site's lead/deal counts from
  71/64 to 806/736 before the integrity check caught it.
- Remediation performed: a safety snapshot of the polluted state was created at
  `20260820_233158`; the known-good `20260820_213106` database and public/private
  files were restored, migrated, and verified at 71 leads, 64 deals, and 2
  itineraries.
- Action: run every fixture-generating backend test—full or targeted—only on a
  disposable, isolated test site. Production release validation should use
  smoke tests and read-only checks on the real site.

### 13. State-changing APIs accepted safe HTTP methods — High — Fixed

- Affected area: representative guards at `crm/api/doc.py:207`,
  `crm/api/form.py:294`, `crm/api/whatsapp.py:1037`, and
  `crm/integrations/twilio/api.py:110`, plus the regression inventory in
  `crm/tests/test_http_method_guards.py:1-104`
- Risk: Frappe permits whitelisted functions over GET unless a method
  restriction is declared. Mutations reachable by GET weaken CSRF assumptions
  and can be triggered by prefetchers, crawlers, embedded links, or cross-site
  navigation.
- Remediation: all audited CRM state-changing endpoints now declare
  `methods=["POST"]`; their frontend callers use resource submission so Frappe
  sends the CSRF token. A CRM wrapper also makes the framework user-setting save
  endpoint POST-only through the override in `crm/hooks.py:359-362`.
- Residual: Exotel's provider callback retains GET compatibility and validates
  a constant-time shared secret because the provider supplies the token in its
  callback URL. Invitation links are now read-only confirmation pages; only a
  CSRF-protected POST consumes an invitation.

### 14. Exotel host setting allowed arbitrary outbound destinations — High — Fixed

- Affected area:
  `crm/fcrm/doctype/crm_exotel_settings/crm_exotel_settings.py:14-53`,
  `crm/integrations/exotel/handler.py:99-164`
- Risk: an administrator-controlled or legacy database value could direct
  authenticated server requests to an attacker host or internal service. Calls
  without explicit timeouts could also exhaust workers.
- Remediation: the host is normalized and restricted to `exotel.com` or its
  subdomains on validation and again at request time. All direct Exotel HTTP
  calls use a 15-second timeout and raise on failed status responses. Dedicated
  tests cover loopback, metadata hosts, user-info ambiguity, and suffix tricks.

### 15. OAuth provider icons reached HTML without scheme validation — Medium — Fixed

- Affected area: `crm/api/auth.py:7-50`
- Risk: a malicious or compromised Social Login Key could place executable or
  ambiguous URLs in rendered login-provider markup.
- Remediation: icon URLs now allow only same-site paths or explicit HTTP(S), and
  all emitted HTML attributes are escaped. Unit tests cover `javascript:`,
  `data:`, protocol-relative, same-site, and HTTPS values.

### 16. Dashboard chart package dominated route startup — Performance — Fixed

- Affected area: `frontend/src/components/Dashboard/EChart.vue:1-80`,
  `frontend/src/components/Dashboard/DashboardItem.vue:49-57`, and
  `frontend/src/pages/Dashboard.vue:160-164`
- Impact: the previous dashboard route loaded the complete chart package in a
  roughly 1.30 MB minified route chunk (about 435 KB compressed).
- Remediation: ECharts now registers only the bar, line, pie, grid, legend,
  title, tooltip, label-layout, and canvas modules. Chart renderers and the add
  dialog load asynchronously. The dashboard shell is now about 155 KB (50 KB
  compressed); the 569 KB chart engine chunk loads only when charts render.
- Verification: production build, unit tests, lint, detector checks, and an
  authenticated live-browser canvas/visual smoke test pass.

### 17. Public form redirects and page policy were too permissive — High — Fixed

- Affected area: `crm/api/form.py:54-67`, `crm/www/crm_form.py:39-40`, and
  `crm/www/crm_form.py:130-140`
- Risk: a manager-authored or imported success URL could redirect form visitors
  to an attacker-controlled HTTP destination or an executable/ambiguous URL.
  Inline public-page scripts also had no per-response CSP nonce.
- Remediation: form redirects now allow only unambiguous local paths or absolute
  HTTPS URLs. Public form, unsubscribe, and invitation pages generate CSP
  nonces, fail closed on framing, and do not load runtime Google Fonts.
- Verification: redirect, framing, nonce, and response-header regression tests
  pass; the invitation confirmation page was also checked over live HTTP.

### 18. Email scanners could consume invitation links — High — Fixed

- Affected area: `crm/api/__init__.py:75-101`,
  `crm/fcrm/doctype/crm_invitation/crm_invitation.py:27-40`, and
  `crm/www/accept_invitation.html:60`
- Risk: the old emailed URL invoked a state-changing guest endpoint through
  GET. Link-preview and anti-malware scanners could create/log in a user merely
  by fetching the URL; the old bearer token was also shorter than desired.
- Remediation: emailed links now land on a read-only confirmation page. A
  CSRF-protected, rate-limited POST is the only consumer, pending status and a
  three-day age are rechecked atomically, and new invitation keys contain 128
  bits of entropy.
- Verification: all 15 invitation tests plus the global HTTP-method inventory
  pass.

### 19. Realtime events exposed customer data across users — High — Fixed

- Affected area: `crm/api/event.py:380-396`,
  `crm/integrations/exotel/handler.py:76-81`, and
  `crm/fcrm/doctype/erpnext_crm_settings/erpnext_crm_settings.py:632`
- Risk: broadcasting event details, phone numbers, caller metadata, or
  customer-created messages without a user target could disclose data to every
  connected CRM session.
- Remediation: event details are sent only to enabled owners/participants,
  Exotel caller data only to the assigned telephony agent, and ERP customer
  feedback only to the initiating session.
- Verification: dedicated event and Exotel isolation tests pass, and the
  Frappe-specific Semgrep scan contains no remaining unrestricted realtime
  publication finding.

### 20. Itinerary PDF images permitted server-side request forgery — High — Fixed

- Affected area:
  `crm/fcrm/doctype/crm_itinerary/crm_itinerary.py:286-343`
- Risk: an arbitrary cover/logo URL was handed to the server-side PDF renderer,
  allowing requests to internal services or cloud metadata. Arbitrary SVG can
  also carry scripts, entities, or nested remote-resource references.
- Remediation: PDF image sources are now limited to this site's uploaded file
  paths or the app's generated SVG data. Generated SVG is base64-decoded and
  parsed, with a 64-element cap and strict tag/attribute/resource allowlists.
  The UI no longer offers arbitrary URL paste controls.
- Verification: four new image-source tests reject remote URLs, scripts, unsafe
  SVG resources, and accept only local uploads/passive generated artwork; all
  121 itinerary tests pass.

### 21. Shared email signatures crossed a stored-HTML trust boundary — High — Fixed

- Affected area: `crm/api/__init__.py:43-48` and
  `frontend/src/components/CommunicationArea.vue:168-175`
- Risk: a shared or imported Email Account signature is inserted into every
  agent's rich-text editor. Unsanitized legacy markup could execute as
  cross-user stored XSS.
- Remediation: signatures are sanitized with Frappe on the server and with
  DOMPurify immediately before editor insertion. Incoming email HTML and other
  rich-content sinks use the same frontend sanitizer.

### 22. Automation templates inherited excessive Jinja globals — High — Fixed

- Affected area: `crm/sequences/email.py:200-251` and
  `crm/workflows.py:819-822`
- Risk: manager-authored automation and email templates evaluated with the full
  template environment could reach globals beyond their explicitly supplied
  record context.
- Remediation: every audited automation render now sets
  `restrict_globals=True`; template failures are parked/logged instead of
  falling through to unsafe alternate evaluation.

### 23. DocType overrides could replace Frappe 17/ERP controllers — Medium — Fixed

- Affected area: `crm/hooks.py:168-175`, `crm/overrides/contact.py:1-48`, and
  `crm/overrides/email_template.py:1-49`
- Risk: `override_doctype_class` makes installed-app ordering significant and
  can discard framework or ERP controller behavior, producing hard-to-detect
  authorization and lifecycle regressions after an upgrade.
- Remediation: Frappe 17's compositional `extend_doctype_class` hook now adds
  small `BaseDocument` mixins without replacing the installed controller.
- Verification: MRO and method-composition regression tests pass on the live
  Frappe 17 environment.

### 24. Public document bearer routes lacked application throttling — Medium — Fixed

- Affected area: `crm/api/quote.py:467` and `crm/api/invoices.py:840`
- Risk: even unguessable share tokens can be subjected to automated probing or
  expensive repeated PDF reads, consuming web-worker and I/O capacity.
- Remediation: public quote and invoice token views are limited to 60 requests
  per minute per Frappe rate-limit key, in addition to the recommended reverse
  proxy limits.

## Release gate

A production release passes only when all of these are true:

1. The exposed Gemini credential has been rotated.
2. Every active WhatsApp Account contains its encrypted Meta App Secret.
3. `scripts/harden-production.sh <site>` completes successfully.
4. The site is behind HTTPS with supervised web, websocket, worker, scheduler,
   Redis, and database processes.
5. A database/files/encryption-key backup has passed a restore rehearsal.
6. Frontend audit, tests, lint, build, backend lint, migration, security-header
   checks, a zero-finding Frappe Semgrep scan, and authenticated smoke tests pass
   on the immutable release. Fixture-generating backend tests run only on a
   disposable site.
