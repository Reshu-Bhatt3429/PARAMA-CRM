# Stage 2B — deviations, decisions and open questions

Scope: master spec §5 items 1 (task due-date reminders), 6 (email forward) and
23 (snippets). Branch `feat/feature-expansion`. Nothing is committed by this
stage.

This file records the places where the build differs from the brief, and why.
Everything not listed here was built as specified.

---

## Decisions the brief asked me to state

### The reminder offset is an ORG-WIDE default, not a per-user preference

The brief allowed either and asked which was chosen. **Per-org, in FCRM
Settings** (`task_reminder_offset_minutes`, default 60).

There is no per-user preference store in this app. The one thing that looks
like one — `crm/integrations/api.py::get_user_default_calling_medium` — reads
its value off a `CRM Telephony Agent` row, which is a doctype for something
else entirely. Building a per-user preference doctype for a single integer is
not a v1 decision, and the master spec explicitly permits the org default.

`crm.reminders.reminder_offset_minutes` is the ONLY reader of that field, so a
future per-user override has exactly one place to hook into.

### The offset needed a patch, and here is the trap it walks around

`FCRM Settings` is a Single. When a new `Int` column appears on a Single that
already exists, the framework writes `0`, not the JSON default. And `0` is a
legitimate value for this field — it means "remind at the due time itself" — so
the reader cannot tell an unset column from a deliberate zero and must not
guess.

`crm.patches.v1_0.set_task_reminder_offset_default` settles it once, and only
when the value is falsy AND the feature has never been switched on. Verified on
the demo site: the value read `0` before the patch and `60` after.

---

## Deviations from the brief

### 1. `/` does not open the snippet popover in the EMAIL composer

**Brief:** "typing `/` at line start (or clicking a snippet icon) opens a
searchable popover of snippets", in the email composer and the WhatsApp
composer.

**Built:** `/` at line start opens it in BOTH WhatsApp composers. In the email
composer only the snippet icon does.

**Why:** `/` in the email composer is already taken, by frappe-ui, not by this
app. `EmailEditor.vue` builds its extensions through
`buildEditorExtensions` (`frontend/src/components/editor/config.ts`), which
always applies `RichTextKit`, and `RichTextKit` registers frappe-ui's own
`SlashCommands` suggestion extension
(`frontend/node_modules/frappe-ui/src/molecules/editor/kits.ts`, the
`options.slashCommands !== false` branch). That is the formatting menu —
headings, lists, image, table, embeds. Two suggestion plugins on the same
character both fire and stack their popovers.

The three ways out and why each was rejected:

* pass `slashCommands: false` and register a snippet extension instead —
  deletes the formatting slash menu from the email composer, a regression in an
  existing feature for the sake of a new one;
* rebuild the built-in command list and append snippets to it — `getCommands()`
  is not exported, so this means copying ~150 lines of a vendored file that
  will drift on the next frappe-ui bump;
* a second trigger character in the email composer only — one composer, two
  conventions.

The brief's own wording offers the icon as an alternative, so the icon is what
the email composer got. **This is the one item a reviewer should re-decide if
the formatting slash menu turns out to be expendable**; the change would then
be about ten lines in `config.ts` and `EmailEditor.vue`.

### 2. The snippet picker is a searchable dialog, not an anchored popover

**Brief:** "opens a searchable popover".

**Built:** `frontend/src/components/Modals/SnippetSelectorModal.vue`, a
searchable `Dialog`.

**Why:** it is the pattern this app already uses for exactly this job
(`EmailTemplateSelectorModal.vue`), and it satisfies C4 (mobile parity) without
a separate mobile treatment — an anchored popover next to a textarea on a phone
is the worse of the two. Behaviour is the same: `/` opens it with what has been
typed already in the search box, Enter takes the first match, and the inserted
body replaces the `/shortcut` the user typed.

### 3. The Snippets settings page does not use `SettingsPage.vue`

`frontend/src/components/Settings/SettingsPage.vue` renders through
`FieldLayout`, and the brief forbids touching `Field.vue` / `Grid.vue` /
`FieldLayout` (master spec D4). `SnippetsPage.vue` is therefore self-contained:
its own list, its own create/edit dialog. It reads and writes `CRM Snippet`
through `createListResource`, exactly as `EmailTemplates.vue` does.

---

## Things a reviewer should know

### The composer's send path moved

