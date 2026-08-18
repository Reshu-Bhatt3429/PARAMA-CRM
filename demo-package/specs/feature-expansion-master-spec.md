# PARAMA-CRM Feature Expansion — Master Spec v2.1 (2026-08-18)

Status: APPROVED FOR BUILD. Owner decisions (2026-08-18): D1 checkpoint commit — approved. D2 feature modifications (on-demand tone; built-in read receipts only) — accepted. D3 — items 8 (leaderboard) and 12 (best time to contact) are CUT. D4 `.pi/PLAN.md` refactor phases — parked, feature expansion proceeds.

Change log v1 → v2: added Foundations stage (§4); resequenced 4 phases → 6 stages (§6); item 15 changed from automatic sentiment to on-demand tone (C6 compliance); item 19 changed to surface Frappe's built-in read receipts only (no custom pixel); item 27 deferred to the final stage behind its own security design review; UX de-clutter rules hardened (§2.13–2.18); compliance, migration, and email-contract requirements added (§7).

## 1. What we are doing

We add 27 features to PARAMA-CRM (self-hosted Frappe CRM fork, small travel agency). Items 8 (leaderboard) and 12 (best time to contact) were cut by owner decision on 2026-08-18. Item 29 (invoice module) was added by owner request on 2026-08-18 after dedicated research. High-risk items build last, on shared foundations, each behind its own design review. Hard constraints:

- C1. No paid third-party services. AI uses the existing `crm/ai` client (BYO key, capped budget, optional). Note: BYOK model calls themselves cost the user's own API budget; that is accepted and capped.
- C2. No new infrastructure. Reuse Frappe scheduler, email queue, Communication hooks, socket.io, CRM Notification, print-format machinery, and the follow-up engine's guardrail patterns.
- C3. UI stays clean. Exactly ONE new sidebar item ("Today"). Everything else lives in existing surfaces or Cmd+K.
- C4. Mobile parity is part of EACH feature's acceptance criteria, not a final pass. Every feature spec below implies: works in the Mobile pages, with touch interactions, or it is not done.
- C5. Every new automation and sweep ships behind a named default-OFF flag. The full flag list is an appendix each stage's worker must extend.
- C6. AI never acts on its own and never writes to records without a user click. No automatic classification, no auto-send.

## 2. UX principles (binding)

1–12: unchanged from v1 (soft badge pills; one sparkle slot for AI; undo-toasts over confirm dialogs except destructive; inline over modal; Cmd+K never empty; minimal empty states; skeletons; thin bars not gauges; no new mandatory fields; digest-batched notifications; `__()` i18n; screenshots in `demo-package/ui-reference/`).

13. Record header is NOT a badge shelf: max two tag chips + "+N"; the lead score (Stage 5) lives in a collapsed "Insights" block in the side panel; multiple health flags collapse into one "Needs attention" chip that expands on click; tone appears only inside the summary card.
14. Composer keeps three visible actions: attach, template/snippet, one sparkle. Formatting and secondary actions move to an overflow menu. Send Later exists only inside the Send split button. `/` triggers snippets.
15. Timeline gets ONE dismissible "Brief" card (summary + suggested next step + tone). No stacked AI cards.
16. Settings groups into three sections — Communication, Automation, Data quality — with advanced options collapsed and disabled features' pages hidden until their flag is on. Never one settings page per doctype.
17. "Today" is a single prioritized list with type icons and filter chips (Tasks / Replies / Deals / Approvals), not four stacked panels. Mobile: full-page route, swipe/overflow actions.
18. Mobile search: the palette opens from a top-bar search button; recents are scoped per site + user; every palette result is permission-checked server-side before display.

## 3. Endpoint authorization rule (binding, from review risk #1)

Every new whitelisted endpoint states, in code review and in its test file: who may call it, how row-level scope is derived SERVER-side (never from client-supplied filters alone), and which permission check covers each referenced record. Use permission-aware queries (`frappe.get_list`) or explicitly apply the org-hierarchy conditions (`crm/permissions/org_hierarchy.py`); raw `frappe.db.sql` / query-builder results must be filtered through the same conditions. Background jobs re-check permissions at execution time, not enqueue time. A permission matrix file `demo-package/specs/permission-matrix.md` is created in Stage 1 and updated by every worker that adds an endpoint.

## 4. Foundations (Stage 1 — build before any feature)

