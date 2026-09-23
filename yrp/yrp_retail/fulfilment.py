"""Permission-checked native ERPNext delivery and invoice draft creation.

This module does not choose a stock engine or submit stock/accounting documents.
Native row references remain the authority for order/delivery/invoice progress.
"""
import frappe
from frappe import _
from frappe.utils import flt

from yrp.yrp_retail.api import atomic
from yrp.yrp_retail.logic import finite_number, require


def _can_create(doctype):
	frappe.has_permission(doctype, 'create', throw=True)


def _names(value):
	value = frappe.parse_json(value) if isinstance(value, str) else value
	require(isinstance(value, list) and 0 < len(value) <= 100 and all(isinstance(name, str) and name for name in value),
		'Provide between one and 100 Sales Orders.')
	require(len(set(value)) == len(value), 'Sales Orders must not repeat.')
	return value


def _selection(items):
	if items is None:
		return None
	items = frappe.parse_json(items) if isinstance(items, str) else items
	require(isinstance(items, list) and items, 'Select at least one Sales Order row.')
	selected = {}
	for row in items:
		require(isinstance(row, dict) and set(row) == {'sales_order_item', 'qty'},
			'Delivery rows require only sales_order_item and qty.')
		name = row['sales_order_item']
		require(isinstance(name, str) and name and name not in selected, 'Select each Sales Order row once.')
		qty = finite_number(row['qty'], 'Delivery quantity')
		require(qty > 0, 'Delivery quantity must be greater than zero.')
		selected[name] = qty
	return selected


def _compatible_taxes(orders):
	"""Native repeated mapping replaces taxes; never silently keep the last policy.

	Fixed charges/discounts need an explicit allocation policy before consolidation.
	Calculated tax amounts differ naturally with quantities and are not compared.
	"""
	if len(orders) < 2:
		return
	from erpnext.accounts.doctype.accounting_dimension.accounting_dimension import get_accounting_dimensions
	fields = ('charge_type', 'row_id', 'account_head', 'rate', 'included_in_print_rate',
		'included_in_paid_amount', 'cost_center', 'project', 'account_currency',
		'is_tax_withholding_account', 'set_by_item_tax_template', *get_accounting_dimensions())
	def signature(order):
		return [tuple(row.get(field) for field in fields) for row in order.taxes]
	first = orders[0]
	for order in orders:
		require(not any(row.charge_type == 'Actual' or row.get('dont_recompute_tax') for row in order.taxes),
			'Orders with fixed or manually frozen taxes must be delivered separately.')
		require(not flt(order.discount_amount) or flt(order.additional_discount_percentage),
			'Orders with a fixed additional discount must be delivered separately.')
		require(signature(order) == signature(first)
			and order.apply_discount_on == first.apply_discount_on
			and flt(order.additional_discount_percentage) == flt(first.additional_discount_percentage),
			'Sales Orders must have matching tax rows and percentage discount settings.')


@frappe.whitelist(methods=['POST'])
def make_delivery_note(customer, sales_orders, items=None):
	"""Create one native draft DN from compatible submitted orders for a Customer."""
	from erpnext.selling.doctype.sales_order.sales_order import make_delivery_note as native_map
	_can_create('Delivery Note')
	names = _names(sales_orders)
	selected = _selection(items)
	with atomic():
		from yrp.yrp_retail.packing import lock_packing
		lock_packing()
		orders = [frappe.get_doc('Sales Order', name, for_update=True) for name in sorted(names)]
		first = orders[0]
		for order in orders:
			order.check_permission('read')
			require(order.docstatus == 1 and order.status not in ('Closed', 'On Hold'),
				'Sales Orders must be submitted and open for delivery.')
			require(order.customer == customer, 'All Sales Orders must belong to the selected Customer.')
			for field in ('company', 'currency', 'conversion_rate', 'selling_price_list', 'price_list_currency',
				'plc_conversion_rate', 'tax_category', 'taxes_and_charges', 'shipping_address_name', 'customer_address'):
				require(order.get(field) == first.get(field), 'Sales Orders need compatible company, currency, pricing, tax and address settings.')
		_compatible_taxes(orders)
		known = {row.name:row for order in orders for row in order.items}
		if selected is not None:
			require(set(selected) <= set(known), 'A selected row is outside the selected Sales Orders.')
			for name, qty in selected.items():
				require(qty <= flt(known[name].qty) - flt(known[name].delivered_qty), 'Delivery quantity exceeds the remaining Sales Order quantity.')
		target = None
		for order in orders:
			chosen = [row.name for row in order.items if selected is None or row.name in selected]
			if not chosen:
				continue
			target = native_map(order.name, target_doc=target, kwargs={'filtered_children': chosen, 'ignore_pricing_rule': True})
		require(target and target.items, 'No undelivered order quantities remain.')
		if selected is not None:
			require({row.so_detail for row in target.items} == set(selected), 'Some selected rows cannot be delivered.')
			for row in target.items:
				row.qty = selected[row.so_detail]
				row.stock_qty = row.qty * flt(row.conversion_factor)
		target.calculate_taxes_and_totals()
		target.insert()
		return {'doctype':target.doctype, 'name':target.name, 'docstatus':target.docstatus}


@frappe.whitelist(methods=['POST'])
def make_sales_invoice(delivery_note):
	"""Create a native invoice draft from submitted DN prices, without stock reposting."""
	from erpnext.stock.doctype.delivery_note.delivery_note import make_sales_invoice as native_map
	_can_create('Sales Invoice')
	with atomic():
		from yrp.yrp_retail.packing import lock_packing
		lock_packing()
		source = frappe.get_doc('Delivery Note', delivery_note, for_update=True)
		source.check_permission('read')
		require(source.docstatus == 1 and not source.is_return, 'Select a submitted, non-return Delivery Note.')
		target = native_map(source.name)
		target.update_stock = 0
		target.ignore_pricing_rule = 1
		source_rows = {row.name:row for row in source.items}
		for row in target.items:
			original = source_rows[row.dn_detail]
			for field in ('rate', 'price_list_rate', 'discount_percentage', 'discount_amount', 'margin_type', 'margin_rate_or_amount'):
				row.set(field, original.get(field))
		target.insert()
		for row in target.items:
			require(flt(row.rate) == flt(source_rows[row.dn_detail].rate), 'Invoice rate differs from its Delivery Note; review pricing configuration.')
		return {'doctype':target.doctype, 'name':target.name, 'docstatus':target.docstatus}
