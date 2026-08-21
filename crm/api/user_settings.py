"""Hardened overrides for framework user-setting mutations."""

import frappe
from frappe.model.utils.user_settings import save as framework_save_user_settings


@frappe.whitelist(methods=["POST"])
def save(doctype: str, user_settings: str | dict):
	return framework_save_user_settings(doctype, user_settings)
