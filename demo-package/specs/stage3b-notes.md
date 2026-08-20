# Stage 3B notes — items 7, 22, 24

Worker: Stage 3B (target meter, deal-health flags, Today page). Branch
`feat/feature-expansion`. Nothing committed.

This file records the decisions a reviewer would otherwise have to reverse
engineer, and the two files this worker touched that its ownership list did not
name.

---

## 0. Two Stage 3A entries carried into `crm/hooks.py`

Stage 3A (commit `69865d21`) left two hook entries it could not apply, because
`crm/hooks.py` is single-owner and Stage 3B holds it this stage. Both were
applied exactly as `stage3a-notes.md` §1a and §1b specify, unchanged:

* `scheduler_events["hourly"]` gains `crm.api.quote.cleanup_quote_links` —
  REQUIRED before the demo, or a tokenised quote link never expires and its
  private PDF is never deleted.
* `after_migrate` gains `crm.api.quote.install_quote_print_format`.

Every dotted path in `scheduler_events` and `after_migrate` was then resolved
against the running site: `checked: 93 unresolvable: []`, with all three of
`crm.api.quote.cleanup_quote_links`, `crm.api.quote.install_quote_print_format`
and `crm.deal_health.sweep_deal_health` registered. `crm.tests.test_quote` (67),
`crm.tests.test_send_later` (58) and `crm.tests.test_form_auto_response` (42)
stay `OK`.

Stage 3A's §1c stands and is NOT Stage 3B's to do: `outbound_engine_enabled` is
still default OFF and must be switched on at deploy or Send Later schedules
without ever delivering.

---

## 1. Files touched outside the stated ownership

The brief said: "Conflicts go into `demo-package/specs/stage3b-notes.md` with
exact diffs." Two files qualify. Neither is on the must-NOT-touch list, and
neither was modified by another Stage-3 worker in this tree at the time of
writing (`git diff` on both shows only the hunks below).

### 1.1 `frontend/src/components/ViewControls.vue` — +26 lines

The "Needs attention" quick filter needs a predicate written into
`list.params.filters`, and `list` is owned by `ViewControls`. Stage 2A set the
precedent with `applyTagFilter`; this sits directly beside it and is exported
through the same `defineExpose`. The Deals page owns only the button.

```diff
@@ -1371,6 +1371,30 @@ function applyTagFilter(tag) {
   updateFilter(filters)
 }
 
+/**
+ * Toggle the "Needs attention" filter on the Deals list (spec §5, item 22).
+ *
+ * `custom_parama_health_flags` holds JSON when a deal is flagged and is left
+ * EMPTY when it is healthy, so `["is", "set"]` is an exact predicate — no LIKE
+ * over a JSON blob, and no second boolean column to keep in step. Clicking the
+ * chip that is already applied clears it, like the like filter below.
+ */
+function applyHealthFilter(fieldname) {
+  if (!fieldname) return
+  let filters = { ...list.value.params.filters }
+
+  if (filters[fieldname]) {
+    delete filters[fieldname]
+  } else {
+    filters[fieldname] = ['is', 'set']
+  }
+  updateFilter(filters)
+}
+
+function healthFilterApplied(fieldname) {
+  return Boolean(list.value?.params?.filters?.[fieldname])
+}
+
 function applyLikeFilter() {
   let filters = { ...list.value.params.filters }
   if (!filters._liked_by) {
@@ -1394,6 +1418,8 @@ defineExpose({
   applyFilter,
   applyLikeFilter,
   applyTagFilter,
+  applyHealthFilter,
+  healthFilterApplied,
   likeDoc,
   updateKanbanSettings,
   fetchAndUpdateKanbanColumns,
```

### 1.2 `frontend/src/pages/WhatsAppInbox.vue` — +24 / -1 lines

Item 24 says a Today row's action is "Reply → navigates". Without this, Reply
lands the agent on the inbox with no thread open and they have to find the
conversation again — which is the thing the page exists to save them. The inbox
did not read route query params at all before this.

It fires exactly once per page visit: the conversation list reloads on every
inbound message, and a repeating handler would drag the reader back to the
thread they had navigated away from.

