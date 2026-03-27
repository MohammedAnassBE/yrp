# Copyright (c) 2026, Mohammed Anas and contributors
# For license information, please see license.txt

import json

import frappe
from frappe import _
from frappe.utils import date_diff, flt, now, nowdate

from yrp.yrp.doctype.item.item import (
	get_attribute_details,
	get_attribute_values,
	get_or_create_variant,
)


class ProductionOrder(frappe.model.document.Document):
	def before_validate(self):
		if self.is_new():
			self.set("production_ordered_details", [])
			self.submitted_by = None
			self.submitted_time = None

		if self.item_details:
			rows = save_production_order_items(self.item_details)
			self.set("production_order_details", [])
			for row in rows:
				self.append("production_order_details", row)

		if self.delivery_date and self.posting_date:
			self.lead_time_given = date_diff(self.delivery_date, self.posting_date)

	def before_submit(self):
		if self.production_term:
			docstatus = frappe.get_value("Production Term", self.production_term, "docstatus")
			if docstatus != 1:
				frappe.throw(_("Selected Production Term is not submitted."))

		self.posting_date = nowdate()

		if self.delivery_date < self.posting_date:
			frappe.throw(_("Delivery Date cannot be before Posting Date."))

		if self.delivery_date > self.dont_deliver_after:
			frappe.throw(_("Delivery Date cannot be after Don't Deliver After date."))

		self.lead_time_given = date_diff(self.delivery_date, self.posting_date)
		self.submitted_by = frappe.session.user
		self.submitted_time = now()

	def before_cancel(self):
		if self.production_ordered_details:
			refs = set()
			for row in self.production_ordered_details:
				if row.reference_name:
					refs.add(row.reference_name)
			if refs:
				frappe.throw(
					_("Cannot cancel. Production groups are linked: {0}").format(", ".join(refs))
				)

	def on_update_after_submit(self):
		if self.delivery_date and self.posting_date:
			self.lead_time_given = date_diff(self.delivery_date, self.posting_date)

	def onload(self):
		self.set_onload("order_summary", get_order_summary(self.name))
		self.set_onload("production_settings", get_production_order_settings())
		if self.production_order_details:
			self.set_onload("item_details", fetch_production_order_items(self))


@frappe.whitelist()
def get_production_order_settings():
	"""Return active attributes, grid attribute, and dependent attribute config from YRP Settings."""
	settings = frappe.get_cached_doc("YRP Settings")
	attrs = []
	grid_attribute = None
	for row in settings.production_order_attributes or []:
		attrs.append(row.attribute)
		if row.is_grid_attribute:
			grid_attribute = row.attribute
	return {
		"attributes": attrs,
		"grid_attribute": grid_attribute,
		"dependent_attribute": settings.po_dependent_attribute or None,
		"dependent_attribute_value": settings.po_dependent_attribute_value or None,
	}


@frappe.whitelist()
def get_item_production_attributes(item):
	"""Return item's attribute values filtered to active Production Order attributes."""
	settings = get_production_order_settings()
	active_attrs = settings["attributes"]
	grid_attr = settings["grid_attribute"]

	if not active_attrs:
		frappe.throw(_("No attributes configured in YRP Settings > Production Order Settings"))

	item_details = get_attribute_details(item)
	all_item_attrs = list(item_details.get("attributes") or [])
	if item_details.get("primary_attribute"):
		all_item_attrs.append(item_details["primary_attribute"])

	# Get values for each active attribute that exists on this item
	matching_attrs = [a for a in active_attrs if a in all_item_attrs]
	if not matching_attrs:
		frappe.throw(
			_("Item {0} does not have any of the configured production order attributes").format(item)
		)

	attr_values = get_attribute_values(item, matching_attrs)

	# Determine grid attribute: configured > item's primary_attribute > first attribute
	item_grid_attr = None
	if grid_attr and grid_attr in matching_attrs:
		item_grid_attr = grid_attr
	elif item_details.get("primary_attribute") and item_details["primary_attribute"] in matching_attrs:
		item_grid_attr = item_details["primary_attribute"]
	elif matching_attrs:
		item_grid_attr = matching_attrs[0]

	grid_values = attr_values.get(item_grid_attr, []) if item_grid_attr else []
	row_attributes = {k: v for k, v in attr_values.items() if k != item_grid_attr}

	return {
		"item": item,
		"grid_attribute": item_grid_attr,
		"grid_attribute_values": grid_values,
		"row_attributes": row_attributes,
		"all_attributes": matching_attrs,
	}


