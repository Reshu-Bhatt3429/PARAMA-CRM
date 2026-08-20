# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

"""Hybrid forms API.

Storage/submission/validation is delegated to Frappe's built-in `Web Form`
doctype; this module only:
  1. curates which target fields a CRM form may collect,
  2. gives the CRM builder a small, scoped CRUD surface over Web Form records,
  3. re-applies CRM-specific enrichment (source, organization, primary contact)
     on submission, which the framework's generic insert does not do.
"""

import json
import re

import frappe
from frappe import _
from frappe.utils import cint
from frappe.utils.telemetry import capture

from crm.suppression import CHANNEL_EMAIL, is_suppressed

ALLOWED_DOCTYPES = ("CRM Lead", "CRM Deal")
FORM_SOURCE = "Web Form"
FORM_MODULE = "FCRM"

AUTO_RESPONSE_DOCTYPE = "CRM Form Auto Response"
AUTO_RESPONSE_LOG_DOCTYPE = "CRM Form Auto Response Log"

# The merge fields a form author may put in an automatic reply, as
# (token, label). This tuple is the WHOLE vocabulary: the builder renders it as
# the "Insert field" menu, and `render_merge` resolves nothing outside it. An
# allowlist rather than a template engine, because the value being substituted
# is text a stranger typed into a public form.
AUTO_RESPONSE_MERGE_FIELDS = (
	("first_name", "First name"),
	("last_name", "Last name"),
	("full_name", "Full name"),
	("email", "Email"),
	("mobile_no", "Mobile number"),
	("organization_name", "Organization"),
	("record_id", "Reference number"),
	("agency_name", "Agency name"),
)

# `{{ token }}`, with any amount of whitespace inside the braces.
MERGE_TOKEN_PATTERN = re.compile(r"\{\{\s*([a-z_][a-z0-9_]*)\s*\}\}", re.IGNORECASE)

AUTO_RESPONSE_JOB = "crm.api.form.send_auto_response"


def ensure_form_source() -> str:
	"""Return the 'Web Form' CRM Lead Source, creating it once if needed."""
	if not frappe.db.exists("CRM Lead Source", FORM_SOURCE):
		frappe.get_doc({"doctype": "CRM Lead Source", "source_name": FORM_SOURCE}).insert(
			ignore_permissions=True
		)
	return FORM_SOURCE


# Fieldtypes a form can render/collect. A `Link` renders as a server-populated,
# pick-only dropdown (see crm_form.py); excludes types needing a live widget or file
# upload (Dynamic Link, Table, Attach, …).
SUPPORTED_FIELDTYPES = (
	"Data",
	"Small Text",
	"Text",
	"Long Text",
	"Text Editor",
	"HTML Editor",
	"Markdown Editor",
	"Select",
	"Link",
	"Int",
	"Float",
	"Currency",
	"Percent",
	"Check",
	"Date",
	"Datetime",
	"Time",
	"Phone",
	"Color",
)

# Never expose these as mappable fields even if their type is supported.
DENIED_FIELDNAMES = (
	"naming_series",
	# a mandatory Link the system sets — seeded as a hidden field with a default, not
	# offered in the picker.
	"status",
	"lead_name",
	"converted",
	"sla_status",
	"response_by",
	"first_response_time",
	"first_responded_on",
	"facebook_form_id",
	"facebook_lead_id",
)

# Starting layout of a brand-new form, per target doctype: labelled sections,
# each a list of columns, each column a list of fieldnames. A sensible
# contact-capture starting point the author can then edit.
SEED_LAYOUT = {
	"CRM Lead": [
		{
			"label": "Personal Details",
			"columns": [["first_name", "email"], ["last_name", "phone"]],
		},
	],
	"CRM Deal": [
		{
			"label": "Personal Details",
			"columns": [["first_name", "email"], ["last_name", "phone"]],
		},
		{
			"label": "Organization Details",
			"columns": [["organization_name"]],
		},
	],
}


def _seeded_visible_fieldnames(document_type: str) -> set:
	names = set()
	for section in SEED_LAYOUT.get(document_type, []):
		for col in section["columns"]:
			names.update(col)
	return names


def guest_can_select(doctype: str) -> bool:
	"""Whether Guest may reference `doctype` in a Link field. Gates on `select` (never
	`read`) — Frappe's minimal tier that lists record names without exposing full
	documents — so a doctype is linkable only when Guest select is deliberately granted."""
	return bool(doctype) and frappe.has_permission(doctype, ptype="select", user="Guest")


