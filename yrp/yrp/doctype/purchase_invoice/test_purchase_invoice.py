import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import flt, nowdate

from yrp.yrp.doctype.goods_received_note.test_purchase_order_grn import (
	_address,
	_default_received_type,
	_item_uom,
	_process,
	_process_cost,
	_production_group_dimensions,
	_purchase_order,
	_purchase_order_grn,
	_supplier,
	_supplier_warehouse,
	_test_item_variant,
	_warehouse,
)


def _work_order_for_invoice(qty=5):
	item_variant = _test_item_variant()
	parent_item = frappe.db.get_value("Item Variant", item_variant, "item")
	uom = frappe.db.get_value("Item", parent_item, "default_unit_of_measure") or "Piece"
	supplier = _supplier(f"_Test PI WO Supplier {frappe.generate_hash(length=6)}")
	delivery_location = _supplier(f"_Test PI WO Delivery {frappe.generate_hash(length=6)}")
	process_name = _process("_Test PI WO Process")
	dimensions = _production_group_dimensions()
	_supplier_warehouse(supplier, f"_Test PI WO Supplier WH {frappe.generate_hash(length=6)}")
	_supplier_warehouse(delivery_location, f"_Test PI WO Delivery WH {frappe.generate_hash(length=6)}")
	_process_cost(process_name, parent_item, supplier, dimensions)
	wo = frappe.get_doc({
		"doctype": "Work Order",
		"supplier": supplier,
		"delivery_location": delivery_location,
		"planned_end_date": nowdate(),
		"supplier_address": _address(f"_Test PI WO Supplier Address {frappe.generate_hash(length=6)}"),
		"delivery_address": _address(f"_Test PI WO Delivery Address {frappe.generate_hash(length=6)}"),
		"process_name": process_name,
		"item": parent_item,
		**dimensions,
		"deliverables": [{
			"item_variant": item_variant,
			"qty": qty,
			"uom": uom,
			"table_index": 0,
			"row_index": 0,
		}],
		"receivables": [{
			"item_variant": item_variant,
			"qty": qty,
			"uom": uom,
			"cost": 12,
			"table_index": 0,
			"row_index": 0,
		}],
		"work_order_calculated_items": [{
			"item_variant": item_variant,
			"quantity": qty,
			"received_qty": 0,
			"billed_qty": 0,
			"set_combination": {},
		}],
	})
	wo.insert(ignore_permissions=True)
	wo.submit()
	return wo


def _work_order_grn(wo, qty=5):
	received_type = _default_received_type()
	item = wo.receivables[0]
	grn = frappe.get_doc({
		"doctype": "Goods Received Note",
		"against": "Work Order",
		"against_id": wo.name,
		"to_warehouse": frappe.db.get_value("Warehouse", {"supplier": wo.delivery_location}, "name"),
		"items": [{
			"item_variant": item.item_variant,
			"quantity": qty,
			"uom": item.uom,
			"rate": item.cost,
			"ref_doctype": "Work Order Receivables",
			"ref_docname": item.name,
			"received_type": received_type,
		}],
	})
	grn.insert(ignore_permissions=True)
	grn.submit()
	return grn


def _purchase_invoice(against, supplier, grn, approved=False):
	data = frappe.get_attr("yrp.yrp.doctype.purchase_invoice.purchase_invoice.fetch_grn_details")(
		[grn.name], against, supplier
	)
	doc = frappe.get_doc({
		"doctype": "Purchase Invoice",
		"supplier": supplier,
		"billing_supplier": supplier,
		"against": against,
		"bill_no": f"BILL-{frappe.generate_hash(length=6)}",
		"bill_date": nowdate(),
		"grn": [{"grn": grn.name}],
		"items": data["items"],
		"pi_work_order_billed_details": data["wo_items"],
		"total_quantity": data["total_quantity"],
	})
	doc.insert(ignore_permissions=True)
	if approved:
		frappe.get_attr("yrp.yrp.doctype.purchase_invoice.purchase_invoice.approve_invoice")(
			doc.name, "Test approval"
		)
		doc.reload()
	return doc


