import frappe
from frappe.tests.utils import FrappeTestCase

from crm.overrides.contact import CustomContact
from crm.overrides.email_template import CustomEmailTemplate


class TestFrappe17DocTypeExtensions(FrappeTestCase):
	def test_contact_list_data_is_composed_onto_the_core_controller(self):
		doc = frappe.new_doc("Contact")

		self.assertIn(CustomContact, type(doc).__mro__)
		self.assertIn("email_id", doc.default_list_data()["rows"])

	def test_email_template_list_data_is_composed_onto_the_core_controller(self):
		doc = frappe.new_doc("Email Template")

		self.assertIn(CustomEmailTemplate, type(doc).__mro__)
		self.assertIn("response_html", doc.default_list_data()["rows"])
