import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, getdate, nowdate, nowtime


class WorkOrder(Document):
	def save(self, *args, **kwargs):
		self.prepare_process_cost_links()
		return super().save(*args, **kwargs)

	def insert(self, *args, **kwargs):
		self.prepare_process_cost_links()
		return super().insert(*args, **kwargs)

	def onload(self):
		from yrp.stock.save_stock_items import group_items_for_ui

		self.set_onload(
			"deliverable_details",
			group_items_for_ui(self.get("deliverables") or [], "Work Order Deliverables"),
		)
		self.set_onload(
			"receivable_details",
			group_items_for_ui(self.get("receivables") or [], "Work Order Receivables"),
		)

	def before_validate(self):
		self.prepare_process_cost_links()
		self.set_default_terms()

	def set_default_terms(self):
		# Prefill Terms and Condition once, on creation, only when empty — so the
		# user can change or remove it and the removal sticks on later saves.
		if self.is_new() and not self.terms_and_condition:
			from yrp.yrp.doctype.terms_and_condition.terms_and_condition import get_default_terms

			self.terms_and_condition = get_default_terms("WO", self.supplier)

	def validate(self):
		self.set_missing_dates()
		self.set_pending_quantities()
		self.validate_rework_source_refs()
		self.set_total_quantity()
		self.set_status()

	def before_submit(self):
		if not self.get("deliverables"):
			frappe.throw("There are no deliverables on the Work Order.")
		if not self.get("receivables"):
			frappe.throw("There are no receivables on the Work Order.")
		if not self.start_date:
			self.start_date = nowdate()
		if not self.get("work_order_tracking_logs"):
			self.append("work_order_tracking_logs", {
				"from_date": self.planned_start_date,
				"to_date": self.expected_delivery_date,
				"user": frappe.session.user,
			})
		self.set_pending_quantities()
		self.set_total_quantity()
		self.set_status()

	def on_submit(self):
		self.create_rework_reservations()

	def on_update_after_submit(self):
		self.set_status()
		self.db_set("status", self.status, update_modified=False)
		self.db_set("is_delivered", self.is_delivered, update_modified=False)

	def before_cancel(self):
		self.validate_no_submitted_downstream_documents()
		# SREs against this WO are cancelled in `on_cancel`; tell Frappe's
		# linked-docs guard to skip them so the cancel isn't blocked.
		self.ignore_linked_doctypes = (
			"Delivery Challan",
			"Goods Received Note",
			"Stock Reservation Entry",
		)

	def on_cancel(self):
		from yrp.stock.utils import close_voucher_reservations

		close_voucher_reservations("Work Order", self.name)
		self.db_set("status", "Cancelled", update_modified=False)

	def validate_no_submitted_downstream_documents(self):
		delivery_challan = frappe.db.get_value(
			"Delivery Challan",
			{"work_order": self.name, "docstatus": 1},
			"name",
		)
		if delivery_challan:
			frappe.throw(
				_("Cannot cancel Work Order {0} because submitted Delivery Challan {1} exists.").format(
					self.name, delivery_challan
				)
			)

		goods_received_note = frappe.db.get_value(
			"Goods Received Note",
			{"against": "Work Order", "against_id": self.name, "docstatus": 1},
			"name",
		)
		if goods_received_note:
			frappe.throw(
				_("Cannot cancel Work Order {0} because submitted Goods Received Note {1} exists.").format(
					self.name, goods_received_note
				)
			)

	def validate_rework_source_refs(self):
		if not self.get("is_rework"):
			return

		if self.get("rework_type") and self.rework_type != "No Cost":
			frappe.throw(_("Only No Cost rework is supported in YRP core."))

		if self.get("parent_wo") and frappe.db.get_value(
			"Work Order", self.parent_wo, "is_rework"
		):
			frappe.throw(
				_("Nested rework is not allowed. Work Order {0} is already a rework Work Order.").format(
					self.parent_wo
				)
			)

		for row in self.get("deliverables") or []:
			has_grn_source = bool(row.get("source_grn_item"))
			has_inspection_source = bool(row.get("source_inspection_entry_item"))
			if has_grn_source == has_inspection_source:
				frappe.throw(
					_(
						"Row {0}: Rework deliverable must reference exactly one source: "
						"Source GRN Item or Source Inspection Entry Item."
					).format(row.idx)
				)
			if not row.get("received_type"):
				frappe.throw(
					_("Row {0}: Source Received Type is required for rework deliverables.").format(row.idx)
				)

	def create_rework_reservations(self):
		if not self.get("is_rework"):
			return

		from yrp.stock.utils import get_available_stock
		from yrp.yrp.doctype.delivery_challan.delivery_challan import _get_warehouse_for_supplier

		warehouse = _get_warehouse_for_supplier(self.delivery_location)
		if not warehouse:
			frappe.throw(_("No active Warehouse found for delivery location {0}.").format(self.delivery_location))

		for row in self.get("deliverables") or []:
			qty = flt(row.qty)
			if qty <= 0:
				continue
			existing = frappe.db.exists(
				"Stock Reservation Entry",
				{
					"voucher_type": "Work Order",
					"voucher_no": self.name,
					"voucher_detail_no": row.name,
					"docstatus": 1,
				},
			)
			if existing:
				continue

			dim_values = _stock_dimension_values(self, row)
			available = get_available_stock(
				row.item_variant,
				warehouse,
				exclude_voucher_type="Work Order",
				exclude_voucher_name=self.name,
				**dim_values,
			)
			sre = frappe.get_doc({
				"doctype": "Stock Reservation Entry",
				"item_code": row.item_variant,
				"warehouse": warehouse,
				"voucher_type": "Work Order",
				"voucher_no": self.name,
				"voucher_detail_no": row.name,
				"stock_uom": row.uom,
				"available_qty": available,
				"voucher_qty": qty,
				"reserved_qty": qty,
				"delivered_qty": 0,
				**dim_values,
			})
			sre.insert(ignore_permissions=True)
			sre.submit()

	def prepare_process_cost_links(self):
		if self.flags.get("process_cost_links_prepared"):
			return
		if self.docstatus != 0 or getattr(self, "_action", None) == "cancel":
			return

		self.sync_vue_item_details()
		self.set_missing_dates()
		self.set_pending_quantities()
		self.set_receivable_process_costs()
		self.flags.process_cost_links_prepared = True

	def set_missing_dates(self):
		if not self.wo_date:
			self.wo_date = nowdate()
		if not self.planned_start_date:
			self.planned_start_date = nowdate()
		if not self.expected_delivery_date:
			self.expected_delivery_date = self.planned_end_date or self.planned_start_date

	def set_pending_quantities(self):
		if self.docstatus != 0:
			return
		for row in self.get("deliverables") or []:
			if flt(row.qty) and not flt(row.pending_quantity):
				row.pending_quantity = row.qty
		for row in self.get("receivables") or []:
			if flt(row.qty) and not flt(row.pending_quantity):
				row.pending_quantity = row.qty

	def set_receivable_process_costs(self):
		if not self.get("receivables"):
			return
		if self.get("rework_type") == "No Cost":
			self.process_cost = None
			for row in self.receivables:
				row.process_cost = None
				row.cost = 0
				row.total_cost = 0
			return

		process_cost_name = self.get_receivable_process_cost()
		if not process_cost_name:
			if self.get("is_rework"):
				return
			frappe.throw(
				_(
					"No approved Process Cost for process {0} / supplier {1}. "
					"Create one under Process & Setup → Process Cost and get it approved."
				).format(self.process_name, self.supplier or _("(any supplier)"))
			)

		self.process_cost = process_cost_name
		process_cost = frappe.get_doc("Process Cost", process_cost_name)
		for row in self.receivables:
			rate = get_process_cost_rate(row.item_variant, row.qty, process_cost)
			row.process_cost = process_cost_name
			row.cost = round(rate, 3)
			row.total_cost = round(rate * flt(row.qty), 2)

	def get_receivable_process_cost(self):
		if not self.process_name or not self.item or not self.wo_date:
			return None

		meta = frappe.get_meta("Process Cost")
		filters = [
			["process_name", "=", self.process_name],
			["item", "=", self.item],
			["is_expired", "=", 0],
			["from_date", "<=", self.wo_date],
			["docstatus", "=", 1],
		]
		if self.supplier:
			filters.append(["supplier", "=", self.supplier])
		if meta.get_field("is_rework"):
			filters.append(["is_rework", "=", 1 if self.get("is_rework") else 0])

		from yrp.stock.dimensions import append_production_group_filters

		append_production_group_filters(filters, self, "Process Cost")

		if meta.get_field("workflow_state") and frappe.db.exists(
			"Workflow", {"document_type": "Process Cost", "is_active": 1}
		):
			filters.append(["workflow_state", "=", "Approved"])

		process_costs = frappe.get_all(
			"Process Cost",
			filters=filters,
			fields=["name", "to_date"],
			order_by="from_date desc, creation desc",
		)
		wo_date = getdate(self.wo_date)
		for process_cost in process_costs:
			if process_cost.to_date and getdate(process_cost.to_date) < wo_date:
				continue
			return process_cost.name
		return None

	def set_total_quantity(self):
		if self.open_status == "Close":
			self.total_quantity = 0
			return

		calculated_qty = sum(flt(row.quantity) for row in self.get("work_order_calculated_items") or [])
		receivable_qty = sum(flt(row.qty) for row in self.get("receivables") or [])
		deliverable_qty = sum(flt(row.qty) for row in self.get("deliverables") or [])
		self.total_quantity = calculated_qty or receivable_qty or deliverable_qty
		if not self.planned_quantity and self.total_quantity:
			self.planned_quantity = self.total_quantity

	def set_status(self):
		if self.docstatus == 0:
			self.status = "Draft"
			return
		if self.docstatus == 2:
			self.status = "Cancelled"
			return

		status = "Submitted"

		total_deliverable_qty = sum(flt(row.qty) for row in self.get("deliverables") or [])
		total_delivery_pending = sum(flt(row.pending_quantity) for row in self.get("deliverables") or [])
		if total_deliverable_qty:
			if total_delivery_pending <= 0:
				status = "Fully Delivered"
			elif total_delivery_pending < total_deliverable_qty:
				status = "Partially Delivered"

		total_receivable_qty = sum(flt(row.qty) for row in self.get("receivables") or [])
		total_received_pending = sum(flt(row.pending_quantity) for row in self.get("receivables") or [])
		if total_receivable_qty:
			received_qty = total_receivable_qty - total_received_pending
			if received_qty > 0:
				status = "Fully Received" if total_received_pending <= 0 else "Partially Received"

		total_billed_qty = sum(flt(row.billed_qty) for row in self.get("work_order_calculated_items") or [])
		if total_receivable_qty and total_billed_qty:
			status = "Fully Billed" if total_billed_qty >= total_receivable_qty else "Partially Billed"

		if self.open_status == "Close":
			status = "Closed"
			self.is_delivered = 1
		elif self.open_status == "Close Request":
			status = "Close Request"
			self.is_delivered = 1 if total_deliverable_qty and total_delivery_pending <= 0 else 0
		else:
			self.is_delivered = 1 if total_deliverable_qty and total_delivery_pending <= 0 else 0

		self.status = status

	def sync_vue_item_details(self):
		if self.docstatus != 0:
			return
		from yrp.stock.save_stock_items import ungroup_items_from_ui

		if self.get("deliverable_details"):
			rows = ungroup_items_from_ui(self.deliverable_details, "Work Order Deliverables")
			self.set("deliverables", [])
			for row in rows:
				self.append("deliverables", row)
		if self.get("receivable_details"):
			rows = ungroup_items_from_ui(self.receivable_details, "Work Order Receivables")
			self.set("receivables", [])
			for row in rows:
				self.append("receivables", row)


