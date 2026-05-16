import json

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, money_in_words, now_datetime, nowdate, nowtime


class PurchaseInvoice(Document):
	def onload(self):
		if self.against == "Work Order" and self.get("pi_work_order_billed_details"):
			self.set_onload(
				"item_details",
				fetch_work_order_items(self.get("pi_work_order_billed_details")),
			)
			work_orders = sorted({row.work_order for row in self.pi_work_order_billed_details if row.work_order})
			if work_orders and frappe.db.exists("DocType", "Debit"):
				self.set_onload(
					"debit_summary",
					frappe.get_all(
						"Debit",
						filters={"work_order": ["in", work_orders], "docstatus": 1},
						fields=[
							"name",
							"work_order",
							"debit_no",
							"debit_value",
							"reason",
							"inspection",
							"on_close",
							"status",
						],
						order_by="creation asc",
					),
				)

	def before_validate(self):
		self.set_missing_values()

	def validate(self):
		self.validate_grns()
		self.calculate_total()
		self.validate_total_against_grn()
		if not self.is_new():
			self.sync_grn_links()

	def after_insert(self):
		self.sync_grn_links()

	def before_submit(self):
		if not self.get("grn") or not self.get("items"):
			frappe.throw(_("Please set at least one GRN and one item row."))
		if self.against == "Work Order":
			if not self.approved_by:
				frappe.throw(_("Invoice is not approved."))
			if not _override_pi_approve():
				status = check_all_wo_closed(self.name)
				if not status["all_closed"]:
					frappe.throw(_("All Work Orders must be closed before submitting this invoice."))
			update_wo_billed_qty(self)
		self.status = "Submitted"

	def before_cancel(self):
		self.ignore_linked_doctypes = ("Goods Received Note",)
		self.unlink_grns()
		if self.against == "Work Order":
			update_wo_billed_qty(self, docstatus=2)
		self.status = "Cancelled"

	def on_cancel(self):
		self.db_set("status", "Cancelled", update_modified=False)

	def set_missing_values(self):
		if not self.posting_date:
			self.posting_date = nowdate()
		if not self.posting_time:
			self.posting_time = nowtime()
		if not self.billing_supplier and self.supplier:
			self.billing_supplier = self.supplier
		if self.is_new():
			self.status = self.status or "Draft"
			self.approved_by = None
			self.senior_merch_approved_by = None
			self.set("purchase_invoice_wo_approval_details", [])
			self.set("purchase_invoice_debit_details", [])

	def validate_grns(self):
		seen = set()
		for row in self.get("grn") or []:
			if not row.grn:
				frappe.throw(_("Row {0}: GRN is required.").format(row.idx))
			if row.grn in seen:
				frappe.throw(_("GRN {0} is duplicated.").format(row.grn))
			seen.add(row.grn)
			if not frappe.db.exists("Goods Received Note", row.grn):
				frappe.throw(_("Goods Received Note {0} does not exist.").format(row.grn))
			grn = frappe.db.get_value(
				"Goods Received Note",
				row.grn,
				["docstatus", "supplier", "against", "purchase_invoice_name"],
				as_dict=True,
			)
			if grn.docstatus != 1:
				frappe.throw(_("Goods Received Note {0} must be submitted.").format(row.grn))
			if self.supplier and grn.supplier != self.supplier:
				frappe.throw(_("Goods Received Note {0} belongs to another supplier.").format(row.grn))
			if self.against and grn.against != self.against:
				frappe.throw(_("Goods Received Note {0} is against {1}.").format(row.grn, grn.against))
			if grn.purchase_invoice_name and grn.purchase_invoice_name != self.name:
				frappe.throw(
					_("Goods Received Note {0} is already linked to Purchase Invoice {1}.").format(
						row.grn, grn.purchase_invoice_name
					)
				)
			linked_invoice = _get_linked_invoice_from_child_table(row.grn, exclude=self.name)
			if linked_invoice:
				frappe.throw(
					_("Goods Received Note {0} is already linked to Purchase Invoice {1}.").format(
						row.grn, linked_invoice
					)
				)

	def sync_grn_links(self):
		current = {row.grn for row in self.get("grn") or [] if row.grn}
		previous = set()
		before = self.get_doc_before_save()
		if before:
			previous = {row.grn for row in before.get("grn") or [] if row.grn}

		for grn in previous - current:
			if frappe.db.get_value("Goods Received Note", grn, "purchase_invoice_name") == self.name:
				frappe.db.set_value("Goods Received Note", grn, "purchase_invoice_name", None)
		for grn in current:
			frappe.db.set_value("Goods Received Note", grn, "purchase_invoice_name", self.name)

	def unlink_grns(self):
		for row in self.get("grn") or []:
			if row.grn and frappe.db.get_value("Goods Received Note", row.grn, "purchase_invoice_name") == self.name:
				frappe.db.set_value("Goods Received Note", row.grn, "purchase_invoice_name", None)

	def calculate_total(self):
		total = 0
		total_tax = 0
		total_quantity = 0
		for row in self.get("items") or []:
			row.qty = flt(row.qty)
			row.rate = flt(row.rate)
			row.amount = row.qty * row.rate
			tax_rate = _get_tax_rate(row.get("tax"))
			tax_amount = row.amount * tax_rate / 100
			total += row.amount
			total_tax += tax_amount
			total_quantity += row.qty
		self.total = total
		self.total_tax = total_tax
		self.grand_total = total + total_tax
		self.total_quantity = total_quantity
		self.in_words = money_in_words(self.grand_total) if flt(self.grand_total) else ""

	def validate_total_against_grn(self):
		if not self.get("grn"):
			return
		grn_total = sum(
			flt(frappe.db.get_value("Goods Received Note", row.grn, "total"))
			for row in self.get("grn") or []
			if row.grn
		)
		self.grn_grand_total = grn_total
		if self.against == "Purchase Order" and flt(self.grand_total) > flt(grn_total) + 0.01:
			frappe.throw(_("Total amount is greater than GRN total amount."))
		if self.against == "Work Order" and not self.allow_to_change_rate:
			if round(flt(self.grand_total), 2) != round(flt(grn_total), 2):
				frappe.throw(_("Invoice total must match GRN total amount."))


