from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from yrp.yrp.doctype.yrp_goods_received_note.yrp_goods_received_note import (
	GoodsReceivedNote,
)

MODULE = "yrp.yrp.doctype.yrp_goods_received_note.yrp_goods_received_note"


def _grn(**values):
	doc = frappe.new_doc("YRP Goods Received Note")
	doc.update({"against": "YRP Work Order", "from_warehouse": "FROM", "to_warehouse": "TO", **values})
	return doc


class TestDraftWithoutQuantity(FrappeTestCase):
	def test_draft_without_rows_passes_item_validation(self):
		doc = _grn()
		GoodsReceivedNote.validate_items(doc)
		GoodsReceivedNote.calculate_totals(doc)
		self.assertEqual(doc.total_received_quantity, 0)
		self.assertEqual(doc.total, 0)

	def test_submit_without_rows_requires_a_quantity(self):
		doc = _grn()
		doc.docstatus = 1
		with self.assertRaisesRegex(frappe.ValidationError, "at least one row before submitting"):
			GoodsReceivedNote.validate_items(doc)

	def test_submit_with_quantity_passes_item_validation(self):
		doc = _grn()
		doc.docstatus = 1
		doc.append("items", {"item_variant": "ITEM", "quantity": 2})
		GoodsReceivedNote.validate_items(doc)

	def test_empty_work_order_draft_shows_pending_rows_at_zero(self):
		doc = _grn(against_id="WO-1", name="GRN-1")
		pending = [{"item_variant": "ITEM", "quantity": 0, "uom": "Nos"}]
		with (
			patch.object(frappe, "get_doc", return_value=frappe._dict()),
			patch(f"{MODULE}._pending_receivable_rows", return_value=pending) as pending_rows,
			patch(f"{MODULE}._get_production_group_dimensions", return_value={}),
			patch(f"{MODULE}._apply_dimension_values_to_rows"),
			patch("yrp.stock.dimensions.apply_dimension_defaults"),
			patch("yrp.stock.save_stock_items.group_items_for_ui", side_effect=lambda rows, _doctype: rows),
		):
			rows = GoodsReceivedNote.get_item_details_for_ui(doc)
		self.assertEqual(rows, pending)
		self.assertEqual(pending_rows.call_args.kwargs["existing_rows"], [])
