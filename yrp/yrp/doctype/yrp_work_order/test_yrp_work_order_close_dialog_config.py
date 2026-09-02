import json

import frappe
from frappe.tests import UnitTestCase


class TestWorkOrderCloseDialogConfig(UnitTestCase):
	def test_base_close_reason_is_free_text_without_company_vocabulary(self):
		work_order_schema_path = frappe.get_app_path(
			"yrp", "yrp", "doctype", "yrp_work_order", "yrp_work_order.json"
		)
		with open(work_order_schema_path, encoding="utf-8") as schema_file:
			work_order_schema = json.load(schema_file)

		close_reason = next(
			row for row in work_order_schema["fields"] if row.get("fieldname") == "close_reason"
		)
		self.assertEqual(close_reason["fieldtype"], "Data")
		self.assertFalse(close_reason.get("options"))
		self.assertNotIn(
			"close_other_reason",
			{row.get("fieldname") for row in work_order_schema["fields"]},
		)