def save_production_order_items(item_details_json):
	"""Convert Vue grouped JSON into flat production_order_details rows.

	Creates Item Variants using the dependent attribute from settings
	to satisfy the full attribute requirements.
	"""
	if isinstance(item_details_json, str):
		item_details = json.loads(item_details_json)
	else:
		item_details = item_details_json

	settings = get_production_order_settings()
	dep_attr = settings.get("dependent_attribute")
	dep_attr_value = settings.get("dependent_attribute_value")

	rows = []
	for group in item_details:
		item_name = group.get("item")
		if not item_name:
			continue

		item_doc = frappe.get_cached_doc("Item", item_name)

		for entry in group.get("entries", []):
			attr_args = dict(entry.get("attributes", {}))
			qty = flt(entry.get("qty", 0))
			if qty <= 0:
				continue

			# Add dependent attribute value if the item has one and it's configured in settings
			if item_doc.dependent_attribute and dep_attr and dep_attr_value:
				if item_doc.dependent_attribute == dep_attr and dep_attr not in attr_args:
					attr_args[dep_attr] = dep_attr_value

			variant_name = get_or_create_variant(item_name, attr_args)
			rows.append({
				"item": item_name,
				"item_variant": variant_name,
				"attributes_json": json.dumps(entry.get("attributes", {}), separators=(",", ":")),
				"quantity": qty,
			})
	return rows


def fetch_production_order_items(doc):
	"""Convert flat child table rows into grouped JSON for Vue."""
	settings = get_production_order_settings()
	active_attrs = settings.get("attributes", [])

	# Group rows by item template
	items_grouped = {}
	for row in doc.production_order_details:
		items_grouped.setdefault(row.item, []).append(row)

	result = []
	for item_name, rows in items_grouped.items():
		try:
			item_attr_info = get_item_production_attributes(item_name)
		except Exception:
			continue

		group = {
			"item": item_name,
			"grid_attribute": item_attr_info.get("grid_attribute"),
			"grid_attribute_values": item_attr_info.get("grid_attribute_values", []),
			"row_attributes": item_attr_info.get("row_attributes", {}),
			"entries": [],
		}
		for row in rows:
			if row.attributes_json:
				attr_map = json.loads(row.attributes_json)
			elif row.item_variant:
				# Fallback: read from variant attributes
				variant = frappe.get_cached_doc("Item Variant", row.item_variant)
				attr_map = {
					a.attribute: a.attribute_value
					for a in variant.attributes
					if a.attribute in active_attrs
				}
			else:
				attr_map = {}
			group["entries"].append({
				"attributes": attr_map,
				"qty": row.quantity,
			})
		result.append(group)
	return result


@frappe.whitelist()
def get_item_attribute_details(item):
	"""Return attribute config for an item — used by Vue to build the grid."""
	return get_attribute_details(item)


@frappe.whitelist()
def get_order_summary(production_order):
	"""Aggregate quantities by item and primary attribute value."""
	doc = frappe.get_doc("Production Order", production_order)
	summary = {}

	for row in doc.production_order_details:
		summary.setdefault(row.item, {})
		item_doc = frappe.get_cached_doc("Item", row.item)
		primary_attr = item_doc.primary_attribute
		attr_map = json.loads(row.attributes_json) if row.attributes_json else {}

		if primary_attr and primary_attr in attr_map:
			val = attr_map[primary_attr]
			summary[row.item].setdefault(val, 0)
			summary[row.item][val] += row.quantity
		elif row.item_variant:
			variant = frappe.get_cached_doc("Item Variant", row.item_variant)
			if primary_attr:
				for attr in variant.attributes:
					if attr.attribute == primary_attr:
						summary[row.item].setdefault(attr.attribute_value, 0)
						summary[row.item][attr.attribute_value] += row.quantity
						break
			else:
				summary[row.item].setdefault("_total", 0)
				summary[row.item]["_total"] += row.quantity
		else:
			summary[row.item].setdefault("_total", 0)
			summary[row.item]["_total"] += row.quantity

	return summary
