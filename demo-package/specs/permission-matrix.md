# PARAMA-CRM permission matrix

Created in Stage 1 as required by the master spec §3. **Every worker that adds a
whitelisted endpoint MUST add a row here in the same change.** A row that is not
filled in is a review blocker, not a to-do.

## The rule this file records

For every new whitelisted endpoint, state:

1. **Who may call it** — the roles, and whether an unauthenticated caller can
   reach it at all.
2. **How row-level scope is derived SERVER-side** — never from a client-supplied
   filter. Use `frappe.get_list` (permission-aware) or apply the org-hierarchy
   conditions from `crm/permissions/org_hierarchy.py` explicitly. Results from
   raw `frappe.db.sql` or the query builder must be put through the same
   conditions before they are returned.
3. **Which permission check covers each referenced record** — one line per
   doctype the endpoint reads or writes.
4. **Background jobs re-check permissions at EXECUTION time, not enqueue time.**

## How to fill a row

| Column | What goes in it |
| --- | --- |
| Endpoint | Dotted path, e.g. `crm.api.foo.bar`, plus the allowed HTTP methods |
| Roles | Exact role names, or `Guest` where the route is public |
| Scope derivation | The server-side mechanism, named. "Filtered by the caller's filters" is never an acceptable answer |
| Record checks | One entry per referenced doctype and the check applied |
| Tested in | The test file and test name that asserts the above |

---

## Stage 1A — Foundations

**Stage 1A adds NO whitelisted endpoint.** That is deliberate: the foundations
are libraries and schema, and every one of them is reached from server-side
callers, document hooks or the scheduler. Nothing new is callable from a
browser.

| Endpoint | Roles | Scope derivation | Record checks | Tested in |
| --- | --- | --- | --- | --- |
| _(none added in Stage 1A)_ | — | — | — | — |

### Non-endpoint entry points added in Stage 1A

These are not whitelisted and cannot be called from a client. They are listed so
a later stage that wants to expose one knows what it must add first.

| Entry point | Reached from | Authorization today | What a future endpoint must add |
| --- | --- | --- | --- |
| `crm.suppression.is_suppressed` / `filter_suppressed` | Server-side send paths | None needed — a read of the caller's own recipient list, no user data returned | Nothing; it returns no record the caller did not supply |
| `crm.suppression.suppress` | `crm.api.followup_engine` opt-out paths, future bounce handlers | Writes with `ignore_permissions=True`. A scheduler or webhook worker recording a customer's opt-out has no session user to check | A whitelisted wrapper needs a Sales Manager check plus a reason, mirroring `reopen_optout` |
| `crm.suppression.unsuppress` | `crm.api.followup_engine.reopen_optout` (already whitelisted, already manager-checked via `check_followup_permission`) | Inherits the caller's check. Requires a reason and writes an audit Comment | A direct endpoint needs its own manager check — do not rely on the follow-up engine's |
| `crm.outbound.process_scheduled_jobs` | Frappe scheduler, hourly | Flag `outbound_engine_enabled`, default OFF | n/a — scheduler only |
| `crm.outbound.execute_job` | Background worker via `enqueue_job_execution` | Re-reads `owner_user` and refuses a disabled user AT EXECUTION TIME (`sender_is_active`) | Row-level scope on the job's `reference_doctype`/`reference_name`, checked as `owner_user`, before any adapter is called |
| `crm.outbound.create_job` / `schedule_job` / `cancel_job` | Server-side callers in later stages | None — the caller owns the check | The endpoint that offers Send Later must check write permission on the referenced record and set `owner_user` from `frappe.session.user`, never from the request |
| `crm.contact_keys.set_contact_keys` | `validate` doc hook on CRM Lead, CRM Deal, Contact | Runs inside the document's own permission check | n/a |
| `crm.sweeps.run_sweep` | Patches and future nightly jobs | System job. Reads with the query builder and no permission conditions — correct only because no result reaches a request | Any endpoint over this data must go through `frappe.get_list` or apply the org-hierarchy conditions itself |

### Doctype role permissions added in Stage 1A

Desk-level access to the new tables. None of these doctypes is exposed in the
CRM frontend in this stage.

