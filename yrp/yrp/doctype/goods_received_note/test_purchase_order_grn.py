import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import nowdate, nowtime

from yrp.stock.dimensions import get_stock_dimensions
from yrp.stock.utils import get_stock_balance
from yrp.yrp_stock.report.stock_availability.stock_availability import execute as stock_availability
from yrp.yrp.doctype.purchase_order.purchase_order import (
	close_purchase_order,
	refresh_status,
	reopen_purchase_order,
)


ITEM_VARIANT_CANDIDATES = (
	"Item-00005-45 cm-Blue",
	"Mens Sports Vest - 11222-Cut-Top Front-Peach-S",
)


def _test_item_variant():
	for item_variant in ITEM_VARIANT_CANDIDATES:
		if frappe.db.exists("Item Variant", item_variant):
			return item_variant
	frappe.throw("No test Item Variant found for Purchase Order GRN tests.")


def _item_uom(item_variant):
	parent_item = frappe.db.get_value("Item Variant", item_variant, "item")
	return frappe.db.get_value("Item", parent_item, "default_unit_of_measure") or "Piece"


def _supplier(supplier_name):
	existing = frappe.db.get_value("Supplier", {"supplier_name": supplier_name}, "name")
	if existing:
		return existing
	return frappe.get_doc(
		{"doctype": "Supplier", "supplier_name": supplier_name}
	).insert(ignore_permissions=True).name


def _warehouse(name):
	if not frappe.db.exists("Warehouse", name):
		frappe.get_doc({"doctype": "Warehouse", "name1": name}).insert(ignore_permissions=True)
	return name


def _supplier_warehouse(supplier, name):
	if not frappe.db.exists("Warehouse", name):
		frappe.get_doc(
			{"doctype": "Warehouse", "name1": name, "supplier": supplier}
		).insert(ignore_permissions=True)
	else:
		frappe.db.set_value("Warehouse", name, "supplier", supplier)
	return name


def _process(name):
	if not frappe.db.exists("Process", name):
		frappe.get_doc({"doctype": "Process", "process_name": name}).insert(ignore_permissions=True)
	return name


def _address(title):
	if frappe.db.exists("Address", title):
		return title
	return frappe.get_doc({
		"doctype": "Address",
		"address_title": title,
		"address_type": "Office",
		"address_line1": "Test Address",
		"city": "Test City",
		"country": "India",
	}).insert(ignore_permissions=True).name


def _default_received_type():
	received_type = frappe.db.get_single_value("YRP Stock Settings", "default_received_type")
	if received_type:
		return received_type
	if not frappe.db.exists("Received Type", "Accepted"):
		frappe.get_doc(
			{"doctype": "Received Type", "received_type_name": "Accepted", "is_default": 1}
		).insert(ignore_permissions=True)
	frappe.db.set_single_value("YRP Stock Settings", "default_received_type", "Accepted")
	return "Accepted"


def _production_group_dimensions():
	values = {}
	for dim in get_stock_dimensions():
		if not dim.get("is_production_group"):
			continue
		value = frappe.db.get_value(dim["dimension_doctype"], {}, "name")
		if not value:
			frappe.throw(f"No {dim['dimension_doctype']} found for Purchase Order GRN tests.")
		values[dim["fieldname"]] = value
	return values


def _purchase_order(qty, warehouse, supplier=None):
	item_variant = _test_item_variant()
	uom = _item_uom(item_variant)
	po = frappe.get_doc({
		"doctype": "Purchase Order",
		"supplier": supplier or _supplier("_Test PO GRN Supplier"),
		"delivery_warehouse": warehouse,
		**_production_group_dimensions(),
		"items": [{
			"item_variant": item_variant,
			"qty": qty,
			"uom": uom,
			"stock_uom": uom,
			"conversion_factor": 1,
			"rate": 25,
			"table_index": 0,
			"row_index": 0,
		}],
	})
	po.flags.ignore_permissions = True
	po.insert(ignore_permissions=True)
	po.submit()
	return po


def _purchase_order_grn(po, qty):
	item = po.items[0]
	grn = frappe.get_doc({
		"doctype": "Goods Received Note",
		"against": "Purchase Order",
		"against_id": po.name,
		"posting_date": nowdate(),
		"posting_time": nowtime(),
		"to_warehouse": po.delivery_warehouse,
		"items": [{
			"item_variant": item.item_variant,
			"quantity": qty,
			"uom": item.uom,
			"stock_uom": item.stock_uom,
			"conversion_factor": item.conversion_factor,
			"rate": item.rate,
			"ref_doctype": "Purchase Order Item",
			"ref_docname": item.name,
		}],
	})
	grn.flags.ignore_permissions = True
	grn.insert(ignore_permissions=True)
	return grn


def _stock_availability_row(item_variant, warehouse, filters=None):
	_, rows = stock_availability(
		{
			"item": item_variant,
			"warehouse": warehouse,
			**(filters or {}),
		}
	)
	for row in rows:
		if row.get("item_code") == item_variant and row.get("warehouse") == warehouse:
			return row
	return None


