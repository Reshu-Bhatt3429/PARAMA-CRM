from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from crm.api.event import _send_system_notification


class TestEventNotificationSecurity(FrappeTestCase):
	def test_event_details_are_sent_only_to_enabled_event_users(self):
		notification = frappe._dict(
			owner="owner@example.com",
			event_participants=["agent@example.com", "external@example.net"],
			event_name="EVT-1",
			subject="Private meeting",
		)
		with (
			patch(
				"crm.api.event.frappe.get_all",
				return_value=["owner@example.com", "agent@example.com"],
			) as get_all,
			patch("crm.api.event.frappe.publish_realtime") as publish,
		):
			_send_system_notification(notification)

		self.assertEqual(get_all.call_args.kwargs["filters"]["enabled"], 1)
		self.assertCountEqual(
			[user_call.kwargs["user"] for user_call in publish.call_args_list],
			["owner@example.com", "agent@example.com"],
		)
		for user_call in publish.call_args_list:
			self.assertEqual(user_call.args, ("event_notification", notification))

	def test_event_without_users_is_not_broadcast(self):
		with patch("crm.api.event.frappe.publish_realtime") as publish:
			_send_system_notification(frappe._dict())

		publish.assert_not_called()