| Doctype | System Manager | Sales Manager | Sales User |
| --- | --- | --- | --- |
| `CRM Suppression` | create, read, write, delete | create, read, write | read |
| `CRM Outbound Job` | create, read, write, delete | create, read, write | — |
| `CRM Outbound Recipient` | create, read, write, delete | create, read, write | — |
| `CRM Reminder Log` | create, read, write, delete | create, read, write | — |

`CRM Suppression` is readable by a Sales User on purpose: an agent about to
write to a customer must be able to see that the customer opted out. Writing it
stays with managers.

---

## Stage 1B — Sequence core, automation context, AI client hardening

**Stage 1B adds NO whitelisted endpoint and NO doctype.** It extracts the
follow-up engine's machine into `crm/sequences/`, adds two libraries with no
consumer yet (`crm/automation_context.py`, `crm/counters.py`), hardens
`crm/ai/client.py`, and wires one scheduler entry.

| Endpoint | Roles | Scope derivation | Record checks | Tested in |
| --- | --- | --- | --- | --- |
| _(none added in Stage 1B)_ | — | — | — | — |

The follow-up engine's four existing endpoints (`approve_pending`,
`dismiss_pending`, `reopen_optout`, `get_template_options`) are unchanged, keep
their POST-only whitelisting and keep `check_followup_permission`, which
delegates to `crm/permissions/org_hierarchy.py`. Their tests are unchanged too:
`crm/tests/test_followup_engine.py::TestPermissions` and
`::TestDraftApproval::test_state_changing_endpoints_are_post_only`.

### Non-endpoint entry points added in Stage 1B

| Entry point | Reached from | Authorization today | What a future endpoint must add |
| --- | --- | --- | --- |
| `crm.sequences.core.*` | `crm.api.followup_engine` only, through an adapter | None of its own. It reads no doctype and holds no permission logic; the adapter it is handed does every read and write | An adapter is a server-side object. It must never be built from request data |
| `crm.outbound.sweep_delivery_states` | Frappe scheduler, hourly | Flag `outbound_engine_enabled`, default OFF. Never raises | n/a — scheduler only |
| `crm.counters.reserve_daily_slot` | `crm.automation_context`, future workflow rules | None. The caller owns the check; the field names are validated against the doctype's own meta before they reach SQL | The rule that spends the cap must already have been permission-checked by its own execution path |
| `crm.automation_context.*` | Nothing yet (Stage 5 workflow rules) | None | A rule action runs as a background job and must re-check permissions AT EXECUTION TIME, as `crm.outbound.execute_job` does |

### What leaves the site on an AI call (spec F6, per call site)

Required by F6: "per-feature documentation of exactly which record fields leave
the site". The same table is in the `crm/ai/client.py` module docstring, next to
the code that sends it. **A call site that is not listed here is a review
blocker.** Every call is BYO key, to the provider the agency configured in
`CRM AI Settings`, and only when that Single is enabled.

| Call site | Feature | Fields that leave the site | What deliberately does NOT |
| --- | --- | --- | --- |
| `crm.api.followup_engine.ai_params` | Fills WhatsApp template variables for one follow-up stage | The lead's `lead_name`, `first_name`, `destination`, `travel_start_date`, `travel_end_date`, `group_size`, `budget` (the `AI_LEAD_FIELDS` tuple); the approved template's name; up to `CONVERSATION_HISTORY_LIMIT` (10) messages of that WhatsApp conversation, HTML-stripped and cut to 200 characters each | The customer's phone number, email address, lead owner, notes, deal values, and every other field on the lead |
| `crm.api.itinerary.ask_model` (`skeleton_prompt`, `day_prompt`) | Drafts itinerary days | The itinerary's `destination`, `start_date`, `num_days`, `group_size`, `budget`, `currency`, and the day titles and summaries already on the itinerary | Any customer name, phone, email, lead or deal reference. The itinerary's linked lead is never named |

Two properties hold at both call sites and are asserted in code, not by
convention: the API key travels in a request header and never in a URL or a log
line, and the answer is validated against the schema that was asked for
(`crm/ai/schema.py`) before any of it is written to a record.
