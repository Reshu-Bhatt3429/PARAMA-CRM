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

---

## Stage 3B — Target meter, deal-health flags, Today

Items 7, 22 and 24 of the master spec §5. **One** new whitelisted endpoint
(`crm.api.today.get_today`). Item 7 adds no endpoint at all — it registers a
chart behind the existing, unchanged `crm.api.dashboard.get_chart` /
`get_dashboard`. Item 22 adds no endpoint either: it is a scheduler sweep, a
stored column and a chip.

| Endpoint | Roles | Scope derivation | Record checks | Tested in |
| --- | --- | --- | --- | --- |
| `crm.api.today.get_today` (GET/POST) | Any agent — `crm.utils.sales_user_only`, i.e. Administrator, `Sales Manager` or `Sales User`. `Guest` is refused with `PermissionError` | Four sources, each scoped server-side. The ONLY argument is `limit`, clamped to `[1, 100]` by `clamp_limit`; it is a page size and never reaches a filter. **Tasks:** `frappe.get_list("CRM Task")` for the doctype check PLUS an explicit `assigned_to == frappe.session.user`, and a second query for `assigned_to is not set AND owner == frappe.session.user`. CRM Task has NO row-level rule in this app, so that filter IS the boundary. **Deals:** `frappe.get_list("CRM Deal")`, which puts `crm.permissions.org_hierarchy.get_deal_permission_query_conditions` into the SQL. **Replies:** delegated whole to `crm.api.whatsapp.get_whatsapp_conversations(scope="mine")`, which runs `validate_access()` and drops every conversation whose reference fails its own `frappe.get_list`. **Approvals:** `frappe.get_list("CRM WhatsApp Followup")`, scoped by `crm.api.followup_engine.get_followup_permission_query_conditions` | `CRM Task`: doctype read, then the explicit owner/assignee filter. `CRM Deal`: hierarchy conditions in the query. `CRM Lead`: hierarchy conditions, both for the reply references and for the display names behind a draft (`lead_names`). `CRM WhatsApp Followup`: its registered query conditions, which resolve to the leads the caller may see. `WhatsApp Message`: never read directly; only through the inbox function above | `crm/tests/test_today.py::TestPermissions::test_a_sales_user_does_not_see_another_users_task`, `::test_a_sales_user_does_see_their_own_task`, `::test_a_sales_user_does_not_see_another_teams_flagged_deal`, `::test_a_sales_user_does_see_their_own_flagged_deal`, `::test_the_deal_scope_comes_from_the_hierarchy_conditions`, `::test_a_non_agent_is_refused`, `::test_the_limit_argument_cannot_widen_anything` |

`get_today` writes nothing. The one action a Today row can fire that changes
state — Approve — calls the EXISTING
`crm.api.followup_engine.approve_pending`, which locks the row, re-checks the
state, and runs `check_followup_permission` (delegating to
`crm.permissions.org_hierarchy.has_lead_permission`). No new write path was
added by this stage.

### Item 7 — the existing dashboard endpoint, unchanged

| Endpoint | Roles | Scope derivation | Record checks | Tested in |
| --- | --- | --- | --- | --- |
| `crm.api.dashboard.get_chart` with `name="target_meter"` (GET/POST) | Any agent — `@sales_user_only`, unchanged | Unchanged and NOT re-implemented: `get_chart` / `get_dashboard` overwrite `user` with `frappe.session.user` for a plain Sales User BEFORE dispatching, so the chart's `deal_owner` filter is server-derived. `name` is dispatched only through the `ALLOWED_CHARTS` allowlist, to which `"target_meter"` was added. The chart function itself is not whitelisted | `CRM Deal` joined to `CRM Deal Status`, filtered by `deal_owner` for a Sales User and unfiltered for a manager — the same scope every other dashboard chart has. No row-level hierarchy conditions are applied, because the dashboard has never applied them; this chart adds no visibility a manager did not already have from `get_won_deals` | `crm/tests/test_target_meter.py::TestPermissions::test_a_sales_user_sees_only_their_own_won_value`, `::test_a_sales_user_cannot_widen_the_scope_from_the_request`, `::test_a_manager_sees_the_whole_site`, `::test_a_non_agent_is_refused`, `::TestRegistration::test_an_unknown_chart_name_is_still_refused` |
| `FCRM Settings.monthly_revenue_target` | Read by the chart with `frappe.db.get_single_value`; written only through the Settings form, which is `System Manager` gated by the doctype's own permissions | n/a — a site-wide scalar, not a record | `FCRM Settings`: Single, existing permissions | `crm/tests/test_target_meter.py::TestTarget::test_the_setting_exists`, `::test_no_target_is_not_zero_percent` |

