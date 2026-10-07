# Copyright (c) 2026, Mohammed Anas and contributors
# For license information, please see license.txt

import math
from collections import defaultdict

import frappe
from frappe import _
from frappe.contacts.doctype.address.address import get_address_display
from frappe.model.document import Document
from frappe.utils import cstr, flt, get_datetime, now_datetime

from yrp.stock.dimensions import apply_dimension_defaults, get_dimension_fieldnames, get_mandatory_dimensions
from yrp.stock.utils import close_voucher_reservations, get_available_stock
from yrp.yrp_retail.logic import require, validate_uom_quantity
from yrp.yrp_retail.pricing import check_rate, get_free_items
from yrp.yrp_sales.doctype.yrp_sales_order.yrp_sales_order import get_conversion_factor, get_price_list_rate
from yrp.yrp_sales.invoicing import (
	get_delivery_note_rows,
	get_invoice_status,
	get_invoiced_qty,
	lock_delivery_note,
)

RESERVATION = "YRP Stock Reservation Entry"
PACKING_SLIP = "YRP Packing Slip"
INACTIVE_RESERVATION_STATUSES = ["Delivered", "Closed", "Cancelled"]
PACKING_INITIATED = "Packing Initiated"
PACKING_COMPLETED = "Packing Completed"
# Header values a caller may set when creating a note; custom fields are also accepted.
HEADER_FIELDS = ("posting_date", "posting_time", "shipping_address_name", "transporter", "remarks", "amended_from")


