import frappe


def allow_report_access() -> None:
	return None


def fail_report(filters, progress=None):
	frappe.throw("Intentional web report test failure")


def report_with_unstamped_link(filters, progress=None):
	return {
		"columns": [{"fieldname": "user", "fieldtype": "Link", "options": "User"}],
		"result": [{"user": frappe.session.user}],
	}
