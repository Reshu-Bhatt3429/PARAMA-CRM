# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class CRMDocumentLink(Document):
	"""One tokenised, expiring URL for a document a customer is allowed to read.

	The token is the whole authorization. That is deliberate and it is why the
	row carries `expires_at` and `active`: a URL that never dies is a permanent
	unauthenticated read of a customer's quote, and a forwarded WhatsApp message
	is the ordinary way such a URL escapes.

	The file this points at stays PRIVATE. Before this doctype existed the
	itinerary send made the PDF public for two hours so the messaging platform
	could fetch it; a public file is readable by anyone who learns the URL, for
	as long as it exists. The route reads the private file and streams it, so
	losing the token costs one document until it expires, not the /files/
	directory.

	Write through `crm.document_links`, never directly.
	"""

	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		active: DF.Check
		created_by_user: DF.Link | None
		expires_at: DF.Datetime | None
		file: DF.Link | None
		file_name: DF.Data | None
		first_viewed_at: DF.Datetime | None
		last_viewed_at: DF.Datetime | None
		payload: DF.LongText | None
		platform_fetch_at: DF.Datetime | None
		purpose: DF.Literal["Quote", "Itinerary", "Other"]
		reference_doctype: DF.Link
		reference_name: DF.DynamicLink
		token: DF.Data
		view_count: DF.Int
	# end: auto-generated types

	pass
