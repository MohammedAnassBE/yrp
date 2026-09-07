from contextlib import nullcontext
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import nowdate, nowtime

from yrp.stock.dimensions import get_stock_dimensions
from yrp.stock.utils import get_stock_balance
from yrp.yrp_stock.report.yrp_stock_availability.yrp_stock_availability import (
	execute as stock_availability,
)
from yrp.yrp.doctype.yrp_purchase_order.yrp_purchase_order import (
	close_purchase_order,
	refresh_status,
	reopen_purchase_order,
)
from yrp.yrp.doctype.yrp_goods_received_note.yrp_goods_received_note import _validate_defaults_source
from yrp.yrp.doctype.yrp_goods_received_note.yrp_goods_received_note import (
	_pending_purchase_order_rows,
)


ITEM_VARIANT_CANDIDATES = (
	"Item-00005-45 cm-Blue",
	"Mens Sports Vest - 11222-Cut-Top Front-Peach-S",
)


def _test_item_variant():
	for item_variant in ITEM_VARIANT_CANDIDATES:
		parent_item = frappe.db.get_value('YRP Item Variant', item_variant, "item")
		item = (
			frappe.db.get_value(
				'YRP Item', parent_item, ["is_stock_item", "dependent_attribute"], as_dict=True
			)
			if parent_item
			else None
		)
		if item and item.is_stock_item and not item.dependent_attribute:
			return item_variant
	# Most tests in this module assert stock quantities in the Item's default
	# UOM.  Prefer a non-dependent Item so a stage-level alternate UOM does not
	# change those unrelated baselines.
	item_variant = frappe.db.sql(
		"""
		SELECT iv.name
		FROM `tabYRP Item Variant` iv
		INNER JOIN `tabYRP Item` i ON i.name = iv.item
		WHERE COALESCE(i.dependent_attribute, '') = ''
			AND COALESCE(i.default_unit_of_measure, '') != ''
			AND COALESCE(i.is_stock_item, 0) = 1
		ORDER BY iv.creation
		LIMIT 1
		""",
		pluck=True,
	)
	if item_variant:
		return item_variant[0]
	if item_variant := frappe.db.get_value('YRP Item Variant', {}, "name"):
		return item_variant
	frappe.throw("No test Item Variant found for Purchase Order GRN tests.")


def _item_uom(item_variant):
	parent_item = frappe.db.get_value('YRP Item Variant', item_variant, "item")
	return frappe.db.get_value('YRP Item', parent_item, "default_unit_of_measure") or "Piece"


def _supplier(supplier_name):
	existing = frappe.db.get_value('YRP Supplier', {"supplier_name": supplier_name}, "name")
	if existing:
		return existing
	return frappe.get_doc(
		{"doctype": 'YRP Supplier', "supplier_name": supplier_name}
	).insert(
		ignore_permissions=True,
		set_name=f"_TEST-SUP-{frappe.generate_hash(length=10)}",
	).name


def _warehouse(name):
	existing = frappe.db.get_value('YRP Warehouse', {"name": name}, "name") or frappe.db.get_value(
		'YRP Warehouse', {"name1": name}, "name"
	)
	if existing:
		return existing
	return frappe.get_doc({"doctype": 'YRP Warehouse', "name1": name}).insert(
		ignore_permissions=True
	).name


def _supplier_warehouse(supplier, name):
	existing = frappe.db.get_value('YRP Warehouse', {"name": name}, "name") or frappe.db.get_value(
		'YRP Warehouse', {"name1": name}, "name"
	)
	if not existing:
		existing = frappe.get_doc(
			{"doctype": 'YRP Warehouse', "name1": name, "supplier": supplier}
		).insert(ignore_permissions=True).name
	else:
		frappe.db.set_value('YRP Warehouse', existing, "supplier", supplier)
	return existing


def _process(name):
	if not frappe.db.exists('YRP Process', name):
		frappe.get_doc({"doctype": 'YRP Process', "process_name": name}).insert(ignore_permissions=True)
	return name


def _tax_slab():
	if not frappe.db.exists('YRP Tax Slab', "0"):
		frappe.get_doc({"doctype": 'YRP Tax Slab', "percentage": "0", "enabled": 1}).insert(ignore_permissions=True)
	return "0"


def _process_cost(process_name, item, supplier, dimensions=None, rate=12, is_rework=0):
	dimensions = dimensions or {}
	filters = {
		"process_name": process_name,
		"item": item,
		"supplier": supplier,
		"is_rework": is_rework,
		"is_expired": 0,
		"docstatus": 1,
		**dimensions,
	}
	existing = frappe.db.get_value('YRP Process Cost', filters, "name")
	if existing:
		return existing
	uom = frappe.db.get_value('YRP Item', item, "default_unit_of_measure") or "Piece"
	doc = frappe.get_doc({
		"doctype": 'YRP Process Cost',
		"item": item,
		"uom": uom,
		"supplier": supplier,
		"process_name": process_name,
		"from_date": "2000-01-01",
		"tax_slab": _tax_slab(),
		"is_rework": is_rework,
		**dimensions,
		"process_cost_values": [{
			"min_order_qty": 0,
			"price": rate,
		}],
	})
	doc.insert(ignore_permissions=True)
	doc.submit()
	return doc.name