def _link_target_doctypes() -> set:
	"""Doctypes reachable via a Link field on any form-mappable target — the only
	doctypes `grant_guest_link_access` is allowed to touch, so the endpoint can't be
	used to open Guest select on an arbitrary doctype."""
	targets = set()
	for document_type in ALLOWED_DOCTYPES:
		for df in frappe.get_meta(document_type).fields:
			if df.fieldtype == "Link" and df.options:
				targets.add(df.options)
	return targets


def _mappable_fields(document_type: str) -> list[dict]:
	"""Fields of a target DocType a form may collect (shared by the picker and by
	the brand-new-form seeding)."""
	meta = frappe.get_meta(document_type)
	fields = []
	for df in meta.fields:
		if df.fieldtype not in SUPPORTED_FIELDTYPES:
			continue
		if not df.fieldname or df.fieldname in DENIED_FIELDNAMES:
			continue
		if df.hidden or df.read_only:
			continue
		# a Link is offered even when guests can't select the target yet; the builder
		# warns and offers a one-click grant (see grant_guest_link_access).
		fields.append(
			{
				"fieldname": df.fieldname,
				"label": df.label or df.fieldname,
				"fieldtype": df.fieldtype,
				"options": df.options,
				"reqd": df.reqd,
				"default": df.default,
			}
		)
	return fields


def _default_status(document_type: str) -> str | None:
	"""The status a new Lead/Deal defaults to — mirrors the doctype controllers so
	the hidden 'Status' field is pre-filled with the value the CRM would use."""
	status_dt = "CRM Lead Status" if document_type == "CRM Lead" else "CRM Deal Status"
	preferred = "New" if document_type == "CRM Lead" else "Qualification"
	if frappe.db.exists(status_dt, preferred):
		return preferred
	rows = frappe.get_all(status_dt, {"type": "Open"}, pluck="name")
	return rows[0] if rows else None


def _seed_visible_fields(document_type: str) -> list[dict]:
	"""Web-form rows for a new form's visible layout: the curated contact set,
	arranged into labelled sections and columns (with Section/Column breaks)."""
	catalog = {f["fieldname"]: f for f in _mappable_fields(document_type)}

	def _break(fieldtype, i, label=""):
		prefix = "section_break" if fieldtype == "Section Break" else "column_break"
		return {
			"fieldname": f"{prefix}_seed{i}",
			"label": label,
			"fieldtype": fieldtype,
			"options": "",
			"reqd": 0,
			"placeholder": "",
			"field_description": "",
		}

	rows = []
	n = 0
	for section in SEED_LAYOUT.get(document_type, []):
		n += 1
		rows.append(_break("Section Break", n, section.get("label") or ""))
		for ci, col in enumerate(section["columns"]):
			if ci > 0:
				n += 1
				rows.append(_break("Column Break", n))
			for fn in col:
				f = catalog.get(fn)
				if not f:
					continue
				rows.append(
					{
						"fieldname": f["fieldname"],
						"label": f["label"],
						"fieldtype": f["fieldtype"],
						"options": f["options"],
						"reqd": 1 if f["reqd"] else 0,
						"placeholder": "",
						"field_description": "",
					}
				)
	return rows


def _seed_hidden_fields(document_type: str) -> list[dict]:
	"""Mandatory fields a public visitor should not fill (e.g. Status, a Link the
	system sets). Seeded into the hidden section with a sensible default so the
	author sees them and can override the value applied on submission."""
	meta = frappe.get_meta(document_type)
	visible = _seeded_visible_fieldnames(document_type)
	hidden = []
	for df in meta.fields:
		if not df.reqd or df.fieldname in visible:
			continue
		if df.fieldtype in SUPPORTED_FIELDTYPES and df.fieldname not in DENIED_FIELDNAMES:
			continue  # a fillable mandatory field belongs in the visible layout
		default = _default_status(document_type) if df.fieldname == "status" else (df.default or "")
		hidden.append(
			{
				"fieldname": df.fieldname,
				"label": df.label or df.fieldname,
				"fieldtype": df.fieldtype,
				"options": df.options or "",
				"default": default,
			}
		)
	return hidden


def _check_manager():
	"""CRM forms are managed by CRM managers, not Website Managers. Gate here and
	then write the Web Form with ignore_permissions (the role mismatch, in code)."""
	roles = set(frappe.get_roles())
	if not roles & {"System Manager", "Sales Manager"}:
		frappe.throw(_("Not permitted"), frappe.PermissionError)


