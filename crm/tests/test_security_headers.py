from types import SimpleNamespace

from frappe.tests.utils import FrappeTestCase
from werkzeug.wrappers import Response

from crm.security import add_security_headers


class TestSecurityHeaders(FrappeTestCase):
	def test_crm_response_gets_browser_security_headers(self):
		response = Response("ok")
		request = SimpleNamespace(path="/crm/leads", is_secure=True)

		add_security_headers(response, request)

		self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
		self.assertEqual(response.headers["X-Frame-Options"], "SAMEORIGIN")
		self.assertIn("microphone=(self)", response.headers["Permissions-Policy"])
		self.assertEqual(response.headers["X-Permitted-Cross-Domain-Policies"], "none")
		self.assertIn("frame-ancestors 'self'", response.headers["Content-Security-Policy"])
		self.assertIn("max-age=31536000", response.headers["Strict-Transport-Security"])

	def test_unrelated_website_response_is_not_restricted(self):
		response = Response("ok")
		request = SimpleNamespace(path="/public-form", is_secure=False)

		add_security_headers(response, request)

		self.assertNotIn("X-Frame-Options", response.headers)
		self.assertNotIn("Content-Security-Policy", response.headers)

	def test_public_form_gets_fail_closed_headers(self):
		response = Response("ok")
		request = SimpleNamespace(path="/crm-form/contact-us", is_secure=True)

		add_security_headers(response, request)

		self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
		self.assertEqual(response.headers["X-Robots-Tag"], "noindex, nofollow, noarchive")
		self.assertIn("default-src 'none'", response.headers["Content-Security-Policy"])
		self.assertNotIn("X-Frame-Options", response.headers)

	def test_invitation_confirmation_is_not_embeddable(self):
		response = Response("ok")
		request = SimpleNamespace(path="/accept-invitation", is_secure=False)

		add_security_headers(response, request)

		self.assertEqual(response.headers["X-Frame-Options"], "DENY")
		self.assertIn("form-action 'self'", response.headers["Content-Security-Policy"])
		self.assertIn("frame-ancestors 'none'", response.headers["Content-Security-Policy"])
		self.assertEqual(response.headers["X-Robots-Tag"], "noindex, nofollow, noarchive")

	def test_existing_header_is_preserved(self):
		response = Response("ok", headers={"Referrer-Policy": "no-referrer"})
		request = SimpleNamespace(path="/api/method/x", is_secure=False)

		add_security_headers(response, request)

		self.assertEqual(response.headers["Referrer-Policy"], "no-referrer")
		self.assertEqual(response.headers["X-Robots-Tag"], "noindex, nofollow, noarchive")