def update_wo_billed_qty(doc, docstatus=1):
	wo_docs = {}
	for row in doc.get("pi_work_order_billed_details") or []:
		if not row.work_order:
			continue
		wo = wo_docs.setdefault(row.work_order, frappe.get_doc("Work Order", row.work_order))
		for wo_item in wo.get("work_order_calculated_items") or []:
			if wo_item.item_variant != row.item_variant:
				continue
			if _normal_json(wo_item.get("set_combination")) != _normal_json(row.get("set_combination")):
				continue
			delta = flt(row.quantity)
			wo_item.billed_qty = flt(wo_item.billed_qty) - delta if docstatus == 2 else flt(wo_item.billed_qty) + delta

	for wo in wo_docs.values():
		wo.save(ignore_permissions=True)


@frappe.whitelist()
def fetch_grn_details(grns, against, supplier):
	grns = frappe.parse_json(grns) if isinstance(grns, str) else grns
	grns = list(dict.fromkeys(grns or []))
	if not grns:
		frappe.throw(_("Please select at least one GRN."))

	items = {}
	wo_items = {}
	total_quantity = 0
	for grn_name in grns:
		grn = frappe.get_doc("Goods Received Note", grn_name)
		if grn.docstatus != 1:
			frappe.throw(_("Goods Received Note {0} must be submitted.").format(grn_name))
		if supplier and grn.supplier != supplier:
			frappe.throw(_("Goods Received Note {0} belongs to another supplier.").format(grn_name))
		if against and grn.against != against:
			frappe.throw(_("Goods Received Note {0} is against {1}.").format(grn_name, grn.against))

		work_order = frappe.get_doc("Work Order", grn.against_id) if grn.against == "Work Order" else None
		for grn_item in grn.get("items") or []:
			rate = flt(grn_item.rate)
			tax = grn_item.get("tax") if grn_item.meta.get_field("tax") else None
			set_combination = _normal_json(grn_item.get("set_combination"))
			key = (
				grn_item.item_variant,
				grn_item.uom,
				rate,
				tax,
				json.dumps(set_combination, sort_keys=True),
			)
			item_group = _get_item_group(grn_item.item_variant)
			items.setdefault(
				key,
				{
					"item": grn_item.item_variant,
					"item_group": item_group,
					"qty": 0,
					"uom": grn_item.uom,
					"rate": rate,
					"amount": 0,
					"tax": tax,
					"actual_rate": rate,
					"actual_qty": 0,
					"set_combination": json.dumps(set_combination) if set_combination else None,
				},
			)
			qty = flt(grn_item.quantity)
			items[key]["qty"] += qty
			items[key]["actual_qty"] += qty
			items[key]["amount"] += qty * rate
			total_quantity += qty

			if work_order:
				wo_key = (
					work_order.name,
					grn_item.item_variant,
					json.dumps(set_combination, sort_keys=True),
				)
				wo_totals = _get_work_order_item_totals(work_order, grn_item.item_variant, set_combination)
				wo_items.setdefault(
					wo_key,
					{
						"work_order": work_order.name,
						"item_variant": grn_item.item_variant,
						"set_combination": json.dumps(set_combination) if set_combination else None,
						"quantity": 0,
						"total_delivered": wo_totals["total_delivered"],
						"total_received": wo_totals["total_received"],
						"billed": wo_totals["billed"],
					},
				)
				wo_items[wo_key]["quantity"] += qty

	item_rows = list(items.values())
	for row in item_rows:
		row["amount"] = flt(row["qty"]) * flt(row["rate"])

	grand_total = sum(
		flt(row["amount"]) + (flt(row["amount"]) * _get_tax_rate(row.get("tax")) / 100)
		for row in item_rows
	)

	return {
		"items": item_rows,
		"total": grand_total,
		"total_quantity": total_quantity,
		"wo_items": list(wo_items.values()),
		"allow_to_change_rate": 0,
	}