class YRPDeliveryNote(Document):
	"""Dispatch against submitted YRP Sales Orders; reserves YRP stock while draft."""

	def _save(self, *args, **kwargs):
		# Sales Orders are locked before Frappe locks this document.
		lock_sales_orders({row.sales_order for row in self.items if row.sales_order})
		return super()._save(*args, **kwargs)

	def validate(self):
		require(self.items, "A Delivery Note requires items.")
		apply_dimension_defaults(self.items)
		sources = self.get_source_rows()
		for row in self.items:
			if row.so_detail:
				self.set_row_values(row, sources[row.so_detail])
			else:
				self.set_additional_row_values(row)
		self.validate_allocation(sources)
		self.validate_rates()
		self.total_qty = sum(flt(row.qty) for row in self.items)
		self.total = sum(flt(row.amount) for row in self.items)
		self.status = self.get_status()
		self.set_shipping_address()

	def before_update_after_submit(self):
		self.set_shipping_address()

	def set_shipping_address(self):
		self.shipping_address = get_address_display(self.shipping_address_name) if self.shipping_address_name else None

	def get_source_rows(self):
		"""Lock linked Sales Orders and return their item rows by name."""
		orders = lock_sales_orders({row.sales_order for row in self.items if row.sales_order})
		for order in orders.values():
			require(order.docstatus == 1, _("Sales Order {0} must be submitted.").format(order.name))
			require(order.customer == self.customer, _("Sales Order {0} belongs to another Customer.").format(order.name))
			require(order.company == self.company, _("Sales Order {0} belongs to another Company.").format(order.name))
		details = sorted({row.so_detail for row in self.items if row.so_detail})
		sources = {row.name: row for row in get_sales_order_rows(details)}
		for row in self.items:
			if not row.so_detail:
				require(not row.sales_order, _("Row {0}: Select the Sales Order Item of Sales Order {1}.").format(
					row.idx, row.sales_order))
				continue
			source = sources.get(row.so_detail)
			require(source and source.parent == row.sales_order,
				_("Row {0}: Sales Order Item does not belong to Sales Order {1}.").format(row.idx, row.sales_order))
		return sources

	def set_row_values(self, row, source):
		label = _("Row {0}").format(row.idx)
		require(row.item_code == source.item_code, _("{0}: Item must match its Sales Order row.").format(label))
		require(row.uom == source.uom, _("{0}: UOM must match its Sales Order row.").format(label))
		require(not row.conversion_factor or flt(row.conversion_factor) == flt(source.conversion_factor),
			_("{0}: Conversion Factor must match its Sales Order row.").format(label))
		row.item_name = source.item_name
		row.conversion_factor = flt(source.conversion_factor)
		row.stock_uom = source.stock_uom
		self.set_quantities(row)

	def set_additional_row_values(self, row):
		"""A row with no Sales Order: an enabled sales Item priced from this note's Price List."""
		item = frappe.get_cached_doc("Item", row.item_code)
		require(not item.disabled and not item.has_variants and item.is_sales_item,
			_("Row {0}: Items must be enabled, concrete sales Items.").format(row.idx))
		row.item_name = item.item_name
		row.stock_uom = item.stock_uom
		row.conversion_factor = get_conversion_factor(item, row.uom)
		if not row.rate and self.selling_price_list:
			row.rate = get_price_list_rate(self.selling_price_list, row.item_code, row.uom)
		self.set_quantities(row)

	def set_quantities(self, row):
		label = _("Row {0}").format(row.idx)
		qty = validate_uom_quantity(row.qty, row.uom, _("{0} Quantity").format(label), row.precision("qty"))
		require(qty > 0, _("{0}: Quantity must be greater than zero.").format(label))
		row.qty = qty
		row.stock_qty = qty * row.conversion_factor
		row.amount = qty * flt(row.rate)
		row.warehouse = row.warehouse or self.set_warehouse
		require(row.warehouse, _("{0}: Warehouse is required.").format(label))
		require(frappe.get_cached_value("Warehouse", row.warehouse, "company") == self.company,
			_("{0}: Warehouse {1} belongs to another Company.").format(label, row.warehouse))

	def validate_allocation(self, sources):
		"""Keep this and other non-cancelled Delivery Notes within each Sales Order row."""
		requested = defaultdict(float)
		for row in self.items:
			if row.so_detail:
				requested[row.so_detail] += flt(row.stock_qty)
		if not requested:
			return
		others = get_allocated_qty(list(requested), exclude=self.name)
		precision = self.items[0].precision("stock_qty")
		for detail, qty in requested.items():
			source = sources[detail]
			allocated = flt(qty + others.get(detail, 0), precision)
			require(allocated <= flt(source.stock_qty, precision), _(
				"Item {0} of Sales Order {1}: {2} would be allocated to Delivery Notes, but only {3} was ordered."
			).format(source.item_code, source.parent, allocated, flt(source.stock_qty, precision)))

	def validate_rates(self):
		free = get_free_items(row.item_code for row in self.items)
		for row in self.items:
			check_rate(row.rate, row.item_code in free, _("Row {0}: {1}").format(row.idx, row.item_code))

	def get_status(self):
		if self.docstatus == 0:
			return "Draft"
		if self.docstatus == 2:
			return "Cancelled"
		if flt(self.per_delivered) >= 100:
			return "Delivered"
		if flt(self.per_delivered) > 0:
			return "Partially Delivered"
		if flt(self.per_billed) >= 100:
			return "Invoiced"
		return "Submitted"

	def on_update(self):
		if self.docstatus == 0:
			self.sync_reservations()

	def on_submit(self):
		self.validate_reservations()
		self.db_set("status", self.get_status(), update_modified=False)

	def before_cancel(self):
		invoices = frappe.get_all("Sales Invoice", filters={"yrp_delivery_note": self.name, "docstatus": ["<", 2]},
			pluck="name", order_by="name")
		require(not invoices, _("Cancel Sales Invoices {0} before cancelling this Delivery Note.").format(
			", ".join(invoices)))
		require(not flt(self.per_delivered), _("A delivered Delivery Note cannot be cancelled."))
		slips = frappe.get_all(PACKING_SLIP, filters={"delivery_note": self.name, "docstatus": 1},
			pluck="name", order_by="name")
		require(not slips, _("Cancel Packing Slips {0} before cancelling this Delivery Note.").format(", ".join(slips)))

	def on_cancel(self):
		close_voucher_reservations(self.doctype, self.name)
		self.db_set("status", "Cancelled", update_modified=False)

	def on_trash(self):
		close_voucher_reservations(self.doctype, self.name)

	def update_billing(self):
		"""Recompute billed quantities from submitted Sales Invoices and the invoice status from all live ones."""
		billed = get_invoiced_qty(self.name, submitted_only=True)
		for row in self.items:
			row.db_set("billed_qty", flt(billed.get(row.name), row.precision("billed_qty")), update_modified=False)
		self.per_billed = get_percent(self.items, "billed_qty", self.precision("per_billed"))
		self.db_set({"per_billed": self.per_billed, "status": self.get_status(),
			"invoice_status": get_invoice_status(self.name)}, update_modified=False)

	def initiate_packing(self):
		"""Start packing a submitted note that is not invoiced or delivered; callers check permissions."""
		header = lock_delivery_note(self.name)
		require(header.docstatus == 1, _("Delivery Note {0} must be submitted.").format(self.name))
		require(not header.delivered_at, _("Delivery Note {0} is already delivered.").format(self.name))
		require(not header.packing_status, _("Packing has already started on Delivery Note {0}.").format(self.name))
		require(not get_invoice_status(self.name), _("Delivery Note {0} is already invoiced.").format(self.name))
		self.db_set({"packing_status": PACKING_INITIATED, "packing_initiated_at": now_datetime()})

	def complete_packing(self, completed_by):
		"""Finish packing that was initiated, recording who packed; callers check permissions."""
		require(cstr(completed_by).strip(), _("Packing completed by is required."))
		header = lock_delivery_note(self.name)
		require(header.docstatus == 1 and header.packing_status == PACKING_INITIATED,
			_("Packing has not been initiated on Delivery Note {0}.").format(self.name))
		self.db_set({"packing_status": PACKING_COMPLETED, "packing_completed_at": now_datetime(),
			"packing_completed_by": cstr(completed_by).strip()})

	def cancel_with_reason(self, reason):
		"""Cancel the note and its undelivered Packing Slips, keeping the reason; callers check permissions."""
		require(cstr(reason).strip(), _("Cancellation reason is required."))
		self.cancel_packing_slips()
		self.cancel_reason = cstr(reason).strip()
		self.flags.ignore_permissions = True
		self.cancel()

	def cancel_packing_slips(self):
		for name in frappe.get_all(PACKING_SLIP, filters={"delivery_note": self.name, "docstatus": 1},
				pluck="name", order_by="name"):
			slip = frappe.get_doc(PACKING_SLIP, name)
			slip.flags.ignore_permissions = True
			slip.cancel()

	def update_rates(self, rates):
		"""Re-price rows of a submitted note that has no live Sales Invoice; callers check permissions."""
		header = lock_delivery_note(self.name)
		require(header.docstatus == 1, _("Delivery Note {0} must be submitted.").format(self.name))
		require(not get_invoice_status(self.name),
			_("Rates of Delivery Note {0} cannot change after it is invoiced.").format(self.name))
		rows = {row.name: row for row in self.items}
		unknown = set(rates) - set(rows)
		require(not unknown, _("Rows {0} do not belong to Delivery Note {1}.").format(", ".join(sorted(unknown)), self.name))
		free = get_free_items(row.item_code for row in self.items)
		for name, rate in rates.items():
			row = rows[name]
			check_rate(rate, row.item_code in free, _("Row {0}: {1}").format(row.idx, row.item_code))
			row.rate = flt(rate, row.precision("rate"))
			row.amount = flt(row.rate * flt(row.qty), row.precision("amount"))
			row.db_set({"rate": row.rate, "amount": row.amount}, update_modified=False)
		self.db_set("total", sum(flt(row.amount) for row in self.items))

	def update_packing(self):
		"""Recompute packed quantities from submitted Packing Slips."""
		packed = get_packed_qty(self.name, submitted_only=True)
		for row in self.items:
			row.db_set("packed_qty", flt(packed.get(row.name), row.precision("packed_qty")), update_modified=False)
		self.db_set("per_packed", get_percent(self.items, "packed_qty", self.precision("per_packed")),
			update_modified=False)

	def update_delivery(self):
		"""Recompute delivered quantities, then the linked Sales Orders."""
		delivered = self.get_delivered_qty()
		for row in self.items:
			row.db_set("delivered_qty", flt(delivered.get(row.name), row.precision("delivered_qty")),
				update_modified=False)
		self.per_delivered = get_percent(self.items, "delivered_qty", self.precision("per_delivered"))
		values = {"per_delivered": self.per_delivered, "status": self.get_status()}
		if self.per_delivered >= 100 and not self.delivered_at:
			values["delivered_at"] = frappe.db.sql(
				"select max(delivered_at) from `tabYRP Packing Slip` where delivery_note = %s and docstatus = 1",
				self.name)[0][0]
		self.db_set(values, update_modified=False)
		for order in sorted({row.sales_order for row in self.items if row.sales_order}):
			frappe.get_doc("YRP Sales Order", order).update_delivered_qty()

	def get_delivered_qty(self):
		"""Delivered slips decide; a note without submitted slips is delivered whole by its own delivered_at."""
		slips = dict(frappe.db.sql(
			"""select psi.dn_detail, sum(if(ps.delivered_at is null, 0, psi.qty)) from `tabYRP Packing Slip Item` psi
			join `tabYRP Packing Slip` ps on ps.name = psi.parent
			where ps.delivery_note = %s and ps.docstatus = 1 group by psi.dn_detail for update""",
			self.name,
		))
		if slips or not self.delivered_at:
			return slips
		return {row.name: row.qty for row in self.items}

	def lock_for_delivery(self):
		"""Lock Sales Orders, then this note; return its current rows."""
		lock_sales_orders({row.sales_order for row in self.items})
		header = lock_delivery_note(self.name)
		require(header.docstatus == 1, _("Delivery Note {0} must be submitted.").format(self.name))
		self.delivered_at = header.delivered_at
		return get_delivery_note_rows(self.name)

	def validate_billed_qty(self, rows, delivered):
		"""Delivered quantity per row may not exceed its invoiced quantity."""
		precision = frappe.get_precision("YRP Delivery Note Item", "qty")
		for detail, qty in delivered.items():
			row = rows[detail]
			require(flt(qty, precision) <= flt(row.billed_qty, precision), _(
				"Row {0} of Delivery Note {1}: {2} would be delivered, but only {3} is invoiced."
			).format(row.idx, self.name, flt(qty, precision), flt(row.billed_qty, precision)))

	@frappe.whitelist()
	def mark_delivered(self, delivered_at=None, remarks=None):
		self.check_permission("write")
		self.deliver(delivered_at, remarks)

	def deliver(self, delivered_at=None, remarks=None):
		"""Deliver every row: through all Packing Slips when there are any, otherwise this note itself.

		`delivered_at` defaults to now; callers check permissions."""
		rows = self.lock_for_delivery()
		require(not self.delivered_at, _("Delivery Note {0} is already delivered.").format(self.name))
		slips = frappe.db.sql(
			"""select name, docstatus, delivered_at from `tabYRP Packing Slip`
			where delivery_note = %s and docstatus < 2 order by name for update""", self.name, as_dict=True)
		drafts = [slip.name for slip in slips if slip.docstatus == 0]
		require(not drafts, _("Submit or delete draft Packing Slips {0} first.").format(", ".join(drafts)))
		if slips:
			self.validate_packing_coverage(rows)
		self.validate_billed_qty(rows, {row.name: row.qty for row in rows.values()})
		delivered_at = get_datetime(delivered_at) if delivered_at else now_datetime()
		if cstr(remarks).strip():
			self.db_set("delivery_remarks", cstr(remarks).strip(), update_modified=False)
		for slip in slips:
			if not slip.delivered_at:
				frappe.get_doc(PACKING_SLIP, slip.name).set_delivered(delivered_at)
		if not slips:
			self.db_set("delivered_at", delivered_at, update_modified=False)
		self.update_delivery()

	def validate_packing_coverage(self, rows):
		packed = get_packed_qty(self.name, submitted_only=True)
		precision = frappe.get_precision("YRP Delivery Note Item", "qty")
		for row in rows.values():
			require(flt(packed.get(row.name), precision) == flt(row.qty, precision), _(
				"Row {0}: Packing Slips cover {1} of {2}. Pack every row before marking the Delivery Note delivered."
			).format(row.idx, flt(packed.get(row.name), precision), flt(row.qty, precision)))

	def sync_reservations(self):
		"""Keep one submitted reservation per row, matching its item, warehouse, dimensions and stock quantity."""
		rows = {row.name: row for row in self.items}
		kept = set()
		for reservation in self.get_active_reservations():
			row = rows.get(reservation.voucher_detail_no)
			if row and row.name not in kept and self.is_reservation_matching(reservation, row):
				kept.add(row.name)
				continue
			cancel_reservation(reservation.name)
		for row in self.items:
			if row.name not in kept:
				self.make_reservation(row)

	def validate_reservations(self):
		reservations = defaultdict(list)
		for reservation in self.get_active_reservations():
			reservations[reservation.voucher_detail_no].append(reservation)
		for row in self.items:
			matches = reservations.get(row.name, [])
			require(len(matches) == 1 and self.is_reservation_matching(matches[0], row),
				_("Row {0}: {1} has no matching stock reservation. Save the draft to reserve stock.").format(
					row.idx, row.item_code))

	def get_active_reservations(self):
		return frappe.get_all(RESERVATION, filters={
			"voucher_type": self.doctype,
			"voucher_no": self.name,
			"docstatus": 1,
			"status": ["not in", INACTIVE_RESERVATION_STATUSES],
		}, fields=["name", "voucher_detail_no", "item_code", "warehouse", "reserved_qty",
			"delivered_qty", "closed_qty", *get_dimension_fieldnames()], order_by="creation")

	def is_reservation_matching(self, reservation, row):
		precision = row.precision("stock_qty")
		open_qty = flt(reservation.reserved_qty) - flt(reservation.delivered_qty) - flt(reservation.closed_qty)
		return (
			reservation.item_code == row.item_code
			and reservation.warehouse == row.warehouse
			and all(reservation.get(fieldname) == row.get(fieldname) for fieldname in get_dimension_fieldnames())
			and flt(reservation.reserved_qty, precision) == flt(row.stock_qty, precision)
			and flt(open_qty, precision) == flt(row.stock_qty, precision)
		)

	def make_reservation(self, row):
		dimensions = {fieldname: row.get(fieldname) for fieldname in get_dimension_fieldnames()}
		reservation = frappe.get_doc({
			"doctype": RESERVATION,
			"item_code": row.item_code,
			"warehouse": row.warehouse,
			"voucher_type": self.doctype,
			"voucher_no": self.name,
			"voucher_detail_no": row.name,
			"stock_uom": row.stock_uom,
			"available_qty": get_available_stock(row.item_code, row.warehouse,
				exclude_voucher_type=self.doctype, exclude_voucher_name=self.name, **dimensions),
			"voucher_qty": row.stock_qty,
			"reserved_qty": row.stock_qty,
			"delivered_qty": 0,
			**dimensions,
		})
		reservation.insert(ignore_permissions=True)
		reservation.submit()


