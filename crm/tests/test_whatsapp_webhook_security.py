import hashlib
import hmac

from frappe.tests.utils import FrappeTestCase

from crm.integrations.whatsapp_security import is_valid_meta_signature


class TestWhatsAppWebhookSignature(FrappeTestCase):
	def test_accepts_matching_sha256_signature(self):
		payload = b'{"entry":[]}'
		secret = "meta-app-secret"
		digest = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()

		self.assertTrue(is_valid_meta_signature(payload, f"sha256={digest}", secret))

	def test_rejects_modified_body(self):
		payload = b'{"entry":[]}'
		secret = "meta-app-secret"
		digest = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()

		self.assertFalse(is_valid_meta_signature(payload + b" ", f"sha256={digest}", secret))

	def test_rejects_missing_or_malformed_credentials(self):
		self.assertFalse(is_valid_meta_signature(b"body", None, "secret"))
		self.assertFalse(is_valid_meta_signature(b"body", "sha1=bad", "secret"))
		self.assertFalse(is_valid_meta_signature(b"body", "sha256=bad", "secret"))
