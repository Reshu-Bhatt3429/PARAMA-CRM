from frappe.tests.utils import FrappeTestCase

from crm.api.auth import safe_provider_icon_url


class TestAuthSecurity(FrappeTestCase):
	def test_provider_icon_rejects_executable_and_ambiguous_urls(self):
		for value in ("javascript:alert(1)", "data:image/svg+xml,<svg/>", "//attacker.example/icon.svg"):
			with self.subTest(value=value):
				self.assertIsNone(safe_provider_icon_url(value))

	def test_provider_icon_accepts_https_and_same_site_paths(self):
		self.assertEqual(safe_provider_icon_url("https://example.com/icon.svg"), "https://example.com/icon.svg")
		self.assertEqual(safe_provider_icon_url("/files/icon.svg"), "/files/icon.svg")
