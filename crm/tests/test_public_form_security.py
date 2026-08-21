from frappe.tests.utils import FrappeTestCase

from crm.api.form import get_safe_form_redirect_url
from crm.www.crm_form import build_form_csp, get_allowed_embedding_domains


class TestPublicFormSecurity(FrappeTestCase):
	def test_redirect_accepts_only_local_paths_or_https(self):
		self.assertEqual(get_safe_form_redirect_url("/crm/thanks"), "/crm/thanks")
		self.assertEqual(
			get_safe_form_redirect_url("https://travel.example/thanks"),
			"https://travel.example/thanks",
		)

		for value in (
			"javascript:alert(1)",
			"data:text/html,<script>alert(1)</script>",
			"//attacker.example/thanks",
			"http://attacker.example/thanks",
			"/\\attacker.example/thanks",
			"https://travel.example/thanks\njavascript:alert(1)",
		):
			with self.subTest(value=value):
				self.assertEqual(get_safe_form_redirect_url(value), "")

	def test_csp_uses_nonce_and_filters_embedding_domains(self):
		raw_domains = "https://forms.example *.partner.example https://evil.example;script-src *"
		self.assertEqual(
			get_allowed_embedding_domains(raw_domains),
			["https://forms.example", "*.partner.example"],
		)

		policy = build_form_csp("test-nonce", raw_domains)
		self.assertIn("script-src 'nonce-test-nonce'", policy)
		self.assertIn("style-src 'nonce-test-nonce'", policy)
		self.assertIn("frame-ancestors 'self' https://forms.example *.partner.example", policy)
		self.assertNotIn("script-src *", policy)
