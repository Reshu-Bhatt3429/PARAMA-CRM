"""Authenticate Meta webhook POSTs before delegating to frappe_whatsapp."""

import hashlib
import hmac
import json

import frappe
from frappe import _

SIGNATURE_HEADER = "X-Hub-Signature-256"
APP_SECRET_FIELD = "crm_app_secret"


def is_valid_meta_signature(payload: bytes, signature: str | None, app_secret: str | None) -> bool:
	if not payload or not signature or not app_secret:
		return False

	prefix, separator, supplied_digest = signature.partition("=")
	if separator != "=" or prefix.lower() != "sha256" or len(supplied_digest) != 64:
		return False

	expected_digest = hmac.new(app_secret.encode(), payload, hashlib.sha256).hexdigest()
	return hmac.compare_digest(supplied_digest.lower(), expected_digest)


def _account_name(payload: bytes) -> str | None:
	try:
		data = json.loads(payload)
		entry = (data.get("entry") or [{}])[0]
		value = (entry.get("changes") or [{}])[0].get("value") or {}
		phone_id = (value.get("metadata") or {}).get("phone_number_id")
		business_id = entry.get("id")
	except (AttributeError, IndexError, TypeError, ValueError):
		return None

	if phone_id:
		name = frappe.db.get_value("WhatsApp Account", {"phone_id": phone_id}, "name")
		if name:
			return name
	if business_id:
		name = frappe.db.get_value("WhatsApp Account", {"business_id": business_id}, "name")
		if name:
			return name

	accounts = frappe.get_all("WhatsApp Account", filters={"status": "Active"}, pluck="name", limit=2)
	return accounts[0] if len(accounts) == 1 else None


def _get_app_secret(payload: bytes) -> str | None:
	if not frappe.db.exists("DocType", "WhatsApp Account"):
		return None
	if not frappe.get_meta("WhatsApp Account").has_field(APP_SECRET_FIELD):
		return None

	account_name = _account_name(payload)
	if not account_name:
		return None

	return frappe.get_doc("WhatsApp Account", account_name).get_password(
		APP_SECRET_FIELD,
		raise_exception=False,
	)


def _validate_post_request():
	payload = frappe.request.get_data(cache=True)
	signature = frappe.request.headers.get(SIGNATURE_HEADER)
	app_secret = _get_app_secret(payload)

	# A local developer can configure the field after installing/upgrading the
	# companion app. Production never accepts an unsigned webhook.
	if not app_secret and frappe.conf.developer_mode:
		return

	if not is_valid_meta_signature(payload, signature, app_secret):
		frappe.throw(_("Invalid WhatsApp webhook signature"), frappe.PermissionError)


@frappe.whitelist(allow_guest=True)  # nosemgrep
def webhook():
	if frappe.request.method == "POST":
		_validate_post_request()

	from frappe_whatsapp.utils.webhook import webhook as upstream_webhook

	return upstream_webhook()
