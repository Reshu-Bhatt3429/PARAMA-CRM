# Stage 4 — the AI slice: notes and decisions

Scope: master spec §5 items **13 + 28 + 15 merged** (the timeline Brief card) and
**14** (the AI email draft), plus two side tasks handed on by earlier stages: the
digest's quiet hours and per-user toggle (Stage 3B open issue 2) and the AI
budget lock (Stage 1B flag 1).

Branch `feat/feature-expansion`. Nothing is committed by this stage.

Verification output — test counts, live checks, build, lint — is in
`demo-package/specs/stage1-verification.md` under "## Stage 4". Endpoint rows are
in `demo-package/specs/permission-matrix.md` under "## Stage 4".

---

## 1. One click, one model call

Items 13 (record summary), 28 (suggested next step) and 15 (message tone) were
three features in v1. The master spec merged them into one card, and the merge is
only worth anything if they share the call: `crm.ai.client` counts REQUESTS
against the agency's monthly budget, so three calls for one button would cost
three times as much and produce a worse screen (UX §2.15 allows the timeline one
dismissible card, not a stack of three).

`crm.api.ai_brief.generate` therefore makes exactly one `complete()` call and
asks for one object: `bullets`, `next_step`, `tone`.
`test_one_click_costs_exactly_one_model_call` is the assertion that keeps it that
way.

### Only `bullets` is required in the schema

A model that expresses "there is no next step" by omitting the member rather than
writing `null` has answered well enough. Requiring all three would spend a SECOND
budgeted request on a retry to be told the same thing. `clean_brief` reads a
missing member as null.

### Tone is evidence-bound, twice

Item 15 was changed by owner decision from standing auto-classification to
on-demand tone, for C6. Two things enforce "on demand" and "about the customer":

1. The prompt says tone MUST be null when the customer has sent no message.
2. The server overrides it anyway. `clean_brief` sets `tone` to `None` unless the
   timeline holds at least one INBOUND communication, whatever the model said.

The second one is the one that holds. A model that returns `"frustrated"` for a
record where only the agency has written gets `null`
(`test_tone_is_null_when_the_customer_has_written_nothing`).

### What "on demand" means in code

Nothing in this stage runs on page open, on a doc event or on a schedule. The
only caller of `crm.api.ai_brief.generate` is a button. Opening fifty leads costs
zero model calls; the frontend does not prefetch and the session-local cache is
filled only by a click.

---

## 2. What the model is shown, and the ceiling nobody wrote

`timeline_items` reads the record's timeline through `crm.api.activities` — the
app's one definition of what a record's timeline holds, and a function that
repeats the read-permission check itself.

**Version rows are excluded on purpose.** A change row reads "email changed to
ravi@example.com". Including version rows would mail the customer's address to a
model vendor through the back door, so they are not read at all
(`test_a_field_change_history_row_never_reaches_the_prompt`).

**There is a ceiling underneath ours.** `frappe.desk.form.load.get_docinfo` reads
the newest **21** communications (`load.py:108`) and no more. The brief therefore
summarises the same messages the agent can see on the record — which is the right
answer for a reading aid, since it cannot claim to know something the timeline
does not show — but a thread's 22nd-newest email is outside its view. This was
found by a test that expected 25 and got 21; the number is now asserted
(`test_the_timeline_itself_hands_over_at_most_twenty_one_emails`) so a Frappe
upgrade that changes it is noticed here rather than in a wrong summary.

### Two caps, guarding different things

* `ACTIVITY_LIMIT = 25` items keeps the prompt readable.
* `PAYLOAD_BYTES = 12_000` keeps the bill bounded. In ASCII the item cap binds
  first and the byte cap never fires. In Devanagari a character is three bytes,
  so an agency writing in Hindi would send three times the payload for the
  same-looking timeline — which is exactly the case the byte cap exists for, and
  what `test_the_excerpt_stays_under_the_byte_cap` uses.
* `INBOUND_FLOOR = 3` keeps the customer's own words in the excerpt when a burst
  of internal notes would otherwise push them out.

All three sit an order of magnitude under `crm.ai.client.MAX_REQUEST_BYTES`
(100 000), which is the last line of defence and not the working limit.

### WhatsApp messages are NOT sent — open question