def _address(title):
	if frappe.db.exists("Address", title):
		return title
	return frappe.get_doc({
		"doctype": "Address",
		"address_title": title,
		"address_type": "Office",
		"address_line1": "Test Address",
		"city": "Test City",
		"state": "Tamil Nadu",
		"country": "India",
	}).insert(ignore_permissions=True).name


def _default_received_type():
	received_type = frappe.db.get_single_value('YRP YRP Stock Settings', "default_received_type")
	if received_type:
		return received_type
	if not frappe.db.exists('YRP Received Type', "Accepted"):
		frappe.get_doc(
			{"doctype": 'YRP Received Type', "received_type_name": "Accepted", "is_default": 1}
		).insert(ignore_permissions=True)
	frappe.db.set_single_value('YRP YRP Stock Settings', "default_received_type", "Accepted")
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


def _item_variant_for_production_dimensions(item_variant, dimensions):
	"""Keep host production dimensions and the test Item internally consistent."""
	for dimension in get_stock_dimensions():
		if not dimension.get("is_production_group"):
			continue
		fieldname = dimension["fieldname"]
		doctype = dimension["dimension_doctype"]
		if not frappe.get_meta(doctype).has_field("item"):
			continue
		dimension_item = frappe.db.get_value(
			doctype, dimensions.get(fieldname), "item"
		)
		dimension_variant = frappe.db.get_value(
			'YRP Item Variant', {"item": dimension_item}, "name"
		)
		if dimension_item and dimension_variant:
			return dimension_variant
	return item_variant


def _purchase_order(
	qty,
	warehouse,
	supplier=None,
	rate=25,
	discount_percentage=0,
	item_variant=None,
):
	item_variant = item_variant or _test_item_variant()
	uom = _item_uom(item_variant)
	po = frappe.get_doc({
		"doctype": 'YRP Purchase Order',
		"supplier": supplier or _supplier("_Test PO GRN Supplier"),
		"delivery_warehouse": warehouse,
		**_production_group_dimensions(),
		"items": [{
			"item_variant": item_variant,
			"qty": qty,
			"uom": uom,
			"stock_uom": uom,
			"conversion_factor": 1,
			"rate": rate,
			"discount_percentage": discount_percentage,
			"table_index": 0,
			"row_index": 0,
		}],
	})
	po.flags.ignore_permissions = True
	# These tests supply explicit rates and exercise GRN valuation, not the
	# separate Item Price resolver. Keep site-level price masters out of scope.
	with patch(
		"yrp.yrp.doctype.yrp_purchase_order.yrp_purchase_order.validate_price_details",
		return_value=[],
	):
		po.insert(ignore_permissions=True)
		po.submit()
	return po


def _purchase_order_grn(po, qty):
	item = po.items[0]
	grn = frappe.get_doc({
		"doctype": 'YRP Goods Received Note',
		"against": 'YRP Purchase Order',
		"against_id": po.name,
		"posting_date": nowdate(),
		"posting_time": nowtime(),
		"to_warehouse": po.delivery_warehouse,
		"supplier_address": po.supplier_address
		or _address(f"_Test PO GRN Supplier Address {frappe.generate_hash(length=6)}"),
		"delivery_address": po.delivery_address
		or _address(f"_Test PO GRN Delivery Address {frappe.generate_hash(length=6)}"),
		"items": [{
			"item_variant": item.item_variant,
			"quantity": qty,
			"uom": item.uom,
			"stock_uom": item.stock_uom,
			"conversion_factor": item.conversion_factor,
			"rate": item.rate,
			"ref_doctype": 'YRP Purchase Order Item',
			"ref_docname": item.name,
		}],
	})
	grn.flags.ignore_permissions = True
	grn.insert(ignore_permissions=True)
	return grn


