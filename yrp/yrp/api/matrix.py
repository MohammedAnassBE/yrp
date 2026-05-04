"""IPD Process Matrix API helpers."""

from itertools import product

import frappe


@frappe.whitelist()
def generate_cross_product(ipd, input_attributes=None, output_attributes=None, input_item=None):
	"""Return the cross-product of attribute values for input and output sides.

	Output values come from the IPD's parent Item; input values come from `input_item`
	if given (e.g. Cutting input is fabric), else also from the IPD's item.

	Args:
	    ipd: name of Item Production Detail.
	    input_attributes: list of Item Attribute names on the input side.
	    output_attributes: list of Item Attribute names on the output side.
	    input_item: optional Item name for the input side (for item-conversion processes).

	Returns dict {input: [{attrs: [{attribute, attribute_value}, ...]}, ...], output: [...]}.
	"""
	input_attributes = frappe.parse_json(input_attributes) or []
	output_attributes = frappe.parse_json(output_attributes) or []
	ipd_item = frappe.db.get_value("Item Production Detail", ipd, "item")
	if not ipd_item:
		frappe.throw(f"IPD {ipd} has no item set.")

	output_values = _attribute_values_for_item(ipd_item)
	input_values = _attribute_values_for_item(input_item) if input_item and input_item != ipd_item else output_values

	def cross(attrs, values_by_attr):
		if not attrs:
			return []
		picked = [[(a, v) for v in values_by_attr.get(a, [])] for a in attrs]
		if any(len(p) == 0 for p in picked):
			missing = [a for a, vs in zip(attrs, picked) if len(vs) == 0]
			frappe.throw(f"No values found for attributes: {missing}")
		out = []
		for combo in product(*picked):
			out.append({"attrs": [{"attribute": a, "attribute_value": v} for (a, v) in combo]})
		return out

	return {"input": cross(input_attributes, input_values), "output": cross(output_attributes, output_values)}


def _attribute_values_for_item(item):
	"""Return {attribute: [value, ...]} for an Item.

	Resolution: Item.attributes (child table → Item Item Attribute) carries a
	`mapping` Link field per row pointing to an `Item Item Attribute Mapping` doc;
	that mapping's `values` child table holds the attribute_value rows.
	"""
	item_doc = frappe.get_doc("Item", item)
	values = {}
	for attr_row in item_doc.get("attributes") or []:
		if not attr_row.mapping:
			continue
		mapping = frappe.get_doc("Item Item Attribute Mapping", attr_row.mapping)
		values[attr_row.attribute] = [v.attribute_value for v in mapping.values]
	return values


@frappe.whitelist()
def get_attribute_values(ipd, attribute):
	"""Return list of legal values for one attribute on the IPD's item."""
	item = frappe.db.get_value("Item Production Detail", ipd, "item")
	if not item:
		return []
	return _attribute_values_for_item(item).get(attribute, [])


@frappe.whitelist()
def get_attribute_values_bulk(ipd, attributes):
	"""Return {attribute: [values]} for the requested attributes on the IPD's item."""
	attributes = frappe.parse_json(attributes) or []
	item = frappe.db.get_value("Item Production Detail", ipd, "item")
	if not item:
		return {}
	all_values = _attribute_values_for_item(item)
	return {a: all_values.get(a, []) for a in attributes}


@frappe.whitelist()
def get_matrix_attribute_values(ipd, input_attributes=None, output_attributes=None, input_item=None):
	"""Return side-keyed attribute values for an IPD Process Matrix editor load.

	Output attributes resolve against the IPD's main item. Input attributes resolve
	against `input_item` if provided (item-conversion case), else against the IPD's item.

	Returns: {"input": {attr: [vals]}, "output": {attr: [vals]}}.
	"""
	input_attributes = frappe.parse_json(input_attributes) or []
	output_attributes = frappe.parse_json(output_attributes) or []
	ipd_item = frappe.db.get_value("Item Production Detail", ipd, "item")
	if not ipd_item:
		return {"input": {}, "output": {}}

	output_all = _attribute_values_for_item(ipd_item)
	if input_item and input_item != ipd_item:
		input_all = _attribute_values_for_item(input_item)
	else:
		input_all = output_all

	return {
		"input": {a: input_all.get(a, []) for a in input_attributes},
		"output": {a: output_all.get(a, []) for a in output_attributes},
	}
