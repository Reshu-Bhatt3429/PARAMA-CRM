# Stage 5.1 — Email sequences (item 21) — implementation notes

Scope: master spec §5 item 21, built to `demo-package/specs/design-21-email-sequences.md`.
Plus the one disjoint fix that note carries: the Brief card now reads WhatsApp.

Branch: `feat/feature-expansion`. Nothing is committed by this stage; all changes
sit in the working tree. Verification is recorded in `stage1-verification.md`
under "## Stage 5.1".

---

## What was built

| Piece | Where |
| --- | --- |
| Email channel adapter | `crm/sequences/email.py` |
| Per-stage channel dispatch | `crm/sequences/router.py` |
| Unsubscribe tokens, ledger write, List-Unsubscribe header | `crm/sequences/unsubscribe.py` |
| Guest unsubscribe page | `crm/www/unsubscribe.py` + `crm/www/unsubscribe.html` |
| Per-stage `channel` / `email_template` / `email_subject_override` | `crm/fcrm/doctype/crm_followup_stage/` |
| `email_sequences_enabled` flag, default OFF | `crm/feature_flags.py` + `fcrm_settings.json` |
| Settings UI (channel select, template picker, subject override) | `frontend/src/components/Settings/AIFollowupSettings.vue` |
| Timeline "Sequence · Stage n" chip | `crm/api/activities.py` + `frontend/src/utils/emailSequence.js` + `EmailArea.vue` |
| Brief reads WhatsApp | `crm/api/ai_brief.py` |
| Tests | `crm/tests/test_email_sequences.py` (69), `frontend/tests/unit/emailSequence.test.js` (11) |

## The five decisions worth knowing

### 1. A router, not a channel column

Master spec F3 says a channel is an ADAPTER. Stages of one sequence can still
mix, so something has to choose per call. `ChannelRouter` does, and holds no
rules of its own:

* calls that touch the ROW — lock, due date, park, advance, enrol — go to the
  WhatsApp adapter for BOTH channels, because the row is one
  `CRM WhatsApp Followup` whatever the stage sends on;
* calls that touch the CHANNEL — build, address, claim, hand over — go to the
  adapter that owns the stage being sent.

The pending stage is `current_stage + 1`, which is exact: the core advances
`current_stage` only after a stage completes.

**The router exists only when a stage is configured on Email.**
`crm.api.followup_engine.get_channel_adapter(stages)` returns the plain WhatsApp
adapter otherwise, so every site that has not used this stage runs the code it
ran before, byte for byte. That is why `crm/tests/test_followup_engine.py` passes
unchanged, and it is a design property rather than a lucky outcome.

### 2. The claim is the outbound job, inserted directly

`crm.outbound.create_job` is forgiving by design — a repeated key hands back the
EXISTING job, which is right for a user double-clicking Send Later and wrong for
an outbox guard, which has to know whether IT created the row. So the adapter
inserts the `CRM Outbound Job` itself and lets the unique index on
`idempotency_key` raise; the collision becomes `AlreadyClaimed` and the core
advances the stage instead of retrying it for ever.

Key format: `{lead}-cycle-{n}-stage-{n}-email`, exactly as the design note asks.

The job leaves `claim` in **Draft**, which the sweep never picks up; `send` moves
it to Scheduled. A crash between the two loses one message and can never repeat
one — the same trade the WhatsApp channel makes.

### 3. Two flags, and what each one stops

| Flag | Off means |
| --- | --- |
| `email_sequences_enabled` | An email stage parks its row with "Email sequences are turned off." No job is created. An inbound email stops nothing. The timeline chip lookup reads no row. |
| `outbound_engine_enabled` | A job that exists is never claimed by the sweep, so nothing is delivered. |

Both are OFF by default and both are needed to send. The demo site was left with
both OFF.

### 4. Suppression is checked three times