The brief reads `crm.api.activities` data, which does not include WhatsApp
messages (they are fetched separately by `crm.api.whatsapp.get_whatsapp_messages`).
That follows the stage brief exactly, and it is the tighter privacy choice.

It is also a real limitation for this particular agency, whose main channel IS
WhatsApp: on the demo site there are 28 WhatsApp messages and zero email
Communications, so on most real records the tone line will be `null` and the
bullets will rest on notes, tasks and field context alone. **Whether to include
inbound WhatsApp messages in the brief is an owner decision, not a worker one**
— it widens what leaves the site to a channel the customer used casually. Flagged
rather than assumed either way.

---

## 3. Item 14 — the draft, and the link rule in both directions

`crm.api.ai_draft.generate` returns a BODY and nothing else: no subject, no
recipients, no attachments, no send.

The field whitelist for a lead **is** `crm.api.followup_engine.AI_LEAD_FIELDS`,
imported rather than copied, so the two answers to "what may a model see about a
lead" cannot drift apart (`test_the_lead_whitelist_is_the_follow_up_engines_own_list`
asserts identity, not equality).

The follow-up engine's link rule — a link survives only if the lead's own data
already holds it — is applied **twice**, which is the one thing this module adds
over that precedent:

* **Outbound, before the prompt is built.** A customer who pastes a competitor's
  booking link into an email has not agreed to that link being forwarded to a
  model vendor, and the agency's internal links are nobody else's business. The
  stage brief asked specifically for this and it is asserted directly:
  `test_a_link_the_record_does_not_hold_never_reaches_the_prompt`.
* **Inbound, on the answer.** So a model cannot put an invented URL in front of
  an agent who is about to mail it to a customer.

### Why the 2000-character cap is not in the schema

A schema `maxLength` would fail an over-long answer and spend a second budgeted
request re-asking for it. Cutting the tail costs nothing. The cap is enforced in
`clean_body`; the system prompt asks for under 200 words.

### Plain text, not HTML

The server returns plain text and the browser turns blank lines into paragraphs
(`bodyToHtml`, every character escaped). Handing a rich-text editor
model-written markup, inside a message an agent is about to send, is a trust
decision nobody needs to make.

### Undo, and what was actually verified

Item 14 asks for "immediate Undo". Nothing in this app implements one — the
editor's own history does. `frappe-ui`'s `RichTextKit` pushes the `UndoRedo`
extension unless `starterKit.undoRedo === false`
(`node_modules/frappe-ui/src/molecules/editor/kits.ts:298`), and `EmailEditor.vue`
passes only `{ paragraph: false }`. `insertContent` is one editor transaction, so
one Ctrl+Z takes the whole draft back out.

**Verified at code level and in the built bundle** (`undoRedo` is present in the
shipped asset), **not by a keystroke in a browser** — this worker has no browser
driver. A human should press Ctrl+Z once after inserting a draft during the
Stage 6 UX walkthrough. It is listed as an open item in §7.

### No streaming, and no pretending

`crm.ai.client` does not stream and master spec F6 forbids claiming it does. The
button shows a loading state and the text appears at once. No typing effect.

---

## 4. Side task A — the digest's quiet hours and per-user switch

Stage 3B open issue 2: the daily digest ignored quiet hours, and the app had no
per-user preference store at all, so there was nowhere to put an opt-out.

### Quiet hours: a schedule shift, not a cancellation

`send_daily_digest` moved from the `daily` scheduler event to `hourly`. `daily`
fires at the start of the day, which is inside the follow-up engine's default
quiet window (21:00 → 09:00) — that is what made the digest arrive inside quiet
hours in the first place.

The job now:

1. returns immediately while `in_digest_quiet_hours(now)` is true — reading the
   SAME `CRM Followup Settings` window the sequence engine defers sends into, via
   `crm.sequences.in_quiet_hours`, so a manager who moves quiet hours moves both
   at once;
2. works out WHO before WHAT, so on twenty-three of the day's ticks it returns
   before building a single aggregate;
3. delivers at the first tick after the window closes.

`in_digest_quiet_hours` **fails open** with a log entry. A settings row the job
cannot read is a configuration problem; a digest that then never arrives again is
a silent one, and this notification sends no message, spends no budget and
reaches no customer.

### At most once a day, with no new state