```diff
@@ -748,8 +748,9 @@ import {
   usePageMeta,
 } from 'frappe-ui'
 import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
-import { useRouter } from 'vue-router'
+import { useRoute, useRouter } from 'vue-router'
 
+const route = useRoute()
 const router = useRouter()
@@ -949,6 +950,28 @@ function siteCurrency() {
   return window.sysdefaults?.currency || 'USD'
 }
 
+// Deep link from the Today page (master spec §5, item 24): `?doctype=&name=`
+// opens that thread as soon as the list has arrived. It fires ONCE — the list
+// reloads on every inbound message, and a second run would drag the reader back
+// to the thread they navigated away from.
+let deepLinkHandled = false
+watch(
+  allConversations,
+  (rows) => {
+    if (deepLinkHandled || !rows.length) return
+
+    const { doctype, name } = route.query || {}
+    if (!doctype || !name) return
+
+    deepLinkHandled = true
+    const match = rows.find(
+      (row) => row.reference_doctype === doctype && row.reference_name === name,
+    )
+    if (match) selectConversation(match)
+  },
+  { immediate: true },
+)
+
 function selectConversation(conversation) {
```

`frontend/src/pages/Deals.vue` was also edited (+35). It is treated as in scope:
the brief names "deal list/kanban components", and the emit hop from
`DealsListView.vue` to `ViewControls` has to pass through the page, exactly as
Stage 2A's tag filter does.

---

## 2. Decisions a reviewer should know about

### 2.1 Item 7 — the meter ignores the dashboard date filter, and says so

`get_target_meter(from_date, to_date, user)` accepts the two dates so the chart
dispatcher can call it like every other chart, and throws them away. The target
is a monthly quota: reading it through a "Last 90 Days" filter would compare 90
days of revenue against one month of target and report 300%.

The widget carries the period in its subtitle
(`"August 2026 so far — this calendar month, not the dashboard date filter"`),
so the reader who just changed the filter is told rather than left to work it
out. `test_the_dashboard_date_filter_is_ignored` and
`test_the_widget_says_which_period_it_used` pin both halves.

### 2.2 Item 7 — no target set is not 0%

`monthly_revenue_target` defaults to 0. At 0 the payload reports
`hasTarget: false` and the widget renders an empty track plus "No monthly target
set — add one in Settings." A meter that said "0%" would read as a failed month
rather than an unconfigured one.

### 2.3 Item 7 — a new widget TYPE, not a number chart

`DashboardItem.vue` dispatches on `item.type` through a `v-if` chain, and there
is no progress primitive in frappe-ui. The meter is therefore a fourth type,
`progress_chart`, with one member (`target_meter`). UX §2.8 rules out the gauge
that would otherwise have been reached for; the widget is a label, a number, one
thin bar with a light track, and a muted `₹X / ₹Y target (Z%)` line. Over target
the bar caps at 100% and a small `+N% over` badge appears. The fill animates
500 ms ease-out on first render only, and not at all under
`prefers-reduced-motion`.

### 2.4 Item 22 — the sweep drives its own batch loop

`crm.deal_health.run_sweep` uses every primitive `crm/sweeps.py` provides — the
per-job lock, the `(modified, name)` keyset cursor, the stored watermark, batch
commits — but does not call `crm.sweeps.run_sweep`. That helper hands the handler
one row at a time, and each of the three questions is an aggregate over a SET of
deals. Per row it would be three queries per deal; per batch it is three queries
per two hundred deals.

### 2.5 Item 22 — the watermark is CLEARED when a pass finishes

`close_date_passed` becomes true through time passing, not through a row being
edited. A cursor that only moved forward would never revisit the deal that went
overdue last night. So the watermark does what it is for — resuming a crashed or
truncated pass — and is reset once a pass reaches the end.
`test_a_finished_pass_clears_the_cursor_so_tomorrow_starts_at_the_top` is the
guard.

### 2.6 Item 22 — closed deals are swept too

A deal flagged while it was open must lose the flag when it is won. Filtering the
sweep to open deals would have left a stale "Needs attention" chip on a won card
for ever. `test_winning_the_deal_clears_the_chip`.

### 2.7 Item 22 — an empty column, and why it is NULL

A healthy deal gets an EMPTY column rather than `{}`, which makes `["is", "set"]`
an exact quick filter with no LIKE over a JSON blob and no second boolean column
to keep in step.

"Empty" on disk is NULL, not `""`. Frappe maps the JSON fieldtype to MariaDB
`json`, and MariaDB puts `CHECK (json_valid(col))` on such a column — the empty
string fails it with `OperationalError (4025)`. This was found by a test, not by
reading, and is recorded because the next JSON custom field will hit it.

**No index** on the column, deliberately: MariaDB refuses an index on a TEXT
column without a prefix length, and `json` is `longtext`. The predicate is
`ifnull(col, '') != ''` over one small table. If CRM Deal outgrows that, the fix
is a generated boolean column, not a prefix index on a JSON blob.

### 2.8 Item 22 — writes use `update_modified=False`

