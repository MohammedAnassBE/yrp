import frappe
from frappe.tests import IntegrationTestCase

from yrp.web_reports.background import needs_background, validate_background


class TestBackgroundRules(IntegrationTestCase):
	def test_always_and_never(self):
		self.assertTrue(needs_background(_entry(background="always"), {}))
		self.assertFalse(needs_background(_entry(background="never"), {}))

	def test_cutting_daily_mode_is_immediate_for_long_range(self):
		self.assertFalse(needs_background(_cutting_entry(), _dates("daily", "2026-01-01", "2026-02-10")))

	def test_cutting_summary_boundary(self):
		cases = [
			("2026-01-11", False),
			("2026-01-12", True),
			("2026-01-01", False),
		]
		for to_date, expected in cases:
			with self.subTest(to_date=to_date):
				self.assertEqual(
					needs_background(_cutting_entry(), _dates("summary", "2026-01-01", to_date)),
					expected,
				)

	def test_cutting_summary_missing_bound_is_background(self):
		for filters in (
			{"mode": "summary", "from_date": "2026-01-01"},
			{"mode": "summary", "to_date": "2026-01-01"},
		):
			with self.subTest(filters=filters):
				self.assertTrue(needs_background(_cutting_entry(), filters))

	def test_default_date_rule(self):
		entry = _entry(filters={"from_date": "Date", "to_date": "Date"})
		cases = [
			(_dates(None, "2026-01-01", "2026-01-31"), False),
			(_dates(None, "2026-01-01", "2026-02-01"), True),
			({"from_date": "2026-01-01"}, False),
		]
		for filters, expected in cases:
			filters.pop("mode", None)
			with self.subTest(filters=filters):
				self.assertEqual(needs_background(entry, filters), expected)

	def test_schema_without_date_range_defaults_to_never(self):
		self.assertEqual(validate_background(_entry(filters={"date": "Date"})), "never")
		self.assertFalse(needs_background(_entry(filters={"date": "Date"}), {"date": "2026-01-01"}))

	def test_invalid_configs_raise(self):
		base = {
			"days": 10,
			"from_field": "from_date",
			"to_field": "to_date",
			"missing_bound": "immediate",
		}
		cases = [
			{**base, "unexpected": True},
			{**base, "days": 0},
			{**base, "from_field": "mode"},
			{**base, "when": {"unknown": "summary"}},
		]
		for background in cases:
			with self.subTest(background=background), self.assertRaises(frappe.ValidationError):
				validate_background(_entry(background=background))


def _entry(*, background=None, filters=None):
	return {
		"filters": filters
		or {"mode": "Select", "from_date": "Date", "to_date": "Date"},
		"background": background,
	}


def _cutting_entry():
	return _entry(
		background={
			"days": 10,
			"from_field": "from_date",
			"to_field": "to_date",
			"when": {"mode": "summary"},
			"missing_bound": "background",
		}
	)


def _dates(mode, from_date, to_date):
	return {"mode": mode, "from_date": from_date, "to_date": to_date}