def get_process_cost_rate(item_variant, quantity, process_cost):
	attributes = get_variant_attributes(item_variant)
	cost_rows = sorted(
		process_cost.get("process_cost_values") or [],
		key=lambda row: flt(row.min_order_qty),
	)
	attribute_value = None
	if process_cost.depends_on_attribute:
		attribute_value = attributes.get(process_cost.attribute)

	rate = 0
	order_quantity = 0
	low_price = 0
	found = False
	for cost_row in cost_rows:
		if process_cost.depends_on_attribute and cost_row.attribute_value != attribute_value:
			continue
		min_order_qty = flt(cost_row.min_order_qty)
		if min_order_qty > flt(quantity):
			rate = flt(cost_row.price)
			found = True
			break
		if order_quantity <= min_order_qty:
			order_quantity = min_order_qty
			low_price = flt(cost_row.price)

	return round(rate if found else low_price, 3)


def get_variant_attributes(item_variant):
	rows = frappe.get_all(
		"Item Variant Attribute",
		filters={"parent": item_variant, "parenttype": "Item Variant"},
		fields=["attribute", "attribute_value"],
	)
	return {row.attribute: row.attribute_value for row in rows}


@frappe.whitelist()
def update_stock(work_order, close_reason=None, close_other_reason=None, close_remarks=None):
	from yrp.stock.stock_ledger import make_sl_entries
	from yrp.stock.utils import close_voucher_reservations, get_stock_balance
	from yrp.yrp.doctype.delivery_challan.delivery_challan import _get_warehouse_for_supplier

	doc = frappe.get_doc("Work Order", work_order)
	if doc.docstatus != 1:
		frappe.throw(_("Only submitted Work Orders can be closed."))
	if doc.open_status == "Close":
		return "Close"

	if not _is_wo_close_manager(throw_if_missing=True):
		if doc.open_status == "Close Request":
			approver_role = _get_wo_close_approver_role()
			frappe.throw(_("Only users with role {0} can approve close requests.").format(approver_role))
		_apply_close_details(doc, "Close Request", close_reason, close_other_reason, close_remarks)
		doc.save(ignore_permissions=True)
		frappe.msgprint(_("Close Request has been submitted for approval."), alert=True)
		return "Close Request"

	_validate_wo_close(doc)

	warehouse = _get_warehouse_for_supplier(doc.supplier)
	if not warehouse:
		frappe.throw(_("No active Warehouse found for supplier {0}.").format(doc.supplier))

	entries = []
	for row in doc.get("deliverables") or []:
		delivered_qty = flt(row.qty) - flt(row.pending_quantity)
		reduce_qty = delivered_qty - flt(row.stock_update)
		if reduce_qty <= 0:
			continue
		dim_values = _stock_dimension_values(doc, row)
		balance, valuation_rate = get_stock_balance(
			row.item_variant,
			warehouse,
			with_valuation_rate=True,
			**dim_values,
		)
		reduce_qty = min(reduce_qty, flt(balance))
		if reduce_qty <= 0:
			continue
		entries.append({
			"item": row.item_variant,
			"warehouse": warehouse,
			"uom": row.uom,
			"voucher_type": doc.doctype,
			"voucher_no": doc.name,
			"voucher_detail_no": row.name,
			"posting_date": nowdate(),
			"posting_time": nowtime(),
			"qty": -reduce_qty,
			"rate": 0,
			"outgoing_rate": flt(row.valuation_rate or row.rate or valuation_rate),
			"is_cancelled": 0,
			**dim_values,
		})
		row.stock_update = flt(row.stock_update) + reduce_qty

	make_sl_entries(entries)
	_apply_close_details(doc, "Close", close_reason, close_other_reason, close_remarks)
	doc.closed_by = frappe.session.user
	doc.is_delivered = 1
	doc.total_quantity = 0
	doc.save(ignore_permissions=True)
	close_voucher_reservations("Work Order", doc.name)
	return "Close"


