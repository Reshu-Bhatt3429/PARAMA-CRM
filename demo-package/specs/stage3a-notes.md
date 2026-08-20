# Stage 3A — deviations, decisions and open questions

Scope: master spec §5 items 4 (web-form auto-response), 5 (send later),
19 (email open state) and 25 (quote PDF). Branch `feat/feature-expansion`.
Nothing is committed by this stage.

This file records the places where the build differs from the brief, the entries
another worker has to make in files this stage may not touch, and the things a
reviewer should know. Everything not listed here was built as specified.

---

## 1. Entries needed in files this stage may not touch

`crm/hooks.py` is owned by the Stage 3B worker this stage (single-owner rule).
Two entries belong there. **Neither is required for the feature to work** —
both have a working fallback — but both should land before the demo.

### 1a. The quote-link expiry sweep (REQUIRED before the demo)

Without it, a tokenised quote link never expires and its private PDF is never
deleted. `crm.api.quote.cleanup_quote_links` is written, tested
(`crm/tests/test_quote.py::TestLink::test_expiry_retires_the_link_and_deletes_the_file`)
and never raises.

The itinerary's existing hourly entry does NOT cover it. That entry sweeps
temporary PUBLIC files attached to `CRM Itinerary`; a quote's file is private and
attached to `CRM Deal`, and the link row it belongs to is a different doctype
with a different lifetime. The shared helper (`crm.document_links`) is what the
two features have in common, not the schedule.

```diff
--- a/crm/hooks.py
+++ b/crm/hooks.py
@@ scheduler_events["hourly"]
 		"crm.api.itinerary.cleanup_public_itinerary_pdfs",
+		# Item 25: retire expired quote links and delete the private PDF each one
+		# held. Never raises. Not covered by the itinerary sweep above: that one
+		# removes temporary PUBLIC files on CRM Itinerary, while a quote's file is
+		# private, lives on CRM Deal, and dies with its CRM Document Link row.
+		"crm.api.quote.cleanup_quote_links",
 		# Behind `outbound_engine_enabled`, default OFF. While the flag is off these
 		# return without reading a single job row.
 		"crm.outbound.process_scheduled_jobs",
```

### 1b. The quote print format on migrate (recommended)

```diff
--- a/crm/hooks.py
+++ b/crm/hooks.py
@@ after_migrate
 	"crm.api.itinerary.install_print_format",
+	# Item 25. Same contract as the itinerary's: the HTML lives in a file so it
+	# is reviewable in git, and the Print Format row is rewritten only when that
+	# file changed, so an administrator's own edit survives a migrate.
+	"crm.api.quote.install_quote_print_format",
 	"crm.domain_enrichment.install.seed_default_rules_and_mappings",
```

Fallback if this is skipped: `crm.document_links.ensure_print_format` installs
the format on the first render, exactly as the itinerary does. The cost of
skipping it is that a later edit to `travel_quote_a4.html` does not reach an
existing site until the Print Format row is deleted.

### 1c. Deployment must turn the outbound engine on

Send Later SCHEDULES with the flag off; it does not DELIVER. The hourly sweep
`crm.outbound.process_scheduled_jobs` is gated on `outbound_engine_enabled`,
which is default OFF by design (master spec C5) and whose default this stage did
not change.

**Deploy step:** switch `outbound_engine_enabled` on in FCRM Settings, or a
scheduled email sits in `Scheduled` for ever and the agent is never told why.
"Send now" works with the flag off, because it does not go through the sweep.

---

## 2. Deviations from the brief

### 2a. `crm.outbound.get_adapter` was NOT made to auto-load the email adapter

**Brief:** "the existing hourly sweep delivers via `crm.api.email.send_email`
when due".

**Built:** the sweep calls a new `crm.outbound.load_adapter_modules()` first,
which runs the named registrar `crm.api.email:register_adapters`. The lookup
itself (`get_adapter`) is unchanged, and `crm.api.email` has NO import-time side
effect.

**Why it matters:** the obvious implementation — register the adapter when
`crm.api.email` is imported, or lazily inside `get_adapter` — breaks
`crm/tests/test_outbound.py::TestDelivery::test_a_job_with_no_adapter_fails_and_sends_nothing`,
which is Stage 1's proof that the foundation is provably send-free. It also
breaks `test_a_due_job_is_claimed_and_run`, which registers a recorder and
expects it to survive the sweep. `register_adapters` therefore does not
overwrite an adapter already bound to the channel; it is the default for the
Email channel, not a claim on it. Both Stage-1 tests still pass unchanged.

### 2b. A scheduled email is ONE outbound recipient row, not one per address

The outbound machine keys idempotency on (job, channel, normalised address). An
email with three people in To is one message, so the job carries one recipient
row — the first To address — and the rest ride in the payload.

