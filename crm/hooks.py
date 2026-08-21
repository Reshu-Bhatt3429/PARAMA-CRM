app_name = "crm"
app_title = "PARAMA CRM"
app_publisher = "PARAMA CRM"
app_description = "Travel sales CRM — leads, itineraries and follow-ups in one place"
app_email = ""
app_license = "AGPLv3"
app_icon_url = "/assets/crm/images/logo.svg"
app_icon_title = "CRM"
app_icon_route = "/crm"

# Apps
# ------------------

# required_apps = []
add_to_apps_screen = [
	{
		"name": "crm",
		"logo": "/assets/crm/images/logo.svg",
		"title": "CRM",
		"route": "/crm",
		"has_permission": "crm.api.check_app_permission",
	}
]

get_site_info = "crm.activation.get_site_info"

export_python_type_annotations = True
require_type_annotated_api_methods = True

# Browser hardening for CRM and API responses. Public website pages are left
# alone because they can intentionally be embedded by customers.
after_request = ["crm.security.add_security_headers"]

# Includes in <head>
# ------------------

# include js, css files in header of desk.html
# app_include_css = "/assets/crm/css/crm.css"
# app_include_js = "/assets/crm/js/crm.js"

# include js, css files in header of web template
# web_include_css = "/assets/crm/css/crm.css"
# web_include_js = "/assets/crm/js/crm.js"

# include custom scss in every website theme (without file extension ".scss")
# website_theme_scss = "crm/public/scss/website"

# include js, css files in header of web form
# webform_include_js = {"doctype": "public/js/doctype.js"}
# webform_include_css = {"doctype": "public/css/doctype.css"}

# include js in page
# page_js = {"page" : "public/js/file.js"}

# include js in doctype views
doctype_js = {
	"Quotation": "public/js/erpnext_quotation_prefill.js",
	"Sales Order": "public/js/erpnext_sales_order_customer.js",
	"CRM Lead": "public/js/domain_enrichment.js",
	"CRM Organization": "public/js/domain_enrichment.js",
	"CRM Deal": "public/js/domain_enrichment.js",
}
# doctype_list_js = {"doctype" : "public/js/doctype_list.js"}
# doctype_tree_js = {"doctype" : "public/js/doctype_tree.js"}
# doctype_calendar_js = {"doctype" : "public/js/doctype_calendar.js"}

# Home Pages
# ----------

# application home page (will override Website Settings)
# home_page = "login"

# website user home page (by Role)
# role_home_page = {
# "Role": "home_page"
# }

website_route_rules = [
	{"from_route": "/crm/<path:app_path>", "to_route": "crm"},
	{"from_route": "/crm-form/<route>", "to_route": "crm_form"},
	{"from_route": "/accept-invitation", "to_route": "accept_invitation"},
]

# Generators
# ----------

# automatically create page for each record of this doctype
# website_generators = ["Web Page"]

# Jinja
# ----------

# add methods and filters to jinja environment
# jinja = {
# "methods": "crm.utils.jinja_methods",
# "filters": "crm.utils.jinja_filters"
# }

# Setup wizard
# setup_wizard_requires = "assets/crm/js/setup_wizard.js"
# setup_wizard_stages = "crm.setup.setup_wizard.setup_wizard.get_setup_stages"
setup_wizard_complete = "crm.demo.api.create_demo_data"
# setup_wizard_test = "crm.setup.setup_wizard.test_setup_wizard.run_setup_wizard_test"

# Installation
# ------------

before_install = "crm.install.before_install"
after_install = "crm.install.after_install"

# Uninstallation
# ------------

before_uninstall = "crm.uninstall.before_uninstall"
# after_uninstall = "crm.uninstall.after_uninstall"

# Integration Setup
# ------------------
# To set up dependencies/integrations with other apps
# Name of the app being installed is passed as an argument

# before_app_install = "crm.utils.before_app_install"
# after_app_install = "crm.utils.after_app_install"

# Integration Cleanup
# -------------------
# To clean up dependencies/integrations with other apps
# Name of the app being uninstalled is passed as an argument

# before_app_uninstall = "crm.utils.before_app_uninstall"
# after_app_uninstall = "crm.utils.after_app_uninstall"

# Desk Notifications
# ------------------
# See frappe.core.notifications.get_notification_config

# notification_config = "crm.notifications.get_notification_config"

# Permissions
# -----------
# Permissions evaluated in scripted ways