`CommunicationArea.vue::sendMail` no longer calls
`frappe.core.doctype.communication.email.make` directly. It calls
`crm.api.email.send_email`, which asks the Stage-1A suppression ledger and then
calls the same `make`. This was NOT optional: the brief requires the forward
path to be suppression-checked, and Forward rides the ordinary composer, so the
check had to go into the shared path.

The alternative — `override_whitelisted_methods` on core's `make` — was
rejected: it would put this app's consent rules in front of every desk caller of
a framework endpoint, which is a far larger blast radius than the composer.

For a recipient nobody opted out of, nothing changes. Addresses are passed
through **by value**: `crm.suppression.filter_suppressed` is the batch tool and
it returns NORMALISED addresses, which would silently rewrite
`Ann Lee <ann@x.com>` into `ann@x.com` on a perfectly clean send. `send_email`
therefore asks `is_suppressed` per address and hands the surviving strings on
untouched.

### Forwarding attachments copies File rows; it does not move them

`frappe.core.doctype.communication.email.add_attachments`, given a File
docname, reads that row's `file_url` and `is_private` and then does
`frappe.new_doc("File")` — a NEW row pointing at the same file. The original
Communication keeps its attachments. Verified by reading the framework source
in the container (`apps/frappe/frappe/core/doctype/communication/email.py`,
lines 240–276).

One thing this exposed and the code guards: the composer's **Discard** button
hard-deletes every File row it is holding. Forwarded chips are the ORIGINAL
message's File rows, so discarding a forward would have stripped the
attachments off an already-sent email. `forwardedAttachments` marks each
carried file `forwarded: true` and `deletableAttachments` filters them out of
the delete list (`frontend/src/utils/emailForward.js`, tested in
`frontend/tests/unit/emailForward.test.js`).

### A forward keeps its signature; a reply still does not

Opening the composer fires the `showEmailBox` watcher, which prepends the
user's signature; `reply()` in `EmailArea.vue` then calls `clearContent()` and
wipes it. That is pre-existing behaviour for Reply and Reply All and was left
alone. `forward()` puts the signature back on top of the quote after building
the content, so a forward is not the one composer that sends unsigned. If a
reviewer wants the two to match, the fix belongs in `reply()`.

### The reminder recipient falls back to the task's creator

`CRM Task.assigned_to` is optional. `crm.reminders.recipient_of` uses
`assigned_to or owner`, so an unassigned task reminds the person who made it.
`Guest` is never a recipient; `Administrator` is, deliberately — on a small
self-hosted site it is somebody's real login.

### Two spellings of "cancelled"

`CRM Task.status` ships `Canceled`. `crm.reminders.CLOSED_STATUSES` lists
`Done`, `Canceled` AND `Cancelled`, so a site that renamed the status does not
start reminding about cancelled work.

---

## Open issues handed on

1. **`/` in the email composer** — deviation 1 above. A decision, not a bug.
2. **The two WhatsApp composers are still duplicates.**
   `Activities/WhatsAppInboxComposer.vue` and `Activities/WhatsAppBox.vue`
   share no code; the snippet trigger had to be written twice (the pure parts —
   `slashTrigger`, `applySnippet` — are shared through
   `frontend/src/utils/snippets.js`, the wiring is not). Merging them is a
   refactor of somebody else's surface and was out of scope here.
3. **`crm/api/activities.py` still does not return the Communication `name`.**
   Forward does not need it — attachments are re-linked by their own File
   docnames — but item 5 (Send Later) and item 19 (read receipts) will want it,
   and adding it is a one-line change to `get_deal_activities` /
   `get_lead_activities`.
4. **No frontend component tests exist anywhere in this repo.**
   `@vue/test-utils` is not a dependency and nothing is mounted in
   `frontend/tests/unit/`. Every testable decision in this stage was therefore
   pushed into pure functions (`utils/tasks.js`, `utils/snippets.js`,
   `utils/emailForward.js`) and tested there. The wiring inside the `.vue`
   files is covered by the production build and by eye, not by a test.
5. **The frappe v15 vs v16 mismatch stands** (Stage 1A open issue 1). The 47
   upstream modules still do not collect in this container, including
   `crm/permissions/test_org_hierarchy.py`. The snippet and lead permission
   rules are nevertheless exercised for real in `crm/tests/test_snippets.py`,
   which sets a real Sales User and lets the real hooks refuse the read.