@frappe.whitelist()
def get_debits(work_order):
	if not frappe.db.exists("DocType", "Debit"):
		return []
	return frappe.get_all(
		"Debit",
		filters={"work_order": work_order, "docstatus": 1},
		fields=[
			"name",
			"debit_type",
			"debit_no",
			"debit_value",
			"inspection",
			"status",
			"on_close",
			"reason",
		],
		order_by="creation asc",
	)


def _validate_wo_close(doc):
	grn = frappe.db.get_value(
		"Goods Received Note",
		{"against": "Work Order", "against_id": doc.name, "docstatus": 1},
		"name",
	)
	if not grn:
		frappe.throw(_("There is no submitted Goods Received Note for this Work Order."))

	unapproved_debit = frappe.db.get_value(
		"Debit",
		{"work_order": doc.name, "docstatus": 1, "status": ["!=", "Approved"]},
		"name",
	) if frappe.db.exists("DocType", "Debit") else None
	if unapproved_debit:
		frappe.throw(_("Debit {0} must be approved before closing.").format(unapproved_debit))


def _apply_close_details(doc, open_status, close_reason=None, close_other_reason=None, close_remarks=None):
	doc.open_status = open_status
	if close_reason:
		doc.close_reason = close_reason
	if close_other_reason:
		doc.close_other_reason = close_other_reason
	if close_remarks:
		doc.close_remarks = close_remarks


