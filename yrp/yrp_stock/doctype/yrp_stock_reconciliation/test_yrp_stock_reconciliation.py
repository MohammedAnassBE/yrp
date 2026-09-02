import frappe
from frappe.tests.utils import FrappeTestCase


class TestStockReconciliationPostingTimeControl(FrappeTestCase):
	def test_posting_fields_require_edit_checkbox(self):
		meta = frappe.get_meta('YRP Stock Reconciliation', cached=False)
		edit_control = meta.get_field("edit_posting_date_and_time")
		posting_date = meta.get_field("posting_date")
		posting_time = meta.get_field("posting_time")

		self.assertEqual(edit_control.fieldtype, "Check")
		self.assertLess(edit_control.idx, posting_date.idx)
		self.assertEqual(posting_date.read_only_depends_on, "eval: !doc.edit_posting_date_and_time")
		self.assertEqual(posting_time.read_only_depends_on, "eval: !doc.edit_posting_date_and_time")
		self.assertTrue(posting_date.no_copy)
		self.assertTrue(posting_time.no_copy)
