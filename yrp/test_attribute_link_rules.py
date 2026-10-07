import json
from pathlib import Path
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from yrp import attribute_links

RULES = {
	"_Test Row": {
		"by_context": {"context": ["first_attribute", "second_attribute"]},
		"by_settings": {
			"context": ["first_attribute"],
			"settings": ["YRP Settings", "po_dependent_attribute"],
		},
		"by_attribute": {"attribute": "Fixed"},
		"by_nothing": {"context": ["first_attribute"]},
	}
}


class TestAttributeLinkRules(IntegrationTestCase):
	def setUp(self):
		patcher = patch.object(attribute_links, "field_rules", return_value=RULES)
		patcher.start()
		self.addCleanup(patcher.stop)
		attribute_links.clear_cache()

	def resolve(self, field, ancestors=(), **values):
		return attribute_links.attribute_for(frappe._dict(doctype="_Test Row", **values), field, ancestors)

	def test_context_fields_are_tried_in_declared_order_across_the_context(self):
		parent = frappe._dict(first_attribute="From Parent")

		self.assertEqual(self.resolve("by_context", (parent,), second_attribute="Own"), "From Parent")
		self.assertEqual(self.resolve("by_context", second_attribute="Own"), "Own")

	def test_settings_rule_reads_the_single_when_context_is_empty(self):
		frappe.db.set_single_value("YRP Settings", "po_dependent_attribute", "Stage")

		self.assertEqual(self.resolve("by_settings"), "Stage")
		self.assertEqual(self.resolve("by_settings", first_attribute="Own"), "Own")

	def test_settings_rule_fails_loudly_when_the_setting_is_unset(self):
		frappe.db.set_single_value("YRP Settings", "po_dependent_attribute", None)

		with self.assertRaises(frappe.ValidationError):
			self.resolve("by_settings")

	def test_fixed_attribute_and_unresolved_rules(self):
		self.assertEqual(self.resolve("by_attribute"), "Fixed")
		self.assertIsNone(self.resolve("by_nothing"))
		self.assertIsNone(self.resolve("unregistered"))

	def test_hooked_context_links_add_the_linked_document_to_the_context(self):
		linked = frappe._dict(first_attribute="From Link")
		with (
			patch.object(frappe, "get_hooks", return_value={"_test_link": ["_Test Linked"]}),
			patch.object(frappe.db, "get_value", return_value=linked) as get_value,
		):
			resolved = self.resolve("by_context", _test_link="LINK-1")

		self.assertEqual(resolved, "From Link")
		get_value.assert_called_once_with("_Test Linked", "LINK-1", "*", as_dict=True)


class TestAttributeLinkRegistry(IntegrationTestCase):
	def test_registered_fields_are_the_declared_rule_names(self):
		with patch.object(attribute_links, "field_rules", return_value=RULES):
			attribute_links.clear_cache()
			self.assertEqual(
				attribute_links.fields(),
				{"_Test Row": ["by_context", "by_settings", "by_attribute", "by_nothing"]},
			)
		attribute_links.clear_cache()

	def test_app_registries_merge_field_rules_per_doctype(self):
		registries = {
			"app_one": {"Shared": {"one": {"attribute": "A"}}},
			"app_two": {"Shared": {"two": {"attribute": "B"}}},
		}
		with patch.object(attribute_links, "_read_app_registries", return_value=registries.values()):
			attribute_links.clear_cache()
			self.assertEqual(
				attribute_links.field_rules(),
				{"Shared": {"one": {"attribute": "A"}, "two": {"attribute": "B"}}},
			)
		attribute_links.clear_cache()

	def test_old_list_registry_format_is_rejected(self):
		with patch.object(attribute_links, "_read_app_registries", return_value=[{"Old": ["field"]}]):
			attribute_links.clear_cache()
			with self.assertRaises(frappe.ValidationError):
				attribute_links.field_rules()
		attribute_links.clear_cache()

	def test_client_context_fields_come_from_rules_and_context_links(self):
		with (
			patch.object(attribute_links, "field_rules", return_value=RULES),
			patch.object(frappe, "get_hooks", return_value={"_test_link": ["_Test Linked"]}),
		):
			attribute_links.clear_cache()
			fieldnames = attribute_links.get_context_fieldnames()
		attribute_links.clear_cache()

		self.assertEqual(
			fieldnames,
			[
				"doctype",
				"name",
				"_test_link",
				"first_attribute",
				"item_production_detail",
				"production_detail",
				"second_attribute",
			],
		)

	def test_base_app_knows_no_customer_doctypes(self):
		root = Path(frappe.get_app_path("yrp"))
		registry = json.loads((root / "attribute_link_fields.json").read_text())
		sources = [
			(root / "attribute_links.py").read_text(),
			(root / "public" / "js" / "attribute_links.js").read_text(),
			json.dumps(registry),
		]
		for source in sources:
			self.assertNotIn("SD YRP", source)
			self.assertNotIn("packing_attribute", source)
			self.assertNotIn("cutting_plan", source)
