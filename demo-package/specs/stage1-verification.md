# Stage 1A (Foundations) — verification record

Scope: F1, F2, F5, F7, F8, F9 from the master spec §4, plus the permission-matrix
skeleton and a recorded test baseline. No user-visible feature ships in this stage.

Branch: `feat/feature-expansion`. Nothing is committed by this stage; all changes
sit in the working tree.

---

## How the suites are run

### Backend

The bench container `crm-local-frappe-1` serves a SEPARATE clone at
`/home/frappe/frappe-bench/apps/crm`, not the bind mount `/workspace/app`. The
host working tree is pushed into that clone before every run with:

```bash
cd /home/kreshnith/CRM
tar -cf - --exclude=__pycache__ --exclude='*.pyc' --exclude='crm/public' crm | \
  docker exec -i crm-local-frappe-1 bash -lc 'cd ~/frappe-bench/apps/crm && tar -xf -'
```

Then, per test module:

```bash
docker exec crm-local-frappe-1 bash -lc \
  'cd ~/frappe-bench && bench --site crm.localhost run-tests --module <module>'
```

**`bench --site crm.localhost run-tests --app crm` CANNOT be used in this
container.** It aborts during collection, before running a single test:

```
  File "/home/frappe/frappe-bench/apps/crm/crm/domain_enrichment/doctype/crm_enrichment_rule/test_crm_enrichment_rule.py", line 5, in <module>
    from frappe.tests import IntegrationTestCase
ImportError: cannot import name 'IntegrationTestCase' from 'frappe.tests' (/home/frappe/frappe-bench/apps/frappe/frappe/tests/__init__.py)
```

Cause: the container has **frappe v15.117.0**, while `pyproject.toml` declares
`frappe = ">=16.0.0-dev,<=17.0.0-dev"` and 47 upstream test modules import
`IntegrationTestCase` / `UnitTestCase`, which exist only in frappe v16. The
project's own test modules (`crm/tests/*`) use the v15 `FrappeTestCase` and do
run. This is a PRE-EXISTING environment mismatch, not a Stage-1 regression, and
it is listed as an open issue below.

The suite is therefore run module by module, and the 47 v16-only modules are
reported as **not collectable in this container** rather than as passes.

### Frontend

```bash
cd /home/kreshnith/CRM/frontend && npx vitest run
cd /home/kreshnith/CRM/frontend && npx vite build --base=/assets/crm/frontend/
```

---

## Baseline

Taken 2026-08-18, on the working tree before any Stage-1 edit
(`git status --porcelain` showed only ` M .pi/PLAN.md` and `?? demo-package/`).

### Backend — modules that collect and run

| Module | Result |
| --- | --- |
| `crm.fcrm.doctype.crm_invitation.test_crm_invitation` | `Ran 13 tests in 4.621s` `OK` |
| `crm.fcrm.doctype.crm_product.test_product_item_sync` | `Ran 25 tests in 0.035s` `OK (skipped=18)` |
| `crm.fcrm.doctype.crm_products.test_crm_products` | `Ran 7 tests in 0.001s` `OK (skipped=6)` |
| `crm.integrations.erpnext.test_utils` | `Ran 9 tests in 0.023s` `OK (skipped=2)` |
| `crm.tests.test_exchange_rate` | `Ran 16 tests in 0.630s` `OK` |
| `crm.tests.test_followup_engine` | `Ran 99 tests in 22.050s` `FAILED (failures=2)` |
| `crm.tests.test_itinerary` | `Ran 112 tests in 50.147s` `OK` |
| `crm.tests.test_state_options` | `Ran 7 tests in 0.232s` `OK` |
| `crm.tests.test_whatsapp` | `Ran 62 tests in 0.199s` `OK` |
| `crm.tests.test_whatsapp_demo` | `Ran 12 tests in 0.049s` `OK` |

Total collected: 362 tests, 2 failures, 26 skips.

### Baseline failures, verbatim

Both are PRE-EXISTING and both depend on the demo site's own state, not on code.

```
FAIL: test_client_refuses_to_run_while_ai_is_disabled (crm.tests.test_followup_engine.TestAIClient.test_client_refuses_to_run_while_ai_is_disabled)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/home/frappe/frappe-bench/apps/crm/crm/tests/test_followup_engine.py", line 1479, in test_client_refuses_to_run_while_ai_is_disabled
    self.assertRaises(ai_client.AIConfigurationError, ai_client.complete, "hello")
    ~~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
AssertionError: AIConfigurationError not raised by complete
```

