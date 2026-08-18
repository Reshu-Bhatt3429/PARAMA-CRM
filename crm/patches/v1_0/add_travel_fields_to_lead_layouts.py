"""Expose the travel fields on existing sites.

The travel fields (destination, travel dates, group size, budget) were added to
the CRM Lead doctype and to the default Lead layouts in ``crm/install.py``, but
that seeder is skip-if-exists -- so sites installed earlier keep their old
layouts and never see the new fields. This patch appends a "Travel" section to
the Lead Side Panel and Data Fields layouts, carrying only the travel fields
that layout does not mention yet. Idempotent: a layout that already mentions
every travel field is left untouched, as is a site whose doctype does not (yet)
carry the fields.
"""

import json

import frappe

TRAVEL_FIELDS = ["destination", "travel_start_date", "travel_end_date", "group_size", "budget"]

TARGET_LAYOUTS = {
	"CRM Lead-Side Panel": [{"name": "column_Tr4v", "fields": TRAVEL_FIELDS}],
	"CRM Lead-Data Fields": [
		{"name": "column_Tr4w", "fields": TRAVEL_FIELDS[:3]},
		{"name": "column_Tr4x", "fields": TRAVEL_FIELDS[3:]},
	],
}


def _iter_columns(layout):
	"""Every column dict in a layout tree, including tabbed layouts that nest
	sections under a top-level section's ``sections`` key."""
	for section in layout:
		yield from section.get("columns", [])
		for nested in section.get("sections", []):
			yield from nested.get("columns", [])


def execute():
	for name, columns in TARGET_LAYOUTS.items():
		if not frappe.db.exists("CRM Fields Layout", name):
			continue

		doc = frappe.get_doc("CRM Fields Layout", name)
		try:
			layout = json.loads(doc.layout or "[]")
		except (ValueError, TypeError):
			continue

		if not isinstance(layout, list):
			continue

		meta = frappe.get_meta(doc.dt)
		if not all(meta.has_field(fieldname) for fieldname in TRAVEL_FIELDS):
			continue

		present = {field for column in _iter_columns(layout) for field in column.get("fields", [])}

		# Append only what is missing, so a layout that already carries one travel
		# field still gets the other four. A layout that carries all five ends up
		# with no columns to add and is left untouched.
		missing_columns = []
		for column in columns:
			fields = [field for field in column["fields"] if field not in present]
			if fields:
				missing_columns.append({**column, "fields": fields})

		if not missing_columns:
			continue

		layout.append(
			{
				"label": "Travel",
				"name": "travel_section",
				"opened": True,
				"columns": missing_columns,
			}
		)
		doc.layout = json.dumps(layout)
		doc.save()
