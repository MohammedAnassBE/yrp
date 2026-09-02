"""Unit tests for the data-preserving namespace migration helpers."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import call, patch

from yrp import namespace_migration


class TestNamespaceMigration(unittest.TestCase):
	def test_stored_doctype_discriminators_are_rewritten(self):
		records = [
			(
				"YRP GRN Item",
				"YRP ",
				[
					{
						"fieldtype": "Select",
						"fieldname": "ref_doctype",
						"options": "YRP Work Order\nYRP Purchase Order",
					}
				],
			),
			("YRP Work Order", "YRP ", []),
			("YRP Purchase Order", "YRP ", []),
		]
		db = SimpleNamespace()
		db.exists = lambda record_type, name: name == "YRP GRN Item"
		db.get_value = lambda *args, **kwargs: 0
		db.has_column = lambda *args, **kwargs: True
		db.set_value = unittest.mock.Mock()
		with (
			patch.object(namespace_migration, "_iter_namespaced_doctypes", return_value=records),
			patch.object(namespace_migration.frappe, "db", db),
		):
			namespace_migration.rewrite_owned_doctype_discriminators(("yrp",))

		self.assertCountEqual(
			db.set_value.call_args_list,
			[
				call(
					"YRP GRN Item",
					{"ref_doctype": "GRN Item"},
					"ref_doctype",
					"YRP GRN Item",
					update_modified=False,
				),
				call(
					"YRP GRN Item",
					{"ref_doctype": "Work Order"},
					"ref_doctype",
					"YRP Work Order",
					update_modified=False,
				),
				call(
					"YRP GRN Item",
					{"ref_doctype": "Purchase Order"},
					"ref_doctype",
					"YRP Purchase Order",
					update_modified=False,
				),
			],
		)

	def test_customization_record_names_are_aligned_with_the_target_doctype(self):
		custom_field = SimpleNamespace(
			name="Supplier-gstin",
			dt="YRP Supplier",
			fieldname="gstin",
		)
		property_setter = SimpleNamespace(
			name="Supplier-main-title_field",
			doc_type="YRP Supplier",
			field_name=None,
			row_name=None,
			property="title_field",
		)
		with (
			patch.object(
				namespace_migration,
				"_iter_namespaced_doctypes",
				return_value=[("YRP Supplier", "YRP ", [])],
			),
			patch.object(
				namespace_migration.frappe,
				"get_all",
				side_effect=[[custom_field], [property_setter]],
			),
			patch.object(namespace_migration, "_rename_customization_record") as rename,
		):
			namespace_migration.rename_owned_customization_records(("yrp",))

		rename.assert_has_calls(
			[
				call("Custom Field", "Supplier-gstin", "YRP Supplier-gstin"),
				call(
					"Property Setter",
					"Supplier-main-title_field",
					"YRP Supplier-main-title_field",
				),
			]
		)

	def test_customization_collision_aborts_instead_of_overwriting(self):
		db = SimpleNamespace(exists=lambda *args, **kwargs: True)
		with (
			patch.object(namespace_migration.frappe, "db", db),
			patch.object(
				namespace_migration.frappe,
				"throw",
				side_effect=RuntimeError("collision"),
			),
			patch.object(namespace_migration.frappe, "rename_doc") as rename_doc,
		):
			with self.assertRaisesRegex(RuntimeError, "collision"):
				namespace_migration._rename_customization_record(
					"Custom Field",
					"Supplier-gstin",
					"YRP Supplier-gstin",
				)

		rename_doc.assert_not_called()

	def test_customization_rename_uses_frappe_v16_arguments(self):
		db = SimpleNamespace(exists=lambda *args, **kwargs: False)
		with (
			patch.object(namespace_migration.frappe, "db", db),
			patch.object(namespace_migration.frappe, "rename_doc") as rename_doc,
		):
			namespace_migration._rename_customization_record(
				"Custom Field",
				"Supplier-gstin",
				"YRP Supplier-gstin",
			)

		rename_doc.assert_called_once_with(
			"Custom Field",
			"Supplier-gstin",
			"YRP Supplier-gstin",
			force=True,
			show_alert=False,
			rebuild_search=False,
		)


if __name__ == "__main__":
	unittest.main()
