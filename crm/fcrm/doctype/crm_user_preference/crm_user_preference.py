"""The app's first per-user preference store, deliberately kept small.

Why it exists
-------------
`crm/reminders.py` and `crm/api/whatsapp_followups.py` both wanted a per-user
switch and both went without one, because there was nowhere to put it: FCRM
Settings is site-wide, and a Check field on User is an edit to an upstream core
doctype (master spec F9 forbids that). Stage 3B recorded the missing digest
opt-out as an owner-accepted gap. This is the smallest thing that closes it and
that the next per-user switch can reuse without a second design.

The shape
---------
One row per (user, preference). `preference_id` is `user::key` with a unique
index, because Frappe has no composite unique constraint in a doctype
definition; `CRM Suppression` solves the same problem the same way.

Field names: the brief called for `key` and `value`. `key` is a reserved word in
MariaDB, so both are prefixed -- `preference_key` and `preference_value` -- and
named as a pair rather than one prefixed and one not.

What it is NOT
--------------
Not a general key-value store. `PREFERENCES` is a closed registry, `validate`
refuses a key that is not in it, and the whitelisted setter refuses one too. A
store that accepts any key is a place for a client to park arbitrary data under
a user's name, and it is also a store nothing can enumerate for a settings
screen.

Absence means default. A user with no row gets `PREFERENCES[key]["default"]`, so
a new preference needs no backfill patch and turning the feature off again just
leaves rows nothing reads.

Authorization
-------------
Row-level scope is enforced by `get_permission_query_conditions` and
`has_permission`, registered in `crm/hooks.py`: a person sees and writes their
own rows; a System Manager sees all of them for support. The two whitelisted
endpoints never take a user from the request -- the row is always
`frappe.session.user`.
"""

import frappe
from frappe import _
from frappe.model.document import Document

DOCTYPE = "CRM User Preference"

# The closed registry. Each entry: what the preference means and what a user who
# has never touched it gets.
PREFERENCES = {
	"daily_digest": {
		"default": True,
		"label": "Daily digest",
		"description": (
			"Send me the daily WhatsApp and deal-health digest. Turning this off stops the "
			"digest for this user only; everyone else keeps getting it."
		),
	}
}


class CRMUserPreference(Document):
	def validate(self):
		if self.preference_key not in PREFERENCES:
			frappe.throw(_("{0} is not a known preference.").format(self.preference_key))

		if not self.user:
			frappe.throw(_("A preference needs a user."))

		self.preference_id = preference_id(self.user, self.preference_key)


def preference_id(user: str, key: str) -> str:
	return f"{user}::{key}"


# --- reading ---------------------------------------------------------------


def get_preference(user: str, key: str):
	"""The stored value, or None when the user has never set one.

	Never raises. Every caller is a scheduler job or a send path, and a settings
	read must not take a queue down with it.
	"""
	try:
		if key not in PREFERENCES:
			frappe.log_error(f"Unknown preference {key!r}.", "CRM preferences: unknown key")
			return None

		if not frappe.db.exists("DocType", DOCTYPE):
			return None

		return frappe.db.get_value(DOCTYPE, {"preference_id": preference_id(user, key)}, "preference_value")
	except Exception:
		frappe.log_error(frappe.get_traceback(), f"CRM preferences: could not read {key}")
		return None


def is_on(user: str, key: str) -> bool:
	"""True when this user has the preference on, defaulting per the registry.

	The default is applied here rather than at each call site so a preference
	cannot mean ON in one place and OFF in another.
	"""
	stored = get_preference(user, key)
	if stored is None:
		return bool(PREFERENCES.get(key, {}).get("default", False))
	return bool(frappe.utils.cint(stored))


def get_all_for(user: str) -> dict:
	"""Every registered preference for one user, defaults filled in."""
	return {key: is_on(user, key) for key in PREFERENCES}


# --- writing ---------------------------------------------------------------


def set_preference(user: str, key: str, value) -> None:
	"""Store one preference for one user, creating the row if it is the first.

	`db_set` on the found row rather than a save, so a preference write is one
	statement and cannot trip a validation on an unrelated field later added to
	this doctype.
	"""
	if key not in PREFERENCES:
		frappe.throw(_("{0} is not a known preference.").format(key))

	stored = "1" if frappe.utils.cint(value) else "0"
	name = frappe.db.get_value(DOCTYPE, {"preference_id": preference_id(user, key)}, "name")

	if name:
		frappe.db.set_value(DOCTYPE, name, "preference_value", stored)
		return

	doc = frappe.new_doc(DOCTYPE)
	doc.user = user
	doc.preference_key = key
	doc.preference_value = stored
	doc.insert(ignore_permissions=True)


# --- endpoints -------------------------------------------------------------


@frappe.whitelist(methods=["GET"])
def get_my_preferences() -> dict:
	"""This user's preferences, with the registry's defaults filled in.

	Authorization: any authenticated user, and the answer is always about
	`frappe.session.user`. There is no user parameter to supply, so there is no
	row-level scope a caller could widen.
	"""
	return {
		"values": get_all_for(frappe.session.user),
		"registry": {
			key: {"label": _(entry["label"]), "description": _(entry["description"])}
			for key, entry in PREFERENCES.items()
		},
	}


@frappe.whitelist(methods=["POST"])
def set_my_preference(key: str, value) -> dict:
	"""Set one of this user's own preferences. Refuses an unregistered key.

	Authorization: as above -- the row written is always this session's user's.
	"""
	set_preference(frappe.session.user, key, value)
	return {"key": key, "value": is_on(frappe.session.user, key)}


# --- row-level permissions -------------------------------------------------


def get_permission_query_conditions(user=None):
	"""A person lists their own preferences and nobody else's."""
	user = user or frappe.session.user
	if is_support_user(user):
		return ""
	return f"(`tabCRM User Preference`.`user` = {frappe.db.escape(user)})"


def has_permission(doc, ptype, user=None):
	user = user or frappe.session.user
	return is_support_user(user) or doc.user == user


def is_support_user(user: str) -> bool:
	return user == "Administrator" or "System Manager" in frappe.get_roles(user)
