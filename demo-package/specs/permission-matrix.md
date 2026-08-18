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

---

## Stage 2A — Tags, duplicate warning, Cmd+K palette, recents

Items 2, 3, 10 and 11 of the master spec §5. Seven whitelisted endpoints, in
three new modules. Every one of them derives its row scope server-side; none of
them accepts a filter, a field list or an order from the request.

| Endpoint | Roles | Scope derivation | Record checks | Tested in |
| --- | --- | --- | --- | --- |
| `crm.api.tags.get_tags` (GET/POST) | Any signed-in user. `Guest` is rejected by `@frappe.whitelist()` itself | `frappe.has_permission(doctype, "read", doc=name)`, which runs the controller `has_permission` hooks — so `crm.permissions.org_hierarchy.has_lead_permission` / `has_deal_permission` decide. The `doctype` argument is checked against the fixed allowlist `("CRM Lead", "CRM Deal")` BEFORE it reaches a query | `CRM Lead` / `CRM Deal`: read on the named record. A record that does not exist and a record the caller may not see raise the same `PermissionError`, so the endpoint is not an existence oracle | `crm/tests/test_tags.py::TestPermissions::test_a_sales_user_without_lead_access_cannot_read_its_tags`, `::TestAllowlist::test_a_missing_record_is_refused_like_a_forbidden_one` |
| `crm.api.tags.add_tag` (**POST only**) | Any signed-in user | Same allowlist, same check with `write` | `CRM Lead` / `CRM Deal`: write on the named record, checked BEFORE `DocTags.add` writes anything. Also creates/reads `Tag` and `Tag Link` through the framework writer | `crm/tests/test_tags.py::TestPermissions::test_a_sales_user_without_lead_access_cannot_add_a_tag`, `::test_the_refused_write_did_not_happen`, `::test_state_changing_endpoints_are_post_only` |
| `crm.api.tags.remove_tag` (**POST only**) | Any signed-in user | Same | Same | `crm/tests/test_tags.py::TestPermissions::test_a_sales_user_without_lead_access_cannot_remove_a_tag`, `::test_state_changing_endpoints_are_post_only` |
| `crm.api.tags.search_tags` (GET/POST) | Any signed-in user who may READ the doctype being tagged | Doctype-level `frappe.has_permission(doctype, "read")` plus the allowlist. Returns `Tag` master names only — a site-wide vocabulary with no customer data and no link to a record | `Tag`: read via `frappe.get_all` after the gate above. No record is named in the result | `crm/tests/test_tags.py::TestSearchTags`, `::TestAllowlist::test_search_refuses_an_unlisted_doctype` |
| `crm.api.duplicates.check_duplicates` (GET/POST) | Any signed-in user | `frappe.get_list` per target doctype, which applies the registered `permission_query_conditions` in SQL. The caller supplies an email and a phone number ONLY; the search set is a fixed map from the allowlisted `doctype` argument (`CRM Lead → CRM Lead, Contact`; `CRM Deal → CRM Deal, Contact`; `Contact → Contact, CRM Lead`) | `CRM Lead` / `CRM Deal`: `crm.permissions.org_hierarchy` conditions in the query. `Contact`: role permissions, no row-level rule exists in this app. A doctype the caller cannot read is skipped, not raised | `crm/tests/test_duplicates.py::TestPermissions::test_a_sales_user_does_not_see_another_teams_lead`, `::test_a_sales_user_does_not_see_another_teams_lead_by_phone_either`, `::test_a_sales_user_does_see_their_own_lead`, `::test_the_scope_comes_from_the_hierarchy_conditions` |
| `crm.api.search.palette_search` (GET/POST) | Any signed-in user | One `frappe.get_list` per group, conditions in SQL. The request carries a search string and a page size; `limit` is clamped to `MAX_LIMIT`, and there is no filter, field, doctype or order argument at all | `CRM Lead` / `CRM Deal`: hierarchy conditions. `Contact`, `CRM Organization`, `CRM Task`, `FCRM Note`: role permissions (see the note below). A group the caller cannot read is skipped | `crm/tests/test_search.py::TestPermissions::test_a_sales_user_does_not_see_another_teams_lead`, `::test_a_sales_user_does_not_reach_it_by_email_either`, `::test_a_sales_user_does_see_their_own_lead` |
| `crm.api.search.resolve_records` (GET/POST) | Any signed-in user | The caller supplies `[{doctype, name}]` from their own localStorage; the doctype is checked against the same fixed group list and the names go into a `name in (...)` filter on a permission-aware `get_list`. Anything the caller may not read is dropped silently | Same per-doctype checks as `palette_search` | `crm/tests/test_search.py::TestPermissions::test_a_recent_the_user_lost_access_to_is_dropped`, `::test_a_recent_the_user_still_owns_is_kept` |