permission_query_conditions = {
	"CRM Lead": "crm.permissions.org_hierarchy.get_lead_permission_query_conditions",
	"CRM Deal": "crm.permissions.org_hierarchy.get_deal_permission_query_conditions",
	"CRM Notification": "crm.fcrm.doctype.crm_notification.crm_notification.get_permission_query_conditions",
	"CRM WhatsApp Followup": "crm.api.followup_engine.get_followup_permission_query_conditions",
	"CRM Itinerary": "crm.api.itinerary.get_itinerary_permission_query_conditions",
	"CRM Snippet": "crm.api.snippets.get_snippet_permission_query_conditions",
	"CRM User Preference": "crm.fcrm.doctype.crm_user_preference.crm_user_preference.get_permission_query_conditions",
	# Item 29: an invoice has no scope of its own. It belongs to the deal it bills,
	# and the deal's org-hierarchy conditions are the answer -- the same shape the
	# itinerary uses for its lead.
	"CRM Invoice": "crm.fcrm.doctype.crm_invoice.crm_invoice.get_invoice_permission_query_conditions",
}

has_permission = {
	"CRM Lead": "crm.permissions.org_hierarchy.has_lead_permission",
	"CRM Deal": "crm.permissions.org_hierarchy.has_deal_permission",
	"CRM Notification": "crm.fcrm.doctype.crm_notification.crm_notification.has_permission",
	"CRM WhatsApp Followup": "crm.api.followup_engine.has_followup_permission",
	"CRM Itinerary": "crm.api.itinerary.has_itinerary_permission",
	"CRM Snippet": "crm.api.snippets.has_snippet_permission",
	"CRM User Preference": "crm.fcrm.doctype.crm_user_preference.crm_user_preference.has_permission",
	"CRM Invoice": "crm.fcrm.doctype.crm_invoice.crm_invoice.has_invoice_permission",
}

# DocType Class
# ---------------
# Frappe 16+ composes these mixins with the installed controller (including an
# ERPNext override) instead of replacing it and making app ordering significant.
extend_doctype_class = {
	"Contact": ["crm.overrides.contact.CustomContact"],
	"Email Template": ["crm.overrides.email_template.CustomEmailTemplate"],
}

# Document Events
# ---------------
# Hook on document methods and events

doc_events = {
	"Contact": {
		"validate": ["crm.api.contact.validate", "crm.contact_keys.set_contact_keys"],
	},
	"CRM Lead": {
		"validate": ["crm.contact_keys.set_contact_keys"],
		# Item 16: mini workflow rules. Behind `workflow_rules_enabled`, default
		# OFF. `on_update` fires on every save of the hottest doctype in the app,
		# so the engine's first two steps are a `frappe.local` attribute read
		# (the depth ceiling) and one Redis read (the flag and the rule table).
		# With the flag off, or with no rule for this doctype and event, it adds
		# ZERO queries to a save. Actions run after the commit.
		"after_insert": ["crm.workflows.after_insert"],
		"on_update": ["crm.workflows.on_update"],
	},
	"FCRM Settings": {
		# The workflow engine caches the master flag in Redis. Saving the settings
		# is the ordinary way that flag moves, and this is what makes the cache
		# correct rather than merely fast.
		"on_update": ["crm.workflows.on_settings_update"],
	},
	"Notification Log": {
		"before_insert": ["crm.extends.notification_log.before_insert"],
	},
	"ToDo": {
		"after_insert": ["crm.api.todo.after_insert"],
		"on_update": ["crm.api.todo.on_update"],
	},
	"Communication": {
		"after_insert": [
			"crm.utils.on_communication_insert",
			# Item 21: a customer who answers stops their email sequence. Matched on
			# In-Reply-To first, then on the lead's normalised address, because mail
			# clients strip headers and a new thread is still an answer. Behind
			# `email_sequences_enabled`, default OFF: with the flag off this returns
			# before it reads a row.
			"crm.sequences.email.handle_inbound_reply",
		],
		"on_update": ["crm.utils.on_communication_update"],
	},
	"Email Queue": {
		# Item 21 / master spec §7: every promotional message carries a
		# List-Unsubscribe header. Frappe v15 has no machinery for it, and the
		# header has to go on the built MIME message, which is what an Email Queue
		# row holds. Armed for the length of one adapter call by
		# `crm.outbound.deliver_recipient`; a message that is not a sequence send
		# never sees this hook do anything.
		"before_insert": ["crm.sequences.unsubscribe.add_list_unsubscribe_header"],
	},
	"Comment": {
		"after_insert": ["crm.utils.on_comment_insert"],
		"on_update": ["crm.api.comment.on_update"],
	},
	"WhatsApp Message": {
		"validate": ["crm.api.whatsapp.validate"],
		"on_update": ["crm.api.whatsapp.on_update"],
		"after_insert": ["crm.api.followup_engine.handle_message_after_insert"],
	},
	"CRM Deal": {
		"validate": ["crm.contact_keys.set_contact_keys"],
		# Item 16: see the CRM Lead entry above for what this costs on a save.
		"after_insert": ["crm.workflows.after_insert"],
		"on_update": [
			"crm.fcrm.doctype.erpnext_crm_settings.erpnext_crm_settings.create_customer_in_erpnext",
			"crm.workflows.on_update",
		],
	},
	"Sales Order": {
		"before_validate": [
			"crm.fcrm.doctype.erpnext_crm_settings.erpnext_crm_settings.create_customer_on_sales_order"
		],
	},
	"Item": {
		"after_insert": ["crm.integrations.erpnext.item.after_insert"],
		"on_update": ["crm.integrations.erpnext.item.on_update"],
		"before_rename": ["crm.integrations.erpnext.item.before_rename"],
		"after_rename": ["crm.integrations.erpnext.item.after_rename"],
		"on_trash": ["crm.integrations.erpnext.item.on_trash"],
	},
	"User Permission": {
		"before_validate": ["crm.integrations.erpnext.user_permission.before_validate"],
		"after_insert": ["crm.integrations.erpnext.user_permission.after_insert"],
		"on_update": ["crm.integrations.erpnext.user_permission.on_update"],
		"on_trash": ["crm.integrations.erpnext.user_permission.on_trash"],
	},
	"DocShare": {
		"before_validate": ["crm.integrations.erpnext.doc_share.before_validate"],
		"after_insert": ["crm.integrations.erpnext.doc_share.after_insert"],
		"on_update": ["crm.integrations.erpnext.doc_share.on_update"],
		"on_trash": ["crm.integrations.erpnext.doc_share.on_trash"],
	},
	"User": {
		"before_validate": ["crm.api.live_demo.validate_user"],
		"validate_reset_password": ["crm.api.live_demo.validate_reset_password"],
	},
}

