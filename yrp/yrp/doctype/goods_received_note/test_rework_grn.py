import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import flt, nowdate, nowtime

from yrp.stock.utils import get_stock_balance
from yrp.yrp.doctype.goods_received_note.goods_received_note import (
	get_work_order_defaults,
)
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
from yrp.yrp.doctype.work_order.test_rework_flow import (
	_make_parent_grn,
	_make_parent_work_order,
	_received_type,
	_set_rejected_received_type,
)
from yrp.yrp.doctype.work_order.work_order import (
	create_rework_work_order,
	get_rework_source_rows,
)


def _make_rework_cycle(parent_process_rate):
	"""Run a parent WO → parent GRN → rework WO → DC → rework GRN cycle.

	Returns (rework_wo, dc, grn, supplier_wh, delivery_wh, accepted_rt,
	rejected_rt, rework_rt, item_variant) so tests can assert on intermediate
	state.
	"""
	accepted_rt = _default_received_type()
	rework_rt = _received_type(f"_T_Rework_GRN_Source_{frappe.generate_hash(length=6)}")
	rejected_rt = _received_type(f"_T_Rework_GRN_Rejected_{frappe.generate_hash(length=6)}")
	_set_rejected_received_type(rejected_rt)

	wo, supplier_wh, delivery_wh, item_variant, uom = _make_parent_work_order(qty=10)
	# Override the auto-resolved process cost with a known rate so we can
	# assert exact valuation downstream.
	parent_item = frappe.db.get_value("Item Variant", item_variant, "item")
	dimensions = _production_group_dimensions()
	_process_cost(
		wo.process_name,
		parent_item,
		wo.supplier,
		dimensions,
		rate=parent_process_rate,
	)
	# Re-run cost resolution on the parent WO so its receivable.cost reflects
	# the freshly-inserted Process Cost rate.
	wo.reload()
	wo.set_receivable_process_costs()
	wo.save()

	_make_parent_grn(wo, supplier_wh, delivery_wh, item_variant, uom, rework_rt, qty=10)

	sources = get_rework_source_rows(wo.name)
	source = next(row for row in sources if row["received_type"] == rework_rt)
	rework_wo_name = create_rework_work_order(
		wo.name,
		[{"source_key": source["source_key"], "qty": 6}],
	)
	rework_wo = frappe.get_doc("Work Order", rework_wo_name)
	rework_wo.submit()

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

	defaults = get_work_order_defaults(rework_wo.name, dc.name)
	self_dc_item = dc.items[0].name

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
				"delivery_challan_item": self_dc_item,
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
				"delivery_challan_item": self_dc_item,
				"ref_doctype": "Work Order Receivables",
				"ref_docname": rework_wo.receivables[0].name,
				"table_index": 0,
				"row_index": "0::rejected",
			},
		],
	})
	grn.insert(ignore_permissions=True)
	grn.submit()
	return (
		rework_wo, dc, grn, supplier_wh, delivery_wh,
		accepted_rt, rejected_rt, rework_rt, item_variant, defaults,
	)


class TestReworkGRN(FrappeTestCase):
	def test_rework_wo_is_auto_no_cost(self):
		"""create_rework_work_order must stamp rework_type='No Cost' so the
		downstream GRN does not add any process cost on top of material."""
		(
			rework_wo, _, _, _, _, _, _, _, _, _,
		) = _make_rework_cycle(parent_process_rate=20)
		self.assertEqual(rework_wo.rework_type, "No Cost")
		# No Cost branch in set_receivable_process_costs zeroes every
		# receivable's cost — that is what the GRN rate calc reads as
		# process_rate.
		self.assertEqual(flt(rework_wo.receivables[0].cost), 0)

	def test_no_cost_rework_grn_preserves_material_rate(self):
		"""When rework_type='No Cost', the GRN receipt must come back in at the
		DC's material valuation_rate — NOT at zero, NOT at material+process.

		Rule: 'No Cost' means no added process cost; the original stock
		valuation flows through unchanged. Forcing rate=0 would silently wipe
		out valuation on every No-Cost rework receipt and corrupt the ledger.
		"""
		parent_process_rate = 20
		(
			_, dc, grn, _, delivery_wh,
			accepted_rt, rejected_rt, _, item_variant, _,
		) = _make_rework_cycle(parent_process_rate=parent_process_rate)

		dc_material_rate = flt(dc.items[0].valuation_rate or dc.items[0].rate)
		self.assertGreater(
			dc_material_rate, 0,
			"DC must carry a non-zero material valuation_rate for this test to be meaningful.",
		)

		for row in grn.items:
			self.assertAlmostEqual(
				flt(row.rate), dc_material_rate,
				msg=f"GRN row received_type={row.received_type}: rate {row.rate} should equal DC material {dc_material_rate} (No Cost rework — no process cost added).",
			)

		_, accepted_valuation = get_stock_balance(
			item_variant, delivery_wh,
			received_type=accepted_rt,
			with_valuation_rate=True,
		)
		_, rejected_valuation = get_stock_balance(
			item_variant, delivery_wh,
			received_type=rejected_rt,
			with_valuation_rate=True,
		)
		self.assertAlmostEqual(flt(accepted_valuation), dc_material_rate)
		self.assertAlmostEqual(flt(rejected_valuation), dc_material_rate)

	def test_rework_grn_splits_into_multiple_received_types(self):
		"""A single rework GRN can split the rework WO's receivable across
		multiple Received Types (Accepted + Rejected) — qty at the source
		warehouse drains, qty at the destination splits per RT."""
		(
			_, _, grn, supplier_wh, delivery_wh,
			accepted_rt, rejected_rt, rework_rt, item_variant, _,
		) = _make_rework_cycle(parent_process_rate=20)

		# Source bin (supplier warehouse, rework RT) fully drained — DC moved
		# 6 out, GRN receives 4+2=6 at destination split across Accepted /
		# Rejected.
		self.assertAlmostEqual(
			flt(get_stock_balance(item_variant, supplier_wh, received_type=rework_rt)),
			0,
		)
		self.assertAlmostEqual(
			flt(get_stock_balance(item_variant, delivery_wh, received_type=accepted_rt)),
			4,
		)
		self.assertAlmostEqual(
			flt(get_stock_balance(item_variant, delivery_wh, received_type=rejected_rt)),
			2,
		)
		# All GRN rows must reference the same DC item — that link is what
		# the rate/material flow-through depends on.
		dc_item_refs = {row.delivery_challan_item for row in grn.items}
		self.assertEqual(len(dc_item_refs), 1)
