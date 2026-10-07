# Copyright (c) 2026, Mohammed Anas and contributors
# For license information, please see license.txt

from collections import defaultdict

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, get_datetime, now_datetime

from yrp.yrp_retail.logic import require, validate_uom_quantity
from yrp.yrp_sales.doctype.yrp_delivery_note.yrp_delivery_note import get_packed_qty
from yrp.yrp_sales.invoicing import get_delivery_note_rows, lock_delivery_note

DELIVERY_NOTE = "YRP Delivery Note"
PACKING_SLIP = "YRP Packing Slip"


class YRPPackingSlip(Document):
	"""Optional package (box, cover, courier) for part of a submitted YRP Delivery Note."""

	def _save(self, *args, **kwargs):
		# The Delivery Note is locked before Frappe locks this document.
		if self.delivery_note:
			lock_delivery_note(self.delivery_note)
		return super()._save(*args, **kwargs)

	def validate(self):
		require(self.items, "A Packing Slip requires items.")
		note = lock_delivery_note(self.delivery_note)
		require(note.docstatus == 1, _("Delivery Note {0} must be submitted.").format(note.name))
		require(not note.delivered_at, _("Delivery Note {0} is already delivered.").format(note.name))
		self.customer = note.customer
		rows = get_delivery_note_rows(note.name)
		for row in self.items:
			self.set_row_values(row, rows.get(row.dn_detail))
		self.validate_carton_no()
		self.validate_packing(rows)
		self.status = self.get_status()

	def set_row_values(self, row, source):
		label = _("Row {0}").format(row.idx)
		require(source, _("{0}: must reference a row of Delivery Note {1}.").format(label, self.delivery_note))
		qty = validate_uom_quantity(row.qty, source.uom, _("{0} Quantity").format(label), row.precision("qty"))
		require(qty > 0, _("{0}: Quantity must be greater than zero.").format(label))
		row.qty = qty
		row.item_code = source.item_code
		row.item_name = source.item_name
		row.uom = source.uom
		row.stock_qty = qty * flt(source.conversion_factor)

	def validate_packing(self, rows):
		"""Keep this and other non-cancelled slips within each Delivery Note row."""
		requested = defaultdict(float)
		for row in self.items:
			requested[row.dn_detail] += flt(row.qty)
		others = get_packed_qty(self.delivery_note, exclude=self.name)
		precision = self.items[0].precision("qty")
		for detail, qty in requested.items():
			source = rows[detail]
			packed = flt(qty + flt(others.get(detail)), precision)
			require(packed <= flt(source.qty, precision), _(
				"Row {0} of Delivery Note {1}: {2} would be packed, but only {3} is on the note."
			).format(source.idx, self.delivery_note, packed, flt(source.qty, precision)))

	def validate_carton_no(self):
		"""A carton number is used once per Delivery Note among non-cancelled slips."""
		if not self.carton_no:
			return
		require(not frappe.db.exists(PACKING_SLIP, {"delivery_note": self.delivery_note, "carton_no": self.carton_no,
			"docstatus": ["<", 2], "name": ["!=", self.name or ""]}),
			_("Carton {0} already exists on Delivery Note {1}.").format(self.carton_no, self.delivery_note))

	def get_status(self):
		if self.docstatus == 0:
			return "Draft"
		if self.docstatus == 2:
			return "Cancelled"
		return "Delivered" if self.delivered_at else "Packed"

	def on_submit(self):
		self.db_set("status", self.get_status(), update_modified=False)
		frappe.get_doc(DELIVERY_NOTE, self.delivery_note).update_packing()

	def before_update_after_submit(self):
		before = self.get_doc_before_save()
		require(get_optional_datetime(self.delivered_at) == get_optional_datetime(before.delivered_at),
			"Use Mark Delivered to deliver a Packing Slip.")

	def before_cancel(self):
		require(not self.delivered_at, _("Delivered Packing Slip {0} cannot be cancelled.").format(self.name))

	def on_cancel(self):
		self.db_set("status", "Cancelled", update_modified=False)
		frappe.get_doc(DELIVERY_NOTE, self.delivery_note).update_packing()

	@frappe.whitelist()
	def mark_delivered(self):
		"""Deliver this slip's saved rows, within each row's invoiced quantity."""
		# run_doc_method builds this document from the request; only the saved slip counts.
		self.reload()
		self.check_permission("write")
		note = frappe.get_doc(DELIVERY_NOTE, self.delivery_note)
		rows = note.lock_for_delivery()
		current = frappe.db.sql("select docstatus, delivered_at from `tabYRP Packing Slip` where name = %s for update",
			self.name, as_dict=True)[0]
		require(current.docstatus == 1, _("Packing Slip {0} must be submitted.").format(self.name))
		require(not current.delivered_at, _("Packing Slip {0} is already delivered.").format(self.name))
		delivered = note.get_delivered_qty()
		for row in self.items:
			delivered[row.dn_detail] = flt(delivered.get(row.dn_detail)) + flt(row.qty)
		note.validate_billed_qty(rows, delivered)
		self.set_delivered(now_datetime())
		note.update_delivery()

	def set_delivered(self, delivered_at):
		self.db_set({"delivered_at": delivered_at, "status": "Delivered"})


def get_optional_datetime(value):
	return get_datetime(value) if value else None


@frappe.whitelist()
def make_packing_slip(delivery_note):
	"""Return an unsaved Packing Slip for the unpacked quantity of a submitted Delivery Note."""
	frappe.has_permission("YRP Packing Slip", "create", throw=True)
	note = frappe.get_doc(DELIVERY_NOTE, delivery_note)
	note.check_permission("read")
	require(note.docstatus == 1, _("Delivery Note {0} must be submitted.").format(note.name))
	require(not note.delivered_at, _("Delivery Note {0} is already delivered.").format(note.name))
	packed = get_packed_qty(note.name)
	slip = frappe.new_doc("YRP Packing Slip")
	slip.update({"delivery_note": note.name, "customer": note.customer})
	for row in note.items:
		unpacked = flt(flt(row.qty) - flt(packed.get(row.name)), row.precision("qty"))
		if unpacked > 0:
			slip.append("items", {"dn_detail": row.name, "item_code": row.item_code, "item_name": row.item_name,
				"qty": unpacked, "uom": row.uom, "stock_qty": unpacked * flt(row.conversion_factor)})
	require(slip.items, _("Everything on Delivery Note {0} is already packed.").format(note.name))
	return slip
