from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from yrp.api.web_reports import run_web_report as api_run_web_report
from yrp.web_reports.jobs import run_web_report
from yrp.web_reports.registry import WebReport, clear_web_report_cache


def allow_test_report():
	return None


def build_test_report(filters):
	return {
		"columns": [{"fieldname": "value", "fieldtype": "Data", "label": "Value"}],
		"result": [{"value": filters.get("value")}],
		"summary": {"rows": 1},
	}


class TestSynchronousWebReports(IntegrationTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		frappe.flags.yrp_web_report_overrides = {
			"test-callable": _callable_entry("test-callable", "never"),
			"test-background": _callable_entry("test-background", "always"),
		}
		clear_web_report_cache()

	def tearDown(self):
		frappe.flags.pop("yrp_web_report_overrides", None)
		clear_web_report_cache()

	def test_callable_returns_sync_envelope(self):
		result = run_web_report("test-callable", {"value": "ok"})
		self.assertEqual(result["mode"], "sync")
		self.assertIsNone(result["job"])
		self.assertEqual(result["result"], [{"value": "ok"}])
		self.assertEqual(result["summary"], {"rows": 1})

	def test_unknown_http_arguments_cannot_force_background(self):
		result = frappe.call(
			api_run_web_report,
			report_key="test-callable",
			filters={"value": "sync"},
			mode="background",
			forged="ignored",
		)
		self.assertEqual(result["mode"], "sync")

	def test_background_decision_returns_a_queued_job(self):
		with (
			patch("yrp.web_reports.jobs.frappe.enqueue") as enqueue,
			patch("yrp.web_reports.jobs.get_workers", return_value=[object()]),
		):
			result = run_web_report("test-background", {})

		self.assertEqual(result["mode"], "background")
		self.assertEqual(result["job"]["status"], "Queued")
		enqueue.assert_called()

	def test_frappe_report_forces_core_synchronous_path(self):
		frappe.flags.yrp_web_report_overrides["test-frappe"] = WebReport(
			key="test-frappe",
			kind="frappe_report",
			report="YRP Stock Balance",
			filters={},
			background="never",
		)
		clear_web_report_cache()
		payload = {"columns": [], "result": [], "report_summary": None, "message": None}
		with (
			patch("yrp.web_reports.jobs.get_report_doc"),
			patch("yrp.web_reports.jobs.run_query_report", return_value=payload) as report_run,
		):
			result = run_web_report("test-frappe", {})
		report_run.assert_called_once_with(
			"YRP Stock Balance",
			filters={},
			ignore_prepared_report=True,
			are_default_filters=True,
		)
		self.assertEqual(result["mode"], "sync")


def _callable_entry(key, background):
	return WebReport(
		key=key,
		kind="callable",
		filters={"value": "Data"},
		runner="yrp.web_reports.test_sync.build_test_report",
		access="yrp.web_reports.test_sync.allow_test_report",
		background=background,
	)
