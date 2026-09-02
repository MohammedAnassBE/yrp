import json
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, getdate, today

from yrp.stock.stock_ledger import (
	StockValuationPeriodClosedError,
	get_last_stock_valuation_closing_date,
	make_sl_entries,
	validate_stock_valuation_period,
)
from yrp.yrp_stock.doctype.yrp_stock_valuation_closing.yrp_stock_valuation_closing import (
	get_closing_snapshot,
)


class TestStockValuationClosing(IntegrationTestCase):
	def _closing(self, closing_date="2026-08-20"):
		return frappe.get_doc(
			{
				"doctype": 'YRP Stock Valuation Closing',
				"closing_through_date": closing_date,
				"closing_remarks": "Regression test closing",
			}
		)

	def test_schema_is_submittable_and_settings_cutoff_is_read_only(self):
		closing_meta = frappe.get_meta('YRP Stock Valuation Closing')
		self.assertTrue(closing_meta.is_submittable)
		self.assertTrue(closing_meta.get_field("closing_through_date").reqd)
		settings_field = frappe.get_meta('YRP YRP Stock Settings').get_field(
			"last_stock_valuation_closing_date"
		)
		self.assertTrue(settings_field.read_only)

	@patch(
		"yrp.yrp_stock.doctype.yrp_stock_valuation_closing.yrp_stock_valuation_closing.get_closing_snapshot"
	)
	def test_submit_and_cancel_lifecycle_updates_real_settings_cutoff(self, snapshot):
		if frappe.db.exists('YRP Stock Valuation Closing', {"docstatus": 1}):
			self.skipTest("A submitted closing already exists on this test site")

		snapshot.side_effect = lambda closing_date: {
			"closing_through_date": str(getdate(closing_date)),
			"checked_at": "2026-08-20 10:00:00",
			"active_stock_ledger_entries": 0,
			"closing_stock_value": 0,
			"active_repost_count": 0,
			"active_valuation_adjustment_count": 0,
			"negative_stock_bucket_count": 0,
			"zero_valuation_bucket_count": 0,
			"blocking_issues": [],
		}
		first_date = add_days(today(), -2)
		second_date = add_days(today(), -1)
		first = self._closing(first_date).insert(ignore_permissions=True)
		first.submit()
		self.assertEqual(
			getdate(
				frappe.db.get_single_value(
					'YRP YRP Stock Settings',
					"last_stock_valuation_closing_date",
					cache=False,
				)
			),
			getdate(first_date),
		)

		second = self._closing(second_date).insert(ignore_permissions=True)
		second.submit()
		second.cancel()
		self.assertEqual(
			getdate(
				frappe.db.get_single_value(
					'YRP YRP Stock Settings',
					"last_stock_valuation_closing_date",
					cache=False,
				)
			),
			getdate(first_date),
		)

		first.cancel()
		self.assertFalse(
			frappe.db.exists(
				"Singles",
				{
					"doctype": 'YRP YRP Stock Settings',
					"field": "last_stock_valuation_closing_date",
				},
			)
		)
		self.assertIsNone(get_last_stock_valuation_closing_date())

	@patch(
		"yrp.yrp_stock.doctype.yrp_stock_valuation_closing.yrp_stock_valuation_closing.get_latest_submitted_closing"
	)
	def test_closing_date_must_move_forward(self, get_latest):
		get_latest.return_value = frappe._dict(
			name="SVC-OLDER", closing_through_date="2026-07-31"
		)
		closing = self._closing("2026-07-15")
		with self.assertRaisesRegex(frappe.ValidationError, "must be after"):
			closing.set_period_boundaries()

	@patch(
		"yrp.yrp_stock.doctype.yrp_stock_valuation_closing.yrp_stock_valuation_closing.get_latest_submitted_closing"
	)
	def test_period_starts_after_previous_closing(self, get_latest):
		get_latest.return_value = frappe._dict(
			name="SVC-JULY", closing_through_date="2026-07-31"
		)
		closing = self._closing("2026-08-20")
		closing.set_period_boundaries()
		self.assertEqual(getdate(closing.previous_closing_date), getdate("2026-07-31"))
		self.assertEqual(getdate(closing.period_start_date), getdate("2026-08-01"))

	@patch(
		"yrp.yrp_stock.doctype.yrp_stock_valuation_closing.yrp_stock_valuation_closing._set_settings_cutoff"
	)
	def test_submit_updates_settings_cutoff(self, set_cutoff):
		closing = self._closing("2026-08-20")
		closing.on_submit()
		set_cutoff.assert_called_once_with("2026-08-20")

	@patch(
		"yrp.yrp_stock.doctype.yrp_stock_valuation_closing.yrp_stock_valuation_closing.get_latest_submitted_closing"
	)
	@patch(
		"yrp.yrp_stock.doctype.yrp_stock_valuation_closing.yrp_stock_valuation_closing._lock_closing_state"
	)
	def test_only_latest_closing_can_be_cancelled(self, _lock, get_latest):
		closing = self._closing("2026-07-31")
		closing.name = "SVC-JULY"
		get_latest.return_value = frappe._dict(
			name="SVC-AUGUST", closing_through_date="2026-08-20"
		)
		with self.assertRaisesRegex(frappe.ValidationError, "Only the latest"):
			closing.before_cancel()

	@patch(
		"yrp.yrp_stock.doctype.yrp_stock_valuation_closing.yrp_stock_valuation_closing._set_settings_cutoff"
	)
	@patch(
		"yrp.yrp_stock.doctype.yrp_stock_valuation_closing.yrp_stock_valuation_closing.get_latest_submitted_closing"
	)
	def test_cancel_restores_previous_submitted_cutoff(self, get_latest, set_cutoff):
		get_latest.return_value = frappe._dict(
			name="SVC-JUNE", closing_through_date="2026-06-30"
		)
		closing = self._closing("2026-07-31")
		closing.name = "SVC-JULY"
		closing.on_cancel()
		get_latest.assert_called_once_with(exclude_name="SVC-JULY")
		set_cutoff.assert_called_once_with("2026-06-30")

	@patch(
		"yrp.yrp_stock.doctype.yrp_stock_valuation_closing.yrp_stock_valuation_closing._set_settings_cutoff"
	)
	@patch(
		"yrp.yrp_stock.doctype.yrp_stock_valuation_closing.yrp_stock_valuation_closing.get_latest_submitted_closing",
		return_value=None,
	)
	def test_cancel_clears_cutoff_when_no_closing_remains(self, _latest, set_cutoff):
		closing = self._closing("2026-07-31")
		closing.name = "SVC-JULY"
		closing.on_cancel()
		set_cutoff.assert_called_once_with(None)

	@patch(
		"yrp.yrp_stock.doctype.yrp_stock_valuation_closing.yrp_stock_valuation_closing._get_latest_valuation_rows",
		return_value=[],
	)
	@patch(
		"yrp.yrp_stock.doctype.yrp_stock_valuation_closing.yrp_stock_valuation_closing._get_negative_stock_bucket_count",
		return_value=0,
	)
	@patch.object(frappe.db, "count")
	def test_pending_repost_blocks_snapshot(self, db_count, _negative, _rows):
		db_count.side_effect = [2, 0, 15]
		snapshot = get_closing_snapshot("2026-07-31")
		self.assertEqual(snapshot["active_repost_count"], 2)
		self.assertEqual(snapshot["active_valuation_adjustment_count"], 0)
		self.assertEqual(snapshot["active_stock_ledger_entries"], 15)
		self.assertTrue(snapshot["blocking_issues"])

	@patch(
		"yrp.yrp_stock.doctype.yrp_stock_valuation_closing.yrp_stock_valuation_closing._get_latest_valuation_rows",
		return_value=[],
	)
	@patch(
		"yrp.yrp_stock.doctype.yrp_stock_valuation_closing.yrp_stock_valuation_closing._get_negative_stock_bucket_count",
		return_value=0,
	)
	@patch.object(frappe.db, "count")
	def test_pending_late_cost_adjustment_blocks_snapshot(
		self, db_count, _negative, _rows
	):
		db_count.side_effect = [0, 1, 15]
		snapshot = get_closing_snapshot("2026-07-31")
		self.assertEqual(snapshot["active_valuation_adjustment_count"], 1)
		self.assertIn("late-cost adjustment", snapshot["blocking_issues"][0])


