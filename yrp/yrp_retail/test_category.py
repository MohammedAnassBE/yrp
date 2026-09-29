"""Fictional classification trees and Items; each test rolls back its records."""
import secrets
import unittest
from unittest.mock import patch

import frappe

from yrp.yrp_retail.category import DOCTYPE, PARENT_FIELD, validate_template_classification


class TestItemCategory(unittest.TestCase):
	def setUp(self):
		self.point = "category_" + frappe.generate_hash(length=10)
		frappe.db.savepoint(self.point)
		self.addCleanup(frappe.db.rollback, save_point=self.point)
		self.root, self.category, self.value = self.tree()
		self.uom = frappe.get_doc({"doctype": "UOM", "uom_name": self.label("Unit")}).insert()
		self.group = frappe.get_doc({"doctype": "Item Group", "item_group_name": self.label("Group"),
			"parent_item_group": frappe.db.get_value("Item Group", {"lft": 1}, "name")}).insert()
		self.hsn = None
		if frappe.get_meta("Item").has_field("gst_hsn_code"):
			self.hsn = frappe.get_doc({"doctype": "GST HSN Code", "hsn_code": str(secrets.randbelow(90_000_000) + 10_000_000),
				"description": "Fictional classification test"}).insert().name

	def label(self, label):
		return "Test " + label + " " + frappe.generate_hash(length=10)

	def node(self, kind, parent=None, **kwargs):
		return frappe.get_doc({"doctype": DOCTYPE, "category_name": self.label(kind), "node_type": kind,
			PARENT_FIELD: parent, "is_group": int(kind != "Value"), **kwargs}).insert()

	def tree(self):
		root = self.node("Item Type")
		category = self.node("Category", root.name)
		return root, category, self.node("Value", category.name)

	def tree_request(self, parent, is_root, **values):
		"""Submit the string-valued payload produced by Frappe's tree dialog."""
		from yrp.yrp.doctype.yrp_item_category.yrp_item_category import add_tree_node

		label = self.label("Tree Dialog")
		args = frappe._dict({
			"doctype": DOCTYPE, "category_name": label,
			"parent": parent, "is_root": is_root, **values,
		})
		with patch.object(frappe, "form_dict", args):
			add_tree_node()
		return frappe.get_doc(DOCTYPE, {"category_name": label})

	def item(self, **kwargs):
		return frappe.get_doc({"doctype": "Item", "item_code": self.label("Item"), "item_name": "Fictional Classified Item",
			"item_group": self.group.name, "stock_uom": self.uom.name, "is_stock_item": 0,
			"gst_hsn_code": self.hsn, "yrp_item_type": self.root.name,
			"yrp_categories": [{"category": self.category.name, "value": self.value.name}], **kwargs})

	def test_three_levels_and_multiple_roots(self):
		other, _, _ = self.tree()
		self.assertNotEqual(other.name, self.root.name)
		for kind, parent in (("Category", None), ("Value", None), ("Value", self.root.name),
			("Item Type", self.root.name), ("Category", self.category.name), ("Value", self.value.name)):
			with self.assertRaises(frappe.ValidationError):
				self.node(kind, parent)

	def test_group_flag_is_derived_from_node_type(self):
		root = self.node("Item Type", is_group=0)
		category = self.node("Category", root.name, is_group=0)
		value = self.node("Value", category.name, is_group=1)
		self.assertTrue(root.is_group)
		self.assertTrue(category.is_group)
		self.assertFalse(value.is_group)
		value.is_group = 1
		value.save()
		self.assertFalse(value.is_group)

	def test_tree_dialog_creates_root_category_and_value(self):
		root = self.tree_request(DOCTYPE, "true", node_type="Value", is_group="0", disabled="1")
		category = self.tree_request(root.name, "false", node_type="Item Type", is_group="0")
		value = self.tree_request(category.name, "false", node_type="Category", is_group="1")
		self.assertEqual(root.node_type, "Item Type")
		self.assertFalse(root.get(PARENT_FIELD))
		self.assertTrue(root.is_group)
		self.assertFalse(root.disabled)
		self.assertEqual(category.node_type, "Category")
		self.assertEqual(category.get(PARENT_FIELD), root.name)
		self.assertTrue(category.is_group)
		self.assertEqual(value.node_type, "Value")
		self.assertEqual(value.get(PARENT_FIELD), category.name)
		self.assertFalse(value.is_group)

	def test_tree_dialog_cannot_add_below_a_value(self):
		with self.assertRaises(frappe.ValidationError):
			self.tree_request(self.value.name, "false")
		self.assertFalse(frappe.db.exists(DOCTYPE, {PARENT_FIELD: self.value.name}))

	def test_tree_dialog_requires_parent_or_explicit_root(self):
		for parent in ("", DOCTYPE):
			with self.assertRaises(frappe.ValidationError):
				self.tree_request(parent, "false")

	def test_new_category_names_use_labels(self):
		for doc in (self.root, self.category, self.value):
			self.assertEqual(doc.name, doc.category_name)
		label = self.label("Trimmed Item Type")
		root = self.node("Item Type", category_name="  " + label + "  ")
		self.assertEqual(root.name, label)
		self.assertEqual(root.category_name, label)

	def test_duplicate_branch_labels_get_readable_unique_names(self):
		other_root = self.node("Item Type")
		category = self.node("Category", other_root.name, category_name=self.category.category_name)
		value = self.node("Value", category.name, category_name=self.value.category_name)
		self.assertEqual(category.category_name, self.category.category_name)
		self.assertEqual(category.name, self.category.name + "-1")
		self.assertEqual(value.category_name, self.value.category_name)
		self.assertEqual(value.name, self.value.name + "-1")
		with self.assertRaises(frappe.ValidationError):
			self.node("Category", other_root.name, category_name=category.category_name)
		category.reload()
		category.category_name = self.label("New display label")
		original_name = category.name
		category.save()
		self.assertEqual(category.name, original_name)

	def test_labels_are_unique_among_siblings_only(self):
		other_root = self.node("Item Type")
		other_category = self.node("Category", other_root.name, category_name=self.category.category_name)
		other_value = self.node("Value", other_category.name, category_name=self.value.category_name)
		self.assertNotEqual(other_category.name, self.category.name)
		self.assertNotEqual(other_value.name, self.value.name)
		for kind, parent, label in (("Item Type", None, self.root.category_name),
			("Category", self.root.name, self.category.category_name),
			("Value", self.category.name, self.value.category_name)):
			with self.assertRaises(frappe.ValidationError):
				self.node(kind, parent, category_name=label)
		sibling = self.node("Value", self.category.name)
		sibling.category_name = self.value.category_name
		with self.assertRaises(frappe.ValidationError):
			sibling.save()

	def test_item_assignment_and_wrong_branches(self):
		other_root, other_category, other_value = self.tree()
		for changes in (
			{"yrp_item_type": None}, {"yrp_item_type": self.category.name}, {"yrp_item_type": other_root.name},
			{"yrp_categories": [{"category": self.category.name, "value": other_value.name}]},
			{"yrp_categories": [{"category": other_category.name, "value": other_value.name}]},
			{"yrp_categories": [{"category": self.category.name, "value": self.value.name}] * 2},
		):
			with self.assertRaises(frappe.ValidationError):
				self.item(**changes).insert()
		item = self.item().insert()
		self.assertEqual(item.yrp_categories[0].value, self.value.name)

	def test_used_tree_accepts_new_categories_and_values(self):
		self.item().insert()
		category = self.node("Category", self.root.name)
		self.assertTrue(self.node("Value", category.name).name)
		self.assertTrue(self.node("Value", self.category.name).name)

	def test_nodes_cannot_be_deleted_or_moved_even_before_use(self):
		other = self.node("Item Type")
		for doc in (self.root, self.category, self.value):
			with self.assertRaises(frappe.ValidationError): doc.on_trash()
		self.category.set(PARENT_FIELD, other.name)
		with self.assertRaises(frappe.ValidationError): self.category.save()
		self.category.reload()
		self.category.node_type = 'Item Type'
		self.category.set(PARENT_FIELD, None)
		with self.assertRaises(frappe.ValidationError): self.category.save()

	def test_labels_and_disabling_remain_allowed(self):
		item = self.item().insert()
		for doc in (self.root, self.category, self.value):
			doc.reload()
			doc.category_name = self.label("Renamed label")
			doc.disabled = 1
			doc.save()
		item.item_name = "Existing assignment survives disabling"
		item.save()
		with self.assertRaises(frappe.ValidationError):
			self.item().insert()

	def test_disabled_category_cannot_receive_a_new_value_assignment(self):
		second = self.node("Value", self.category.name)
		item = self.item().insert()
		self.category.reload()
		self.category.disabled = 1
		self.category.save()
		item.yrp_categories[0].value = second.name
		with self.assertRaises(frappe.ValidationError):
			item.save()

	def test_template_prefill_lists_categories_not_arbitrary_values(self):
		from yrp.yrp_retail.category import get_template_categories
		self.item().insert()
		new_category = self.node('Category', self.root.name)
		rows = get_template_categories(self.root.name)
		self.assertEqual({r.name for r in rows}, {self.category.name,new_category.name})
		self.assertTrue(all('value' not in row for row in rows))
		new_category.disabled=1
		new_category.save()
		self.assertEqual([r.name for r in get_template_categories(self.root.name)], [self.category.name])

	def test_template_assignments_validate_without_blocking_additions(self):
		template = frappe.get_doc({"doctype": "YRP Item Master Template", "item_type": self.root.name,
			"categories": [{"category": self.category.name, "value": self.value.name}]})
		validate_template_classification(template)
		self.assertTrue(self.node("Value", self.category.name).name)
		template.categories[0].value = self.category.name
		with self.assertRaises(frappe.ValidationError):
			validate_template_classification(template)

	def test_regular_rename_updates_assignments_but_merge_is_rejected(self):
		item = self.item().insert()
		new_name = self.label("Renamed Value")
		frappe.rename_doc(DOCTYPE, self.value.name, new_name, force=True)
		item.reload()
		self.assertEqual(item.yrp_categories[0].value, new_name)
		_, _, other_value = self.tree()
		with self.assertRaises(frappe.ValidationError):
			frappe.rename_doc(DOCTYPE, new_name, other_value.name, merge=True, force=True)

	def test_partner_with_manager_role_stays_read_only(self):
		user = frappe.get_doc({'doctype':'User','email':'test-category-'+frappe.generate_hash(length=10)+'@example.invalid',
			'first_name':'Fictional Catalog Reader','send_welcome_email':0,
			'roles':[{'role':'YRP Partner'},{'role':'YRP Partner Manager'}]}).insert()
		old_user = frappe.session.user
		try:
			frappe.set_user(user.name)
			self.assertIn(self.root.name,frappe.get_list(DOCTYPE,pluck='name'))
			self.root.disabled=1
			with self.assertRaises(frappe.PermissionError):self.root.save(ignore_permissions=True)
		finally:frappe.set_user(old_user)
