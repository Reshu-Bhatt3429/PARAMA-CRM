# Stage 5.3 — Invoice module (item 29), backend — implementation notes

Built against `demo-package/specs/design-29-invoices.md` (approved 2026-08-19)
and master spec §5 item 29, §2, §3 and §7.

Scope: **backend only**. No frontend file is touched. `Field.vue` and `Grid.vue`
are not touched (master spec D4). Nothing is committed; every change sits in the
working tree of `feat/feature-expansion`.

Everything is behind `invoices_enabled`, default OFF. The reminder ladder is
behind a second flag, `invoice_reminders_enabled`, also default OFF.

---

## What was built

**Doctypes**

| Doctype | Kind | What it is |
| --- | --- | --- |
| `CRM Invoice` | main, `autoname: hash` | The record. `invoice_number` is its own locked column with a UNIQUE index. |
| `CRM Invoice Item` | child | Description, SAC, qty, rate, tax rate, amount (recomputed). |
| `CRM Invoice Payment` | child | Date, amount, mode, reference, note, schedule row, recorded by/at. Append-only. |
| `CRM Invoice Schedule` | child | Label, due date, amount, settled, paused. One reminder ladder per row. |
| `CRM Invoice Status Log` | child | Send-log style status history: when, from, to, by, note. |
| `CRM SAC Code` | master, `autoname: field:code` | Admin-editable placeholders, each carrying a "verify with your CA" note. |
| `CRM Company Profile` | Single | Already built before this stage. Unchanged. |

**Code**

| Piece | Where |
| --- | --- |
| Arithmetic, numbering, refusals (pre-existing) | `crm/invoicing.py` — two corrections, see §Deviations |
| Controller: recompute, refuse, status, finalize, void, record payment | `crm/fcrm/doctype/crm_invoice/crm_invoice.py` |
| Row scope + role grants | same file (`get_invoice_permission_query_conditions`, `has_invoice_permission`, `add_invoice_roles`) |
| Endpoints | `crm/api/invoices.py` (14 whitelisted) |
| Reminder ladders | `crm/invoice_reminders.py` |
| Print format | `crm/templates/print_formats/gst_invoice_a4.html` + `crm.api.invoices.install_print_format` |
| SAC seed | `crm/fcrm/doctype/crm_sac_code/crm_sac_code.py::seed_sac_codes` |
| Flags (both halves of the registry contract) | `crm/feature_flags.py` + `crm/fcrm/doctype/fcrm_settings/fcrm_settings.json` |
| Hooks: permissions, scheduler, after_migrate | `crm/hooks.py` |
| QR dependency | `pyproject.toml` — `segno==1.6.6` |
| Tests | `crm/tests/test_invoicing.py` (69), `crm/tests/test_invoices.py` (120) |

---

## The e-invoicing threshold — the account `crm/invoicing.py` promises

`crm/invoicing.py` says, in the comment block above `EINVOICE_THRESHOLD_RUPEES`,
that the same account is written here in prose. This is it.

**The decision.** This module generates NO IRN, produces no e-invoice QR, and
talks to no Invoice Registration Portal. That is safe only for an agency whose
aggregate annual turnover is below the e-invoicing threshold. So the threshold is
STATED TO THE USER in Company Profile rather than assumed on their behalf, and
the field that holds it is editable.

**The figure.** The last figure this project could establish is
**Rs 5,00,00,000 (Rs 5 crore)**, set by Notification No. 10/2023 – Central Tax
dated 10 May 2023, with effect from 1 August 2023.

**Its verification status: UNCONFIRMED for 2026.** The design note required the
worker to re-check the live 2026 threshold once and record it. The re-check was
attempted on **2026-08-19 and FAILED**. What was tried and what each returned:

| Source | Result |
| --- | --- |
| WebSearch | Session budget exhausted; zero results |
| `https://taxinformation.cbic.gov.in/api/notification/search` | HTTP 401, "Full authentication is required to access this resource" |
| `https://taxinformation.cbic.gov.in` (notification archive) | TLS error "unable to verify the first certificate"; also a JavaScript app a fetcher cannot render |
| `https://einvoice.gst.gov.in/einvoice/laws-and-regulations/notifications` | HTTP 302 to `/error/notfound` |
| `https://einvoice1.gst.gov.in/Others/Notifications` | Reachable; notification table empty without JavaScript |
| `https://cbic-gst.gov.in/` | Reachable; "What's New" showed no e-invoicing threshold item — but it is not a notification archive, so its silence proves nothing |
| cleartax.in, taxguru.in, irisgst.com, mastersindia.co, indiafilings.com, zoho.com/in/books | 404, 403 or 503 on every article slug tried |

