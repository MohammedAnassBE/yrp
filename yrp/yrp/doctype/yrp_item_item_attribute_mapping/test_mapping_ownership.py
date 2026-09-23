"""Synthetic mapping owners; every test rolls its entire transaction back."""

import secrets
import unittest

import frappe

from yrp.yrp.doctype.yrp_item_item_attribute_mapping.ownership import MAPPING
from yrp.yrp_retail.doctype.yrp_product.yrp_product import get_template_defaults


class TestMappingOwnership(unittest.TestCase):
	def setUp(self):
		self.savepoint = "mapping_owner_" + frappe.generate_hash(length=10)
		frappe.db.savepoint(self.savepoint)
		self.addCleanup(frappe.db.rollback, save_point=self.savepoint)
		self.attribute = frappe.get_doc({
			"doctype": "Item Attribute", "attribute_name": self.label("Attribute"),
			"item_attribute_values": [
				{"attribute_value": "Alpha", "abbr": "A"},
				{"attribute_value": "Beta", "abbr": "B"},
			],
		}).insert()
		self.uom = frappe.get_doc({"doctype": "UOM", "uom_name": self.label("Unit")}).insert()
		self.hsn = frappe.get_doc({
			"doctype": "GST HSN Code", "hsn_code": str(secrets.randbelow(90_000_000) + 10_000_000),
			"description": "Fictional mapping ownership test",
		}).insert()
		self.template = frappe.get_doc({
			"doctype": "YRP Item Master Template", "name": self.label("Template"),
			"default_unit_of_measure": self.uom.name,
			"attributes": [{"attribute": self.attribute.name}],
		}).insert()
		self.template_mapping = self.template.attributes[0].mapping
		mapping = frappe.get_doc(MAPPING, self.template_mapping)
		mapping.append("values", {"attribute_value": "Alpha"})
		mapping.save()

	def label(self, kind):
		return "Test Mapping " + kind + " " + frappe.generate_hash(length=10)

	def product(self):
		return frappe.get_doc({
			"doctype": "YRP Product", "product_name": self.label("Product"),
			"item_template": self.template.name, "gst_hsn_code": self.hsn.name,
			**get_template_defaults(self.template.name),
		}).insert()

	def values(self, mapping):
		return [row.attribute_value for row in frappe.get_doc(MAPPING, mapping).get("values")]

	def test_product_has_private_values_and_stable_mapping_on_save(self):
		product = self.product()
		private = product.attributes[0].mapping
		self.assertNotEqual(private, self.template_mapping)
		self.assertEqual(self.values(private), ["Alpha"])
		mapping = frappe.get_doc(MAPPING, private)
		mapping.set("values", [{"attribute_value": "Beta"}])
		mapping.save()
		self.assertEqual(self.values(self.template_mapping), ["Alpha"])
		product.save()
		self.template.save()
		self.assertEqual(product.attributes[0].mapping, private)
		self.assertEqual(self.template.attributes[0].mapping, self.template_mapping)

	def test_reassigning_template_mapping_copies_and_removes_replaced_map(self):
		product = self.product()
		previous = product.attributes[0].mapping
		product.attributes[0].mapping = self.template_mapping
		product.save()
		self.assertNotEqual(product.attributes[0].mapping, self.template_mapping)
		self.assertNotEqual(product.attributes[0].mapping, previous)
		self.assertFalse(frappe.db.exists(MAPPING, previous))
		self.assertTrue(frappe.db.exists(MAPPING, self.template_mapping))

	def test_removing_last_owner_reference_deletes_map_and_its_values(self):
		product = self.product()
		private = product.attributes[0].mapping
		product.set("attributes", [])
		product.save()
		self.assertFalse(frappe.db.exists(MAPPING, private))
		self.assertFalse(frappe.db.exists("YRP Item Item Attribute Mapping Value", {"parent": private}))
		self.assertTrue(frappe.db.exists(MAPPING, self.template_mapping))

	def test_parent_deletion_cleans_only_its_private_maps(self):
		product = self.product()
		private = product.attributes[0].mapping
		frappe.delete_doc("YRP Product", product.name)
		self.assertFalse(frappe.db.exists(MAPPING, private))
		self.assertTrue(frappe.db.exists(MAPPING, self.template_mapping))
		frappe.delete_doc("YRP Item Master Template", self.template.name)
		self.assertFalse(frappe.db.exists(MAPPING, self.template_mapping))

	def test_consumed_standalone_map_is_cleaned_without_sweeping_other_maps(self):
		standalone = frappe.get_doc({"doctype": MAPPING, "attribute_name": self.attribute.name}).insert()
		consumed = frappe.get_doc({"doctype": MAPPING, "attribute_name": self.attribute.name,
			"values": [{"attribute_value": "Beta"}]}).insert()
		self.template.attributes[0].mapping = consumed.name
		self.template.save()
		self.assertNotEqual(self.template.attributes[0].mapping, consumed.name)
		self.assertEqual(self.values(self.template.attributes[0].mapping), ["Beta"])
		self.assertFalse(frappe.db.exists(MAPPING, consumed.name))
		self.assertFalse(frappe.db.exists(MAPPING, self.template_mapping))
		self.assertTrue(frappe.db.exists(MAPPING, standalone.name))

	def test_repair_patch_is_idempotent_and_preserves_values(self):
		from yrp.patches.separate_template_product_attribute_mappings import separate_owner

		product = self.product()
		# Represent an existing row written before owner snapshots were introduced.
		frappe.db.set_value("YRP Item Item Attribute", product.attributes[0].name, "mapping", self.template_mapping)
		self.assertTrue(separate_owner("YRP Product", product.name))
		product.reload()
		self.assertNotEqual(product.attributes[0].mapping, self.template_mapping)
		self.assertEqual(self.values(product.attributes[0].mapping), ["Alpha"])
		self.assertTrue(frappe.db.exists(MAPPING, self.template_mapping))
		modified = product.modified
		self.assertFalse(separate_owner("YRP Product", product.name))
		product.reload()
		self.assertEqual(product.modified, modified)

	def test_native_item_reference_keeps_detached_template_mapping(self):
		from yrp.yrp.doctype.yrp_item_master_template.yrp_item_master_template import create_item_from_template

		group = frappe.get_doc({"doctype": "Item Group", "item_group_name": self.label("Group"),
			"parent_item_group": frappe.db.get_value("Item Group", {"lft": 1}, "name")}).insert()
		name = create_item_from_template(self.template.name, self.label("Item"), group.name, self.hsn.name)
		item = frappe.get_doc("Item", name)
		# A native Item can retain a historical mapping shared with its template.
		item.attributes[0].mapping = self.template_mapping
		item.save()
		self.template.set("attributes", [])
		self.template.save()
		item.reload()
		self.assertEqual(item.attributes[0].mapping, self.template_mapping)
		self.assertTrue(frappe.db.exists(MAPPING, self.template_mapping))

	def test_mapping_cleanup_rolls_back_with_owner_changes(self):
		point = "mapping_unlink_" + frappe.generate_hash(length=10)
		frappe.db.savepoint(point)
		self.template.set("attributes", [])
		self.template.save()
		self.assertFalse(frappe.db.exists(MAPPING, self.template_mapping))
		frappe.db.rollback(save_point=point)
		self.template.reload()
		self.assertEqual(self.template.attributes[0].mapping, self.template_mapping)
		self.assertTrue(frappe.db.exists(MAPPING, self.template_mapping))