The consequence, stated plainly: the machine's pre-send suppression check covers
the primary address only. The other addresses are re-checked at send time by
`crm.api.email.send_email`, which is the same check the composer performs, so no
send path is unchecked. What differs is bookkeeping: a suppressed CC shows up in
the send's `suppressed` list rather than as a `Suppressed` recipient row.

The alternative — one row per address, one adapter call each — would produce
three separate Communications and three separate emails for one message.

### 2c. Item 4's hook is the Lead/Deal controller, not `crm/hooks.py`

`queue_auto_response` is called from `CRM Lead.after_insert` and
`CRM Deal.after_insert`, mirroring the existing `enrich_form_submission` call in
their `before_insert`. A `doc_events` entry in `crm/hooks.py` would have been
equivalent, and this stage may not edit that file. The controller call is the
established precedent for exactly this feature's sibling.

`after_insert` and not `before_insert`: the spec asks for the
successful-submission point, and before the insert there is no submission — a
validation failure after a `before_insert` send would leave a stranger holding a
receipt for an enquiry that does not exist.

### 2d. The auto-response is its own doctype, not custom fields on `Web Form`

**Brief:** a per-form setting.

**Built:** `CRM Form Auto Response`, named after the Web Form (one row per form,
enforced by the document name), plus `CRM Form Auto Response Log` for the
idempotency claim.

**Why:** `crm.install.add_web_form_custom_fields` guards itself with
`if meta.has_field("crm_published") and meta.has_field("crm_hidden_defaults"): return`,
so adding a third custom field there would silently never install on any site
that already migrated. Master spec F9 also asks for `custom_parama_*` namespacing
on core doctypes, which conflicts with that file's existing `crm_*` convention.
A separate doctype avoids both problems and needs no patch.

### 2e. Quote totals are summed from the product rows, not read off the deal

`CRM Deal.total` and `.net_total` are maintained by a CLIENT script
(`crm/fcrm/doctype/crm_deal/crm_deal.js`). They are filled in only when a human
edited the products grid in a browser. **Verified on the demo site:** a deal
inserted through the API with two products worth ₹1,34,000 stored `total = None`.

A customer-facing quote whose lines add to one figure and whose total says
another is worse than no quote, so `crm.api.quote.quote_totals` sums the rows it
prints and falls back to the stored fields only for a deal with no products.
The same numbers fill the preview modal and the PDF, from the same function.

### 2f. `get_form_config` now reads `placeholder` with `.get()`

`placeholder` is a `Web Form Field` column on frappe v16 and NOT on the v15 in
this container, and attribute access on a column that is not in the meta raises
`AttributeError`. This was pre-existing and invisible because
`crm/tests/test_form_api.py` is one of the 47 modules that do not collect here.
One-character-class fix in an owned file; the value round-trips on v16 exactly as
before and is simply always empty on v15.

### 2g. `CRM Notification.type` gained an `Email` option

Reply-cancel notifies the author through the existing `notify_user` helper, whose
`type` is a `Select` with `Mention / Task / Assignment / WhatsApp`. One option
appended. Additive; the only type-dependent frontend code is the WhatsApp icon in
`Notifications.vue` / `MobileNotification.vue`, which falls through to the default
for everything else.

---

## 3. Files this stage touched outside its own new modules

| File | Change | Why |
| --- | --- | --- |
| `crm/outbound.py` | `+ADAPTER_REGISTRARS`, `+load_adapter_modules()`, one call in `process_scheduled_jobs` | §2a. Stage-1 tests unchanged and passing (46/46) |
| `crm/utils/__init__.py` | one call to `crm.api.email.handle_inbound_reply` in `on_communication_insert` | The only seam for reply-cancel that is not `crm/hooks.py` |
| `crm/fcrm/doctype/crm_lead/crm_lead.py`, `crm_deal.py` | one call in `after_insert` | §2c |
| `crm/fcrm/doctype/crm_notification/crm_notification.json` | one Select option | §2g |
| `crm/api/itinerary.py` | PDF helpers now delegate to `crm.document_links` | The extraction the brief asks for. Public names, signatures and behaviour unchanged; its 112 tests pass |

---

## 4. Things a reviewer should know

### The quote link is a PRIVATE file behind a token, not a public file

This is the substantive upgrade the master spec asks for. The itinerary send
writes a temporary PUBLIC copy of its PDF so Meta can fetch it, and sweeps it two
hours later; while it exists, anyone who learns the URL can read it and nothing
records who did. A quote keeps its PDF private and hands Meta the tokenised route
instead. Meta fetches that URL like any other, and its fetch is what lands in the
view log flagged as the platform's.

**Verified over real HTTP on the running site**, not only in tests:

```
$ curl -A 'facebookexternalhit/1.1' '…/api/method/crm.api.quote.view?token=…'
HTTP/1.1 200 OK
Content-Type: application/pdf
Content-Disposition: inline; filename=Quote-CRM-DEAL-2026-00010-v1.pdf
Content-Length: 22292

$ curl -A 'Mozilla/5.0 Chrome/120' '…?token=…'
200 application/pdf 22292

$ curl '…?token=deadbeef'
403  {"exc_type":"PermissionError", … "This link has expired or is no longer valid."}
```

and the log that produced:

```
name         is_platform_fetch  ua                        viewed_at
sjctkfeihp   1                  facebookexternalhit/1.1   2026-08-19 01:50:52
sjgqvp2etu   0                  Mozilla/5.0 Chrome/120    2026-08-19 01:50:52

name         view_count  first_viewed_at        platform_fetch_at
sh0h92geba   1           …:52.818415            …:52.381471
```

### The bot rule needs BOTH halves

`classify_fetch` calls a fetch the platform's when the user agent looks like a
crawler AND it is the first fetch on that link. The user agent alone would write
off for ever a customer who previews the link inside a chat app; "first fetch"
alone would discard the customer's own first read whenever the platform never
came.

### The sweep is HOURLY, so "18:00" means "the first sweep at or after 18:00"

The Send Later popover says so in as many words. Do not let a later change
promise the minute; the engine does not offer one.

### Merge fields are substitution, not a template engine

The reply's template is written by a manager, but the values come from whoever
filled in a public form. `render_merge` replaces `{{ token }}` from a fixed
allowlist and HTML-escapes the value; there is nothing to evaluate, and nothing
outside `AUTO_RESPONSE_MERGE_FIELDS` is reachable. `{{ frappe.session.user }}` in
a template renders as itself.

The editor's pill is a TipTap atom node whose rendered HTML CONTAINS the literal
`{{ token }}`, so the server needs no conversion step:

```html
<span class="merge-field" data-merge-field="first_name">{{ first_name }}</span>
```

The wrapper survives into the sent email and is harmless. **Verified live:** a
guest form POST produced `subject = "Thanks Second"` and
`content = '<p>Hi <span class="merge-field" data-merge-field="first_name">Second</span>, …'`.

### The test send cannot be pointed at anybody else

`send_auto_response_test` takes ONE argument, the form name. The recipient is
always `frappe.session.user`. An endpoint that sent an arbitrary body to an
arbitrary address would be an open relay wearing the CRM's return address.

### Item 19 replaced a badge rather than adding one

`EmailArea.vue` used to print the raw `delivery_status` word in a coloured
`Badge` on every message. That was a badge shelf (spec §2.13) and it answered a
question nobody asks. It is now one gray tick for sent, two plus "Opened · 2
hours ago" once the read receipt lands, and the exact timestamp on hover. No new
tracking: both fields were already in the payload.

`read_by_recipient_on` was NOT: `frappe.desk.form.load.get_communication_data`
returns `read_by_recipient` and not the timestamp, so
`crm.api.activities.read_receipt_times` fetches it in one extra query for the
handful of messages that were actually opened.

---

## 5. Open issues handed on

1. **`crm/hooks.py` entries 1a and 1b are not made.** 1a matters: without it a
   quote link never expires. Both diffs are above.
2. **`outbound_engine_enabled` must be switched on at deploy** or scheduled
   emails never leave. Send-now is unaffected.
3. **The agency postal address is a placeholder on the quote PDF.** The app
   stores no such field anywhere. The template prints
   `[ Add your agency address in Settings ]` so the gap is visible before a quote
   reaches a customer rather than after. The natural home is the Company Profile
   settings section the invoice module (item 29) will add in Stage 5.
4. **The quote's default terms are a module constant** (`crm.api.quote.DEFAULT_TERMS`),
   editable per quote in the modal but not site-wide. `fcrm_settings.json` is
   owned by another worker this stage. One `Small Text` field and one reader in
   `terms_lines` would make it a setting.
5. **Reply-cancel matches on the Communication link first, the Message-ID
   second.** The framework's receiver resolves the `In-Reply-To` header into a
   Communication link and does not keep the raw header, so the id is read back
   off the Communication the reply points at. A reply that arrives with no
   `in_reply_to` at all matches nothing and cancels nothing — which is the safe
   direction, but it means a customer replying from a client that strips the
   header will still receive the scheduled follow-up.
6. **A scheduled email is not editable, only cancellable.** The brief asked for
   Send now and Cancel and that is what is built; "edit and reschedule" is
   cancel-then-recompose today.
7. **Still no frontend component tests in this repo** (Stage 2B open issue 4).
   Every testable decision is in a pure module — `utils/emailStatus.js`,
   `utils/sendLater.js` — and tested there. The `.vue` wiring is covered by the
   production build and by the live checks recorded in `stage1-verification.md`.
8. **The frappe v15 vs v16 mismatch stands** (Stage 1A open issue 1). Seven
   upstream-style modules still do not collect in this container, including
   `crm/tests/test_form_api.py`, which is why §2f went unnoticed until now.
