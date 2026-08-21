"""Regression coverage for state-changing whitelisted API methods."""

import frappe
from frappe.tests.utils import FrappeTestCase

from crm import api as crm_api
from crm.api import (
	assignment_rule,
	comment,
	contact,
	dashboard,
	doc,
	form,
	live_demo,
	notifications,
	settings,
	user,
	user_settings,
)
from crm.api import whatsapp as whatsapp_api
from crm.demo import api as demo_api
from crm.domain_enrichment import api as enrichment_api
from crm.fcrm.doctype.crm_call_log import crm_call_log
from crm.fcrm.doctype.crm_deal import crm_deal
from crm.fcrm.doctype.crm_fields_layout import crm_fields_layout
from crm.fcrm.doctype.crm_invitation.crm_invitation import CRMInvitation
from crm.fcrm.doctype.crm_lead import crm_lead
from crm.fcrm.doctype.crm_twilio_settings.crm_twilio_settings import CRMTwilioSettings
from crm.fcrm.doctype.crm_view_settings import crm_view_settings
from crm.fcrm.doctype.erpnext_crm_settings import erpnext_crm_settings
from crm.fcrm.doctype.fcrm_settings.fcrm_settings import FCRMSettings
from crm.integrations import api as integration_api
from crm.integrations.exotel import handler as exotel_handler
from crm.integrations.twilio import api as twilio_api
from crm.lead_syncing.doctype.failed_lead_sync_log.failed_lead_sync_log import FailedLeadSyncLog
from crm.lead_syncing.doctype.lead_sync_source import facebook
from crm.lead_syncing.doctype.lead_sync_source.lead_sync_source import LeadSyncSource

POST_ONLY_METHODS = (
	crm_api.accept_invitation,
	crm_api.invite_by_email,
	assignment_rule.duplicate_assignment_rule,
	comment.add_comment,
	contact.create_new,
	contact.set_as_primary,
	dashboard.reset_to_default,
	doc.update_quick_filters,
	doc.remove_assignments,
	doc.remove_linked_doc_reference,
	doc.delete_bulk_docs,
	form.grant_guest_link_access,
	form.save_form,
	form.set_published,
	form.delete_form,
	live_demo.login,
	notifications.mark_as_read,
	settings.create_email_account,
	user.change_password,
	user.add_existing_users,
	user.update_user_role,
	user.remove_crm_roles_from_user,
	user_settings.save,
	whatsapp_api.create_whatsapp_message,
	whatsapp_api.send_whatsapp_template,
	whatsapp_api.react_on_whatsapp_message,
	demo_api.clear_demo_data,
	enrichment_api.enrich,
	enrichment_api.retry,
	enrichment_api.enrich_preview,
	crm_call_log.create_lead_from_call_log,
	crm_deal.add_contact,
	crm_deal.remove_contact,
	crm_deal.set_primary_contact,
	crm_deal.create_deal,
	crm_fields_layout.save_fields_layout,
	CRMInvitation.accept_invitation,
	crm_lead.convert_to_deal,
	FCRMSettings.restore_defaults,
	FCRMSettings.restore_demo_data,
	CRMTwilioSettings.fetch_applications,
	crm_view_settings.create,
	crm_view_settings.update,
	crm_view_settings.delete,
	crm_view_settings.public,
	crm_view_settings.pin,
	crm_view_settings.set_as_default,
	crm_view_settings.create_or_update_standard_view,
	crm_view_settings.fetch_and_update_kanban_columns,
	erpnext_crm_settings.ERPNextCRMSettings.reset_erpnext_form_script,
	erpnext_crm_settings.ERPNextCRMSettings.run_product_sync,
	erpnext_crm_settings.dismiss_sync_issue,
	erpnext_crm_settings.get_quotation_url,
	erpnext_crm_settings.check_customer_for_quotation,
	integration_api.set_default_calling_medium,
	integration_api.add_note_to_call_log,
	integration_api.add_task_to_call_log,
	exotel_handler.make_a_call,
	twilio_api.voice,
	twilio_api.twilio_incoming_call_handler,
	twilio_api.update_recording_info,
	twilio_api.update_call_status_info,
	FailedLeadSyncLog.retry_sync,
	facebook.fetch_and_store_pages_from_facebook,
	LeadSyncSource.sync_leads,
)


class TestHTTPMethodGuards(FrappeTestCase):
	def test_state_changing_methods_are_post_only(self):
		for method in POST_ONLY_METHODS:
			with self.subTest(method=f"{method.__module__}.{method.__qualname__}"):
				self.assertEqual(
					tuple(frappe.allowed_http_methods_for_whitelisted_func[method]),
					("POST",),
				)
