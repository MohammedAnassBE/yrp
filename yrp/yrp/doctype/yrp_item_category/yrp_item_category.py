"""Item Type -> Category -> Value tree; classification does not create variants."""
import frappe
from frappe.model.naming import append_number_if_name_exists
from frappe.utils.nestedset import NestedSet

from yrp.yrp_retail.category import (
	DOCTYPE, NODE_TYPES, PARENT_FIELD, lock_tree, node, root_for,
)
from yrp.yrp_retail.logic import require


class YRPItemCategory(NestedSet):
	nsm_parent_field = PARENT_FIELD

	def autoname(self):
		"""Use the label as ID; standard numeric suffixes distinguish other branches.

		Sibling labels remain unique through validate(). Existing identifiers are
		never changed by saving a label; native Rename preserves all document links.
		"""
		self._check_write_access()
		lock_tree()
		self.category_name = (self.category_name or "").strip()
		require(self.category_name, "Category Name is required.")
		self.name = append_number_if_name_exists(DOCTYPE, self.category_name)

	def validate(self):
		self._check_write_access()
		lock_tree()
		self.category_name = (self.category_name or "").strip()
		require(self.category_name, "Category Name is required.")
		duplicate = frappe.db.sql(
			"""select name from `tabYRP Item Category`
			where coalesce(parent_yrp_item_category, '')=%s and category_name=%s
			and name!=%s limit 1 for update""",
			(self.get(PARENT_FIELD) or "", self.category_name, self.name or ""),
		)
		require(not duplicate, "Classification names must be unique within the same parent.")
		require(self.node_type in NODE_TYPES, "Choose Item Type, Category or Value.")
		# Group/leaf status is determined by the level, never by a second user choice.
		# Tree requests also serialize Check values as strings (including "0").
		self.is_group = int(self.node_type != "Value")
		parent_name = self.get(PARENT_FIELD)
		parent = node(parent_name) if parent_name else None
		if self.node_type == "Item Type":
			require(not parent, "Item Types must be root nodes.")
		else:
			expected = "Item Type" if self.node_type == "Category" else "Category"
			require(parent, f"Select a parent {expected} for this {self.node_type}.")
			require(parent and parent.node_type == expected and parent.is_group,
				f"The parent of a {self.node_type} must be a {expected}.")
		old = self.get_doc_before_save()
		if old:
			for field in (PARENT_FIELD, "node_type", "is_group"):
				require((self.get(field) or "") == (old.get(field) or ""),
					"Existing classifications cannot change parent or level. Rename or disable them instead.")
		# NestedSet owns these fields; clients cannot forge indexes or move history.
		self.lft = old.lft if old else 0
		self.rgt = old.rgt if old else 0
		self.old_parent = old.get(PARENT_FIELD) if old else None

	def on_trash(self):
		self._check_write_access()
		require(False, "Classifications cannot be deleted. Disable them instead.")

	def before_rename(self, olddn, newdn, merge=False):
		self._check_write_access()
		lock_tree()
		root_for(olddn)
		require(not merge, "Classification nodes cannot be merged; rename or disable them instead.")
		super().before_rename(olddn, newdn, merge=False)

	def _check_write_access(self):
		from yrp.yrp_partner.permissions import is_partner
		if is_partner():
			frappe.throw("Partner users can only read item classifications.", frappe.PermissionError)


ItemCategory = YRPItemCategory


@frappe.whitelist(methods=["POST"])
def add_tree_node():
	"""Create the next level under the selected node using Frappe's tree hook.

	The native dialog supplies its selected parent, including the virtual root.
	Only the label and parent are inputs; normal insert permissions, hierarchy
	validation and NestedSet updates still apply.
	"""
	from frappe.desk.treeview import make_tree_args

	args = make_tree_args(**frappe.form_dict)
	frappe.has_permission(DOCTYPE, "create", throw=True)
	lock_tree()
	parent_name = args.get(PARENT_FIELD)
	if args.is_root:
		require(not parent_name, "Select All Item Types to create an Item Type.")
		kind = "Item Type"
	else:
		require(parent_name, "Select a parent in the classification tree.")
		parent = node(parent_name)
		parent.check_permission("read")
		kind = {"Item Type": "Category", "Category": "Value"}.get(parent.node_type)
		require(kind and parent.is_group, "Values cannot have children. Select an Item Type or Category.")
	return frappe.get_doc({
		"doctype": DOCTYPE,
		"category_name": args.category_name,
		"node_type": kind,
		PARENT_FIELD: parent_name or None,
	}).insert().name