def get_percent(rows, fieldname, precision):
	"""Share of row quantity covered by ``fieldname``, capped per row."""
	total = sum(flt(row.qty) for row in rows)
	covered = sum(min(flt(row.get(fieldname)), flt(row.qty)) for row in rows)
	return flt(covered / total * 100, precision) if total else 0


def get_packed_qty(delivery_note, exclude=None, submitted_only=False):
	"""Quantity per Delivery Note row on non-cancelled (or only submitted) Packing Slips, read under lock."""
	return dict(frappe.db.sql(
		"""select psi.dn_detail, sum(psi.qty) from `tabYRP Packing Slip Item` psi
		join `tabYRP Packing Slip` ps on ps.name = psi.parent
		where ps.delivery_note = %(note)s and ps.docstatus in %(docstatuses)s and ps.name != %(exclude)s
		group by psi.dn_detail for update""",
		{"note": delivery_note, "docstatuses": (1,) if submitted_only else (0, 1), "exclude": exclude or ""},
	))


def cancel_reservation(name):
	reservation = frappe.get_doc(RESERVATION, name)
	reservation.flags.ignore_permissions = True
	reservation.cancel()


def lock_sales_orders(names):
	"""Lock Sales Orders in name order and return their current header values."""
	names = sorted(name for name in names if name)
	if not names:
		return {}
	rows = frappe.db.sql(
		"""select name, docstatus, customer, company from `tabYRP Sales Order`
		where name in %(names)s order by name for update""",
		{"names": names}, as_dict=True,
	)
	found = {row.name: row for row in rows}
	missing = [name for name in names if name not in found]
	require(not missing, _("Sales Order {0} does not exist.").format(", ".join(missing)))
	return found


