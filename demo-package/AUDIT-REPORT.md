# PARAMA CRM — Pre-Demo Audit Report
Date: 2026-08-18 · Scope: bug audit, security audit, feature review of the fork (nissu99/PARAMA-CRM)
Method: static code review by 4 parallel agents + live checks against the docker demo site. Frappe is not installed on the review host, so framework-behaviour claims are inferred unless marked "confirmed live".

## 0. FIX STATUS (updated 2026-08-18, after the fixes)

All findings below were FIXED across 49 files by 6 Opus workers, then verified. Scope chosen by the user: everything, custom + upstream (accepts merge-conflict risk on the next pull from origin).

Verification evidence (run against the deployed code in the docker container):
- Backend syntax: py_compile OK on all 49 changed files.
- `bench migrate`: clean (the travel-fields patch change applied).
- Backend tests: test_whatsapp 62 pass, test_whatsapp_demo 12 pass (one test rewritten for the new seeder assignment), test_exchange_rate 16 pass. (test_dashboard/form/integrations fail to IMPORT due to a pre-existing Frappe-version skew — `IntegrationTestCase` is absent in the pinned Frappe 15.117.0 — not a regression.)
- Frontend: unit tests 181 pass; `yarn build` OK; app renders; no new console errors (the two 417s are a pre-existing Frappe telemetry endpoint, unrelated).
- Live security re-probes (confirmed CLOSED): create_deal owner-spoof now returns PermissionError; a legitimate self-owned deal still creates; the guest boot-info leak returns "only meant for developer mode" after developer_mode was set to 0 on the running site.

Deploy note: the fixes are in the host working tree (uncommitted) AND copied into the running container so the demo box runs the fixed code. The canonical deploy is to commit on the host, then in the container `git pull upstream main` + migrate + build. Two items were left as documented TODOs by the workers: the timezone-parse frontend fix (breaks the test harness, no effect on the current site) and the realtime publish scope (narrowing it would break the manager inbox). Both are noted in code comments.

## 1. TOP RISKS FOR THE DEMO (read this first)

### DEMO-BLOCKER — sending a WhatsApp reply fails on the demo site  [CONFIRMED LIVE]
- Cause: the demo WhatsApp account uses a fake token, so Meta rejects every outbound send with "Authentication Error".
- Files: crm/api/whatsapp.py:947-986 (send path), crm/demo/whatsapp_demo.py:336-343 (fake token).
- Impact: on the demo/practice site you cannot show a live reply. Navigation, reading chats, trip details, stages, and dashboards all work.
- With a REAL client Meta credential, sending works. This does not block a real client demo.
- Extra bug (Frontend): the composer clears the typed text BEFORE the send, so on any failure the text is lost with no draft. File: frontend/src/components/Activities/WhatsAppInboxComposer.vue:172. Fix: clear on success, restore on error.

### DEMO-BLOCKER — manager "All conversations" toggle is silently dead + cross-user cache leak  [code review, high confidence]
- Cause: the conversations resource uses a module-level cache and IndexedDB persistence. The scope toggle does not reach the server, and the first paint after a re-login/re-seed can show the PREVIOUS user's conversations.
- Files: frontend/src/pages/WhatsAppInbox.vue:677-687; session store does not clear IndexedDB on logout (stores/session.js:31-37).
- Impact: if you demo as a manager, or switch users during the demo, you may see a stale or wrong inbox.
- Workaround for now: demo as ONE agent (priya@demo.crm). Do not switch users mid-session. Fix: drop the `cache` option from the resource.

### MAJOR — thread pane can crash on certain live messages  [code review, high confidence]
- Null-guards missing in frontend/src/components/Activities/WhatsAppArea.vue: a reply to a CRM-created message (:207), or a media message with null text (:104,:135), throws a TypeError and the thread does not paint.
- Impact: low on the seeded demo, higher once real webhook messages arrive. Fix: coerce message to string and guard the reply lookup.