### What "row-level scope" means per doctype, stated plainly

`crm/hooks.py` registers `permission_query_conditions` for `CRM Lead` and
`CRM Deal` only. `Contact`, `CRM Organization`, `CRM Task` and `FCRM Note` have
no row-level rule anywhere in this app: a Sales User's Tasks page already lists
every task on the site. The palette and the duplicate warning therefore show
exactly what those list pages already show, and add no new visibility. Narrowing
them is a change to the app's permission model, not to these endpoints, and it
was not made here. **This is a known gap, not an oversight** — it is on the
Stage 6 correctness/security reviewer's list.

### Deliberate consequences

* A Sales User about to re-enter a lead that belongs to another team sees NO
  duplicate warning. The alternative leaks the existence, the name and the owner
  of another team's customer to anybody who can guess an email address. The
  warning is worth less than the leak would cost.
* `crm.api.tags.*` deliberately does NOT wrap
  `frappe.desk.doctype.tag.tag.add_tag`, which is whitelisted upstream and
  reachable on any site. That endpoint writes `_user_tags` with
  `frappe.db.set_value` and only then reaches `doc.check_permission("write")`
  inside `update_tags`; the write is rolled back with the request, but the order
  is wrong and it accepts a tag containing a comma, which is the separator the
  column is joined on. Ours checks first and validates the tag. **The upstream
  endpoint is still reachable** — this stage adds a safe door, it does not close
  the unsafe one. Closing it means overriding a framework whitelist, which is a
  separate decision.

### Non-endpoint entry points added in Stage 2A

| Entry point | Reached from | Authorization today | What a future endpoint must add |
| --- | --- | --- | --- |
| `crm.api.tags.clean_tag` / `split_tags` / `read_tags` | The four endpoints above | None of their own. `read_tags` reads the column with no check and is only ever called after `_guard` | Any new caller must call `_guard` first — `read_tags` is not a front door |
| `crm.api.duplicates.title_of` / `_match` | `check_duplicates` | `_match` runs the read check and the query conditions itself; `title_of` is pure | n/a |
| `crm.api.search.search_group` / `as_result` / `build_or_filters` | `palette_search`, `resolve_records` | `search_group` runs the read check and `get_list`; the other two are pure | A caller that builds its own query must not reuse `build_or_filters` without `get_list` |

---

## Stage 2B — Task reminders, email forward, snippets

Three endpoints are added and one existing client call is moved off a framework
endpoint onto a CRM wrapper so it passes the suppression ledger.

| Endpoint | Roles | Scope derivation | Record checks | Tested in |
| --- | --- | --- | --- | --- |
| `crm.api.snippets.get_snippets` (GET/POST) | Any signed-in user with a CRM role | `frappe.get_list`, which applies `get_snippet_permission_query_conditions`: own rows plus `shared = 1`, everything for a manager. The caller's `search` is a text filter ON TOP of that scope and can only narrow it | `CRM Snippet`: the permission query condition above, plus the doctype's role permissions | `crm/tests/test_snippets.py::TestVisibility` (`test_another_users_private_snippet_is_invisible`, `test_search_narrows_and_cannot_widen`, `test_the_query_condition_scopes_a_plain_get_list`) |
| `crm.api.snippets.render` (GET/POST) | Any signed-in user with a CRM role | Two independent checks. The snippet: `frappe.get_doc(...).check_permission("read")`, which runs `has_snippet_permission`. The record named in the call: `frappe.get_doc(doctype, docname).check_permission("read")`, which runs the org-hierarchy `has_permission` hook for CRM Lead and CRM Deal. The doctype must also be in `MERGEABLE_DOCTYPES` | `CRM Snippet`: read. `CRM Lead` / `CRM Deal` / `Contact` / `CRM Organization`: read, on the named record | `crm/tests/test_snippets.py::TestRenderPermissions` (all three tests) |
| `crm.api.email.send_email` (**POST only**) | Any signed-in user | Derived SERVER-side by `frappe.core.doctype.communication.email.make`, which does `frappe.has_permission(doctype, doc=name, ptype="email", throw=True)` on the reference record. The wrapper adds a suppression filter in front and changes nothing about who may send | `CRM Lead` / `CRM Deal` (whatever the composer is open on): `email` permission, by `make`. `CRM Suppression`: read, server-side, no result returned to the caller. `File`: the docnames are passed to `make`, which copies them onto the new Communication | `crm/tests/test_email_compose.py::TestAuthorization`, `::TestSendEmail::test_the_reference_record_is_handed_to_make` |

