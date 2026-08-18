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

---

# Stage 2A (Low-risk slice) — verification record

## Stage 2A

Scope: master spec §5 items **2** (tag chips), **3** (duplicate warning on
create), **10** (Cmd/Ctrl+K palette) and **11** (recently viewed). Items 1, 6 and
23 of the same stage belong to a second worker running in parallel; their files
are listed here only where they affected this stage's verification.

Branch: `feat/feature-expansion`. Nothing is committed by this stage; all changes
sit in the working tree, on top of Stage 1B.

Run 2026-08-19, same environment and same commands as Stage 1A and 1B.

---

## What changed

### Backend — files added

| File | What it is |
| --- | --- |
| `crm/api/tags.py` | Item 2. Four whitelisted endpoints (`get_tags`, `add_tag`, `remove_tag`, `search_tags`) in front of `frappe.desk.doctype.tag.tag.DocTags`, plus the tag validation the core writer does not do |
| `crm/api/duplicates.py` | Item 3. `check_duplicates(doctype, email, phone)` over the Stage-1A `custom_parama_*` columns |
| `crm/api/search.py` | Items 10 and 11. `palette_search(query, limit)` over six groups, and `resolve_records(records)` for the recents list |
| `crm/tests/test_tags.py` | 36 tests |
| `crm/tests/test_duplicates.py` | 18 tests |
| `crm/tests/test_search.py` | 36 tests |

**No schema change.** Stage 2A adds no doctype, no field, no index and no patch.
`crm/patches.txt`, `crm/hooks.py` and `fcrm_settings.json` are untouched by this
stage, so no `bench migrate` is needed to run this code. The two columns the
duplicate check reads were created by Stage 1A; `_user_tags` is created by the
framework's own `DocTags.setup()` the first time a doctype is tagged.

### Frontend — files added

| File | What it is |
| --- | --- |
| `frontend/src/utils/tags.js` | The 8-colour pastel palette, the name hash, the "+N" collapse rule and the fuzzy ranker. Pure functions |
| `frontend/src/utils/palette.js` | Row routing, group icons, quick actions, section building, arrow-key movement. Pure functions |
| `frontend/src/utils/recents.js` | The localStorage key shape and the merge/dedupe/cap rules. Pure functions |
| `frontend/src/composables/recents.js` | The one shared `useStorage` handle over those rules |
| `frontend/src/composables/commandPalette.js` | The module-level open/close ref |
| `frontend/src/components/TagChips.vue` | Chips, the "+N" expander, and the "+" Combobox with fuzzy search and a "Create '<query>'" row |
| `frontend/src/components/CommandPalette.vue` | The palette itself |
| `frontend/src/components/DuplicateWarning.vue` | The amber banner |
| `frontend/tests/unit/tags.test.js` | 30 tests |
| `frontend/tests/unit/palette.test.js` | 24 tests |
| `frontend/tests/unit/recents.test.js` | 15 tests |

### Frontend — files changed

| File | Change |
| --- | --- |
| `frontend/src/components/Modals/GlobalModals.vue` | Mounts `<CommandPalette />` once. It is rendered by both `DesktopLayout` and `MobileLayout`, which is why the palette is reachable from all three of its triggers |
| `frontend/src/components/Layouts/AppSidebar.vue` | One `SidebarItem` above Notifications that opens the palette, with a ⌘K / Ctrl K hint. Nothing else in the file was touched — the grouped-sidebar work was already committed |
| `frontend/src/components/Mobile/MobileAppHeader.vue` | A search button in the top bar (spec §2.18) |
| `frontend/src/components/Modals/LeadModal.vue`, `ContactModal.vue`, `DealModal.vue` | One `<DuplicateWarning>` each, bound to that form's email and phone fields |
| `frontend/src/pages/Lead.vue`, `Deal.vue` | `<TagChips>` under the record title in the side panel, plus the recents write |
| `frontend/src/pages/MobileLead.vue`, `MobileDeal.vue` | `<TagChips>` in a row of its own under the action bar — editable, not read-only — plus the recents write |
| `frontend/src/pages/Contact.vue`, `Organization.vue`, `MobileContact.vue`, `MobileOrganization.vue` | The recents write only |
| `frontend/src/components/ListViews/LeadsListView.vue`, `DealsListView.vue` | A `_user_tags` column branch rendering read-only chips; clicking one emits `applyTagFilter` |
| `frontend/src/pages/Leads.vue`, `Deals.vue` | One line each wiring `applyTagFilter` to `ViewControls` |
| `frontend/src/components/ViewControls.vue` | New `applyTagFilter(tag)`, exposed. No existing function changed |
| `frontend/src/utils/model.js` | One entry so "Tags" is selectable in Column Settings |

### Docs

| File | Change |
| --- | --- |
| `demo-package/specs/permission-matrix.md` | Stage 2A section: all seven endpoints, one row each, plus the honest statement of which doctypes have no row-level rule in this app |
| `demo-package/specs/stage2a-notes.md` | New. The changes this stage wanted but could not make, and the findings handed to other owners |

---

## Backend — every module that collects, run individually

Same push-then-run commands as Stage 1A. The push:

```bash
cd /home/kreshnith/CRM
tar -cf - --exclude=__pycache__ --exclude='*.pyc' --exclude='crm/public' crm | \
  docker exec -i crm-local-frappe-1 bash -lc 'cd ~/frappe-bench/apps/crm && tar -xf -'
```

Verbatim result lines, 2026-08-19:

| Module | Result |
| --- | --- |
| `crm.fcrm.doctype.crm_invitation.test_crm_invitation` | `Ran 13 tests in 4.195s` `OK` |
| `crm.fcrm.doctype.crm_product.test_product_item_sync` | `Ran 25 tests in 0.033s` `OK (skipped=18)` |
| `crm.fcrm.doctype.crm_products.test_crm_products` | `Ran 7 tests in 0.001s` `OK (skipped=6)` |
| `crm.integrations.erpnext.test_utils` | `Ran 9 tests in 0.009s` `OK (skipped=2)` |
| `crm.tests.test_ai_client` | `Ran 35 tests in 0.269s` `OK` |
| `crm.tests.test_automation_context` | `Ran 20 tests in 0.507s` `OK` |
| **`crm.tests.test_duplicates`** (new, Stage 2A) | `Ran 18 tests in 43.724s` `OK` |
| `crm.tests.test_email_compose` (new, other worker) | `Ran 18 tests in 0.522s` `OK` |
| `crm.tests.test_exchange_rate` | `Ran 16 tests in 0.547s` `OK` |
| `crm.tests.test_followup_engine` | `Ran 99 tests in 20.623s` `OK` |
| `crm.tests.test_itinerary` | `Ran 112 tests in 53.178s` `OK` |
| `crm.tests.test_outbound` | `Ran 46 tests in 2.327s` `OK` |
| `crm.tests.test_reminders` (new, other worker) | `Ran 40 tests in 6.518s` `OK` |
| **`crm.tests.test_search`** (new, Stage 2A) | `Ran 36 tests in 5.252s` `OK` |
| `crm.tests.test_sequences` | `Ran 39 tests in 0.157s` `OK` |
| `crm.tests.test_snippets` (new, other worker) | `Ran 45 tests in 3.138s` `OK` |
| `crm.tests.test_state_options` | `Ran 7 tests in 0.259s` `OK` |
| `crm.tests.test_suppression` | `Ran 32 tests in 2.812s` `OK` |
| `crm.tests.test_sweeps` | `Ran 31 tests in 61.592s` `OK` |
| **`crm.tests.test_tags`** (new, Stage 2A) | `Ran 36 tests in 8.150s` `OK` |
| `crm.tests.test_whatsapp` | `Ran 62 tests in 0.212s` `OK` |
| `crm.tests.test_whatsapp_demo` | `Ran 12 tests in 0.051s` `OK` |

**758 tests, 0 failures, 0 errors, 26 skips**, across 22 modules. Stage 2A adds
90 of them (36 + 18 + 36). The other 103 new tests since Stage 1B belong to the
parallel worker's items 1, 6 and 23; they are reported here because they run in
the same tree, not because this stage wrote them.

The module list was derived rather than remembered: every `crm/**/test_*.py` was
checked for an `IntegrationTestCase` / `UnitTestCase` import, and the 22 that do
not import one were run. The other **47 still do not collect** in this container,
unchanged from Stage 1A and 1B — the container has frappe v15.117.0 while the app
declares `>=16.0.0-dev`. They are reported as not collectable here, never as
passes.

### The permission-matrix tests DID run — Stage 1A/1B open issue 1 is narrower than it looked

Stage 1A and 1B both recorded that "role and hierarchy permission tests cannot be
run on this machine". That is true of `crm/permissions/test_org_hierarchy.py`,
which imports `IntegrationTestCase` and still does not collect. It is NOT true of
the hierarchy RULE, which this stage tests directly and without any patching, by
the pattern `crm/tests/test_itinerary.py::TestPermissions` already used: create a
real `User` with the `Sales User` role, `frappe.set_user(...)`, and call the
endpoint.