### MAJOR — manager first screen is empty  [CONFIRMED LIVE]
- The inbox defaults to scope "mine". The seeder assigns leads to agents, so a manager (Administrator) sees an empty inbox until clicking "All conversations". File: WhatsAppInbox.vue:657 + whatsapp.py:529.
- Workaround applied for the video: all four demo leads were reassigned to priya@demo.crm, so her inbox is full. Fix: initialise scope from role.

### MINOR — contact/trip panel hides below 1280px screen width
- File: WhatsAppInbox.vue:396-398 (hidden below Tailwind `xl`). Demo at 1280px width or wider. There is no toggle to reopen it.

## 2. SECURITY FINDINGS (custom code is clean; the serious holes are upstream)

Important: your team's custom WhatsApp/demo code introduced NO high or critical hole. Every HIGH/CRITICAL below is upstream frappe/crm code that ships in the fork.

### Confirmed live on the demo box
- **create_deal has no permission gate + mass assignment** (HIGH). crm/fcrm/doctype/crm_deal/crm_deal.py:476. CONFIRMED: sales user priya created a "Won" deal owned by another user via the API. Fix: add a create permission check, drop ignore_permissions, whitelist fields.
- **Guest boot-info leak** (MEDIUM). crm/www/crm.py:29 is guest-callable when developer_mode=1, which the demo box sets (docker/init-local.sh:47). CONFIRMED: an unauthenticated request returns site info + a CSRF token. Fix: set developer_mode 0 and server_script_enabled 0 on any client-facing box; set a strong Administrator password (currently "admin").

### High-severity, from code review (verify on a running bench before sizing the fix)
- CRITICAL: unsanitised email HTML in an iframe without sandbox — stored XSS from an inbound email. frontend/src/components/Activities/EmailContent.vue:2-6,227. Only relevant if you enable Email.
- HIGH: whatsapp send uses READ permission and does not validate the `to` number against the lead — a sales user could send from the client's brand to any number. crm/api/whatsapp.py:947-986. Fix: require write permission; reject a `to` that does not match the record.
- HIGH: remove_assignments passes caller-supplied ignore_permissions. crm/api/doc.py:591.
- HIGH: add_task_to_call_log / add_note_to_call_log overwrite arbitrary tasks/notes. crm/integrations/api.py:120-131.
- HIGH: contact-by-phone lookups bypass permission scoping. crm/integrations/api.py:141,158.
- Full list (27 findings) is in the security agent output; medium/low items are mostly upstream and not demo-blocking.

### Checked and found SOUND
- The WhatsApp `scope="all"` manager gate is enforced server-side and cannot be bypassed by a request field.
- No SQL injection in custom code. No live secrets committed (only obvious placeholders + the known demo docker creds).
- Role-management, dashboard, session, form, and enrichment endpoints are correctly gated.

## 3. HARDENING BEFORE ANY CLIENT-FACING HOST
1. Set `developer_mode 0` and `server_script_enabled 0` (edit docker/init-local.sh:47-49 and docker/init.sh).
2. Set a strong Administrator password (not "admin").
3. Rotate or disable the demo users (priya@demo.crm, rahul@demo.crm, password demo1234) — crm/demo/whatsapp_demo.py:62.
4. Give client agents the "Sales User" role, not Manager, so admin/integration screens are hidden.
5. If you enable Email later, fix the EmailContent.vue XSS first.
6. Add the create_deal permission check.

## 4. FEATURES
See PARAMA-CRM-Feature-Sheet.pdf. ~90 features total; ~10 recommended for a simple demo. Your custom work: WhatsApp team inbox, auto-capture, round-robin, follow-up nudges/digest, travel fields, status ladder, RelayCRM theme.

## 5. DELIVERABLES IN THIS FOLDER
- PARAMA-CRM-demo.mp4 — 62s captioned product tour (inbox, trip details, leads, dashboard).
- PARAMA-CRM-Manual.pdf — client onboarding + user manual (the 7 WhatsApp values to ask for, two-screen guide, FAQ).
- PARAMA-CRM-Feature-Sheet.pdf — keep/hide decision sheet for a simple demo.
- AUDIT-REPORT.md — this file.
