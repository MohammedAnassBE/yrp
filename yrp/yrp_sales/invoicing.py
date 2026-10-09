from collections import defaultdict

import frappe
from frappe import _
from frappe.utils import cint, flt

from yrp.stock.dimensions import get_dimension_fieldnames
from yrp.stock.stock_ledger import _lock_entry_buckets
from yrp.stock.uom import resolve_item_uom
from yrp.stock.utils import consume_voucher_reservation
from yrp.yrp_retail.logic import require

DELIVERY_NOTE = "YRP Delivery Note"
DELIVERY_NOTE_ITEM = "YRP Delivery Note Item"
STOCK_ENTRY = "YRP Stock Entry"
PREVIEW_FIELDS = ("total", "discount_amount", "net_total", "total_taxes_and_charges", "grand_total", "rounded_total",
	"currency")
PREVIEW_TAX_FIELDS = ("description", "account_head", "rate", "tax_amount")


@frappe.whitelist()
def make_sales_invoice(delivery_note, items=None):
	"""Insert a draft Sales Invoice for a submitted Delivery Note.

	``items`` maps Delivery Note row names to quantities; by default every row's unbilled quantity is billed.
	A return note is billed with a Credit Note: the same rows with negative quantities.
	"""
	frappe.has_permission("Sales Invoice", "create", throw=True)
	frappe.get_doc(DELIVERY_NOTE, delivery_note).check_permission("read")
	invoice = get_sales_invoice(delivery_note, items)
	invoice.insert()
	return invoice


def get_sales_invoice(delivery_note, items=None):
	"""Unsaved Sales Invoice for a submitted Delivery Note; callers check permissions.

	Apps adjust it through `yrp_sales_invoice_mappers` hooks, called as `handler(invoice, note)`."""
	note = frappe.get_doc(DELIVERY_NOTE, delivery_note)
	require(note.docstatus == 1, _("Delivery Note {0} must be submitted.").format(note.name))
	requested = get_requested_qty(note, items)
	invoice = frappe.new_doc("Sales Invoice")
	invoice.update({
		"customer": note.customer,
		"company": note.company,
		"selling_price_list": note.selling_price_list,
		"currency": note.currency,
		"ignore_pricing_rule": 1,
		"update_stock": 0,
		"is_return": cint(note.is_return),
		"yrp_delivery_note": note.name,
		"shipping_address_name": note.shipping_address_name,
		"transporter": note.transporter,
	})
	sign = get_sign(note)
	for row in note.items:
		if flt(requested.get(row.name), row.precision("qty")) > 0:
			invoice.append("items", {
				"item_code": row.item_code,
				"qty": sign * requested[row.name],
				"uom": row.uom,
				"conversion_factor": row.conversion_factor,
				"rate": row.rate,
				"yrp_delivery_note_item": row.name,
			})
	require(invoice.items, _("Nothing is pending billing on Delivery Note {0}.").format(note.name))
	for handler in frappe.get_hooks("yrp_sales_invoice_mappers"):
		frappe.get_attr(handler)(invoice, note)
	invoice.set_missing_values()
	return invoice


def get_sign(note):
	"""Invoice quantities are negative on a return note's Credit Note."""
	return -1 if cint(note.get("is_return")) else 1


def preview_sales_invoice(delivery_note):
	"""Totals and taxes the next Sales Invoice would carry; nothing is kept. Callers check permissions."""
	savepoint = "preview_sales_invoice_" + frappe.generate_hash(length=10)
	frappe.db.savepoint(savepoint)
	try:
		invoice = get_sales_invoice(delivery_note)
		invoice.flags.ignore_permissions = True
		invoice.insert()
		return {
			**{fieldname: invoice.get(fieldname) for fieldname in PREVIEW_FIELDS},
			"taxes": [{fieldname: tax.get(fieldname) for fieldname in PREVIEW_TAX_FIELDS} for tax in invoice.taxes],
		}
	finally:
		frappe.db.rollback(save_point=savepoint)


def get_invoice_status(delivery_note):
	"""Draft while any live Sales Invoice of the note is a draft, Submitted when all are, else empty."""
	docstatuses = set(frappe.get_all("Sales Invoice",
		filters={"yrp_delivery_note": delivery_note, "docstatus": ["<", 2]}, pluck="docstatus"))
	if 0 in docstatuses:
		return "Draft"
	return "Submitted" if docstatuses else ""


def refresh_delivery_note_billing(doc, method=None):
	"""Keep the Delivery Note's invoice status in step when a draft invoice is made or deleted."""
	if doc.get("yrp_delivery_note") and frappe.db.exists(DELIVERY_NOTE, doc.yrp_delivery_note):
		frappe.get_doc(DELIVERY_NOTE, doc.yrp_delivery_note).update_billing()


