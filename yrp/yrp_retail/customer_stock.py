"""Customer-reported balances in one retail UOM, without stock ledger postings.

A report replaces the current balance. Summary submission consumes its Customer
allocation, clamped at zero. Draft edits and cancellation do not assert physical
receipts and therefore do not change this reported balance.
"""
from contextlib import contextmanager
from contextvars import ContextVar

import frappe
from frappe.utils import now_datetime

from yrp.yrp_retail.logic import finite_number, require

_stock_write = ContextVar('yrp_customer_stock_write', default=None)


def permitted_write(doc):
	return doc.doctype == 'YRP Customer Stock' and _stock_write.get() is doc


@contextmanager
def writing(doc):
	token = _stock_write.set(doc)
	try:
		yield
	finally:
		_stock_write.reset(token)


def configured_uom(lock=False):
	"""Read uncached settings; writers lock the singleton's stable DocType row."""
	if lock:
		frappe.db.sql("select name from `tabDocType` where name='YRP Retail Settings' for update")
	if lock:
		rows = frappe.db.sql("select value from `tabSingles` where doctype='YRP Retail Settings' and field='stock_uom' for update")
		value = rows[0][0] if rows else None
	else:
		value = frappe.db.get_single_value('YRP Retail Settings', 'stock_uom', cache=False)
	require(value, 'Configure Retail UOM in YRP Retail Settings first.')
	return value


def validate_qty(value, uom):
	qty = finite_number(value, 'Customer stock quantity')
	require(qty >= 0, 'Customer stock quantity cannot be negative.')
	if frappe.db.get_value('UOM', uom, 'must_be_whole_number'):
		require(qty.is_integer(), 'Customer stock quantity must be a whole number for this UOM.')
	return qty


def validate_item(item_code):
	item = frappe.get_doc('Item', item_code)
	require(not item.disabled and not item.has_variants and item.is_sales_item,
		'Customer stock requires an enabled, concrete sales Item.')


def _balance(customer, item_code, uom):
	"""Serialize updates including the first insert; Customer/item is unique."""
	found = frappe.db.sql('select name from `tabCustomer` where name=%s for update', customer)
	require(found, 'Customer does not exist.')
	rows = frappe.db.sql('select name from `tabYRP Customer Stock` where customer=%s and item_code=%s for update', (customer, item_code))
	if rows:
		doc = frappe.get_doc('YRP Customer Stock', rows[0][0], for_update=True)
		require(doc.uom == uom, 'Customer stock UOM does not match Retail Settings.')
		return doc
	return frappe.get_doc({'doctype':'YRP Customer Stock', 'customer':customer,
		'item_code':item_code, 'uom':uom, 'qty':0})


def _save(doc):
	with writing(doc):
		doc.save(ignore_permissions=True)
	return payload(doc)


def payload(doc):
	return {key:doc.get(key) for key in ('customer','item_code','uom','qty','last_counted_at','last_counted_by')} | {'recorded':bool(doc.last_counted_at)}


def report(customer, item_code, qty):
	"""Absolute physical count, not an increment; caller authorizes Customer access."""
	uom = configured_uom(lock=True)
	validate_item(item_code)
	qty = validate_qty(qty, uom)
	doc = _balance(customer, item_code, uom)
	doc.qty = qty
	doc.last_counted_at = now_datetime()
	doc.last_counted_by = frappe.session.user
	return _save(doc)


def consume_summary(summary):
	"""Called only on submission, in the same transaction as the summary."""
	rows = [row for row in summary.items if row.customer_stock_qty > 0]
	if not rows:
		return
	uom = configured_uom(lock=True)
	for row in sorted(rows, key=lambda row: row.item_code):
		require(row.uom == uom, 'Summary allocation must use the configured Retail UOM.')
		qty = validate_qty(row.customer_stock_qty, uom)
		validate_item(row.item_code)
		doc = _balance(summary.customer, row.item_code, uom)
		doc.qty = max(0, doc.qty - qty)
		_save(doc)