### Non-endpoint entry points added in Stage 3B

These are not whitelisted and cannot be called from a client.

| Entry point | Reached from | Authorization today | What a future endpoint must add |
| --- | --- | --- | --- |
| `crm.deal_health.sweep_deal_health` | Frappe scheduler, ONE `daily` entry | Flag `deal_health_enabled`, default OFF. Also refuses to run when `frappe.db.has_column` says the patch has not run. Never raises | n/a — scheduler only. A manual "recompute now" endpoint would need a Sales Manager check and must still take the sweep lock |
| `crm.deal_health.run_sweep` / `apply_batch` / `build_context` | `sweep_deal_health`, and tests | System job. Reads CRM Deal, CRM Deal Status, CRM Status Change Log, Communication and WhatsApp Message with the query builder and NO permission conditions. Correct only because no result reaches a request: the single thing done with a row is to write a derived value back onto the same row | Any endpoint over this data must go through `frappe.get_list` or apply the org-hierarchy conditions itself. `crm.api.today.flagged_deals` is the worked example |
| `crm.deal_health.flagged_deals` | `crm.api.whatsapp_followups.get_flagged_deals`, for the manager digest | **Deliberately NOT permission-scoped.** It rides the existing daily digest, which already reports site-wide WhatsApp counts to every manager, and it returns no record to a request — the rows are counted and at most three titles are named in one CRM Notification | A whitelisted caller must not use this function. Use `crm.api.today.flagged_deals`, which is the `frappe.get_list` version |
| `crm.deal_health.evaluate` / `is_awaiting_reply` / `serialise` / `parse` / `flag_label` | `apply_batch`, `crm.api.today`, the digest | Pure over the row and the batch context they are handed. No query, no session | Nothing |
| `crm.api.whatsapp_followups.get_flagged_deals` | `send_daily_digest` | Flag `deal_health_enabled`, default OFF. Wrapped in `try/except` + `frappe.log_error`, so a deal-health failure cannot cost a manager their WhatsApp summary | n/a — scheduler only |
| `crm.api.whatsapp_followups.digest_deal_health_line` / `empty_digest_summary` | `create_digest_notification`, `send_daily_digest` | Pure. The line it builds is put through `frappe.utils.escape_html` by `create_digest_notification` before it reaches the notification HTML, because a deal title is customer data | Nothing |
| `crm.api.today.due_tasks` / `flagged_deals` / `awaiting_reply` / `pending_approvals` / `lead_names` | `get_today` | Each applies the scope described in the endpoint row above. `awaiting_reply` swallows every exception, so a site without `frappe_whatsapp` or a user without the inbox roles gets an empty section rather than a 500 | Nothing — they already hold the checks |

### One behaviour change to an existing path

`crm.api.whatsapp_followups.create_digest_notification` now escapes its message
before interpolating it into `notification_text`. The message previously carried
only integers; the deal-health section carries deal titles, which are customer
data. Nothing else about the digest changes when `deal_health_enabled` is off:
same recipients, same counts, same single CRM Notification.
`crm/tests/test_deal_health.py::TestDigest::test_the_notification_body_escapes_the_deal_title`.

---

## Stage 3A — web-form auto-response, send later, email open state, quote PDF

Master spec §5 items 4, 5, 19 and 25. Every row below is asserted by the named
test, not merely described.