def get_requested_qty(note, items):
	"""Return quantity per Delivery Note row: the caller's request or everything not yet invoiced."""
	if items:
		requested = frappe.parse_json(items) if isinstance(items, str) else items
		unknown = set(requested) - {row.name for row in note.items}
		require(not unknown, _("Rows {0} do not belong to Delivery Note {1}.").format(", ".join(unknown), note.name))
		return {name: flt(qty) for name, qty in requested.items()}
	invoiced = get_invoiced_qty(note.name)
	return {row.name: flt(row.qty) - flt(invoiced.get(row.name)) for row in note.items}


def get_invoiced_qty(delivery_note, exclude=None, submitted_only=False):
	"""Quantity per Delivery Note row on non-cancelled (or only submitted) Sales Invoices, read under lock
	so a request that waited on the note lock sees invoices committed meanwhile. Credit Note rows count
	by size: a return note's invoices all carry negative quantities."""
	return dict(frappe.db.sql(
		"""select sii.yrp_delivery_note_item, sum(abs(sii.qty)) from `tabSales Invoice Item` sii
		join `tabSales Invoice` si on si.name = sii.parent and sii.parenttype = 'Sales Invoice'
		where si.yrp_delivery_note = %(note)s and si.docstatus in %(docstatuses)s and si.name != %(exclude)s
		group by sii.yrp_delivery_note_item for update""",
		{"note": delivery_note, "docstatuses": (1,) if submitted_only else (0, 1), "exclude": exclude or ""},
	))


def validate_sales_invoice(doc, method=None):
	"""Keep a Delivery Note invoice on matching rows and within each row's unbilled quantity."""
	if doc.docstatus == 0:
		# Only submit records issued entries; server-side copies keep no_copy values.
		doc.yrp_stock_entries = None
	if not doc.get("yrp_delivery_note"):
		require(not any(row.get("yrp_delivery_note_item") for row in doc.items),
			_("Rows reference a YRP Delivery Note, but the invoice has none."))
		return
	require(not cint(doc.update_stock), _("A Sales Invoice for a YRP Delivery Note cannot update stock."))
	note = lock_delivery_note(doc.yrp_delivery_note)
	require(not cint(doc.is_return) or note.is_return, _("A return cannot reference a YRP Delivery Note."))
	require(cint(doc.is_return) or not note.is_return,
		_("Delivery Note {0} is a return; bill it with a Credit Note.").format(note.name))
	require(note.docstatus == 1, _("Delivery Note {0} must be submitted.").format(note.name))
	require(note.customer == doc.customer, _("Delivery Note {0} belongs to another Customer.").format(note.name))
	require(note.company == doc.company, _("Delivery Note {0} belongs to another Company.").format(note.name))
	sources = get_delivery_note_rows(note.name)
	requested = defaultdict(float)
	for row in doc.items:
		source = sources.get(row.yrp_delivery_note_item)
		validate_invoice_row(row, source, note.name, get_sign(note))
		requested[source.name] += abs(flt(row.qty))
	validate_billing_capacity(doc.name, note.name, sources, requested)


def validate_invoice_row(row, source, delivery_note, sign=1):
	label = _("Row {0}").format(row.idx)
	require(source, _("{0}: must reference a row of Delivery Note {1}.").format(label, delivery_note))
	require(row.item_code == source.item_code, _("{0}: Item must match its Delivery Note row.").format(label))
	require(row.uom == source.uom, _("{0}: UOM must match its Delivery Note row.").format(label))
	require(flt(row.conversion_factor) == flt(source.conversion_factor),
		_("{0}: Conversion Factor must match its Delivery Note row.").format(label))
	require(flt(row.rate, row.precision("rate")) == flt(source.rate, row.precision("rate")),
		_("{0}: Rate must match its Delivery Note row.").format(label))
	if sign > 0:
		require(flt(row.qty) > 0, _("{0}: Quantity must be greater than zero.").format(label))
	else:
		require(flt(row.qty) < 0, _("{0}: A Credit Note quantity must be negative.").format(label))


def validate_billing_capacity(invoice, delivery_note, sources, requested):
	"""Keep this and other non-cancelled invoices within each Delivery Note row."""
	others = get_invoiced_qty(delivery_note, exclude=invoice)
	precision = frappe.get_precision(DELIVERY_NOTE_ITEM, "qty")
	for detail, qty in requested.items():
		source = sources[detail]
		billed = flt(qty + flt(others.get(detail)), precision)
		require(billed <= flt(source.qty, precision), _(
			"Row {0} of Delivery Note {1}: {2} would be invoiced, but only {3} was delivered."
		).format(source.idx, delivery_note, billed, flt(source.qty)))


def lock_delivery_note(name):
	"""Lock the Delivery Note header; its invoices, Packing Slips and deliveries serialize here."""
	rows = frappe.db.sql(
		"""select name, docstatus, customer, company, per_delivered, delivered_at, packing_status, is_return,
		skip_stock from `tabYRP Delivery Note` where name = %s for update""", name, as_dict=True)
	require(rows, _("Delivery Note {0} does not exist.").format(name))
	return rows[0]


