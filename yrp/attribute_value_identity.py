"""Attribute value master names: the value itself, or "<value> (<attribute>)" when another attribute owns it."""

import frappe

MASTER = "YRP Item Attribute Value"
CACHE_KEY = "attribute_value_names"


def scoped_name(attribute, value):
	return f"{value} ({attribute})"


def get_attribute_value_names(pairs):
	"""{(attribute, value): name} for existing masters, and the name a new master would take."""
	pairs = sorted({(str(attribute), str(value)) for attribute, value in pairs})
	if not pairs:
		return {}
	values = sorted({value for _attribute, value in pairs})
	existing = frappe.get_all(
		MASTER,
		filters={"attribute_value": ["in", values]},
		fields=["name", "attribute_name", "attribute_value"],
		order_by="creation asc, name asc",
	)
	names = {}
	for row in existing:
		names.setdefault((row.attribute_name, row.attribute_value), row.name)
	taken = {row.name.casefold() for row in existing}
	for attribute, value in pairs:
		if (attribute, value) in names:
			continue
		is_free = value.casefold() not in taken and not frappe.db.exists(MASTER, value)
		name = value if is_free else scoped_name(attribute, value)
		names[(attribute, value)] = name
		taken.add(name.casefold())
	return {pair: names[pair] for pair in pairs}


def attribute_value_name(attribute, value):
	cache = getattr(frappe.local, CACHE_KEY, None)
	if cache is None:
		cache = frappe.local.attribute_value_names = {}
	pair = (str(attribute), str(value))
	if pair not in cache:
		cache[pair] = get_attribute_value_names([pair])[pair]
	return cache[pair]


def clear_name_cache():
	for key in (CACHE_KEY, "attribute_link_names", "attribute_link_values", "attribute_value_aliases"):
		if hasattr(frappe.local, key):
			delattr(frappe.local, key)
