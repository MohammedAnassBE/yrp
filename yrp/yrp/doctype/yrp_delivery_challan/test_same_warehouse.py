import frappe
from frappe.tests import UnitTestCase

from yrp.yrp.doctype.yrp_delivery_challan.yrp_delivery_challan import YRPDeliveryChallan
from yrp.yrp.doctype.yrp_goods_received_note.yrp_goods_received_note import YRPGoodsReceivedNote


class TestSameWarehouseMovement(UnitTestCase):
	def test_delivery_challan_allows_same_source_and_target_warehouse(self):
		delivery_challan = YRPDeliveryChallan(
			{
				"doctype": "YRP Delivery Challan",
				"docstatus": 0,
				"from_warehouse": "DYEING-WH",
				"to_warehouse": "DYEING-WH",
				"items": [{"item_variant": "ITEM-1", "qty": 1}],
			}
		)

		delivery_challan.validate_items()

	def test_goods_received_note_allows_same_source_and_target_warehouse(self):
		goods_received_note = YRPGoodsReceivedNote(
			{
				"doctype": "YRP Goods Received Note",
				"docstatus": 0,
				"against": "YRP Work Order",
				"from_warehouse": "DYEING-WH",
				"to_warehouse": "DYEING-WH",
				"items": [{"item_variant": "ITEM-1", "quantity": 1}],
			}
		)

		goods_received_note.validate_items()
