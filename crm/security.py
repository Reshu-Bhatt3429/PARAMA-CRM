"""Application-level response headers and production configuration checks."""

import frappe
from frappe import _
from frappe.utils import cint


def add_security_headers(response, request):
	"""Add headers that are safe for the CRM and API routes.

	The CSP is intentionally additive rather than a strict script policy: Form
	Scripts are a documented System Manager feature and currently require
	dynamic JavaScript evaluation.
	"""
	if not response or not request:
		return

	path = request.path or ""
	is_crm = path == "/crm" or path.startswith("/crm/")
	is_api = path == "/api" or path.startswith("/api/")
	is_public_form = path == "/crm-form" or path.startswith("/crm-form/")
	is_unsubscribe = path == "/unsubscribe"
	is_invitation = path == "/accept-invitation"
	if not (is_crm or is_api or is_public_form or is_unsubscribe or is_invitation):
		return

	response.headers.setdefault("X-Content-Type-Options", "nosniff")
	response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
	response.headers.setdefault("X-Permitted-Cross-Domain-Policies", "none")
	response.headers.setdefault(
		"Permissions-Policy",
		"camera=(self), geolocation=(self), microphone=(self), payment=(), usb=()",
	)
	if is_api or is_public_form or is_unsubscribe or is_invitation:
		response.headers.setdefault("X-Robots-Tag", "noindex, nofollow, noarchive")

	if is_crm:
		response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
		response.headers.setdefault(
			"Content-Security-Policy",
			"base-uri 'self'; object-src 'none'; frame-ancestors 'self'",
		)
	elif is_public_form:
		# The page controller normally supplies a nonce policy. This fail-closed
		# fallback keeps a rendering error from turning the public page unrestricted.
		response.headers.setdefault(
			"Content-Security-Policy",
			"default-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'self'",
		)
	elif is_unsubscribe:
		response.headers.setdefault("X-Frame-Options", "DENY")
		response.headers.setdefault(
			"Content-Security-Policy",
			"default-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'",
		)
	elif is_invitation:
		response.headers.setdefault("X-Frame-Options", "DENY")
		response.headers.setdefault(
			"Content-Security-Policy",
			"default-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'",
		)

	if request.is_secure:
		response.headers.setdefault(
			"Strict-Transport-Security",
			"max-age=31536000; includeSubDomains",
		)


def get_production_configuration_issues() -> list[str]:
	issues = []
	conf = frappe.conf

	if cint(conf.get("ignore_csrf")):
		issues.append("CSRF protection is disabled (ignore_csrf=1).")
	if cint(conf.get("developer_mode")):
		issues.append("Developer mode is enabled.")
	if cint(conf.get("allow_tests")):
		issues.append("Test execution is enabled on the site.")
	if cint(conf.get("server_script_enabled")):
		issues.append("Server Scripts are enabled.")
	if cint(conf.get("maintenance_mode")):
		issues.append("The site is still in maintenance mode.")
	if not conf.get("encryption_key"):
		issues.append("The site encryption key is missing.")
	if conf.get("demo_username") or conf.get("demo_password"):
		issues.append("Live demo credentials are configured.")

	host_name = str(conf.get("host_name") or "").strip()
	if not host_name:
		issues.append("The public host_name is not configured.")
	elif not host_name.lower().startswith("https://"):
		issues.append("The public host_name must use HTTPS.")

	issues.extend(_whatsapp_secret_issues())
	issues.extend(_exotel_secret_issues())
	return issues


def assert_production_configuration():
	issues = get_production_configuration_issues()
	if issues:
		message = _("Production readiness checks failed:")
		message += "\n" + "\n".join(f"- {issue}" for issue in issues)
		frappe.throw(message, frappe.ValidationError)
	return {"ok": True}


def _whatsapp_secret_issues() -> list[str]:
	if "frappe_whatsapp" not in frappe.get_installed_apps():
		return []
	if not frappe.db.exists("DocType", "WhatsApp Account"):
		return []

	accounts = frappe.get_all("WhatsApp Account", filters={"status": "Active"}, pluck="name")
	if not accounts:
		return []
	if not frappe.get_meta("WhatsApp Account").has_field("crm_app_secret"):
		return ["WhatsApp webhook signature protection has not been migrated."]

	missing = [
		name
		for name in accounts
		if not frappe.get_doc("WhatsApp Account", name).get_password("crm_app_secret", raise_exception=False)
	]
	return [f"Meta App Secret is missing for active WhatsApp account: {name}." for name in missing]


def _exotel_secret_issues() -> list[str]:
	if not frappe.db.exists("DocType", "CRM Exotel Settings"):
		return []

	settings = frappe.get_single("CRM Exotel Settings")
	if not settings.enabled:
		return []
	if settings.get_password("webhook_verify_token", raise_exception=False):
		return []
	return ["Exotel is enabled without an encrypted webhook verify token."]
