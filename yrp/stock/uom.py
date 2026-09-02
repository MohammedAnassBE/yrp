"""Authoritative Item UOM resolution for YRP transactions.

Transaction UOM is master data, not user input.  The Item supplies the stock
UOM and, when configured, the selected variant's dependent-attribute value
supplies the transaction UOM.  Every server controller should overwrite the
submitted UOM fields with the values returned here.
"""

import frappe
from frappe import _
from frappe.utils import flt


def resolve_item_uom(item_variant):
	"""Return master-derived UOM details for an Item Variant.

	The conversion factor always means transaction-UOM units to Item stock-UOM
	units (for example, Box -> Piece is 10).  Errors describe incomplete master
	configuration; a stale or manipulated client value is deliberately ignored.
	"""
	if not item_variant:
		return frappe._dict()

	parent_item = frappe.get_cached_value('YRP Item Variant', item_variant, "item")
	if not parent_item:
		frappe.throw(_("Item Variant {0} does not exist.").format(item_variant))

	item = frappe.get_cached_doc('YRP Item', parent_item)
	stock_uom = item.default_unit_of_measure
	if not stock_uom:
		frappe.throw(
			_("Item {0} has no Default Unit of Measure. Complete the Item master first.").format(
				parent_item
			)
		)

	transaction_uom = stock_uom
	if item.dependent_attribute:
		if not item.dependent_attribute_mapping:
			frappe.throw(
				_(
					"Item {0} has dependent attribute {1}, but no Dependent Attribute Mapping. "
					"Complete the Item master first."
				).format(parent_item, item.dependent_attribute)
			)

		attribute_value = frappe.db.get_value(
			'YRP Item Variant Attribute',
			{
				"parent": item_variant,
				"parenttype": 'YRP Item Variant',
				"attribute": item.dependent_attribute,
			},
			"attribute_value",
		)
		if not attribute_value:
			frappe.throw(
				_(
					"Item Variant {0} has no value for dependent attribute {1}. "
					"Complete the Item Variant master first."
				).format(item_variant, item.dependent_attribute)
			)

		mapping = frappe.get_cached_doc(
			'YRP Item Dependent Attribute Mapping', item.dependent_attribute_mapping
		)
		mapping_row = next(
			(row for row in mapping.get("details") or [] if row.attribute_value == attribute_value),
			None,
		)
		if not mapping_row or not mapping_row.uom:
			frappe.throw(
				_(
					"Item {0} has no UOM configured for {1} = {2}. "
					"Complete the Dependent Attribute Mapping first."
				).format(parent_item, item.dependent_attribute, attribute_value)
			)
		transaction_uom = mapping_row.uom

	conversion_factor = 1.0
	if transaction_uom != stock_uom:
		conversion_row = next(
			(
				row
				for row in item.get("uom_conversion_details") or []
				if row.uom == transaction_uom
			),
			None,
		)
		conversion_factor = flt(conversion_row.conversion_factor) if conversion_row else 0
		if conversion_factor <= 0:
			frappe.throw(
				_(
					"Item {0} uses UOM {1}, but it has no positive conversion to stock UOM {2}. "
					"Complete UOM Conversion Details first."
				).format(parent_item, transaction_uom, stock_uom)
			)

	return frappe._dict(
		item=parent_item,
		uom=transaction_uom,
		stock_uom=stock_uom,
		conversion_factor=conversion_factor,
		secondary_uom=item.secondary_unit_of_measure,
	)


def apply_item_uom(row, item_field="item_variant"):
	"""Overwrite supported UOM fields on a transaction child row."""
	item_variant = row.get(item_field)
	if not item_variant:
		return frappe._dict()

	details = resolve_item_uom(item_variant)
	for fieldname in ("uom", "stock_uom", "conversion_factor", "secondary_uom"):
		if row.meta.get_field(fieldname):
			row.set(fieldname, details.get(fieldname))
	return details


def apply_item_uoms(rows, item_field="item_variant"):
	"""Apply the Item master UOM rule to every populated row."""
	return [apply_item_uom(row, item_field=item_field) for row in rows or []]