- F1. **Consent & suppression ledger.** New doctype `CRM Suppression` keyed by channel + normalized address (email or E.164 phone): state (opted_out / bounced / complained), source, timestamp, audit trail. EVERY outbound path (composer, sequences, mass email, web-form auto-response, quote sends) checks it. The existing WhatsApp opt-out state writes through to it. Reversal requires a reason (existing follow-up pattern).
- F2. **Outbound-job state machine.** Doctypes `CRM Outbound Job` + `CRM Outbound Recipient` with unique idempotency keys and states Draft → Scheduled → Claimed → Queued → Sent / Failed / Cancelled. Enqueue only after commit; row-lock before Send-now/Cancel; correlate the Email Queue row; reply matching by Message-ID / In-Reply-To, never subject. Send Later, mass email, sequences, and scheduled reports all ride this. UI wording distinguishes queued / delivered / failed.
- F3. **Sequence core extraction.** Generic enrollment + state-transition engine with channel adapters (WhatsApp adapter = current Meta semantics incl. commit-before-external-send; Email adapter = Email Queue semantics via F2). Migrate the existing WhatsApp follow-up engine onto the core with byte-equivalent behavior (its tests in `crm/tests/test_followup_engine.py` must pass unchanged, plus new core tests). Do NOT just add a channel column to the existing child table.
- F4. **Automation execution context.** For workflow rules (Stage 5): depth-ceiling counter for synchronous nesting, `doc.get_doc_before_save()` for real transition detection, durable unique execution key (rule, document, source version/event, action) for cross-job idempotency, side effects queued after commit, atomic daily-cap reservation. Built in Stage 1 as a small library; consumed later.
- F5. **Reminder delivery ledger.** `CRM Reminder Log` with unique (task, recipient, offset, due_date, channel); bounded query window; composite index on CRM Task (due_date, status); suppress Done/Cancelled.
- F6. **AI client hardening** (`crm/ai/client.py`): atomic budget reservation (row-lock or atomic UPDATE before the network call), real JSON-schema validation of responses, request payload size caps, per-feature documentation of exactly which record fields leave the site. No streaming claim anywhere until implemented.
- F7. **Data plumbing:** normalized + indexed email/phone columns on Lead/Contact/Deal (backfilled by a resumable patch that sends nothing); incremental sweep pattern (watermark + pagination + per-job lock) used by every nightly job.
- F8. **Feature-flag registry:** one FCRM Settings section listing every default-OFF switch; one worker owns `fcrm_settings.json` and `crm/hooks.py` edits per stage (single-owner rule, integrated continuously, permission/concurrency tests after each hook addition).
- F9. **Migration discipline:** every worker ships patches with: new doctypes/fields enumerated, indexes, backfills (resumable, silent), default-off flags, and a stated downgrade behavior. No edits to upstream core schemas; custom fields on core doctypes (Communication, Web Form) are namespaced `custom_parama_*`.

## 5. Feature specs (deltas from v1 only; v1 text stands where not amended)