# Scheduled Tasks
# ---------------

scheduler_events = {
	"all": ["crm.api.event.trigger_offset_event_notifications"],
	"hourly": [
		"crm.api.event.trigger_hourly_event_notifications",
		"crm.api.whatsapp_followups.notify_pending_followups",
		# Moved off `daily` in Stage 4. `daily` fires at the start of the day,
		# which is inside the follow-up engine's default quiet window, and master
		# spec §5 item 22 requires the digest to respect it. The job is now
		# at-most-once per manager per day by itself and returns immediately
		# while quiet hours are open, so it shifts rather than repeats.
		"crm.api.whatsapp_followups.send_daily_digest",
		"crm.api.followup_engine.process_followups",
		"crm.api.itinerary.cleanup_public_itinerary_pdfs",
		# Item 25: retire expired quote links and delete the private PDF each one
		# held. Never raises. Not covered by the itinerary sweep above: that one
		# removes temporary PUBLIC files on CRM Itinerary, while a quote's file is
		# private, lives on CRM Deal, and dies with its CRM Document Link row.
		"crm.api.quote.cleanup_quote_links",
		# Item 29: the same for invoice links, which have their own, longer TTL --
		# a customer opens an invoice again when they pay the balance, which on a
		# travel booking is weeks after the deposit.
		"crm.api.invoices.cleanup_invoice_links",
		# Item 29: one payment-reminder ladder per payment-schedule row (due date,
		# +7 days, +14 days). Behind `invoice_reminders_enabled` AND
		# `invoices_enabled`, both default OFF; while either is off this reads no
		# invoice row. It only CREATES outbound jobs -- delivery still needs
		# `outbound_engine_enabled` and the sweep below.
		"crm.invoice_reminders.send_invoice_reminders",
		# Behind `outbound_engine_enabled`, default OFF. While the flag is off these
		# return without reading a single job row.
		"crm.outbound.process_scheduled_jobs",
		# Reads the Email Queue's own verdict back onto the recipients this app
		# queued, so "Sent" keeps meaning "the framework says sent".
		"crm.outbound.sweep_delivery_states",
	],
	"daily": [
		"crm.api.event.trigger_daily_event_notifications",
		"crm.fcrm.doctype.crm_invitation.crm_invitation.expire_invitations",
		"crm.fcrm.doctype.crm_view_settings.crm_view_settings.clear_old_versions",
		"crm.telemetry.capture_feature_state",
		# Deal-health flags. Behind `deal_health_enabled`, default OFF; while the
		# flag is off this reads no deal row and writes nothing. It holds a
		# per-job lock and resumes from a watermark, so a run that overlaps or
		# crashes costs one batch rather than the night.
		"crm.deal_health.sweep_deal_health",
		# Item 16: keep 90 days of workflow execution log. Bounded (500 rows per
		# batch, 20 batches per night) so a site that was never cleaned catches
		# up over several nights instead of holding one enormous transaction.
		# Not behind the feature flag: rows written while the flag was on still
		# have to age out after it is turned off.
		"crm.workflows.cleanup_execution_log",
	],
	"weekly": ["crm.api.event.trigger_weekly_event_notifications"],
	"daily_long": ["crm.lead_syncing.background_sync.sync_leads_from_sources_daily"],
	"hourly_long": ["crm.lead_syncing.background_sync.sync_leads_from_sources_hourly"],
	"monthly_long": ["crm.lead_syncing.background_sync.sync_leads_from_sources_monthly"],
	"cron": {
		"*/5 * * * *": ["crm.lead_syncing.background_sync.sync_leads_from_sources_5_minutes"],
		"*/10 * * * *": ["crm.lead_syncing.background_sync.sync_leads_from_sources_10_minutes"],
		"*/15 * * * *": [
			"crm.lead_syncing.background_sync.sync_leads_from_sources_15_minutes",
			# Task due-date reminders. Registered on exactly ONE schedule, on
			# purpose: the event-reminder path above sits on both `all` and
			# `hourly` and therefore double-fires. Behind `task_reminders_enabled`,
			# default OFF, and every delivery claims a unique CRM Reminder Log key
			# first, so even a second schedule could not produce a second reminder.
			"crm.reminders.send_task_reminders",
		],
	},
}

