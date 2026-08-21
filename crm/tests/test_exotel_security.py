from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from crm.fcrm.doctype.crm_exotel_settings.crm_exotel_settings import validate_exotel_subdomain
from crm.integrations.exotel.handler import _publish_call_update


class TestExotelSecurity(FrappeTestCase):
	def test_exotel_api_host_accepts_only_exotel_domains(self):
		self.assertEqual(validate_exotel_subdomain("API.IN.EXOTEL.COM."), "api.in.exotel.com")

		for value in ("127.0.0.1", "metadata.google.internal", "exotel.com.attacker.test", "api.exotel.com@evil.test"):
			with self.subTest(value=value), self.assertRaises(frappe.ValidationError):
				validate_exotel_subdomain(value)

	def test_call_payload_is_published_only_to_the_assigned_agent(self):
		payload = {"AgentEmail": "agent@example.com", "CallFrom": "+911234567890"}
		with (
			patch("crm.integrations.exotel.handler.frappe.db.exists", return_value=True),
			patch("crm.integrations.exotel.handler.frappe.publish_realtime") as publish,
		):
			_publish_call_update(payload)

		publish.assert_called_once_with("exotel_call", payload, user="agent@example.com")

	def test_call_payload_without_a_known_agent_is_not_broadcast(self):
		with patch("crm.integrations.exotel.handler.frappe.publish_realtime") as publish:
			_publish_call_update({"CallFrom": "+911234567890"})

		publish.assert_not_called()