def get_delivery_note_rows(name):
	rows = frappe.db.sql(
		"""select name, idx, item_code, item_name, uom, conversion_factor, qty, rate, billed_qty
		from `tabYRP Delivery Note Item`
		where parent = %s and parenttype = %s order by name for update""", (name, DELIVERY_NOTE), as_dict=True)
	return {row.name: row for row in rows}


def issue_delivery_note_stock(doc, method=None):
	"""Consume the invoiced rows' reservations, then issue their stock from each warehouse."""
	if not doc.get("yrp_delivery_note"):
		return
	note = frappe.get_doc(DELIVERY_NOTE, doc.yrp_delivery_note)
	if note.skip_stock:
		note.update_billing()
		return
	issues = get_stock_issues(doc, note)
	lock_stock_buckets(issues)
	# The ledger floor counts this note's reservation, so consume it before posting.
	for row, stock_qty in issues:
		consume_voucher_reservation(DELIVERY_NOTE, note.name, row.name, stock_qty)
	warehouses = defaultdict(list)
	for row, stock_qty in issues:
		warehouses[row.warehouse].append((row, stock_qty))
	entries = [make_issue_entry(doc, note, warehouse, warehouses[warehouse]) for warehouse in sorted(warehouses)]
	doc.db_set("yrp_stock_entries", "\n".join(entries), update_modified=False)
	note.update_billing()


def reverse_delivery_note_stock(doc, method=None):
	"""Cancel the invoice's stock issues, then restore the reservations they consumed."""
	if not doc.get("yrp_delivery_note"):
		return
	note = frappe.get_doc(DELIVERY_NOTE, doc.yrp_delivery_note, for_update=True)
	require(not flt(note.per_delivered),
		_("Delivery Note {0} has deliveries, so its invoices cannot be cancelled.").format(note.name))
	if note.skip_stock:
		note.update_billing()
		return
	for name in reversed((doc.yrp_stock_entries or "").split()):
		entry = frappe.get_doc(STOCK_ENTRY, name)
		entry.flags.ignore_permissions = True
		entry.flags.cancel_from_sales_invoice = True
		entry.cancel()
	for row, stock_qty in get_stock_issues(doc, note):
		consume_voucher_reservation(DELIVERY_NOTE, note.name, row.name, -stock_qty)
	note.update_billing()


def get_stock_issues(invoice, note):
	"""Return (Delivery Note row, stock quantity) for each row this invoice bills."""
	qty = defaultdict(float)
	for row in invoice.items:
		qty[row.yrp_delivery_note_item] += flt(row.qty)
	return [(row, flt(qty[row.name] * flt(row.conversion_factor), row.precision("stock_qty")))
		for row in note.items if row.name in qty]


def lock_stock_buckets(issues):
	"""Lock the valuation buckets before reservations change, in the ledger's order."""
	fieldnames = get_dimension_fieldnames()
	_lock_entry_buckets([{"item": row.item_code, "warehouse": row.warehouse,
		**{fieldname: row.get(fieldname) for fieldname in fieldnames}} for row, _qty in issues], fieldnames)


def make_issue_entry(invoice, note, warehouse, issues):
	entry = frappe.get_doc({
		"doctype": STOCK_ENTRY,
		"purpose": "Material Issue",
		"from_warehouse": warehouse,
		"against": DELIVERY_NOTE,
		"against_id": note.name,
		"edit_posting_date_and_time": 1,
		"posting_date": invoice.posting_date,
		"posting_time": invoice.posting_time,
		"items": [get_issue_row(row, stock_qty) for row, stock_qty in issues],
	})
	entry.set_rate_from_last_sle()
	entry.insert(ignore_permissions=True)
	validate_issued_qty(entry, issues)
	entry.flags.ignore_permissions = True
	entry.submit()
	return entry.name


def get_issue_row(row, stock_qty):
	"""Translate the Delivery Note stock quantity into the Stock Entry's master UOM."""
	uom = resolve_item_uom(row.item_code)
	return {
		"item": row.item_code,
		"uom": uom.uom,
		"conversion_factor": uom.conversion_factor,
		"qty": stock_qty / uom.conversion_factor,
		"against": DELIVERY_NOTE_ITEM,
		"against_id_detail": row.name,
		**{fieldname: row.get(fieldname) for fieldname in get_dimension_fieldnames()},
	}


def validate_issued_qty(entry, issues):
	expected = {row.name: stock_qty for row, stock_qty in issues}
	require(len(entry.items) == len(expected), _("Stock Entry {0} lost Delivery Note rows.").format(entry.name))
	for row in entry.items:
		precision = row.precision("stock_qty")
		require(flt(row.stock_qty, precision) == flt(expected.get(row.against_id_detail), precision), _(
			"Stock Entry {0} row {1} issues {2} {3}, but the Delivery Note reserved {4}. Check the Item UOM conversion."
		).format(entry.name, row.idx, flt(row.stock_qty, precision), row.stock_uom,
			flt(expected.get(row.against_id_detail), precision)))