`has_digest_today` asks whether a CRM Notification whose message starts with the
digest's own leading words already exists for that user today. The digest's own
notification is the record of the digest having been sent, so there is nothing
extra to keep in step and nothing to clean up. Matching on the prefix is what
keeps it apart from an hourly follow-up NUDGE, which is also a WhatsApp
notification on the same user — the mirror image of the problem
`has_unread_followup` already solves.

### One bug this uncovered, and the one-word fix

`notify_user` drops a notification whose every field matches one that already
exists (Stage 3B open issue 3). Two quiet days running produce identical digest
text, so the second day's digest would have been silently swallowed — harmless
before, when the job ran once a day, and newly visible now that it runs hourly.

The digest's notification title now carries its date
(`WhatsApp daily digest · 19 Aug`). Two days' digests are therefore different
documents, and the date is useful in a notification list anyway. `send_daily_digest`
also now counts what actually exists rather than how many times it called
`notify_user`, so its return value is true.

### `CRM User Preference` — the store, and what it is not

A new doctype: `user`, `preference_key`, `preference_value`, plus
`preference_id` = `user::key` with a unique index. Frappe has no composite unique
constraint in a doctype definition; `CRM Suppression` solves the same problem the
same way.

**Field naming deviates from the brief.** The brief said `key` and `value`. `key`
is a reserved word in MariaDB, so both are prefixed — naming one and not the
other would be worse.

**It is a closed registry, not a key-value store.** `PREFERENCES` in the
controller lists every accepted key; `validate` refuses anything else and so does
the whitelisted setter. A store that accepts any key is a place for a client to
park arbitrary data under a user's name, and it is also a store that a settings
screen cannot enumerate.

**Absence means default.** A user with no row gets the registry's default, so a
new preference needs no backfill patch, and removing one leaves rows that nothing
reads. `daily_digest` defaults to ON: a manager who has never opened the setting
keeps the digest they had before it existed.

**Row-level scope** is enforced by `get_permission_query_conditions` and
`has_permission` in `crm/hooks.py`: everyone sees their own rows, System Manager
sees all. Neither whitelisted endpoint takes a user parameter.

**The next per-user setting reuses this** — that is the point of building it for
one switch. `crm/reminders.py:40-45` records the same gap for the task-reminder
offset and names `reminder_offset_minutes` as the single place a future per-user
override would hook in.

### Where the switch lives in the UI

Settings → Preferences, a new "Notifications" section. Not a new page (UX §2.16),
and not in AI & Follow-ups: that page holds the AGENCY's configuration, and this
is one person's choice. The list is built from the server's registry, so the next
preference appears there without a second edit. Each switch saves on change.

---

## 5. Side task B — the AI budget lock, measured

Stage 1B flag 1. The budget claim is one atomic `UPDATE` on the settings row,
made BEFORE the network call — correct, and it holds a row lock until the
CALLER's transaction ends, which is after the provider answers. Invisible while
every AI call came from the scheduler. Stage 4 makes AI interactive.

### The measurement

Two `bench execute` processes against the demo site, so there are two real
database connections (a `FrappeTestCase` has one and cannot observe a row lock).
Process A claims and holds for 8 seconds; process B starts ~3 seconds later and
reports how long ITS claim took. Both use the real `claim_request`.

```
# in-transaction claim (the Stage 1B behaviour)
holder: {"role": "holder", "claimed": true, "claim_seconds": 0.229}
waiter: {"role": "waiter", "claimed": true, "claim_seconds": 5.132}

# isolated claim (this stage)
holder: {"role": "holder", "claimed": true, "claim_seconds": 0.218}
waiter: {"role": "waiter", "claimed": true, "claim_seconds": 0.215}
```

**5.132 seconds is the whole remaining hold.** The second agent waited out the
first agent's provider call, exactly and entirely. With the claim isolated, the
same test measures 0.215 s — no wait at all. The contention is real and it is
user-visible: two people pressing Summarize at the same moment serialise.

Budget counter around the measurement: **19 before, 21 after** (the isolated run
commits two claims by design), restored to **19** with
`crm.tests.test_ai_budget.set_budget_counter`. No provider call was made by the
measurement.

### What changed

`crm.ai.client.complete(..., isolate_budget_claim=False)`. When true,
`reserve_request` commits the claim immediately and releases the lock before the
request leaves.

