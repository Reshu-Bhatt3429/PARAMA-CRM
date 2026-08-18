# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class CRMDocumentLinkView(Document):
	"""One fetch of one tokenised document.

	Every fetch is written, including the messaging platform's own prefetch. The
	prefetch is flagged rather than dropped: an agent who is told "the customer
	opened your quote" ten seconds after sending it, because a bot fetched the
	media, stops believing the signal. Only rows with `is_platform_fetch` clear
	reach the timeline and the view count.

	No session user is recorded. The route is reached without a login by
	definition, so there is nobody to record.
	"""

	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		document_link: DF.Link
		ip_address: DF.Data | None
		is_platform_fetch: DF.Check
		reference_doctype: DF.Link | None
		reference_name: DF.DynamicLink | None
		user_agent: DF.SmallText | None
		viewed_at: DF.Datetime | None
	# end: auto-generated types

	pass
