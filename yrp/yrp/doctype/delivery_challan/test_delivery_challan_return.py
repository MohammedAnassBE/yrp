import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import flt

from yrp.stock.dimensions import get_stock_dimensions
from yrp.stock.utils import get_stock_balance
from yrp.stock.uom import resolve_item_uom
from yrp.yrp.doctype.delivery_challan.delivery_challan import (
	create_return_grn,
	get_return_delivery_items,
)
from yrp.yrp.doctype.delivery_challan.test_internal_unit_transfer import (
	_make_dc,
	_make_wo,
	_non_company_supplier,
	_row_dimensions,
	_seed_stock,
)
from yrp.yrp.doctype.goods_received_note.test_purchase_order_grn import (
	_default_received_type,
)
from yrp.yrp.doctype.work_order.test_rework_flow import _received_type


def _make_return_cycle(*, delivered=10, consumed=6, with_reservation=False):
	from_location = _non_company_supplier("_T_Return_From")
	supplier = _non_company_supplier("_T_Return_To")
	work_order, from_warehouse, to_warehouse, item_variant, uom = _make_wo(
		from_location,
		supplier,
		qty=delivered,
	)
	_seed_stock(item_variant, from_warehouse, delivered + 10)
	deliverable = work_order.deliverables[0]
	reservation = None
	if with_reservation:
		dimensions = _row_dimensions(deliverable)
		uom_details = resolve_item_uom(item_variant)
		reserved_stock_qty = delivered * flt(uom_details.conversion_factor or 1)
		reservation = frappe.get_doc(
			{
				"doctype": "Stock Reservation Entry",
				"item_code": item_variant,
				"warehouse": from_warehouse,
				"voucher_type": "Work Order",
				"voucher_no": work_order.name,
				"voucher_detail_no": deliverable.name,
				"stock_uom": uom_details.stock_uom,
				"available_qty": (delivered + 10) * flt(
					uom_details.conversion_factor or 1
				),
				"voucher_qty": reserved_stock_qty,
				"reserved_qty": reserved_stock_qty,
				"delivered_qty": 0,
				**dimensions,
			}
		)
		reservation.insert(ignore_permissions=True)
		reservation.submit()

	delivery_challan = _make_dc(
		work_order,
		from_warehouse,
		to_warehouse,
		item_variant,
		uom,
		qty=delivered,
	)
	delivery_challan.submit()
	frappe.db.set_value(
		"Work Order Deliverables",
		deliverable.name,
		"stock_update",
		consumed,
		update_modified=False,
	)
	work_order.reload()
	delivery_challan.reload()
	return (
		work_order,
		delivery_challan,
		from_warehouse,
		to_warehouse,
		item_variant,
		reservation,
	)


def _balance(item_variant, warehouse, dimensions):
	return flt(get_stock_balance(item_variant, warehouse, **dimensions))


