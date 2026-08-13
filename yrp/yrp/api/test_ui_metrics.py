# Copyright (c) 2026, Essdee and contributors
# For license information, please see license.txt

"""Colocated tests for ``yrp.yrp.api.ui_metrics``.

House rule: NO ``frappe.db.commit()`` anywhere in this file; the test runner
rolls everything back. These are integration tests against the dev site's
real data — value assertions therefore cross-check against independent
queries with the SAME filter semantics rather than hardcoding numbers.
"""

import json
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import flt

from yrp.yrp.api import ui_metrics
from yrp.yrp.api.ui_metrics import (
	METRICS,
	get_calculation_registry,
	get_metric_registry,
	get_ui_metrics,
	run_ui_calculation,
)

# Every metric key the task/demo registry promises. New metrics may be added
# over time (registry growth is allowed); these may never disappear.
REQUIRED_METRIC_KEYS = {
	"open_wos",
	"draft_dcs",
	"draft_grns",
	"stock_entries",
	"total_wo",
	"ordered_qty",
	"produced_qty",
	"completion",
	"delayed",
}


class TestMetricsRegistry(IntegrationTestCase):
	"""Registry completeness + response shape."""

	def test_registry_carries_every_promised_metric(self):
		self.assertTrue(REQUIRED_METRIC_KEYS.issubset(set(METRICS)))

	def test_every_registry_entry_is_fully_specified(self):
		for key, spec in METRICS.items():
			self.assertIsInstance(spec["label"], str, key)
			self.assertTrue(spec["label"].strip(), key)
			self.assertIsInstance(spec["doctypes"], list, key)
			self.assertTrue(spec["doctypes"], key)
			self.assertTrue(callable(spec["compute"]), key)
			self.assertTrue(callable(spec["goto"]), key)

	def test_every_goto_is_a_deep_linkable_triple_filter(self):
		for key, spec in METRICS.items():
			goto = spec["goto"]()
			self.assertIsInstance(goto["doctype"], str, key)
			self.assertIsInstance(goto["filters"], list, key)
			for triple in goto["filters"]:
				self.assertIsInstance(triple, list, key)
				self.assertEqual(len(triple), 3, key)
				self.assertIsInstance(triple[0], str, key)  # fieldname
				self.assertIsInstance(triple[1], str, key)  # operator
			# The whole target must survive JSON (it rides to the client).
			json.dumps(goto)

	def test_calculations_registry_completeness(self):
		for name, spec in get_calculation_registry().items():
			self.assertIsInstance(spec["label"], str, name)
			self.assertTrue(callable(spec["run"]), name)

	def test_downstream_registry_hook_is_merged_without_mutating_base(self):
		contribution = {
			"consumer_metric": {
				"label": "Consumer",
				"doctypes": ["Work Order"],
				"compute": lambda: 0,
				"goto": lambda: {"doctype": "Work Order", "filters": []},
			}
		}
		with (
			patch.object(frappe, "get_hooks", return_value=["consumer.metrics"]),
			patch.object(frappe, "get_attr", return_value=lambda: contribution),
		):
			registry = get_metric_registry()
		self.assertIn("consumer_metric", registry)
		self.assertNotIn("consumer_metric", METRICS)

	def test_duplicate_consumer_contribution_is_skipped_atomically(self):
		def spec(label):
			return {
				"label": label,
				"doctypes": ["Work Order"],
				"compute": lambda: 0,
				"goto": lambda: {"doctype": "Work Order", "filters": []},
			}

		contribution = {
			"consumer_metric": spec("Consumer"),
			"open_wos": spec("Must not override base"),
		}
		with (
			patch.object(frappe, "get_hooks", return_value=["consumer.metrics"]),
			patch.object(frappe, "get_attr", return_value=lambda: contribution),
			patch.object(frappe, "log_error"),
		):
			registry = get_metric_registry()
		self.assertNotIn("consumer_metric", registry)
		self.assertIs(registry["open_wos"]["compute"], METRICS["open_wos"]["compute"])

	def test_malformed_consumer_contribution_is_skipped(self):
		with (
			patch.object(frappe, "get_hooks", return_value=["consumer.metrics"]),
			patch.object(frappe, "get_attr", return_value=lambda: {"bad": {"label": "Bad"}}),
			patch.object(frappe, "log_error"),
		):
			registry = get_metric_registry()
		self.assertNotIn("bad", registry)


