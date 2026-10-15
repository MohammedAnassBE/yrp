from contextlib import contextmanager
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from yrp.web_reports.registry import WebReport


class ReportAccessTestCase(IntegrationTestCase):
	def setUp(self):
		super().setUp()
		self.original_user = frappe.session.user
		frappe.set_user("Administrator")
		user = frappe.get_doc(
			{
				"doctype": "User",
				"email": f"report-permitted-{frappe.generate_hash(length=8)}@example.test",
				"first_name": "Permitted Report Test",
				"send_welcome_email": 0,
			}
		).insert(ignore_permissions=True)
		user.add_roles("Stock User", "System Manager")
		self.permitted_user = user.name

	def tearDown(self):
		try:
			frappe.set_user("Administrator")
			for name in frappe.get_all(
				"YRP User Report Layout", filters={"user": self.permitted_user}, pluck="name"
			):
				frappe.delete_doc("YRP User Report Layout", name, ignore_permissions=True, force=True)
			frappe.delete_doc("User", self.permitted_user, ignore_permissions=True, force=True)
		finally:
			frappe.set_user(self.original_user)
			super().tearDown()


def allow_report_access() -> None:
	return None


def require_manager_report_access() -> None:
	frappe.only_for("System Manager")


@contextmanager
def report_access_entries():
	original_user = frappe.session.user
	frappe.set_user("Administrator")
	suffix = frappe.generate_hash(length=8)
	user = frappe.get_doc(
		{
			"doctype": "User",
			"email": f"report-access-{suffix}@example.test",
			"first_name": "Report Access Test",
			"send_welcome_email": 0,
		}
	).insert(ignore_permissions=True)
	user.add_roles("Stock User")
	item = frappe.get_doc(
		{
			"doctype": "Item",
			"name": f"Report Access Item {suffix}",
			"item_code": f"Report Access Item {suffix}",
			"item_name": "Report Access Item",
		}
	)
	item.db_insert()
	report = frappe.get_doc(
		{
			"doctype": "Report",
			"name": f"Report Access {suffix}",
			"report_name": f"Report Access {suffix}",
			"ref_doctype": "Item",
			"report_type": "Query Report",
			"is_standard": "No",
			"query": "select name from tabItem",
			"disabled": 0,
		}
	)
	report.db_insert()
	frappe.get_doc(
		{
			"doctype": "Has Role",
			"parent": report.name,
			"parenttype": "Report",
			"parentfield": "roles",
			"role": "System Manager",
		}
	).db_insert()
	entries = {
		kind: WebReport(
			key=f"access-{kind}-{suffix}",
			kind=kind,
			filters={},
			link_sources={"item": "Item"},
			base_columns={"item": "Item"},
			report=report.name if kind == "frappe_report" else None,
			runner="yrp.web_reports.test_support.fail_report" if kind == "callable" else None,
			access="yrp.web_reports.test_support.require_manager_report_access" if kind == "callable" else None,
		)
		for kind in ("callable", "frappe_report")
	}
	try:
		with patch("yrp.web_reports.registry.get_web_reports", return_value={e.key: e for e in entries.values()}):
			yield frappe._dict(entries=entries, denied_user=user.name, item=item)
	finally:
		frappe.set_user("Administrator")
		for entry in entries.values():
			for name in frappe.get_all("YRP User Report Layout", filters={"report_key": entry.key}, pluck="name"):
				frappe.delete_doc("YRP User Report Layout", name, ignore_permissions=True, force=True)
		for doc in (report, item, user):
			frappe.delete_doc(doc.doctype, doc.name, ignore_permissions=True, force=True)
		frappe.set_user(original_user)


def fail_report(filters, progress=None):
	frappe.throw("Intentional web report test failure")


def report_with_unstamped_link(filters, progress=None):
	return {
		"columns": [{"fieldname": "user", "fieldtype": "Link", "options": "User"}],
		"result": [{"user": frappe.session.user}],
	}
