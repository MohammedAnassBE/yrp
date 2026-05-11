import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import nowdate, nowtime

from yrp.stock.dimensions import get_stock_dimensions
from yrp.stock.utils import get_stock_balance


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


def _purchase_order(qty, warehouse):
	item_variant = _test_item_variant()
	uom = _item_uom(item_variant)
	po = frappe.get_doc({
		"doctype": "Purchase Order",
		"supplier": _supplier("_Test PO GRN Supplier"),
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


class TestPurchaseOrderGRN(FrappeTestCase):
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