**What follows from that.** `EINVOICE_THRESHOLD_RUPEES` is a DEFAULT, not a
verified fact. `EINVOICE_THRESHOLD_VERIFIED` is `False` in code, and
`crm/tests/test_invoicing.py::TestEinvoiceAccount` asserts that it stays `False`
until somebody actually verifies it. The field it seeds,
`CRM Company Profile.einvoice_threshold`, is editable, and the read-only note
beside it says what the module does not do and names the source so a reader can
judge how old the figure is:

> E-invoicing applies above ₹ 5,00,00,000.00 turnover — this module does not
> generate IRN. Threshold last recorded from Notification No. 10/2023 - Central
> Tax, 10 May 2023 (w.e.f. 1 Aug 2023); confirm the current figure with your CA.

**Do not present this number to a user as verified.** An agency near the line
must confirm the current threshold with their CA. Whoever next has working web
access should re-run the check and update three places together: the constant,
`EINVOICE_THRESHOLD_CHECKED_ON`, and this table.

---

## The decisions worth knowing

### 1. Every figure is recomputed on every save; no client total is ever trusted

`CRMInvoice.validate` calls `crm.invoicing.compute_totals` from the ITEM ROWS and
overwrites `taxable_total`, the tax split, `tax_total`, `rounding_adjustment` and
`grand_total`. Each line's `amount` is recomputed from qty × rate as well. A
browser that posts its own figures has them replaced, not honoured —
`test_a_client_supplied_total_is_overwritten_not_honoured` proves it.

This is the Stage 3A finding applied again: `CRM Deal.total` is maintained by a
CLIENT script, so a deal made by the API, an import or the seeder carries
products worth lakhs and a stored total of zero. `items_from_deal` therefore sums
the product rows and bills the NET amount per row.

Section 170 order of operations is the rule, and it is held in place by
`test_lines_are_summed_before_the_tax_is_taken`: sum the lines in paise, take the
tax on that sum per rate band, round only at the end. Rounding each line to the
rupee first gives a different — and wrong — answer.

### 2. The number locks, and the lock freezes a documented list

While `number_locked_at` is empty the invoice is a draft: it may be edited and
renumbered freely. The moment `finalize` allocates a number, `LOCKED_FIELDS`
(number, the three dates, mode, reverse charge, place of supply, supplier state
code, and the whole recipient block) and the ITEM ROWS become immutable, for
every user including a manager. The refusal names the fields that changed.

What stays editable after the lock, deliberately: `due_date`, `terms`, the
payment schedule, `reminders_paused`, and the payments table (append-only). An
agency renegotiates instalments on an issued invoice; it does not renegotiate the
taxable value.

### 3. The allocator is a loop around a unique index — and it was broken

`crm.invoicing.allocate_number` builds `<prefix><FY>/<serial>`, writes it with
`doc.db_set`, and retries on collision. The UNIQUE index on
`tabCRM Invoice.invoice_number` is the authority; verified live on the database
(`Non_unique = 0`, `Null = YES`).

An empty number is stored as **NULL, not `""`**. MariaDB allows many NULLs in a
unique index and exactly one empty string, so two coexisting drafts would
otherwise collide on a field neither had set —
`test_two_drafts_can_coexist_without_colliding_on_the_unique_index`.

**The correction.** As written, the retry loop caught
`frappe.UniqueValidationError` and `frappe.DuplicateEntryError`. A live probe
showed `doc.db_set` raises neither: `db_set` goes through `frappe.db.set_value`,
and only the DOCUMENT save path (`frappe.model.base_document`) maps the driver's
error onto a framework exception. A `set_value` that loses the unique index
raises `pymysql.err.IntegrityError` unchanged, so the raw driver error went
straight past the `except` and the allocator crashed on the one collision it
exists to survive. Fixed with `crm.invoicing.is_duplicate_entry`, which asks
`frappe.db.is_unique_key_violation` rather than matching on a message. Anything
that is not a collision is re-raised rather than spending one of the eight
attempts. `test_the_allocator_loses_the_race_once_and_takes_the_next_serial` is
the regression test: `highest_serial` is stubbed to return the STALE value first,
which is exactly what the loser of a real race sees.