def _work_order(qty, warehouse):
	item_variant = _test_item_variant()
	parent_item = frappe.db.get_value("Item Variant", item_variant, "item")
	uom = _item_uom(item_variant)
	delivery_location = _supplier(f"_Test WO Availability Delivery {frappe.generate_hash(length=6)}")
	_supplier_warehouse(delivery_location, warehouse)
	wo = frappe.get_doc({
		"doctype": "Work Order",
		"is_rework": 1,
		"rework_type": "No Cost",
		"supplier": _supplier("_Test WO Availability Supplier"),
		"delivery_location": delivery_location,
		"planned_end_date": nowdate(),
		"supplier_address": _address(f"_Test WO Supplier Address {frappe.generate_hash(length=6)}"),
		"delivery_address": _address(f"_Test WO Delivery Address {frappe.generate_hash(length=6)}"),
		"process_name": _process("_Test WO Availability Process"),
		"item": parent_item,
		**_production_group_dimensions(),
		"deliverables": [{
			"item_variant": item_variant,
			"qty": 1,
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
	return wo


class TestPurchaseOrderGRN(FrappeTestCase):
	def test_po_ignores_blank_child_rows_before_mandatory_validation(self):
		warehouse = _warehouse("_Test_PO_BLANK_ROW_WH")
		item_variant = _test_item_variant()
		uom = _item_uom(item_variant)
		po = frappe.get_doc({
			"doctype": "Purchase Order",
			"supplier": _supplier("_Test PO Blank Row Supplier"),
			"delivery_warehouse": warehouse,
			**_production_group_dimensions(),
			"items": [
				{},
				{
					"item_variant": item_variant,
					"qty": 3,
					"uom": uom,
					"stock_uom": uom,
					"conversion_factor": 1,
					"rate": 25,
					"table_index": 0,
					"row_index": 0,
				},
			],
		})
		po.insert(ignore_permissions=True)

		self.assertEqual(len(po.items), 1)
		self.assertEqual(po.items[0].item_variant, item_variant)
		self.assertEqual(po.status, "Draft")

	def test_po_grn_updates_pending_received_and_stock(self):
		warehouse = _warehouse("_Test_PO_GRN_WH")
		received_type = _default_received_type()
		po = _purchase_order(qty=10, warehouse=warehouse)
		item_variant = po.items[0].item_variant
		baseline = get_stock_balance(item_variant, warehouse, received_type=received_type)

		grn = _purchase_order_grn(po, qty=4)
		grn.submit()

		po.reload()
		po_item = po.items[0]
		self.assertAlmostEqual(po_item.pending_quantity, 6)
		self.assertAlmostEqual(po_item.received_quantity, 4)
		self.assertEqual(po.status, "Partially Received")
		self.assertAlmostEqual(
			get_stock_balance(item_variant, warehouse, received_type=received_type),
			baseline + 4,
		)

		grn.cancel()

		po.reload()
		po_item = po.items[0]
		self.assertAlmostEqual(po_item.pending_quantity, 10)
		self.assertAlmostEqual(po_item.received_quantity, 0)
		self.assertEqual(po.status, "Ordered")
		self.assertAlmostEqual(
			get_stock_balance(item_variant, warehouse, received_type=received_type),
			baseline,
		)

	def test_po_grn_blocks_over_receipt(self):
		warehouse = _warehouse("_Test_PO_GRN_OVER_WH")
		po = _purchase_order(qty=5, warehouse=warehouse)
		grn = _purchase_order_grn(po, qty=6)

		with self.assertRaises(frappe.ValidationError):
			grn.submit()

	def test_po_status_management_for_full_receipt(self):
		warehouse = _warehouse("_Test_PO_GRN_STATUS_WH")
		po = _purchase_order(qty=5, warehouse=warehouse)

		grn = _purchase_order_grn(po, qty=5)
		grn.submit()

		po.reload()
		self.assertEqual(po.status, "Received")
		self.assertEqual(po.open_status, "Open")
		self.assertEqual(refresh_status(po.name), "Received")

		self.assertEqual(close_purchase_order(po.name), "Close")
		po.reload()
		self.assertEqual(po.status, "Closed")
		self.assertEqual(po.open_status, "Close")

		self.assertEqual(reopen_purchase_order(po.name), "Open")
		po.reload()
		self.assertEqual(po.status, "Received")
		self.assertEqual(po.open_status, "Open")

	def test_stock_availability_includes_purchase_order_pending(self):
		warehouse = _warehouse(f"_Test_PO_AVAIL_{frappe.generate_hash(length=6)}")
		po = _purchase_order(qty=7, warehouse=warehouse)
		item_variant = po.items[0].item_variant

		row = _stock_availability_row(item_variant, warehouse)

		self.assertIsNotNone(row)
		self.assertAlmostEqual(row.get("on_order"), 7)
		self.assertAlmostEqual(row.get("wo_expected"), 0)

	def test_stock_availability_includes_work_order_receivables_pending(self):
		warehouse = f"_Test_WO_AVAIL_{frappe.generate_hash(length=6)}"
		wo = _work_order(qty=6, warehouse=warehouse)
		item_variant = wo.receivables[0].item_variant

		row = _stock_availability_row(item_variant, warehouse)

		self.assertIsNotNone(row)
		self.assertAlmostEqual(row.get("on_order"), 0)
		self.assertAlmostEqual(row.get("wo_expected"), 6)
