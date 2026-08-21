# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and Contributors
# See license.txt

"""Tests for the quote PDF and its tokenised link (master spec §5, item 25).

The PDF is rendered for real in one test, because "the products come out right"
is the acceptance criterion and a stubbed renderer cannot show it. Every other
test stubs `crm.document_links.render_print_pdf`, which keeps the suite off
wkhtmltopdf for the twenty tests that are about the link, the view log and the
permissions rather than about typography.

Nothing here reaches Meta. `crm.api.whatsapp.create_whatsapp_message` is
stubbed; the assertion is on the URL this module hands it.

Endpoint authorization (master spec §3), asserted below rather than described:

* `crm.api.quote.get_quote_preview` -- `read` on the named CRM Deal, checked
  with `frappe.has_permission(..., doc=name)` so the org-hierarchy
  `has_deal_permission` hook decides. `TestPermissions` proves a Sales User
  without access is refused, and that a missing deal answers exactly like a
  forbidden one.
* `crm.api.quote.download_quote` -- POST only, `read` AND `print`.
* `crm.api.quote.send_quote_on_whatsapp` -- POST only, `write`. The recipient
  number comes from the deal, never from the request:
  `test_the_endpoint_takes_no_number_argument`.
* `crm.api.quote.view` -- **Guest**. The token is the authorization.
  `TestTokenRoute` proves an unknown, a revoked and an expired token are
  indistinguishable, and that a live token streams exactly one file.
"""

from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from crm import document_links
from crm.api import quote

DEAL_DOCTYPE = "CRM Deal"
OUTSIDER = "quote-outsider@example.com"

BROWSER_AGENT = "Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36 Chrome/120 Mobile Safari/537.36"
META_AGENT = "facebookexternalhit/1.1 (+http://www.facebook.com/externalhit_uatext.php)"


def stub_pdf() -> bytes:
	"""A real, minimal PDF.

	Not a placeholder byte string: the File doctype runs every `.pdf` upload
	through `pdf_contains_js`, which parses it. Anything unparseable is rejected
	before the row is written, so a stub has to be a genuine document.
	"""
	from io import BytesIO

	from pypdf import PdfWriter

	writer = PdfWriter()
	writer.add_blank_page(width=595, height=842)
	buffer = BytesIO()
	writer.write(buffer)
	return buffer.getvalue()


def deal_status() -> str:
	status = frappe.db.get_value("CRM Deal Status", {"type": ["!=", "Lost"]}, "name")
	if not status:
		status = (
			frappe.get_doc(
				{"doctype": "CRM Deal Status", "deal_status": "Qualification", "position": 1, "type": "Open"}
			)
			.insert(ignore_permissions=True)
			.name
		)
	return status


def make_user(user: str, role: str):
	if not frappe.db.exists("User", user):
		frappe.get_doc(
			{
				"doctype": "User",
				"email": user,
				"first_name": user.split("@")[0],
				"send_welcome_email": 0,
				"roles": [{"role": role}],
			}
		).insert(ignore_permissions=True)


