"""Create the deal-health flags column on CRM Deal (spec §5, item 22).

One namespaced Custom Field, `custom_parama_health_flags`. The field is JSON
because the answer is a set of named flags rather than one state, and a set is
what the "Needs attention" chip expands into.

The column is EMPTY, not `{}`, for a healthy deal. That is what makes
`["is", "set"]` an exact quick filter for the list view and for
`crm.api.today`, with no LIKE over a JSON blob and no second boolean column to
keep in step.

NO INDEX is added, and that is a decision rather than an omission: Frappe maps
the JSON fieldtype to MariaDB `json`, which is `longtext`, and MariaDB refuses
an index on a TEXT column without a prefix length. The predicate is
`ifnull(col, '') != ''` over one small table, on a page a handful of agents
open a handful of times a day. If CRM Deal ever grows past that, the fix is a
generated boolean column, not a prefix index on a JSON blob.

Idempotent. `create_custom_fields` skips a field that already exists, so
`bench migrate` may re-run this as often as it likes.

This patch writes no record data and turns nothing on. The values are filled in
by `crm.deal_health.sweep_deal_health`, which is behind the default-OFF
`deal_health_enabled` flag; until somebody switches that on, the column stays
empty on every row.

Downgrade: drop the Custom Field row. Nothing else depends on the
column existing -- `crm.deal_health` and `crm.api.today` both check
`frappe.db.has_column` first, and the frontend renders no chip for an absent
value.
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

from crm.deal_health import DEAL_DOCTYPE, HEALTH_FIELD


def execute():
	if not frappe.db.exists("DocType", DEAL_DOCTYPE):
		return

	create_custom_fields(
		{
			DEAL_DOCTYPE: [
				{
					"fieldname": HEALTH_FIELD,
					"fieldtype": "JSON",
					"label": "Needs Attention",
					"description": (
						"Derived: which health flags this deal currently carries, written by "
						"the nightly crm.deal_health sweep. Empty means nothing is wrong. "
						"Do not edit."
					),
					"insert_after": "status",
					"read_only": 1,
					"no_copy": 1,
					"allow_on_submit": 0,
					"in_list_view": 0,
				}
			]
		}
	)

	frappe.clear_cache(doctype=DEAL_DOCTYPE)
