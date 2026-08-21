import frappe


def execute():
	"""Keep existing sites aligned with the PARAMA product name."""
	old_name = "Frappe CRM"
	new_name = "PARAMA CRM"

	if frappe.db.exists("Workspace", old_name) and not frappe.db.exists("Workspace", new_name):
		frappe.rename_doc("Workspace", old_name, new_name, force=True)

	if frappe.db.exists("Workspace", new_name):
		frappe.db.set_value(
			"Workspace",
			new_name,
			{"label": new_name, "title": new_name},
			update_modified=False,
		)