**The code path is split, not switched.** The default is unchanged, so the
follow-up engine's scheduler path keeps its claim inside its own transaction,
where it belongs next to the bookkeeping of the message it is about to send.
Only `crm.api.ai_brief.generate` and `crm.api.ai_draft.generate` pass `True`, and
both read records and write none — which is the rule for passing it, stated in
the docstring and named against both callers.

Isolating also fixes the direction the failure leans. An in-transaction claim is
undone by a rollback, so a request that WAS sent could end up uncounted. A
committed claim survives, and the worst case is the over-count by one the
function was designed around.

The commit goes through a `commit()` seam, the same pattern
`crm.api.followup_engine` uses, so the tests can neutralise it rather than the
database. A commit that fails is logged and does not cost the caller their
answer: the slot is claimed either way and only the lock hold time was at stake.

### One thing NOT fixed, and it is worth knowing

`claim_request` catches every exception and **fails open** (returns True) with a
log entry — Stage 1B's deliberate choice, that an AI feature stopping because a
counter row is unreadable is worse than a budget overrunning while somebody fixes
it. A lock-wait TIMEOUT is one of those exceptions. Under the old behaviour, a
sufficiently long provider call plus a queue of waiters could therefore have
produced uncounted requests rather than an error. Isolating the claim removes the
wait that would cause it. The fail-open policy itself is unchanged and is not
this stage's to change.

---

## 6. Deviations from the stage brief

1. **`CRM User Preference` field names** are `preference_key` / `preference_value`,
   not `key` / `value`. `key` is reserved in MariaDB. §4.
2. **"Save as note" has no endpoint.** It is a `frappe.client.insert` of an FCRM
   Note from the browser, through the same API the Note modal already uses. The
   brief implied a backend action; adding one would have meant a new whitelisted
   endpoint and a new permission-matrix row for something the framework already
   does correctly. The HTML is built by `briefToNoteHtml`, which escapes every
   fragment — a note is rendered as HTML in the timeline and this text came from
   a model.
3. **"Create task" opens the task modal prefilled** rather than creating the task
   outright. The brief allows either ("creates via the existing task API — never
   automatic"); the modal is the stronger reading of C6, because the agent sees
   and confirms what is about to be written.
4. **The Brief sparkle is on the Activity tab only.** The Emails tab has its own
   primary action and the composer has its own sparkle (item 14). Putting a
   second one there would break "one sparkle slot per surface" (§2.14).
5. **`due_hint` is an enum, not a date.** The model does not know today's date,
   the agency's calendar or the agent's workload, and a date it invents reads as
   a commitment. `today` / `tomorrow` / `this_week` / `next_week` map to +0 / +1 /
   +3 / +7 days locally, as a starting point the agent edits in the modal.
6. **No new feature flag.** AI features are gated by `CRM AI Settings.enabled`,
   which is default-off on a fresh site and is a real switch a manager already
   uses. A second flag over the same thing would be a second place to look. The
   digest change is a behaviour change to an existing scheduled job, not a new
   automation, so C5 does not apply to it either.

---

## 7. Open issues handed on

1. **Ctrl+Z after a draft insert has not been pressed by a human.** Evidence is
   code-level plus the built bundle (§3). One keystroke during the Stage 6 UX
   walkthrough closes it.
2. **WhatsApp messages are outside the brief** (§2). Owner decision.
3. **The demo site has no email Communications at all** (28 WhatsApp messages, 0
   Communications). The Brief and the Draft both work live against it — see the
   verification record — but neither has ever run against a real email thread on
   this site, so the "last 10 messages" path and the tone-from-inbound path are
   covered by tests and not by the demo data. Seeding a two-sided email thread on
   one lead would make the Stage 6 walkthrough show these features properly.
4. **`crm.api.activities.get_activities` is called twice per brief** — once by the
   page and once by `generate`. It is not cheap (`get_docinfo` plus several
   queries). A brief is on-demand and rare, so this is not worth a cache today;
   if a later stage makes the brief automatic — it must not — this is the first
   thing that breaks.
5. **`notify_user`'s exact-tuple dedup is still there** (Stage 3B issue 3). §4
   works around it for the digest by dating the title. Any other notification
   whose text can repeat has the same trap.
6. **The container's frontend was redeployed by this stage.** `crm/www/crm.html`
   and `crm/public/frontend` were pushed TOGETHER from one build, and the page
   plus its hashed assets were re-checked (200). Never push one without the
   other.
