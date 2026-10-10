"""YRP Link identities at storage boundaries; actual values at business boundaries."""

import copy

import frappe
from frappe import _
from frappe.utils import cstr

from yrp.attribute_value_identity import attribute_value_name

MASTER = "YRP Item Attribute Value"
MAPPING = "YRP Item Item Attribute Mapping"


def ensure_value_master(attribute, value):
	"""Project an existing ERPNext value into a linkable master, without changing it."""
	value = cstr(value)
	if not attribute or not value:
		frappe.throw(_("Attribute and value are required."))
	name = attribute_value_name(attribute, value)
	if frappe.db.exists(MASTER, name):
		return name
	attribute_doc = frappe.get_cached_doc("Item Attribute", attribute)
	if attribute_doc.numeric_values:
		from erpnext.controllers.item_variant import validate_is_incremental

		validate_is_incremental(attribute_doc, attribute, value, attribute)
	else:
		actual = frappe.db.get_value(
			"Item Attribute Value",
			{
				"parent": attribute,
				"parenttype": "Item Attribute",
				"parentfield": "item_attribute_values",
				"attribute_value": value,
			},
			"attribute_value",
		)
		if actual != value:
			frappe.throw(_("Value {0} does not belong to attribute {1}.").format(value, attribute))
	doc = frappe.get_doc({"doctype": MASTER, "attribute_name": attribute, "attribute_value": value})
	doc.flags.from_item_attribute = True
	doc.insert(ignore_permissions=True, ignore_if_duplicate=True)
	return name


def normalize_mapping(doc):
	"""Normalize internal plain-value writes and validate Link ownership on saves."""
	for row in doc.get("values") or []:
		value = row.get("attribute_value")
		if not value:
			continue
		linked = frappe.db.get_value(MASTER, value, ["attribute_name", "attribute_value"], as_dict=True)
		if linked:
			if linked.attribute_name != doc.attribute_name:
				frappe.throw(
					_("Attribute Value {0} belongs to {1}, not {2}.").format(
						value, linked.attribute_name, doc.attribute_name
					)
				)
		else:
			row.attribute_value = ensure_value_master(doc.attribute_name, value)


def get_mapping_document(name, *, cached=False):
	"""Return a detached mapping with actual values for existing business APIs.

	Never mutate Frappe's cached document. The mapping controller translates this
	view back to Links if an internal workflow saves it.
	"""
	doc = (frappe.get_cached_doc if cached else frappe.get_doc)(MAPPING, name)
	data = copy.deepcopy(doc.as_dict())
	links = [row.get("attribute_value") for row in data.get("values") or []]
	values = (
		{
			row.name: row
			for row in frappe.get_all(
				MASTER,
				filters={"name": ["in", links]},
				fields=["name", "attribute_name", "attribute_value"],
				limit_page_length=0,
			)
		}
		if links
		else {}
	)
	for row in data.get("values") or []:
		if not row.get("attribute_value"):
			continue
		linked = values.get(row.get("attribute_value"))
		if not linked or linked.attribute_name != doc.attribute_name:
			frappe.throw(_("Mapping {0} has an invalid attribute-value Link.").format(name))
		row["attribute_value"] = linked.attribute_value
	return frappe.get_doc(data)


def get_mapping_values(name):
	return [
		row.attribute_value
		for row in get_mapping_document(name, cached=True).get("values") or []
		if row.attribute_value
	]


def sync_attribute_value_masters(doc, method=None):
	if not frappe.db.exists("DocType", MASTER):
		return
	if doc.numeric_values:
		from erpnext.controllers.item_variant import validate_is_incremental

		for value in frappe.get_all(MASTER, filters={"attribute_name": doc.name}, pluck="attribute_value"):
			validate_is_incremental(doc, doc.name, value, doc.name)
		return
	current = {cstr(row.attribute_value) for row in doc.get("item_attribute_values") or []}
	for value in current:
		ensure_value_master(doc.name, value)
	for row in frappe.get_all(
		MASTER, filters={"attribute_name": doc.name}, fields=["name", "attribute_value"]
	):
		if row.attribute_value not in current:
			# Normal link validation blocks removal while any mapping uses it.
			frappe.delete_doc(MASTER, row.name, ignore_permissions=True)


def rename_attribute_value_masters(doc, method, old, new, merge=False):
	for row in frappe.get_all(MASTER, filters={"attribute_name": new}, fields=["name", "attribute_value"]):
		expected = attribute_value_name(new, row.attribute_value)
		if row.name != expected:
			frappe.rename_doc(
				MASTER,
				row.name,
				expected,
				force=True,
				merge=bool(merge and frappe.db.exists(MASTER, expected)),
			)


def verify_attribute_value_links():
	"""Independent SQL audit of mapping ownership and native value membership."""
	invalid = frappe.db.sql("""SELECT v.name
		FROM `tabYRP Item Item Attribute Mapping Value` v
		JOIN `tabYRP Item Item Attribute Mapping` m ON m.name=v.parent
		LEFT JOIN `tabYRP Item Attribute Value` a ON a.name=v.attribute_value
		WHERE COALESCE(v.attribute_value,'')<>''
		AND (a.name IS NULL OR NOT (a.attribute_name <=> m.attribute_name)) LIMIT 20""")
	missing = frappe.db.sql("""SELECT a.name
		FROM `tabYRP Item Attribute Value` a
		LEFT JOIN `tabItem Attribute Value` n ON n.parent=a.attribute_name
		AND n.parenttype='Item Attribute' AND n.parentfield='item_attribute_values'
		AND n.attribute_value=a.attribute_value
		WHERE n.name IS NULL AND NOT EXISTS (SELECT 1 FROM `tabItem Attribute` t
		WHERE t.name=a.attribute_name AND t.numeric_values=1) LIMIT 20""")
	failures = [f"Invalid mapping Link {row[0]}" for row in invalid] + [
		f"YRP attribute value missing in ERPNext: {row[0]}" for row in missing
	]
	for row in frappe.get_all(
		MASTER, fields=["name", "attribute_name", "attribute_value"], limit_page_length=0
	):
		if row.name != attribute_value_name(row.attribute_name, row.attribute_value):
			failures.append(f"Attribute value identity mismatch: {row.name}")
	return failures
