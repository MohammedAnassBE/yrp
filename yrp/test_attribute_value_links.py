"""Integration tests: Link storage, native ERPNext domain and semantic readers."""

import unittest
from unittest.mock import patch

import frappe

from yrp.attribute_value_identity import clear_name_cache, get_attribute_value_names
from yrp.attribute_values import (
	MASTER,
	attribute_value_name,
	get_mapping_document,
	get_mapping_values,
)


class AttributeValueLinksTest(unittest.TestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		self.point = "attribute_link_test"
		frappe.db.savepoint(self.point)
		self.attribute = "_LinkTest-" + frappe.generate_hash(length=8)
		self.value = "XL " + frappe.generate_hash(length=8)
		self.native = frappe.get_doc(
			{
				"doctype": "Item Attribute",
				"attribute_name": self.attribute,
				"item_attribute_values": [{"attribute_value": self.value, "abbr": "XL"}],
			}
		).insert()
		self.link = attribute_value_name(self.attribute, self.value)

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.db.rollback(save_point=self.point)
		for dt in ["Item Attribute", MASTER, "YRP Item Item Attribute Mapping"]:
			frappe.clear_document_cache(dt)

	def mapping(self, value=None):
		return frappe.get_doc(
			{
				"doctype": "YRP Item Item Attribute Mapping",
				"attribute_name": self.attribute,
				"values": [{"attribute_value": value or self.link}],
			}
		).insert()

	def test_native_insert_creates_linkable_master(self):
		self.assertEqual(frappe.db.get_value(MASTER, self.link, "attribute_value"), self.value)

	def test_link_storage_and_plain_value_api_do_not_mutate_cache(self):
		doc = self.mapping()
		self.assertEqual(frappe.get_cached_doc(doc.doctype, doc.name).values[0].attribute_value, self.link)
		self.assertEqual(get_mapping_values(doc.name), [self.value])
		self.assertEqual(frappe.get_cached_doc(doc.doctype, doc.name).values[0].attribute_value, self.link)
		view = get_mapping_document(doc.name)
		view.save()
		self.assertEqual(
			frappe.db.get_value(
				"YRP Item Item Attribute Mapping Value", view.values[0].name, "attribute_value"
			),
			self.link,
		)

	def test_legacy_internal_text_write_is_normalized(self):
		doc = self.mapping(self.value)
		self.assertEqual(doc.values[0].attribute_value, self.link)

	def test_same_value_in_other_attribute_has_distinct_identity(self):
		other = frappe.get_doc(
			{
				"doctype": "Item Attribute",
				"attribute_name": self.attribute + "-Other",
				"item_attribute_values": [{"attribute_value": self.value, "abbr": "XL"}],
			}
		).insert()
		other_link = attribute_value_name(other.name, self.value)
		self.assertNotEqual(self.link, other_link)
		with self.assertRaises(frappe.ValidationError):
			self.mapping(other_link)

	def test_plain_value_named_like_another_attributes_legacy_master_resolves_to_own_value(self):
		from yrp.attribute_links import link

		other = frappe.get_doc(
			{
				"doctype": "Item Attribute",
				"attribute_name": self.attribute + "-Legacy",
				"item_attribute_values": [{"attribute_value": self.value, "abbr": "XL"}],
			}
		).insert()
		legacy = attribute_value_name(other.name, self.value)
		frappe.db.sql(f"update `tab{MASTER}` set name=%s where name=%s", (self.value, legacy))
		frappe.local.attribute_link_names = {}
		self.assertEqual(link(self.value, self.attribute), self.link)
		self.assertEqual(self.mapping(self.value).values[0].attribute_value, self.link)
		with self.assertRaises(frappe.ValidationError):
			link(self.value, self.attribute + "-Unrelated")

	def test_direct_master_creation_populates_native_values(self):
		doc = frappe.get_doc(
			{"doctype": MASTER, "attribute_name": self.attribute, "attribute_value": "XXL"}
		).insert()
		self.assertEqual(doc.name, attribute_value_name(self.attribute, "XXL"))
		self.assertTrue(
			frappe.db.exists("Item Attribute Value", {"parent": self.attribute, "attribute_value": "XXL"})
		)

	def user_with_roles(self, *roles):
		return (
			frappe.get_doc(
				{
					"doctype": "User",
					"email": f"value-create-{frappe.generate_hash(length=8)}@example.test",
					"first_name": "Value Create",
					"send_welcome_email": 0,
					"roles": [{"role": role} for role in roles],
				}
			)
			.insert(ignore_permissions=True)
			.name
		)

	def test_value_create_permission_adds_native_value_without_item_attribute_write(self):
		frappe.set_user(self.user_with_roles("Item Master Manager"))
		self.assertFalse(frappe.has_permission("Item Attribute", "write"))
		doc = frappe.get_doc(
			{"doctype": MASTER, "attribute_name": self.attribute, "attribute_value": "XXL"}
		).insert()
		frappe.set_user("Administrator")
		self.assertEqual(doc.name, attribute_value_name(self.attribute, "XXL"))
		self.assertTrue(
			frappe.db.exists("Item Attribute Value", {"parent": self.attribute, "attribute_value": "XXL"})
		)

	def test_value_create_needs_attribute_value_create_permission(self):
		frappe.set_user(self.user_with_roles("Purchase User"))
		self.assertFalse(frappe.has_permission(MASTER, "create"))
		with self.assertRaises(frappe.PermissionError):
			frappe.get_doc(
				{"doctype": MASTER, "attribute_name": self.attribute, "attribute_value": "XXL"}
			).insert()

	def test_value_identity_cannot_be_edited(self):
		doc = frappe.get_doc(MASTER, self.link)
		doc.attribute_value = "Other"
		with self.assertRaises(frappe.ValidationError):
			doc.save()

	def test_removing_used_native_value_is_blocked(self):
		self.mapping()
		doc = frappe.get_doc("Item Attribute", self.attribute)
		doc.set("item_attribute_values", [])
		with self.assertRaises(frappe.LinkExistsError):
			doc.save()

	def test_numeric_attribute_uses_native_range_validation(self):
		doc = frappe.get_doc(
			{
				"doctype": "Item Attribute",
				"attribute_name": self.attribute + "-Numeric",
				"numeric_values": 1,
				"from_range": 1,
				"to_range": 10,
				"increment": 1,
			}
		).insert()
		master = frappe.get_doc(
			{"doctype": MASTER, "attribute_name": doc.name, "attribute_value": "3"}
		).insert()
		self.assertTrue(frappe.db.exists(MASTER, master.name))
		self.assertEqual(frappe.db.count("Item Attribute Value", {"parent": doc.name}), 0)
		with self.assertRaises(frappe.ValidationError):
			frappe.get_doc({"doctype": MASTER, "attribute_name": doc.name, "attribute_value": "3.5"}).insert()

	def test_attribute_rename_updates_master_and_mapping_link(self):
		mapping = self.mapping()
		new = self.attribute + "-Renamed"
		frappe.rename_doc("Item Attribute", self.attribute, new, force=True)
		expected = attribute_value_name(new, self.value)
		self.assertTrue(frappe.db.exists(MASTER, expected))
		self.assertEqual(
			frappe.db.get_value("YRP Item Item Attribute Mapping", mapping.name, "attribute_name"), new
		)
		self.assertEqual(
			frappe.db.get_value(
				"YRP Item Item Attribute Mapping Value", mapping.values[0].name, "attribute_value"
			),
			expected,
		)
		self.assertEqual(get_mapping_values(mapping.name), [self.value])

	def test_hash_identity_and_title_display(self):
		self.assertRegex(self.link, r"^IAV-[0-9a-f]{40}$")
		meta = frappe.get_meta(MASTER)
		self.assertEqual(meta.title_field, "attribute_value")
		self.assertEqual(meta.show_title_field_in_link, 1)
		self.assertEqual(frappe.get_doc(MASTER, self.link).get_title(), self.value)

	def test_pair_lookup_is_stable_for_existing_and_missing_values(self):
		pairs = [(self.attribute, "XL"), (self.attribute, "Missing"), ("Other", "XL"), ("Gauge", 3)]
		expected = {
			(attribute, str(value)): attribute_value_name(attribute, value) for attribute, value in pairs
		}
		self.assertEqual(get_attribute_value_names(iter(pairs)), expected)
		clear_name_cache()
		self.assertEqual(get_attribute_value_names(pairs), expected)
		self.assertEqual(get_attribute_value_names([]), {})

	def test_mapping_with_only_blank_rows_allows_every_attribute_value(self):
		from yrp.yrp.doctype.yrp_item.yrp_item import _mapping_values

		doc = self.mapping()
		frappe.db.set_value(
			"YRP Item Item Attribute Mapping Value", doc.values[0].name, "attribute_value", None
		)
		frappe.clear_document_cache(doc.doctype, doc.name)
		row = frappe._dict(attribute=self.attribute, mapping=doc.name)
		self.assertEqual(_mapping_values(row), [self.value])

	def test_value_search_needs_only_attribute_value_read(self):
		from yrp.yrp.doctype.yrp_item.yrp_item import search_item_attribute_values

		def has_permission(doctype, ptype="read", *args, **kwargs):
			return doctype == MASTER

		with patch("frappe.has_permission", side_effect=has_permission):
			self.assertEqual(search_item_attribute_values(attribute=self.attribute), [self.value])

	def test_value_search_ranks_exact_and_prefix_matches_before_paging(self):
		from yrp.yrp.doctype.yrp_item.yrp_item import search_item_attribute_values

		for value in ["Off white", "White Melange", "White"]:
			self.native.append(
				"item_attribute_values", {"attribute_value": value, "abbr": value.replace(" ", "")}
			)
		self.native.save()
		self.assertEqual(
			search_item_attribute_values("white", attribute=self.attribute, page_len=2),
			["White", "White Melange"],
		)
