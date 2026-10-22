"""DDL compatibility tests for Stock Dimension schema maintenance."""

from unittest import TestCase
from unittest.mock import Mock, patch

from yrp.stock import dimensions


class TestStockDimensionDDL(TestCase):
	def test_grn_header_lot_is_conditionally_mandatory(self):
		dimension = {
			"fieldname": "lot",
			"dimension_doctype": "SD YRP Lot",
			"label": "Lot",
			"is_production_group": 1,
		}
		with (
			patch.object(dimensions, "get_stock_dimensions", return_value=[dimension]),
			patch.object(dimensions.frappe, "db", Mock()),
			patch.object(dimensions, "_get_insert_after", return_value="name"),
			patch.object(dimensions, "_ensure_bin_unique_constraint"),
			patch.object(dimensions, "_delete_orphan_dimension_fields"),
			patch.object(dimensions, "create_custom_fields") as create_fields,
		):
			dimensions.create_dimension_fields()
		fields = create_fields.call_args.args[0]
		grn_field = fields["YRP Goods Received Note"][0]
		self.assertEqual(grn_field["reqd"], 0)
		self.assertEqual(grn_field["mandatory_depends_on"], "eval:doc.against!='Purchase Order'")
		for doctype in ("YRP Work Order", "YRP Delivery Challan", "YRP Process Cost"):
			self.assertEqual(fields[doctype][0]["reqd"], 1)
		self.assertEqual(fields["Purchase Order"][0]["reqd"], 0)
		self.assertEqual(fields["YRP Work Order"][0]["in_standard_filter"], 1)
		self.assertNotIn("in_standard_filter", fields["YRP Delivery Challan"][0])

	def test_dimension_reads_are_scoped_to_the_namespaced_single(self):
		cache = Mock()
		cache.get_value.return_value = None
		with (
			patch.object(dimensions.frappe, "cache", return_value=cache),
			patch.object(dimensions.frappe, "get_all", return_value=[]) as get_all,
		):
			dimensions.get_stock_dimensions()

		get_all.assert_called_once_with(
			"YRP Stock Dimension",
			filters={
				"parent": "YRP Stock Settings",
				"parenttype": "YRP Stock Settings",
				"parentfield": "stock_dimensions",
			},
			fields=[
				"dimension_doctype",
				"fieldname",
				"label",
				"mandatory",
				"in_valuation",
				"is_production_group",
			],
			order_by="idx asc",
		)

	def test_changed_bin_index_is_rebuilt_with_the_ddl_api(self):
		db = Mock()
		db.sql.return_value = [
			{"Column_name": "item_code"},
			{"Column_name": "warehouse"},
			{"Column_name": "old_dimension"},
		]
		with patch.object(dimensions.frappe, "db", db):
			dimensions._ensure_bin_unique_constraint([{"fieldname": "lot"}, {"fieldname": "received_type"}])

		db.sql_ddl.assert_called_once_with(
			"ALTER TABLE `tabYRP Bin` DROP INDEX `unique_bin_dimension`, "
			"ADD UNIQUE INDEX `unique_bin_dimension` "
			"(`item_code`, `warehouse`, `lot`, `received_type`)"
		)

	def test_missing_bin_index_is_created_with_the_ddl_api(self):
		db = Mock()
		db.sql.return_value = []
		with patch.object(dimensions.frappe, "db", db):
			dimensions._ensure_bin_unique_constraint([])

		db.sql_ddl.assert_called_once_with(
			"ALTER TABLE `tabYRP Bin` ADD UNIQUE INDEX `unique_bin_dimension` (`item_code`, `warehouse`)"
		)

	def test_matching_bin_index_does_not_run_ddl(self):
		db = Mock()
		db.sql.return_value = [
			{"Column_name": "warehouse"},
			{"Column_name": "item_code"},
		]
		with patch.object(dimensions.frappe, "db", db):
			dimensions._ensure_bin_unique_constraint([])

		db.sql_ddl.assert_not_called()


class TestProductionGroupDimensionIndex(TestCase):
	def test_only_the_production_group_dimension_is_indexed(self):
		dims = [
			{"fieldname": "lot", "dimension_doctype": "SD YRP Lot", "label": "Lot", "is_production_group": 1},
			{
				"fieldname": "received_type",
				"dimension_doctype": "YRP Received Type",
				"label": "Received Type",
				"is_production_group": 0,
			},
		]
		with (
			patch.object(dimensions, "get_stock_dimensions", return_value=dims),
			patch.object(dimensions.frappe, "db", Mock()),
			patch.object(dimensions, "_get_insert_after", return_value="name"),
			patch.object(dimensions, "_ensure_bin_unique_constraint"),
			patch.object(dimensions, "_delete_orphan_dimension_fields"),
			patch.object(dimensions, "create_custom_fields") as create_fields,
		):
			dimensions.create_dimension_fields()
		fields = {row["fieldname"]: row for row in create_fields.call_args.args[0]["YRP Stock Ledger Entry"]}
		self.assertEqual(fields["lot"]["search_index"], 1)
		self.assertNotIn("search_index", fields["received_type"])
		self.assertEqual(create_fields.call_args.args[0]["YRP Work Order"][0]["search_index"], 1)