class TestGetUIMetrics(IntegrationTestCase):
	"""End-to-end endpoint behaviour as Administrator on real site data."""

	def setUp(self):
		frappe.set_user("Administrator")

	def test_all_metrics_returned_with_exact_shape(self):
		out = get_ui_metrics()
		self.assertEqual(out["warnings"], [])
		returned = {m["key"] for m in out["metrics"]}
		self.assertTrue(REQUIRED_METRIC_KEYS.issubset(returned))
		for metric in out["metrics"]:
			self.assertEqual(set(metric), {"key", "label", "value", "goto"})
			self.assertIsInstance(metric["value"], (int, float))
			self.assertGreaterEqual(metric["value"], 0)
			self.assertEqual(set(metric["goto"]), {"doctype", "filters"})
		json.dumps(out)  # must be wire-safe as a whole

	def test_queue_counts_match_the_live_home_filter_semantics(self):
		"""Base-owned live home cards use the same filters and numbers."""
		out = {m["key"]: m["value"] for m in get_ui_metrics()["metrics"]}
		expected = {
			"open_wos": frappe.db.count(
				"Work Order",
				{"docstatus": 1, "status": ("not in", ["Closed", "Cancelled"])},
			),
			"draft_dcs": frappe.db.count("Delivery Challan", {"docstatus": 0}),
			"draft_grns": frappe.db.count("Goods Received Note", {"docstatus": 0}),
			"stock_entries": frappe.db.count("Stock Entry"),
			"total_wo": frappe.db.count("Work Order"),
		}
		for key, value in expected.items():
			self.assertEqual(out[key], value, key)

	def test_completion_is_produced_over_ordered_percent(self):
		out = {m["key"]: m["value"] for m in get_ui_metrics()["metrics"]}
		ordered, produced = out["ordered_qty"], out["produced_qty"]
		expected = round(100 * produced / ordered) if ordered else 0
		self.assertEqual(out["completion"], expected)

	def test_keys_selects_a_subset_in_all_wire_forms(self):
		for keys in (["open_wos", "total_wo"], json.dumps(["open_wos", "total_wo"]), "open_wos, total_wo"):
			out = get_ui_metrics(keys)
			self.assertEqual([m["key"] for m in out["metrics"]], ["open_wos", "total_wo"], keys)
			self.assertEqual(out["warnings"], [])

	def test_unknown_keys_are_omitted_with_warnings_never_an_error(self):
		out = get_ui_metrics(json.dumps(["open_wos", "no_such_metric"]))
		self.assertEqual([m["key"] for m in out["metrics"]], ["open_wos"])
		self.assertEqual(len(out["warnings"]), 1)
		self.assertIn("no_such_metric", out["warnings"][0])

	def test_unhashable_keys_warn_instead_of_raising(self):
		# 2026-07-16 review finding 1: METRICS.get(key) ran BEFORE the
		# isinstance guard, so a list/dict entry raised TypeError (unhashable).
		for bad_keys in ([["a"], "open_wos"], json.dumps([["a"], "open_wos"])):
			out = get_ui_metrics(bad_keys)
			self.assertEqual([m["key"] for m in out["metrics"]], ["open_wos"], bad_keys)
			self.assertEqual(len(out["warnings"]), 1, bad_keys)
			self.assertIn("ignored", out["warnings"][0])
		out = get_ui_metrics([{"x": 1}])
		self.assertEqual(out["metrics"], [])
		self.assertEqual(len(out["warnings"]), 1)

	def test_permission_gating_omits_unreadable_doctypes_silently(self):
		def deny_work_order(doctype, ptype="read", *args, **kwargs):
			return doctype != "Work Order"

		with patch.object(ui_metrics.frappe, "has_permission", side_effect=deny_work_order):
			out = get_ui_metrics(json.dumps(["open_wos", "draft_dcs"]))
		self.assertEqual([m["key"] for m in out["metrics"]], ["draft_dcs"])
		self.assertEqual(out["warnings"], [])  # permission skip is silent

	def test_a_compute_failure_degrades_that_metric_only(self):
		def explode():
			raise RuntimeError("boom")

		broken = dict(METRICS["open_wos"], compute=explode)
		with patch.dict(METRICS, {"open_wos": broken}):
			out = get_ui_metrics(json.dumps(["open_wos", "total_wo"]))
		self.assertEqual([m["key"] for m in out["metrics"]], ["total_wo"])
		self.assertEqual(len(out["warnings"]), 1)
		self.assertIn("open_wos", out["warnings"][0])


class TestRunUICalculation(IntegrationTestCase):
	def setUp(self):
		frappe.set_user("Administrator")

	def test_unknown_calculation_name_throws_cleanly(self):
		with self.assertRaises(frappe.ValidationError) as ctx:
			run_ui_calculation("no_such_calc")
		self.assertIn("Unknown calculation", str(ctx.exception))
		with self.assertRaises(frappe.ValidationError):
			run_ui_calculation(None)


