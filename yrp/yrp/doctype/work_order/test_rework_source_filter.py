import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import nowdate, nowtime

from yrp.yrp.doctype.goods_received_note.test_purchase_order_grn import (
	_default_received_type,
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


class TestReworkSourceFilter(FrappeTestCase):
	"""The rework popup must show only non-default, non-rejected RT rows,
	with qty scoped to this WO's GRN/inspection chain (not warehouse pool).
	"""

	def test_default_received_type_excluded(self):
		accepted = _default_received_type()
		wo, supplier_wh, delivery_wh, item_variant, uom = _make_parent_work_order(qty=10)
		_make_parent_grn(wo, supplier_wh, delivery_wh, item_variant, uom, accepted, qty=10)

		rows = get_rework_source_rows(wo.name)

		self.assertEqual(rows, [])

	def test_rejected_received_type_excluded(self):
		rejected = _received_type(f"_T_Filter_Rejected_{frappe.generate_hash(length=6)}")
		_set_rejected_received_type(rejected)
		wo, supplier_wh, delivery_wh, item_variant, uom = _make_parent_work_order(qty=10)
		_make_parent_grn(wo, supplier_wh, delivery_wh, item_variant, uom, rejected, qty=10)

		rows = get_rework_source_rows(wo.name)

		self.assertEqual(rows, [])

	def test_grn_row_qty_used_not_warehouse_pool(self):
		"""available_qty must equal the GRN row's quantity even when extra
		matching stock has been deposited at the same warehouse by another WO.
		"""
		oil_mark = _received_type(f"_T_Filter_Oil_{frappe.generate_hash(length=6)}")
		wo, supplier_wh, delivery_wh, item_variant, uom = _make_parent_work_order(qty=10)
		_make_parent_grn(wo, supplier_wh, delivery_wh, item_variant, uom, oil_mark, qty=10)

		seed_wo, seed_supplier_wh, _, _, _ = _make_parent_work_order(qty=25)
		seed_grn = frappe.get_doc({
			"doctype": "Goods Received Note",
			"against": "Work Order",
			"against_id": seed_wo.name,
			"posting_date": nowdate(),
			"posting_time": nowtime(),
			"supplier": seed_wo.supplier,
			"delivery_location": seed_wo.delivery_location,
			"from_warehouse": seed_supplier_wh,
			"to_warehouse": delivery_wh,
			"process_name": seed_wo.process_name,
			"item": seed_wo.item,
			"items": [{
				"item_variant": item_variant,
				"quantity": 25,
				"uom": uom,
				"stock_uom": uom,
				"conversion_factor": 1,
				"received_type": oil_mark,
				"ref_doctype": "Work Order Receivables",
				"ref_docname": seed_wo.receivables[0].name,
				"table_index": 0,
				"row_index": "0",
			}],
		})
		seed_grn.insert(ignore_permissions=True)
		seed_grn.submit()

		rows = get_rework_source_rows(wo.name)

		self.assertEqual(len(rows), 1)
		self.assertEqual(rows[0]["received_type"], oil_mark)
		self.assertEqual(rows[0]["available_qty"], 10)

	def test_inspection_emits_rework_source_for_non_default_target(self):
		"""An Inspection Entry converting Accepted → Oil Mark must surface
		one inspection-anchored source row with qty = inspection qty. The
		original GRN row (at default RT) must NOT be shown.
		"""
		accepted = _default_received_type()
		oil_mark = _received_type(f"_T_Filter_OilInsp_{frappe.generate_hash(length=6)}")
		wo, supplier_wh, delivery_wh, item_variant, uom = _make_parent_work_order(qty=10)
		grn = _make_parent_grn(wo, supplier_wh, delivery_wh, item_variant, uom, accepted, qty=10)
		grn_row = grn.items[0]

		insp = frappe.get_doc({
			"doctype": "Inspection Entry",
			"against": "Goods Received Note",
			"against_id": grn.name,
			"posting_date": nowdate(),
			"posting_time": nowtime(),
			"status": "Converted",
			"is_converted": 1,
			"items": [{
				"item_variant": item_variant,
				"warehouse": delivery_wh,
				"grn_qty": 10,
				"target_received_type": oil_mark,
				"qty": 4,
				"received_date": nowdate(),
				"ref_doctype": "Goods Received Note Item",
				"ref_docname": grn_row.name,
				"received_type": accepted,
				"lot": grn_row.lot,
			}],
		})
		insp.insert(ignore_permissions=True)
		insp.submit()

		rows = get_rework_source_rows(wo.name)

		self.assertEqual(len(rows), 1)
		self.assertEqual(rows[0]["source_key"].startswith("inspection::"), True)
		self.assertEqual(rows[0]["received_type"], oil_mark)
		self.assertEqual(rows[0]["available_qty"], 4)

	def test_inspection_outflow_reduces_grn_row_available(self):
		"""When an Inspection Entry converts qty out of a GRN row's bucket
		(e.g. Oil Mark -> Accepted), that GRN row's rework-available qty
		must drop accordingly.
		"""
		accepted = _default_received_type()
		oil_mark = _received_type(f"_T_Filter_OilFlow_{frappe.generate_hash(length=6)}")
		wo, supplier_wh, delivery_wh, item_variant, uom = _make_parent_work_order(qty=10)
		grn = _make_parent_grn(wo, supplier_wh, delivery_wh, item_variant, uom, oil_mark, qty=10)
		grn_row = grn.items[0]

		insp = frappe.get_doc({
			"doctype": "Inspection Entry",
			"against": "Goods Received Note",
			"against_id": grn.name,
			"posting_date": nowdate(),
			"posting_time": nowtime(),
			"status": "Converted",
			"is_converted": 1,
			"items": [{
				"item_variant": item_variant,
				"warehouse": delivery_wh,
				"grn_qty": 10,
				"target_received_type": accepted,
				"qty": 4,
				"received_date": nowdate(),
				"ref_doctype": "Goods Received Note Item",
				"ref_docname": grn_row.name,
				"received_type": oil_mark,
				"lot": grn_row.lot,
			}],
		})
		insp.insert(ignore_permissions=True)
		insp.submit()

		rows = get_rework_source_rows(wo.name)

		grn_rows = [r for r in rows if r["source_key"].startswith("grn::")]
		self.assertEqual(len(grn_rows), 1)
		self.assertEqual(grn_rows[0]["available_qty"], 6)

	def test_identity_conversion_inspection_excluded(self):
		"""An Inspection Entry row whose source RT equals target RT emits no
		SLE and must not surface as a rework source.
		"""
		accepted = _default_received_type()
		oil_mark = _received_type(f"_T_Filter_OilIdentity_{frappe.generate_hash(length=6)}")
		wo, supplier_wh, delivery_wh, item_variant, uom = _make_parent_work_order(qty=10)
		grn = _make_parent_grn(wo, supplier_wh, delivery_wh, item_variant, uom, oil_mark, qty=10)
		grn_row = grn.items[0]

		insp = frappe.get_doc({
			"doctype": "Inspection Entry",
			"against": "Goods Received Note",
			"against_id": grn.name,
			"posting_date": nowdate(),
			"posting_time": nowtime(),
			"status": "Converted",
			"is_converted": 1,
			"items": [{
				"item_variant": item_variant,
				"warehouse": delivery_wh,
				"grn_qty": 10,
				"target_received_type": oil_mark,
				"qty": 3,
				"received_date": nowdate(),
				"ref_doctype": "Goods Received Note Item",
				"ref_docname": grn_row.name,
				"received_type": oil_mark,
				"lot": grn_row.lot,
			}],
		})
		insp.insert(ignore_permissions=True)
		insp.submit()

		rows = get_rework_source_rows(wo.name)

		inspection_rows = [r for r in rows if r["source_key"].startswith("inspection::")]
		self.assertEqual(inspection_rows, [])
		grn_rows = [r for r in rows if r["source_key"].startswith("grn::")]
		self.assertEqual(len(grn_rows), 1)
		self.assertEqual(grn_rows[0]["available_qty"], 10)

	def test_prior_rework_reduces_available_qty(self):
		"""A draft or submitted rework WO that has already consumed qty from
		the source row must reduce the popup's available_qty.
		"""
		oil_mark = _received_type(f"_T_Filter_OilPrior_{frappe.generate_hash(length=6)}")
		wo, supplier_wh, delivery_wh, item_variant, uom = _make_parent_work_order(qty=10)
		_make_parent_grn(wo, supplier_wh, delivery_wh, item_variant, uom, oil_mark, qty=10)

		first_pass = get_rework_source_rows(wo.name)
		self.assertEqual(len(first_pass), 1)
		self.assertEqual(first_pass[0]["available_qty"], 10)

		create_rework_work_order(
			wo.name,
			frappe.as_json([{"source_key": first_pass[0]["source_key"], "qty": 4}]),
		)

		rows = get_rework_source_rows(wo.name)

		self.assertEqual(len(rows), 1)
		self.assertEqual(rows[0]["available_qty"], 6)