| Endpoint | Roles | Scope derivation | Record checks | Tested in |
| --- | --- | --- | --- | --- |
| `crm.api.email.schedule_email` (**POST only**) | Any signed-in user. `Guest` is rejected by `@frappe.whitelist()` itself | `frappe.has_permission(doctype, doc=name, ptype="email", throw=True)` — the SAME check `frappe.core.doctype.communication.email.make` performs, run BEFORE any durable row exists — so the org-hierarchy `has_lead_permission` / `has_deal_permission` hooks decide. `doctype` is checked against the fixed allowlist `("CRM Lead", "CRM Deal")` first. `owner_user` is taken from `frappe.session.user`; the function has no argument for it | `CRM Lead` / `CRM Deal`: `email` on the named record. `CRM Outbound Job` / `CRM Outbound Recipient` are written with `ignore_permissions=True` by `crm.outbound`, after the check above | `crm/tests/test_send_later.py::TestPermissions::test_a_sales_user_without_lead_access_cannot_schedule`, `::test_the_owner_of_the_lead_can_schedule`, `::test_state_changing_endpoints_are_post_only`, `TestScheduling::test_the_owner_is_the_session_user_not_the_request`, `::test_a_doctype_outside_the_allowlist_is_refused` |
| `crm.api.email.get_scheduled_emails` (GET/POST) | Any signed-in user | `frappe.has_permission(doctype, "read", name)` plus the same fixed doctype allowlist. The reply is scoped to that ONE record: `pending_jobs` filters on `reference_doctype` + `reference_name` and nothing else | `CRM Lead` / `CRM Deal`: read on the named record. `CRM Outbound Job` is then read with `frappe.get_all` (no permission conditions), which is safe only because the record check above already ran and the query cannot reach another record | `crm/tests/test_send_later.py::TestPermissions::test_a_sales_user_without_lead_access_cannot_list`, `TestScheduling::test_a_pending_job_is_listed_on_the_record` |
| `crm.api.email.cancel_scheduled_email` (**POST only**) | The job's `owner_user`, or `Sales Manager` / `System Manager` | Two checks, both server-side. (1) `job.owner_user == frappe.session.user` or the caller holds a manager role. (2) `frappe.has_permission(job.reference_doctype, doc=job.reference_name, ptype="email", throw=True)` — read from the JOB ROW, never from the request, so an agent who lost access to a deal cannot still act on a job they left behind. The state change takes the job's row lock (`SELECT … FOR UPDATE`) through `crm.outbound.transition_job` | `CRM Outbound Job`: the job must exist AND have `job_type == "Send Later"`, so a mass-email or sequence job is not reachable here. `CRM Lead` / `CRM Deal`: `email` on the referenced record | `crm/tests/test_send_later.py::TestPermissions::test_somebody_elses_job_is_not_cancellable`, `::test_a_manager_may_cancel_somebody_elses_job`, `TestCancel::test_a_job_of_another_type_is_not_reachable_through_this_endpoint`, `::test_cancel_after_claim_is_refused` |
| `crm.api.email.send_scheduled_email_now` (**POST only**) | Same as cancel | Same two checks (`load_job_for_action`). Then `claim_job` takes the row lock and commits, so a click and a sweep that land together serialise and only one sends | Same. The delivery itself runs as `job.owner_user` (`frappe.set_user` in `email_adapter`), and `crm.api.email.send_email` re-checks the `email` permission and the suppression ledger for every address | `crm/tests/test_send_later.py::TestPermissions::test_losing_access_to_the_record_takes_the_job_with_it`, `TestSendNow::test_a_second_send_now_sends_nothing_more`, `::test_a_suppressed_recipient_is_never_written_to` |
| `crm.api.form.get_auto_response_fields` (GET/POST) | `System Manager` or `Sales Manager` (`_check_manager`) | No record is named and none is returned. The reply is a fixed, code-defined vocabulary | None needed — nothing is read | `crm/tests/test_form_auto_response.py::TestPermissions::test_a_sales_user_cannot_read_the_merge_vocabulary`, `::test_a_sales_manager_can` |
| `crm.api.form.send_auto_response_test` (**POST only**) | `System Manager` or `Sales Manager` | `_check_manager`, then `_get_crm_form`, which refuses a Web Form that is not `module == "FCRM"` and not targeting `CRM Lead` / `CRM Deal`. **The recipient is always `frappe.session.user`'s own address and cannot be supplied**: the function takes one argument, the form name | `Web Form`: must be a CRM form. `CRM Suppression`: the caller's own address is checked before the send, so a manager who opted out is not written to | `crm/tests/test_form_auto_response.py::TestPermissions::test_a_sales_user_cannot_send_a_test`, `TestTestSend::test_the_test_goes_to_the_caller_and_nobody_else`, `::test_the_endpoint_takes_no_recipient_argument` |
| `crm.api.quote.get_quote_preview` (GET/POST) | Any signed-in user | `frappe.has_permission("CRM Deal", "read", deal)`, which runs `crm.permissions.org_hierarchy.has_deal_permission`. The deal name is the ONLY scope input; there is no filter, field or doctype argument | `CRM Deal`: read on the named record. A deal that does not exist and a deal the caller may not see raise the SAME `PermissionError`, so the endpoint is not an existence oracle | `crm/tests/test_quote.py::TestPermissions::test_a_sales_user_without_deal_access_cannot_preview`, `::test_a_missing_deal_is_refused_like_a_forbidden_one`, `::test_the_owner_of_the_deal_can_preview` |
| `crm.api.quote.download_quote` (**POST only**) | Any signed-in user | `read` AND `print` on the named `CRM Deal`, both through `frappe.has_permission` | `CRM Deal`: read + print. `File`: written with `ignore_permissions=True`, PRIVATE, attached to that deal and nothing else | `crm/tests/test_quote.py::TestPermissions::test_a_sales_user_without_deal_access_cannot_download`, `::test_state_changing_endpoints_are_post_only`, `TestDownload::test_the_pdf_is_attached_privately` |
| `crm.api.quote.send_quote_on_whatsapp` (**POST only**) | Any signed-in user | `write` on the named `CRM Deal` (an outbound message writes to its timeline). The recipient number comes from `crm.api.whatsapp.get_reference_whatsapp_numbers(deal)` — **the endpoint has no number argument** — and `create_whatsapp_message` then re-checks write access and refuses a number the reference does not hold | `CRM Deal`: write. `WhatsApp Message`: created through `crm.api.whatsapp.create_whatsapp_message`, which keeps its own checks. `CRM Document Link`: minted after the checks, retired again if the send fails | `crm/tests/test_quote.py::TestPermissions::test_a_sales_user_without_deal_access_cannot_send`, `TestWhatsAppSend::test_the_endpoint_takes_no_number_argument`, `::test_the_tokenised_url_is_what_the_platform_is_handed`, `::test_a_send_failure_retires_the_token_it_minted` |
| `crm.api.quote.view` (**GET, `allow_guest=True`**) | **`Guest`**, deliberately | **The token IS the authorization.** 32 hex characters from `frappe.generate_hash` (128 bits), single-purpose, bound to ONE `File`, expiring (`expires_at`, default 14 days) and revocable (`active`). `resolve_link` answers `None` identically for a token that never existed, one that was revoked and one that expired, so the route cannot be used to learn which tokens were ever real. No caller-supplied doctype, name, filter or field reaches any query — the token is the whole request | `CRM Document Link`: must be `active` and unexpired. `File`: must still exist; the file is PRIVATE and is streamed by this route rather than served by its own URL. `CRM Deal`: **not read at all** — no deal field other than the already-rendered PDF is reachable | `crm/tests/test_quote.py::TestTokenRoute::test_an_unknown_token_is_refused`, `::test_a_revoked_token_is_refused_the_same_way`, `::test_an_expired_token_is_refused_the_same_way`, `::test_no_token_at_all_is_refused`, `::test_a_link_whose_file_is_gone_is_refused`, `::test_the_route_is_reachable_without_a_login`, `TestLink::test_an_absurdly_long_token_is_refused_without_a_query` |