def fetch_work_order_items(rows):
	grouped = {}
	for row in rows or []:
		if not row.work_order:
			continue
		data = grouped.setdefault(
			row.work_order,
			{
				"work_order": row.work_order,
				"bills": _get_existing_work_order_bills(row.work_order),
				"rows": [],
				"total_delivered": 0,
				"total_received": 0,
				"total_billed": 0,
				"total_quantity": 0,
			},
		)
		data["rows"].append(row.as_dict())
		data["total_delivered"] += flt(row.total_delivered)
		data["total_received"] += flt(row.total_received)
		data["total_billed"] += flt(row.billed)
		data["total_quantity"] += flt(row.quantity)
	return list(grouped.values())


@frappe.whitelist()
def approve_invoice(name, comments=None):
	doc = frappe.get_doc("Purchase Invoice", name)
	if doc.docstatus != 0:
		frappe.throw(_("Only draft invoices can be approved."))

	role = get_merch_roles()
	if doc.against == "Work Order" and role == "merch_manager" and not _override_pi_approve():
		status = check_all_wo_closed(doc.name)
		if not status["all_closed"]:
			frappe.throw(_("Cannot approve before closing all Work Orders."))

	if role == "merch_manager":
		doc.status = "Approved"
		doc.approved_by = frappe.session.user
		if not doc.senior_merch_approved_by:
			doc.senior_merch_approved_by = frappe.session.user
	elif role == "senior_merch":
		doc.status = "Approval Pending"
		doc.senior_merch_approved_by = frappe.session.user
	else:
		doc.status = "Approval Initiated"

	doc.append(
		"purchase_invoice_wo_approval_details",
		{
			"user": frappe.session.user,
			"approved_time": now_datetime(),
			"comments": comments,
		},
	)
	doc.save(ignore_permissions=True)
	return doc.status