Reason: the demo site has `CRM AI Settings` enabled with a stored key, so the
guard the test asserts does not trigger. The test assumes a fresh CI site.

```
FAIL: test_quiet_hours_defer_instead_of_cancel (crm.tests.test_followup_engine.TestGuardrails.test_quiet_hours_defer_instead_of_cancel)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/home/frappe/frappe-bench/apps/crm/crm/tests/test_followup_engine.py", line 675, in test_quiet_hours_defer_instead_of_cancel
    self.assertEqual(
    ~~~~~~~~~~~~~~~~^
    	frappe.utils.get_datetime(row.next_due),
     ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
    	datetime.combine(self.now.date() + timedelta(days=1), datetime.min.time()) + timedelta(hours=9),
     ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
    )
    ^
AssertionError: datetime.datetime(2026, 8, 18, 23, 0, 57, 233991) != datetime.datetime(2026, 8, 19, 9, 0)
```

Reason: wall-clock dependent. An earlier run of the same module at 22:53 local
reported `Ran 99 tests in 22.047s / FAILED (failures=1)` — this test passed then
and failed seven minutes later. It is a flaky, time-of-day-sensitive test.

### Backend — modules that do NOT collect in this container

47 modules abort at import with
`ImportError: cannot import name 'IntegrationTestCase' from 'frappe.tests'` or
the same error for `UnitTestCase`. They are upstream Frappe CRM tests written
against frappe v16. Full list in the module sweep log; the affected paths are
everything under `crm/domain_enrichment/`, `crm/fcrm/doctype/*/test_*.py`
(except `crm_invitation`, `crm_product`, `crm_products`),
`crm/lead_syncing/doctype/*/test_*.py`, `crm/permissions/test_org_hierarchy.py`,
and `crm/tests/{test_dashboard,test_demo_data,test_form_api,test_integrations,test_notification_log,test_utils}.py`.

### Frontend

```
 RUN  v4.1.4 /home/kreshnith/CRM/frontend

 Test Files  10 passed (10)
      Tests  224 passed (224)
   Start at  22:53:27
   Duration  2.26s (transform 932ms, setup 294ms, import 1.53s, tests 376ms, environment 7.73s)
```

---

## Migrations

Migration manifest for Stage 1A, as required by spec F9. Every schema change is
made by a patch listed in `crm/patches.txt`, and every patch is idempotent.

### New doctypes

All four are created by `bench migrate` from their JSON, in module FCRM. None is
a Single, none is a child table, and none is exposed in the CRM frontend.

| Doctype | Purpose | Unique index | Other indexes |
| --- | --- | --- | --- |
| `CRM Suppression` (F1) | Consent ledger: one row per channel + normalised address | `suppression_key` | `address` |
| `CRM Outbound Job` (F2) | One scheduled unit of outbound work | `idempotency_key` | — |
| `CRM Outbound Recipient` (F2) | One address inside one job; the outbox guard | `idempotency_key` | `job`, `address`, `email_queue`, `message_id` |
| `CRM Reminder Log` (F5) | Reminder outbox ledger. Ledger only — no reminder feature ships in this stage | `dedup_key` | `task` |

`CRM Outbound Recipient` is a standalone doctype, NOT a child table. A child row
cannot carry a table-wide unique index, and that index is the whole at-most-once
mechanism.

### New fields on existing doctypes

| Doctype | Field | Type | How | Notes |
| --- | --- | --- | --- | --- |
| `FCRM Settings` | `feature_flags_section` | Section Break | doctype JSON | Collapsible "Feature Flags" section (F8) |
| `FCRM Settings` | `outbound_engine_enabled` | Check, default `0` | doctype JSON | The only flag registered so far. Registry: `crm/feature_flags.py` |
| `CRM Lead` | `custom_parama_email_normalized` | Data, hidden, read-only | Custom Field, patch | Derived (F7) |
| `CRM Lead` | `custom_parama_phone_e164` | Data, hidden, read-only | Custom Field, patch | Derived (F7) |
| `CRM Deal` | `custom_parama_email_normalized` | Data, hidden, read-only | Custom Field, patch | Derived (F7) |
| `CRM Deal` | `custom_parama_phone_e164` | Data, hidden, read-only | Custom Field, patch | Derived (F7) |
| `Contact` | `custom_parama_email_normalized` | Data, hidden, read-only | Custom Field, patch | Derived (F7). Contact is a framework doctype |
| `Contact` | `custom_parama_phone_e164` | Data, hidden, read-only | Custom Field, patch | Derived (F7) |

