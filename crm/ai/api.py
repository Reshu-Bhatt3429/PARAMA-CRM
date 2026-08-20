"""The one thing a browser may ask about the AI provider: is it usable?

Every AI surface in the CRM has to answer the same question before it draws
anything -- the Brief button in the timeline, the sparkle in the email composer,
the itinerary editor. UX §2.2 says a disabled feature explains itself instead of
failing, so the button must know the answer BEFORE it is clicked, not after a
round trip that throws.

This endpoint returns a boolean and nothing else. It does not say which provider
is configured, which model, or how much budget is left: a Sales User has no use
for any of that, and a probe that returns it is a probe worth writing.
"""

import frappe

from crm.ai.client import is_configured


@frappe.whitelist(methods=["GET"])
def is_available() -> bool:
	"""True when an AI feature can run right now.

	Authorization: any authenticated CRM user. `frappe.whitelist()` already
	refuses Guest. No record is read and no record data is returned, so there is
	no row-level scope to derive.
	"""
	return is_configured()