### 4. Payments only ever ADD

`validate_payments` compares the payment table against `get_doc_before_save`.
An existing row that changed is refused; an existing row that disappeared is
refused. A correction is a NEGATIVE row with a MANDATORY note. Status then comes
from `crm.invoicing.status_for`, so a correction moves a Paid invoice back to
Partially Paid by itself.

Over-payment is refused, and `record_payment` defaults the amount to the
remaining balance — the only default that cannot be wrong by a paisa.

### 5. Void is terminal, manager-only, and nothing is ever deleted

`CRMInvoice.on_trash` refuses for every user including Administrator, and the
doctype grants `delete` to nobody — two locks, not one. Void keeps the number,
the amounts and the history, requires a reason, and excludes the invoice from all
three dashboard tiles. A voided invoice takes no payments and can never be issued
again.

Manager-only is checked TWICE: in `crm.api.invoices.void_invoice` and again
inside `CRMInvoice.void`, so a future caller that skips the endpoint is still
refused.

### 6. One ladder per SCHEDULE ROW, not per invoice

A travel invoice is a deposit and a balance, and the two are chased separately. A
customer who has paid the deposit must stop hearing about the deposit and must
still hear about the balance. So the ladder belongs to the schedule row:
`CRMInvoice.settle_schedule_rows` marks the row a payment named, and
`crm.invoice_reminders.schedule_rows` skips settled and paused rows. That single
flag is what makes acceptance criterion 5 true, and
`test_paying_the_deposit_stops_only_the_deposit_ladder` is the proof.

Each step (due date, +7, +14) is its own `CRM Outbound Job` with its own
idempotency key `invoice-<invoice>-row-<row>-day-<n>`, so a step is sent at most
once whatever the scheduler does. The sweep CREATES jobs; `crm.outbound` delivers
them, which buys the claim-commit-send guarantee, the row locks and the
delivery-state read-back for free.

Three switches gate a reminder, all OFF by default:
`invoice_reminders_enabled` (no ladder exists), `invoices_enabled` (the module is
off entirely), `outbound_engine_enabled` (nothing is delivered).

The reminder body offers a link ONLY when one is already live —
`live_link_url` reads, it never mints, and the sweep renders nothing. See
§Deviations 5 for the full contract and its stated cost.

Quiet hours are the FOLLOW-UP ENGINE'S window (`CRM Followup Settings`), reused
rather than reinvented: an agency configures "do not message customers at night"
once. Inside the window the job is SCHEDULED for the moment the window closes
rather than skipped — skipping would lose the step, because its moment has passed
and the next sweep would find it outside the catch-up window.

The catch-up window is 7 days. Switching the flag on must not replay a year of
history into a customer's inbox — the same reasoning as
`crm.reminders.LOOKBACK_MINUTES`.

### 7. Row scope is the deal's scope

An invoice has no scope of its own. `get_invoice_permission_query_conditions`
wraps the deal's org-hierarchy conditions in a subquery on `CRM Invoice.deal`,
and `has_invoice_permission` defers to `has_deal_permission` — the same shape
`crm.api.itinerary` uses for its lead. `get_tiles` aggregates over
`frappe.get_list`, which applies those conditions; a raw query or the query
builder would return the whole table.

`collected_this_month` takes its filters as a LIST of conditions, not a dict: a
dict cannot hold two bounds on one column, the second key silently replaces the
first, and the tile would then report every payment ever recorded as this
month's.

### 8. The QR is generated at render time, from the remaining balance

`crm.invoicing.upi_qr_data_uri` is called inside `decorate`, never stored, so a
customer who re-downloads the PDF after paying the deposit is asked for the
balance and not for the whole invoice again. `segno` is pure Python with no
dependencies of its own and no system libraries, so it cannot pull a C toolchain
into a self-hosted install, and nothing touches the network.

---

## Migration and downgrade

- **New doctypes:** `CRM Invoice`, `CRM Invoice Item`, `CRM Invoice Payment`,
  `CRM Invoice Schedule`, `CRM Invoice Status Log`, `CRM SAC Code`.
  (`CRM Company Profile` was added just before this stage.)
- **Indexes:** UNIQUE on `CRM Invoice.invoice_number` (verified live:
  `Non_unique = 0`); UNIQUE on `CRM SAC Code.code`; search indexes on
  `CRM Invoice.deal`.