All six contact-key fields are Custom Fields with the `custom_parama_*`
namespace, on all three doctypes rather than only on the framework one. That
gives the three doctypes one shared field name and leaves `crm_lead.json` and
`crm_deal.json` untouched.

### New indexes

| Table | Index | Columns | Created by |
| --- | --- | --- | --- |
| `tabCRM Lead` | `custom_parama_email_normalized_index` | `custom_parama_email_normalized` | `create_parama_contact_key_fields` |
| `tabCRM Lead` | `custom_parama_phone_e164_index` | `custom_parama_phone_e164` | same |
| `tabCRM Deal` | `custom_parama_email_normalized_index` | `custom_parama_email_normalized` | same |
| `tabCRM Deal` | `custom_parama_phone_e164_index` | `custom_parama_phone_e164` | same |
| `tabContact` | `custom_parama_email_normalized_index` | `custom_parama_email_normalized` | same |
| `tabContact` | `custom_parama_phone_e164_index` | `custom_parama_phone_e164` | same |
| `tabCRM Task` | `due_date_status_index` | `due_date`, `status` | `add_task_reminder_index` (F5) |

Verified on the demo site after migrate:

```
CRM Lead ['custom_parama_email_normalized_index', 'custom_parama_phone_e164_index']
CRM Deal ['custom_parama_email_normalized_index', 'custom_parama_phone_e164_index']
Contact ['custom_parama_email_normalized_index', 'custom_parama_phone_e164_index']
CRM Task ['PRIMARY', 'due_date_status_index', 'modified']
```

### Patches, in `crm/patches.txt` order (`[post_model_sync]`)

1. `crm.patches.v1_0.create_parama_contact_key_fields` — creates the six Custom
   Fields and the six indexes. Idempotent: `create_custom_fields` skips existing
   fields, `frappe.db.add_index` checks `SHOW INDEX` first.
2. `crm.patches.v1_0.backfill_parama_contact_keys` — fills the columns.
   **Resumable**: runs through `crm.sweeps.run_sweep`, which pages on a
   `(modified, name)` cursor stored in the framework's DefaultValue table and
   commits each batch. **Silent**: `frappe.db.set_value(..., update_modified=False)`
   only — no document is loaded, so no hook, notification or send path can run.
   Skips a doctype whose columns do not exist yet.
3. `crm.patches.v1_0.add_task_reminder_index` — the composite CRM Task index.

Migration run output on the demo site:

```
Executing crm.patches.v1_0.create_parama_contact_key_fields #18-08-2026 in crm.localhost (_c8b3e5206096f0bd)
Success: Done in 1.11s
Executing crm.patches.v1_0.backfill_parama_contact_keys #18-08-2026 in crm.localhost (_c8b3e5206096f0bd)
Success: Done in 0.678s
Executing crm.patches.v1_0.add_task_reminder_index #18-08-2026 in crm.localhost (_c8b3e5206096f0bd)
Success: Done in 0.021s
```

A database backup was taken immediately before the migrate:
`./crm.localhost/private/backups/20260818_231303-crm_localhost-database.sql.gz`.

### Backfill result on the demo site

```
leads with email key: 6 of 8
leads with phone key: 4
contacts with phone key: 1 of 13
flag outbound_engine_enabled: 0
```

Records without a key are records whose source field is empty or does not parse
as a valid address or E.164 number. That is the designed outcome: an
un-normalisable value is never stored, so it can never be mistaken for a match.

### Downgrade behaviour

