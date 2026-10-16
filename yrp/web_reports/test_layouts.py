import frappe

from yrp.api import web_reports as api
from yrp.web_reports.layouts import LAYOUT_DOCTYPE
from yrp.web_reports.test_support import ReportAccessTestCase, report_access_entries


class TestLayoutReportAccess(ReportAccessTestCase):
	def test_denied_layout_endpoints_do_not_create_or_change_layouts(self):
		with report_access_entries() as fixture:
			for kind, entry in fixture.entries.items():
				filters = {"user": fixture.denied_user, "report_key": entry.key}
				frappe.set_user(fixture.denied_user)
				self._assert_denied(entry.key, kind)
				self.assertFalse(frappe.db.exists(LAYOUT_DOCTYPE, filters))

				frappe.set_user("Administrator")
				doc = frappe.get_doc(
					{
						"doctype": LAYOUT_DOCTYPE,
						**filters,
						"columns": [_column(width=140)],
					}
				).insert(ignore_permissions=True)
				original = frappe.get_doc(LAYOUT_DOCTYPE, doc.name).as_dict()
				frappe.set_user(fixture.denied_user)
				self._assert_denied(entry.key, kind, doc.name, original)

	def _assert_denied(self, key, kind, layout_name=None, original=None):
		operations = (
			("get", lambda: api.get_report_layout(key)),
			("save", lambda: api.save_report_layout(key, [_column(width=200)])),
			("save-invalid", lambda: api.save_report_layout(key, "invalid json")),
			("reset", lambda: api.reset_report_layout(key)),
		)
		for endpoint, operation in operations:
			with self.subTest(kind=kind, endpoint=endpoint, existing=bool(layout_name)):
				with self.assertRaises(frappe.PermissionError):
					operation()
				if layout_name:
					self.assertEqual(frappe.get_doc(LAYOUT_DOCTYPE, layout_name).as_dict(), original)
				else:
					self.assertFalse(
						frappe.db.exists(LAYOUT_DOCTYPE, {"user": frappe.session.user, "report_key": key})
					)

	def test_authorized_layout_save_get_update_and_reset(self):
		with report_access_entries() as fixture:
			frappe.set_user(self.permitted_user)
			for kind, entry in fixture.entries.items():
				with self.subTest(kind=kind):
					empty = {
						"layout": None,
						"link_sources": {"item": {"label": "Item", "doctype": "Item"}},
					}
					self.assertEqual(api.get_report_layout(entry.key), empty)
					columns = [
						_column(width=140),
						{
							"column_key": "linked:item:item_name",
							"kind": "linked",
							"source_column": "item",
							"target_field": "item_name",
							"visible": 1,
							"align": "left",
							"width": 180,
						},
					]
					saved = api.save_report_layout(entry.key, columns)
					self.assertEqual(saved, {**empty, "layout": columns})
					self.assertEqual(api.get_report_layout(entry.key), saved)
					updated = api.save_report_layout(entry.key, [_column(width=200)])
					self.assertEqual(updated["layout"], [_column(width=200)])
					self.assertEqual(api.get_report_layout(entry.key), updated)
					self.assertEqual(api.reset_report_layout(entry.key), empty)
					self.assertEqual(api.get_report_layout(entry.key), empty)
					self.assertFalse(
						frappe.db.exists(LAYOUT_DOCTYPE, {"user": self.permitted_user, "report_key": entry.key})
					)


def _column(*, width):
	return {
		"column_key": "base:item",
		"kind": "base",
		"source_column": "",
		"target_field": "",
		"visible": 1,
		"align": "auto",
		"width": width,
	}
