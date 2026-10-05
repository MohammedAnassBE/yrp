# Copyright (c) 2026, Mohammed Anas and contributors
# For license information, please see license.txt

from collections import defaultdict

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt

from yrp.stock.dimensions import apply_dimension_defaults, get_dimension_fieldnames
from yrp.stock.utils import close_voucher_reservations, get_available_stock
from yrp.yrp_retail.logic import require, validate_uom_quantity
from yrp.yrp_retail.pricing import check_rate

RESERVATION = "YRP Stock Reservation Entry"
INACTIVE_RESERVATION_STATUSES = ["Delivered", "Closed", "Cancelled"]


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
			self.set_row_values(row, sources[row.so_detail])
		self.validate_allocation(sources)
		self.validate_rates()
		self.total_qty = sum(flt(row.qty) for row in self.items)
		self.total = sum(flt(row.amount) for row in self.items)
		self.status = self.get_status()

	def get_source_rows(self):
		"""Lock linked Sales Orders and return their item rows by name."""
		orders = lock_sales_orders({row.sales_order for row in self.items})
		for order in orders.values():
			require(order.docstatus == 1, _("Sales Order {0} must be submitted.").format(order.name))
			require(order.customer == self.customer, _("Sales Order {0} belongs to another Customer.").format(order.name))
			require(order.company == self.company, _("Sales Order {0} belongs to another Company.").format(order.name))
		details = sorted({row.so_detail for row in self.items if row.so_detail})
		sources = {row.name: row for row in get_sales_order_rows(details)}
		for row in self.items:
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
		qty = validate_uom_quantity(row.qty, row.uom, _("{0} Quantity").format(label), row.precision("qty"))
		require(qty > 0, _("{0}: Quantity must be greater than zero.").format(label))
		row.qty = qty
		row.item_name = source.item_name
		row.conversion_factor = flt(source.conversion_factor)
		row.stock_qty = qty * row.conversion_factor
		row.stock_uom = source.stock_uom
		row.amount = qty * flt(row.rate)
		row.warehouse = row.warehouse or self.set_warehouse
		require(row.warehouse, _("{0}: Warehouse is required.").format(label))

	def validate_allocation(self, sources):
		"""Keep this and other non-cancelled Delivery Notes within each Sales Order row."""
		requested = defaultdict(float)
		for row in self.items:
			requested[row.so_detail] += flt(row.stock_qty)
		others = get_allocated_qty(list(requested), exclude=self.name)
		precision = self.items[0].precision("stock_qty")
		for detail, qty in requested.items():
			source = sources[detail]
			allocated = flt(qty + others.get(detail, 0), precision)
			require(allocated <= flt(source.stock_qty, precision), _(
				"Item {0} of Sales Order {1}: {2} would be allocated to Delivery Notes, but only {3} was ordered."
			).format(source.item_code, source.parent, allocated, flt(source.stock_qty, precision)))

	def validate_rates(self):
		codes = sorted({row.item_code for row in self.items})
		free = set(frappe.get_all("Item", filters={"name": ["in", codes], "yrp_is_free_item": 1}, pluck="name"))
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

	def on_cancel(self):
		close_voucher_reservations(self.doctype, self.name)
		self.db_set("status", "Cancelled", update_modified=False)

	def on_trash(self):
		close_voucher_reservations(self.doctype, self.name)

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


@frappe.whitelist()
def make_delivery_note(customer, sales_orders):
	"""Return an unsaved Delivery Note for the pending quantity of submitted Sales Orders."""
	frappe.has_permission("YRP Delivery Note", "create", throw=True)
	names = sorted(set(frappe.parse_json(sales_orders) if isinstance(sales_orders, str) else sales_orders))
	require(names, "Select at least one Sales Order.")
	orders = [frappe.get_doc("YRP Sales Order", name) for name in names]
	for order in orders:
		order.check_permission("read")
		require(order.docstatus == 1, _("Sales Order {0} must be submitted.").format(order.name))
		require(order.customer == customer, _("Sales Order {0} belongs to another Customer.").format(order.name))
	require(len({order.company for order in orders}) == 1, "Sales Orders must belong to one Company.")
	allocated = get_allocated_qty([row.name for order in orders for row in order.items])
	warehouses = {order.set_warehouse for order in orders}
	note = frappe.new_doc("YRP Delivery Note")
	note.update({
		"customer": customer,
		"company": orders[0].company,
		"selling_price_list": orders[0].selling_price_list,
		"currency": orders[0].currency,
		"set_warehouse": warehouses.pop() if len(warehouses) == 1 else None,
	})
	for order in orders:
		for row in order.items:
			append_pending_row(note, order, row, flt(row.stock_qty) - flt(allocated.get(row.name)))
	require(note.items, "Nothing is pending delivery on the selected Sales Orders.")
	apply_dimension_defaults(note.items)
	return note


def append_pending_row(note, order, row, pending):
	if flt(pending, row.precision("stock_qty")) <= 0:
		return
	note.append("items", {
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