def _stock_dimension_values(doc, row):
	from yrp.stock.dimensions import get_dimension_fieldnames

	values = {}
	for fieldname in get_dimension_fieldnames():
		row_value = row.get(fieldname) if row.meta.get_field(fieldname) else None
		doc_value = doc.get(fieldname) if doc.meta.get_field(fieldname) else None
		values[fieldname] = row_value or doc_value
	if "received_type" in values and not values.get("received_type"):
		values["received_type"] = frappe.db.get_single_value("YRP Stock Settings", "default_received_type")
	return values


@frappe.whitelist()
def get_rework_source_rows(work_order):
	doc = frappe.get_doc("Work Order", work_order)
	if doc.docstatus != 1:
		frappe.throw(_("Work Order {0} must be submitted.").format(work_order))
	if doc.open_status == "Close":
		frappe.throw(_("Work Order {0} is closed.").format(work_order))
	if doc.get("is_rework"):
		frappe.throw(_("Nested rework is not allowed."))

	rows = []
	grns = _submitted_work_order_grns(work_order)
	grn_by_name = {row.name: row for row in grns}
	rows.extend(_direct_grn_rework_sources(grn_by_name))
	rows.extend(_inspection_rework_sources(grn_by_name))
	_enrich_variant_attributes(rows)
	rows.sort(key=lambda r: (r.get("item_variant") or "", r.get("source_label") or ""))
	return rows


def _enrich_variant_attributes(rows):
	"""Attach grouping/pivot metadata to popup rows in-place.

	For each row, set:
	  - parent_item: the Item Variant's parent Item.
	  - primary_attribute: name of the parent Item's primary_attribute (e.g. "Size").
	  - primary_attribute_value: the variant's value for that attribute (e.g. "S").
	  - non_primary_attrs: dict of other attributes (e.g. {"Colour": "Pista", ...}).

	Batched so popup latency stays bounded for WOs with many variants.
	"""
	variants = sorted({row.get("item_variant") for row in rows if row.get("item_variant")})
	if not variants:
		return
	parent_by_variant = dict(
		frappe.get_all(
			"Item Variant",
			filters={"name": ["in", variants]},
			fields=["name", "item"],
			as_list=True,
		)
	)
	primary_by_item = dict(
		frappe.get_all(
			"Item",
			filters={"name": ["in", list({v for v in parent_by_variant.values() if v})]},
			fields=["name", "primary_attribute"],
			as_list=True,
		)
	) if parent_by_variant else {}
	attr_rows = frappe.get_all(
		"Item Variant Attribute",
		filters={"parent": ["in", variants], "parenttype": "Item Variant"},
		fields=["parent", "attribute", "attribute_value"],
	) if variants else []
	attrs_by_variant = {}
	for r in attr_rows:
		attrs_by_variant.setdefault(r.parent, {})[r.attribute] = r.attribute_value

	for row in rows:
		variant = row.get("item_variant")
		parent_item = parent_by_variant.get(variant)
		primary_attr = primary_by_item.get(parent_item) if parent_item else None
		attrs = attrs_by_variant.get(variant, {})
		row["parent_item"] = parent_item
		row["primary_attribute"] = primary_attr
		row["primary_attribute_value"] = attrs.get(primary_attr) if primary_attr else None
		row["non_primary_attrs"] = {
			attr: val for attr, val in attrs.items() if attr != primary_attr
		}


