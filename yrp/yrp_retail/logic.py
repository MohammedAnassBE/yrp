"""Shared retail validation, with no accounting or stock side effects."""

import json
import math
from collections import defaultdict

import frappe
from frappe import _
from frappe.utils import getdate


def require(condition, message):
	if not condition:
		frappe.throw(_(message))


def finite_number(value, label):
	"""Return a finite number; never silently coerce invalid quantities to zero."""
	try:
		number = float(value)
	except (TypeError, ValueError, OverflowError):
		frappe.throw(_("{0} must be a finite number.").format(label))
	require(math.isfinite(number), "{0} must be a finite number.".format(label))
	return number


def validate_location(value):
	"""Accept a GeoJSON Point or a FeatureCollection containing exactly one Point."""
	try:
		geo = json.loads(value) if isinstance(value, str) else value
		require(isinstance(geo, dict), "Location must contain a GeoJSON point.")
		if geo.get("type") == "FeatureCollection":
			features = geo.get("features")
			require(isinstance(features, list) and len(features) == 1, "Location must contain exactly one point.")
			feature = features[0]
			require(isinstance(feature, dict) and feature.get("type") == "Feature", "Invalid location feature.")
			geo = feature.get("geometry")
		require(isinstance(geo, dict) and geo.get("type") == "Point", "Location must be a GeoJSON Point.")
		coords = geo.get("coordinates")
		require(isinstance(coords, list) and len(coords) in (2, 3), "Location requires longitude and latitude.")
		for coordinate in coords:
			require(isinstance(coordinate, (int, float)) and not isinstance(coordinate, bool) and math.isfinite(coordinate), "Location coordinates must be finite numbers.")
		require(-180 <= coords[0] <= 180 and -90 <= coords[1] <= 90, "Location coordinates are out of range.")
	except (ValueError, TypeError):
		frappe.throw(_("Location must be valid GeoJSON."))


def validate_assignment(sales_person, customer, retailer=None):
	"""Enforce Customer assignment; retailers may be visited by any assigned salesperson."""
	from yrp.yrp_retail.setup import assigned_customers

	require(sales_person and customer, "Sales Person and Customer are required.")
	person = frappe.get_doc("Sales Person", sales_person)
	require(person.enabled and not person.is_group, "Sales Person must be enabled and must not be a group.")
	require(customer in assigned_customers(sales_person), "Customer is not assigned to this Sales Person.")
	if retailer:
		outlet = frappe.get_doc("YRP Retailer", retailer)
		require(not outlet.disabled, "Disabled retailers cannot receive new visits or orders.")
		require(outlet.customer == customer, "Retailer does not belong to this Customer.")


def validate_order_items(rows):
	"""Validate saleable Item/UOM pairs and derive immutable stock quantities."""
	require(rows, "At least one order item is required.")
	retail_uom = frappe.db.get_single_value("YRP Retail Settings", "stock_uom", cache=False)
	seen = set()
	for row in rows:
		if retail_uom:
			require(row.uom == retail_uom, "Use the configured Retail UOM for retail orders.")
		key = (row.item_code, row.uom)
		require(key not in seen, "Duplicate Item and UOM rows are not allowed.")
		seen.add(key)
		require(row.item_code and row.uom, "Item and UOM are required.")
		item = frappe.get_doc("Item", row.item_code)
		require(not item.disabled and not item.has_variants and item.is_sales_item, "Order items must be enabled, concrete sales Items.")
		row.qty = finite_number(row.qty, "Quantity")
		require(row.qty > 0, "Quantity must be greater than zero.")
		if row.uom == item.stock_uom:
			factor = 1.0
		else:
			matches = [entry for entry in item.get("uoms", []) if entry.uom == row.uom]
			require(len(matches) == 1, "UOM must have one conversion on the Item.")
			factor = finite_number(matches[0].conversion_factor, "Conversion factor")
			require(factor > 0, "Conversion factor must be greater than zero.")
		if frappe.db.get_value("UOM", row.uom, "must_be_whole_number"):
			require(row.qty.is_integer(), "Quantity must be a whole number for this UOM.")
		row.conversion_factor = factor
		row.stock_qty = finite_number(row.qty * factor, "Stock quantity")
		if frappe.db.get_value("UOM", item.stock_uom, "must_be_whole_number"):
			require(row.stock_qty.is_integer(), "Stock quantity must be a whole number for the stock UOM.")


def aggregate_orders(orders):
	"""Aggregate requested quantities in their selected UOM, never across UOMs."""
	totals = defaultdict(float)
	for order in orders:
		for row in order.items:
			totals[(row.item_code, row.uom)] += finite_number(row.qty, "Requested quantity")
	return dict(sorted(totals.items()))


def same_date(left, right):
	return getdate(left) == getdate(right)
