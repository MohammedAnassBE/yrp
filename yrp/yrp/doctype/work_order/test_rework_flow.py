import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import flt, nowdate, nowtime

from yrp.stock.utils import get_stock_balance
from yrp.yrp.doctype.goods_received_note.goods_received_note import get_work_order_defaults
from yrp.yrp.doctype.goods_received_note.test_purchase_order_grn import (
	_address,
	_default_received_type,
	_item_uom,
	_process,
	_process_cost,
	_production_group_dimensions,
	_supplier,
	_supplier_warehouse,
	_test_item_variant,
)
from yrp.yrp.doctype.work_order.work_order import (
	create_rework_work_order,
	get_rework_source_rows,
)


def _received_type(name):
	if frappe.db.exists("Received Type", name):
		return name
	doc = frappe.get_doc({
		"doctype": "Received Type",
		"received_type_name": name,
	})
	doc.insert(ignore_permissions=True)
	return doc.name


def _set_rejected_received_type(name):
	"""Mark `name` as the rejected RT in YRP Stock Settings for the duration of
	the test transaction.
	"""
	frappe.db.set_single_value("YRP Stock Settings", "default_rejected_received_type", name)


def _make_parent_work_order(qty=10):
	item_variant = _test_item_variant()
	parent_item = frappe.db.get_value("Item Variant", item_variant, "item")
	uom = _item_uom(item_variant)
	supplier = _supplier(f"_T_Rework_Parent_Supplier_{frappe.generate_hash(length=6)}")
	delivery_location = _supplier(f"_T_Rework_Location_{frappe.generate_hash(length=6)}")
	supplier_wh = _supplier_warehouse(supplier, f"_T_Rework_Parent_Supplier_WH_{frappe.generate_hash(length=6)}")
	delivery_wh = _supplier_warehouse(delivery_location, f"_T_Rework_Location_WH_{frappe.generate_hash(length=6)}")
	process_name = _process("_Test Rework Parent Process")
	dimensions = _production_group_dimensions()
	_process_cost(process_name, parent_item, supplier, dimensions)

	wo = frappe.get_doc({
		"doctype": "Work Order",
		"supplier": supplier,
		"delivery_location": delivery_location,
		"planned_end_date": nowdate(),
		"supplier_address": _address(f"_T_Rework_Parent_Supplier_Addr_{frappe.generate_hash(length=6)}"),
		"delivery_address": _address(f"_T_Rework_Location_Addr_{frappe.generate_hash(length=6)}"),
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
			"table_index": 0,
			"row_index": 0,
		}],
	})
	wo.insert(ignore_permissions=True)
	wo.submit()
	return wo, supplier_wh, delivery_wh, item_variant, uom


def _make_parent_grn(wo, supplier_wh, delivery_wh, item_variant, uom, received_type, qty=10):
	row = wo.receivables[0]
	grn = frappe.get_doc({
		"doctype": "Goods Received Note",
		"against": "Work Order",
		"against_id": wo.name,
		"posting_date": nowdate(),
		"posting_time": nowtime(),
		"supplier": wo.supplier,
		"delivery_location": wo.delivery_location,
		"from_warehouse": supplier_wh,
		"to_warehouse": delivery_wh,
		"process_name": wo.process_name,
		"item": wo.item,
		"items": [{
			"item_variant": item_variant,
			"quantity": qty,
			"uom": uom,
			"stock_uom": uom,
			"conversion_factor": 1,
			"received_type": received_type,
			"ref_doctype": "Work Order Receivables",
			"ref_docname": row.name,
			"table_index": 0,
			"row_index": "0",
		}],
	})
	grn.insert(ignore_permissions=True)
	grn.submit()
	return grn