@frappe.whitelist()
def create_rework_work_order(parent_wo, rows, supplier_type="Same Supplier", supplier=None):
	frappe.has_permission("Work Order", "create", throw=True)
	parent = frappe.get_doc("Work Order", parent_wo)
	if parent.docstatus != 1:
		frappe.throw(_("Parent Work Order {0} must be submitted.").format(parent_wo))
	if parent.open_status == "Close":
		frappe.throw(_("Parent Work Order {0} is closed.").format(parent_wo))
	if parent.get("is_rework"):
		frappe.throw(_("Nested rework is not allowed."))

	selected = frappe.parse_json(rows or "[]")
	if not selected:
		frappe.throw(_("Select at least one source row."))

	source_by_key = {row["source_key"]: row for row in get_rework_source_rows(parent_wo)}
	rework_rows = []
	for row in selected:
		source_key = row.get("source_key")
		source = source_by_key.get(source_key)
		if not source:
			frappe.throw(_("Selected source row is no longer available."))
		qty = flt(row.get("qty") or row.get("rework_qty"))
		if qty <= 0:
			continue
		if qty > flt(source.get("available_qty")) + 0.0001:
			frappe.throw(
				_("Selected qty {0} exceeds available qty {1} for {2}.").format(
					qty, flt(source.get("available_qty")), source.get("source_label")
				)
			)
		rework_rows.append({**source, "qty": qty})

	if not rework_rows:
		frappe.throw(_("Enter Rework Qty for at least one source row."))
	rework_rows = _consolidate_rework_rows(rework_rows)
	_validate_rework_bucket_availability(rework_rows)

	target_supplier = parent.supplier
	if supplier_type == "Different Supplier":
		target_supplier = supplier
		if not target_supplier:
			frappe.throw(_("Supplier is required for Different Supplier rework."))
	elif supplier_type != "Same Supplier":
		frappe.throw(_("Invalid Supplier Type {0}.").format(supplier_type))

	supplier_address = parent.supplier_address if target_supplier == parent.supplier else _primary_supplier_address(target_supplier)
	if not supplier_address:
		frappe.throw(_("No primary address found for supplier {0}.").format(target_supplier))

	wo = frappe.new_doc("Work Order")
	wo.is_rework = 1
	wo.parent_wo = parent.name
	wo.supplier_type = supplier_type
	wo.rework_type = "No Cost"
	wo.supplier = target_supplier
	wo.delivery_location = parent.delivery_location
	wo.supplier_address = supplier_address
	wo.delivery_address = parent.delivery_address
	wo.process_name = parent.process_name
	wo.item = parent.item
	wo.production_detail = parent.production_detail
	wo.planned_start_date = nowdate()
	wo.planned_end_date = parent.planned_end_date or nowdate()
	wo.expected_delivery_date = parent.expected_delivery_date or wo.planned_end_date
	_copy_rework_header_dimensions(wo, parent, rework_rows)

	for idx, row in enumerate(rework_rows):
		wo.append("deliverables", _rework_deliverable_row(row, idx))
	for row in _rework_receivable_rows(rework_rows):
		wo.append("receivables", row)

	wo.insert(ignore_permissions=True)
	return wo.name


@frappe.whitelist()
def get_close_permission():
	approver_role = frappe.db.get_single_value("YRP Settings", "work_order_closing_approver_role")
	return {
		"approver_role": approver_role,
		"is_close_manager": bool(approver_role and approver_role in frappe.get_roles(frappe.session.user)),
	}


def _get_wo_close_approver_role():
	approver_role = frappe.db.get_single_value("YRP Settings", "work_order_closing_approver_role")
	if not approver_role:
		frappe.throw(_("Please configure Work Order Closing Approver Role in YRP Settings."))
	return approver_role


def _submitted_work_order_grns(work_order):
	filters = {
		"against": "Work Order",
		"against_id": work_order,
		"docstatus": 1,
	}
	if frappe.get_meta("Goods Received Note").get_field("is_rework"):
		filters["is_rework"] = 0
	return frappe.get_all(
		"Goods Received Note",
		filters=filters,
		fields=["name", "to_warehouse", "posting_date"],
		order_by="posting_date asc, creation asc",
	)


