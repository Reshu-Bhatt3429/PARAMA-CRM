"""Read-only confirmation page for a CRM invitation bearer token."""

import secrets

import frappe

no_cache = 1


def get_context(context):
	context.no_cache = 1
	context.csp_nonce = secrets.token_urlsafe(24)
	context.csrf_token = frappe.sessions.get_csrf_token()
	context.invitation_key = frappe.utils.cstr(frappe.form_dict.get("key")).strip()
	context.is_valid = bool(
		context.invitation_key
		and frappe.db.exists(
			"CRM Invitation",
			{
				"key": context.invitation_key,
				"status": "Pending",
				"creation": [">=", frappe.utils.add_days(frappe.utils.now_datetime(), -3)],
			},
		)
	)

	frappe.local.response_headers["Content-Security-Policy"] = "; ".join(
		(
			"default-src 'none'",
			f"style-src 'nonce-{context.csp_nonce}'",
			"base-uri 'none'",
			"form-action 'self'",
			"frame-ancestors 'none'",
		)
	)
	return context
