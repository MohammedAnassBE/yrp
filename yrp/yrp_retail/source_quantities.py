"""Conversion snapshots for retail demand, without changing its selected UOM.

Claimed Retail Orders cannot change their items, so a summary derives its
conversion from those source rows instead of storing a second copy. Orders with
different pack sizes cannot be combined into one Item/UOM summary row.
"""
import math

import frappe

from yrp.yrp_retail.logic import finite_number, require


def order_conversion_factors(orders):
	"""Return one unambiguous saved conversion per Item/UOM pair."""
	factors = {}
	for order in orders:
		for row in order.items:
			key = (row.item_code, row.uom)
			factor = finite_number(row.conversion_factor, "Source conversion factor")
			require(factor > 0, "Source conversion factor must be positive.")
			if key in factors:
				require(math.isclose(factors[key], factor, rel_tol=1e-12, abs_tol=1e-9),
					"Retail Orders with different conversion factors for the same Item and UOM cannot share a summary.")
			else:
				factors[key] = factor
	return factors


def summary_conversion_factors(summary):
	"""Read immutable source rows under the caller's retail/pricing locks."""
	orders = [frappe.get_doc("YRP Retail Order", name, for_update=True)
		for name in sorted({row.order for row in summary.source_orders})]
	return order_conversion_factors(orders)


def validate_current_conversion(item, uom, stored):
	"""Reject Item conversion changes; return the original demand's factor."""
	stored = finite_number(stored, "Source conversion factor")
	require(stored > 0, "Source conversion factor must be positive.")
	if uom == item.stock_uom:
		current = 1.0
	else:
		matches = [row for row in item.uoms if row.uom == uom]
		require(len(matches) == 1, "Source UOM must have one conversion on the Item.")
		current = finite_number(matches[0].conversion_factor, "Source conversion factor")
	require(current > 0, "Source conversion factor must be positive.")
	require(math.isclose(stored, current, rel_tol=1e-12, abs_tol=1e-9),
		"Retail source conversion factor differs from the current Item conversion.")
	return stored