class TestStockValuationPeriodGuard(IntegrationTestCase):
	@patch(
		"yrp.stock.stock_ledger.get_last_stock_valuation_closing_date",
		return_value=None,
	)
	def test_blank_cutoff_skips_validation(self, _closing_date):
		validate_stock_valuation_period(
			"2026-01-01", 'YRP Stock Entry', "STE-TEST"
		)

	@patch(
		"yrp.stock.stock_ledger.get_last_stock_valuation_closing_date",
		return_value=getdate("2026-07-31"),
	)
	def test_date_on_cutoff_is_blocked(self, _closing_date):
		with self.assertRaises(StockValuationPeriodClosedError):
			validate_stock_valuation_period(
				"2026-07-31", 'YRP Stock Entry', "STE-CLOSED"
			)

	@patch(
		"yrp.stock.stock_ledger.get_last_stock_valuation_closing_date",
		return_value=getdate("2026-07-31"),
	)
	def test_date_after_cutoff_is_allowed(self, _closing_date):
		validate_stock_valuation_period(
			"2026-08-01", 'YRP Stock Entry', "STE-OPEN"
		)

	@patch("yrp.stock.stock_ledger._set_voucher_cancelled")
	@patch(
		"yrp.stock.stock_ledger.get_last_stock_valuation_closing_date",
		return_value=getdate("2026-07-31"),
	)
	def test_cancel_is_blocked_before_existing_sles_are_mutated(
		self, _closing_date, set_cancelled
	):
		with self.assertRaises(StockValuationPeriodClosedError):
			make_sl_entries(
				[
					{
						"item": "ITEM-TEST",
						"warehouse": "WAREHOUSE-TEST",
						"posting_date": "2026-07-15",
						"voucher_type": 'YRP Goods Received Note',
						"voucher_no": "GRN-CLOSED",
						"qty": 1,
					}
				],
				cancel=True,
			)
		set_cancelled.assert_not_called()

	def test_doctype_json_grants_cancel_to_stock_manager(self):
		path = frappe.get_app_path(
			"yrp",
			"yrp_stock",
			"doctype",
			"yrp_stock_valuation_closing",
			"yrp_stock_valuation_closing.json",
		)
		with open(path) as source:
			metadata = json.load(source)
		stock_manager = next(
			row for row in metadata["permissions"] if row["role"] == "Stock Manager"
		)
		self.assertEqual(stock_manager["cancel"], 1)