# Testing
# -------

before_tests = "crm.tests.before_tests"

# Overriding Methods
# ------------------------------
#
override_whitelisted_methods = {
	"frappe_whatsapp.utils.webhook.webhook": "crm.integrations.whatsapp_security.webhook",
	"frappe.model.utils.user_settings.save": "crm.api.user_settings.save",
}

# each overriding function accepts a `data` argument;
# generated from the base implementation of the doctype dashboard,
# along with any modifications made in other Frappe apps
# override_doctype_dashboards = {
# "Task": "crm.task.get_dashboard_data"
# }

# exempt linked doctypes from being automatically cancelled
#
# auto_cancel_exempted_doctypes = ["Auto Repeat"]

# Ignore links to specified DocTypes when deleting documents
# -----------------------------------------------------------

ignore_links_on_delete = ["Failed Lead Sync Log"]

# Request Events
# ----------------
# before_request = ["crm.utils.before_request"]
# after_request = ["crm.utils.after_request"]

# Job Events
# ----------
# before_job = ["crm.utils.before_job"]
# after_job = ["crm.utils.after_job"]

# User Data Protection
# --------------------

# user_data_fields = [
# {
# "doctype": "{doctype_1}",
# "filter_by": "{filter_by}",
# "redact_fields": ["{field_1}", "{field_2}"],
# "partial": 1,
# },
# {
# "doctype": "{doctype_2}",
# "filter_by": "{filter_by}",
# "partial": 1,
# },
# {
# "doctype": "{doctype_3}",
# "strict": False,
# },
# {
# "doctype": "{doctype_4}"
# }
# ]

# Authentication and authorization
# --------------------------------

# auth_hooks = [
# "crm.auth.validate"
# ]

after_migrate = [
	"crm.fcrm.doctype.fcrm_settings.fcrm_settings.after_migrate",
	"crm.api.whatsapp.add_roles",
	"crm.api.followup_engine.add_followup_roles",
	"crm.api.itinerary.add_itinerary_roles",
	"crm.api.itinerary.install_print_format",
	# Item 25. Same contract as the itinerary's: the HTML lives in a file so it
	# is reviewable in git, and the Print Format row is rewritten only when that
	# file changed, so an administrator's own edit survives a migrate.
	"crm.api.quote.install_quote_print_format",
	# Item 29. Same contract as the quote's format: the HTML lives in a file and
	# the Print Format row is rewritten only when that file changed.
	"crm.api.invoices.install_invoice_print_format",
	"crm.fcrm.doctype.crm_invoice.crm_invoice.add_invoice_roles",
	# Placeholder SAC codes, each flagged "verify with your CA". Idempotent by
	# code: a row an administrator edited is never overwritten.
	"crm.fcrm.doctype.crm_sac_code.crm_sac_code.seed_sac_codes",
	"crm.domain_enrichment.install.seed_default_rules_and_mappings",
	"crm.install.add_default_scripts",
	"crm.install.add_web_form_custom_fields",
]

standard_dropdown_items = [
	{
		"name1": "app_selector",
		"label": "Apps",
		"type": "Route",
		"route": "#",
		"is_standard": 1,
	},
	{
		"name1": "settings",
		"label": "Settings",
		"type": "Route",
		"icon": "settings",
		"route": "#",
		"is_standard": 1,
	},
	{
		"name1": "about",
		"label": "About",
		"type": "Route",
		"icon": "info",
		"route": "#",
		"is_standard": 1,
	},
	{
		"name1": "separator",
		"label": "",
		"type": "Separator",
		"is_standard": 1,
	},
	{
		"name1": "logout",
		"label": "Log out",
		"type": "Route",
		"icon": "log-out",
		"route": "#",
		"is_standard": 1,
	},
]