| Change | To undo |
| --- | --- |
| The four new doctypes | Delete the DocType records and drop `tabCRM Suppression`, `tabCRM Outbound Job`, `tabCRM Outbound Recipient`, `tabCRM Reminder Log`. Nothing outside `crm/suppression.py`, `crm/outbound.py` and the reminder ledger reads them |
| The six Custom Fields | Delete the six Custom Field rows. `crm.contact_keys.set_contact_keys` then sets an attribute the framework ignores; nothing breaks |
| The seven indexes | `DROP INDEX <name> ON <table>`. Costs query time only |
| The backfilled values | Clear the two columns. No feature reads them in this stage |
| The sweep cursors | `crm.sweeps.reset_watermark("contact_keys:<doctype>")` |
| `outbound_engine_enabled` | Already OFF. Delete the field to remove it entirely; `crm.feature_flags.is_enabled` then reads it as OFF |
| The hourly scheduler entry | Remove `crm.outbound.process_scheduled_jobs` from `crm/hooks.py`. It is a no-op while the flag is off |
| The three `validate` doc-event hooks | Remove them from `crm/hooks.py`. The columns then go stale but nothing errors |

No upstream core schema was edited. No existing field, index or doctype was
changed or removed.

---

## After

Run 2026-08-18, after the Stage-1A implementation, same commands as the baseline.

### Backend — modules that collect and run

| Module | Result |
| --- | --- |
| `crm.fcrm.doctype.crm_invitation.test_crm_invitation` | `Ran 13 tests in 5.552s` `OK` |
| `crm.fcrm.doctype.crm_product.test_product_item_sync` | `Ran 25 tests in 0.038s` `OK (skipped=18)` |
| `crm.fcrm.doctype.crm_products.test_crm_products` | `Ran 7 tests in 0.001s` `OK (skipped=6)` |
| `crm.integrations.erpnext.test_utils` | `Ran 9 tests in 0.010s` `OK (skipped=2)` |
| `crm.tests.test_exchange_rate` | `Ran 16 tests in 0.759s` `OK` |
| `crm.tests.test_followup_engine` | `Ran 99 tests in 36.587s` `FAILED (failures=1, errors=1)` |
| `crm.tests.test_itinerary` | `Ran 112 tests in 54.108s` `OK` |
| **`crm.tests.test_outbound`** (new) | `Ran 42 tests in 2.371s` `OK` |
| `crm.tests.test_state_options` | `Ran 7 tests in 0.207s` `OK` |
| **`crm.tests.test_suppression`** (new) | `Ran 32 tests in 2.133s` `OK` |
| **`crm.tests.test_sweeps`** (new) | `Ran 31 tests in 48.920s` `OK` |
| `crm.tests.test_whatsapp` | `Ran 62 tests in 0.204s` `OK` |
| `crm.tests.test_whatsapp_demo` | `Ran 12 tests in 0.058s` `OK` |

Total collected: 467 tests (362 baseline + 105 new), 2 failing tests, 26 skips.
The 47 modules that need frappe v16 still do not collect, exactly as in the
baseline.

### The two failures, verbatim — both PRE-EXISTING, both the same tests as the baseline

```
ERROR: test_client_refuses_to_run_while_ai_is_disabled (crm.tests.test_followup_engine.TestAIClient.test_client_refuses_to_run_while_ai_is_disabled)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/home/frappe/frappe-bench/apps/crm/crm/tests/test_followup_engine.py", line 1479, in test_client_refuses_to_run_while_ai_is_disabled
    self.assertRaises(ai_client.AIConfigurationError, ai_client.complete, "hello")
    ~~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/home/frappe/frappe-bench/apps/crm/crm/ai/client.py", line 123, in complete
    text = dispatch(settings, prompt, system, max_tokens)
  File "/home/frappe/frappe-bench/apps/crm/crm/ai/client.py", line 212, in dispatch
    return call_openai_compatible(settings, prompt, system, max_tokens)
  File "/home/frappe/frappe-bench/apps/crm/crm/ai/client.py", line 263, in call_openai_compatible
    data = post_json(
```

Same test as the baseline. It reported `FAIL` there and `ERROR` here for the
same underlying reason: the demo site has `CRM AI Settings` enabled with a key,
so `complete` does not raise the configuration error the test expects and
instead reaches the provider call. Nothing in Stage 1A touches `crm/ai/`.

**Flag for the owner:** this pre-existing test makes a real outbound call to the
configured AI provider whenever it runs on a site with AI enabled. That is a
Stage-1 finding, not a Stage-1 change, and it belongs on the F6 (AI client
hardening) work list.

