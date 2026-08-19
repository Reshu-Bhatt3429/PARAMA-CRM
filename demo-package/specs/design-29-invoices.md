# Design note — Item 29: Invoice module (Stage 5, project 3)

Status: DRAFT pending Codex review, then build. Jurisdiction: India/GST (owner-confirmed). UPI QR: confirmed. Research basis: hallmark-workflow report (Zoho/HubSpot/Stripe/Travefy), GST compliance report (CBIC primary texts), Frappe-path report (this session, 2026-08-18/19).

## Data model

- `CRM Company Profile` (Single): legal name, address lines, state + state code, GSTIN, phone, email, logo, UPI VPA, default SAC, tour-package-mode default, invoice number prefix (default `INV/`), terms default. Feeds quotes too (Stage 3A open item: agency address placeholder).
- `CRM Invoice`: naming series from prefix + FY (`INV/25-26/####`), validated ≤16 chars, charset `[A-Za-z0-9\-\/]`, unique per financial year; deal (Link, required v1), customer fields snapshot (name, address, state+code, GSTIN optional), place_of_supply (state code, default = customer state), invoice_date, due_date, service_date, mode (Tour package 5% gross / Commission standard-rate), reverse_charge (default No), status (Draft / Sent / Partially Paid / Paid / Void; Overdue is computed for display, never stored), totals (taxable, cgst, sgst, igst, grand, rounded per Section 170), payment schedule child, items child, payments child, tokenized share link (reuse crm/document_links.py), status history (send-log style rows).
- Child `CRM Invoice Item`: description, SAC (Link to `CRM SAC Code` master — admin-editable, seeded with placeholders flagged "verify code with your CA"), qty, rate, amount, tax_rate.
- Child `CRM Invoice Payment`: date, amount, mode (UPI / Bank / Cash / Other), reference, recorded_by. Payments only ever ADD; corrections are a negative-amount row with mandatory note (audit-safe, no edits/deletes).
- Child `CRM Invoice Schedule`: label (Deposit / Balance / custom), due_date, amount. Reminder ladder per row.

## Rules (from the compliance report — Rule 46 / Section 170 / Rule 47, CBIC primary texts)

- Number locks at first send (immutable after finalization; Draft may renumber). 16-char + charset validation server-side.
- Tax split: intra-state (place_of_supply == company state) ⇒ CGST+SGST halves; inter-state ⇒ IGST. Rates per line from SAC master; tour-package mode forces 5% gross on all lines, blocks ITC note, and auto-prints the mandatory statement: "The amount charged is gross amount and inclusive of charges of accommodation and transportation."
- B2C ≥ ₹50,000 ⇒ recipient name/address/state become required fields (validation on finalize, not on draft).
- Rounding: Section 170 round-half-up to whole rupee on the final tax totals and grand total; per-line values keep paise.
- Rule 47 warning (non-blocking) when invoice_date > service_date + 30 days.
- E-invoicing (IRN/QR): OUT of v1. Build gate: the worker re-checks the live 2026 turnover threshold ONCE (web), records the figure + date + source in the code comment and the runbook; UI shows a one-line note in Company Profile: "E-invoicing applies above ₹<threshold> turnover — this module does not generate IRN."
- Retention: invoices are never hard-deleted; Void is the terminal negative state and excludes from revenue tiles.

## Flow

- One-click "Convert deal to invoice" on the Deal record (same placement as Create quote): items prefill from deal products (sum from product rows — deal.total is client-written and unreliable, Stage 3A finding), customer from contact/org, mode from Company Profile default.
- PDF: print format `GST Invoice A4` (clone the quote/print machinery): all Rule 46 fields, tax table, rounded totals, UPI QR (locally generated from the VPA + amount via a small pure-python QR dependency — pin it; no network), signature line, the tour-package statement when applicable.
- Send: email + WhatsApp via the existing paths (suppression-checked); tokenized view link with the quote view-log machinery; open events on the deal timeline.
- Payment recording: "Record payment" on the invoice: amount (defaults to remaining), mode, reference; status math automatic (Partially Paid / Paid); over-payment blocked v1. A "payment received" thank-you template email optional per send.
- Reminders: per schedule row, ladder due-date / +7d / +14d on the outbound machine, suppression-checked, quiet-hours aware, per-invoice pausable. Flag `invoice_reminders_enabled` default OFF (separate from the module itself).
- Dashboard: three tiles — Outstanding, Overdue, Collected this month — permission-scoped like other charts.
- Sidebar: "Invoices" under Travel (item 30 anticipated this).

## Permissions

Sales User: create Draft from own-scope deals, read own-scope invoices, record payments; cannot Void, cannot edit a Sent invoice's amounts, cannot manage Company Profile or SAC master (managers only). All endpoints in the permission matrix; invoice row scope inherits the deal's org-hierarchy conditions.

## Acceptance criteria

1. Won deal → one click → Draft invoice with correct items, totals, and CGST/SGST vs IGST split by place of supply; Section 170 rounding matches hand-computed values in tests.
2. Finalize/send locks the number; a 17-char or bad-charset series is rejected; two invoices in one FY cannot share a number (race-tested).
3. Tour-package mode prints the mandatory statement; commission mode does not.
4. B2C ≥ ₹50,000 without recipient address refuses to finalize with a clear message; < ₹50,000 finalizes.
5. A 2-row schedule (deposit/balance) fires two independent reminder ladders; recording the deposit stops only its ladder.
6. Payments: partial → Partially Paid; exact remaining → Paid; negative correction row restores Partially Paid; every row immutable.
7. Void excludes the invoice from all three tiles; the PDF gains a VOID watermark.
8. UPI QR on the PDF decodes to `upi://pay?pa=<vpa>&am=<remaining>` (test decodes it).
9. A Sales User cannot Void or see out-of-scope invoices (matrix tests).
10. Module behind `invoices_enabled` default OFF; OFF hides the sidebar entry and refuses the endpoints.

## Risks

SAC codes ship as admin-editable placeholders (research was single-source) — the UI labels them "verify with your CA". The QR dependency must be pure-python and pinned (no system libs). FY boundary for the series uses India FY (Apr 1). Currency v1 is INR only; foreign-currency invoicing deferred (place-of-supply complexity flagged in research).
