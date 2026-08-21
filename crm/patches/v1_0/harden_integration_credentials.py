"""Encrypt integration secrets and remove obsolete token copies.

The patch is deliberately idempotent. It can run after the DocType sync has
changed Exotel's field to Password, while the legacy clear-text value still
lives in the Single table column.
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields
from frappe.utils.password import set_encrypted_password


def execute():
	_add_whatsapp_app_secret_field()
	_migrate_exotel_webhook_token()
	_purge_facebook_page_tokens()


def _add_whatsapp_app_secret_field():
	if not frappe.db.exists("DocType", "WhatsApp Account"):
		return

	create_custom_fields(
		{
			"WhatsApp Account": [
				{
					"fieldname": "crm_app_secret",
					"fieldtype": "Password",
					"label": "Meta App Secret",
					"description": "Used by PARAMA CRM to verify X-Hub-Signature-256 on inbound webhooks.",
					"insert_after": "webhook_verify_token",
					"no_copy": 1,
				}
			]
		},
		ignore_validate=True,
	)
	frappe.clear_cache(doctype="WhatsApp Account")


def _migrate_exotel_webhook_token():
	if not frappe.db.exists("DocType", "CRM Exotel Settings"):
		return

	token = frappe.db.get_single_value("CRM Exotel Settings", "webhook_verify_token")
	if not token or token == "*****":
		return

	set_encrypted_password(
		"CRM Exotel Settings",
		"CRM Exotel Settings",
		token,
		"webhook_verify_token",
	)
	frappe.db.set_single_value("CRM Exotel Settings", "webhook_verify_token", "*****")


def _purge_facebook_page_tokens():
	if not frappe.db.exists("DocType", "Facebook Page"):
		return
	if not frappe.db.has_column("Facebook Page", "access_token"):
		return

	frappe.db.sql(
		"""UPDATE `tabFacebook Page`
		SET `access_token` = NULL
		WHERE COALESCE(`access_token`, '') != ''"""
	)