class TestDeliveryChallanReturn(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		_default_received_type()

	def test_return_uses_only_unconsumed_quantity_and_preserves_stock_update(self):
		(
			work_order,
			delivery_challan,
			from_warehouse,
			to_warehouse,
			item_variant,
			_,
		) = _make_return_cycle(delivered=10, consumed=6)
		target_received_type = _received_type(
			f"_T_Returned_{frappe.generate_hash(length=6)}"
		)

		defaults = get_return_delivery_items(delivery_challan.name)
		self.assertEqual(len(defaults["items"]), 1)
		self.assertAlmostEqual(defaults["items"][0]["delivered_quantity"], 10)
		self.assertAlmostEqual(defaults["items"][0]["consumed_quantity"], 6)
		self.assertAlmostEqual(defaults["items"][0]["returnable_quantity"], 4)

		grn_name = create_return_grn(
			delivery_challan.name,
			[
				{
					"delivery_challan_item": delivery_challan.items[0].name,
					"return_quantity": 4,
				}
			],
			received_type=target_received_type,
		)
		return_grn = frappe.get_doc("Goods Received Note", grn_name)
		self.assertEqual(return_grn.docstatus, 0)
		self.assertEqual(return_grn.is_return, 1)
		self.assertEqual(return_grn.delivery_challan, delivery_challan.name)
		self.assertEqual(
			return_grn.get("supplier_address"),
			delivery_challan.get("supplier_address"),
		)
		self.assertEqual(
			return_grn.get("supplier_address_display"),
			delivery_challan.get("supplier_address_details"),
		)
		self.assertEqual(
			return_grn.get("delivery_address"),
			delivery_challan.get("from_address"),
		)
		self.assertEqual(
			return_grn.get("delivery_address_display"),
			delivery_challan.get("from_address_details"),
		)
		self.assertEqual(return_grn.from_warehouse, to_warehouse)
		self.assertEqual(return_grn.to_warehouse, from_warehouse)
		self.assertEqual(
			return_grn.items[0].delivery_challan_item,
			delivery_challan.items[0].name,
		)
		self.assertAlmostEqual(return_grn.items[0].max_receivable_quantity, 4)
		self.assertEqual(return_grn.items[0].ref_doctype, "Work Order Deliverables")

		# Loading the saved draft must retain return rows; ordinary GRN onload
		# rebuilds rows from Work Order Receivables and would corrupt this draft.
		return_grn.run_method("onload")
		self.assertEqual(return_grn.items[0].ref_doctype, "Work Order Deliverables")

		source_dimensions = _row_dimensions(delivery_challan.items[0])
		target_dimensions = dict(source_dimensions)
		if "received_type" in target_dimensions:
			target_dimensions["received_type"] = target_received_type
		source_before = _balance(item_variant, to_warehouse, source_dimensions)
		target_before = _balance(item_variant, from_warehouse, target_dimensions)

		return_grn.submit()
		work_order.reload()
		self.assertAlmostEqual(work_order.deliverables[0].pending_quantity, 4)
		self.assertAlmostEqual(work_order.deliverables[0].stock_update, 6)
		self.assertAlmostEqual(
			_balance(item_variant, to_warehouse, source_dimensions),
			source_before - 4,
		)
		self.assertAlmostEqual(
			_balance(item_variant, from_warehouse, target_dimensions),
			target_before + 4,
		)

		sles = frappe.get_all(
			"Stock Ledger Entry",
			filters={
				"voucher_type": "Goods Received Note",
				"voucher_no": return_grn.name,
				"is_cancelled": 0,
			},
			fields=["warehouse", "qty", "valuation_rate"],
			order_by="creation asc",
		)
		self.assertEqual([(row.warehouse, flt(row.qty)) for row in sles], [
			(to_warehouse, -4),
			(from_warehouse, 4),
		])
		self.assertGreater(flt(sles[0].valuation_rate), 0)
		self.assertAlmostEqual(flt(sles[1].valuation_rate), flt(sles[0].valuation_rate))

		return_grn.cancel()
		work_order.reload()
		self.assertAlmostEqual(work_order.deliverables[0].pending_quantity, 0)
		self.assertAlmostEqual(work_order.deliverables[0].stock_update, 6)
		self.assertAlmostEqual(
			_balance(item_variant, to_warehouse, source_dimensions),
			source_before,
		)
		self.assertAlmostEqual(
			_balance(item_variant, from_warehouse, target_dimensions),
			target_before,
		)

	def test_return_rejects_quantity_already_consumed(self):
		work_order, delivery_challan, *_rest = _make_return_cycle(
			delivered=10,
			consumed=6,
		)
		with self.assertRaises(frappe.ValidationError):
			create_return_grn(
				delivery_challan.name,
				[
					{
						"delivery_challan_item": delivery_challan.items[0].name,
						"return_quantity": 5,
					}
				],
				received_type=_default_received_type(),
			)
		work_order.reload()
		self.assertAlmostEqual(work_order.deliverables[0].pending_quantity, 0)
		self.assertAlmostEqual(work_order.deliverables[0].stock_update, 6)

	def test_return_updates_reservation_delivery_without_touching_consumption(self):
		(
			work_order,
			delivery_challan,
			*_warehouses_and_item,
			reservation,
		) = _make_return_cycle(delivered=10, consumed=6, with_reservation=True)
		conversion_factor = flt(delivery_challan.items[0].conversion_factor) or 1
		reservation.reload()
		self.assertAlmostEqual(reservation.delivered_qty, 10 * conversion_factor)

		grn_name = create_return_grn(
			delivery_challan.name,
			[
				{
					"delivery_challan_item": delivery_challan.items[0].name,
					"return_quantity": 4,
				}
			],
			received_type=_default_received_type(),
		)
		return_grn = frappe.get_doc("Goods Received Note", grn_name)
		return_grn.submit()
		reservation.reload()
		work_order.reload()
		self.assertAlmostEqual(reservation.delivered_qty, 6 * conversion_factor)
		self.assertAlmostEqual(work_order.deliverables[0].stock_update, 6)

		return_grn.cancel()
		reservation.reload()
		self.assertAlmostEqual(reservation.delivered_qty, 10 * conversion_factor)

	def test_return_keeps_every_dimension_except_selected_received_type(self):
		_, delivery_challan, *_rest = _make_return_cycle(delivered=10, consumed=6)
		target_received_type = _received_type(
			f"_T_Return_Dim_{frappe.generate_hash(length=6)}"
		)
		grn_name = create_return_grn(
			delivery_challan.name,
			[
				{
					"delivery_challan_item": delivery_challan.items[0].name,
					"return_quantity": 4,
				}
			],
			received_type=target_received_type,
		)
		return_grn = frappe.get_doc("Goods Received Note", grn_name)
		for dimension in get_stock_dimensions():
			fieldname = dimension["fieldname"]
			if fieldname == "received_type":
				self.assertEqual(return_grn.items[0].get(fieldname), target_received_type)
			else:
				self.assertEqual(
					return_grn.items[0].get(fieldname),
					delivery_challan.items[0].get(fieldname),
				)

	def test_return_cancel_rejects_quantity_already_redelivered(self):
		work_order, delivery_challan, *_rest = _make_return_cycle(
			delivered=10,
			consumed=6,
		)
		grn_name = create_return_grn(
			delivery_challan.name,
			[
				{
					"delivery_challan_item": delivery_challan.items[0].name,
					"return_quantity": 4,
				}
			],
			received_type=_default_received_type(),
		)
		return_grn = frappe.get_doc("Goods Received Note", grn_name)
		return_grn.submit()
		work_order.reload()
		work_order.deliverables[0].db_set(
			"pending_quantity",
			0,
			update_modified=False,
		)

		with self.assertRaises(frappe.ValidationError):
			return_grn.cancel()

		return_grn.reload()
		self.assertEqual(return_grn.docstatus, 1)
		work_order.reload()
		work_order.deliverables[0].db_set(
			"pending_quantity",
			4,
			update_modified=False,
		)
		return_grn.reload()
		return_grn.cancel()