@frappe.whitelist()
def get_form_fields(document_type: str) -> list[dict]:
	"""Mappable fields of a target DocType, for the builder's field picker."""
	if document_type not in ALLOWED_DOCTYPES:
		frappe.throw(_("Forms can only map to: {0}").format(", ".join(ALLOWED_DOCTYPES)))
	return _mappable_fields(document_type)


@frappe.whitelist()
def get_hidden_seed(document_type: str) -> list[dict]:
	"""The system-managed hidden required fields for a doctype (e.g. Status) with
	their default value. Used by the builder to reconcile on a doctype switch."""
	if document_type not in ALLOWED_DOCTYPES:
		frappe.throw(_("Forms can only map to: {0}").format(", ".join(ALLOWED_DOCTYPES)))
	return _seed_hidden_fields(document_type)


@frappe.whitelist()
def link_field_guest_access(doctype: str) -> dict:
	"""Whether guests can already select `doctype`. Drives the builder's Link-field
	notice: if False, the author is warned that the public dropdown will be empty and
	is offered a one-click grant (grant_guest_link_access)."""
	_check_manager()
	return {"doctype": doctype, "guest_can_select": guest_can_select(doctype)}


@frappe.whitelist()
def grant_guest_link_access(doctype: str) -> dict:
	"""Grant Guest `select` on `doctype` so a public Link dropdown can list its records — a
	deliberate choice by a form manager (the builder warns that anyone with the form link
	will then see them). Limited to doctypes that are Link targets on a CRM form; stored as
	a site-level Custom DocPerm."""
	_check_manager()
	if doctype not in _link_target_doctypes():
		frappe.throw(_("{0} isn't a linkable field on a CRM form.").format(doctype))

	if not guest_can_select(doctype):
		from frappe.permissions import add_permission

		# a Sales Manager may run the builder but can't normally write Custom DocPerm; do
		# this narrow, doctype-scoped grant with permission checks off (as this module
		# already does to write the Web Form).
		had_flag = frappe.flags.ignore_permissions
		frappe.flags.ignore_permissions = True
		try:
			perm_name = frappe.db.get_value(
				"Custom DocPerm",
				{"parent": doctype, "role": "Guest", "permlevel": 0, "if_owner": 0},
			)
			if not perm_name:
				add_permission(doctype, "Guest", 0, ptype="select")
				perm_name = frappe.db.get_value(
					"Custom DocPerm",
					{"parent": doctype, "role": "Guest", "permlevel": 0, "if_owner": 0},
				)
			# add_permission defaults the row to read/export; a public Link only lists
			# names, so trim the Guest row to `select` alone.
			perm = frappe.get_doc("Custom DocPerm", perm_name)
			perm.update(
				{
					"select": 1,
					"read": 0,
					"write": 0,
					"create": 0,
					"delete": 0,
					"submit": 0,
					"cancel": 0,
					"amend": 0,
					"report": 0,
					"export": 0,
					"import": 0,
					"print": 0,
					"email": 0,
					"share": 0,
				}
			)
			perm.save(ignore_permissions=True)
		finally:
			frappe.flags.ignore_permissions = had_flag
		frappe.clear_cache(doctype=doctype)

	return {"doctype": doctype, "guest_can_select": guest_can_select(doctype)}


@frappe.whitelist()
def list_forms() -> list[dict]:
	"""CRM forms only (native Web Form records mapped to Lead/Deal)."""
	_check_manager()
	return frappe.get_all(
		"Web Form",
		filters={"module": FORM_MODULE, "doc_type": ["in", ALLOWED_DOCTYPES]},
		fields=[
			"name",
			"title",
			"route",
			"doc_type as document_type",
			"crm_published as published",
			"modified",
		],
		order_by="modified desc",
	)


@frappe.whitelist()
def get_form_config(name: str) -> dict:
	"""Full config for the builder, read from the native Web Form record."""
	_check_manager()
	doc = _get_crm_form(name)
	return {
		"name": doc.name,
		"title": doc.title,
		"route": doc.route,
		"document_type": doc.doc_type,
		"published": doc.crm_published,
		"submit_button_label": doc.button_label or _("Submit"),
		"description": doc.introduction_text or "",
		"success_message": doc.success_message or "",
		"redirect_url": doc.success_url or "",
		"allowed_embedding_domains": doc.allowed_embedding_domains or "",
		"fields": [
			{
				"fieldname": f.fieldname,
				"label": f.label,
				"fieldtype": f.fieldtype,
				"options": f.options,
				"reqd": f.reqd,
				# `.get()`, not attribute access: `placeholder` is a Web Form Field
				# column on frappe v16 and not on v15, and attribute access on a
				# column that is not in the meta raises. The value round-trips
				# either way; on v15 it is simply always empty.
				"placeholder": f.get("placeholder"),
				"field_description": f.description,
			}
			for f in doc.web_form_fields
		],
		"hidden_fields": _load_hidden_fields(doc),
		"auto_response": load_auto_response(doc.name),
	}