So the §3 permission-matrix requirement is met with real tests, not with mocks:

* `crm/tests/test_duplicates.py::TestPermissions::test_a_sales_user_does_not_see_another_teams_lead` —
  a Sales User who supplies the EXACT email address of a lead owned by somebody
  else gets `[]`.
* `...::test_a_sales_user_does_see_their_own_lead` — the same user, the same
  address, after the lead is reassigned to them: one result. The filter is
  row-level, not a blanket refusal.
* `crm/tests/test_search.py::TestPermissions::test_a_sales_user_does_not_see_another_teams_lead`
  and `::test_a_sales_user_does_not_reach_it_by_email_either` — the same for the
  palette, by name and by address.
* `crm/tests/test_search.py::TestPermissions::test_a_recent_the_user_lost_access_to_is_dropped` —
  item 11's requirement: a localStorage recent whose record the user may no
  longer read renders nothing.
* `crm/tests/test_tags.py::TestPermissions` — read, add and remove on another
  team's lead all raise `PermissionError`, and
  `::test_the_refused_write_did_not_happen` proves the column was not written
  before the check.

Both endpoint modules also carry
`test_the_scope_comes_from_the_hierarchy_conditions`, which asserts the condition
TEXT that `crm.permissions.org_hierarchy` produces for that user. That is the
"test the query-condition application directly" fallback the stage brief asked
for; it is kept alongside the real tests, not instead of them, so that a rewrite
away from `frappe.get_list` fails loudly.

---

## Live check on the running demo site

The unit tests prove the rule. This proves the wiring, on seeded demo data,
signed in over HTTP as the demo Sales User `priya@demo.crm` (the site has
`enable_sales_hierarchy = 1`).

```
$ curl -s -b $J "http://crm.localhost:8000/api/method/crm.api.search.palette_search?query=an"
{"message":{"query":"an","groups":[{"doctype":"CRM Lead","label":"Leads","items":[
  {"doctype":"CRM Lead","name":"CRM-LEAD-2026-00019","title":"Amara Okafor","subtitle":"Lumen Analytics · Proposal Sent", ...

$ curl -s -b $J ".../crm.api.duplicates.check_duplicates?doctype=CRM%20Lead&email=amara.okafor@example.com"
{"message":[{"doctype":"CRM Lead","name":"CRM-LEAD-2026-00019","title":"Amara Okafor","matched_field":"email"}]}

$ curl -s -b $J ".../crm.api.tags.add_tag?doctype=CRM%20Lead&name=X&tag=Y"      # GET
403
```

The 403 is the POST-only whitelisting doing its job. With a CSRF token and a
POST, the whole round trip works:

```
add_tag        -> {"message":["Live Smoke"]}
get_tags       -> {"message":["Live Smoke"]}
search_tags    -> {"message":["Live Smoke"]}
add_tag "a,b"  -> frappe.exceptions.ValidationError: A tag cannot contain a comma.
resolve_records([real, "nope"]) -> one row, the fabricated name dropped
remove_tag     -> {"message":[]}
```

The `Tag` master row and the `Tag Link` created by that check were deleted
afterwards; the demo site is back where it started.

Row filtering, on real data rather than a fixture: one of the site's eight leads
(`CRM-LEAD-2026-00023`, "Animesh") is outside `priya`'s subtree, and
`frappe.get_list` returns 7 of 8 for her. Searching that lead's exact name in the
palette as `priya`:

```
palette for 'Animes' -> ['CRM-LEAD-2026-00018', 'CRM-DEAL-2026-00007', 'Animesh']
contains the hidden lead? False
```

A DIFFERENT lead with the same person's name comes back; the one she may not see
does not. That is §3 working end to end.

### Speed (item 10's "under 500 ms" target)

Five `palette_search` calls against the seeded database, in-process:

```
5 palette queries in 0.219s
```

~44 ms each, six `get_list` queries per call. Well inside the target. Stated
limit: the derived email and phone columns are searched as "contains", so the
Stage-1A index cannot be used for them. That is deliberate — typing the last four
digits of a number has to find the record — and the index still earns its keep on
the exact-equality lookups in `crm.api.duplicates`. On a database an order of
magnitude larger this is the first thing to measure again.

---

## Frontend

```bash
cd /home/kreshnith/CRM/frontend && npx vitest run
```

```
 RUN  v4.1.4 /home/kreshnith/CRM/frontend

 Test Files  16 passed (16)
      Tests  349 passed (349)
   Start at  00:30:56
   Duration  3.78s (transform 2.83s, setup 306ms, import 4.46s, tests 511ms, environment 12.17s)
```

Stage 2A's own three files, run alone:

```
 Test Files  3 passed (3)
      Tests  69 passed (69)
```

Against Stage 1B's 10 files / 224 tests (30 + 24 + 15 = 69 here). The other 56
new tests belong to the parallel worker.

