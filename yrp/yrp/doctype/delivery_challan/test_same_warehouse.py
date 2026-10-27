import frappe
from frappe.tests import UnitTestCase

from yrp.yrp.doctype.delivery_challan.delivery_challan import DeliveryChallan
from yrp.yrp.doctype.goods_received_note.goods_received_note import GoodsReceivedNote


def _stock_row(doctype, qty_field):
	return {
		"doctype": doctype,
		"item_variant": "TEST-ITEM-VARIANT",
		qty_field: 1,
		"qty": 1,
		"uom": "Kg",
	}


class TestSameWarehouseWorkOrderMovement(UnitTestCase):
	def test_delivery_challan_allows_same_source_and_target_warehouse(self):
		doc = frappe.get_doc({
			"doctype": "Delivery Challan",
			"docstatus": 1,
			"from_warehouse": "TEST-WAREHOUSE",
			"to_warehouse": "TEST-WAREHOUSE",
			"items": [_stock_row("Delivery Challan Item", "delivered_quantity")],
		})

		self.assertIsNone(DeliveryChallan.validate_items(doc))

	def test_work_order_grn_allows_same_source_and_target_warehouse(self):
		doc = frappe.get_doc({
			"doctype": "Goods Received Note",
			"against": "Work Order",
			"from_warehouse": "TEST-WAREHOUSE",
			"to_warehouse": "TEST-WAREHOUSE",
			"items": [_stock_row("Goods Received Note Item", "quantity")],
		})

		self.assertIsNone(GoodsReceivedNote.validate_items(doc))

	def test_purchase_order_grn_still_rejects_same_warehouse(self):
		doc = frappe.get_doc({
			"doctype": "Goods Received Note",
			"against": "Purchase Order",
			"from_warehouse": "TEST-WAREHOUSE",
			"to_warehouse": "TEST-WAREHOUSE",
			"items": [_stock_row("Goods Received Note Item", "quantity")],
		})

		with self.assertRaisesRegex(
			frappe.ValidationError,
			"From Warehouse and To Warehouse must be different",
		):
			GoodsReceivedNote.validate_items(doc)