def _direct_grn_rework_sources(grn_by_name):
	if not grn_by_name:
		return []

	from yrp.stock.dimensions import get_dimension_fieldnames

	dim_fields = get_dimension_fieldnames()
	fields = [
		"name",
		"parent",
		"item_variant",
		"quantity",
		"uom",
		"table_index",
		"row_index",
		"set_combination",
	] + dim_fields
	rows = frappe.get_all(
		"Goods Received Note Item",
		filters={
			"parent": ["in", list(grn_by_name)],
			"parenttype": "Goods Received Note",
		},
		fields=fields,
		order_by="parent asc, idx asc",
	)

	default_rt, rejected_rt = _eligible_rt_context()
	out = []
	for row in rows:
		rt = row.get("received_type")
		if not _is_rework_eligible_rt(rt, default_rt, rejected_rt):
			continue
		grn = grn_by_name.get(row.parent)
		warehouse = grn.to_warehouse if grn else None
		if not row.item_variant or not warehouse:
			continue
		available = (
			flt(row.quantity)
			- flt(_inspection_outflow_from_grn_row(row.name))
			- flt(_prior_rework_consumed(source_grn_item=row.name))
		)
		if available <= 0:
			continue
		dim_values = _row_dimension_values(row, "Goods Received Note Item")
		out.append({
			"source_key": f"grn::{row.name}",
			"source_type": "Goods Received Note Item",
			"source_label": f"{row.parent} / {row.name}",
			"source_grn": row.parent,
			"source_grn_item": row.name,
			"source_inspection_entry_item": "",
			"item_variant": row.item_variant,
			"uom": row.uom or _item_uom(row.item_variant),
			"warehouse": warehouse,
			"received_type": dim_values.get("received_type") or rt,
			"role": _rework_role_label(rt),
			"table_index": row.table_index,
			"row_index": row.row_index,
			"set_combination": row.set_combination,
			"dimensions": dim_values,
			"available_qty": flt(available),
			"qty": 0,
			**dim_values,
		})
	return out


def _inspection_rework_sources(grn_by_name):
	if not grn_by_name:
		return []

	entries = frappe.get_all(
		"Inspection Entry",
		filters={
			"against": "Goods Received Note",
			"against_id": ["in", list(grn_by_name)],
			"docstatus": 1,
		},
		fields=["name", "against_id", "status", "is_converted"],
		order_by="posting_date asc, creation asc",
	)
	entries = [row for row in entries if row.is_converted or row.status == "Converted"]
	if not entries:
		return []

	from yrp.stock.dimensions import get_dimension_fieldnames

	dim_fields = get_dimension_fieldnames()
	entry_by_name = {row.name: row for row in entries}
	fields = [
		"name",
		"parent",
		"item_variant",
		"warehouse",
		"qty",
		"target_received_type",
		"ref_doctype",
		"ref_docname",
	] + dim_fields
	rows = frappe.get_all(
		"Inspection Entry Item",
		filters={
			"parent": ["in", list(entry_by_name)],
			"parenttype": "Inspection Entry",
		},
		fields=fields,
		order_by="parent asc, idx asc",
	)

	default_rt, rejected_rt = _eligible_rt_context()
	out = []
	for row in rows:
		target_rt = row.target_received_type
		if not _is_rework_eligible_rt(target_rt, default_rt, rejected_rt):
			continue
		# Identity conversions (source RT == target RT) emit no SLEs and create
		# no new physical stock — they must not appear as rework sources.
		source_rt = row.get("received_type") or default_rt
		if source_rt == target_rt:
			continue
		if not row.item_variant or not row.warehouse:
			continue
		available = flt(row.qty) - flt(
			_prior_rework_consumed(source_inspection_entry_item=row.name)
		)
		if available <= 0:
			continue
		entry = entry_by_name.get(row.parent)
		dim_values = _row_dimension_values(
			row,
			"Inspection Entry Item",
			override_received_type=target_rt,
		)
		# Inherit table/row_index + set_combination from the source GRN row
		# (when ref_doctype is GRN Item) so the rework deliverable groups
		# alongside GRN-sourced deliverables in the pivot UI.
		src_table_index = None
		src_row_index = f"insp::{row.name}"
		src_set_combination = None
		if row.ref_doctype == "Goods Received Note Item" and row.ref_docname:
			src = frappe.db.get_value(
				"Goods Received Note Item",
				row.ref_docname,
				["table_index", "row_index", "set_combination"],
				as_dict=True,
			)
			if src:
				src_table_index = src.table_index
				if src.row_index not in (None, ""):
					src_row_index = f"{src.row_index}::{target_rt or ''}"
				src_set_combination = src.set_combination
		out.append({
			"source_key": f"inspection::{row.name}",
			"source_type": "Inspection Entry Item",
			"source_label": f"{row.parent} / {row.name}",
			"source_grn": entry.against_id if entry else "",
			"source_grn_item": "",
			"source_inspection_entry_item": row.name,
			"item_variant": row.item_variant,
			"uom": _item_uom(row.item_variant),
			"warehouse": row.warehouse,
			"received_type": target_rt,
			"role": _rework_role_label(target_rt),
			"table_index": src_table_index,
			"row_index": src_row_index,
			"set_combination": src_set_combination,
			"dimensions": dim_values,
			"available_qty": flt(available),
			"qty": 0,
			**dim_values,
		})
	return out


def _eligible_rt_context():
	settings = frappe.get_cached_doc("YRP Stock Settings")
	return settings.get("default_received_type"), settings.get("default_rejected_received_type")


