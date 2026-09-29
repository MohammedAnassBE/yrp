"""Three-level retail classification, independent of variant attributes.

All assignment and structural writers share a taxonomy mutex and root locks.
Current reads serialize additions, renames and assignment validation.
Existing exact assignments remain valid when their nodes are later disabled.
"""
import frappe

from yrp.yrp_retail.logic import require

DOCTYPE = "YRP Item Category"
PARENT_FIELD = "parent_yrp_item_category"
NODE_TYPES = ("Item Type", "Category", "Value")


def lock_tree():
	"""A stable mutex also protects root discovery and NestedSet index changes."""
	frappe.db.sql("select name from `tabDocType` where name=%s for update", DOCTYPE)


def node(name):
	return frappe.get_doc(DOCTYPE, name, for_update=True)


def root_for(name):
	"""Resolve and lock the current root without relying on nested-set snapshots."""
	seen = set()
	while name:
		require(name not in seen, "Item classification cannot contain a parent cycle.")
		seen.add(name)
		doc = node(name)
		if not doc.get(PARENT_FIELD):
			return doc
		name = doc.get(PARENT_FIELD)
	frappe.throw("Item classification requires an Item Type root.")


def validate_item_classification(doc, method=None):
	validate_classification(doc, "yrp_item_type", "yrp_categories")


def validate_template_classification(doc, method=None):
	validate_classification(doc, "item_type", "categories")


def validate_classification(doc, type_field, table_field):
	"""Validate an optional root and one Value per Category under that root."""
	root_name = doc.get(type_field)
	rows = doc.get(table_field) or []
	old = doc.get_doc_before_save()
	old_root = old.get(type_field) if old else None
	if not root_name and not rows and not old_root:
		return
	lock_tree()
	# Removing the last assignment must serialize with structural checks too.
	for name in sorted({name for name in (root_name, old_root) if name}):
		node(name)
	if not root_name:
		require(not rows, "Select an Item Type before assigning Categories.")
		return
	root = node(root_name)
	require(root.node_type == "Item Type" and not root.get(PARENT_FIELD) and root.is_group,
		"Item Type must be an Item Type root.")
	unchanged_root = old_root == root_name
	if not unchanged_root:
		require(not root.disabled, "Disabled Item Types cannot receive new assignments.")
	old_pairs = {(row.category, row.value) for row in old.get(table_field) or []} if old and unchanged_root else set()
	seen = set()
	for row in rows:
		require(row.category and row.value, "Each classification requires a Category and Value.")
		require(row.category not in seen, "A Category can only be assigned once.")
		seen.add(row.category)
		category = node(row.category)
		value = node(row.value)
		require(category.node_type == "Category" and category.is_group and category.get(PARENT_FIELD) == root_name,
			"Category must belong directly to the selected Item Type.")
		require(value.node_type == "Value" and not value.is_group and value.get(PARENT_FIELD) == category.name,
			"Value must belong directly to the selected Category.")
		if (row.category, row.value) not in old_pairs:
			require(not root.disabled and not category.disabled and not value.disabled,
				"Disabled classification nodes cannot receive new assignments.")


@frappe.whitelist()
def get_template_categories(item_type):
	"""Prefill category rows, leaving each value for the user to select."""
	root = frappe.get_doc(DOCTYPE, item_type)
	root.check_permission('read')
	require(root.node_type == 'Item Type' and not root.disabled,
		'Select an enabled Item Type.')
	return frappe.get_list(DOCTYPE, filters={PARENT_FIELD:item_type, 'node_type':'Category', 'disabled':0},
		fields=['name','category_name'], order_by='category_name', limit_page_length=0)