At claim time (`resolve_destination` returns "" for a suppressed address and the
row parks with the address in the reason), at delivery
(`crm.outbound.deliver_recipient`, inside the recipient's row lock), and again
inside `crm.api.email.send_email`. Three, because the gap between claiming a
stage and sending it is an hour long and consent can change inside it.

### 5. The List-Unsubscribe header had to be built

Frappe v15 has **no** unsubscribe-header machinery: `grep -rn "List-Unsubscribe"`
over `apps/frappe` returns nothing, and `make` takes no header argument. The
header therefore goes on where the message actually becomes MIME — one
`before_insert` hook on Email Queue, armed for the length of one adapter call by
`crm.outbound.deliver_recipient` when the job's payload carries an
`unsubscribe_url`. A message that is not a sequence send never sees the hook do
anything. A header line may sit anywhere in the header block (RFC 5322 §3.6), so
prepending one leaves the rest of the message byte-identical.

## Migration and downgrade

* **New doctypes:** none.
* **New fields:** `CRM Followup Stage.channel` (Select, default `WhatsApp`, NOT
  mandatory), `.email_template` (Link → Email Template), `.email_subject_override`
  (Data). `FCRM Settings.email_sequences_enabled` (Check, default `0`).
* **Indexes:** none added. The reply-stop address lookup uses
  `custom_parama_email_normalized`, which F7 already indexed.
* **Backfill:** `crm.patches.v1_0.backfill_followup_stage_channel` writes
  `WhatsApp` into stage rows saved before the field existed. Silent, idempotent,
  and not load-bearing: `get_stages` reads an empty channel as WhatsApp anyway.
* **Flags:** `email_sequences_enabled`, default OFF.
* **Downgrade:** remove the app's new modules and nothing sends. Scheduled
  sequence jobs stay in `CRM Outbound Job` and are never claimed (the sweep is
  flag-gated and OFF). Suppression rows stay and are still honoured by every
  other send path — that is the point of a consent ledger. The `/unsubscribe`
  route stops existing and answers 404, so an old link fails closed rather than
  silently doing nothing. Stage rows keep their channel column and it is ignored.

## Ops: deliverability

Sequence mail leaves through the agency's own outgoing Email Account, the same
one the composer uses. **SPF and DKIM for that sending domain are an ops task and
are out of code scope.** Bulk-ish mail from a domain with neither is filtered as
spam whatever the app does. One line for the runbook: publish an SPF record that
authorises the sending host, and enable DKIM signing on the SMTP provider, before
turning `email_sequences_enabled` on for real customers.

## Open issues and deviations

1. **The unsubscribe route is a GET that writes.** The design note specifies a
   tokenised footer link that writes the ledger and shows a confirmation page,
   and that is what was built. The cost is that a mail client or link scanner
   which prefetches links will unsubscribe the customer on their behalf. The
   failure is in the safe direction (a message not sent, never a message sent).
   RFC 8058 one-click is NOT advertised, because `List-Unsubscribe-Post` requires
   the URL to accept POST and this route does not. A future stage that wants
   one-click should add a POST handler and the second header together.
2. **The address rides inside the token.** It is base64 in the URL, so it appears
   in the customer's browser history and in any log that records query strings.
   The alternative — a stored token row — buys little: the link only ever travels
   to that address, and a stored row adds a table to enumerate and a write on the
   send path. Recorded as a deliberate trade, not an oversight.
3. **The enrolment row is still `CRM WhatsApp Followup`.** The design note calls
   the rename churn for v1 and asks for the debt to be recorded in the code; it
   is, in `crm/sequences/email.py`. Rename it when a THIRD channel arrives, not
   before.
4. **One daily cap covers both channels**, counted as WhatsApp claims plus email
   claims. `crm.sequences.core.sweep` asks for the budget once per row and before
   it knows the row's channel, so a per-channel cap would need the core to change
   shape. Master spec item 21 says "per-channel caps"; a shared cap is STRICTER
   than two separate caps, never looser, and is what an agency means by "no more
   than 50 follow-ups a day". Flagged here rather than implemented around.
5. **`crm/api/activities.py` is edited**, which is outside the file list this
   stage was given. The chip needs a stage number and the timeline payload is the
   only place it can arrive; the edit is two lines plus one guarded helper that
   returns `{}` while the flag is off. `crm/ai/client.py` is edited for the same
   kind of reason: its docstring holds the authoritative "what leaves the site"
   table, and the Brief now sends WhatsApp text.
6. **Approving an email draft REBUILDS the message** from the template rather
   than sending the stored copy, which is the opposite of the WhatsApp branch. A
   WhatsApp draft may hold AI-written values that cost a model call and cannot be
   reproduced; an email stage has no AI in it at all, so subject and body are a
   pure function of the template and the lead. Rebuilding produces the same
   message and picks up a template the manager corrected while the draft sat
   parked.
7. **Email stages do not use AI.** The stage's `use_ai` flag is WhatsApp-only and
   is hidden for an email stage. The design note asks for a template plus an
   optional subject override and nothing more, and an AI-written email body is a
   larger decision than this stage was scoped for.
8. **An email reply stops the WHOLE sequence, including WhatsApp stages.** That
   follows from "any customer reply stops the sequence at once", and it only
   happens while `email_sequences_enabled` is on, so no existing site changes
   behaviour.