class QuoteTestCase(FrappeTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		self.deal = self.new_deal()

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.db.rollback()

	def new_contact(self):
		"""A CRM Deal's email and mobile mirror its PRIMARY CONTACT.

		`CRM Deal.set_primary_email_mobile_no` wipes both fields when there is no
		primary contact, so a deal that is meant to have an address needs one.
		"""
		contact = frappe.new_doc("Contact")
		contact.first_name = "Priya"
		contact.last_name = "Sharma"
		contact.append("email_ids", {"email_id": "priya@example.com", "is_primary": 1})
		contact.append("phone_nos", {"phone": "+919876543210", "is_primary_mobile_no": 1})
		return contact.insert(ignore_permissions=True)

	def new_deal(self, with_products=True):
		contact = self.new_contact()
		doc = frappe.new_doc(DEAL_DOCTYPE)
		doc.update(
			{
				"status": deal_status(),
				"organization_name": "Sharma Travels",
				"first_name": "Priya",
				"last_name": "Sharma",
				"currency": "INR",
				"expected_deal_value": 121000,
				"expected_closure_date": frappe.utils.add_days(frappe.utils.nowdate(), 30),
			}
		)
		doc.append("contacts", {"contact": contact.name, "is_primary": 1})
		if with_products:
			doc.append(
				"products",
				{
					"product_name": "Bali 5N/6D package",
					"qty": 2,
					"rate": 65000,
					"amount": 130000,
					"discount_percentage": 10,
					"net_amount": 117000,
				},
			)
			doc.append(
				"products",
				{
					"product_name": "Airport transfers",
					"qty": 1,
					"rate": 4000,
					"amount": 4000,
					"net_amount": 4000,
				},
			)
		return doc.insert(ignore_permissions=True)

	def stub_render(self):
		return patch.object(document_links, "render_print_pdf", return_value=stub_pdf())


# --- the numbers -----------------------------------------------------------


class TestTotals(QuoteTestCase):
	def test_the_rows_carry_the_products(self):
		rows = quote.quote_rows(self.deal)
		self.assertEqual([r["name"] for r in rows], ["Bali 5N/6D package", "Airport transfers"])

	def test_a_percentage_discount_is_shown_as_a_percentage(self):
		self.assertEqual(quote.quote_rows(self.deal)[0]["discount"], "10%")

	def test_a_row_with_no_discount_says_so_without_a_zero(self):
		self.assertEqual(quote.quote_rows(self.deal)[1]["discount"], "—")

	def test_a_whole_quantity_has_no_decimal_tail(self):
		self.assertEqual(quote.quote_rows(self.deal)[0]["qty"], "2")

	def test_the_totals_are_summed_from_the_rows(self):
		"""`CRM Deal.total` is filled in by a CLIENT script, so a deal created by
		the API or an import carries products and a stored total of zero. The
		quote must not print a total its own lines contradict."""
		self.assertIsNone(self.deal.total)
		totals = quote.quote_totals(self.deal)
		self.assertEqual(totals["total"], frappe.utils.fmt_money(134000, currency="INR"))
		self.assertEqual(totals["net_total"], frappe.utils.fmt_money(121000, currency="INR"))
		self.assertEqual(totals["discount"], frappe.utils.fmt_money(13000, currency="INR"))

	def test_a_missing_amount_is_computed_from_rate_and_quantity(self):
		row = frappe._dict({"rate": 100, "qty": 3})
		self.assertEqual(quote.row_amount(row), 300)

	def test_a_missing_net_amount_applies_the_percentage(self):
		row = frappe._dict({"rate": 100, "qty": 2, "discount_percentage": 25})
		self.assertEqual(quote.row_net_amount(row), 150)

	def test_a_missing_net_amount_applies_the_flat_discount(self):
		row = frappe._dict({"rate": 100, "qty": 2, "discount_amount": 50})
		self.assertEqual(quote.row_net_amount(row), 150)

	def test_no_discount_means_no_discount_line(self):
		deal = self.new_deal(with_products=False)
		deal.append("products", {"product_name": "Flat fee", "qty": 1, "rate": 5000, "amount": 5000})
		self.assertEqual(quote.quote_totals(deal)["discount"], "")

	def test_a_deal_with_no_products_falls_back_to_the_stored_figures(self):
		deal = self.new_deal(with_products=False)
		frappe.db.set_value(DEAL_DOCTYPE, deal.name, {"total": 9000, "net_total": 8000})
		reloaded = frappe.get_doc(DEAL_DOCTYPE, deal.name)
		self.assertEqual(
			quote.quote_totals(reloaded)["net_total"], frappe.utils.fmt_money(8000, currency="INR")
		)


class TestTerms(FrappeTestCase):
	def test_the_default_terms_are_used_when_none_are_given(self):
		self.assertEqual(quote.terms_lines(None), list(quote.DEFAULT_TERMS))

	def test_the_terms_are_split_into_lines(self):
		self.assertEqual(quote.terms_lines("One\n\nTwo\n  "), ["One", "Two"])

	def test_empty_terms_print_nothing(self):
		self.assertEqual(quote.terms_lines(""), [])

	def test_the_terms_are_capped(self):
		lines = quote.terms_lines("x" * (quote.MAX_TERMS_LENGTH + 500))
		self.assertLessEqual(len(lines[0]), quote.MAX_TERMS_LENGTH)


# --- the PDF ---------------------------------------------------------------


class TestRender(QuoteTestCase):
	def test_the_pdf_renders_the_products(self):
		"""The acceptance criterion, rendered for real."""
		doc = quote.decorate(frappe.get_doc(DEAL_DOCTYPE, self.deal.name), "Custom term", 1)
		document_links.ensure_print_format(quote.PRINT_FORMAT, quote.install_print_format)
		html = frappe.get_print(
			DEAL_DOCTYPE, self.deal.name, print_format=quote.PRINT_FORMAT, no_letterhead=1, doc=doc
		)

		self.assertIn("Bali 5N/6D package", html)
		self.assertIn("Airport transfers", html)
		self.assertIn("Custom term", html)
		self.assertIn(frappe.utils.fmt_money(121000, currency="INR"), html)

	def test_the_print_format_installs_on_first_use(self):
		frappe.db.delete("Print Format", {"name": quote.PRINT_FORMAT})
		document_links.ensure_print_format(quote.PRINT_FORMAT, quote.install_print_format)
		self.assertTrue(frappe.db.exists("Print Format", quote.PRINT_FORMAT))

	def test_installing_twice_does_not_rewrite_an_unchanged_format(self):
		document_links.ensure_print_format(quote.PRINT_FORMAT, quote.install_print_format)
		before = frappe.db.get_value("Print Format", quote.PRINT_FORMAT, "modified")
		quote.install_print_format()
		self.assertEqual(frappe.db.get_value("Print Format", quote.PRINT_FORMAT, "modified"), before)

	def test_the_customer_never_sees_the_internal_fields(self):
		frappe.db.set_value(DEAL_DOCTYPE, self.deal.name, {"probability": 40, "next_step": "chase the boss"})
		doc = quote.decorate(frappe.get_doc(DEAL_DOCTYPE, self.deal.name), None, 1)
		document_links.ensure_print_format(quote.PRINT_FORMAT, quote.install_print_format)
		html = frappe.get_print(
			DEAL_DOCTYPE, self.deal.name, print_format=quote.PRINT_FORMAT, no_letterhead=1, doc=doc
		)
		self.assertNotIn("chase the boss", html)

	def test_the_terms_are_not_written_onto_the_deal(self):
		"""A quote's terms belong to the quote. Previewing must not edit a record."""
		quote.decorate(frappe.get_doc(DEAL_DOCTYPE, self.deal.name), "Only for this quote", 1)
		self.assertFalse(
			frappe.db.get_value(DEAL_DOCTYPE, self.deal.name, "next_step") == "Only for this quote"
		)


class TestDownload(QuoteTestCase):
	def test_the_pdf_is_attached_privately(self):
		with self.stub_render():
			result = quote.download_quote(self.deal.name)

		file_row = frappe.db.get_value(
			"File",
			{"attached_to_doctype": DEAL_DOCTYPE, "attached_to_name": self.deal.name},
			["is_private"],
			as_dict=True,
		)
		self.assertEqual(file_row.is_private, 1)
		self.assertTrue(result["file_name"].endswith(".pdf"))

	def test_the_file_name_carries_the_organization(self):
		with self.stub_render():
			result = quote.download_quote(self.deal.name)
		self.assertIn("Quote", result["file_name"])


# --- the tokenised link ----------------------------------------------------


class LinkTestCase(QuoteTestCase):
	"""Shared fixture: one live tokenised link over a stubbed PDF."""

	def make_link(self):
		with self.stub_render():
			file_doc, _sequence = quote.build_pdf(frappe.get_doc(DEAL_DOCTYPE, self.deal.name))
		return document_links.create_link(DEAL_DOCTYPE, self.deal.name, file_doc, purpose=quote.PURPOSE)


class TestLink(LinkTestCase):
	def test_a_link_resolves_by_its_token(self):
		link = self.make_link()
		self.assertEqual(document_links.resolve_link(link.token)["name"], link.name)

	def test_the_file_behind_a_link_stays_private(self):
		link = self.make_link()
		self.assertEqual(frappe.db.get_value("File", link.file, "is_private"), 1)

	def test_an_unknown_token_resolves_to_nothing(self):
		self.assertIsNone(document_links.resolve_link("not-a-real-token"))

	def test_an_empty_token_resolves_to_nothing(self):
		self.assertIsNone(document_links.resolve_link(""))
		self.assertIsNone(document_links.resolve_link(None))

	def test_an_absurdly_long_token_is_refused_without_a_query(self):
		self.assertIsNone(document_links.resolve_link("x" * 5000))

	def test_a_revoked_link_resolves_to_nothing(self):
		link = self.make_link()
		document_links.revoke_links(DEAL_DOCTYPE, self.deal.name, quote.PURPOSE)
		self.assertIsNone(document_links.resolve_link(link.token))

	def test_an_expired_link_resolves_to_nothing(self):
		link = self.make_link()
		frappe.db.set_value(
			document_links.LINK_DOCTYPE,
			link.name,
			"expires_at",
			frappe.utils.add_to_date(frappe.utils.now_datetime(), hours=-1),
			update_modified=False,
		)
		self.assertIsNone(document_links.resolve_link(link.token))

	def test_a_new_quote_retires_the_previous_link(self):
		"""A price the agency withdrew must not still be readable at yesterday's URL."""
		first = self.make_link()
		second = self.make_link()
		self.assertIsNone(document_links.resolve_link(first.token))
		self.assertIsNotNone(document_links.resolve_link(second.token))

	def test_every_link_gets_its_own_token(self):
		self.assertNotEqual(self.make_link().token, self.make_link().token)

	def test_the_public_url_names_the_route_and_the_token(self):
		link = self.make_link()
		url = document_links.public_url(link.token)
		self.assertIn("crm.api.quote.view", url)
		self.assertIn(link.token, url)

	def test_expiry_retires_the_link_and_deletes_the_file(self):
		link = self.make_link()
		frappe.db.set_value(
			document_links.LINK_DOCTYPE,
			link.name,
			"expires_at",
			frappe.utils.add_to_date(frappe.utils.now_datetime(), hours=-1),
			update_modified=False,
		)
		file_name = link.file
		quote.cleanup_quote_links()
		self.assertEqual(frappe.db.get_value(document_links.LINK_DOCTYPE, link.name, "active"), 0)
		self.assertFalse(frappe.db.exists("File", file_name))

	def test_the_expiry_sweep_never_raises(self):
		with patch.object(document_links.frappe, "get_all", side_effect=Exception("database gone")):
			self.assertEqual(quote.cleanup_quote_links(), 0)


# --- the view log ----------------------------------------------------------


class TestClassifyFetch(FrappeTestCase):
	def test_the_platforms_first_fetch_is_the_platforms(self):
		self.assertTrue(document_links.classify_fetch(META_AGENT, previous_views=0))

	def test_the_same_agent_later_is_not_written_off_for_ever(self):
		"""A customer previewing the link inside a chat app must still count."""
		self.assertFalse(document_links.classify_fetch(META_AGENT, previous_views=1))

	def test_a_browsers_first_fetch_is_a_customer_open(self):
		self.assertFalse(document_links.classify_fetch(BROWSER_AGENT, previous_views=0))

	def test_no_user_agent_at_all_is_a_customer_open(self):
		self.assertFalse(document_links.classify_fetch("", previous_views=0))

	def test_the_match_is_case_insensitive(self):
		self.assertTrue(document_links.looks_like_platform_agent("FacebookExternalHit/1.1"))


class TestViewLog(LinkTestCase):
	def test_a_customer_open_is_counted(self):
		link = self.make_link()
		counted = document_links.record_view(
			document_links.resolve_link(link.token), user_agent=BROWSER_AGENT, ip_address="1.2.3.4"
		)
		self.assertTrue(counted)
		self.assertEqual(frappe.db.get_value(document_links.LINK_DOCTYPE, link.name, "view_count"), 1)

	def test_a_platform_prefetch_is_logged_but_not_counted(self):
		link = self.make_link()
		counted = document_links.record_view(document_links.resolve_link(link.token), user_agent=META_AGENT)
		self.assertFalse(counted)
		self.assertEqual(frappe.db.get_value(document_links.LINK_DOCTYPE, link.name, "view_count"), 0)
		self.assertTrue(frappe.db.get_value(document_links.LINK_DOCTYPE, link.name, "platform_fetch_at"))
		self.assertEqual(frappe.db.count(document_links.VIEW_DOCTYPE, {"document_link": link.name}), 1)

	def test_the_prefetch_then_the_customer(self):
		"""The ordinary WhatsApp send: Meta fetches, then a person opens it."""
		link = self.make_link()
		document_links.record_view(document_links.resolve_link(link.token), user_agent=META_AGENT)
		document_links.record_view(document_links.resolve_link(link.token), user_agent=BROWSER_AGENT)

		self.assertEqual(frappe.db.get_value(document_links.LINK_DOCTYPE, link.name, "view_count"), 1)
		views = document_links.customer_views(DEAL_DOCTYPE, self.deal.name)
		self.assertEqual(len(views), 1)

	def test_the_first_open_stamps_first_viewed_at(self):
		link = self.make_link()
		document_links.record_view(document_links.resolve_link(link.token), user_agent=BROWSER_AGENT)
		first = frappe.db.get_value(document_links.LINK_DOCTYPE, link.name, "first_viewed_at")
		document_links.record_view(document_links.resolve_link(link.token), user_agent=BROWSER_AGENT)
		self.assertEqual(
			frappe.db.get_value(document_links.LINK_DOCTYPE, link.name, "first_viewed_at"), first
		)
		self.assertEqual(frappe.db.get_value(document_links.LINK_DOCTYPE, link.name, "view_count"), 2)

	def test_a_logging_failure_does_not_break_the_read(self):
		link = self.make_link()
		with patch.object(document_links.frappe, "new_doc", side_effect=Exception("table gone")):
			self.assertFalse(
				document_links.record_view(document_links.resolve_link(link.token), user_agent=BROWSER_AGENT)
			)

	def test_a_long_user_agent_is_truncated(self):
		link = self.make_link()
		document_links.record_view(document_links.resolve_link(link.token), user_agent="x" * 5000)
		stored = frappe.db.get_value(document_links.VIEW_DOCTYPE, {"document_link": link.name}, "user_agent")
		self.assertLessEqual(len(stored), document_links.MAX_USER_AGENT_LENGTH)


class TestTokenRoute(LinkTestCase):
	def test_a_live_token_streams_the_file(self):
		link = self.make_link()
		quote.view(link.token)
		self.assertEqual(frappe.response.type, "pdf")
		self.assertTrue(frappe.response.filecontent)
		frappe.response.pop("filecontent", None)

	def test_the_route_writes_a_view(self):
		link = self.make_link()
		quote.view(link.token)
		frappe.response.pop("filecontent", None)
		self.assertEqual(frappe.db.count(document_links.VIEW_DOCTYPE, {"document_link": link.name}), 1)

	def test_an_unknown_token_is_refused(self):
		self.assertRaises(frappe.PermissionError, quote.view, "nope")

	def test_a_revoked_token_is_refused_the_same_way(self):
		link = self.make_link()
		document_links.revoke_links(DEAL_DOCTYPE, self.deal.name, quote.PURPOSE)
		self.assertRaises(frappe.PermissionError, quote.view, link.token)

	def test_an_expired_token_is_refused_the_same_way(self):
		link = self.make_link()
		frappe.db.set_value(
			document_links.LINK_DOCTYPE,
			link.name,
			"expires_at",
			frappe.utils.add_to_date(frappe.utils.now_datetime(), hours=-1),
			update_modified=False,
		)
		self.assertRaises(frappe.PermissionError, quote.view, link.token)

	def test_no_token_at_all_is_refused(self):
		self.assertRaises(frappe.PermissionError, quote.view)

	def test_a_link_whose_file_is_gone_is_refused(self):
		link = self.make_link()
		# `force` skips the link check: the point of the test is a live token
		# whose file has vanished, which is exactly the dangling state.
		frappe.delete_doc("File", link.file, force=True, ignore_permissions=True, delete_permanently=True)
		self.assertRaises(frappe.PermissionError, quote.view, link.token)

	def test_the_route_is_reachable_without_a_login(self):
		self.assertIn(quote.view, frappe.whitelisted)
		self.assertIn(quote.view, frappe.guest_methods)


# --- whatsapp --------------------------------------------------------------


class TestWhatsAppSend(QuoteTestCase):
	def test_a_deal_with_no_number_is_told_so(self):
		"""A deal's numbers come from its contacts as well as its own columns."""
		naked = self.new_deal(with_products=False)
		naked.set("contacts", [])
		naked.save(ignore_permissions=True)
		with self.stub_render():
			result = quote.send_quote_on_whatsapp(naked.name)
		self.assertFalse(result["success"])
		self.assertEqual(result["reason"], "no_number")

	def test_the_tokenised_url_is_what_the_platform_is_handed(self):
		"""Not a public file. The whole point of item 25."""
		with self.stub_render():
			with patch("crm.api.whatsapp.create_whatsapp_message", return_value="MSG") as send:
				result = quote.send_quote_on_whatsapp(self.deal.name, "Terms")

		self.assertTrue(result["success"])
		attached = send.call_args.kwargs["attach"]
		self.assertIn("crm.api.quote.view?token=", attached)
		self.assertEqual(send.call_args.kwargs["content_type"], "document")

	def test_no_public_file_is_left_behind(self):
		with self.stub_render():
			with patch("crm.api.whatsapp.create_whatsapp_message", return_value="MSG"):
				quote.send_quote_on_whatsapp(self.deal.name)

		public = frappe.get_all(
			"File",
			filters={
				"attached_to_doctype": DEAL_DOCTYPE,
				"attached_to_name": self.deal.name,
				"is_private": 0,
			},
		)
		self.assertEqual(public, [])

	def test_a_send_failure_retires_the_token_it_minted(self):
		with self.stub_render():
			with patch("crm.api.whatsapp.create_whatsapp_message", side_effect=Exception("outside 24h")):
				result = quote.send_quote_on_whatsapp(self.deal.name)

		self.assertFalse(result["success"])
		self.assertEqual(result["reason"], "send_failed")
		self.assertIn("24 hours", result["hint"])
		live = frappe.get_all(
			document_links.LINK_DOCTYPE,
			filters={"reference_name": self.deal.name, "active": 1},
		)
		self.assertEqual(live, [])

	def test_the_endpoint_takes_no_number_argument(self):
		"""The recipient comes from the deal, never from the request."""
		import inspect

		self.assertEqual(list(inspect.signature(quote.send_quote_on_whatsapp).parameters), ["deal", "terms"])


# --- the timeline ----------------------------------------------------------


class TestTimeline(LinkTestCase):
	def test_a_customer_open_reaches_the_deal_timeline(self):
		from crm.api import activities

		link = self.make_link()
		document_links.record_view(document_links.resolve_link(link.token), user_agent=BROWSER_AGENT)

		rows = activities.get_quote_view_activities(DEAL_DOCTYPE, self.deal.name)
		self.assertEqual(len(rows), 1)
		self.assertEqual(rows[0]["activity_type"], "quote_view")

	def test_a_platform_prefetch_never_reaches_the_timeline(self):
		from crm.api import activities

		link = self.make_link()
		document_links.record_view(document_links.resolve_link(link.token), user_agent=META_AGENT)
		self.assertEqual(activities.get_quote_view_activities(DEAL_DOCTYPE, self.deal.name), [])

	def test_the_timeline_helper_never_raises(self):
		from crm.api import activities

		with patch.object(document_links, "customer_views", side_effect=Exception("boom")):
			self.assertEqual(activities.get_quote_view_activities(DEAL_DOCTYPE, self.deal.name), [])


# --- permissions -----------------------------------------------------------


class TestPermissions(QuoteTestCase):
	"""Master spec §3. No patching: the real org-hierarchy rule refuses."""

	def setUp(self):
		super().setUp()
		make_user(OUTSIDER, "Sales User")

	def test_a_sales_user_without_deal_access_cannot_preview(self):
		frappe.set_user(OUTSIDER)
		self.assertRaises(frappe.PermissionError, quote.get_quote_preview, self.deal.name)

	def test_a_sales_user_without_deal_access_cannot_download(self):
		frappe.set_user(OUTSIDER)
		self.assertRaises(frappe.PermissionError, quote.download_quote, self.deal.name)

	def test_a_sales_user_without_deal_access_cannot_send(self):
		frappe.set_user(OUTSIDER)
		self.assertRaises(frappe.PermissionError, quote.send_quote_on_whatsapp, self.deal.name)

	def test_a_missing_deal_is_refused_like_a_forbidden_one(self):
		"""So the endpoint is not an existence oracle."""
		frappe.set_user(OUTSIDER)
		self.assertRaises(frappe.PermissionError, quote.get_quote_preview, "CRM-DEAL-NOPE")

	def test_the_owner_of_the_deal_can_preview(self):
		frappe.db.set_value(DEAL_DOCTYPE, self.deal.name, "deal_owner", OUTSIDER)
		frappe.set_user(OUTSIDER)
		self.assertTrue(quote.get_quote_preview(self.deal.name)["has_products"])

	def test_state_changing_endpoints_are_post_only(self):
		for method in (quote.download_quote, quote.send_quote_on_whatsapp):
			self.assertIn(method, frappe.whitelisted, msg=f"{method.__name__} must be whitelisted")
			self.assertEqual(
				tuple(frappe.allowed_http_methods_for_whitelisted_func[method]),
				("POST",),
				msg=f"{method.__name__} must be POST only",
			)

	def test_the_link_doctypes_are_not_readable_by_a_sales_user(self):
		"""A view log names which customers opened which quotes. Managers only."""
		frappe.set_user(OUTSIDER)
		self.assertFalse(frappe.has_permission(document_links.LINK_DOCTYPE, "read"))
		self.assertFalse(frappe.has_permission(document_links.VIEW_DOCTYPE, "read"))