def _load_hidden_fields(doc) -> list[dict]:
	try:
		return json.loads(doc.get("crm_hidden_defaults") or "[]")
	except Exception:
		return []


def _assert_hidden_defaults_set(hidden: list[dict]):
	"""Publishing requires every hidden required field to carry a default — a blank
	one would break record creation on submission. Drafts may save without them."""
	missing = [
		h.get("label") or h.get("fieldname") for h in hidden if not str(h.get("default") or "").strip()
	]
	if missing:
		frappe.throw(_("Set a default value before publishing for: {0}").format(", ".join(missing)))


@frappe.whitelist()
def save_form(name: str | None, form: dict | str) -> dict:
	"""Create/update a native Web Form scoped to CRM doctypes."""
	_check_manager()
	if isinstance(form, str):
		form = json.loads(form or "{}")

	if form.get("document_type") not in ALLOWED_DOCTYPES:
		frappe.throw(_("Forms can only map to: {0}").format(", ".join(ALLOWED_DOCTYPES)))

	doc = _get_crm_form(name) if name else frappe.new_doc("Web Form")

	doc.title = form.get("title")
	doc.route = form.get("route")
	doc.doc_type = form.get("document_type")
	doc.introduction_text = form.get("description")
	doc.button_label = form.get("submit_button_label") or "Submit"
	doc.success_message = form.get("success_message")
	# redirect visitors here after a successful submission (native Web Form field);
	# blank falls back to showing the success message
	doc.success_url = form.get("redirect_url") or ""
	doc.allowed_embedding_domains = form.get("allowed_embedding_domains")
	# `crm_published` drives the CRM's own branded page at /crm-form/<route>; mirror
	# it onto the native `published` so the Desk Web Form view shows the right state.
	# (This also makes Frappe serve its generic form at /<route> — an additional,
	# unbranded public URL alongside /crm-form/<route>.)
	doc.crm_published = 1 if form.get("published") else 0
	doc.published = doc.crm_published
	# CRM forms are public, single-purpose lead/deal capture
	doc.login_required = 0
	doc.allow_multiple = 1
	doc.is_standard = 0
	doc.module = FORM_MODULE

	# brand-new form → seed a starting contact-capture layout (only on create)
	fields = form.get("fields")
	if not name and not fields:
		fields = _seed_visible_fields(form["document_type"])

	# only rewrite the layout when fields were actually sent — an update that omits
	# `fields` (e.g. a settings-only save) must not wipe the existing layout
	if fields is not None:
		doc.set("web_form_fields", [])
		for i, f in enumerate(fields):
			doc.append(
				"web_form_fields",
				{
					"fieldname": f.get("fieldname"),
					"label": f.get("label"),
					"fieldtype": f.get("fieldtype"),
					"options": f.get("options"),
					"reqd": 1 if f.get("reqd") else 0,
					"placeholder": f.get("placeholder"),
					"description": f.get("field_description"),
					"idx": i + 1,
				},
			)

	# hidden required fields: mandatory fields kept out of the visible form, with
	# the default value applied on submission (see enrich_form_submission). Seeded
	# once on create (e.g. Status); thereafter whatever the builder sends wins.
	hidden = form.get("hidden_fields")
	if hidden is None and not name:
		hidden = _seed_hidden_fields(form["document_type"])
	if isinstance(hidden, str):
		hidden = json.loads(hidden or "[]")
	hidden = hidden or []
	if doc.crm_published:
		_assert_hidden_defaults_set(hidden)
	doc.crm_hidden_defaults = json.dumps(hidden) if hidden else ""

	doc.save(ignore_permissions=True)

	# The automatic reply lives in its own doctype, named after the form. It is
	# written after the form is saved because a brand-new form has no name until
	# then. An update that omits `auto_response` leaves the stored reply alone,
	# for the same reason an update that omits `fields` leaves the layout alone.
	auto_response = form.get("auto_response")
	if isinstance(auto_response, str):
		auto_response = json.loads(auto_response or "{}")
	if auto_response is not None:
		save_auto_response(doc.name, auto_response)

	return {"name": doc.name, "route": doc.route}