### Why one endpoint is Guest, stated plainly

`crm.api.quote.view` is the only Guest endpoint this program has added. It exists
because the master spec requires it: the itinerary's public-File approach is not
sufficient for a quote, and a customer has to be able to open the document from a
WhatsApp message with no login.

What bounds it:

* the token is 128 bits of `frappe.generate_hash`, not derivable from the
  customer's name, the deal name or anything else the recipient can see;
* it expires (14 days) and is revoked the moment a NEW quote is minted for the
  same deal, so a withdrawn price cannot be read at yesterday's URL;
* it is bound to one File. There is no doctype, name or field argument, and the
  deal record is never read by the route;
* every fetch is written to `CRM Document Link View` with its user agent and IP;
* the file behind it stays PRIVATE. Losing a token costs one document until it
  expires, not the `/files/` directory.

### Doctype role permissions added in Stage 3A

`CRM Document Link` and `CRM Document Link View` name which customers opened
which quotes, so **no Sales User grant**. The deal timeline still shows a Sales
User their own record's quote opens, through `crm.api.activities`, which has
already checked read permission on the deal and then reads the log with
`frappe.get_all` scoped to that one record.

| Doctype | System Manager | Sales Manager | Sales User |
| --- | --- | --- | --- |
| `CRM Form Auto Response` | create, read, write, delete | create, read, write, delete | — |
| `CRM Form Auto Response Log` | create, read, write, delete | create, read, write | — |
| `CRM Document Link` | create, read, write, delete | create, read, write | — |
| `CRM Document Link View` | create, read, write, delete | create, read, write | — |

