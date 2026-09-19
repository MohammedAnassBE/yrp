"""Unit tests for the data-preserving namespace migration helpers."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import call, patch

from yrp import namespace_migration


class TestNamespaceMigration(unittest.TestCase):
	def test_legacy_single_children_are_deduplicated_or_moved_without_loss(self):
		records = [
			(
				"YRP Stock Settings",
				"YRP ",
				[
					{
						"fieldname": "stock_dimensions",
						"fieldtype": "Table",
						"options": "YRP Stock Dimension",
					}
				],
			)
		]
		legacy_rows = [
			namespace_migration.frappe._dict(
				name="old-lot", idx=1, fieldname="lot", label="Lot"
			),
			namespace_migration.frappe._dict(
				name="old-received", idx=2, fieldname="received_type", label="Received Type"
			),
		]
		target_rows = [
			namespace_migration.frappe._dict(
				name="new-lot", idx=1, fieldname="lot", label="Lot"
			)
		]
		db = SimpleNamespace()
		db.exists = lambda record_type, name: name == "YRP Stock Settings"
		db.get_value = lambda *args, **kwargs: 1
		db.table_exists = lambda *args, **kwargs: True
		db.get_table_columns = lambda *args, **kwargs: [
			"name",
			"idx",
			"parent",
			"parenttype",
			"parentfield",
			"fieldname",
			"label",
		]
		db.delete = unittest.mock.Mock()
		db.set_value = unittest.mock.Mock()
		with (
			patch.object(namespace_migration, "_iter_namespaced_doctypes", return_value=records),
			patch.object(namespace_migration.frappe, "db", db),
			patch.object(
				namespace_migration.frappe,
				"get_all",
				side_effect=[legacy_rows, target_rows],
			),
		):
			result = namespace_migration.reconcile_legacy_single_child_parents(("yrp",))

		self.assertEqual(result, {"moved": 1, "deduplicated": 1})
		db.delete.assert_called_once_with(
			"YRP Stock Dimension", {"name": ["in", ["old-lot"]]}
		)
		db.set_value.assert_called_once_with(
			"YRP Stock Dimension",
			"old-received",
			{
				"parent": "YRP Stock Settings",
				"parenttype": "YRP Stock Settings",
				"parentfield": "stock_dimensions",
				"idx": 2,
			},
			update_modified=False,
		)

	def test_empty_unregistered_legacy_table_is_dropped_but_erpnext_table_is_preserved(self):
		records = [
			("Supplier", "YRP ", []),
			("YRP Legacy Child", "YRP ", []),
		]
		db = SimpleNamespace()
		db.exists = lambda record_type, name: name in {
			"Supplier",
			"Supplier",
			"YRP Legacy Child",
		}
		db.table_exists = lambda name, cached=False: name == "Legacy Child"
		db.sql = unittest.mock.Mock(return_value=[[0]])
		db.sql_ddl = unittest.mock.Mock()
		with (
			patch.object(namespace_migration, "_iter_namespaced_doctypes", return_value=records),
			patch.object(namespace_migration.frappe, "db", db),
		):
			dropped = namespace_migration.drop_empty_legacy_namespace_tables(("yrp",))

		self.assertEqual(dropped, ["Legacy Child"])
		db.sql.assert_called_once_with("SELECT COUNT(*) FROM `tabLegacy Child`")
		db.sql_ddl.assert_called_once_with("DROP TABLE `tabLegacy Child`")

	def test_nonempty_legacy_namespace_table_fails_closed(self):
		db = SimpleNamespace()
		db.exists = lambda record_type, name: name == "YRP Legacy Child"
		db.table_exists = lambda name, cached=False: True
		db.sql = unittest.mock.Mock(return_value=[[3]])
		db.sql_ddl = unittest.mock.Mock()
		with (
			patch.object(
				namespace_migration,
				"_iter_namespaced_doctypes",
				return_value=[("YRP Legacy Child", "YRP ", [])],
			),
			patch.object(namespace_migration.frappe, "db", db),
			patch.object(
				namespace_migration.frappe,
				"throw",
				side_effect=RuntimeError("non-empty"),
			),
		):
			with self.assertRaisesRegex(RuntimeError, "non-empty"):
				namespace_migration.drop_empty_legacy_namespace_tables(("yrp",))

		db.sql_ddl.assert_not_called()

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
			("Purchase Order", "YRP ", []),
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
					"Purchase Order",
					update_modified=False,
				),
			],
		)

	def test_customization_record_names_are_aligned_with_the_target_doctype(self):
		custom_field = SimpleNamespace(
			name="Process-is_group",
			dt="YRP Process",
			fieldname="is_group",
		)
		property_setter = SimpleNamespace(
			name="Process-main-title_field",
			doc_type="YRP Process",
			field_name=None,
			row_name=None,
			property="title_field",
		)
		with (
			patch.object(
				namespace_migration,
				"_iter_namespaced_doctypes",
				return_value=[("YRP Process", "YRP ", [])],
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
				call("Custom Field", "Process-is_group", "YRP Process-is_group"),
				call(
					"Property Setter",
					"Process-main-title_field",
					"YRP Process-main-title_field",
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
