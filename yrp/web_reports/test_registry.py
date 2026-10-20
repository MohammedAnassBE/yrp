from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from yrp.web_reports.background import needs_background
from yrp.web_reports.filters import authorize_filters
from yrp.web_reports.registry import (
	WebReport,
	clear_web_report_cache,
	get_web_reports,
	resolve_entry_path,
)


def first_duplicate_provider():
	return [_test_entry("duplicate")]


def second_duplicate_provider():
	return [_test_entry("duplicate")]


class TestWebReportRegistry(IntegrationTestCase):
	def setUp(self):
		clear_web_report_cache()
		frappe.flags.pop("yrp_web_report_overrides", None)

	def tearDown(self):
		frappe.flags.pop("yrp_web_report_overrides", None)
		clear_web_report_cache()

	def test_hooks_merge_both_apps(self):
		reports = get_web_reports()
		production_keys = {
			"stock-balance",
			"purchase-order-itemwise",
			"finishing-plan",
			"cut-bundle-balance",
			"cutting-dpr",
		}
		self.assertTrue(production_keys.issubset(reports))
		for key in production_keys:
			self.assertIn(f"{key}--test-never", reports)
			self.assertIn(f"{key}--test-always", reports)
		self.assertIn("web-report--test-failing", reports)

	def test_http_test_entries_require_allow_tests(self):
		original = frappe.conf.allow_tests
		try:
			frappe.conf.allow_tests = False
			clear_web_report_cache()
			reports = get_web_reports()
			self.assertFalse(any("--test-" in key for key in reports))
			self.assertNotIn("web-report--test-failing", reports)
		finally:
			frappe.conf.allow_tests = original

	def test_every_registered_dotted_path_resolves(self):
		for entry in get_web_reports().values():
			for fieldname in ("runner", "access", "dependency_doctypes"):
				if getattr(entry, fieldname):
					with self.subTest(report=entry.key, fieldname=fieldname):
						self.assertTrue(callable(resolve_entry_path(entry, fieldname)))

	def test_duplicate_key_raises(self):
		hooks = [
			"yrp.web_reports.test_registry.first_duplicate_provider",
			"yrp.web_reports.test_registry.second_duplicate_provider",
		]
		with patch("yrp.web_reports.registry.frappe.get_hooks", return_value=hooks):
			with self.assertRaisesRegex(frappe.ValidationError, "Duplicate web report key"):
				get_web_reports()

	def test_invalid_background_configuration_raises_during_load(self):
		entry = _test_entry("invalid-background")
		entry.background = {"unexpected": True}
		frappe.flags.yrp_web_report_overrides = {entry.key: entry}
		clear_web_report_cache()
		with self.assertRaisesRegex(frappe.ValidationError, "Unknown background configuration"):
			get_web_reports()

	def test_overrides_are_test_only(self):
		frappe.flags.yrp_web_report_overrides = {"override-only": _test_entry("override-only")}
		original = frappe.flags.in_test
		try:
			frappe.flags.in_test = False
			clear_web_report_cache()
			self.assertNotIn("override-only", get_web_reports())
			frappe.flags.in_test = True
			clear_web_report_cache()
			self.assertIn("override-only", get_web_reports())
		finally:
			frappe.flags.in_test = original

	def test_stock_registration_has_no_essdee_dependency_without_dimensions(self):
		with (
			patch("yrp.yrp_stock.web_reports.get_stock_dimensions", return_value=[]),
			patch("yrp.yrp_stock.web_reports.frappe.get_all", return_value=[]),
		):
			from yrp.yrp_stock.web_reports import get_reports

			entry = get_reports()[0]
			payload = repr(
				(entry.filters, entry.link_sources, resolve_entry_path(entry, "dependency_doctypes")())
			)
		self.assertNotIn("SD YRP", payload)

	def test_full_stock_balance_page_payload_is_allowed(self):
		entry = get_web_reports()["stock-balance"]
		payload = {
			"from_date": "2026-10-01",
			"to_date": "2026-10-13",
			"item": "",
			"parent_item": "",
			"warehouse": "",
			"lot": "",
			"received_type": "",
			"remove_zero_balance_item": 1,
			"show_variant_attributes": 0,
			"show_stock_ageing_data": 0,
			"show_inward_date_split": 0,
		}
		self.assertEqual(authorize_filters(entry.filters, payload), payload)

	def test_stock_balance_always_runs_in_background_like_prepared_desk_report(self):
		entry = get_web_reports()["stock-balance"]
		self.assertTrue(needs_background(entry, {"from_date": "2026-09-15", "to_date": "2026-10-15"}))
		self.assertTrue(needs_background(entry, {"from_date": "2026-10-15", "to_date": "2026-10-15"}))

	def test_unknown_filter_is_rejected(self):
		entry = get_web_reports()["stock-balance"]
		with self.assertRaisesRegex(frappe.ValidationError, "Unknown report filters"):
			authorize_filters(entry.filters, {"forged": 1})

	def test_stock_dimensions_are_multiselect_and_validate_every_link(self):
		entry = get_web_reports()["stock-balance"]
		self.assertEqual(entry.filters["lot"], ("MultiSelectList", "SD YRP Lot"))
		self.assertEqual(entry.filters["received_type"], ("MultiSelectList", "YRP Received Type"))
		with patch("yrp.web_reports.filters.frappe.has_permission", return_value=True) as permission:
			payload = {"lot": ["A", "B"], "received_type": ["Accepted", "Rejected"]}
			self.assertEqual(authorize_filters(entry.filters, payload), payload)
			self.assertEqual(permission.call_count, 4)
		with patch(
			"yrp.web_reports.filters.frappe.has_permission", side_effect=lambda dt, ptype, doc: doc == "A"
		):
			with self.assertRaises(frappe.PermissionError):
				authorize_filters(entry.filters, {"lot": ["A", "B"]})

	def test_multiselect_links_reject_invalid_members_and_accept_empty(self):
		schema = {"quality": ("MultiSelectList", "Quality")}
		for value in ([], "", None):
			self.assertEqual(authorize_filters(schema, {"quality": value}), {"quality": value})
		for value in ([{"name": "A"}], [1], {"name": "A"}, 1):
			with self.subTest(value=value), self.assertRaises(frappe.ValidationError):
				authorize_filters(schema, {"quality": value})

	def test_multiselect_empty_members_are_normalized_before_authorization(self):
		schema = {"lot": ("MultiSelectList", "SD YRP Lot")}
		for value in (["A", None, ""], '["A", null, ""]'):
			with (
				self.subTest(value=value),
				patch("yrp.web_reports.filters.frappe.has_permission", return_value=True) as permission,
			):
				self.assertEqual(authorize_filters(schema, {"lot": value}), {"lot": ["A"]})
				self.assertEqual(permission.call_count, 1)

	def test_stock_item_filters_accept_lists_and_check_every_item(self):
		entry = get_web_reports()["stock-balance"]
		for field in ("item", "parent_item"):
			self.assertEqual(entry.filters[field], ("MultiSelectList", "Item"))
			with patch(
				"yrp.web_reports.filters.frappe.has_permission", side_effect=lambda dt, ptype, doc: doc == "A"
			):
				with self.assertRaises(frappe.PermissionError):
					authorize_filters(entry.filters, {field: ["A", "B"]})


def _test_entry(key):
	return WebReport(
		key=key,
		kind="frappe_report",
		report="YRP Stock Balance",
		filters={},
		background="never",
	)
