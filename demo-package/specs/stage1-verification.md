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