def get_sales_order_rows(names):
	if not names:
		return []
	return frappe.db.sql(
		"""select name, parent, item_code, item_name, uom, conversion_factor, stock_qty, stock_uom,
			rate, warehouse
		from `tabYRP Sales Order Item` where name in %(names)s and parenttype = 'YRP Sales Order'
		order by name for update""",
		{"names": names}, as_dict=True,
	)


def get_allocated_qty(so_details, exclude=None):
	"""Stock quantity on non-cancelled Delivery Notes per Sales Order row, read under lock."""
	if not so_details:
		return {}
	return dict(frappe.db.sql(
		"""select dni.so_detail, sum(dni.stock_qty) from `tabYRP Delivery Note Item` dni
		join `tabYRP Delivery Note` dn on dn.name = dni.parent
		where dni.so_detail in %(details)s and dn.docstatus < 2 and dn.name != %(exclude)s
		group by dni.so_detail for update""",
		{"details": list(so_details), "exclude": exclude or ""},
	))


def get_pending_rows(customer=None, sales_orders=None, item_codes=None):
	"""Submitted Sales Order rows with stock quantity not yet on a non-cancelled Delivery Note."""
	conditions = ["so.docstatus = 1"]
	values = {}
	for column, key, value in (("so.customer", "customer", customer), ("so.name", "orders", sales_orders),
			("soi.item_code", "items", item_codes)):
		if value is None:
			continue
		conditions.append(f"{column} {'=' if key == 'customer' else 'in'} %({key})s")
		values[key] = value if key == "customer" else tuple(value) or ("",)
	rows = frappe.db.sql(
		f"""select so.name as sales_order, soi.name as so_detail, soi.idx, soi.item_code, soi.uom,
			soi.conversion_factor, soi.qty, soi.stock_qty,
			(select ifnull(sum(dni.stock_qty), 0) from `tabYRP Delivery Note Item` dni
				join `tabYRP Delivery Note` dn on dn.name = dni.parent
				where dni.so_detail = soi.name and dn.docstatus < 2) as allocated
		from `tabYRP Sales Order Item` soi join `tabYRP Sales Order` so on so.name = soi.parent
		where soi.parenttype = 'YRP Sales Order' and {" and ".join(conditions)}
		order by so.transaction_date, so.name, soi.idx""",
		values, as_dict=True,
	)
	precision = frappe.get_precision("YRP Sales Order Item", "stock_qty")
	pending_rows = []
	for row in rows:
		row.pending = flt(flt(row.stock_qty) - flt(row.allocated), precision)
		if row.pending > 0:
			row.pending_qty = row.pending / flt(row.conversion_factor)
			pending_rows.append(row)
	return pending_rows


