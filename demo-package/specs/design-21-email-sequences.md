# Design note — Item 21: Email sequences (Stage 5, project 1)

Status: approved by the planner 2026-08-19. Build on this note plus spec §5 item 21.

## Architecture

- Rides the Stage-1 foundations. The F3 sequence core (crm/sequences/core.py) gains an **EmailAdapter** (crm/sequences/email.py) implementing the same ChannelAdapter interface as WhatsApp. Delivery goes through **CRM Outbound Job/Recipient** (F2) — the adapter creates a Scheduled job per due step; the existing hourly sweep delivers via crm.api.email.send_email (already suppression-checked).
- Enrollment model: per-stage `channel` (WhatsApp / Email) on CRM Followup Stage plus `email_template` (Link, Email Template) and `email_subject_override` (optional). The enrollment row stays CRM WhatsApp Followup for v1 (rename is churn; the core already treats it as generic state — document this debt in the code). A lead with no email skips email stages with a stated blocked_reason, never an error.
- Reply-stop: Communication after_insert (inbound) → crm.outbound.match_reply by In-Reply-To/Message-ID → core.mark_replied (same state machine as WhatsApp replies). Additionally: ANY inbound email from the lead's normalized address stops the sequence (address match fallback, since clients strip headers — Stage 3A open issue).
- Unsubscribe: every sequence email carries a List-Unsubscribe header + a tokenized footer link → a public endpoint that writes the F1 suppression ledger (channel Email) and shows a plain confirmation page. Suppression is checked at claim time AND send time.

## Guardrails (all inherited or extended, none weakened)

Enabled flag: new `email_sequences_enabled`, default OFF, independent of the WhatsApp engine flag. Quiet hours, daily cap, idle bound, opt-out keywords (email body reply STOP equivalent: unsubscribe link only, no keyword scan v1), draft-for-approval mode: all honored via the core. At-most-once: outbound idempotency key `{lead}-cycle-{n}-stage-{n}-email` mirrors the send-log pattern. The WhatsApp engine's 99 tests must pass unchanged.

## UI

AIFollowupSettings gains a per-stage channel select and, when Email is chosen, the template picker + subject override. One settings screen, no new page. The lead's follow-up state panel shows the channel icon per stage. Timeline: sequence emails render as normal outgoing emails with a small "Sequence · Stage n" chip.

## Acceptance criteria

1. A 2-stage email sequence sends stage 1 via the outbound sweep, stops on an inbound reply (header match AND address-only match tested separately).
2. The unsubscribe link suppresses the address; the next due step is skipped with blocked_reason; the ledger row carries source "unsubscribe_link".
3. Draft-for-approval parks email steps exactly like WhatsApp steps.
4. Double-claim cannot double-send (idempotency test at the outbound layer).
5. Mixed sequence (WhatsApp stage 1, Email stage 2) runs both channels in order.
6. Flag OFF = the adapter never registers; the foundation stays provably send-free.

## Also in this work package (small, disjoint)

- **Brief reads WhatsApp**: crm/api/ai_brief.py currently sees only Communications; this agency is WhatsApp-first (28 WhatsApp messages, 0 emails on the demo site). Include the last N WhatsApp messages in the Brief payload under the same size caps; tone uses inbound WhatsApp too. (Stage 4 open issue 2 — planner decision: include.)

## Risks

Deliverability of sequence mail from the agency's SMTP (SPF/DKIM are ops, out of code scope — one line in the runbook). Public unsubscribe endpoint must be rate-limited and token-scoped (no enumeration). Guest 403 quirk on www pages: the unsubscribe page must be a Guest-allowed www route — test it as Guest.
