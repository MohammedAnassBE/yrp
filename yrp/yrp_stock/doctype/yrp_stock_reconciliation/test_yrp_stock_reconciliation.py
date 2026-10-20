from types import SimpleNamespace
from unittest import TestCase

import frappe
from frappe.tests.utils import FrappeTestCase


class TestStockReconciliationPostingTimeControl(FrappeTestCase):
	def test_posting_fields_require_edit_checkbox(self):
		meta = frappe.get_meta("YRP Stock Reconciliation", cached=False)
		edit_control = meta.get_field("edit_posting_date_and_time")
		posting_date = meta.get_field("posting_date")
		posting_time = meta.get_field("posting_time")

		self.assertEqual(edit_control.fieldtype, "Check")
		self.assertLess(edit_control.idx, posting_date.idx)
		self.assertEqual(posting_date.read_only_depends_on, "eval: !doc.edit_posting_date_and_time")
		self.assertEqual(posting_time.read_only_depends_on, "eval: !doc.edit_posting_date_and_time")
		self.assertTrue(posting_date.no_copy)
		self.assertTrue(posting_time.no_copy)


class TestStockReconciliationDuplicateRows(TestCase):
	def rows(self, *lots):
		return [
			frappe._dict(idx=index, item="CLOTH-RED", warehouse="WH-1", lot=lot, received_type="Accepted")
			for index, lot in enumerate(lots, start=1)
		]

	def validate(self, rows):
		from unittest.mock import patch

		from yrp.yrp_stock.doctype.yrp_stock_reconciliation.yrp_stock_reconciliation import (
			YRPStockReconciliation,
		)

		doc = SimpleNamespace(items=rows)
		with patch(
			"yrp.stock.dimensions.get_dimension_fieldnames", return_value=["lot", "received_type"]
		):
			YRPStockReconciliation.validate_unique_rows(doc)

	def test_same_item_warehouse_and_dimensions_twice_is_rejected(self):
		with self.assertRaisesRegex(frappe.ValidationError, "Row 2: CLOTH-RED is already counted in row 1"):
			self.validate(self.rows("LOT-1", "LOT-1"))

	def test_same_item_in_different_lots_is_allowed(self):
		self.validate(self.rows("LOT-1", "LOT-2"))

	def test_blank_dimension_spellings_count_as_the_same_row(self):
		rows = self.rows("LOT-1", "LOT-1")
		rows[0].received_type = None
		rows[1].received_type = ""
		with self.assertRaisesRegex(frappe.ValidationError, "already counted"):
			self.validate(rows)