```
FAIL: test_quiet_hours_defer_instead_of_cancel (crm.tests.test_followup_engine.TestGuardrails.test_quiet_hours_defer_instead_of_cancel)
----------------------------------------------------------------------
AssertionError: datetime.datetime(2026, 8, 18, 23, 24, 47, 996017) != datetime.datetime(2026, 8, 19, 9, 0)
```

Identical to the baseline failure. Wall-clock dependent: the same test passed at
22:53 and failed at 23:00 and 23:24 on the same code.

### Frontend

```
 RUN  v4.1.4 /home/kreshnith/CRM/frontend

 Test Files  10 passed (10)
      Tests  224 passed (224)
   Start at  23:23:05
   Duration  2.92s (transform 1.14s, setup 385ms, import 2.04s, tests 483ms, environment 9.37s)
```

Identical to the baseline: 10 files, 224 tests, 0 failures. Stage 1A changes no
frontend source file.

### Frontend production build

```bash
cd /home/kreshnith/CRM/frontend
NODE_OPTIONS="--max-old-space-size=6144" npx vite build --base=/assets/crm/frontend/
```

```
✓ built in 1m 29s

PWA v0.21.2
mode      generateSW
precache  1 entries (0.00 KiB)
files generated
  ../crm/public/frontend/sw.js.map
  ../crm/public/frontend/sw.js
  ../crm/public/frontend/workbox-8c29f6e4.js.map
  ../crm/public/frontend/workbox-8c29f6e4.js
warnings
  An error occurred when globbing for files. '(0 , brace_expansion_1.expand) is not a function'
```

The FIRST attempt, without `NODE_OPTIONS`, aborted with a V8 out-of-memory:

```
 1: 0x90c82b node::OOMErrorHandler(char const*, v8::OOMDetails const&) [node]
 ...
Aborted (core dumped)
```

That is a machine limit on this laptop, not a code fault: Stage 1A changes no
file under `frontend/`, and the build succeeds unchanged with a larger heap. The
build output is gitignored (`.gitignore:10 crm/public/frontend`), so the build
left no working-tree churn.

### Lint

```bash
uvx --from 'ruff==0.8.1' ruff check crm/     # All checks passed!
uvx --from 'ruff==0.8.1' ruff format crm/    # all files formatted
```

Ruff is pinned to v0.8.1 to match `.pre-commit-config.yaml`.

---

## Open issues handed to the next stage

1. **The container's frappe is v15.117.0; the app declares `>=16.0.0-dev`.** 47
   upstream test modules cannot be collected here, including
   `crm/permissions/test_org_hierarchy.py` — the module a permission-matrix test
   would extend. Until the local bench is on frappe v16, role and hierarchy
   permission tests cannot be run on this machine, only in CI.
2. **`test_client_refuses_to_run_while_ai_is_disabled` reaches the real AI
   provider** on a site with AI configured. See the flag above. F6 work.
3. **`test_quiet_hours_defer_instead_of_cancel` is time-of-day flaky.** It
   passes and fails on the same code depending on the wall clock.
4. **No channel adapter is registered for the outbound engine.** That is
   deliberate for Stage 1 — it is what makes the foundation provably send-free —
   but Stage 3 must register one before Send Later can work.
5. **`crm.outbound.refresh_delivery_states` has no scheduler entry yet.** It is
   written and tested but nothing calls it, because nothing is queued yet. Wire
   it when the first adapter ships.

---

# Stage 1B (Foundations, part 2) — verification record

Scope: F3 (sequence core extraction), F4 (automation execution context), F6 (AI
client hardening), the two flaky/unsafe tests Stage 1A handed on (open issues 2
and 3), and the missing scheduler entry (open issue 5). No user-visible feature
ships in this stage and no schema changes at all.

Branch: `feat/feature-expansion`. Nothing is committed by this stage; all changes
sit in the working tree, on top of Stage 1A (`f1cfc59e`).

Run 2026-08-18 23:00 – 2026-08-19 00:30 local, same commands as Stage 1A.

---

## What changed

### Files added

