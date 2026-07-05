import frappe
from frappe.utils import flt


def execute():
	affected_work_orders = set()
	for dc in frappe.get_all(
		"Delivery Challan",
		filters={"work_order": ["is", "set"]},
		fields=["name", "work_order"],
	):
		wo = frappe.get_doc("Work Order", dc.work_order)
		for item in frappe.get_all(
			"Delivery Challan Item",
			filters={"parent": dc.name},
			fields=[
				"name",
				"item_variant",
				"set_combination",
				"ref_doctype",
				"ref_docname",
			],
		):
			target = _find_matching_deliverable(wo, item)
			if not target:
				continue

			values = {}
			if item.ref_doctype != "Work Order Deliverables":
				values["ref_doctype"] = "Work Order Deliverables"
			if item.ref_docname != target.name:
				values["ref_docname"] = target.name
			if values:
				frappe.db.set_value(
					"Delivery Challan Item",
					item.name,
					values,
					update_modified=False,
				)
				affected_work_orders.add(dc.work_order)

	for work_order in affected_work_orders:
		_recompute_work_order_deliverable_pending(work_order)


def _recompute_work_order_deliverable_pending(work_order):
	wo = frappe.get_doc("Work Order", work_order)
	delivered = {row.name: 0 for row in wo.get("deliverables") or []}

	for dc_name in frappe.get_all(
		"Delivery Challan",
		filters={"work_order": work_order, "docstatus": 1},
		pluck="name",
	):
		for item in frappe.get_all(
			"Delivery Challan Item",
			filters={"parent": dc_name},
			fields=["item_variant", "set_combination", "delivered_quantity", "qty"],
		):
			target = _find_matching_deliverable(wo, item)
			if not target:
				continue
			delivered[target.name] = delivered.get(target.name, 0) + flt(
				item.delivered_quantity or item.qty
			)

	for row in wo.get("deliverables") or []:
		row.pending_quantity = flt(row.qty) - flt(delivered.get(row.name))
		frappe.db.set_value(
			row.doctype,
			row.name,
			"pending_quantity",
			row.pending_quantity,
			update_modified=False,
		)

	wo.set_status()
	frappe.db.set_value(
		"Work Order",
		work_order,
		{"status": wo.status, "is_delivered": wo.is_delivered},
		update_modified=False,
	)


def _find_matching_deliverable(wo, source_row):
	for row in wo.get("deliverables") or []:
		if row.item_variant != source_row.item_variant:
			continue
		if _normal_json(row.get("set_combination")) == _normal_json(
			source_row.get("set_combination")
		):
			return row
	return None


def _normal_json(value):
	if not value:
		return {}
	return frappe.parse_json(value) if isinstance(value, str) else value