def _get_crm_form(name: str):
	doc = frappe.get_doc("Web Form", name)
	# scope to CRM's own forms — a Web Form from another app that happens to target
	# CRM Lead/Deal must not be readable/mutable/deletable through this API
	if doc.module != FORM_MODULE or doc.doc_type not in ALLOWED_DOCTYPES:
		frappe.throw(_("Not a CRM form"))
	return doc


@frappe.whitelist()
def set_published(name: str, published: int) -> None:
	"""Publish/unpublish from the list, bypassing native Web Form role perms."""
	_check_manager()
	doc = _get_crm_form(name)
	if int(published):
		_assert_hidden_defaults_set(_load_hidden_fields(doc))
	doc.crm_published = 1 if int(published) else 0
	doc.published = doc.crm_published  # mirror onto native flag (see save_form)
	doc.save(ignore_permissions=True)


@frappe.whitelist()
def delete_form(name: str) -> None:
	_check_manager()
	_get_crm_form(name)
	frappe.delete_doc("Web Form", name, ignore_permissions=True)


@frappe.whitelist()
def test_submit_form(name: str, values: dict | str) -> dict:
	"""Dry-run a submission for an author previewing a draft: validate required
	fields the same way the live form does, but create no record."""
	_check_manager()
	if isinstance(values, str):
		values = json.loads(values or "{}")

	doc = _get_crm_form(name)
	for f in doc.web_form_fields:
		if f.fieldtype in ("Section Break", "Column Break"):
			continue
		value = values.get(f.fieldname)
		if f.reqd and (value is None or value == ""):
			frappe.throw(_("{0} is required").format(f.label or f.fieldname))
	return {"test": True}


# Public form serving + submission run through the framework's own Web Form engine:
# the CRM page (`www/crm_form.py`) renders the published form and posts to the
# built-in `accept()`, which triggers `enrich_form_submission` below via the
# `in_web_form` flag. No CRM-owned guest endpoint is needed for that path.


def enrich_form_submission(doc):
	"""Called from the CRM Lead/Deal `before_insert`: when the record is created via a
	web form, apply the same enrichment the CRM applies on manual creation.

	The framework's Web Form `accept()` just inserts the target doc, so without this
	web submissions would miss Source / Organization / primary Contact. `accept()`
	sets `frappe.flags.in_web_form`, which is how we scope this to web submissions.
	"""
	if not frappe.flags.get("in_web_form"):
		return
	if doc.doctype not in ALLOWED_DOCTYPES:
		return

	_apply_hidden_defaults(doc)

	# stamp the source so form records are identifiable/filterable
	if doc.meta.has_field("source") and not doc.get("source"):
		doc.source = ensure_form_source()

	capture(
		"web_form_submitted",
		"crm",
		properties={"doctype": doc.doctype, "form": frappe.form_dict.get("web_form")},
	)

	if doc.doctype != "CRM Deal":
		return

	from crm.fcrm.doctype.crm_deal.crm_deal import create_contact, create_organization

	# auto-create the linked Organization from the org name (as the CRM does for deals)
	if doc.get("organization_name") and not doc.get("organization"):
		created = create_organization(doc)
		if created:
			doc.organization = created

	# auto-create & link a primary Contact from the person's details
	if not doc.get("contacts") and (
		doc.get("first_name") or doc.get("last_name") or doc.get("email") or doc.get("mobile_no")
	):
		contact = create_contact(doc)
		if contact:
			doc.append("contacts", {"contact": contact, "is_primary": 1})


