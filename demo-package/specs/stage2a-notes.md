# Stage 2A — notes for other owners

Written by the Stage 2A worker (master spec §5, items 2, 3, 10, 11). Everything
here is a change this stage WANTED but did not make, because the file belongs to
somebody else this stage, plus the findings that came out of the work.

---

## 1. The duplicate banner is under the form, not under the field

**Spec text:** item 3 asks for "an inline amber banner directly under the field".

**What shipped:** the banner (`frontend/src/components/DuplicateWarning.vue`)
renders immediately below the whole `<FieldLayout>` block in `LeadModal.vue`,
`ContactModal.vue` and `DealModal.vue`, above the error message and the Create
button.

**Why:** the email and phone inputs inside those modals are rendered by
`FieldLayout` / `Field.vue`, which this stage is explicitly barred from touching
(ownership note, and master spec §8 D4 parks the Field/Grid refactor). Placing
the banner under one specific field means either a slot in `Field.vue` or a
Teleport that targets a DOM node by fieldname. The first is somebody else's
file; the second is fragile.

**What a Field.vue owner would need to add:** one named slot after a field's
control, keyed by fieldname — for example `#after-field-<fieldname>` — so a
consumer can put a message under exactly one input. That would also serve the
inline validation messages the modals currently show in one lump at the bottom.

Everything else in item 3 is as specified: the check is debounced 400 ms after
typing stops, the banner is non-blocking, it carries an open-record link and a
"Continue anyway" dismiss, and creation is never prevented.

## 2. `_user_tags` was added to the standard field list

`frontend/src/utils/model.js` gained one entry:

```js
{ label: 'Tags', fieldtype: 'Data', fieldname: '_user_tags' },
```

That is what makes "Tags" selectable in Column Settings. The server side already
offered it — `crm/api/doc.py::get_filterable_fields` has carried `_user_tags`
since before this stage — so this is the column half of a filter that already
existed. The file is small and shared; flagging it here because it is outside
the components/pages this stage owns.

## 3. `ViewControls.vue` gained one function

`applyTagFilter(tag)`, exposed alongside `applyFilter` and `applyLikeFilter`,
and wired from `Leads.vue` / `Deals.vue`. It sets
`filters._user_tags = ['LIKE', '%<tag>%']`, and clicking the same tag again
clears it, exactly as `applyLikeFilter` toggles `_liked_by`. No existing
function was modified.

## 4. Finding handed to the item 23 (Snippets) owner — FIXED BY ITS OWNER

**Status: resolved 2026-08-19 00:39 by the Snippets owner, while this stage was
still running. Recorded because it shaped Stage 2A's verification order.**


`frontend/src/components/Settings/Snippets/SnippetsPage.vue` does not compile.
The production build fails, verbatim:

```
[plugin vite:vue] src/components/Settings/Snippets/SnippetsPage.vue (122:17): Error parsing JavaScript expression: Unterminated string constant. (2:18)
```

Cause: lines 120–125 put a literal `{{ field }}` inside a `{{ __('...') }}`
interpolation:

```vue
{{
  __(
    'Use {{ field }} to pull in the record, for example {{ lead_name }} or {{ user.full_name }}.',
  )
}}
```

Vue's template compiler closes the outer interpolation at the FIRST `}}`, which
lands in the middle of the string. One fix is to move the message into the
script block as a constant and render `{{ hintText }}`; another is to build the
literal braces from `String.fromCharCode` or from separate spans. This stage did
not touch the file.

Consequence for Stage 2A's own verification: the first production build could
not be run against the real working tree, so it was run against a copy in which
only that one interpolation was replaced with a placeholder. The owner then
fixed it in the real tree (the message is now built in the script block and
rendered with `v-text`), and the build was re-run against the real tree and
passed. Both runs are recorded in the Stage 2A section of
`stage1-verification.md`.

## 5. Reachable upstream tag endpoint

`frappe.desk.doctype.tag.tag.add_tag` / `remove_tag` are whitelisted by the
framework and remain reachable on this site. They write `_user_tags` before they
check `write` permission, and they accept a comma inside a tag. `crm.api.tags`
is a safe alternative door, not a replacement. Closing the upstream one means
overriding a framework whitelist — a decision for the Stage 6 security reviewer,
not for a feature stage.

## 6. Cmd+K quick actions mount the real create modals

`CommandPalette.vue` mounts `LeadModal.vue` and `DealModal.vue` itself for its
"Create lead" / "Create deal" actions, rather than routing to the Leads/Deals
list pages and asking them to open theirs. That keeps the palette self-contained
and reuses the modals that already set the status and owner defaults; the
generic `CreateDocumentModal` does not set them. If a later stage adds a global
"create lead" trigger, the palette should switch to it.

## 7. Tasks and Notes have no record route

`crm.api.search.palette_search` returns CRM Task and FCRM Note rows, but this
app has no record page for either — both are edited in a modal on top of their
list. `frontend/src/utils/palette.js::recordRoute` therefore opens the PARENT
record when the task or note names one, and falls back to the Tasks / Notes list
otherwise. A stage that adds a task or note detail route should add it to
`RECORD_ROUTES` and delete the fallback.
