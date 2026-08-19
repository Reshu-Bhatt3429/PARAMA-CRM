# Stage 5.2 — Mini workflow rules (item 16) — implementation notes

Built against `demo-package/specs/design-16-workflow-rules.md` (approved
2026-08-19) and master spec §5 item 16, §2, §3 and §7.

The feature is the first and only consumer of the F4 automation library
(`crm/automation_context.py`), which Stage 1B built and left unwired. Every one
of F4's five guards is used, and each one earns its place below.

Everything is behind `workflow_rules_enabled`, default OFF, and behind each
rule's own `enabled`, default 0. **Both** must be on before anything fires.

## What was built

**Backend**

- `CRM Workflow Rule` — title, apply on (Lead / Deal), event, watched field,
  condition JSON, enabled, daily action cap, child action table, and the three
  read-only counter columns the atomic cap reservation writes.
- `CRM Workflow Action` — a child table with four action types and the fields
  each one needs.
- `CRM Workflow Execution Log` — one row per action per execution, keyed by a
  unique `execution_key`. No delete for Sales Manager.
- `crm/workflows.py` — the engine: the cached rule table, the two document
  hooks, the structural condition evaluator, the after-commit executor, the four
  actions, the daily cleanup and six manager-only endpoints.
- `crm/hooks.py` — `after_insert` + `on_update` on both doctypes, `on_update` on
  FCRM Settings for cache invalidation, and one daily cleanup job.
- `crm/feature_flags.py` + `fcrm_settings.json` — the flag, in both halves of
  the registry contract.

**Frontend**

- Settings → Automation & Rules → **Workflow Rules**: a list with an inline
  enable toggle and a runs-today counter, and an editor that is ONE vertical
  stack of When / If / Then cards with short connectors and no nested modal.
- The "If" card is the assignment-rule condition builder itself
  (`AssignmentRulesSection` → `CFConditions` → `CFCondition`), reused rather
  than reimplemented.
- A read-only "Recent runs" panel under the editor.
- `frontend/src/utils/workflows.js` — the pure half (shapes, validation,
  summaries), so it can be tested without mounting anything.

## The six decisions worth knowing

### 1. The hot path costs zero queries, and that is the whole cache design

`on_update` fires on every save of a lead and of a deal. A feature that is
switched off must not cost the site a query per save forever, so the master flag
AND the enabled rule table live in ONE Redis blob, dropped by the rule's own
document hooks and by the FCRM Settings hook.

Measured, in `TestAC5NoEngineWorkWhenOff`, by counting `frappe.db.sql` calls
around a direct call to the engine's entry points:

| State | Statements the engine adds |
| --- | --- |
| Flag OFF, cache warm | **0** |
| Flag OFF, cache cold | **1** (the `Singles` read for the flag) |
| Flag ON, no rules, cache warm | **0** |
| Flag ON, rule disabled | **0** |
| Flag ON, rule for the other doctype | **0** |
| Flag ON, an enabled rule that MATCHES | **0** — the decision is made in memory |

The last row is the one worth pausing on: even the firing path reads nothing.
Conditions are evaluated against the in-memory document, and `has_changed` uses
`doc.get_doc_before_save()`, which the framework already loaded for this save.

**The stated hole:** a flag or a rule changed by raw SQL does not pass through a
document hook and therefore does not drop the blob. `crm.workflows.clear_cache()`
is the fix; the tests call it, and any script that flips the switch behind the
doctype's back must too. This is written in the module docstring, in the flag's
registry entry and in the field description.

### 2. Depth ceiling ONE, not the library's default three

`crm/automation_context.py` defaults to three levels of synchronous nesting.
This engine uses one: **a field a workflow writes never triggers a workflow.**
The design note asked for it, and it is the safest v1 answer — a cascade is the
most damaging thing a rule engine can do to a customer's data.

The ceiling is checked FIRST, before the cache read, because it is a
`frappe.local` attribute lookup. `update_field` wraps its `doc.save()` in
`execution_depth(limit=1)`, so the `on_update` that save triggers finds the
depth already at the ceiling and returns before reading anything.

`TestAC2NoCascade` proves both halves: a rule that writes a watched field
queues nothing, and the same field changed BY HAND still fires the watcher. A
ceiling that also stopped humans would be a broken feature, not a safe one.

### 3. The rule carries its OWNER'S authority, not the saver's

A background job runs as the user who saved the record. That user may be a sales
agent with no right to email the customer or write the field the manager's rule
sets — and then the same rule would do more for a manager's own edits than for
an agent's, which is not what "when a deal reaches Won" means.