def queue_auto_response(doc):
	"""Called from the CRM Lead/Deal `after_insert`: send the form's automatic reply.

	`after_insert` and not `before_insert`, deliberately. The spec asks for the
	successful-submission point, and before the insert there is no submission --
	a validation failure after a `before_insert` send would leave a stranger
	holding a "thank you for your enquiry" for an enquiry that does not exist.

	The work is enqueued AFTER COMMIT, never done inline. Rendering and queueing
	an email inside the visitor's POST would put an SMTP-shaped delay in front of
	their success page, and an exception in the send would roll back the lead
	itself -- losing the enquiry to save the receipt.
	"""
	if not frappe.flags.get("in_web_form"):
		return
	if doc.doctype not in ALLOWED_DOCTYPES:
		return

	web_form = frappe.form_dict.get("web_form")
	if not web_form:
		return

	# `web_form` comes from the (client-controllable) POST body. Only a CRM form
	# that targets this exact doctype may drive a send, exactly as
	# `_apply_hidden_defaults` only trusts such a form for its defaults.
	form = frappe.db.get_value("Web Form", web_form, ["name", "doc_type", "module"], as_dict=True)
	if not form or form.module != FORM_MODULE or form.doc_type != doc.doctype:
		return

	if not frappe.db.get_value(AUTO_RESPONSE_DOCTYPE, form.name, "enabled"):
		return

	frappe.enqueue(
		AUTO_RESPONSE_JOB,
		queue="short",
		enqueue_after_commit=True,
		web_form=form.name,
		reference_doctype=doc.doctype,
		reference_name=doc.name,
	)


def submission_key(web_form: str, reference_doctype: str, reference_name: str) -> str:
	"""The idempotency key: one reply per record created by one form."""
	return f"{web_form}:{reference_doctype}:{reference_name}"


def claim_submission(web_form: str, reference_doctype: str, reference_name: str, recipient: str):
	"""Take the one send slot for this submission, or return None.

	The row is inserted BEFORE anything is rendered and its `submission_key`
	carries a unique index, so a retried job, a double POST and two workers
	racing the same submission all collide on the index and only the first
	proceeds. Same discipline as `crm.outbound`, one size smaller.
	"""
	row = frappe.new_doc(AUTO_RESPONSE_LOG_DOCTYPE)
	row.update(
		{
			"submission_key": submission_key(web_form, reference_doctype, reference_name),
			"web_form": web_form,
			"reference_doctype": reference_doctype,
			"reference_name": reference_name,
			"recipient": recipient,
			"status": "Claimed",
		}
	)
	try:
		row.insert(ignore_permissions=True)
	except (frappe.UniqueValidationError, frappe.DuplicateEntryError):
		return None
	return row


def close_submission(row, status: str, detail: str = "", communication: str | None = None):
	"""Write the outcome onto the claim row."""
	frappe.db.set_value(
		AUTO_RESPONSE_LOG_DOCTYPE,
		row.name,
		{
			"status": status,
			"detail": frappe.utils.strip_html(frappe.utils.cstr(detail))[:500],
			"communication": communication,
			"sent_at": frappe.utils.now_datetime() if status == "Sent" else None,
		},
		update_modified=False,
	)


def merge_values(doc) -> dict:
	"""The value behind every merge token, for one submitted record.

	Nothing outside `AUTO_RESPONSE_MERGE_FIELDS` is reachable. That is the point:
	a form author cannot accidentally (or deliberately) put the lead owner's
	address, the deal value or an internal note into a message that goes to a
	stranger.
	"""
	from crm.fcrm.doctype.crm_itinerary.crm_itinerary import get_agency_details

	first = frappe.utils.cstr(doc.get("first_name") or "").strip()
	last = frappe.utils.cstr(doc.get("last_name") or "").strip()
	full = frappe.utils.cstr(doc.get("lead_name") or "").strip() or " ".join(x for x in (first, last) if x)

	return {
		"first_name": first,
		"last_name": last,
		"full_name": full,
		"email": frappe.utils.cstr(doc.get("email") or ""),
		"mobile_no": frappe.utils.cstr(doc.get("mobile_no") or doc.get("phone") or ""),
		"organization_name": frappe.utils.cstr(doc.get("organization_name") or ""),
		"record_id": frappe.utils.cstr(doc.name or ""),
		"agency_name": frappe.utils.cstr((get_agency_details() or {}).get("name") or ""),
	}


def render_merge(template, values: dict, escape: bool = True) -> str:
	"""Replace `{{ token }}` from `values`. An unknown token renders as nothing.

	A deliberate substitution rather than Jinja. The template is written by a
	manager, but the values come from whoever filled in a public form, and a real
	template engine invited to evaluate them would be evaluating a stranger's
	text. Substitution cannot execute anything, and the value is HTML-escaped on
	its way into an HTML body.

	An unknown token becomes an empty string rather than staying visible: the
	person receiving this email should never be shown the plumbing.
	"""
	text = frappe.utils.cstr(template or "")

	def replace(match):
		value = frappe.utils.cstr(values.get(match.group(1).lower(), ""))
		return frappe.utils.escape_html(value) if escape else value

	return MERGE_TOKEN_PATTERN.sub(replace, text)