- **1. Task reminders** — uses F5 ledger; NOT the event-reminder precedent (it double-fires across `all` + hourly schedules).
- **2. Tags / 3. Duplicate warning / 6. Forward / 23. Snippets / 10. Cmd+K / 11. Recents** — as v1, plus: duplicate check endpoint returns only records the caller can read (§3); Cmd+K results permission-checked server-side; recents localStorage key scoped site+user.
- **5. Send later** — rides F2; authoritative timezone = the sender's user timezone, stored explicitly; cancellation cutoff = until Claimed; reply-cancel matches In-Reply-To.
- **4. Web-form auto-response** — hooks the POST submission path of the built-in Web Form (NOT the GET-oriented `crm/www/crm_form.py`); idempotent per submission; suppression-checked (F1).
- **19. Email open state (changed)** — surface the EXISTING `read_by_recipient` + `delivery_status` already returned by `crm/api/activities.py` as a quiet indicator + tooltip. No custom pixel, no new tracking. Default visible; no toast, no timeline text.
- **7. Target meter** — metric defined: sum of won deals (status type Won) by `closed_date`, org currency via the existing exchange-rate path, calendar-month target period independent of the dashboard date filter; site-wide setting v1.
- **8. Leaderboard — CUT (owner decision 2026-08-18).** Do not build.
- **12. Best time to contact — CUT (owner decision 2026-08-18).** Do not build.
- **13/28/15 merged into one "Brief" card** — on-demand summary (3–5 bullets) + suggested next step (one-click task, never auto) + tone line ("Tone: frustrated") from the SAME single LLM call. Item 15's standing auto-classification is REMOVED (C6). No Communication custom field for sentiment.
- **14. AI email draft** — insert-at-cursor with immediate Undo; no streaming (F6); the popover shows which lead fields will be sent to the model.
- **22. Deal-health flags** — incremental sweep (F7); flags stored JSON; "Needs attention" single chip (§2.13); digest respects quiet hours + per-user toggle.
- **24. Today** — single prioritized list (§2.17); becomes the Sales User default home; the one allowed sidebar addition.
- **25. Quote PDF** — requires a token-gated controller route with a view log that distinguishes Meta's fetch (user-agent + first-fetch) from customer opens; the current itinerary public-File approach (`crm/api/itinerary.py:713`) is NOT sufficient — extract and upgrade that machinery into a shared helper both features use; respect the WhatsApp 24-hour service window rules already handled for itineraries.
- **9. Scheduled reports (Stage 5)** — schedules bind to the CREATOR's permissions and are disabled automatically when the creator is disabled; external recipients require a manager role; exports cleaned up after send.
- **16. Workflow rules (Stage 5)** — consumes F4; actions limited to: send template email (suppression-checked), create task, update field, notify. Execution log doctype with unique execution key.
- **17. Find & merge (Stage 5)** — v1 ships the read-only duplicate scanner + warnings only; the merge writer is a separately reviewed design (preflight manifest of ALL linked doctypes incl. dynamic links, transaction locks, framework rename/merge machinery where possible, tombstone doctype for redirect/audit — not a live "merged" Lead).
- **18. Lead scoring (Stage 5)** — after health flags + Today prove the prioritization UX; recompute via F7 sweeps + doc events.
- **20. Mass email (Stage 5)** — requires F1 + F2 + unsubscribe link + List-Unsubscribe header + bounce/complaint suppression feeding F1. Progress UI reports queued/delivered/failed truthfully.
- **21. Email sequences (Stage 5)** — on the F3 core, email adapter; unsubscribe as mass email; per-channel caps and reply rules.
- **26. Booking page (Stage 5)** — interval-overlap locking (SELECT FOR UPDATE on slot window), explicit timezone/DST handling, rate limit + honeypot, duplicate-lead policy = reuse the WhatsApp phone/email lookup; the page states that availability reflects CRM calendar only (no external calendar sync in v1).
- **27. Ask-the-CRM chat (Stage 5, last)** — requires its own security design review before build: read-only tool registry executed as the session user, ≤ 5 tool calls, per-tool permission matrix entry, no raw SQL tools, output cites records as permission-checked chips.
- **29. Invoice module (Stage 5; added 2026-08-18; own design note before build).** Research basis: hallmark-workflow, GST-compliance, and Frappe-path reports (this session; compliance points cite CBIC primary texts). Design:
  - Data: `CRM Invoice` + child `CRM Invoice Item` + child `CRM Invoice Payment`. One-click "Convert deal to invoice" on the Deal record (HubSpot's hallmark pattern: invoice section in the deal sidebar; line items, customer, org auto-fill from deal products). Status machine: Draft → Sent → Partially Paid → Paid, plus Overdue (display state from due date) and Void (terminal, excluded from revenue reports). History log of status changes on the record.
  - Numbering: Frappe naming series, default `INV/.YY.-.####`, validated to GST Rule 46(b): ≤ 16 chars, charset `[A-Za-z0-9\-\/]`, unique per financial year; number locks at first send (immutable after finalization, HubSpot-style).
  - GST fields (Rule 46, primary-source verified): supplier name/address/GSTIN from a new Company Profile settings section; recipient GSTIN (B2B); enforced recipient name/address/state+code when B2C and total ≥ ₹50,000; per-line SAC from an admin-editable master (codes NOT hard-coded — single-source research); CGST/SGST vs IGST split from place-of-supply state vs supplier state; reverse-charge flag; tour-package mode: 5% without ITC with the mandatory "amount charged is gross and inclusive of accommodation and transportation" statement auto-printed, vs commission mode (standard rate on service fee). Section 170 rounding (round-half-up to whole rupee) on tax and total. Issue-date warning at 30 days after service date (Rule 47).
  - Out of scope v1 (documented, not silently dropped): e-invoicing IRN/QR (last-confirmed threshold ₹5 crore, 2023 — worker must re-check the live 2026 threshold once and record it), Bill of Supply variant, credit notes (Void + manual adjustment v1), recurring invoices, online payment gateways (C1). Optional free extra: static UPI QR (agency VPA) rendered on the PDF via a local qrcode dependency — no gateway, no fees.
  - Flow: PDF via print format (shared machinery with item 25); send on email + WhatsApp (24-h window rules as itineraries); tokenized view link with view log; manual payment recording full/partial with automatic status math; payment schedule for travel deposits (deposit now / balance before departure — Travefy's hallmark), each schedule row generating its own due-date reminders; overdue reminder ladder on the F2 outbound machine, default due-date / +7 d / +14 d (HubSpot's default), suppression-checked, quiet-hours aware, per-invoice mutable; "payment received" thank-you template on recording.
  - Reports: dashboard tiles — outstanding total, overdue total, collected this month.
  - AC: won deal converts to a correct GST invoice in one click; totals match Section 170 rounding; a 2-payment schedule fires two reminder ladders; Void excludes the invoice from tiles; a 17-char or bad-charset number is rejected.

## 6. Sequencing (6 stages; replaces the 4-phase plan)

- **Stage 0 — Baseline.** Checkpoint commit of the current tree (USER APPROVAL PENDING); full backend + frontend test baseline recorded; secret scan; supersession note for `.pi/PLAN.md` Phases 3B/4 (recorded there and here: feature expansion proceeds first by owner decision; the Field/Grid refactor is parked — our features do not modify Field.vue/Grid.vue internals, and any worker who must touch them STOPS and escalates instead).
- **Stage 1 — Foundations.** F1–F9. One worker; no user-visible change; everything behind flags; full suite green.
- **Stage 2 — Low-risk slice.** Items 2, 3, 6, 23, 10, 11, 1. Desktop + mobile together.
- **Stage 3 — Operational slice.** Items 4, 5, 19, 7, 22, 24, 25.
- **Stage 4 — AI slice.** Items 13+28+15 (Brief card), 14.
- **Stage 5 — High-risk projects, each behind a short design note reviewed before build.** Order: 21 → 16 → 29 (invoices) → 20 → 17 → 9 → 18 → 26 → 27.
- **Stage 6 — Adversarial review (user-mandated).** ≥ 2 fresh-context reviewers with distinct lenses: correctness/security (permission matrix, concurrency, queue crash points) and UX-clutter (§2 compliance, mobile). Fix round, then final full verification.

Collision rules: one owner per stage for `crm/hooks.py` and `fcrm_settings.json`; composer/timeline components (`EmailEditor.vue`, `CommunicationArea.vue`, `EmailArea.vue`) are touched by exactly one worker per stage (Stage 2: item 6+23; Stage 3: item 5+19; Stage 4: item 14 + Brief card); Lead/Deal page + list renderers likewise single-owner per stage.

## 7. Cross-cutting requirements (from review §7)

- **Compliance:** unsubscribe link + header on every promotional path (mass email, sequences); bounce/complaint suppression; tracking disclosure line in Settings; AI-provider disclosure in AI settings; suppression ledger honored by all sends; data-retention note for logs (default 1 year, configurable).
- **Email contracts:** "sent" means Email Queue reports sent; retry per queue defaults; sender account = the user's default outgoing account, stated in the composer; generated CSV/XLSX/PDF files cleaned by the existing hourly cleanup pattern.
- **Testing floor (per feature):** unit tests + role/hierarchy permission matrix test + (where queues/jobs are involved) a two-worker concurrency test and a crash-point test around claim/commit; frontend component test for composer/palette changes; a mobile smoke check; full suite green, reported verbatim.
- **Demo/ops safety:** demo data must never reach real email, WhatsApp, or AI providers (outbound-disabled sandbox flag for the demo site); seeded examples for health flags, duplicates, drafts; queue-health indicator in Settings → about; runbook notes per stage in demo-package/specs/.
- **Upstream strategy:** supported Frappe version pinned at Stage 0; namespaced custom fields; overridden APIs documented; compatibility tests around Communication email APIs.

## 8. Owner decisions (resolved 2026-08-18)

- D1. Checkpoint commit — APPROVED.
- D2. Feature modifications (on-demand tone; built-in read receipts only) — ACCEPTED.
- D3. Leaderboard (8) and best time to contact (12) — CUT.
- D4. `.pi/PLAN.md` Field/Grid refactor phases — PARKED; feature expansion proceeds. Workers who would need to modify Field.vue/Grid.vue internals STOP and escalate.
- D5 (PENDING): confirm the agency's tax jurisdiction is India/GST (the invoice module's compliance floor assumes it), and whether to include the free static UPI QR on invoice PDFs.
- D6 (2026-08-18): rebrand the product UI to "PARAMA CRM" — user-directed; display-level only, AGPL attribution kept in the About modal; placeholder wordmark until the user supplies a real logo.

## 9. Must-not-do list — unchanged from v1, plus: no client-supplied filter is ever trusted server-side; no sweep without a watermark and lock; no send path without a suppression check.