So `execute_rule` switches to the rule's owner with `frappe.set_user`, but only
after `acting_user()` re-checks AT EXECUTION TIME (master spec §3) that the
owner still exists, is still enabled, and still holds a manager role. Every
action then runs under the framework's ordinary checks for that user. This is
the same shape as master spec §5 item 9 ("schedules bind to the CREATOR's
permissions") and the outbound engine's `owner_user`.

Verified live: the job was enqueued by `crm.rep1@example.com` (a Sales User) and
the task it created has `owner: crm.manager@example.com`.

### 4. Conditions are evaluated structurally, never by building an expression

The stored shape is exactly what the assignment-rule builder produces — a flat
list of `[field, operator, value]` rows joined by `and` / `or`, with optional
nested groups — so there is ONE condition format in the app and the reused
frontend components need no adapter.

The server does **not** turn it into a Python string and evaluate it.
`frappe.safe_eval` is safe enough for a desk-only assignment rule; a condition
an operator types into a web form, that then runs on every save of every lead,
is not the place to find out how safe "safe enough" is. `matches()` folds the
tree directly. `TestConditions` covers all twelve operators, both joining words,
nesting, and the two shapes an In value arrives in.

Rule save also refuses any field name that is not a real field of the target
doctype, so a typo is caught by the manager's Save button rather than by a
background job at two in the morning.

### 5. Claim, then reserve, then act — in that order, for a reason each

Per action:

1. **Claim** the `execution_key` by inserting the log row, and **commit**. A
   retry, a double-fire and a second worker all build the same key and lose on
   the unique index. A process that dies between the claim and the action leaves
   a `Claimed` row that is never retried: at most once, never twice.
2. **Reserve** one of the day's slots with `reserve_daily_slot` — one `UPDATE
   ... WHERE`, so two workers cannot spend the same last slot. Refused ⇒
   `Skipped-cap` on the row, plus one notification to the rule's owner, claimed
   by its own single atomic `UPDATE` on `cap_notified_on` so the owner is told
   once a day and not once per refused action.
3. **Act**, inside a per-action savepoint, so an action that half-wrote
   something is undone without touching the claim above it or the actions beside
   it.

Two consequences, stated rather than discovered later: a **failed** action keeps
the cap slot it spent (a rule failing 500 times a day should stop), and the log
row records the exception MESSAGE while the full traceback goes to the Error Log
— the top of a traceback never says what went wrong.

The `Claimed` status is an addition to the design note's list of four. It is the
transient first state, exactly as on `CRM Followup Send Log` and
`CRM Reminder Log`, which is the precedent the note itself cites.

### 6. The editor is a linear stack, not a canvas

The design note's UI research: HubSpot's workflow canvas simplifies to a linear
When / If / Then read. So there are three connected cards with short vertical
connectors, action cards that expand INLINE (§2 UX principle 4: inline over
modal), and no nested dialog anywhere. The type dropdown reveals only the fields
its type uses, and switching type clears the fields the old type owned.

Two things the UI says out loud because they are promises, not implementation
details: that a field this rule writes never triggers another rule, and that
every address is checked against the opt-out ledger before anything is queued.
The list screen also shows a one-line banner while the master flag is off, so a
rule that looks enabled but cannot fire is never a silent mystery.

## Migration and downgrade

- **New doctypes:** `CRM Workflow Rule`, `CRM Workflow Action` (child),
  `CRM Workflow Execution Log`.
- **Indexes:** unique on `CRM Workflow Execution Log.execution_key` (verified on
  the live database: `Non_unique = 0`); search indexes on `rule` and
  `reference_docname`.
- **New field on an existing doctype:** `FCRM Settings.workflow_rules_enabled`,
  `Check`, default `"0"`.
- **No patch was needed.** There is nothing to backfill: the three doctypes are
  new and empty, and the flag's default is the correct value for every existing
  site. No upstream core schema is touched.
- **Downgrade:** turn `workflow_rules_enabled` off. The engine then stops on one
  cached read; no rule can fire whatever its own switch says. Removing the app
  code leaves three unused tables and one unused settings field, and no other
  feature reads any of them. Existing rules and logs survive a downgrade and
  resume exactly where they were if the flag is turned back on.

## Ops

- The daily cleanup keeps 90 days of execution log, in bounded batches (500 rows
  × 20 batches per night). A site that was never cleaned catches up over several
  nights rather than holding one enormous transaction open.
- A rule left `Claimed` in the log is a worker that died mid-action. It is not
  retried, by design. Counting them is the way to notice a crashing action.
- Before turning the flag on for a real agency: check that the rules that exist
  are the rules that are wanted, and that any email action's template renders —
  a template that raises produces a `Failed` row, not a send.

## Open issues and deviations

1. **`frappe.enqueue` swallows a kwarg named `event`.** The engine originally
   sent the workflow event under that name. `frappe.enqueue` takes `event` for
   itself, so the argument never reached the job and the worker raised
   `TypeError: execute_rule() missing 1 required positional argument: 'event'`
   into a worker log — with no log row, no notification and no sign in the app.
   The unit suite did not catch it because its `frappe.enqueue` double took
   `**kwargs` alone. **A live run found it.** Fixed by renaming to
   `workflow_event`; the double now spells out `frappe.enqueue`'s real
   parameters, and `test_no_job_argument_collides_with_enqueue` compares
   `JOB_KWARGS` against `inspect.signature(frappe.enqueue)`.
   **Handed on:** `crm.automation_context.queue_after_commit` is a shared helper
   with the same trap for every future caller. Putting the collision check
   inside it would fix this once for everybody; that file is outside this
   stage's ownership, so it is flagged rather than edited.
2. **`Claimed` was added to the execution log's status list**, which the design
   note gives as four values. It is the transient first state and follows the
   send-log precedent the note itself cites. Deviation, stated.
3. **A rule owned by `Administrator` runs as whoever saved the record.**
   `acting_user()` returns `None` for Administrator rather than switching to it,
   because switching a background job to Administrator would silently escalate
   every action past the org hierarchy. The consequence is that an
   Administrator-owned rule inherits the saver's permissions, so an action the
   saver may not perform is logged `Failed`. Rules should be created by a real
   manager account; the UI creates them as the signed-in user, so this only
   affects rules made from a console.
4. **`Notify user` writes an in-app notification only.** The design note says
   "notify"; an email leg would be a second send path and would need its own
   suppression story. In-app only is the narrower, safer reading.
5. **`like` is case-sensitive substring matching**, faithful to what the
   assignment-rule condition compiles to. It is not SQL `LIKE` and there are no
   wildcards. Recorded because "like" invites the other expectation.
6. **The daily cap counts ACTIONS, not rule firings.** A rule with three actions
   spends three slots per record. That is what `daily_action_cap` says, and it
   is the number that bounds the damage.
