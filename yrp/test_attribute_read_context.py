from unittest.mock import patch

import frappe
from frappe.tests import UnitTestCase


class TestAttributeReadContext(UnitTestCase):
	def test_nested_scope_detaches_results_and_discards_on_exit(self):
		from yrp.attribute_read_context import attribute_read_scope, read_value

		calls = []
		def load():
			calls.append(1)
			return {"values": [len(calls)]}

		with attribute_read_scope():
			first = read_value("test", "item", load)
			first["values"].append(99)
			with attribute_read_scope():
				self.assertEqual(read_value("test", "item", load), {"values": [1]})
		self.assertEqual(read_value("test", "item", load), {"values": [2]})

	def test_user_mapping_and_exception_boundaries(self):
		from yrp.attribute_read_context import attribute_read_scope, read_value

		with self.assertRaises(RuntimeError):
			with attribute_read_scope():
				self.assertEqual(read_value("details", ("item", "one"), lambda: 1), 1)
				self.assertEqual(read_value("details", ("item", "two"), lambda: 2), 2)
				with patch.object(frappe, "session", frappe._dict(user="other")):
					self.assertEqual(read_value("details", ("item", "one"), lambda: 3), 3)
				raise RuntimeError()
		self.assertEqual(read_value("details", ("item", "one"), lambda: 4), 4)

	def test_reentering_same_scope_never_reuses_data_after_exit(self):
		from yrp.attribute_read_context import attribute_read_scope, read_value

		scope = attribute_read_scope()
		with scope:
			self.assertEqual(read_value("test", "same", lambda: 1), 1)
			with scope:
				self.assertEqual(read_value("test", "same", lambda: 2), 1)
		self.assertEqual(read_value("test", "same", lambda: 3), 3)

	def test_attribute_details_rechecks_read_permission_on_scope_hits(self):
		from unittest.mock import Mock

		from yrp.attribute_read_context import attribute_read_scope
		from yrp.yrp.doctype.yrp_item import yrp_item

		item = Mock()
		with (
			patch.object(frappe, "get_cached_doc", return_value=item),
			patch.object(yrp_item, "_build_attribute_details", return_value={"attributes": ["Colour"]}),
			attribute_read_scope(),
		):
			self.assertEqual(yrp_item.get_attribute_details("I"), {"attributes": ["Colour"]})
			item.check_permission.side_effect = frappe.PermissionError
			with self.assertRaises(frappe.PermissionError):
				yrp_item.get_attribute_details("I")

	def test_variant_reads_keep_original_fold_and_are_detached_per_scope(self):
		from yrp.attribute_read_context import attribute_read_scope
		from yrp import utils

		rows = [dict(attribute="Size", attribute_value="S"), dict(attribute="Colour", attribute_value="Blue"), dict(attribute="Size", attribute_value="M")]
		with patch.object(frappe.db, "sql", return_value=rows) as query, patch.object(utils, "_attribute_value", side_effect=lambda v: v):
			with attribute_read_scope():
				first = utils.get_variant_attr_details("V")
				self.assertEqual(list(first), ["Size", "Colour"])
				self.assertEqual(first["Size"], "M")
				first["Size"] = "changed"
				self.assertEqual(utils.get_variant_attr_details("V")["Size"], "M")
				query.assert_called_once()
			utils.get_variant_attr_details("V")
			self.assertEqual(query.call_count, 2)

	def test_ipd_primary_values_reuse_only_inside_read_scope(self):
		from yrp.attribute_read_context import attribute_read_scope
		from yrp.yrp.doctype.yrp_item_production_detail import yrp_item_production_detail as ipd

		doc = frappe._dict(primary_item_attribute="Size", item_attributes=[frappe._dict(attribute="Size", mapping="Map")])
		mapping = type("Mapping", (), {"values": [frappe._dict(attribute_value="S")]})()
		with patch.object(frappe, "get_cached_doc", return_value=doc), patch.object(ipd, "get_mapping_document", return_value=mapping) as load, patch.object(ipd, "_attribute_value", side_effect=lambda v: v):
			with attribute_read_scope():
				first = ipd.get_ipd_primary_values("IPD")
				first.append("mutated")
				self.assertEqual(ipd.get_ipd_primary_values("IPD"), ["S"])
				load.assert_called_once()
			mapping.values[0].attribute_value = "M"
			self.assertEqual(ipd.get_ipd_primary_values("IPD"), ["M"])