What the 69 cover: the "+N" collapse rule (§2.13), the colour hash being stable
and case-insensitive, the fuzzy ranker's three tiers, the "Create '<query>'"
offer being suppressed for a name that already exists in another casing, every
palette route including the task/note parent fallback, arrow-key wrapping,
**`buildSections` never returning an empty list** (§2's "Cmd+K is never blank"),
the DOM ids that `aria-activedescendant` points at being unique, and the recents
key being scoped per site AND per user with a cap that holds under 30 writes.

### Production build

```bash
cd /home/kreshnith/CRM/frontend
NODE_OPTIONS="--max-old-space-size=6144" npx vite build --base=/assets/crm/frontend/
```

```
✓ built in 1m 21s

PWA v0.21.2
mode      generateSW
precache  1 entries (0.00 KiB)
warnings
  An error occurred when globbing for files. '(0 , brace_expansion_1.expand) is not a function'
```

Same glob warning and same `NODE_OPTIONS` requirement as Stage 1A. Both
`crm/public/frontend` and `crm/www/crm.html` are gitignored (`.gitignore:10-11`),
so the build left no working-tree churn. The build was re-run after the last
code change and passed again (`✓ built in 1m 14s`).

**The running demo site still serves the PREVIOUS bundle.** The container serves
its own clone at `~/frappe-bench/apps/crm`, and the push command this stage uses
excludes `crm/public` on purpose. Building is a verification step here, not a
deploy; deploying a half-finished Stage 2 (this worker's items plus the parallel
worker's) is a decision for whoever closes the stage.

**The first attempt failed, and not on a Stage 2A file.** Verbatim:

```
[plugin vite:vue] src/components/Settings/Snippets/SnippetsPage.vue (122:17): Error parsing JavaScript expression: Unterminated string constant. (2:18)
```

That file belongs to the parallel worker (item 23). Rather than edit somebody
else's file, this stage rebuilt an rsync copy of `frontend/` in which only that
one interpolation was replaced with a placeholder, and that copy built clean —
proving every Stage 2A file compiles. The owner then fixed it in the real tree at
00:39 (the message is now built in the script block and rendered with `v-text`),
and the build above is the real tree, unmodified. The finding is recorded in
`demo-package/specs/stage2a-notes.md` §4.

### Lint

```bash
uvx --from 'ruff==0.8.1' ruff check crm/api/tags.py crm/api/duplicates.py crm/api/search.py \
  crm/tests/test_tags.py crm/tests/test_duplicates.py crm/tests/test_search.py
# All checks passed!
uvx --from 'ruff==0.8.1' ruff format <same six files>
# 6 files left unchanged
```

Ruff was pointed at Stage 2A's six files rather than at `crm/` as in Stage 1,
deliberately: a second worker has uncommitted Python in the same tree this stage,
and `ruff format crm/` would have rewritten their files mid-edit.

---

## Deviations from the stage brief

1. **The duplicate banner sits under the form, not under the field.** Item 3 asks
   for it "directly under the field". The email and phone inputs are rendered by
   `FieldLayout` / `Field.vue`, which this stage is barred from touching and which
   master spec §8 D4 parks. The banner renders immediately below the whole
   `<FieldLayout>` block instead — still inline, still amber, still non-blocking,
   still dismissible. Full reasoning and what a `Field.vue` owner would need to
   add: `demo-package/specs/stage2a-notes.md` §1.
2. **Mobile tags are editable, not read-only.** The brief allowed read-only as
   the minimum. A row of its own under the mobile action bar was cheap, so both
   mobile record pages get the full editable component.
3. **`frontend/src/utils/model.js` and `ViewControls.vue` were touched**, one
   entry and one new function respectively. Neither is in this stage's stated
   ownership list, and neither modifies existing behaviour. Recorded in
   `stage2a-notes.md` §2 and §3.
4. **Tag chips are on CRM Lead and CRM Deal only.** `crm.api.tags` allowlists
   exactly those two. Contacts and Organizations can carry `_user_tags` — the
   framework and the list filter both support it — but no endpoint of ours will
   write them, so nothing tags them from the CRM UI.

---

## Open issues handed on

1. **`Contact`, `CRM Organization`, `CRM Task` and `FCRM Note` have no row-level
   permission rule anywhere in this app.** `crm/hooks.py` registers
   `permission_query_conditions` for CRM Lead and CRM Deal only. The palette and
   the duplicate check therefore show exactly what the Tasks, Notes, Contacts and
   Organizations list pages already show — no more, but also no less. This is
   pre-existing and it is now visible in one more place. It belongs on the Stage 6
   correctness/security reviewer's list, not to a feature stage.
2. **`frappe.desk.doctype.tag.tag.add_tag` is still reachable.** It is whitelisted
   by the framework, it writes `_user_tags` before it checks `write`, and it
   accepts a comma inside a tag. `crm.api.tags` is a safe door added beside it,
   not a replacement. Closing the framework one means overriding a framework
   whitelist — a Stage 6 decision.
3. **The palette searches the derived columns with a leading wildcard**, so the
   Stage-1A indexes do not serve it. Measured at ~44 ms per query on the demo
   database; re-measure before this ships to a materially larger site.
4. **CRM Task and FCRM Note have no record route in this app.** The palette opens
   the parent record when the row names one and falls back to the list otherwise.
   A stage that adds a detail route should extend
   `frontend/src/utils/palette.js::RECORD_ROUTES` and delete the fallback.
5. **No live click-through of the UI was performed by this stage.** The endpoints
   were exercised over real HTTP as a real Sales User and every component compiles
   and is unit-tested, but nobody drove the palette, the chips or the banner in a
   browser — the demo site still serves the pre-Stage-2 bundle (see the build
   section). That walkthrough is Stage 6's (b) reviewer's job and is explicitly
   still owed. Two things it should check first, because they are the parts a
   unit test cannot reach: the frappe-ui `Combobox` in `TagChips.vue` (this stage
   uses its `trigger="button"` + `#trigger` slot + `type: 'custom'` option
   contract, all of which are library behaviour, not ours), and the palette's
   focus handling inside reka-ui's `DialogContent`.
6. **Lint scope was narrowed on purpose.** `ruff format crm/` and
   `prettier --write frontend/` were NOT run tree-wide, because a second worker
   has uncommitted files in the same tree. Whoever closes Stage 2 should run both
   tree-wide once the parallel work has landed.
7. **Stage 1A/1B open issue 1 stands but is narrower than recorded.** The 47
   v16-only modules still do not collect, including
   `crm/permissions/test_org_hierarchy.py`. Hierarchy behaviour itself IS testable
   here, and Stage 2A tests it — see the section above.

---

# Stage 2B — verification record

Scope: master spec §5 items **1** (task due-date reminders, on the F5 ledger),
**6** (email forward) and **23** (snippets). Run 2026-08-19, same commands as
Stage 1A and 1B.

Branch: `feat/feature-expansion`. Nothing is committed by this stage; all
changes sit in the working tree, alongside Stage 2A's (a second worker was
landing items 2, 3, 10 and 11 in the same tree at the same time — see the note
on shared files at the end).

Deviations, decisions and open questions live in
`demo-package/specs/stage2b-notes.md`. The endpoints are in
`demo-package/specs/permission-matrix.md` under "Stage 2B".

---

## What changed

### Files added — backend

| File | What it is |
| --- | --- |
| `crm/reminders.py` | Item 1. The bounded, flag-gated, ledger-backed reminder sweep |
| `crm/api/snippets.py` | Item 23. `get_snippets`, `render`, the merge, and the two permission hooks |
| `crm/api/email.py` | Item 6. `send_email` — the composer's send path with the suppression ledger in front of it |
| `crm/fcrm/doctype/crm_snippet/` | The `CRM Snippet` doctype: JSON, controller, `__init__.py` |
| `crm/patches/v1_0/set_task_reminder_offset_default.py` | Gives the new Int column on the Single its intended default of 60 |
| `crm/tests/test_reminders.py` | 40 tests |
| `crm/tests/test_snippets.py` | 45 tests |
| `crm/tests/test_email_compose.py` | 18 tests |

### Files added — frontend

| File | What it is |
| --- | --- |
| `frontend/src/utils/tasks.js` | The due-date chip state, class and tooltip reason. Pure |
| `frontend/src/utils/snippets.js` | `filterSnippets`, `slashTrigger`, `applySnippet`, `htmlToText`. Pure |
| `frontend/src/utils/emailForward.js` | `forwardSubject`, `forwardQuote`, `forwardedAttachments`, `deletableAttachments`. Pure |
| `frontend/src/components/Modals/SnippetSelectorModal.vue` | The searchable picker both composers open |
| `frontend/src/components/Settings/Snippets/SnippetsPage.vue` | Settings → Email → Snippets: list, create, edit, delete |
| `frontend/tests/unit/taskDueDate.test.js` | 11 tests |
| `frontend/tests/unit/snippets.test.js` | 28 tests |
| `frontend/tests/unit/emailForward.test.js` | 17 tests |

### Files changed

| File | Change |
| --- | --- |
| `crm/hooks.py` | One cron entry (`crm.reminders.send_task_reminders` on `*/15 * * * *`), one `permission_query_conditions` entry and one `has_permission` entry for `CRM Snippet` |
| `crm/feature_flags.py` | One registry entry: `task_reminders_enabled` |
| `crm/fcrm/doctype/fcrm_settings/fcrm_settings.json` | One Check in the Feature Flags section, plus a collapsible "Task Reminders" section with the offset and the email switch |
| `crm/patches.txt` | One line |
| `frontend/src/components/Activities/EmailArea.vue` | A Forward button next to Reply and Reply All |
| `frontend/src/components/CommunicationArea.vue` | `forward()` exposed; `sendMail` moved onto `crm.api.email.send_email`; Discard no longer deletes carried files |
| `frontend/src/components/EmailEditor.vue` | A snippet button, the picker, and `focusTo()` for the forward |
| `frontend/src/components/Activities/WhatsAppInboxComposer.vue` | Snippet button and the `/` trigger |
| `frontend/src/components/Activities/WhatsAppBox.vue` | Snippet icon and the `/` trigger |
| `frontend/src/components/ListViews/TasksListView.vue` | Due-date chip colour and its tooltip reason |
| `frontend/src/components/Activities/TaskArea.vue` | The same, on the record page's Tasks tab |
| `frontend/src/components/Settings/Settings.vue` | The Snippets entry under Email |
| `demo-package/specs/permission-matrix.md` | The Stage 2B section |

---

## Migrations

### New doctypes

| Doctype | Purpose | Unique index | Other indexes |
| --- | --- | --- | --- |
| `CRM Snippet` | One reusable piece of composer text, with `{{ token }}` merge fields | — | `shortcut` |

`shortcut` is deliberately NOT unique at the table level. Two Sales Users may
each hold a private `/booking`, because neither of them ever sees both; a shared
snippet collides with everything. That rule cannot be expressed as a table-wide
index, so it lives in `CRMSnippet.check_shortcut_is_free` and is tested three
ways.

### New fields on existing doctypes

| Doctype | Field | Type | Notes |
| --- | --- | --- | --- |
| `FCRM Settings` | `task_reminders_enabled` | Check, default `0` | The flag. Registry: `crm/feature_flags.py` |
| `FCRM Settings` | `task_reminders_section` | Section Break | Collapsible, `depends_on` the flag |
| `FCRM Settings` | `task_reminder_offset_minutes` | Int, default `60` | Org-wide. See the note below |
| `FCRM Settings` | `task_reminder_email` | Check, default `0` | The optional second channel |

### Patches

`crm.patches.v1_0.set_task_reminder_offset_default #19-08-2026`.

It exists because of a trap worth recording: **`FCRM Settings` is a Single, and
a new `Int` column on a Single that already exists is written as `0`, not as the
JSON default.** `0` is a legitimate value here — it means "remind at the due
time itself" — so the reader cannot tell an unset column from a deliberate zero.
The patch settles it once, and only when the value is falsy AND the feature has
never been switched on.

Observed on the demo site, before and after:

```
$ bench --site crm.localhost execute ...   # before the patch
offset: 0

Executing crm.patches.v1_0.set_task_reminder_offset_default #19-08-2026 in crm.localhost (_c8b3e5206096f0bd)
Success: Done in 0.189s

$ bench --site crm.localhost execute ...   # after
offset: 60
```

A database backup was taken immediately before the first migrate:
`./crm.localhost/private/backups/20260819_002810-crm_localhost-database.sql.gz`.

### State on the demo site after migrate

```
snippet doctype: CRM Snippet
table: (('tabCRM Snippet',),)
task_reminders_enabled True
task_reminder_offset_minutes True
task_reminder_email True
offset: 60
email leg: False
flags: {'outbound_engine_enabled': False, 'task_reminders_enabled': False}
sweep with flag off: 0
reminder log rows: 0
```

Both flags are OFF, the sweep reads nothing, and the ledger is empty. No
user-visible automation starts by itself.

### Downgrade behaviour

| Change | To undo |
| --- | --- |
| `CRM Snippet` | Delete the DocType record and drop `tabCRM Snippet`. Only `crm/api/snippets.py` and the Settings page read it |
| The four `FCRM Settings` fields | Delete them. `crm.feature_flags.is_enabled` then reads the flag as OFF and `reminder_offset_minutes` falls back to 60 — but the sweep never runs, because the flag is gone |
| The cron entry | Remove `crm.reminders.send_task_reminders` from `crm/hooks.py`. It is a no-op while the flag is off |
| The two `CRM Snippet` permission hooks | Remove them from `crm/hooks.py`. The doctype then falls back to its role permissions, which are wider — delete the doctype instead |
| The composer's send path | Restore the `frappe.core.doctype.communication.email.make` call in `CommunicationArea.vue::sendMail` and add `send_email: 1` back. That also removes the suppression check |
| The due-date chip colours | Revert two `:class` bindings and two tooltip helpers. No data is involved |
| Reminder ledger rows | Delete the `CRM Reminder Log` rows. Deleting a row makes that one reminder eligible again — the row IS the at-most-once mechanism |

No upstream core schema was edited. No existing field, index or doctype was
changed or removed.

---

## Backend — every module that collects, run individually

Same push-then-run commands as Stage 1A. Verbatim result lines:

| Module | Result |
| --- | --- |
| `crm.fcrm.doctype.crm_invitation.test_crm_invitation` | `Ran 13 tests in 3.756s` `OK` |
| `crm.fcrm.doctype.crm_product.test_product_item_sync` | `Ran 25 tests in 0.029s` `OK (skipped=18)` |
| `crm.fcrm.doctype.crm_products.test_crm_products` | `Ran 7 tests in 0.001s` `OK (skipped=6)` |
| `crm.integrations.erpnext.test_utils` | `Ran 9 tests in 0.007s` `OK (skipped=2)` |
| `crm.tests.test_ai_client` | `Ran 35 tests in 0.257s` `OK` |
| `crm.tests.test_automation_context` | `Ran 20 tests in 0.690s` `OK` |
| `crm.tests.test_duplicates` (Stage 2A) | `Ran 18 tests in 3.848s` `OK` |
| **`crm.tests.test_email_compose`** (new) | `Ran 18 tests in 0.714s` `OK` |
| `crm.tests.test_exchange_rate` | `Ran 16 tests in 0.596s` `OK` |
| `crm.tests.test_followup_engine` | `Ran 99 tests in 19.650s` `OK` |
| `crm.tests.test_itinerary` | `Ran 112 tests in 42.777s` `OK` |
| `crm.tests.test_outbound` | `Ran 46 tests in 2.434s` `OK` |
| **`crm.tests.test_reminders`** (new) | `Ran 40 tests in 6.471s` `OK` |
| `crm.tests.test_search` (Stage 2A) | `Ran 36 tests in 5.249s` `OK` |
| `crm.tests.test_sequences` | `Ran 39 tests in 0.122s` `OK` |
| **`crm.tests.test_snippets`** (new) | `Ran 45 tests in 2.337s` `OK` |
| `crm.tests.test_state_options` | `Ran 7 tests in 0.180s` `OK` |
| `crm.tests.test_suppression` | `Ran 32 tests in 1.999s` `OK` |
| `crm.tests.test_sweeps` | `Ran 31 tests in 48.804s` `OK` |
| `crm.tests.test_tags` (Stage 2A) | `Ran 36 tests in 5.595s` `OK` |
| `crm.tests.test_whatsapp` | `Ran 62 tests in 0.187s` `OK` |
| `crm.tests.test_whatsapp_demo` | `Ran 12 tests in 0.045s` `OK` |

**758 tests, 0 failures, 0 errors, 26 skips.** 103 of them are Stage 2B's
(40 + 45 + 18); 90 are Stage 2A's, which were already in the tree.

### Modules that still do NOT collect in this container

Unchanged from Stage 1A and 1B: the same 47 upstream modules abort at import,
because the container has frappe v15.117.0 while the app declares
`>=16.0.0-dev`. Spot-checked, verbatim, on this tree:

```
$ bench --site crm.localhost run-tests --module crm.permissions.test_org_hierarchy
ImportError: cannot import name 'IntegrationTestCase' from 'frappe.tests' (/home/frappe/frappe-bench/apps/frappe/frappe/tests/__init__.py)

$ bench --site crm.localhost run-tests --module crm.tests.test_dashboard
ImportError: cannot import name 'IntegrationTestCase' from 'frappe.tests' (/home/frappe/frappe-bench/apps/frappe/frappe/tests/__init__.py)
```

Stage 2B adds no test to that set and removes none. They are reported as not
collectable here, not as passes.

**What that costs, and what covers it.** `crm/permissions/test_org_hierarchy.py`
is the module a permission-matrix test would normally extend. Because it cannot
run here, `crm/tests/test_snippets.py` exercises the real rules directly instead
of patching them: it sets a real Sales User with `frappe.set_user` and lets the
real `has_snippet_permission` and the real org-hierarchy `has_permission` hook
refuse the read
(`TestVisibility::test_another_users_private_snippet_is_invisible`,
`TestRenderPermissions::test_a_record_the_caller_cannot_read_is_refused`).

### What the three new suites actually assert

The four checks the brief named, and where they are:

| Required check | Test |
| --- | --- |
| Reminder idempotency — run the job twice, one notification | `test_reminders.py::TestIdempotency::test_two_runs_produce_one_notification` |
| Ledger dedup under a simulated double-fire | `::TestIdempotency::test_a_simulated_double_fire_claims_once`, plus `::test_the_claim_is_committed_before_the_notification`, which asserts the ORDER that makes a racing worker's insert collide |
| Snippet permissions — another user's private snippet is invisible | `test_snippets.py::TestVisibility::test_another_users_private_snippet_is_invisible` and `::TestDocumentPermission::test_a_user_may_not_read_another_users_private_snippet` |
| Suppression check on the forward path | `test_email_compose.py::TestSendEmail` — `test_a_suppressed_recipient_is_dropped_and_named`, `test_a_suppressed_cc_is_dropped_and_the_mail_still_goes`, `test_nothing_is_sent_when_every_recipient_opted_out` |

Two things the reminder suite had to learn about this site, recorded so the next
worker does not rediscover them:

1. **Inserting a `CRM Task` with an assignee already produces a
   `CRM Notification`** ("assigned a new task ... to you", through
   `crm.api.todo.after_insert`). A naive count of notifications on the task is
   one too high. `notifications_for` filters to the reminder text.
2. **The reminder carries no `owner`.** `notify_user` returns early when the
   owner equals the recipient, and a task somebody made for themselves is the
   commonest case there is. Asserted by
   `TestDelivery::test_a_self_assigned_task_still_notifies`.

---

## Live check on the demo site

Not a test — the endpoints run against real seeded data, inside a transaction
that was rolled back afterwards:

```
lead: {'name': 'CRM-LEAD-2026-00023', 'first_name': 'Animesh', 'lead_owner': 'crm.admin@example.com'}
rendered: <p>Hi Animesh, from Administrator. {{ nope }}</p>
flag off sweep: 0
due tasks in window: 0
rolled back; snippet still there? None
```

The merge resolved `{{ first_name }}` and `{{ user.full_name }}` and left the
unknown `{{ nope }}` exactly as typed — a misspelt token is meant to be found by
the agent, not by the customer.

---

## Frontend

```
 RUN  v4.1.4 /home/kreshnith/CRM/frontend

 Test Files  16 passed (16)
      Tests  349 passed (349)
   Start at  00:40:15
   Duration  3.40s (transform 730ms, setup 268ms, import 1.69s, tests 610ms, environment 12.08s)
```

16 files against Stage 1B's 10: three are Stage 2B's (`taskDueDate.test.js`,
`snippets.test.js`, `emailForward.test.js`, 11 + 28 + 17 = 56 tests) and three
are Stage 2A's (69 tests).

**There are no component tests in this repo, and Stage 2B did not add the first
one.** `@vue/test-utils` is not a dependency and nothing under
`frontend/tests/unit/` mounts anything. Every decision worth testing was
therefore pushed into a pure function — the chip state, the `/` trigger, the
snippet insert, the forward subject and quote, the attachment marking — and
tested there. The wiring inside the `.vue` files is covered by the production
build, not by a test. That is stated rather than papered over.

### Frontend production build

```bash
cd /home/kreshnith/CRM/frontend
NODE_OPTIONS="--max-old-space-size=6144" npx vite build --base=/assets/crm/frontend/
```

```
✓ built in 1m 27s

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

The globbing warning is the same one Stage 1A recorded and is unrelated.

The FIRST build attempt failed, on Stage 2B's own code, and the failure is worth
recording because the cause is not obvious:

```
x Build failed in 1m
error during build:
[vite-plugin-pwa:build] There was an error during the build:
  [plugin vite:vue] src/components/Settings/Snippets/SnippetsPage.vue (122:17): Error parsing JavaScript expression: Unterminated string constant. (2:18)
```

A help line explaining the merge tokens contained a literal `{{ field }}` inside
a `{{ __(...) }}` interpolation, and the Vue template compiler cannot parse
nested braces. The hint is now assembled in the script with `{0}`-style
placeholders and rendered through `v-text`. **Any later feature that documents a
`{{ token }}` in a template will hit exactly this.**

### Lint

```bash
uvx --from 'ruff==0.8.1' ruff check crm/     # All checks passed!
uvx --from 'ruff==0.8.1' ruff format crm/    # 344 files left unchanged
npx prettier@3.2.5 --write <the 16 changed frontend files>
npx oxlint@1.50.0 <the 13 changed frontend source files>
# Found 0 warnings and 0 errors.
# Finished in 15ms on 13 files with 93 rules using 8 threads.
```

Ruff is pinned to v0.8.1 and prettier/oxlint to the versions in
`.pre-commit-config.yaml`. The vitest suite was re-run after the prettier pass
and is the 349-test result above.

---

## Shared files, with a second worker in the tree

Stage 2A (items 2, 3, 10, 11) was being written into the same working tree at
the same time. The single-owner rule held: `crm/hooks.py`,
`crm/fcrm/doctype/fcrm_settings/fcrm_settings.json`, `crm/patches.txt` and
`frontend/src/components/Settings/Settings.vue` carry Stage 2B's changes only
(`git diff --stat` on `crm/hooks.py` and `Settings.vue`: 12 and 7 added lines,
all of them listed above). `EmailEditor.vue`, `CommunicationArea.vue` and
`EmailArea.vue` were touched by Stage 2B alone, as the master spec's collision
rule requires. No file in the "must not touch" list was modified.

---

## Open issues handed to Stage 3

1. **`/` does not open the snippet picker in the EMAIL composer** — frappe-ui's
   `RichTextKit` already owns that character for the formatting menu. The
   snippet icon does. Full reasoning and the three rejected alternatives are in
   `demo-package/specs/stage2b-notes.md`.
2. **`crm/api/activities.py` does not return the Communication `name`.** Forward
   did not need it (attachments re-link by their own File docnames), but item 5
   (Send Later) and item 19 (read receipts) will, and it is a one-line change to
   `get_deal_activities` / `get_lead_activities`.
3. **The two WhatsApp composers are still duplicates.** The snippet trigger had
   to be wired twice; only the pure helpers are shared.
4. **A reply still loses the user's signature.** `reply()` in `EmailArea.vue`
   calls `clearContent()` after the `showEmailBox` watcher has inserted it. That
   is pre-existing and was left alone; `forward()` puts the signature back.
5. **The frappe v15 vs v16 mismatch stands** (Stage 1A open issue 1).
6. **Stage 1A open issue 4 and Stage 1B open issue 6 stand**: no channel adapter
   is registered for the outbound engine. Item 6 does not use it — the composer
   sends through `Communication.make`, as it always did — so Send Later is still
   the first feature that will need one.

---

# Stage 3B

Scope: master spec §5 items **7** (target meter), **22** (deal-health flags) and
**24** (Today page). Branch `feat/feature-expansion`. Nothing committed by this
stage; all changes sit in the working tree.

Decisions, deviations and the two files touched outside the stated ownership are
in `demo-package/specs/stage3b-notes.md`. Endpoint authorisation rows are in
`demo-package/specs/permission-matrix.md`.

## Files

**New — backend**

* `crm/deal_health.py` — the nightly sweep, the three flag rules, the stored value
* `crm/api/today.py` — one whitelisted endpoint aggregating four existing queries
* `crm/patches/v1_0/create_parama_deal_health_field.py` — the `custom_parama_health_flags` JSON custom field on CRM Deal
* `crm/tests/test_deal_health.py`, `crm/tests/test_target_meter.py`, `crm/tests/test_today.py`

**Modified — backend**

* `crm/api/dashboard.py` — `+"target_meter"` in `ALLOWED_CHARTS`, `get_target_meter()`
* `crm/api/whatsapp_followups.py` — deal-health section on the existing daily digest
* `crm/hooks.py` — one `daily` scheduler entry
* `crm/feature_flags.py` — `deal_health_enabled`
* `crm/fcrm/doctype/fcrm_settings/fcrm_settings.json` — `monthly_revenue_target`, `deal_health_enabled`, `deal_health_section`, `deal_health_stalled_days`
* `crm/patches.txt` — one line

**New — frontend**

* `frontend/src/components/Dashboard/TargetMeter.vue`
* `frontend/src/components/DealHealthChip.vue`
* `frontend/src/pages/Today.vue`
* `frontend/src/composables/today.js`
* `frontend/src/utils/targetMeter.js`, `dealHealth.js`, `today.js`
* `frontend/tests/unit/targetMeter.test.js`, `dealHealth.test.js`, `today.test.js`

**Modified — frontend**

* `frontend/src/components/Dashboard/DashboardItem.vue`, `AddChartModal.vue` — the `progress_chart` type
* `frontend/src/components/ListViews/DealsListView.vue` — the health column cell
* `frontend/src/pages/Deals.vue` — the "Needs attention" quick filter and the kanban card branch
* `frontend/src/components/ViewControls.vue` — the filter predicate (see stage3b-notes §1.1)
* `frontend/src/pages/WhatsAppInbox.vue` — the Today reply deep link (see stage3b-notes §1.2)
* `frontend/src/components/Layouts/AppSidebar.vue` — "Today" at the top of Sales, with a count badge
* `frontend/src/router.js` — the `/today` route and the landing-route fallback

Single-owner check: `crm/hooks.py` (+5), `crm/fcrm/doctype/fcrm_settings/fcrm_settings.json`
(+36), `crm/feature_flags.py` (+6), `crm/patches.txt` (+1), `crm/api/dashboard.py`
(+65), `crm/api/whatsapp_followups.py` (+81/-6), `frontend/src/router.js` (+26/-2)
and `frontend/src/components/Layouts/AppSidebar.vue` (+32/-1) carry Stage 3B's
changes ONLY — verified with `git diff` on each file. No file on the
must-NOT-touch list was modified.

## Schema

The custom field had to exist before the tests could run:

```bash
docker exec crm-local-frappe-1 bash -lc \
  'cd ~/frappe-bench && bench --site crm.localhost execute crm.patches.v1_0.create_parama_deal_health_field.execute'

docker exec crm-local-frappe-1 bash -lc 'cd ~/frappe-bench && bench --site crm.localhost mariadb -e "describe \`tabCRM Deal\`"' | grep parama
```

```
custom_parama_email_normalized	varchar(140)	YES		NULL
custom_parama_phone_e164	varchar(140)	YES		NULL
custom_parama_health_flags	longtext	YES		NULL
```

`FCRM Settings` was reloaded so the four new fields exist on the test site:

```
In [4]: OK DocField(5t05njnsk5) DocField(5t0eq6glod)
```

(`deal_health_enabled` and `monthly_revenue_target`, via
`frappe.reload_doc("fcrm", "doctype", "fcrm_settings")`.)

## Backend — every collectable module, run individually

Pushed with the Stage-1A recipe, then one `bench run-tests --module` per module.

```bash
cd /home/kreshnith/CRM
tar -cf - --exclude=__pycache__ --exclude='*.pyc' --exclude='crm/public' crm | \
  docker exec -i crm-local-frappe-1 bash -lc 'cd ~/frappe-bench/apps/crm && tar -xf -'
```

| Module | Result |
| --- | --- |
| `crm.fcrm.doctype.crm_invitation.test_crm_invitation` | `Ran 13 tests in 3.539s` `OK` |
| `crm.fcrm.doctype.crm_product.test_product_item_sync` | `Ran 25 tests in 0.032s` `OK (skipped=18)` |
| `crm.fcrm.doctype.crm_products.test_crm_products` | `Ran 7 tests in 0.001s` `OK (skipped=6)` |
| `crm.integrations.erpnext.test_utils` | `Ran 9 tests in 0.009s` `OK (skipped=2)` |
| `crm.tests.test_ai_client` | `Ran 35 tests in 0.348s` `OK` |
| `crm.tests.test_automation_context` | `Ran 20 tests in 0.323s` `OK` |
| **`crm.tests.test_deal_health`** | `Ran 37 tests in 28.020s` `OK` |
| `crm.tests.test_duplicates` | `Ran 18 tests in 4.378s` `OK` |
| `crm.tests.test_email_compose` | `Ran 18 tests in 0.729s` `OK` |
| `crm.tests.test_exchange_rate` | `Ran 16 tests in 0.557s` `OK` |
| `crm.tests.test_followup_engine` | `Ran 99 tests in 18.752s` `OK` |
| `crm.tests.test_form_auto_response` | `Ran 42 tests in 9.148s` `OK` |
| `crm.tests.test_itinerary` | `Ran 112 tests in 42.994s` `OK` |
| `crm.tests.test_outbound` | `Ran 46 tests in 2.512s` `OK` |
| `crm.tests.test_quote` | `Ran 67 tests in 14.111s` `OK` |
| `crm.tests.test_reminders` | `Ran 40 tests in 6.474s` `OK` |
| `crm.tests.test_search` | `Ran 36 tests in 5.183s` `OK` |
| `crm.tests.test_send_later` | `Ran 58 tests in 9.163s` `OK` |
| `crm.tests.test_sequences` | `Ran 39 tests in 0.125s` `OK` |
| `crm.tests.test_snippets` | `Ran 45 tests in 2.476s` `OK` |
| `crm.tests.test_state_options` | `Ran 7 tests in 0.191s` `OK` |
| `crm.tests.test_suppression` | `Ran 32 tests in 2.103s` `OK` |
| `crm.tests.test_sweeps` | `Ran 31 tests in 48.881s` `OK` |
| `crm.tests.test_tags` | `Ran 36 tests in 5.626s` `OK` |
| **`crm.tests.test_target_meter`** | `Ran 23 tests in 4.527s` `OK` |
| **`crm.tests.test_today`** | `Ran 28 tests in 40.131s` `OK` |
| `crm.tests.test_whatsapp` | `Ran 62 tests in 0.205s` `OK` |
| `crm.tests.test_whatsapp_demo` | `Ran 12 tests in 0.046s` `OK` |

**Total: 1013 tests, 0 failures, 0 errors, 26 skips.** 88 of those tests are new
in Stage 3B.

`crm.tests.test_followup_engine` was at `FAILED (failures=2)` in the Stage-1A
baseline (`Ran 99 tests in 22.050s`) and is `Ran 99 tests in 18.752s` `OK` here.
Stage 3B changed nothing in that module or in the code it covers; some earlier
stage fixed the two failures. Recorded as an observation, not as a claim about
which stage did it.

47 test modules still cannot be collected in this container (they import
`frappe.tests.IntegrationTestCase` / `UnitTestCase`, frappe v16 only, against the
container's frappe v15.117.0). That includes `crm/tests/test_dashboard.py`, which
is why the target-meter tests are a NEW module rather than an addition to it.
Stage 1A open issue 1 stands, unchanged.

## What the new tests actually assert

**`crm.tests.test_target_meter` (23)** — a deal won this month counts; one won
last month does not; the last day of this month still counts (half-open upper
bound); one won next month does not; an open deal never counts; the value is
converted through `deal_value * IfNull(exchange_rate, 1)` like every other value
chart; the dashboard's `from_date`/`to_date` are ignored and the subtitle says so;
`percent` is achieved/target; no target is `hasTarget: false`, not 0%; over target
reports over 100; the chart is allow-listed and dispatched; an unknown name is
still refused. **Authorisation:** a real Sales User sees only their own won value,
cannot widen it by sending `user="Administrator"`, a manager sees the site, and a
non-agent gets `PermissionError`.

**`crm.tests.test_deal_health` (37)** — serialisation (empty column, not `{}`;
display order; unknown flags dropped; junk never raises); the three rules as pure
functions including the closed-deal clear and the configurable stalled window;
and against the database: an overdue deal is flagged, a healthy one gets an empty
column, **running the sweep twice writes nothing the second time**, resolving the
problem clears the chip, winning the deal clears the chip, **a truncated run
stores a cursor and the next one resumes**, **a finished pass clears the cursor**,
a second copy does not run while the lock is held. **Flag off:** zero writes, the
cursor does not move, `sweep_deal_health()` returns 0. **Digest:** the flagged
deal is named, at most three then "and N more", nothing at all when the flag is
off, and the deal title is HTML-escaped.

**`crm.tests.test_today` (28)** — the limit is clamped not trusted; overdue sorts
before merely due; a row with no due time sorts last; the payload is one list plus
counts that agree with it; every row carries exactly one action; no row appears
twice; tasks due today and overdue are listed, next week's and finished ones are
not, an unassigned task belongs to its creator; a flagged deal is listed with its
flags and an unflagged one is not; **the flag being off removes every deal row
even over stale stored values**; a pending draft is listed with an Approve action
and an active follow-up is not. **Authorisation:** a Sales User does not see
another user's task, does see their own, does not see another team's flagged deal,
does see their own, the scope comes from the hierarchy conditions, a non-agent is
refused, and `limit=999999` widens nothing.

## Frontend

```bash
cd /home/kreshnith/CRM/frontend && npx vitest run
```

```
 RUN  v4.1.4 /home/kreshnith/CRM/frontend

 Test Files  19 passed (19)
      Tests  395 passed (395)
   Start at  01:28:12
   Duration  3.68s (transform 981ms, setup 397ms, import 1.84s, tests 557ms, environment 14.05s)
```

19 files against Stage 2B's 16; the three new ones are Stage 3B's
(`targetMeter.test.js` 10, `dealHealth.test.js` 14, `today.test.js` 22 = 46
tests). Stage 2B's note still holds and is worth repeating: **there are no
component tests in this repo**, `@vue/test-utils` is not a dependency, and
nothing under `frontend/tests/unit/` mounts anything. Every decision worth
testing was therefore pushed into a pure function — the bar cap, the "no target"
state, the chip label, the route per row type, the keyboard cursor, the optimistic
row removal — and tested there. The wiring inside the `.vue` files is covered by
the production build, not by a test. That is stated rather than papered over.

### Frontend production build

```bash
cd /home/kreshnith/CRM/frontend
NODE_OPTIONS="--max-old-space-size=6144" npx vite build --base=/assets/crm/frontend/
```

```
✓ built in 1m 18s

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

The globbing warning is the same one Stage 1A recorded and is unrelated. The
build passed on the first attempt.

### Lint

```bash
uvx --from 'ruff==0.8.1' ruff check crm/          # All checks passed!
uvx --from 'ruff==0.8.1' ruff format <the 8 changed backend files>
npx prettier@3.2.5 --write <the 18 changed frontend files>
npx oxlint@1.50.0 <the 15 changed frontend source files>
# Found 0 warnings and 0 errors.  (after fixing two `no-useless-fallback-in-spread`)
```

`ruff format` was run on Stage 3B's files only. After that,
`ruff format --check crm/` still reports `8 files would be reformatted, 355 files
already formatted`. All eight belong to other Stage-3 workers sharing this tree
(`crm/api/email.py`, `crm/api/form.py`, `crm/api/quote.py`,
`crm/document_links.py`,
`crm/fcrm/doctype/crm_form_auto_response_log/crm_form_auto_response_log.py`,
`crm/tests/test_form_auto_response.py`, `crm/tests/test_quote.py`,
`crm/tests/test_send_later.py`) and were left alone. No Stage 3B file is in that
list.

One pre-existing oxlint warning remains in `frontend/src/router.js`
(`no-unused-vars` on the `catch (error)` of the persona "fail open" block). It is
not Stage 3B's line and was not touched.

## Two failures worth recording, because the cause is not obvious

**1. `CONSTRAINT 'tabCRM Deal.custom_parama_health_flags' failed`, MariaDB 4025.**
The first version of the sweep wrote `""` for a healthy deal. Frappe maps the
JSON fieldtype to MariaDB `json`, which is `longtext` **with a
`CHECK (json_valid(col))` constraint** — and the empty string is not valid JSON.
The sweep now writes NULL, which satisfies the check and which `["is", "set"]`
reads identically. **Any later feature that adds a JSON custom field will hit
exactly this.** For the same reason the column carries no index: MariaDB refuses
an index on a TEXT column without a prefix length.

**2. `TypeError: '<' not supported between instances of 'datetime.datetime' and
'str'`.** `frappe.utils.add_to_date` returns whatever TYPE it was given, so
passing a string `now` produced a string cutoff that could not be compared with a
parsed datetime. `evaluate` and `is_awaiting_reply` now normalise `now` with
`frappe.utils.get_datetime` on entry. Production always passed a datetime, so
this was only ever reachable from a test — which is exactly what found it.

Two test premises were also wrong and were corrected rather than worked around:
`CRM Deal.exchange_rate` and `deal_value` are both NOT NULL columns, so the
`IfNull` / `Coalesce` guards in the chart can never fire through a NULL. The tests
now assert what the code actually does (the conversion matches the other value
charts) instead of a NULL case that cannot occur.

## Open issues handed to Stage 4

1. **The digest still has no quiet-hours check and no per-user opt-out.** Master
   spec §5 item 22 asks for both. Neither exists for `send_daily_digest` today,
   and this app has no per-user preference store at all
   (`crm/reminders.py:41-45`). The deal-health section inherits the gap rather
   than inventing a preference store for one line of text. **This is a deviation
   and needs an owner decision.**
2. **`notify_user` dedups on the exact field tuple**, so two digests with
   identical counts on consecutive days collapse into one. Pre-existing.
3. **The "Needs Attention" column is not in any default list layout.** It is a
   real Custom Field so it appears in the column picker and the kanban field
   picker, but a seeded demo has to add it once per view.
4. **Today is a personal task list even for a manager.** CRM Task has no
   row-level rule, so the endpoint scopes tasks to the session user explicitly;
   flagged deals do follow the hierarchy. The asymmetry is deliberate — see
   `stage3b-notes.md` §2.11.
5. **The frappe v15 vs v16 mismatch stands** (Stage 1A open issue 1), and
   `crm/tests/test_dashboard.py` is one of the 47 modules it blocks.

---

# Stage 3A

Scope: master spec §5 items 4 (web-form auto-response), 5 (send later),
19 (email open state), 25 (quote PDF). Branch `feat/feature-expansion`. Nothing
is committed. Deviations, decisions and the `crm/hooks.py` entries this stage may
not make are in `demo-package/specs/stage3a-notes.md`.

A second worker (Stage 3B) had uncommitted Python and Vue in the same tree
throughout. Every count below is from a run of the tree as it stood, so the
Stage 3B suites appear in the table too.

## What changed

### Backend — files added

| File | What it is |
| --- | --- |
| `crm/document_links.py` | The shared customer-document machinery. Render a print format, attach it as a File, sweep temporary public copies — extracted from `crm/api/itinerary.py` — plus the new tokenised link, its view log and the platform-vs-customer fetch rule |
| `crm/api/quote.py` | Item 25. Preview, download, WhatsApp send, and the Guest `view` route |
| `crm/templates/print_formats/travel_quote_a4.html` | The `Travel Quote A4` print format |
| `crm/fcrm/doctype/crm_document_link/` | The token row: purpose, reference, private file, expiry, view counters, payload |
| `crm/fcrm/doctype/crm_document_link_view/` | One row per fetch, with the platform-fetch flag |
| `crm/fcrm/doctype/crm_form_auto_response/` | Item 4's per-form setting, named after the Web Form |
| `crm/fcrm/doctype/crm_form_auto_response_log/` | Item 4's idempotency claim and audit trail |
| `crm/tests/test_quote.py` | 67 tests |
| `crm/tests/test_send_later.py` | 58 tests |
| `crm/tests/test_form_auto_response.py` | 42 tests |

### Backend — files changed

| File | Change |
| --- | --- |
| `crm/api/email.py` | Item 5: timezone helpers, presets, `schedule_email`, `get_scheduled_emails`, `cancel_scheduled_email`, `send_scheduled_email_now`, `email_adapter`, `register_adapters`, `handle_inbound_reply`. `send_email` itself is untouched |
| `crm/api/form.py` | Item 4: the auto-response engine, its configuration round trip and the test send. Plus one defensive read — see stage3a-notes §2f |
| `crm/api/activities.py` | Item 19: the Communication `name`, `read_by_recipient_on`. Items 5 and 25: `scheduled_email` and `quote_view` activities. All additive |
| `crm/api/itinerary.py` | The PDF helpers now delegate to `crm.document_links`. Public names, signatures and behaviour unchanged |
| `crm/outbound.py` | `ADAPTER_REGISTRARS` + `load_adapter_modules()`, called once by `process_scheduled_jobs`. `get_adapter` unchanged |
| `crm/utils/__init__.py` | One call to `handle_inbound_reply` in `on_communication_insert` |
| `crm/fcrm/doctype/crm_lead/crm_lead.py`, `crm_deal.py` | One call to `queue_auto_response` in `after_insert` |
| `crm/fcrm/doctype/crm_notification/crm_notification.json` | One `Select` option: `Email` |

### Frontend — files added

| File | What it is |
| --- | --- |
| `src/utils/emailStatus.js` | Item 19's pure state helper (10 tests) |
| `src/utils/sendLater.js` | Item 5's pure preset/parse/cancel helpers (28 tests) |
| `src/components/editor/mergeField.js` | The TipTap atom node behind item 4's merge pills |
| `src/components/SendLaterPopover.vue` | The split-button popover |
| `src/components/Activities/ScheduledEmailArea.vue` | The timeline card, with inline Send now / Cancel |
| `src/components/Settings/Forms/AutoResponsePanel.vue` | The builder's Auto-response tab |
| `src/components/Modals/QuoteModal.vue` | The quote preview |
| `tests/unit/emailStatus.test.js`, `tests/unit/sendLater.test.js` | 38 tests |

### Frontend — files changed

`src/components/EmailEditor.vue` (split Send button),
`src/components/CommunicationArea.vue` (`scheduleEmail`),
`src/components/Activities/EmailArea.vue` (item 19's indicator replaces the raw
delivery-status badge), `src/components/Activities/Activities.vue` (the two new
activity types), `src/components/Settings/Forms/FormBuilderPanel.vue` (the tab,
the model, load and save), `src/pages/Deal.vue` and `src/pages/MobileDeal.vue`
(the Create quote action).

## Migrations

### New doctypes

| Doctype | Naming | Unique keys | Roles |
| --- | --- | --- | --- |
| `CRM Form Auto Response` | `field:web_form` | the name is the key: one row per form | System Manager (crud+delete), Sales Manager (crud+delete) |
| `CRM Form Auto Response Log` | hash | `submission_key` (web form + record) | System Manager (crud+delete), Sales Manager (crud) |
| `CRM Document Link` | hash | `token` | System Manager (crud+delete), Sales Manager (crud) |
| `CRM Document Link View` | hash | — | System Manager (crud+delete), Sales Manager (crud) |

No Sales User grant on any of the four. A view log names which customers opened
which quotes; the deal timeline surfaces it through
`crm.api.activities`, which has already checked read permission on the deal.

### New fields on existing doctypes

| Doctype | Field | Change |
| --- | --- | --- |
| `CRM Notification` | `type` | one appended `Select` option, `Email` |

No other core schema is touched. No patch is needed: the four doctypes are new,
and a `Select` gaining an option needs none.

### Downgrade behaviour

Remove the app's new modules and: scheduled jobs stay in `CRM Outbound Job` and
are never claimed (the sweep is flag-gated and OFF by default); auto-response
rows stay and nothing reads them; quote links stay `active` until their
`expires_at` passes, and the route that would serve them no longer exists, so
they answer 404 rather than serving a document. Nothing is deleted by a
downgrade, and nothing sends.

## Backend — every module that collects, run individually

The container is `crm-local-frappe-1`, the site `crm.localhost`, and the host
tree is pushed into the container's own clone before each run, exactly as in
Stage 1. `--app crm` still cannot be used: see "How the suites are run".

```bash
cd /home/kreshnith/CRM
tar -cf - --exclude=__pycache__ --exclude='*.pyc' --exclude='crm/public' crm | \
  docker exec -i crm-local-frappe-1 bash -lc 'cd ~/frappe-bench/apps/crm && tar -xf -'

docker exec crm-local-frappe-1 bash -lc 'cd ~/frappe-bench && \
  bench --site crm.localhost run-tests --module crm.tests.<module>'
```

| Module | Result | Verdict |
| --- | --- | --- |
| `crm.tests.test_ai_client` | Ran 35 tests | OK |
| `crm.tests.test_automation_context` | Ran 20 tests | OK |
| `crm.tests.test_dashboard` | — | DID NOT COLLECT |
| `crm.tests.test_deal_health` | Ran 38 tests | OK |
| `crm.tests.test_demo_data` | — | DID NOT COLLECT |
| `crm.tests.test_duplicates` | Ran 18 tests | OK |
| `crm.tests.test_email_compose` | Ran 18 tests | OK |
| `crm.tests.test_exchange_rate` | Ran 16 tests | OK |
| `crm.tests.test_followup_engine` | Ran 99 tests | OK |
| `crm.tests.test_form_api` | — | DID NOT COLLECT |
| `crm.tests.test_form_auto_response` | Ran 42 tests | OK |
| `crm.tests.test_integrations` | — | DID NOT COLLECT |
| `crm.tests.test_itinerary` | Ran 112 tests | OK |
| `crm.tests.test_notification_log` | — | DID NOT COLLECT |
| `crm.tests.test_outbound` | Ran 46 tests | OK |
| `crm.tests.test_quote` | Ran 67 tests | OK |
| `crm.tests.test_reminders` | Ran 40 tests | OK |
| `crm.tests.test_search` | Ran 36 tests | OK |
| `crm.tests.test_send_later` | Ran 58 tests | OK |
| `crm.tests.test_sequences` | Ran 39 tests | OK |
| `crm.tests.test_snippets` | Ran 45 tests | OK |
| `crm.tests.test_state_options` | Ran 7 tests | OK |
| `crm.tests.test_suppression` | Ran 32 tests | OK |
| `crm.tests.test_sweeps` | Ran 31 tests | OK |
| `crm.tests.test_tags` | Ran 36 tests | OK |
| `crm.tests.test_target_meter` | Ran 23 tests | OK |
| `crm.tests.test_today` | Ran 28 tests | OK |
| `crm.tests.test_utils` | — | DID NOT COLLECT |
| `crm.tests.test_whatsapp` | Ran 62 tests | OK |
| `crm.tests.test_whatsapp_demo` | Ran 12 tests | OK |

**960 tests, 24 modules, all OK. 6 modules do not collect** (the frappe v15 vs
v16 mismatch, Stage 1A open issue 1 — unchanged by this stage).

### One transient failure, and why it is not a regression

An earlier pass of the same sweep reported:

```
ERROR: test_the_cursor_round_trips (crm.tests.test_sweeps.TestWatermark.test_the_cursor_round_trips)
pymysql.err.OperationalError: (1213, 'Deadlock found when trying to get lock; try restarting transaction')
```

`crm.tests.test_sweeps` re-run immediately, alone: `Ran 31 tests … OK`, and OK
again in the full sweep above. A second worker was running its own suites against
the same MariaDB at the time. Recorded rather than dropped.

### The three new suites, and what they actually assert

**`crm/tests/test_form_auto_response.py` — 42 tests.** `_make_auto_response` is
the one seam between the decision path and the email queue and is stubbed with a
recorder, so "one submission = exactly one reply" is asserted by COUNTING what
the recorder was handed, not inferred. The acceptance criteria have a test each:
`test_one_submission_produces_exactly_one_reply`,
`test_a_second_run_for_the_same_submission_sends_nothing`,
`test_the_toggle_off_sends_nothing`,
`test_a_suppressed_address_is_never_written_to`. `TestQueueTrigger` proves the
`web_form` name from the POST body is never trusted: a form for another doctype
and a Web Form from another app both queue nothing.

**`crm/tests/test_send_later.py` — 58 tests.** `outbound.commit` / `rollback` are
neutralised as in `test_outbound.py`, and
`test_the_claim_is_committed_before_the_adapter_runs` asserts the ORDERING that
makes the send at-most-once. `test_cancel_after_claim_is_refused` is the
cancellation cutoff. `TestReplyCancels` covers the match, the stamp, the claimed
job that survives a reply, the notification, and that the handler never raises
inside an inbound insert. `TestSweep` proves the sweep registers the adapter and
that the registrar does not overwrite one already bound.

**`crm/tests/test_quote.py` — 67 tests.** The PDF is rendered FOR REAL in
`TestRender` because "the products come out right" is the acceptance criterion.
`TestTokenRoute` proves an unknown, a revoked and an expired token are
indistinguishable. `TestClassifyFetch` and `TestViewLog` cover the bot rule from
both sides. `TestWhatsAppSend::test_the_tokenised_url_is_what_the_platform_is_handed`
and `::test_no_public_file_is_left_behind` are the upgrade the master spec asked
for, asserted rather than described.

### The itinerary suite is the check on the extraction

`crm.tests.test_itinerary`: **112 tests, OK** — the same 112 that passed before
this stage began, on the same tree, unchanged. The tests that patch
`crm.api.itinerary.frappe.get_all` / `.log_error` / `.delete_doc` still exercise
the delegated code, because `frappe` is one module object shared by both files.

## Live checks on the running demo site

Every one of these was run against `crm.localhost` over the real request path,
and every artifact was deleted afterwards.

### Item 4 — the whole pipeline, from a Guest POST

A published CRM web form with the auto-response on, then:

```
$ curl -X POST '…/api/method/frappe.website.doctype.web_form.web_form.accept' \
    --data-urlencode 'web_form=smoke-auto-response' \
    --data-urlencode 'data={"first_name":"Second","email":"second.tester@example.com"}'
HTTP 200
```

```
submission_key                                     status   recipient                   communication
smoke-auto-response:CRM Lead:CRM-LEAD-2026-00025   Sent     second.tester@example.com   tdb67j3k9j

name        subject          recipients                  sender                 communication_type
tdb67j3k9j  Thanks Second    second.tester@example.com   agency@example.invalid Automated Message
body: <p>Hi <span class="merge-field" data-merge-field="first_name">Second</span>, we have your …
queued: 1
```

Guest POST → lead insert → `after_insert` → enqueue after commit → the real
background worker → the claim row → merge → Communication → Email Queue. The
merge pill's `{{ first_name }}` became `Second` and the wrapper span survived, as
designed.

The other three acceptance criteria, same path:

```
smoke-auto-response:CRM Lead:CRM-LEAD-2026-00024  No Email Account  smoke.tester@example.com     no outgoing Email Account is configured
smoke-auto-response:CRM Lead:CRM-LEAD-2026-00026  Suppressed        optedout.tester@example.com  the address is on the suppression ledger
```

and with the toggle off: `HTTP 200`, **no log row at all** and
`select count(*) from tabCommunication where recipients like '%toggledoff%'` → `0`.
The refusal happens before the enqueue, which is the earliest possible point.

### Item 25 — the Guest route, over HTTP

```
$ curl -A 'facebookexternalhit/1.1' '…/api/method/crm.api.quote.view?token=…'
HTTP/1.1 200 OK
Content-Type: application/pdf
Content-Disposition: inline; filename=Quote-CRM-DEAL-2026-00010-v1.pdf
Content-Length: 22292
%PDF-1.4

$ curl -A 'Mozilla/5.0 Chrome/120' '…?token=…'
200 application/pdf 22292

$ curl '…?token=deadbeef'
403 {"exc_type":"PermissionError", … "This link has expired or is no longer valid."}
```

and the log it wrote:

```
name         is_platform_fetch  ua                        ip          viewed_at
sjctkfeihp   1                  facebookexternalhit/1.1   127.0.0.1   2026-08-19 01:50:52.381471
sjgqvp2etu   0                  Mozilla/5.0 Chrome/120    127.0.0.1   2026-08-19 01:50:52.818415

name         view_count  first_viewed_at   platform_fetch_at  active
sh0h92geba   1           …:52.818415       …:52.381471        1
```

Meta's prefetch flagged and not counted; the customer's open counted once.

The PDF itself, rendered from a deal with two products:

```
row: {'name': 'Bali 5N/6D package', 'rate': '₹ 65,000.00', 'qty': '2', 'discount': '10%', 'net_amount': '₹ 1,17,000.00'}
row: {'name': 'Airport transfers',  'rate': '₹ 4,000.00',  'qty': '1', 'discount': '—',   'net_amount': '₹ 4,000.00'}
totals: {'total': '₹ 1,34,000.00', 'discount': '₹ 13,000.00', 'net_total': '₹ 1,21,000.00'}
pdf bytes: 22284
HAS ROWS: True   HAS TERMS: True   HAS PLACEHOLDER: True
```

The deal's own stored `total` was `None` — see stage3a-notes §2e.

### Item 5 — schedule, list, timeline, cancel

```
LEAD CRM-LEAD-2026-00023
JOB  u5t9jvu8t7  Scheduled  2026-08-20 09:00:00  Asia/Calcutta
LISTED ['u5t9jvu8t7']
TIMELINE [('scheduled_email', 'Live check', True)]
AFTER CANCEL Cancelled
LISTED AFTER []
```

The `tomorrow_morning` preset resolved on the server to 09:00 in the site's
timezone, which was stored on the job.

### Item 19 — the payload the indicator reads

```
TOP-LEVEL name: uan7iijm98
data.name: uan7iijm98
read_by_recipient: 1
read_by_recipient_on: 2026-08-19 01:53:49.492825
delivery_status: Sent
```

Stage 2B open issue 3 (`activities.py` does not return the Communication `name`)
is closed.

## Frontend

```bash
cd /home/kreshnith/CRM/frontend && npx vitest run
```

```
 Test Files  21 passed (21)
      Tests  433 passed (433)
   Duration  3.96s
```

433 includes the Stage 3B suites in the same tree. The 38 tests this stage added
are `tests/unit/emailStatus.test.js` (10) and `tests/unit/sendLater.test.js` (28).

### Frontend production build

```bash
cd /home/kreshnith/CRM/frontend && \
  NODE_OPTIONS="--max-old-space-size=6144" npx vite build --base=/assets/crm/frontend/
```

```
✓ built in 1m 16s
```

The one warning, `An error occurred when globbing for files. '(0 , brace_expansion_1.expand) is not a function'`,
comes from the PWA plugin and is pre-existing (recorded in Stage 2A).

## Lint

```bash
uvx --from 'ruff==0.8.1' ruff check <the 17 Python files this stage touched>
# All checks passed!
uvx --from 'ruff==0.8.1' ruff format <the 15 non-controller Python files>
# 8 files reformatted, 7 files left unchanged

npx prettier@3.2.5 --write <the 16 frontend files this stage touched>
npx oxlint@1.50.0 <the 14 frontend source files>
# Found 0 warnings and 0 errors.
# Finished in 17ms on 14 files with 93 rules using 8 threads.
```

Ruff was pointed at this stage's files rather than at `crm/`, for the reason
Stage 2A recorded: a second worker had uncommitted Python in the same tree, and
`ruff format crm/` would have rewritten their files mid-edit. The backend suites
above were re-run after the ruff pass and are the counts reported.

## Open issues handed on

The full list, with the `crm/hooks.py` diffs another worker must apply, is in
`demo-package/specs/stage3a-notes.md` §1 and §5. The two that matter most:

1. **`crm.api.quote.cleanup_quote_links` is not on the hourly schedule.** Until
   it is, a quote link never expires and its private PDF is never deleted. The
   exact diff is in stage3a-notes §1a.
2. **`outbound_engine_enabled` must be switched on at deploy** or a scheduled
   email is never delivered. Scheduling, listing, cancelling and Send-now all
   work with the flag off; only the hourly sweep is gated.