- **New fields on existing doctypes:** `FCRM Settings.invoices_enabled` and
  `FCRM Settings.invoice_reminders_enabled`, both `Check`, both default `"0"`.
- **One option added to an existing Select:** `CRM Document Link.purpose` gains
  `Invoice`, so an invoice link is a different kind of token from a quote link.
  `crm.api.invoices.view` checks the purpose, so a quote token cannot be redeemed
  on the invoice route and the reverse is also true.
- **New print format:** `GST Invoice A4`, installed by `after_migrate` from
  `crm/templates/print_formats/gst_invoice_a4.html`, rewritten only when the file
  changed, so an administrator's own edit survives a migrate.
- **Seed:** six placeholder `CRM SAC Code` rows, idempotent by code. A row an
  administrator edited, renamed or disabled is never overwritten.
- **New Python dependency:** `segno==1.6.6`, pinned exactly. A QR encoder that
  changed behaviour would change a payment instruction. Version verified against
  the live package index on 2026-08-19 (`pip index versions segno` → 1.6.6 is the
  newest). Installed into the container env for this stage's test run; a real
  deployment gets it from `pyproject.toml` via `bench setup requirements`.
- **No patch was needed.** There is nothing to backfill: the six doctypes are new
  and empty, and OFF is the correct value of both flags for every existing site.
  No upstream core schema is touched.
- **Downgrade:** turn `invoices_enabled` off. Every endpoint then refuses with a
  permission error and the public link route answers exactly like a dead token.
  Removing the app code leaves six unused tables, two unused settings fields, one
  unused Select option and one unused print format; no other feature reads any of
  them. Existing invoices survive and resume exactly where they were if the flag
  is turned back on. Reminder jobs already in `CRM Outbound Job` are never
  claimed while `outbound_engine_enabled` is off.

---

## Ops

- **Before turning `invoices_enabled` on for a real agency:** fill Company
  Profile completely. `finalize` refuses while legal name, address, state code or
  GSTIN is missing, and it names the missing field. Check the SAC codes against
  the agency's CA — they ship as placeholders and every row says so.
- **Before turning `invoice_reminders_enabled` on:** the same deliverability
  point as Stage 5.1 applies. Reminder mail leaves through the agency's own
  outgoing Email Account; publish an SPF record and enable DKIM for that domain
  first. Also check that no long-overdue invoice is about to be chased: the
  7-day catch-up window bounds it, but a genuinely overdue instalment inside that
  window WILL be mailed on the first sweep.
- **The invoice number prefix is validated at the moment it is typed**, not at
  the moment an invoice is issued. `CRMCompanyProfile.validate` probes
  `<prefix><FY>/9999` against Rule 46(b), so a prefix that could never produce a
  legal number is refused by the Save button.