### Non-endpoint entry points added in Stage 3A

These are not whitelisted and cannot be called from a client.

| Entry point | Reached from | Authorization today | What a future endpoint must add |
| --- | --- | --- | --- |
| `crm.api.form.queue_auto_response` | `CRM Lead.after_insert`, `CRM Deal.after_insert` | Returns immediately unless `frappe.flags.in_web_form` is set. The `web_form` name arrives in the POST body and is therefore NEVER trusted as given: the row must be `module == "FCRM"` and its `doc_type` must equal the doctype of the record that was just created | Nothing. A manual "send the reply now" endpoint would need a Sales Manager check and must still take the `submission_key` claim |
| `crm.api.form.send_auto_response` | Background job only, enqueued after commit | Runs as `Administrator` for the `make` permission check, deliberately: the submitting user is `Guest`, and the message is the agency's, not the visitor's. It reads only the record it was handed and only the form it was handed. Never raises | A whitelisted wrapper would need a manager check AND must not accept a recipient — the address comes off the record |
| `crm.api.form.render_merge` / `merge_values` | `send_auto_response`, `send_auto_response_test` | Pure substitution over a FIXED allowlist (`AUTO_RESPONSE_MERGE_FIELDS`). Not a template engine: there is nothing to evaluate, and no field outside the allowlist is reachable. Values are HTML-escaped into a body | Nothing |
| `crm.api.email.email_adapter` | `crm.outbound.deliver_recipient`, via the registrar | Sets the session user to `job.owner_user` for the duration of the send, so `make` checks the right person's permissions and the Communication carries the right name. `crm.outbound.execute_job` has already refused a disabled owner AT EXECUTION TIME | Nothing. Never call it with a job that did not come from the state machine |
| `crm.api.email.register_adapters` | `crm.outbound.load_adapter_modules`, and Send-now | None needed — it binds a function to a channel and reads nothing. It does NOT overwrite an adapter already bound | Nothing |
| `crm.api.email.handle_inbound_reply` | `crm.utils.on_communication_insert` (the `Communication` `after_insert` hook) | None of its own; it runs inside the framework's own insert. It only ever CANCELS, and only a job whose recipient row it matched by the Communication link or by Message-ID. Never raises: a failure here would lose the customer's inbound message | A whitelisted "cancel by reply" endpoint would need the same two checks `load_job_for_action` makes |
| `crm.document_links.create_link` / `revoke_links` / `resolve_link` | `crm.api.quote` | None — the caller owns the check, and `crm.api.quote` makes it before every call | Any new caller must check the referenced record FIRST. `create_link` will happily mint a token for a record the caller cannot read |
| `crm.document_links.record_view` | `crm.api.quote.view` | None. It writes one row about a fetch that has already been authorised by the token. Never raises | Nothing |
| `crm.document_links.customer_views` | `crm.api.activities.get_quote_view_activities` | **Reads with `frappe.get_all`, which applies NO permission conditions.** Correct only because every caller has already checked read permission on the referenced record and the query is scoped to that one record | Never call it with a doctype/name a caller supplied without checking first. The docstring says so |
| `crm.document_links.cleanup_public_pdfs` / `expire_links` | The hourly scheduler, via `crm.api.itinerary.cleanup_public_itinerary_pdfs` and (pending) `crm.api.quote.cleanup_quote_links` | System jobs. Neither raises | n/a — scheduler only |
| `crm.outbound.load_adapter_modules` | `crm.outbound.process_scheduled_jobs` | Flag `outbound_engine_enabled`, default OFF, checked before this runs | n/a — scheduler only |
| `crm.api.activities.get_scheduled_email_activities` / `get_quote_view_activities` | `get_deal_activities` / `get_lead_activities`, which have already checked read permission on the record | Scope is the one record the caller was already granted. Both swallow every exception, so a broken outbound engine or a missing link doctype costs a timeline section rather than the whole record page | Nothing |

### One pre-existing endpoint whose payload changed

`crm.api.activities.get_activities` now returns, per communication, the
Communication `name` and `read_by_recipient_on`, plus two new activity types
(`scheduled_email`, `quote_view`). All additive; no field was removed or renamed.
Its permission check is unchanged and is still the first thing it does.