class TestPurchaseInvoice(FrappeTestCase):
	def test_purchase_order_invoice_tracks_grn_and_blocks_grn_cancel(self):
		po = _purchase_order(qty=4, warehouse=_warehouse(f"_Test_PI_PO_WH_{frappe.generate_hash(length=6)}"))
		grn = _purchase_order_grn(po, qty=4)
		grn.submit()

		invoice = _purchase_invoice("Purchase Order", po.supplier, grn)
		invoice.submit()

		grn.reload()
		self.assertEqual(grn.purchase_invoice_name, invoice.name)
		with self.assertRaises(frappe.ValidationError):
			grn.cancel()

		invoice.cancel()
		grn.reload()
		self.assertFalse(grn.purchase_invoice_name)

	def test_work_order_invoice_updates_billed_qty(self):
		old_override = frappe.db.get_single_value("YRP Settings", "override_pi_approve")
		frappe.db.set_single_value("YRP Settings", "override_pi_approve", 1)
		wo = _work_order_for_invoice(qty=3)
		grn = _work_order_grn(wo, qty=3)
		try:
			invoice = _purchase_invoice("Work Order", wo.supplier, grn, approved=True)
			invoice.submit()

			wo.reload()
			self.assertAlmostEqual(wo.work_order_calculated_items[0].billed_qty, 3)

			invoice.cancel()
			wo.reload()
			self.assertAlmostEqual(wo.work_order_calculated_items[0].billed_qty, 0)
		finally:
			frappe.db.set_single_value("YRP Settings", "override_pi_approve", old_override)

	def test_purchase_order_invoice_uses_form_uom_rate_after_freight(self):
		old_method = frappe.db.get_single_value("YRP Stock Settings", "freight_allocation_method")
		frappe.db.set_single_value("YRP Stock Settings", "freight_allocation_method", "By Quantity")
		try:
			item_variant = _test_item_variant()
			uom = _item_uom(item_variant)
			warehouse = _warehouse(f"_Test_PI_Freight_CF_{frappe.generate_hash(length=6)}")
			po = frappe.get_doc({
				"doctype": "Purchase Order",
				"supplier": _supplier(f"_Test PI Freight Supplier {frappe.generate_hash(length=6)}"),
				"delivery_warehouse": warehouse,
				**_production_group_dimensions(),
				"items": [{
					"item_variant": item_variant,
					"qty": 2,
					"uom": uom,
					"stock_uom": uom,
					"conversion_factor": 10,
					"rate": 100,
					"table_index": 0,
					"row_index": 0,
				}],
			})
			po.insert(ignore_permissions=True)
			po.submit()
			grn = frappe.get_doc({
				"doctype": "Goods Received Note",
				"against": "Purchase Order",
				"against_id": po.name,
				"to_warehouse": warehouse,
				"freight_charges": 20,
				"items": [{
					"item_variant": item_variant,
					"quantity": 2,
					"uom": uom,
					"stock_uom": uom,
					"conversion_factor": 10,
					"rate": 100,
					"ref_doctype": "Purchase Order Item",
					"ref_docname": po.items[0].name,
				}],
			})
			grn.insert(ignore_permissions=True)
			grn.submit()
			grn.reload()

			self.assertAlmostEqual(flt(grn.items[0].rate), 11, places=4)
			self.assertAlmostEqual(flt(grn.items[0].amount), 220, places=2)

			data = frappe.get_attr("yrp.yrp.doctype.purchase_invoice.purchase_invoice.fetch_grn_details")(
				[grn.name], "Purchase Order", po.supplier
			)
			self.assertAlmostEqual(flt(data["items"][0]["rate"]), 110, places=4)
			self.assertAlmostEqual(flt(data["items"][0]["amount"]), 220, places=2)
		finally:
			frappe.db.set_single_value("YRP Stock Settings", "freight_allocation_method", old_method or "By Quantity")