def _is_rework_eligible_rt(received_type, default_rt, rejected_rt):
	if not received_type:
		return False
	if received_type == default_rt:
		return False
	if rejected_rt and received_type == rejected_rt:
		return False
	return True


def _rework_role_label(received_type):
	return "Rework"


def _inspection_outflow_from_grn_row(grn_item_name):
	"""Sum qty that left this GRN row's bucket via converted Inspection Entries.

	Each converted inspection row whose source RT differs from its target RT
	emits an SLE that debits the source bucket. For a GRN row to remain a
	valid rework source, its remaining qty must reflect that outflow.
	Identity rows (source == target) and non-converted inspections emit no
	SLE and are excluded.
	"""
	if not grn_item_name:
		return 0
	row = frappe.db.sql(
		"""
		SELECT COALESCE(SUM(i.qty), 0) AS qty
		FROM `tabInspection Entry Item` i
		JOIN `tabInspection Entry` p ON p.name = i.parent
		WHERE i.ref_doctype = 'Goods Received Note Item'
		  AND i.ref_docname = %s
		  AND p.docstatus = 1
		  AND (p.is_converted = 1 OR p.status = 'Converted')
		  AND i.target_received_type IS NOT NULL
		  AND i.received_type IS NOT NULL
		  AND i.target_received_type != i.received_type
		""",
		(grn_item_name,),
	)
	return flt(row[0][0]) if row else 0


def _prior_rework_consumed(source_grn_item=None, source_inspection_entry_item=None):
	"""Sum qty in non-cancelled, non-closed rework Work Order deliverables that
	cite this source row. Draft (docstatus=0) consumption is included so in-flight
	rework WOs reduce the popup's available_qty.
	"""
	if not (source_grn_item or source_inspection_entry_item):
		return 0
	conds = [
		"d.parenttype = 'Work Order'",
		"wo.is_rework = 1",
		"wo.docstatus IN (0, 1)",
		"(wo.open_status IS NULL OR wo.open_status != 'Close')",
	]
	values = []
	if source_grn_item:
		conds.append("d.source_grn_item = %s")
		values.append(source_grn_item)
	if source_inspection_entry_item:
		conds.append("d.source_inspection_entry_item = %s")
		values.append(source_inspection_entry_item)
	where_sql = " AND ".join(conds)
	row = frappe.db.sql(
		f"""
		SELECT COALESCE(SUM(d.qty), 0) AS qty
		FROM `tabWork Order Deliverables` d
		JOIN `tabWork Order` wo ON wo.name = d.parent
		WHERE {where_sql}
		"""
		,
		values,
	)
	return flt(row[0][0]) if row else 0


def _row_dimension_values(row, child_doctype, override_received_type=None):
	from yrp.stock.dimensions import get_dimension_fieldnames

	meta = frappe.get_meta(child_doctype)
	values = {}
	for fn in get_dimension_fieldnames():
		if not meta.get_field(fn):
			continue
		value = row.get(fn)
		if fn == "received_type" and override_received_type is not None:
			value = override_received_type
		if fn == "received_type" and not value:
			value = frappe.db.get_single_value("YRP Stock Settings", "default_received_type")
		if value is not None:
			values[fn] = value
	return values


def _consolidate_rework_rows(rows):
	"""Merge popup-selected source rows that share the same physical bucket
	(variant, lot, set_combination, received_type) into a single deliverable row.

	Two source rows can land in the same bucket — e.g., one GRN-direct Adas
	and one inspection-converted Adas for the same variant. They are fungible
	in the warehouse, so the WO Deliverable / DC / GRN chain should carry ONE
	row per bucket. Without this merge, the GRN pivot UI silently drops one
	entry when two rows hit the same cell.

	Source ref handling: prefer the first GRN-sourced row's `source_grn_item`
	(authoritative audit anchor). If all contributors are inspection-sourced,
	use the first `source_inspection_entry_item`. The `validate_rework_source_refs`
	check still passes (exactly one of the two refs is set).
	"""
	if not rows:
		return rows
	merged = {}
	order = []
	for row in rows:
		dims = row.get("dimensions") or {}
		key = (
			row.get("item_variant"),
			dims.get("lot") or row.get("lot"),
			_json_key(row.get("set_combination")),
			row.get("received_type"),
		)
		if key not in merged:
			merged[key] = {**row}
			order.append(key)
			continue
		merged[key]["qty"] = flt(merged[key]["qty"]) + flt(row.get("qty"))
		if not merged[key].get("source_grn_item") and row.get("source_grn_item"):
			merged[key]["source_grn_item"] = row.get("source_grn_item")
			merged[key]["source_grn"] = row.get("source_grn") or merged[key].get("source_grn")
			merged[key]["source_inspection_entry_item"] = ""
		elif (
			not merged[key].get("source_grn_item")
			and not merged[key].get("source_inspection_entry_item")
			and row.get("source_inspection_entry_item")
		):
			merged[key]["source_inspection_entry_item"] = row.get("source_inspection_entry_item")
	return [merged[k] for k in order]


