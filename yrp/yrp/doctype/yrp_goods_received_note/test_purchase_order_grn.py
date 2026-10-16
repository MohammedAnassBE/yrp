from contextlib import nullcontext
from unittest import TestCase
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import nowdate, nowtime

from yrp.stock.dimensions import get_stock_dimensions
from yrp.stock.utils import get_stock_balance
from yrp.yrp.doctype.yrp_goods_received_note.yrp_goods_received_note import (
	_pending_purchase_order_rows,
	_validate_defaults_source,
	get_purchase_order_lot_options,
	validate_purchase_order_lot,
)
from yrp.yrp.doctype.yrp_item.yrp_item import get_parent_item
from yrp.yrp.doctype.yrp_purchase_order.yrp_purchase_order import (
	close_purchase_order,
	refresh_status,
	reopen_purchase_order,
)
from yrp.yrp_stock.report.yrp_stock_availability.yrp_stock_availability import (
	execute as stock_availability,
)

ITEM_VARIANT_CANDIDATES = (
	"Item-00005-45 cm-Blue",
	"Mens Sports Vest - 11222-Cut-Top Front-Peach-S",
)


def _test_item_variant():
	for item_variant in ITEM_VARIANT_CANDIDATES:
		parent_item = get_parent_item(item_variant)
		item = (
			frappe.db.get_value("Item", parent_item, ["is_stock_item", "dependent_attribute"], as_dict=True)
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
		FROM `tabItem` iv
		INNER JOIN `tabItem` i ON i.name = COALESCE(NULLIF(iv.variant_of, ''), iv.name)
		WHERE COALESCE(i.dependent_attribute, '') = ''
			AND COALESCE(i.stock_uom, '') != ''
			AND COALESCE(i.is_stock_item, 0) = 1
			AND COALESCE(iv.has_variants, 0) = 0
		ORDER BY iv.creation
		LIMIT 1
		""",
		pluck=True,
	)
	if item_variant:
		return item_variant[0]
	if item_variant := frappe.db.get_value("Item", {}, "name"):
		return item_variant
	frappe.throw("No test Item Variant found for Purchase Order GRN tests.")


def _item_uom(item_variant):
	parent_item = get_parent_item(item_variant)
	return frappe.db.get_value("Item", parent_item, "stock_uom") or "Piece"


def _supplier(supplier_name):
	existing = frappe.db.get_value("Supplier", {"supplier_name": supplier_name}, "name")
	if existing:
		return existing
	return (
		frappe.get_doc({"doctype": "Supplier", "supplier_name": supplier_name})
		.insert(
			ignore_permissions=True,
			set_name=f"_TEST-SUP-{frappe.generate_hash(length=10)}",
		)
		.name
	)


def _warehouse(name):
	existing = frappe.db.get_value("Warehouse", {"name": name}, "name") or frappe.db.get_value(
		"Warehouse", {"warehouse_name": name}, "name"
	)
	if existing:
		return existing
	return (
		frappe.get_doc({"doctype": "Warehouse", "warehouse_name": name}).insert(ignore_permissions=True).name
	)


def _supplier_warehouse(supplier, name):
	existing = frappe.db.get_value("Warehouse", {"name": name}, "name") or frappe.db.get_value(
		"Warehouse", {"warehouse_name": name}, "name"
	)
	if not existing:
		existing = (
			frappe.get_doc({"doctype": "Warehouse", "warehouse_name": name, "supplier": supplier})
			.insert(ignore_permissions=True)
			.name
		)
	else:
		frappe.db.set_value("Warehouse", existing, "supplier", supplier)
	return existing


def _process(name):
	if not frappe.db.exists("YRP Process", name):
		frappe.get_doc({"doctype": "YRP Process", "process_name": name}).insert(ignore_permissions=True)
	return name


def _tax_slab():
	if not frappe.db.exists("YRP Tax Slab", "0"):
		frappe.get_doc({"doctype": "YRP Tax Slab", "percentage": "0", "enabled": 1}).insert(
			ignore_permissions=True
		)
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
	existing = frappe.db.get_value("YRP Process Cost", filters, "name")
	if existing:
		return existing
	uom = frappe.db.get_value("Item", item, "stock_uom") or "Piece"
	doc = frappe.get_doc(
		{
			"doctype": "YRP Process Cost",
			"item": item,
			"uom": uom,
			"supplier": supplier,
			"process_name": process_name,
			"from_date": "2000-01-01",
			"tax_slab": _tax_slab(),
			"is_rework": is_rework,
			**dimensions,
			"process_cost_values": [
				{
					"min_order_qty": 0,
					"price": rate,
				}
			],
		}
	)
	doc.insert(ignore_permissions=True)
	doc.submit()
	return doc.name


def _address(title):
	if frappe.db.exists("Address", title):
		return title
	return (
		frappe.get_doc(
			{
				"doctype": "Address",
				"address_title": title,
				"address_type": "Office",
				"address_line1": "Test Address",
				"city": "Test City",
				"state": "Tamil Nadu",
				"country": "India",
			}
		)
		.insert(ignore_permissions=True)
		.name
	)


def _default_received_type():
	received_type = frappe.db.get_single_value("YRP Stock Settings", "default_received_type")
	if received_type:
		return received_type
	if not frappe.db.exists("YRP Received Type", "Accepted"):
		frappe.get_doc(
			{"doctype": "YRP Received Type", "received_type_name": "Accepted", "is_default": 1}
		).insert(ignore_permissions=True)
	frappe.db.set_single_value("YRP Stock Settings", "default_received_type", "Accepted")
	return "Accepted"


def _production_group_dimensions():
	values = {}
	for dim in get_stock_dimensions():
		if not dim.get("is_production_group"):
			continue
		dimension_doctype = dim["dimension_doctype"]
		filters = {}
		if frappe.get_meta(dimension_doctype).has_field("item"):
			# A production group with no Item cannot safely drive Item-derived
			# Process Cost or Work Order validation.
			filters = {"item": ["is", "set"]}
		value = frappe.db.get_value(dimension_doctype, filters, "name")
		if not value:
			frappe.throw(f"No usable {dimension_doctype} found for Purchase Order GRN tests.")
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
		dimension_item = frappe.db.get_value(doctype, dimensions.get(fieldname), "item")
		if not dimension_item:
			continue
		if get_parent_item(item_variant) == dimension_item:
			return item_variant
		dimension_variant = frappe.db.get_value("Item", {"variant_of": dimension_item}, "name")
		if dimension_variant:
			return dimension_variant
		if frappe.db.exists("Item", dimension_item):
			return dimension_item
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
	po = frappe.get_doc(
		{
			"doctype": "Purchase Order",
			"is_yrp_managed": 1,
			"supplier": supplier or _supplier("_Test PO GRN Supplier"),
			"set_warehouse": warehouse,
			**_production_group_dimensions(),
			"items": [
				{
					"item_code": item_variant,
					"qty": qty,
					"uom": uom,
					"stock_uom": uom,
					"conversion_factor": 1,
					"rate": rate,
					"discount_percentage": discount_percentage,
					"table_index": 0,
					"row_index": 0,
				}
			],
		}
	)
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


def _purchase_order_grn(po, qty, dimensions=None):
	item = po.items[0]
	grn = frappe.get_doc(
		{
			"doctype": "YRP Goods Received Note",
			"against": "Purchase Order",
			"against_id": po.name,
			"posting_date": nowdate(),
			"posting_time": nowtime(),
			"to_warehouse": po.set_warehouse,
			"supplier_address": po.supplier_address
			or _address(f"_Test PO GRN Supplier Address {frappe.generate_hash(length=6)}"),
			"delivery_address": po.shipping_address
			or _address(f"_Test PO GRN Delivery Address {frappe.generate_hash(length=6)}"),
			"items": [
				{
					"item_variant": item.item_code,
					"quantity": qty,
					"uom": item.uom,
					"stock_uom": item.stock_uom,
					"conversion_factor": item.conversion_factor,
					"rate": item.rate,
					"ref_doctype": "Purchase Order Item",
					"ref_docname": item.name,
					**(dimensions or {}),
				}
			],
		}
	)
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
	parent_item = get_parent_item(item_variant)
	uom = _item_uom(item_variant)
	delivery_location = _supplier(f"_Test WO Availability Delivery {frappe.generate_hash(length=6)}")
	supplier = _supplier("_Test WO Availability Supplier")
	process_name = _process("_Test WO Availability Process")
	_supplier_warehouse(delivery_location, warehouse)
	_process_cost(process_name, parent_item, supplier, dimensions)
	wo = frappe.get_doc(
		{
			"doctype": "YRP Work Order",
			"supplier": supplier,
			"delivery_location": delivery_location,
			"planned_end_date": nowdate(),
			"supplier_address": _address(f"_Test WO Supplier Address {frappe.generate_hash(length=6)}"),
			"delivery_address": _address(f"_Test WO Delivery Address {frappe.generate_hash(length=6)}"),
			"process_name": process_name,
			"item": parent_item,
			**dimensions,
			"deliverables": [
				{
					"item_variant": item_variant,
					"qty": 1,
					"uom": uom,
					"table_index": 0,
					"row_index": 0,
				}
			],
			"receivables": [
				{
					"item_variant": item_variant,
					"qty": qty,
					"uom": uom,
					"table_index": 0,
					"row_index": 0,
				}
			],
		}
	)
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
			item_code="ITEM-1",
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
			rows = _pending_purchase_order_rows(frappe._dict(items=[row]), existing_rows=None)

		self.assertEqual(rows[0]["lot"], "LOT-ROW")
		self.assertEqual(rows[0]["received_type"], "Accepted")

	def test_source_defaults_require_submitted_open_document(self):
		with self.assertRaisesRegex(frappe.ValidationError, "must be submitted"):
			_validate_defaults_source(
				frappe._dict(
					doctype="Purchase Order",
					name="PO-DRAFT",
					docstatus=0,
					open_status="Open",
				)
			)
		with self.assertRaisesRegex(frappe.ValidationError, "is closed"):
			_validate_defaults_source(
				frappe._dict(
					doctype="YRP Work Order",
					name="WO-CLOSED",
					docstatus=1,
					open_status="Close",
				)
			)

	def test_po_ignores_blank_child_rows_before_mandatory_validation(self):
		warehouse = _warehouse("_Test_PO_BLANK_ROW_WH")
		item_variant = _test_item_variant()
		uom = _item_uom(item_variant)
		po = frappe.get_doc(
			{
				"doctype": "Purchase Order",
				"is_yrp_managed": 1,
				"supplier": _supplier("_Test PO Blank Row Supplier"),
				"set_warehouse": warehouse,
				**_production_group_dimensions(),
				"items": [
					{},
					{
						"item_code": item_variant,
						"qty": 3,
						"uom": uom,
						"stock_uom": uom,
						"conversion_factor": 1,
						"rate": 25,
						"table_index": 0,
						"row_index": 0,
					},
				],
			}
		)
		po.insert(ignore_permissions=True)

		self.assertEqual(len(po.items), 1)
		self.assertEqual(po.items[0].item_code, item_variant)
		self.assertEqual(po.status, "Draft")

	def test_po_grn_updates_pending_received_and_stock(self):
		warehouse = _warehouse("_Test_PO_GRN_WH")
		received_type = _default_received_type()
		po = _purchase_order(qty=10, warehouse=warehouse)
		item_variant = po.items[0].item_code
		baseline = get_stock_balance(item_variant, warehouse, received_type=received_type)

		grn = _purchase_order_grn(po, qty=4)
		grn.submit()

		po.reload()
		po_item = po.items[0]
		self.assertAlmostEqual(po_item.pending_quantity, 6)
		self.assertAlmostEqual(po_item.received_qty, 4)
		self.assertEqual(po.status, "To Receive")
		self.assertEqual(po.yrp_fulfillment_status, "Partially Received")
		self.assertAlmostEqual(
			get_stock_balance(item_variant, warehouse, received_type=received_type),
			baseline + 4,
		)

		grn.cancel()

		po.reload()
		po_item = po.items[0]
		self.assertAlmostEqual(po_item.pending_quantity, 10)
		self.assertAlmostEqual(po_item.received_qty, 0)
		self.assertEqual(po.status, "To Receive")
		self.assertEqual(po.yrp_fulfillment_status, "Ordered")
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
			"YRP Stock Ledger Entry",
			{
				"voucher_type": "YRP Goods Received Note",
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
		_item, variant = _dependent_item_variant(_ensure_uom("Piece"), _ensure_uom("Box"))
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
		self.assertAlmostEqual(po.items[0].received_qty, 1)

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
		self.assertEqual(po.status, "Completed")
		self.assertEqual(po.yrp_fulfillment_status, "Received")
		self.assertEqual(po.open_status, "Open")
		self.assertEqual(refresh_status(po.name), "Received")

		self.assertEqual(close_purchase_order(po.name), "Close")
		po.reload()
		self.assertEqual(po.status, "Closed")
		self.assertEqual(po.open_status, "Close")

		self.assertEqual(reopen_purchase_order(po.name), "Open")
		po.reload()
		self.assertEqual(po.status, "Completed")
		self.assertEqual(po.yrp_fulfillment_status, "Received")
		self.assertEqual(po.open_status, "Open")

	def test_stock_availability_includes_purchase_order_pending(self):
		warehouse = _warehouse(f"_Test_PO_AVAIL_{frappe.generate_hash(length=6)}")
		po = _purchase_order(qty=7, warehouse=warehouse)
		item_variant = po.items[0].item_code

		row = _stock_availability_row(item_variant, warehouse)

		self.assertIsNotNone(row)
		self.assertAlmostEqual(row.get("on_order"), 7)
		self.assertAlmostEqual(row.get("wo_expected"), 0)

	def test_stock_availability_includes_work_order_receivables_pending(self):
		warehouse = f"_Test_WO_AVAIL_{frappe.generate_hash(length=6)}"
		wo = _work_order(qty=6, warehouse=warehouse)
		warehouse = frappe.db.get_value("Warehouse", {"supplier": wo.delivery_location}, "name")
		item_variant = wo.receivables[0].item_variant

		row = _stock_availability_row(item_variant, warehouse)

		self.assertIsNotNone(row)
		self.assertAlmostEqual(row.get("on_order"), 0)
		self.assertAlmostEqual(row.get("wo_expected"), 6)


class TestPurchaseOrderLotValidation(TestCase):
	def test_lot_options_are_per_row_and_exclude_header(self):
		po = frappe._dict(
			lot="HEADER",
			items=[
				frappe._dict(name="ROW-1", lot="OWN"),
				frappe._dict(name="ROW-2", lot="OTHER"),
				frappe._dict(name="ROW-3", lot=None),
			],
		)
		with (
			patch(
				"yrp.yrp.doctype.yrp_goods_received_note.yrp_goods_received_note.get_purchase_order_lot_fieldname",
				return_value="lot",
			),
			patch("frappe.get_hooks", return_value=["hook-one", "hook-two"]),
			patch(
				"frappe.get_attr", side_effect=[lambda po: ["LINKED", "OWN", ""], lambda po: ["LINKED", None]]
			),
		):
			self.assertEqual(
				get_purchase_order_lot_options(po),
				{"ROW-1": ["OWN", "LINKED"], "ROW-2": ["OTHER", "LINKED", "OWN"], "ROW-3": ["LINKED", "OWN"]},
			)

	def test_no_linked_lots_keeps_only_own_lot(self):
		po = frappe._dict(items=[frappe._dict(name="ROW-1", lot="OWN"), frappe._dict(name="ROW-2")])
		with (
			patch(
				"yrp.yrp.doctype.yrp_goods_received_note.yrp_goods_received_note.get_purchase_order_lot_fieldname",
				return_value="lot",
			),
			patch("frappe.get_hooks", return_value=[]),
		):
			self.assertEqual(get_purchase_order_lot_options(po), {"ROW-1": ["OWN"], "ROW-2": []})

	def test_own_and_linked_lots_are_allowed_per_row(self):
		options = {"ROW-1": ["OWN", "LINKED"], "ROW-2": ["OTHER", "LINKED"]}
		for reference, lot in (
			("ROW-1", "OWN"),
			("ROW-1", "LINKED"),
			("ROW-2", "OTHER"),
			("ROW-2", "LINKED"),
		):
			with self.subTest(reference=reference, lot=lot):
				validate_purchase_order_lot({"ref_docname": reference, "lot": lot}, "lot", options, "PO-1", 1)

	def test_unlinked_and_empty_lots_are_rejected(self):
		options = {"ROW-1": ["OWN", "LINKED"], "ROW-2": ["OTHER", "LINKED"]}
		for lot in ("OTHER", "HEADER", "DEFAULT", None, ""):
			with self.subTest(lot=lot), self.assertRaises(frappe.ValidationError):
				validate_purchase_order_lot({"ref_docname": "ROW-1", "lot": lot}, "lot", options, "PO-1", 1)

	def test_empty_options_allow_only_empty_lot(self):
		for lot in (None, ""):
			validate_purchase_order_lot({"ref_docname": "ROW-1", "lot": lot}, "lot", {"ROW-1": []}, "PO-1", 1)
		with self.assertRaises(frappe.ValidationError):
			validate_purchase_order_lot(
				{"ref_docname": "ROW-1", "lot": "ANY"}, "lot", {"ROW-1": []}, "PO-1", 1
			)

	def test_missing_po_row_is_rejected(self):
		for reference in (None, "", "UNKNOWN"):
			with self.subTest(reference=reference), self.assertRaises(frappe.ValidationError):
				validate_purchase_order_lot({"ref_docname": reference}, "lot", {"ROW-1": []}, "PO-1", 1)

	def test_no_lot_dimension_does_not_restrict(self):
		validate_purchase_order_lot({"lot": "ANY"}, None, {}, "PO-1", 1)


class TestPurchaseOrderLotSplits(FrappeTestCase):
	def order_with_two_lots(self):
		from yrp.stock.dimensions import get_production_group

		dimension = get_production_group()
		if not dimension:
			self.skipTest("A production-group dimension is required.")
		warehouse = _warehouse(f"_Test_PO_LOTS_{frappe.generate_hash(length=6)}")
		po = _purchase_order(qty=10, warehouse=warehouse)
		fieldname = dimension["fieldname"]
		first_lot = po.items[0].get(fieldname) or po.get(fieldname)
		other_lot = frappe.db.get_value(dimension["dimension_doctype"], {"name": ["!=", first_lot]}, "name")
		if not other_lot:
			self.skipTest("Two production groups are required.")
		# The second lot is allowed through the linked-lot hook, not the header.
		frappe.db.set_value("Purchase Order Item", po.items[0].name, fieldname, first_lot)
		frappe.db.set_value("Purchase Order", po.name, fieldname, other_lot)
		if po.meta.has_field("sd_lot"):
			po.append("sd_lot", {"lot": other_lot}).db_insert()
		po.reload()
		return po, fieldname, first_lot, other_lot

	def test_desk_insert_into_another_lot_submits(self):
		po, fieldname, _first_lot, other_lot = self.order_with_two_lots()
		grn = _purchase_order_grn(po, qty=6, dimensions={fieldname: other_lot})
		grn.submit()
		grn.reload()
		self.assertEqual(grn.items[0].get(fieldname), other_lot)
		self.assertEqual(grn.items[0].ref_docname, po.items[0].name)

	def test_split_reload_submit_and_cancel_keep_lots_and_totals(self):
		from yrp.stock.save_stock_items import ungroup_items_from_ui

		po, fieldname, first_lot, other_lot = self.order_with_two_lots()
		grn = _purchase_order_grn(po, qty=4)
		grn.items[0].set(fieldname, first_lot)
		grn.append("items", {**grn.items[0].as_dict(), "name": None, fieldname: other_lot, "quantity": 6})
		grn.save()
		grn.reload()
		grn.onload()
		rows = ungroup_items_from_ui(grn.get_onload()["item_details"], grn.doctype)
		self.assertEqual({row[fieldname]: row["quantity"] for row in rows}, {first_lot: 4, other_lot: 6})
		grn.item_details = frappe.as_json(grn.get_onload()["item_details"])
		grn.save()
		grn.submit()
		po.reload()
		self.assertAlmostEqual(po.items[0].received_qty, 10)
		self.assertAlmostEqual(po.items[0].pending_quantity, 0)
		receipt_entries = self.assert_ledger_lots(grn, fieldname, {first_lot: 4, other_lot: 6})
		grn.cancel()
		po.reload()
		self.assertAlmostEqual(po.items[0].received_qty, 0)
		self.assertAlmostEqual(po.items[0].pending_quantity, 10)
		self.assert_ledger_lots(grn, fieldname, {})
		cancelled_entries = frappe.get_all(
			"YRP Stock Ledger Entry",
			filters={"name": ["in", [entry.name for entry in receipt_entries]], "is_cancelled": 1},
			fields=["name", fieldname, "qty"],
		)
		self.assertCountEqual(cancelled_entries, receipt_entries)

	def assert_ledger_lots(self, grn, fieldname, expected):
		entries = frappe.get_all(
			"YRP Stock Ledger Entry",
			filters={"voucher_type": grn.doctype, "voucher_no": grn.name, "is_cancelled": 0},
			fields=["name", fieldname, "qty"],
		)
		totals = {}
		for entry in entries:
			lot = entry[fieldname]
			totals[lot] = totals.get(lot, 0) + entry.qty
		self.assertEqual(totals, expected)
		return entries

	def test_split_lots_share_the_po_row_excess_allowance(self):
		po, fieldname, first_lot, other_lot = self.order_with_two_lots()
		grn = _purchase_order_grn(po, qty=6)
		grn.items[0].set(fieldname, first_lot)
		grn.append("items", {**grn.items[0].as_dict(), "name": None, fieldname: other_lot, "quantity": 5})
		grn.save()
		with (
			patch(
				"yrp.yrp.doctype.yrp_goods_received_note.yrp_goods_received_note._po_excess_percentage",
				return_value=0,
			),
			self.assertRaisesRegex(frappe.ValidationError, "exceeds allowance"),
		):
			grn.submit()

	def test_desk_invalid_lot_is_rejected(self):
		from yrp.stock.dimensions import get_production_group

		po, fieldname, _first_lot, _other_lot = self.order_with_two_lots()
		invalid_lot = frappe.db.get_value(
			get_production_group()["dimension_doctype"],
			{"name": ["not in", [_first_lot, _other_lot]]},
			"name",
		)
		if not invalid_lot:
			self.skipTest("A third lot is required for the invalid-lot save test.")
		grn = _purchase_order_grn(po, qty=1)
		grn.items[0].set(fieldname, invalid_lot)
		with self.assertRaises(frappe.ValidationError) as error:
			grn.save()
		self.assertIn(invalid_lot, str(error.exception))
		self.assertIn(po.name, str(error.exception))

	def test_other_po_rows_unlinked_lot_is_rejected_on_save(self):
		po, fieldname, first_lot, other_lot = self.order_with_two_lots()
		frappe.db.delete(
			po.meta.get_field("sd_lot").options, {"parent": po.name, "parenttype": "Purchase Order"}
		)
		po.append("items", {**po.items[0].as_dict(), "name": None, fieldname: other_lot}).db_insert()
		po.reload()
		grn = _purchase_order_grn(po, qty=1, dimensions={fieldname: first_lot})
		grn.items[0].set(fieldname, other_lot)
		with self.assertRaisesRegex(frappe.ValidationError, "not allowed for Purchase Order"):
			grn.save()

	def test_submit_revalidates_changed_lot_links(self):
		po, fieldname, _first_lot, other_lot = self.order_with_two_lots()
		grn = _purchase_order_grn(po, qty=1, dimensions={fieldname: other_lot})
		frappe.db.delete(
			po.meta.get_field("sd_lot").options, {"parent": po.name, "parenttype": "Purchase Order"}
		)
		with self.assertRaisesRegex(frappe.ValidationError, "not allowed for Purchase Order"):
			grn.submit()

	def test_cancel_skips_lot_revalidation_but_amend_checks_new_draft(self):
		po, fieldname, _first_lot, other_lot = self.order_with_two_lots()
		grn = _purchase_order_grn(po, qty=1, dimensions={fieldname: other_lot})
		grn.submit()
		frappe.db.delete(
			po.meta.get_field("sd_lot").options, {"parent": po.name, "parenttype": "Purchase Order"}
		)
		grn.cancel()
		self.assertEqual(grn.docstatus, 2)
		amended = frappe.copy_doc(grn)
		amended.docstatus = 0
		for row in amended.get_all_children():
			row.docstatus = 0
		amended.amended_from = grn.name
		amended.flags.ignore_permissions = True
		with self.assertRaisesRegex(frappe.ValidationError, "not allowed for Purchase Order"):
			amended.insert(ignore_permissions=True)

	def test_pending_rows_preserve_lot_and_received_type_splits(self):
		warehouse = _warehouse(f"_Test_PO_LOTS_{frappe.generate_hash(length=6)}")
		po = _purchase_order(qty=10, warehouse=warehouse)
		base = _pending_purchase_order_rows(po)[0]
		existing = [
			{**base, "lot": "LOT-1", "received_type": "Accepted", "quantity": 4},
			{**base, "lot": "Open Lot", "received_type": "Accepted", "quantity": 3},
			{**base, "lot": "Open Lot", "received_type": "Rejected", "quantity": 2},
		]
		rows = _pending_purchase_order_rows(po, existing_rows=existing)
		self.assertEqual(
			[(row["lot"], row["received_type"], row["quantity"]) for row in rows],
			[("LOT-1", "Accepted", 4), ("Open Lot", "Accepted", 3), ("Open Lot", "Rejected", 2)],
		)
		self.assertTrue(all(row["ref_docname"] == po.items[0].name for row in rows))
		existing.append({**existing[0], "quantity": 1})
		rows = _pending_purchase_order_rows(po, existing_rows=existing)
		self.assertEqual(len(rows), 3)
		self.assertEqual(rows[0]["quantity"], 5)

	def test_single_lot_defaults_and_onload_keep_quantity(self):
		from yrp.stock.dimensions import get_production_group
		from yrp.stock.save_stock_items import ungroup_items_from_ui
		from yrp.yrp.doctype.yrp_goods_received_note.yrp_goods_received_note import (
			get_purchase_order_defaults,
		)

		dimension = get_production_group()
		if not dimension:
			self.skipTest("A production-group dimension is required.")
		warehouse = _warehouse(f"_Test_PO_SINGLE_LOT_{frappe.generate_hash(length=6)}")
		po = _purchase_order(qty=10, warehouse=warehouse)
		fieldname = dimension["fieldname"]
		defaults = get_purchase_order_defaults(po.name)
		self.assertEqual(
			defaults["lot_options"],
			{po.items[0].name: [po.items[0].get(fieldname) or po.get(fieldname)]},
		)
		grn = _purchase_order_grn(po, qty=4)
		grn.onload()
		self.assertEqual(grn.get_onload()["lot_options"], defaults["lot_options"])
		rows = ungroup_items_from_ui(grn.get_onload()["item_details"], grn.doctype)
		self.assertEqual(len(rows), 1)
		self.assertEqual(rows[0]["quantity"], 4)
		self.assertEqual(rows[0][fieldname], defaults["items"][0][fieldname])