def get_delivery_note(customer, sales_orders=None, items=None, warehouse=None, selling_price_list=None, company=None):
	"""Unsaved Delivery Note from submitted Sales Orders; callers check permissions.

	`items` lists `{so_detail, qty}` rows to deliver (qty in the order row's UOM) and
	`{item_code, uom, qty}` rows with no Sales Order; leave it out for every pending row.
	`warehouse` overrides every row warehouse and `selling_price_list` re-prices every row.
	"""
	orders = get_source_orders(customer, sales_orders)
	companies = {order.company for order in orders} | ({company} if company else set())
	require(companies, "Select at least one Sales Order.")
	require(len(companies) == 1, "Sales Orders must belong to one Company.")
	warehouses = {order.set_warehouse for order in orders}
	note = frappe.new_doc("YRP Delivery Note")
	note.update({
		"customer": customer,
		"company": companies.pop(),
		"set_warehouse": warehouse or (warehouses.pop() if len(warehouses) == 1 else None),
		**get_pricing(orders, selling_price_list),
	})
	rows = frappe.parse_json(items) if isinstance(items, str) else items
	if rows is None:
		append_pending_rows(note, orders)
	else:
		append_selected_rows(note, orders, rows)
	require(note.items, "Nothing is pending delivery on the selected Sales Orders.")
	for row in note.items:
		if warehouse:
			row.warehouse = warehouse
		if selling_price_list and row.so_detail:
			row.rate = get_price_list_rate(selling_price_list, row.item_code, row.uom)
	apply_dimension_defaults(note.items)
	return note


