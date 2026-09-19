from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from yrp.stock.uom import apply_item_uom, resolve_item_uom
from yrp.yrp.doctype.yrp_item.yrp_item import create_variant, ensure_global_attribute_values
from yrp.yrp.doctype.yrp_work_order.yrp_work_order import WorkOrder


def _ensure_uom(name):
	if not frappe.db.exists('UOM', name):
		frappe.get_doc(
			{"doctype": 'UOM', "uom_name": name, "enabled": 1}
		).insert(ignore_permissions=True)
	return name


def _item_group():
	item_group = frappe.db.get_value('Item Group', {"is_group": 0}, "name")
	if not item_group:
		item_group = frappe.get_doc(
			{
				"doctype": 'Item Group',
				"item_group_name": f"_Test UOM Group {frappe.generate_hash(length=8)}",
				"is_group": 0,
			}
		).insert(ignore_permissions=True).name
	return item_group


def _india_compliance_item_fields():
	"""Supply a valid HSN only when India Compliance enabled that validation."""
	if (
		frappe.get_meta('Item').has_field("gst_hsn_code")
		and frappe.db.exists("DocType", "GST HSN Code")
		and frappe.db.exists("GST HSN Code", "999900")
	):
		return {"gst_hsn_code": "999900"}
	return {}


def _attribute(name, value):
	frappe.get_doc(
		{"doctype": 'Item Attribute', "attribute_name": name}
	).insert(ignore_permissions=True)
	ensure_global_attribute_values(name, [value], check_permission=False)
	return frappe.get_doc(
		{
			"doctype": 'YRP Item Item Attribute Mapping',
			"attribute_name": name,
			"values": [{"attribute_value": value}],
		}
	).insert(ignore_permissions=True).name


def _simple_item_variant(stock_uom):
	item_code = f"_Test Default UOM {frappe.generate_hash(length=8)}"
	item = frappe.get_doc(
		{
			"doctype": 'Item',
			"item_code": item_code,
			"item_name": item_code,
			"item_group": _item_group(),
			"stock_uom": stock_uom,
			"is_stock_item": 1,
			"is_sales_item": 0,
			**_india_compliance_item_fields(),
		}
	).insert(ignore_permissions=True)
	return item


def _dependent_item_variant(stock_uom, alternate_uom):
	suffix = frappe.generate_hash(length=8)
	stage = f"_Test Stage {suffix}"
	size = f"_Test Size {suffix}"
	stage_value = f"_Test Pack {suffix}"
	size_value = f"_Test S {suffix}"
	stage_mapping = _attribute(stage, stage_value)
	size_mapping = _attribute(size, size_value)

	item_code = f"_Test Dependent UOM {suffix}"
	item = frappe.get_doc(
		{
			"doctype": 'Item',
			"item_code": item_code,
			"item_name": item_code,
			"item_group": _item_group(),
			"stock_uom": stock_uom,
			"secondary_unit_of_measure": stock_uom,
			"is_stock_item": 1,
			"is_sales_item": 0,
			"has_variants": 1,
			**_india_compliance_item_fields(),
			"primary_attribute": size,
			"attributes": [
				{"attribute": stage, "mapping": stage_mapping},
				{"attribute": size, "mapping": size_mapping},
			],
			"uoms": [
				{"uom": stock_uom, "conversion_factor": 1},
				{"uom": alternate_uom, "conversion_factor": 10},
			],
		}
	).insert(ignore_permissions=True)
	# The dependent mapping links back to Item, so establish it after the Item
	# itself exists (the same two-save sequence used by the Item form).
	item.dependent_attribute = stage
	item.save(ignore_permissions=True)

	mapping = frappe.get_doc(
		'YRP Item Dependent Attribute Mapping', item.dependent_attribute_mapping
	)
	for row in mapping.details:
		if row.attribute_value == stage_value:
			row.uom = alternate_uom
	mapping.save(ignore_permissions=True)
	frappe.clear_document_cache('Item', item.name)
	frappe.clear_document_cache('YRP Item Dependent Attribute Mapping', mapping.name)

	variant = create_variant(
		item.name,
		{stage: stage_value, size: size_value},
	).insert(ignore_permissions=True)
	return item, variant