def _validate_rework_bucket_availability(rows):
	from yrp.stock.dimensions import get_dimension_fieldnames
	from yrp.stock.utils import get_available_stock

	dim_fields = get_dimension_fieldnames()
	buckets = {}
	for row in rows:
		dims = row.get("dimensions") or {}
		key = (
			row.get("item_variant"),
			row.get("warehouse"),
			tuple(dims.get(fn) for fn in dim_fields),
		)
		buckets.setdefault(key, {"row": row, "qty": 0})
		buckets[key]["qty"] += flt(row.get("qty"))

	for bucket in buckets.values():
		row = bucket["row"]
		dims = row.get("dimensions") or {}
		available = get_available_stock(row["item_variant"], row["warehouse"], **dims)
		if flt(bucket["qty"]) > flt(available) + 0.0001:
			frappe.throw(
				_("Selected rework qty {0} exceeds available stock {1} for {2} at {3}.").format(
					flt(bucket["qty"]),
					flt(available),
					row["item_variant"],
					row["warehouse"],
				)
			)


def _copy_rework_header_dimensions(target, parent, rows):
	from yrp.stock.dimensions import get_stock_dimensions

	for dim in get_stock_dimensions():
		fn = dim["fieldname"]
		if not target.meta.get_field(fn):
			continue
		if not dim.get("is_production_group"):
			continue
		values = {
			row.get("dimensions", {}).get(fn)
			for row in rows
			if row.get("dimensions", {}).get(fn)
		}
		if len(values) > 1:
			frappe.throw(
				_("Create separate Rework Work Orders for different {0} values.").format(
					dim.get("label") or fn
				)
			)
		target.set(fn, next(iter(values), None) or parent.get(fn))


def _rework_deliverable_row(source, idx):
	row = {
		"item_variant": source["item_variant"],
		"qty": flt(source["qty"]),
		"uom": source["uom"],
		"table_index": source.get("table_index") if source.get("table_index") is not None else 0,
		"row_index": source.get("row_index") if source.get("row_index") is not None else idx,
		"set_combination": source.get("set_combination"),
		"received_type": source.get("received_type"),
		"source_grn": source.get("source_grn"),
		"source_grn_item": source.get("source_grn_item"),
		"source_inspection_entry_item": source.get("source_inspection_entry_item"),
	}
	_apply_child_dimension_values(row, "Work Order Deliverables", source.get("dimensions") or {})
	return row


def _rework_receivable_rows(rows):
	grouped = {}
	for idx, row in enumerate(rows):
		key = (
			row.get("item_variant"),
			row.get("uom"),
			_json_key(row.get("set_combination")),
		)
		if key not in grouped:
			grouped[key] = {
				"item_variant": row["item_variant"],
				"qty": 0,
				"uom": row["uom"],
				"cost": 0,
				"total_cost": 0,
				"table_index": row.get("table_index") if row.get("table_index") is not None else 0,
				"row_index": _strip_receivable_rt_suffix(row.get("row_index"), idx),
				"set_combination": row.get("set_combination"),
			}
			_apply_child_dimension_values(
				grouped[key],
				"Work Order Receivables",
				row.get("dimensions") or {},
				exclude={"received_type"},
			)
		grouped[key]["qty"] += flt(row["qty"])
	return list(grouped.values())


def _strip_receivable_rt_suffix(row_index, fallback_idx):
	"""Rework Receivables consolidate by (parent Item × non-primary attrs) regardless
	of source Received Type — the returned stock is always classified at the
	default RT (or whatever the supplier reports). Strip the `::<RT>` suffix from
	the source row_index so all source-RT variants share one Receivable row in
	the pivot UI.
	"""
	if row_index is None:
		return fallback_idx
	base = str(row_index).split("::", 1)[0]
	return base or fallback_idx


def _apply_child_dimension_values(row, child_doctype, values, exclude=None):
	exclude = exclude or set()
	meta = frappe.get_meta(child_doctype)
	for fn, value in (values or {}).items():
		if fn in exclude:
			continue
		if meta.get_field(fn) and value is not None:
			row[fn] = value


def _primary_supplier_address(supplier):
	if not supplier:
		return None
	try:
		from yrp.yrp.doctype.supplier.supplier import get_primary_address

		return get_primary_address(supplier)
	except Exception:
		return None


def _item_uom(item_variant):
	parent_item = frappe.get_cached_value("Item Variant", item_variant, "item")
	if not parent_item:
		return None
	return frappe.get_cached_value("Item", parent_item, "default_unit_of_measure")


def _json_key(value):
	if not value:
		return ""
	import json

	parsed = frappe.parse_json(value) if isinstance(value, str) else value
	return json.dumps(parsed, sort_keys=True)


def _is_wo_close_manager(throw_if_missing=False):
	if throw_if_missing:
		approver_role = _get_wo_close_approver_role()
	else:
		approver_role = frappe.db.get_single_value("YRP Settings", "work_order_closing_approver_role")
		if not approver_role:
			return False
	return approver_role in frappe.get_roles(frappe.session.user)