def _rework_source_refs(qty=1):
	warehouse = _warehouse(f"_Test Rework Source WH {frappe.generate_hash(length=6)}")
	po = _purchase_order(qty, warehouse)
	grn = _purchase_order_grn(po, qty)
	grn.submit()
	return {
		"source_grn": grn.name,
		"source_grn_item": grn.items[0].name,
	}


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
	dimensions = _production_group_dimensions()
	item_variant = _item_variant_for_production_dimensions(item_variant, dimensions)
	parent_item = frappe.db.get_value('YRP Item Variant', item_variant, "item")
	uom = _item_uom(item_variant)
	delivery_location = _supplier(f"_Test WO Availability Delivery {frappe.generate_hash(length=6)}")
	supplier = _supplier("_Test WO Availability Supplier")
	process_name = _process("_Test WO Availability Process")
	_supplier_warehouse(delivery_location, warehouse)
	_process_cost(process_name, parent_item, supplier, dimensions)
	wo = frappe.get_doc({
		"doctype": 'YRP Work Order',
		"supplier": supplier,
		"delivery_location": delivery_location,
		"planned_end_date": nowdate(),
		"supplier_address": _address(f"_Test WO Supplier Address {frappe.generate_hash(length=6)}"),
		"delivery_address": _address(f"_Test WO Delivery Address {frappe.generate_hash(length=6)}"),
		"process_name": process_name,
		"item": parent_item,
		**dimensions,
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
	host_validator = (
		patch("essdee_yrp.work_order_hooks.validate_lot_process_selection")
		if "essdee_yrp" in frappe.get_installed_apps()
		else nullcontext()
	)
	with host_validator:
		wo.insert(ignore_permissions=True)
		wo.submit()
	return wo


class TestPurchaseOrderGRN(FrappeTestCase):
	def test_po_row_stock_dimensions_are_carried_to_grn_defaults(self):
		row = frappe._dict(
			name="POI-1",
			item_variant="ITEM-1",
			qty=5,
			pending_quantity=5,
			uom="Nos",
			stock_uom="Nos",
			conversion_factor=1,
			table_index=0,
			row_index=0,
			set_combination="{}",
			rate=10,
			discount_percentage=0,
			lot="LOT-ROW",
			received_type="Accepted",
		)
		with (
			patch(
				"yrp.stock.dimensions.get_dimension_fieldnames",
				return_value=["lot", "received_type"],
			),
			patch(
				"yrp.yrp.doctype.yrp_goods_received_note.yrp_goods_received_note._po_excess_percentage",
				return_value=0,
			),
		):
			rows = _pending_purchase_order_rows(
				frappe._dict(items=[row]), existing_rows=None
			)

		self.assertEqual(rows[0]["lot"], "LOT-ROW")
		self.assertEqual(rows[0]["received_type"], "Accepted")

	def test_source_defaults_require_submitted_open_document(self):
		with self.assertRaisesRegex(frappe.ValidationError, "must be submitted"):
			_validate_defaults_source(frappe._dict(
				doctype='YRP Purchase Order',
				name="PO-DRAFT",
				docstatus=0,
				open_status="Open",
			))
		with self.assertRaisesRegex(frappe.ValidationError, "is closed"):
			_validate_defaults_source(frappe._dict(
				doctype='YRP Work Order',
				name="WO-CLOSED",
				docstatus=1,
				open_status="Close",
			))

	def test_po_ignores_blank_child_rows_before_mandatory_validation(self):
		warehouse = _warehouse("_Test_PO_BLANK_ROW_WH")
		item_variant = _test_item_variant()
		uom = _item_uom(item_variant)
		po = frappe.get_doc({
			"doctype": 'YRP Purchase Order',
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

	def test_po_discount_reduces_grn_stock_value(self):
		warehouse = _warehouse(f"_Test_PO_GRN_DISCOUNT_{frappe.generate_hash(length=6)}")
		po = _purchase_order(
			qty=10,
			warehouse=warehouse,
			rate=100,
			discount_percentage=10,
		)

		grn = _purchase_order_grn(po, qty=10)
		grn.submit()
		grn.reload()

		self.assertAlmostEqual(grn.items[0].rate, 90)
		self.assertAlmostEqual(grn.items[0].amount, 900)
		self.assertAlmostEqual(grn.total, 900)

		sle = frappe.db.get_value(
			'YRP Stock Ledger Entry',
			{
				"voucher_type": 'YRP Goods Received Note',
				"voucher_no": grn.name,
				"voucher_detail_no": grn.items[0].name,
				"is_cancelled": 0,
			},
			["rate", "stock_value_difference"],
			as_dict=True,
		)
		self.assertIsNotNone(sle)
		self.assertAlmostEqual(sle.rate, 90)
		self.assertAlmostEqual(sle.stock_value_difference, 900)

	def test_partial_po_grn_applies_discount_after_stock_uom_conversion(self):
		from yrp.stock.test_uom import _dependent_item_variant, _ensure_uom

		warehouse = _warehouse(f"_Test_PO_GRN_DISCOUNT_UOM_{frappe.generate_hash(length=6)}")
		_item, variant = _dependent_item_variant(
			_ensure_uom("Piece"), _ensure_uom("Box")
		)
		po = _purchase_order(
			qty=2,
			warehouse=warehouse,
			rate=100,
			discount_percentage=10,
			item_variant=variant.name,
		)

		# Receive one purchase UOM: net value 1 x 90 = 90, stock qty 1 x 10 = 10.
		grn = _purchase_order_grn(po, qty=1)
		grn.submit()
		grn.reload()

		self.assertAlmostEqual(grn.items[0].stock_qty, 10)
		self.assertAlmostEqual(grn.items[0].rate, 9)
		self.assertAlmostEqual(grn.items[0].amount, 90)
		self.assertAlmostEqual(grn.total, 90)

		po.reload()
		self.assertAlmostEqual(po.items[0].pending_quantity, 1)
		self.assertAlmostEqual(po.items[0].received_quantity, 1)

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