@frappe.whitelist()
def make_delivery_note(customer, sales_orders, items=None, warehouse=None, selling_price_list=None):
	"""Return an unsaved Delivery Note for the pending quantity of submitted Sales Orders."""
	frappe.has_permission("YRP Delivery Note", "create", throw=True)
	for name in sorted(set(frappe.parse_json(sales_orders) if isinstance(sales_orders, str) else sales_orders)):
		frappe.get_doc("YRP Sales Order", name).check_permission("read")
	return get_delivery_note(customer, sales_orders, items, warehouse, selling_price_list)


def create_delivery_note(customer, sales_orders=None, items=None, warehouse=None, header=None,
		selling_price_list=None, company=None, submit=True):
	"""Build, reserve stock and optionally submit in one savepoint; any failure leaves nothing behind."""
	savepoint = "create_delivery_note_" + frappe.generate_hash(length=10)
	frappe.db.savepoint(savepoint)
	try:
		note = get_delivery_note(customer, sales_orders, items, warehouse, selling_price_list, company)
		note.update(get_input_values(note.meta, header or {}, HEADER_FIELDS))
		allocate_dimensions(note)
		note.flags.ignore_permissions = True
		note.insert()
		if submit:
			note.submit()
	except Exception:
		frappe.db.rollback(save_point=savepoint)
		raise
	return note


