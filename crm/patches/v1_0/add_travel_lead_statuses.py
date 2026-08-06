"""Seed the travel sales ladder on sites installed before it existed.

`add_default_lead_statuses` is skip-if-exists, so re-running it only fills in
the statuses a site is missing and never touches the ones it already uses.
"""

from crm.install import add_default_lead_statuses


def execute():
	add_default_lead_statuses()