Touching `modified` would move the row to the end of the cursor's ordering and
the sweep would read it a second time in the same pass. It also keeps a derived
value from looking like a human edit in the list's "last modified" column.

### 2.9 Item 22 — the digest is escaped now

`create_digest_notification` interpolates its message into an HTML string. It
previously carried only integers; the deal-health line carries deal titles, which
are customer data. The message is now put through `frappe.utils.escape_html`
before it goes into `notification_text`. This is a behaviour change to an
existing path and is called out here rather than buried.
`test_the_notification_body_escapes_the_deal_title`.

### 2.10 Item 22 — the digest works without the WhatsApp app

`send_daily_digest` used to return 0 immediately when the `WhatsApp Message`
doctype was absent. The deal-health section does not go through Meta, so the
function now falls back to `empty_digest_summary()` and still reports deal
health. When `deal_health_enabled` is off the section is empty and the digest is
byte-for-byte what it was before.

### 2.11 Item 24 — CRM Task has no row-level rule, so the endpoint makes one

`crm/hooks.py` registers `permission_query_conditions` for CRM Lead, CRM Deal,
CRM Notification, CRM WhatsApp Followup, CRM Itinerary and CRM Snippet — and NOT
for CRM Task. A Sales User's Tasks page already lists every task on the site.
`crm.api.today.due_tasks` therefore filters `assigned_to == frappe.session.user`
explicitly, plus a second query for tasks nobody was assigned that the caller
created (the rule `crm.reminders.recipient_of` already applies).
`test_a_sales_user_does_not_see_another_users_task` is what stops that
regressing.

This means Today is a PERSONAL list even for a manager: their own due tasks, not
their team's. Their team's flagged deals do appear, because CRM Deal's hierarchy
conditions say so. That asymmetry is deliberate and is the honest reading of
"each permission-scoped as its source already is".

### 2.12 Item 24 — the default landing route is a FALLBACK, not an override

`homeRouteFor()` is consulted only when `getDefaultView()` returns nothing. A
user who saved a default view chose it, and item 24 is not a licence to take that
away. On a phone everybody gets Today: the Dashboard is desktop-only in the nav
(`condition: () => !props.mobile`), so landing a manager there would put them on
a page with no way back into the app.

### 2.13 Item 24 — one request feeds three surfaces

`crm.api.today.get_today` returns `items`, `counts` and `deal_health_enabled`.
The composable `@/composables/today` holds it, and the Today page, the sidebar
badge and the Deals list's chip gate all read the same payload. That is one
request, not three.

`dealHealthEnabled` starts false and stays false if the fetch fails, because the
acceptance criterion is "flag OFF = no chips" and a client that guessed
"probably on" would break it.

---

## 3. Open issues handed on

1. **The Today reply row opens the inbox, not the record.** That is where the
   composer is. The deep link added in §1.2 selects the thread; if a later stage
   moves the composer onto the record page, `replyRoute` in
   `frontend/src/utils/today.js` is the one place to change.
2. **The digest is still site-wide and has no per-user opt-out.** Master spec
   §5 item 22 says the digest should respect "quiet hours + per-user toggle".
   Neither exists for this digest today: `send_daily_digest` had no quiet-hours
   check before this stage and this app has no per-user preference store at all
   (stated at `crm/reminders.py:41-45`). The deal-health section inherits that
   gap rather than inventing a preference store for one line of text. **This is
   a deviation from the spec and needs an owner decision.**
3. **`notify_user` dedups on the exact field tuple.** Two digests with identical
   counts on consecutive days collapse into one notification. Pre-existing, and
   the deal-health line makes a collision less likely rather than more, but it is
   still there.
4. **The "Needs attention" column must be added by hand.** It is a real Custom
   Field on CRM Deal labelled "Needs Attention", so it appears in the column
   picker and in the kanban field picker, but no default list layout includes it.
   A seeded demo needs it added once per view.
5. **`awaiting_reply` reads Communication and WhatsApp Message directly.** There
   is no stored last-inbound/last-outbound pair on CRM Deal; the WhatsApp inbox
   computes its own at query time and `CRM WhatsApp Followup` stores one only for
   leads that are enrolled. The sweep therefore aggregates both channels per
   batch. If a later stage adds stored columns, `last_message_times` is the one
   function to replace.
6. **`crm/tests/test_dashboard.py` still cannot be collected** in this container:
   it imports `frappe.tests.IntegrationTestCase`, which is frappe v16 only. The
   target-meter tests are therefore a NEW module,
   `crm/tests/test_target_meter.py`, on the v15 `FrappeTestCase`. Stage 1A open
   issue 1 stands.