- **Link expiry:** invoice links live 60 days (a quote's live 14). A customer
  opens an invoice again when they pay the balance, which on a travel booking is
  weeks after the deposit. `crm.api.invoices.cleanup_invoice_links` runs hourly
  and deletes the private PDF each expired link held.
- **A job left `Scheduled` while `outbound_engine_enabled` is off is not a
  fault.** It is the flag doing its job. Counting jobs in `Scheduled` older than a
  day is the way to notice that the engine was never switched on.

---

## Open issues and deviations

1. **`crm/invoicing.py` was corrected in two places**, against the instruction to
   wire it rather than rewrite it. Both are stated here rather than done quietly.
   (a) `allocate_number` now catches the driver's duplicate-entry error through
   the new `is_duplicate_entry` helper — without it the retry loop was dead code
   and acceptance criterion 2's race could not pass. (b) The final `frappe.throw`
   in the same function now raises `frappe.ValidationError` instead of
   `type(last_error)`, because `last_error` can now be a `pymysql` class and
   raising a driver exception class as a user-facing error is worse than a plain
   validation error. Nothing else in the module changed.
2. **`CRMInvoice.number_is_locked()` is not called `is_locked()`.**
   `frappe.model.document.Document.is_locked` is a PROPERTY reporting the
   framework's own file lock. A controller method of that name shadows it with a
   bound method, which is always truthy, so `check_if_locked` then tried to
   `stat` a lock file that does not exist and EVERY save raised
   `FileNotFoundError`. Found by a live test run, not by reading. Recorded because
   the trap is invisible and applies to any future controller in this app.
3. **Criterion 8 is not a third-party decode.** The container has no independent
   QR decoder — `pyzbar`, `cv2` and `qrcode` are all absent (checked 2026-08-19);
   only `PIL` is present. `test_the_data_uri_is_a_png_that_encodes_exactly_that_string`
   therefore reads the rendered PNG back into a module matrix pixel by pixel and
   compares it against the matrix `segno` builds for the URI the test wrote by
   hand. That proves the image on the invoice carries that symbol and no other,
   and it is a genuine round trip through PNG rendering — but it is not an
   independent decoder, and the encoder is on both sides of the comparison. **The
   gap is real and is stated rather than hidden.** A future stage that wants a
   true decode should add `pyzbar` (needs the `libzbar0` system library) or
   `opencv-python-headless`, both of which were rejected here as system-library
   dependencies for a self-hosted install.
4. **The VOID watermark is asserted at the payload level, not on the rendered
   PDF.** `test_the_print_payload_carries_the_qr_and_the_void_marker` proves
   `inv_meta["is_void"]` flips and the template renders `.inv-void` from it. No
   test rasterises the PDF and looks for red diagonal text; the suite deliberately
   stays off wkhtmltopdf. The template markup is reviewable in git.
5. **The reminder email renders nothing and mints nothing; it reuses a link that
   already exists.** Reviewer decision, 2026-08-19. A reminder with no way to open
   the invoice is too weak, and rendering a PDF inside an unattended hourly sweep
   would put wkhtmltopdf on the scheduler's critical path for every open invoice,
   every hour. The middle path is `crm.invoice_reminders.live_link_url`: one
   indexed read for a tokenised share link that an agent already minted at send
   time. When one is live the body gains a single line carrying that URL; when
   there is none the body is exactly what it was before. A link counts as live
   only when it is this invoice's, carries the `Invoice` purpose, is still
   `active`, has not expired, and its private File still exists — the last check
   because `expire_links` clears `file` before it deletes the document, so a row
   can look active for the length of one sweep. **The sweep never calls
   `create_link` and never calls `render_print_pdf`**, asserted directly by
   `test_the_sweep_never_mints_a_link_and_never_renders_a_pdf`. The consequence,
   stated rather than hidden: an invoice the agency never sent from the app has no
   link to give, so its reminder carries none. The reminder is exactly as useful
   as the send that preceded it.
6. **`customer_snapshot` reads a postal address only through
   `CRM Organization.address`, and that is the v1 answer.** Reviewer decision,
   2026-08-19: **keep the organization-only snapshot; do NOT add a
   contact-address fallback.** The limitation is real and is recorded here rather
   than worked around. A deal with no organization, or an organization with no
   linked `Address`, produces an invoice whose address block is empty. Above
   ₹50,000 with no recipient GSTIN, criterion 4 then REFUSES to issue it, naming
   the missing fields.
   **The workaround is the Draft itself.** Nothing is lost and nothing is
   silently wrong: the conversion still succeeds, the invoice sits in Draft, and
   the agent types the address into the Draft before pressing Issue. A Draft is
   fully editable and may be renumbered freely — that is what the Draft state is
   for. The refusal is the feature working, not a gap: it is better to stop an
   agent at the Issue button than to send a customer an invoice that fails Rule 46.
   A contact-address fallback stays available to a future stage; it was considered
   and deliberately not built in v1.
7. **Currency is INR only.** The field is read-only and defaults to `INR`.
   Foreign-currency invoicing is deferred (design note §Risks: place-of-supply
   complexity).
8. **`get_tiles` reads every permitted invoice into Python rather than
   aggregating in SQL.** `frappe.get_list` is what applies the row-level scope,
   and doing the sum in SQL would mean re-deriving those conditions by hand — the
   exact thing master spec §3 warns about. For a small agency the row count is in
   the hundreds. If it ever is not, the fix is a permission-aware aggregate, not a
   raw query.
9. **No frontend.** The sidebar entry, the deal-page action, the invoice editor
   and the tiles are Stage 5.3b. Every endpoint this stage adds is already
   permission-checked and flag-gated, so the frontend adds no new authorization
   surface.
10. **`crm/tests/test_dashboard.py` does not run on this container** and did not
    run before this stage either: it imports `frappe.tests.IntegrationTestCase`,
    which exists in frappe v16-dev but not in the v15.117 the container runs.
    Pre-existing, unrelated to this stage, and unchanged by it.