class TestRowLevelPermissionScope(IntegrationTestCase):
	"""Real row-level User Permission scope for generic quantity metrics.

	Finding 2: the produced side used permission-bypassing ``frappe.get_all``
	while the ordered side ran permission-aware ``frappe.get_list`` — a
	restricted user leaked global produced totals and saw >100% completion.
	The throwaway user and User Permissions are created INSIDE the tests (no
	``frappe.db.commit``; the runner transaction rolls them back). Each User
	Permission is removed again via ``addCleanup`` because the class-level
	rollback happens only after ALL tests in the class have run.
	"""

	RESTRICTED_USER = "yrp-ui-metrics-rowperm@essdee.local"

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		if not frappe.db.exists("User", cls.RESTRICTED_USER):
			frappe.get_doc(
				{
					"doctype": "User",
					"email": cls.RESTRICTED_USER,
					"first_name": "YRP UI Metrics Row-Perm Test",
					"send_welcome_email": 0,
					"enabled": 1,
				}
			).insert(ignore_permissions=True)

	def setUp(self):
		frappe.set_user("Administrator")

	def tearDown(self):
		frappe.set_user("Administrator")

	def _restrict_to(self, doctype, value):
		"""User Permission pinning RESTRICTED_USER to one record. Removed after
		the test so the next test starts unrestricted (the User Permission
		controller clears its own user_permissions cache on insert and trash)."""
		perm = frappe.get_doc(
			{
				"doctype": "User Permission",
				"user": self.RESTRICTED_USER,
				"allow": doctype,
				"for_value": value,
				"apply_to_all_doctypes": 1,
			}
		).insert(ignore_permissions=True)
		self.addCleanup(self._drop_restriction, perm.name)

	def _drop_restriction(self, name):
		frappe.set_user("Administrator")
		frappe.delete_doc("User Permission", name, ignore_permissions=True, force=True)

	@staticmethod
	def _engine_produced(wo_names):
		"""Independent recomputation of the receivables math over EXACTLY the
		given parents (permission-bypassing on purpose: it builds the expected
		values for both the restricted and the global view)."""
		rows = frappe.get_all(
			"Work Order Receivables",
			filters={"parenttype": "Work Order", "docstatus": 1, "parent": ["in", wo_names]},
			fields=["parent", "qty", "pending_quantity"],
			parent_doctype="Work Order",
		)
		per_wo = {}
		for row in rows:
			bucket = per_wo.setdefault(row.parent, {"qty": 0.0, "pending": 0.0})
			bucket["qty"] += flt(row.qty)
			bucket["pending"] += max(flt(row.pending_quantity), 0)
		return sum(max(b["qty"] - b["pending"], 0) for b in per_wo.values())

	def test_ordered_and_produced_share_the_user_permission_scope(self):
		all_wos = frappe.get_all(
			"Work Order",
			filters={"docstatus": 1},
			fields=["name", "planned_quantity"],
			order_by="name",
		)
		if len(all_wos) < 2:
			self.skipTest("needs at least two submitted Work Orders on this site")
		target = all_wos[0]
		global_produced = self._engine_produced([w.name for w in all_wos])
		expected_produced = self._engine_produced([target.name])
		expected_ordered = flt(target.planned_quantity)

		self._restrict_to("Work Order", target.name)
		frappe.set_user(self.RESTRICTED_USER)
		# Sanity: the User Permission really bites on the permitted-names fetch.
		self.assertEqual(
			frappe.get_list("Work Order", filters=[["docstatus", "=", 1]], pluck="name", limit=0),
			[target.name],
		)
		out = {
			m["key"]: m["value"]
			for m in get_ui_metrics(["ordered_qty", "produced_qty", "completion"])["metrics"]
		}
		self.assertEqual(flt(out["ordered_qty"]), expected_ordered)
		self.assertEqual(flt(out["produced_qty"]), flt(expected_produced))
		expected_completion = (
			round(100 * expected_produced / expected_ordered) if expected_ordered else 0
		)
		self.assertEqual(out["completion"], expected_completion)
		if flt(global_produced) != flt(expected_produced):
			# The pre-fix behaviour leaked the GLOBAL produced total.
			self.assertNotEqual(flt(out["produced_qty"]), flt(global_produced))

	def test_no_permitted_submitted_wos_yields_zero_quantities(self):
		draft = frappe.get_all("Work Order", filters={"docstatus": 0}, pluck="name", limit=1)
		if not draft:
			self.skipTest("no draft Work Order on this site")
		# Permitted to a DRAFT WO only → the permitted SUBMITTED set is empty.
		self._restrict_to("Work Order", draft[0])
		frappe.set_user(self.RESTRICTED_USER)
		out = {
			m["key"]: m["value"]
			for m in get_ui_metrics(["ordered_qty", "produced_qty", "completion"])["metrics"]
		}
		self.assertEqual(flt(out["ordered_qty"]), 0)
		self.assertEqual(flt(out["produced_qty"]), 0)
		self.assertEqual(out["completion"], 0)

	def test_produced_qty_with_no_permitted_wos_never_queries_unfiltered(self):
		with patch.object(
			ui_metrics, "_wo_child_rows", side_effect=AssertionError("must not query")
		):
			self.assertEqual(ui_metrics._produced_qty([]), 0.0)