@frappe.whitelist()
def get_merch_roles():
	roles = set(frappe.get_roles(frappe.session.user))
	approver_role = frappe.db.get_single_value("YRP Settings", "purchase_invoice_approver_role")
	pending_role = frappe.db.get_single_value("YRP Settings", "purchase_invoice_approval_pending_role")
	initiate_role = frappe.db.get_single_value("YRP Settings", "purchase_invoice_approval_initiate_role")
	if approver_role and approver_role in roles:
		return "merch_manager"
	if pending_role and pending_role in roles:
		return "senior_merch"
	if initiate_role and initiate_role in roles:
		return "merch_user"
	return None


@frappe.whitelist()
def check_all_wo_closed(purchase_invoice):
	work_orders = frappe.get_all(
		"PI Work Order Billed Detail",
		filters={"parent": purchase_invoice, "parenttype": "Purchase Invoice"},
		fields=["distinct work_order as work_order"],
	)
	open_wos = []
	close_request_wos = []
	for row in work_orders:
		status = frappe.db.get_value("Work Order", row.work_order, "open_status")
		if status == "Close Request":
			close_request_wos.append(row.work_order)
		elif status != "Close":
			open_wos.append(row.work_order)
	return {
		"all_closed": len(open_wos) == 0 and len(close_request_wos) == 0,
		"open_work_orders": open_wos,
		"close_request_wos": close_request_wos,
	}


def _get_existing_work_order_bills(work_order):
	return frappe.db.sql(
		"""
		SELECT parent AS pi_name
		FROM `tabPI Work Order Billed Detail`
		WHERE work_order = %(work_order)s
		  AND docstatus = 1
		GROUP BY parent
		ORDER BY parent
		""",
		{"work_order": work_order},
		as_dict=True,
	)


def _get_work_order_item_totals(work_order, item_variant, set_combination):
	for row in work_order.get("work_order_calculated_items") or []:
		if row.item_variant == item_variant and _normal_json(row.get("set_combination")) == set_combination:
			return {
				"total_delivered": flt(row.delivered_quantity),
				"total_received": flt(row.received_qty),
				"billed": flt(row.billed_qty),
			}

	total_received = 0
	for row in work_order.get("receivables") or []:
		if row.item_variant == item_variant and _normal_json(row.get("set_combination")) == set_combination:
			total_received += flt(row.qty) - flt(row.pending_quantity)

	total_delivered = 0
	for row in work_order.get("deliverables") or []:
		if row.item_variant == item_variant and _normal_json(row.get("set_combination")) == set_combination:
			total_delivered += flt(row.qty) - flt(row.pending_quantity)

	return {"total_delivered": total_delivered, "total_received": total_received, "billed": 0}


def _get_item_group(item_variant):
	item = frappe.db.get_value("Item Variant", item_variant, "item")
	return frappe.db.get_value("Item", item, "item_group") if item else None


def _get_tax_rate(tax):
	if not tax:
		return 0
	return flt(frappe.db.get_value("Tax Slab", tax, "percentage") or tax)


def _normal_json(value):
	if not value:
		return {}
	return frappe.parse_json(value) if isinstance(value, str) else value


def _get_linked_invoice_from_child_table(grn, exclude=None):
	if not frappe.db.exists("DocType", "Purchase Invoice GRN"):
		return None
	for row in frappe.get_all(
		"Purchase Invoice GRN",
		filters={"grn": grn, "parenttype": "Purchase Invoice"},
		fields=["parent"],
		limit=20,
	):
		if row.parent == exclude:
			continue
		if _is_active_invoice(row.parent):
			return row.parent
	return None


def _is_active_invoice(name):
	if not name:
		return False
	docstatus = frappe.db.get_value("Purchase Invoice", name, "docstatus")
	return docstatus is not None and int(docstatus) != 2


def _override_pi_approve():
	return bool(frappe.db.get_single_value("YRP Settings", "override_pi_approve"))