def amend_delivery_note(name, customer, sales_orders=None, items=None, warehouse=None, header=None,
		selling_price_list=None, company=None):
	"""Cancel a submitted note and its Packing Slips, then create and submit its amendment, in one savepoint.

	The amendment gets the same rows as `create_delivery_note` would; any failure leaves the original.
	Callers check permissions."""
	savepoint = "amend_delivery_note_" + frappe.generate_hash(length=10)
	frappe.db.savepoint(savepoint)
	try:
		original = frappe.get_doc("YRP Delivery Note", name)
		require(original.docstatus == 1, _("Delivery Note {0} must be submitted.").format(name))
		require(original.customer == customer, _("Delivery Note {0} belongs to another Customer.").format(name))
		original.cancel_packing_slips()
		original.flags.ignore_permissions = True
		original.cancel()
		note = create_delivery_note(customer, sales_orders, items, warehouse, {**(header or {}), "amended_from": name},
			selling_price_list, company)
	except Exception:
		frappe.db.rollback(save_point=savepoint)
		raise
	return note


def get_source_orders(customer, sales_orders):
	names = sorted(set(frappe.parse_json(sales_orders) if isinstance(sales_orders, str) else sales_orders or []))
	orders = [frappe.get_doc("YRP Sales Order", name) for name in names]
	for order in orders:
		require(order.docstatus == 1, _("Sales Order {0} must be submitted.").format(order.name))
		require(order.customer == customer, _("Sales Order {0} belongs to another Customer.").format(order.name))
	return orders


def get_pricing(orders, selling_price_list):
	if selling_price_list:
		return {"selling_price_list": selling_price_list,
			"currency": frappe.db.get_value("Price List", selling_price_list, "currency")}
	pricing = {(order.selling_price_list, order.currency) for order in orders}
	require(len(pricing) <= 1, "Sales Orders must share one Price List.")
	price_list, currency = pricing.pop() if pricing else (None, None)
	return {"selling_price_list": price_list, "currency": currency}


def get_input_values(meta, values, allowed=()):
	"""Caller values for the listed fields and for custom fields only."""
	return {key: value for key, value in values.items()
		if key in allowed or (meta.get_field(key) and meta.get_field(key).get("is_custom_field"))}


def append_pending_rows(note, orders):
	allocated = get_allocated_qty([row.name for order in orders for row in order.items])
	for order in orders:
		for row in order.items:
			append_pending_row(note, order, row, flt(row.stock_qty) - flt(allocated.get(row.name)))