def outgoing_sender() -> dict | None:
	"""The account an automatic reply is sent from, or None when there is none.

	A CRM form answers on behalf of the agency, not of whoever happens to be
	logged in, so the address is the site's default outgoing account. Without one
	the reply is not attempted at all: `make` would raise inside a background job
	and the visitor would never learn anything went wrong.
	"""
	row = frappe.db.get_value(
		"Email Account",
		{"enable_outgoing": 1, "default_outgoing": 1},
		["name", "email_id"],
		as_dict=True,
	)
	if not row:
		row = frappe.db.get_value("Email Account", {"enable_outgoing": 1}, ["name", "email_id"], as_dict=True)
	return row or None


def send_auto_response(web_form: str, reference_doctype: str, reference_name: str) -> str:
	"""Send one form's automatic reply for one submission. Returns the outcome.

	Runs as a background job. Never raises: the visitor is long gone and the
	record already exists, so the only thing an exception could achieve here is a
	noisy failed job and a log row stuck on `Claimed`.

	Every refusal is written to the claim row, so "why did this visitor get no
	reply" is one lookup: Disabled, No Recipient, No Email Account, Suppressed or
	Failed.
	"""
	previous_user = frappe.session.user
	row = None
	try:
		if reference_doctype not in ALLOWED_DOCTYPES:
			return "not_allowed"
		if not frappe.db.exists(reference_doctype, reference_name):
			return "gone"

		settings = frappe.db.get_value(
			AUTO_RESPONSE_DOCTYPE, web_form, ["enabled", "subject", "message"], as_dict=True
		)
		doc = frappe.get_doc(reference_doctype, reference_name)
		recipient = frappe.utils.cstr(doc.get("email") or "").strip()

		row = claim_submission(web_form, reference_doctype, reference_name, recipient)
		if row is None:
			# Somebody already holds the slot for this submission.
			return "duplicate"

		if not settings or not settings.enabled:
			close_submission(row, "Disabled", "the form's automatic reply is switched off")
			return "disabled"

		if not recipient:
			close_submission(row, "No Recipient", "the submission carried no email address")
			return "no_recipient"

		if is_suppressed(CHANNEL_EMAIL, recipient):
			close_submission(row, "Suppressed", "the address is on the suppression ledger")
			return "suppressed"

		account = outgoing_sender()
		if not account:
			close_submission(row, "No Email Account", "no outgoing Email Account is configured")
			return "no_email_account"

		values = merge_values(doc)
		subject = render_merge(settings.subject, values, escape=False).strip() or _(
			"Thanks for getting in touch"
		)
		content = render_merge(settings.message, values)

		# Administrator, not the submitting Guest. `make` checks the `email`
		# permission on the referenced record and Guest holds none; the message is
		# the agency's, not the visitor's, so the agency's account is the honest
		# sender either way.
		frappe.set_user("Administrator")
		result = _make_auto_response(
			doctype=reference_doctype,
			name=reference_name,
			recipient=recipient,
			subject=subject,
			content=content,
			sender=account.email_id,
			sender_full_name=values.get("agency_name") or None,
		)
		close_submission(row, "Sent", "", (result or {}).get("name"))
		return "sent"
	except Exception:
		frappe.log_error(frappe.get_traceback(), "CRM form: automatic reply failed")
		if row is not None:
			try:
				close_submission(
					row, "Failed", frappe.utils.cstr(frappe.get_traceback(with_context=False))[-400:]
				)
			except Exception:
				pass
		return "failed"
	finally:
		if frappe.session.user != previous_user:
			frappe.set_user(previous_user)


def _make_auto_response(doctype, name, recipient, subject, content, sender, sender_full_name):
	"""The one place the automatic reply hands a message to the email queue.

	A named seam: `crm/tests/test_form_auto_response.py` stands in for it so the
	suite exercises the whole decision path -- claim, suppression, account,
	merge -- without a mail server, and so a test can count how many messages one
	submission produced.
	"""
	from frappe.core.doctype.communication.email import make

	return make(
		doctype=doctype,
		name=name,
		recipients=recipient,
		subject=subject,
		content=content,
		sender=sender,
		sender_full_name=sender_full_name,
		send_email=1,
		communication_type="Automated Message",
	)


# --- auto-response configuration (the builder's "Auto-response" tab) --------


@frappe.whitelist()
def get_auto_response_fields() -> list[dict]:
	"""The merge-field vocabulary, for the builder's "Insert field" menu."""
	_check_manager()
	return [{"token": token, "label": _(label)} for token, label in AUTO_RESPONSE_MERGE_FIELDS]


