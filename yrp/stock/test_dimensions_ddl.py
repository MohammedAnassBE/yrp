"""DDL compatibility tests for Stock Dimension schema maintenance."""

from unittest import TestCase
from unittest.mock import Mock, patch

from yrp.stock import dimensions


class TestStockDimensionDDL(TestCase):
	def test_dimension_reads_are_scoped_to_the_namespaced_single(self):
		cache = Mock()
		cache.get_value.return_value = None
		with (
			patch.object(dimensions.frappe, "cache", return_value=cache),
			patch.object(dimensions.frappe, "get_all", return_value=[]) as get_all,
		):
			dimensions.get_stock_dimensions()

		get_all.assert_called_once_with(
			'YRP YRP Stock Dimension',
			filters={
				"parent": 'YRP YRP Stock Settings',
				"parenttype": 'YRP YRP Stock Settings',
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
			dimensions._ensure_bin_unique_constraint(
				[{"fieldname": "lot"}, {"fieldname": "received_type"}]
			)

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
			"ALTER TABLE `tabYRP Bin` ADD UNIQUE INDEX `unique_bin_dimension` "
			"(`item_code`, `warehouse`)"
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