def append_selected_rows(note, orders, rows):
	"""Listed order rows within their pending quantity, plus rows with no Sales Order."""
	sources = {row.name: (order, row) for order in orders for row in order.items}
	allocated = get_allocated_qty(list(sources))
	requested = defaultdict(float)
	item_meta = frappe.get_meta("YRP Delivery Note Item")
	for values in rows:
		extra = get_input_values(item_meta, values)
		if not values.get("so_detail"):
			note.append("items", {**extra, "item_code": values.get("item_code"), "uom": values.get("uom"),
				"qty": flt(values.get("qty"))})
			continue
		require(values["so_detail"] in sources,
			_("Sales Order Item {0} is not on the selected Sales Orders.").format(values["so_detail"]))
		order, source = sources[values["so_detail"]]
		qty = flt(values.get("qty"))
		requested[source.name] += qty * flt(source.conversion_factor)
		pending = flt(source.stock_qty) - flt(allocated.get(source.name))
		precision = source.precision("stock_qty")
		require(qty > 0, _("Row {0} of Sales Order {1}: Quantity must be greater than zero.").format(
			source.idx, order.name))
		require(flt(requested[source.name], precision) <= flt(pending, precision),
			_("Row {0} of Sales Order {1}: {2} exceeds pending {3}.").format(
				source.idx, order.name, qty, flt(pending / flt(source.conversion_factor), precision)))
		append_pending_row(note, order, source, qty * flt(source.conversion_factor), extra)


def append_pending_row(note, order, row, pending, extra=None):
	if flt(pending, row.precision("stock_qty")) <= 0:
		return
	note.append("items", {
		**(extra or {}),
		"item_code": row.item_code,
		"item_name": row.item_name,
		"qty": flt(pending / flt(row.conversion_factor), row.precision("qty")),
		"uom": row.uom,
		"conversion_factor": row.conversion_factor,
		"stock_qty": pending,
		"stock_uom": row.stock_uom,
		"rate": row.rate,
		"warehouse": row.warehouse or order.set_warehouse,
		"sales_order": order.name,
		"so_detail": row.name,
	})


def allocate_dimensions(note):
	"""Give rows without stock dimensions the warehouse buckets that hold free stock, oldest first.

	A row larger than one bucket is split; a shortage fails before any reservation is made."""
	fieldnames = [dimension["fieldname"] for dimension in get_mandatory_dimensions()]
	if not fieldnames:
		return
	buckets = {}
	rows = []
	for row in note.items:
		if all(row.get(fieldname) for fieldname in fieldnames):
			rows.append(row)
			continue
		fixed = tuple((fieldname, row.get(fieldname)) for fieldname in fieldnames if row.get(fieldname))
		key = (row.item_code, row.warehouse or note.set_warehouse, fixed)
		if key not in buckets:
			buckets[key] = get_free_buckets(*key)
		rows.extend(split_row(row, buckets[key], key[1]))
	note.set("items", rows)


def get_free_buckets(item_code, warehouse, fixed=()):
	"""Bins with free stock, oldest first, matching the dimension values the row already has."""
	columns = ", ".join(f"`{fieldname}`" for fieldname in get_dimension_fieldnames())
	matches = "".join(f" and `{fieldname}` = %s" for fieldname, _value in fixed)
	return frappe.db.sql(
		f"""select {columns}, actual_qty - reserved_qty as free_qty from `tabYRP Bin`
		where item_code = %s and warehouse = %s{matches} and actual_qty - reserved_qty > 0
		order by creation, name""",
		(item_code, warehouse, *(value for _fieldname, value in fixed)), as_dict=True,
	)


def split_row(row, buckets, warehouse):
	"""Row copies, one per bucket used; the UOM's whole-number rule decides how much a bucket can give."""
	factor = flt(row.conversion_factor) or get_conversion_factor(frappe.get_cached_doc("Item", row.item_code), row.uom)
	whole = frappe.db.get_value("UOM", row.uom, "must_be_whole_number")
	remaining = flt(row.qty)
	values = row.as_dict(no_default_fields=True)
	rows = []
	for bucket in buckets:
		available = flt(bucket.free_qty) / factor
		take = min(remaining, math.floor(available + 1e-9) if whole else available)
		if take <= 0:
			continue
		bucket.free_qty = flt(bucket.free_qty) - take * factor
		remaining -= take
		dimensions = {fieldname: bucket[fieldname] for fieldname in get_dimension_fieldnames()}
		rows.append({**values, **dimensions, "qty": take, "stock_qty": take * factor, "warehouse": warehouse})
		if remaining <= 1e-9:
			return rows
	frappe.throw(_("Row {0}: {1} needs {2} {3} in {4}, but only {5} is free.").format(
		row.idx, row.item_code, flt(row.qty), row.uom, warehouse, flt(row.qty) - remaining))
