from unittest.mock import patch

import frappe
from frappe.tests import UnitTestCase

from yrp.yrp.doctype.yrp_goods_received_note.yrp_goods_received_note import (
	_pending_receivable_rows,
	_pending_rework_receivable_rows,
	_purchase_receipt_splits,
)

MODULE = "yrp.yrp.doctype.yrp_goods_received_note.yrp_goods_received_note"


def _receivable():
	return frappe._dict(
		name="WOR-1",
		item_variant="SHIRT-RED-S",
		qty=10,
		pending_quantity=10,
		uom="Nos",
		row_index="0",
		idx=1,
		table_index=0,
		set_combination=None,
		cost=0,
	)


def _saved_rows(**extra):
	return [
		frappe._dict(
			item_variant="SHIRT-RED-S",
			quantity=qty,
			received_type=received_type,
			ref_doctype="YRP Work Order Receivables",
			ref_docname="WOR-1",
			comments=comments,
			**extra,
		)
		for received_type, qty, comments in (("Accepted", 8, "Pressed"), ("Adas", 2, "Wash again"))
	]


class TestDraftRowComments(UnitTestCase):
	def test_reloaded_work_order_draft_keeps_each_received_type_comment(self):
		wo = frappe._dict(name="WO-1", receivables=[_receivable()], is_rework=0)
		with (
			patch(f"{MODULE}._get_received_type_options", return_value=(["Accepted", "Adas"], "Accepted")),
			patch(f"{MODULE}._wo_excess_percentage", return_value=0),
		):
			rows = _pending_receivable_rows(wo, existing_rows=_saved_rows())

		self.assertEqual(
			{row["received_type"]: row["comments"] for row in rows},
			{"Accepted": "Pressed", "Adas": "Wash again"},
		)

	def test_reloaded_rework_draft_keeps_each_received_type_comment(self):
		wo = frappe._dict(name="WO-1", receivables=[_receivable()], is_rework=1)
		dc_item = frappe._dict(
			name="DCI-1",
			item_variant="SHIRT-RED-S",
			qty=10,
			delivered_quantity=10,
			received_quantity=0,
			ref_doctype="YRP Work Order Receivables",
			ref_docname="WOR-1",
			row_index="0",
			idx=1,
			table_index=0,
			uom="Nos",
			stock_uom="Nos",
			conversion_factor=1,
			set_combination=None,
			rate=0,
		)
		delivery_challan = frappe._dict(items=[dc_item])
		with (
			patch(
				f"{MODULE}._get_rework_output_received_type_options",
				return_value=(["Accepted", "Adas"], "Accepted"),
			),
			patch(f"{MODULE}._delivery_challan_item_dimension_values", return_value={}),
		):
			rows = _pending_rework_receivable_rows(
				wo, delivery_challan, existing_rows=_saved_rows(delivery_challan_item="DCI-1")
			)

		self.assertEqual(
			{row["received_type"]: row["comments"] for row in rows},
			{"Accepted": "Pressed", "Adas": "Wash again"},
		)

	def test_reloaded_purchase_order_draft_keeps_each_split_comment(self):
		rows = _purchase_receipt_splits({"item_variant": "TAPE", "conversion_factor": 1}, _saved_rows())
		self.assertEqual([row["comments"] for row in rows], ["Pressed", "Wash again"])
