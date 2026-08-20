# Design note — Item 16: Mini workflow rules (Stage 5, project 2)

Status: approved by the planner 2026-08-19. Build on this note plus spec §5 item 16.

## Data model

- `CRM Workflow Rule`: title, apply_on (CRM Lead / CRM Deal), event (Record created / Field changed / Stage changed), watched_field (for Field changed), condition JSON (same shape the assignment-rule builder produces), enabled (default 0), daily_action_cap (default 500), owner-role Manager.
- Child `CRM Workflow Action`: action_type (Send email template / Create task / Notify user / Update field) + per-type fields (template, recipient mode; task title/priority/due offset; notify user/role; field + new value).
- `CRM Workflow Execution Log`: unique `execution_key` (rule, document, source docversion/event, action index), rule, document, event, status (Executed / Skipped-cap / Skipped-suppressed / Failed + reason), timestamp. No delete permission for Sales Manager (send-log precedent).

## Execution

- doc_events after_insert / on_update on Lead and Deal → an engine module (crm/workflows.py) collects enabled rules for the doctype+event; "Stage changed" and "Field changed" require a REAL transition via automation_context's get_doc_before_save helper (F4).
- Actions run AFTER COMMIT via F4's after-commit queue. Email actions go through crm.api.email.send_email (suppression-checked). Update-field actions execute inside the F4 automation context with depth ceiling 1: a workflow-caused update NEVER triggers workflows again (v1 rule — simple and safe; document it in the UI help text).
- Daily cap: F4 reserve_daily_slot per rule (atomic). Cap reached → action skipped + logged Skipped-cap; a CRM Notification tells the rule owner once per day.
- Idempotency: the execution_key makes retries and double-fires no-ops across background jobs.
- Flag: `workflow_rules_enabled`, default OFF, plus per-rule enabled default OFF (two switches must be on before anything fires).

## UI

Settings → Automation → "Workflow rules": list of rules (title, on, event, enabled toggle, runs today); editor = ONE vertical stack of connected cards: "When" card (doctype + event + field picker), "If" condition rows reusing the assignment-rule condition components (AND/OR), "Then" action cards (type dropdown revealing inline fields), short vertical connectors, inline expand — no nested modals (UI research: HubSpot canvas simplified to a linear stack). A read-only "Recent runs" panel from the execution log. Managers only.

## Acceptance criteria

1. "Stage → Qualified ⇒ create task" fires exactly once per real transition and logs one row; re-saving without a stage change fires nothing.
2. A rule whose action updates a field that another rule watches does NOT cascade (depth-ceiling test).
3. Cap: with cap=2, the third matching record logs Skipped-cap and notifies the owner once.
4. Email action to a suppressed address logs Skipped-suppressed and sends nothing.
5. Flag OFF or rule disabled ⇒ zero engine work on save (measured: no queries beyond a cached flag read).
6. A Sales User cannot create, edit, or list rules; a manager can.

## Risks

on_update fires on every save of hot doctypes — the enabled-rules lookup must be cached (invalidate on rule save) so the no-rules path costs ~nothing. Execution log growth: daily cleanup keeps 90 days.
