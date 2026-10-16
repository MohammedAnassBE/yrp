import frappe


def has_manager_permission(doc, ptype=None, user=None, debug=False):
	"""Restrict internal report records to system managers."""

	return _is_report_manager(user)


def manager_only_query_conditions(user=None, doctype=None):
	"""Hide internal report records from non-managers."""

	return "" if _is_report_manager(user) else "1=0"


def _is_report_manager(user=None) -> bool:
	user = user or frappe.session.user
	return user == "Administrator" or "System Manager" in frappe.get_roles(user)