| File | Lines | What it is |
| --- | --- | --- |
| `crm/sequences/__init__.py` | 28 | The package. Re-exports the core's public names |
| `crm/sequences/core.py` | 490 | F3. The channel-agnostic engine: enrolment loop, sweep, per-row decision, stage selection, the claim/commit/send ordering, the transition maths, quiet hours, the outbox key |
| `crm/sequences/whatsapp.py` | 236 | F3. `WhatsAppFollowupAdapter` — Meta's semantics as the core's adapter interface. Every method is a one-line dispatch into `crm.api.followup_engine` |
| `crm/automation_context.py` | 195 | F4. Depth ceiling, real-transition detection, durable execution key, after-commit queueing, the daily-cap re-export |
| `crm/counters.py` | 113 | The atomic reservation both F4 and F6 use. `rows_affected`, `validated_field`, `reserve_daily_slot` |
| `crm/ai/schema.py` | 240 | F6. The JSON-schema validator, with no new dependency |
| `crm/tests/test_sequences.py` | 617 | 39 tests: the core, driven by a fake in-memory adapter |
| `crm/tests/test_automation_context.py` | 237 | 20 tests: the five automation guards and the atomic counter |
| `crm/tests/test_ai_client.py` | 478 | 35 tests: schema validation, request-size cap, budget reservation |

### Files changed

| File | Change |
| --- | --- |
| `crm/api/followup_engine.py` | 1557 → 1405 lines. The loops and the ordering now call `crm.sequences.core` through `get_channel_adapter()`. Every public name it exported still exists, with the same signature |
| `crm/ai/client.py` | 348 → 544 lines. Budget reserved atomically before the network call; the answer validated against the schema; a request-size ceiling; the per-call-site data-flow documented in the module docstring |
| `crm/outbound.py` | Added `sweep_delivery_states()`, the flag-guarded, never-raising scheduler entry for `refresh_delivery_states` |
| `crm/hooks.py` | One line added to `scheduler_events["hourly"]`: `crm.outbound.sweep_delivery_states` |
| `crm/tests/test_followup_engine.py` | Two tests fixed (below). Nothing else touched: the other 97 tests are byte-identical and pass unchanged |
| `crm/tests/test_outbound.py` | Four tests added for the new scheduler entry |
| `demo-package/specs/permission-matrix.md` | Stage 1B section: no endpoint added, the new non-endpoint entry points, and the F6 "what leaves the site" table |

### Schema

**None.** Stage 1B adds no doctype, no field, no index and no patch. `crm/patches.txt`
is untouched, and no `bench migrate` is needed to run this code.

### Extraction depth for F3

Full extraction of the MACHINE, not of the channel rules. The core owns the
enrolment loop and its per-row savepoint, the sweep and its per-row isolation,
`process_row` (locked re-read → reply check → quiet hours → stage), `send_stage`
(terminal → exhausted → content → destination → draft), `deliver` (claim →
commit → send → record) and `advance`. The WhatsApp adapter owns every rule that
mentions Meta, a template, a phone number or a Send Log row.

The webhook handlers (`handle_message_after_insert`, `handle_incoming`,
`handle_outgoing`, opt-out recording) stayed in the engine deliberately. They are
WhatsApp-webhook-shaped, and an email adapter's reply detection is Message-ID
matching through `crm.outbound.match_reply`, not the same code with a flag.

The adapter is constructed with the engine MODULE as an argument rather than
importing it. That removes the import cycle and keeps every seam patchable: each
call is an attribute lookup on the module at call time, so
`patch.object(engine, "create_template_message")` still intercepts the send and
the suite still cannot reach Meta.

---

## The two tests Stage 1A handed on

### Open issue 3 — `test_quiet_hours_defer_instead_of_cancel` was wall-clock flaky

Cause, found by reading the failure rather than the test: the test froze the
clock at 23:00 but built the follow-up row's `next_due` from the REAL clock
(`self.now - 1 minute`). After 23:00 real time, that `next_due` is in the future
relative to the frozen 23:00, so `process_one` returned at the "not due yet"
branch and never reached the quiet-hours branch the test is about. Before 23:00
it passed. Verbatim failure at 23:34, on unmodified code:

```
AssertionError: datetime.datetime(2026, 8, 18, 23, 34, 19, 117561) != datetime.datetime(2026, 8, 19, 9, 0)
```

Fix: the row's timestamps are now derived from the injected moment, not from the
wall clock, and the expected value is derived from the same moment. The clock is
injected exactly as before (`patch.object(frappe.utils, "now_datetime")`), and
every assertion is unchanged — same state, same exact `next_due`, same
"no message was sent".

Evidence it is fixed at the hour that used to fail — the whole module, run at
23:44 local:

```
Ran 99 tests in 20.124s

OK
```

### Open issue 2 — `test_client_refuses_to_run_while_ai_is_disabled` reached the real provider

On the demo site `CRM AI Settings` is enabled with a real key, so the guard the
test asserts did not fire and `complete()` went to the network. It cost a real
request every run and, on 2026-08-18, produced this verbatim error:

```
crm.ai.client.AIResponseError: The AI provider answered with HTTP 429: [{
  "error": {
    "code": 429,
    "message": "You exceeded your current quota, ...
```

Fix: the disabled state is FORCED inside the test
(`frappe.db.set_single_value(..., "enabled", 0)` plus a document-cache clear, both
rolled back by the base class), and `requests.post` is stubbed with an
`AssertionError`. The assertions are unchanged and now test what they say they
test: the real guard on the real code path. If the guard ever regresses the test
fails loudly instead of quietly reaching the network.

Both tests, run individually after the fix:

```
$ bench --site crm.localhost run-tests --module crm.tests.test_followup_engine \
    --test test_quiet_hours_defer_instead_of_cancel
Ran 1 test in 1.156s

OK

$ bench --site crm.localhost run-tests --module crm.tests.test_followup_engine \
    --test test_client_refuses_to_run_while_ai_is_disabled
Ran 1 test in 0.120s

OK
```

The 0.120s is itself the evidence for the second one: the same test took ~12
seconds before, because it was waiting on a provider.

---

## Backend — every module that collects, run individually

Same push-then-run commands as Stage 1A. Verbatim result lines:

| Module | Result |
| --- | --- |
| `crm.fcrm.doctype.crm_invitation.test_crm_invitation` | `Ran 13 tests in 3.979s` `OK` |
| `crm.fcrm.doctype.crm_product.test_product_item_sync` | `Ran 25 tests in 0.030s` `OK (skipped=18)` |
| `crm.fcrm.doctype.crm_products.test_crm_products` | `Ran 7 tests in 0.001s` `OK (skipped=6)` |
| `crm.integrations.erpnext.test_utils` | `Ran 9 tests in 0.009s` `OK (skipped=2)` |
| **`crm.tests.test_ai_client`** (new) | `Ran 35 tests in 0.261s` `OK` |
| **`crm.tests.test_automation_context`** (new) | `Ran 20 tests in 0.667s` `OK` |
| `crm.tests.test_exchange_rate` | `Ran 16 tests in 0.544s` `OK` |
| `crm.tests.test_followup_engine` | `Ran 99 tests in 19.281s` `OK` |
| `crm.tests.test_itinerary` | `Ran 112 tests in 42.012s` `OK` |
| `crm.tests.test_outbound` | `Ran 46 tests in 2.417s` `OK` |
| **`crm.tests.test_sequences`** (new) | `Ran 39 tests in 0.126s` `OK` |
| `crm.tests.test_state_options` | `Ran 7 tests in 0.189s` `OK` |
| `crm.tests.test_suppression` | `Ran 32 tests in 2.161s` `OK` |
| `crm.tests.test_sweeps` | `Ran 31 tests in 48.120s` `OK` |
| `crm.tests.test_whatsapp` | `Ran 62 tests in 0.185s` `OK` |
| `crm.tests.test_whatsapp_demo` | `Ran 12 tests in 0.047s` `OK` |

**565 tests, 0 failures, 0 errors, 26 skips**, against Stage 1A's 467 tests with
2 failures. The 98 new tests are 39 (`test_sequences`) + 35 (`test_ai_client`) +
20 (`test_automation_context`) + 4 added to `test_outbound`.

`crm.tests.test_followup_engine` deserves its own line: **all 99 pass**, and 97
of them are byte-identical to the file Stage 1A left. That is the acceptance
criterion for F3, and it was checked BEFORE the two test fixes as well — the
refactor was run against the unmodified test file first and produced exactly the
Stage 1A failure list, no more and no less:

```
Ran 99 tests in 35.911s

FAILED (failures=1, errors=1)
```

### Modules that still do NOT collect in this container

Unchanged from Stage 1A: the same 47 upstream modules abort at import with
`ImportError: cannot import name 'IntegrationTestCase' from 'frappe.tests'`,
because the container has frappe v15.117.0 while the app declares
`>=16.0.0-dev`. Stage 1B adds no test to that set and removes none. They are
reported as not collectable here, not as passes.