class TestReworkFlow(FrappeTestCase):
	def test_supplier_rework_flow_reserves_dispatches_and_returns_split_outputs(self):
		accepted_rt = _default_received_type()
		rework_rt = _received_type(f"_T_Rework_Source_{frappe.generate_hash(length=6)}")
		rejected_rt = _received_type(f"_T_Rework_Rejected_{frappe.generate_hash(length=6)}")
		_set_rejected_received_type(rejected_rt)
		wo, supplier_wh, delivery_wh, item_variant, uom = _make_parent_work_order(qty=10)
		_make_parent_grn(wo, supplier_wh, delivery_wh, item_variant, uom, rework_rt, qty=10)

		sources = get_rework_source_rows(wo.name)
		source = next(row for row in sources if row["received_type"] == rework_rt)
		rework_wo_name = create_rework_work_order(
			wo.name,
			[{"source_key": source["source_key"], "qty": 6}],
		)
		rework_wo = frappe.get_doc("Work Order", rework_wo_name)
		self.assertEqual(rework_wo.is_rework, 1)
		self.assertEqual(rework_wo.parent_wo, wo.name)
		self.assertEqual(rework_wo.deliverables[0].received_type, rework_rt)

		rework_wo.submit()
		sre = frappe.get_doc(
			"Stock Reservation Entry",
			frappe.db.get_value(
				"Stock Reservation Entry",
				{
					"voucher_type": "Work Order",
					"voucher_no": rework_wo.name,
					"voucher_detail_no": rework_wo.deliverables[0].name,
					"docstatus": 1,
				},
				"name",
			),
		)
		self.assertAlmostEqual(sre.reserved_qty, 6)
		self.assertEqual(sre.status, "Reserved")

		dc = frappe.get_doc({
			"doctype": "Delivery Challan",
			"work_order": rework_wo.name,
			"from_location": rework_wo.delivery_location,
			"supplier": rework_wo.supplier,
			"from_warehouse": delivery_wh,
			"to_warehouse": supplier_wh,
			"process_name": rework_wo.process_name,
			"item": rework_wo.item,
			"items": [{
				"item_variant": item_variant,
				"qty": 6,
				"delivered_quantity": 6,
				"uom": uom,
				"stock_uom": uom,
				"conversion_factor": 1,
				"received_type": rework_rt,
				"ref_doctype": "Work Order Deliverables",
				"ref_docname": rework_wo.deliverables[0].name,
				"table_index": 0,
				"row_index": "0",
			}],
		})
		dc.insert(ignore_permissions=True)
		dc.submit()
		sre.reload()
		self.assertAlmostEqual(sre.delivered_qty, 6)
		self.assertEqual(sre.status, "Delivered")

		defaults = get_work_order_defaults(rework_wo.name, dc.name)
		default_rows = defaults["items"]
		self.assertTrue(default_rows)
		self.assertTrue(all(row.get("delivery_challan_item") == dc.items[0].name for row in default_rows))

		grn = frappe.get_doc({
			"doctype": "Goods Received Note",
			"against": "Work Order",
			"against_id": rework_wo.name,
			"delivery_challan": dc.name,
			"posting_date": nowdate(),
			"posting_time": nowtime(),
			"supplier": rework_wo.supplier,
			"delivery_location": rework_wo.delivery_location,
			"from_warehouse": supplier_wh,
			"to_warehouse": delivery_wh,
			"process_name": rework_wo.process_name,
			"item": rework_wo.item,
			"items": [
				{
					"item_variant": item_variant,
					"quantity": 4,
					"uom": uom,
					"stock_uom": uom,
					"conversion_factor": 1,
					"received_type": accepted_rt,
					"delivery_challan_item": dc.items[0].name,
					"ref_doctype": "Work Order Receivables",
					"ref_docname": rework_wo.receivables[0].name,
					"table_index": 0,
					"row_index": "0::accepted",
				},
				{
					"item_variant": item_variant,
					"quantity": 2,
					"uom": uom,
					"stock_uom": uom,
					"conversion_factor": 1,
					"received_type": rejected_rt,
					"delivery_challan_item": dc.items[0].name,
					"ref_doctype": "Work Order Receivables",
					"ref_docname": rework_wo.receivables[0].name,
					"table_index": 0,
					"row_index": "0::rejected",
				},
			],
		})
		grn.insert(ignore_permissions=True)
		grn.submit()

		dc.reload()
		self.assertAlmostEqual(dc.items[0].received_quantity, 6)
		self.assertAlmostEqual(
			flt(get_stock_balance(item_variant, supplier_wh, received_type=rework_rt)),
			0,
		)
		self.assertAlmostEqual(
			flt(get_stock_balance(item_variant, delivery_wh, received_type=rework_rt)),
			4,
		)
		self.assertAlmostEqual(
			flt(get_stock_balance(item_variant, delivery_wh, received_type=accepted_rt)),
			4,
		)
		self.assertAlmostEqual(
			flt(get_stock_balance(item_variant, delivery_wh, received_type=rejected_rt)),
			2,
		)