class TestMasterDerivedUOM(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.stock_uom = _ensure_uom("Piece")
		cls.alternate_uom = _ensure_uom("Box")
		cls.simple_variant = _simple_item_variant(cls.stock_uom)
		cls.dependent_item, cls.dependent_variant = _dependent_item_variant(
			cls.stock_uom, cls.alternate_uom
		)

	def test_item_without_dependent_attribute_uses_default_uom(self):
		details = resolve_item_uom(self.simple_variant.name)

		self.assertEqual(details.uom, self.stock_uom)
		self.assertEqual(details.stock_uom, self.stock_uom)
		self.assertEqual(details.conversion_factor, 1)

	def test_dependent_stage_uses_mapped_uom_and_item_conversion(self):
		details = resolve_item_uom(self.dependent_variant.name)

		self.assertEqual(details.uom, self.alternate_uom)
		self.assertEqual(details.stock_uom, self.stock_uom)
		self.assertEqual(details.conversion_factor, 10)
		self.assertEqual(details.secondary_uom, self.stock_uom)

	def test_purchase_order_overwrites_client_uom_and_preserves_dimensions(self):
		row = frappe.get_doc(
			{
				"doctype": 'Purchase Order Item',
				"item_code": self.dependent_variant.name,
				"qty": 2,
				"uom": self.stock_uom,
				"stock_uom": self.alternate_uom,
				"conversion_factor": 99,
				"rate": 50,
			}
		)
		dimension_values = {}
		for fieldname in frappe.get_meta('Purchase Order Item').fields:
			if fieldname.fieldname == "received_type":
				row.received_type = "Accepted"
				dimension_values["received_type"] = "Accepted"

		apply_item_uom(row, item_field="item_code")

		self.assertEqual(row.uom, self.alternate_uom)
		self.assertEqual(row.stock_uom, self.stock_uom)
		self.assertEqual(row.conversion_factor, 10)
		self.assertEqual(row.secondary_uom, self.stock_uom)
		for fieldname, value in dimension_values.items():
			self.assertEqual(row.get(fieldname), value)

	def test_stock_entry_controller_uses_master_uom(self):
		entry = frappe.get_doc(
			{
				"doctype": 'YRP Stock Entry',
				"purpose": "Material Receipt",
				"items": [
					{
						"item": self.dependent_variant.name,
						"qty": 3,
						"rate": 5,
						"uom": self.stock_uom,
						"stock_uom": self.alternate_uom,
						"conversion_factor": 99,
					}
				],
			}
		)

		entry.validate_items()

		row = entry.items[0]
		self.assertEqual(row.uom, self.alternate_uom)
		self.assertEqual(row.stock_uom, self.stock_uom)
		self.assertEqual(row.conversion_factor, 10)
		self.assertEqual(row.stock_qty, 30)

	def test_stock_entry_ledger_uses_stock_uom_for_stock_quantity(self):
		entry = frappe.get_doc(
			{
				"doctype": 'YRP Stock Entry',
				"name": "TEST-UOM-STOCK-ENTRY",
				"purpose": "Material Issue",
				"from_warehouse": "TEST-WAREHOUSE",
				"posting_date": frappe.utils.nowdate(),
				"posting_time": frappe.utils.nowtime(),
				"items": [
					{
						"item": self.dependent_variant.name,
						"qty": 1,
						"rate": 5,
					}
				],
			}
		)
		entry.validate_items()

		ledger = entry.get_sl_entries()[0]

		self.assertEqual(ledger["qty"], -10)
		self.assertEqual(ledger["uom"], self.stock_uom)

	def test_rework_reservation_uses_stock_uom_quantity(self):
		from yrp.stock.dimensions import get_mandatory_dimensions

		suffix = frappe.generate_hash(length=8)
		dimension_values = {}
		for dimension in get_mandatory_dimensions():
			fieldname = dimension["fieldname"]
			if fieldname == "received_type":
				value = frappe.db.get_single_value(
					'YRP Stock Settings', "default_received_type"
				)
			else:
				value = frappe.db.get_value(
					dimension["dimension_doctype"], {}, "name"
				)
			self.assertTrue(value, fieldname)
			dimension_values[fieldname] = value
		supplier = frappe.get_doc(
			{
				"doctype": 'Supplier',
				"supplier_name": f"_Test Reservation Supplier {suffix}",
			}
		).insert(ignore_permissions=True)
		warehouse = frappe.get_doc(
			{
				"doctype": 'Warehouse',
				"warehouse_name": f"_Test Reservation Warehouse {suffix}",
				"supplier": supplier.name,
			}
		).insert(ignore_permissions=True)
		receipt = frappe.get_doc(
			{
				"doctype": 'YRP Stock Entry',
				"purpose": "Material Receipt",
				"to_warehouse": warehouse.name,
				"items": [
					{
						"item": self.dependent_variant.name,
						"qty": 5,
						"rate": 10,
						**dimension_values,
					}
				],
			}
		).insert(ignore_permissions=True)
		receipt.submit()

		work_order = frappe.get_doc(
			{
				"doctype": 'YRP Work Order',
				"is_rework": 1,
				"delivery_location": supplier.name,
				"deliverables": [
					{
						"item_variant": self.dependent_variant.name,
						"qty": 2,
						"uom": self.alternate_uom,
						**dimension_values,
					}
				],
			}
		)
		work_order.name = f"_Test Rework Reservation {suffix}"
		work_order.deliverables[0].name = f"_Test Rework Deliverable {suffix}"

		WorkOrder.create_rework_reservations(work_order)

		sre = frappe.get_last_doc(
			'YRP Stock Reservation Entry',
			filters={"voucher_no": work_order.name, "docstatus": 1},
		)
		self.assertEqual(sre.stock_uom, self.stock_uom)
		self.assertAlmostEqual(sre.voucher_qty, 20)
		self.assertAlmostEqual(sre.reserved_qty, 20)

	def test_missing_master_conversion_is_the_only_uom_error(self):
		item = frappe.get_doc('Item', self.dependent_item.name)
		item.set(
			"uoms",
			[
				row.as_dict()
				for row in item.uoms
				if row.uom != self.alternate_uom
			],
		)
		original_get_cached_doc = frappe.get_cached_doc

		def get_cached_doc(doctype, name):
			if doctype == 'Item' and name == item.name:
				return item
			return original_get_cached_doc(doctype, name)

		with patch("yrp.stock.uom.frappe.get_cached_doc", side_effect=get_cached_doc):
			with self.assertRaisesRegex(
				frappe.ValidationError, "Complete UOM Conversion Details"
			):
				resolve_item_uom(self.dependent_variant.name)

	def test_transaction_uom_fields_are_read_only(self):
		fields = {
			# Native ERPNext PO rows retain their standard editable UOM/conversion
			# behavior. YRP-managed POs hide that grid and derive UOM in the matrix.
			'Purchase Order Item': ("stock_uom", "secondary_uom"),
			'YRP Goods Received Note Item': ("uom", "stock_uom", "conversion_factor", "secondary_uom"),
			'YRP Delivery Challan Item': ("uom", "stock_uom", "conversion_factor", "secondary_uom"),
			'YRP Work Order Deliverables': ("uom", "secondary_uom"),
			'YRP Work Order Receivables': ("uom", "secondary_uom"),
			'YRP Work Order Excess Usage Item': ("uom",),
			'YRP Purchase Invoice Item': ("uom",),
			'YRP Stock Entry Detail': ("uom", "stock_uom", "conversion_factor", "secondary_uom"),
			'YRP Stock Reconciliation Item': ("uom", "stock_uom", "conversion_factor", "secondary_uom"),
			'YRP Stock Update Detail': ("uom", "stock_uom", "conversion_factor", "secondary_uom"),
			'YRP Stock Reservation Entry': ("stock_uom", "secondary_uom"),
			'YRP Stock Ledger Entry': ("uom",),
		}
		for doctype, fieldnames in fields.items():
			meta = frappe.get_meta(doctype)
			for fieldname in fieldnames:
				self.assertTrue(
					meta.get_field(fieldname).read_only,
					f"{doctype}.{fieldname} must be read-only",
				)
		self.assertFalse(frappe.get_meta('Purchase Order Item').get_field("uom").read_only)