## Frontend

Untouched, and provably so:

```
$ git status --porcelain -- frontend/
$
```

No file under `frontend/` is modified or added. The suite was run anyway:

```
 RUN  v4.1.4 /home/kreshnith/CRM/frontend

 Test Files  10 passed (10)
      Tests  224 passed (224)
   Start at  00:01:08
   Duration  2.08s (transform 714ms, setup 316ms, import 1.26s, tests 315ms, environment 6.69s)
```

Identical to Stage 1A. No production build was run: no frontend source changed,
and the build output is gitignored.

## Lint

```bash
uvx --from 'ruff==0.8.1' ruff check crm/     # All checks passed!
uvx --from 'ruff==0.8.1' ruff format crm/    # 3 files reformatted, 326 files left unchanged
```

The three reformatted files are the three new test modules; the reformatting was
applied and the suites re-run after it.

## Scheduler entry (Stage 1A open issue 5)

```python
"hourly": [
    ...
    "crm.outbound.process_scheduled_jobs",
    "crm.outbound.sweep_delivery_states",
],
```

`sweep_delivery_states` is a new wrapper, not a change to
`refresh_delivery_states`, because the two have different contracts: the sweep is
gated on `outbound_engine_enabled` (default OFF — it reads nothing at all) and
never raises, while the function it calls is also used from a request handler and
wants neither guard. The dotted path was resolved on the live site:

```
$ bench --site crm.localhost execute frappe.get_attr --args '["crm.outbound.sweep_delivery_states"]'
"<function sweep_delivery_states at 0x7e86147dbc10>"
```

Four tests cover it in `crm/tests/test_outbound.py`: off-by-default, on when the
flag is on, never raises, and the hooks entry is present.

## Downgrade behaviour

| Change | To undo |
| --- | --- |
| The sequence core | Delete `crm/sequences/` and restore the pre-Stage-1B `crm/api/followup_engine.py`. No data, no schema and no setting is involved — the extraction is code only |
| `crm/automation_context.py`, `crm/counters.py` | Delete them. Nothing imports them except their own tests and the AI client's use of `rows_affected` |
| The AI client hardening | Restore the previous `crm/ai/client.py` and delete `crm/ai/schema.py`. The counter it writes is the SAME field (`CRM AI Settings.requests_this_month`) the old code wrote, so no value needs converting |
| The hourly scheduler entry | Remove the line from `crm/hooks.py`. It is a no-op while the flag is off |

## Open issues handed to Stage 2

1. **The frappe v15 vs v16 mismatch stands** (Stage 1A open issue 1). 47 upstream
   modules still cannot be collected in this container, including
   `crm/permissions/test_org_hierarchy.py`. Role and hierarchy permission tests
   still cannot be run on this machine.
2. **The AI budget reservation holds a row lock for the length of the provider
   call.** The claim is `UPDATE tabSingles ... WHERE there is room`, and it does
   not commit, so the lock is held until the CALLER's transaction ends — which,
   inside the follow-up sweep, is after the network call. Committing inside the
   client is not an option: it would release the follow-up row's
   `SELECT ... FOR UPDATE` early and break the at-most-once ordering. With one
   scheduler and one worker there is no contention today. If Stage 4's Brief card
   makes AI calls concurrent and interactive, measure it before assuming it is
   still free.
3. **`claim_request` fails OPEN on an unexpected database error**, with a log
   entry. Being over the cap fails closed; a broken counter row does not take the
   AI features down. That is a deliberate trade and it is tested
   (`test_an_unexpected_database_failure_fails_open_and_is_logged`).
4. **No email adapter exists yet**, by design. `crm/sequences/core.py` documents
   what one must implement, and `test_sequences.py` proves the core runs with an
   adapter that touches no WhatsApp doctype at all. Item 21 builds the real one on
   `crm.outbound`.
5. **`crm.automation_context` has no consumer.** That is what F4 asked for. Stage
   5's workflow rules must supply their own counter and day columns to
   `reserve_daily_slot`, and must re-check permissions at execution time.
6. **Stage 1A open issue 4 stands**: no channel adapter is registered for the
   outbound engine, so `process_scheduled_jobs` cannot send even with the flag on.
   Stage 3 registers the first one.