### Why the merge is a token substitution and not Jinja

`crm.api.snippets.render` does NOT use `frappe.render_template`, although the
Email Template path it otherwise mirrors does. An Email Template is written by
an administrator; a snippet is written by a Sales User. Handing user-authored
Jinja to the server-side renderer would let any agent read the site's Jinja
context. The merge is therefore a flat `{{ token }}` substitution over a fixed
grammar, resolved from a record the caller has already been checked against,
with every value HTML-escaped on the way in
(`crm/tests/test_snippets.py::TestMerge::test_jinja_is_not_evaluated`,
`::test_a_value_is_html_escaped`).

### Doctype role permissions added in Stage 2B

| Doctype | System Manager | Sales Manager | Sales User |
| --- | --- | --- | --- |
| `CRM Snippet` | create, read, write, delete | create, read, write, delete | create, read, write, delete |

The table grants look flat because the row-level rules do the work:

* `get_snippet_permission_query_conditions` limits every LIST read to own rows
  plus shared rows (managers: everything).
* `has_snippet_permission` limits every DOCUMENT read to own or shared, and
  every write or delete to own AND not shared (managers: everything).
* `CRMSnippet.check_shared_is_a_manager_decision` guards the `shared`
  TRANSITION in both directions, so a Sales User can neither publish to the
  shared library nor withdraw from it.

### Non-endpoint entry points added in Stage 2B

| Entry point | Reached from | Authorization today | What a future endpoint must add |
| --- | --- | --- | --- |
| `crm.reminders.send_task_reminders` | Frappe scheduler, ONE cron entry (`*/15 * * * *`) | Flag `task_reminders_enabled`, default OFF. Never raises | n/a — scheduler only. A manual "remind now" endpoint would need write permission on the task and must still go through `claim` |
| `crm.reminders.remind_about` / `claim` / `close` | `send_task_reminders` | Reads tasks with `frappe.get_all` and no permission conditions. Correct only because no result reaches a request: the single thing done with a task is to notify the user it is already assigned to. The recipient is re-checked AT EXECUTION TIME (`recipient_is_reachable` refuses a disabled user) | Any endpoint over this data must go through `frappe.get_list` or apply the org-hierarchy conditions itself |
| `crm.reminders.deliver_email` | `remind_about`, when `task_reminder_email` is on | Asks `crm.suppression.is_suppressed` for the recipient's address before it queues anything | n/a |
| `crm.api.email.split_addresses` / `drop_suppressed` | `send_email` | Pure over the caller's own recipient list. Returns no record the caller did not supply | Nothing |
| `crm.api.snippets.merge` / `token_values` | `render` | `merge` is pure. `token_values` runs the record's own `check_permission("read")` and refuses a doctype outside `MERGEABLE_DOCTYPES` | A caller that resolves tokens from a record it did not check is a permission bypass |
| `crm.api.snippets.is_snippet_manager` | The permission hooks and the doctype's `validate` | Reads `frappe.get_roles` | n/a |

### One behaviour change to an existing path

`frontend/src/components/CommunicationArea.vue::sendMail` called
`frappe.core.doctype.communication.email.make` directly and therefore bypassed
the Stage-1A consent ledger entirely. It now calls `crm.api.email.send_email`,
which asks the ledger and then calls the same `make` with the same arguments.
For a recipient nobody opted out of, nothing about the send changes — same
permission check, same Communication, same Email Queue row. For a recipient who
did opt out, the address is removed and named back to the composer; when every
recipient opted out, nothing is sent and the agent is told.