def load_auto_response(web_form: str) -> dict:
	"""The stored auto-response for one form, or the empty default."""
	row = frappe.db.get_value(
		AUTO_RESPONSE_DOCTYPE, web_form, ["enabled", "subject", "message"], as_dict=True
	)
	if not row:
		return {"enabled": 0, "subject": "", "message": ""}
	return {
		"enabled": cint(row.enabled),
		"subject": row.subject or "",
		"message": row.message or "",
	}


def save_auto_response(web_form: str, values: dict) -> None:
	"""Write one form's auto-response. Created on first save, never before."""
	values = values or {}
	enabled = 1 if values.get("enabled") else 0
	subject = frappe.utils.cstr(values.get("subject") or "")[:200]
	message = frappe.utils.cstr(values.get("message") or "")

	if frappe.db.exists(AUTO_RESPONSE_DOCTYPE, web_form):
		frappe.db.set_value(
			AUTO_RESPONSE_DOCTYPE,
			web_form,
			{"enabled": enabled, "subject": subject, "message": message},
		)
		return

	if not (enabled or subject or message):
		# Nothing to store. A form nobody configured should not grow a row.
		return

	doc = frappe.new_doc(AUTO_RESPONSE_DOCTYPE)
	doc.update({"web_form": web_form, "enabled": enabled, "subject": subject, "message": message})
	doc.insert(ignore_permissions=True)


@frappe.whitelist(methods=["POST"])
def send_auto_response_test(name: str) -> dict:
	"""Send the form's automatic reply to the manager who asked, as a test.

	The recipient is ALWAYS the caller's own address and is never taken from the
	request. An endpoint that sent an arbitrary body to an arbitrary address
	would be an open relay wearing a CRM's return address.
	"""
	_check_manager()
	doc = _get_crm_form(name)

	settings = load_auto_response(doc.name)
	account = outgoing_sender()
	if not account:
		frappe.throw(_("No outgoing Email Account is configured, so nothing can be sent."))

	recipient = frappe.db.get_value("User", frappe.session.user, "email") or frappe.session.user
	if is_suppressed(CHANNEL_EMAIL, recipient):
		frappe.throw(_("Your own address is on the suppression list, so the test was not sent."))

	values = sample_merge_values()
	subject = render_merge(settings["subject"], values, escape=False).strip() or _(
		"Thanks for getting in touch"
	)
	content = render_merge(settings["message"], values)

	frappe.sendmail(
		recipients=[recipient],
		sender=account.email_id,
		subject=_("[Test] {0}").format(subject),
		message=content,
		reference_doctype="Web Form",
		reference_name=doc.name,
		expose_recipients="header",
	)
	return {"sent_to": recipient}


def sample_merge_values() -> dict:
	"""Stand-in values for a test send, so every pill shows something."""
	from crm.fcrm.doctype.crm_itinerary.crm_itinerary import get_agency_details

	return {
		"first_name": _("Priya"),
		"last_name": _("Sharma"),
		"full_name": _("Priya Sharma"),
		"email": "priya@example.com",
		"mobile_no": "+91 98765 43210",
		"organization_name": _("Sharma Travels"),
		"record_id": "CRM-LEAD-2026-00001",
		"agency_name": frappe.utils.cstr((get_agency_details() or {}).get("name") or ""),
	}


def _apply_hidden_defaults(doc):
	"""Apply the submitting form's hidden-field defaults (e.g. Status) to fields the
	visitor didn't fill, so mandatory values are present before the record is saved."""
	web_form = frappe.form_dict.get("web_form")
	if not web_form:
		return
	# `web_form` comes from the (client-controllable) POST body — only trust a CRM
	# form that targets this exact doctype, else a submission could pull in another
	# form's defaults (or a different doctype's).
	form = frappe.db.get_value("Web Form", web_form, ["doc_type", "crm_hidden_defaults"], as_dict=True)
	if not form or form.doc_type not in ALLOWED_DOCTYPES or form.doc_type != doc.doctype:
		return
	raw = form.crm_hidden_defaults
	if not raw:
		return
	try:
		hidden = json.loads(raw)
	except Exception:
		return
	for h in hidden:
		fieldname = h.get("fieldname")
		default = h.get("default")
		if (
			fieldname
			and default not in (None, "")
			and doc.meta.has_field(fieldname)
			and not doc.get(fieldname)
		):
			doc.set(fieldname, default)
