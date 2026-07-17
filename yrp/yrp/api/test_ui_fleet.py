# Copyright (c) 2026, Essdee and contributors
# For license information, please see license.txt

"""Colocated tests for ``yrp.yrp.api.ui_fleet`` (bulk layout assignment).

House rule: NO ``frappe.db.commit()`` anywhere in this file; the test runner
rolls everything back. ``seed_verify_user`` is deliberately NOT exercised
here — it writes a credentials file OUTSIDE the transaction (the file write
would survive the DB rollback and desync from the rolled-back password), and
it is run for real via ``bench execute`` as its own deliverable.
"""

import json

import frappe
from frappe.tests import IntegrationTestCase

from yrp.yrp.api.ui_config import DEFAULT_LAYOUT_NAME, get_skeleton
from yrp.yrp.api.ui_fleet import assign_layout

FLEET_USERS = (
	"yrp-ui-fleet-a@essdee.local",
	"yrp-ui-fleet-b@essdee.local",
	"yrp-ui-fleet-c@essdee.local",
)
DISABLED_USER = "yrp-ui-fleet-disabled@essdee.local"
# FLEET_USERS[0] doubles as the non-SM caller: a bare user with no roles.
ALL_TEST_USERS = FLEET_USERS + (DISABLED_USER,)

FLEET_LAYOUT = "Fleet Test Layout A"
SECOND_LAYOUT = "Fleet Test Layout B"
DISABLED_LAYOUT = "Fleet Test Layout Disabled"


class TestAssignLayout(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.set_user("Administrator")
		for email in ALL_TEST_USERS:
			if not frappe.db.exists("User", email):
				frappe.get_doc(
					{
						"doctype": "User",
						"email": email,
						"first_name": "YRP UI Fleet Test",
						"send_welcome_email": 0,
						"enabled": 0 if email == DISABLED_USER else 1,
					}
				).insert(ignore_permissions=True)
		for layout_name in (DEFAULT_LAYOUT_NAME, FLEET_LAYOUT, SECOND_LAYOUT, DISABLED_LAYOUT):
			if not frappe.db.exists("UI Layout", layout_name):
				frappe.get_doc(
					{
						"doctype": "UI Layout",
						"layout_name": layout_name,
						"config": json.dumps(get_skeleton()),
						"disabled": 1 if layout_name == DISABLED_LAYOUT else 0,
					}
				).insert(ignore_permissions=True)

	def setUp(self):
		frappe.set_user("Administrator")
		# Clean slate per test — the class-level transaction persists records
		# across test methods, so each test resets its own preference rows.
		for user in ALL_TEST_USERS:
			if frappe.db.exists("YRP UI Preference", user):
				frappe.delete_doc(
					"YRP UI Preference", user, ignore_permissions=True, force=True
				)

	def tearDown(self):
		frappe.set_user("Administrator")

	# ── happy path ────────────────────────────────────────────────────────

	def test_happy_path_assigns_every_user_in_one_call(self):
		out = assign_layout(FLEET_LAYOUT, json.dumps(list(FLEET_USERS)))
		self.assertEqual(out["assigned"], list(FLEET_USERS))
		self.assertEqual(out["skipped"], {})
		for user in FLEET_USERS:
			self.assertEqual(
				frappe.db.get_value("YRP UI Preference", user, "layout"), FLEET_LAYOUT
			)
		json.dumps(out)  # wire-safe

	def test_in_process_list_and_duplicates_are_handled(self):
		out = assign_layout(FLEET_LAYOUT, [FLEET_USERS[0], FLEET_USERS[0], FLEET_USERS[1]])
		# Duplicate input rows are processed once, same outcome.
		self.assertEqual(out["assigned"], [FLEET_USERS[0], FLEET_USERS[1]])
		self.assertEqual(out["skipped"], {})

	# ── per-user skips never abort the batch ──────────────────────────────

	def test_unknown_and_disabled_users_skip_with_reasons_batch_continues(self):
		out = assign_layout(
			FLEET_LAYOUT,
			json.dumps([FLEET_USERS[0], "no-such-user@example.com", DISABLED_USER, FLEET_USERS[1]]),
		)
		# The good users on BOTH sides of the bad entries still land.
		self.assertEqual(out["assigned"], [FLEET_USERS[0], FLEET_USERS[1]])
		self.assertIn("unknown", out["skipped"]["no-such-user@example.com"])
		self.assertIn("disabled", out["skipped"][DISABLED_USER])
		self.assertFalse(frappe.db.exists("YRP UI Preference", DISABLED_USER))
		for user in (FLEET_USERS[0], FLEET_USERS[1]):
			self.assertEqual(
				frappe.db.get_value("YRP UI Preference", user, "layout"), FLEET_LAYOUT
			)

	def test_builtin_accounts_are_skipped_never_repointed(self):
		prior = frappe.db.get_value("YRP UI Preference", "Administrator", "layout")
		out = assign_layout(
			FLEET_LAYOUT, json.dumps(["Administrator", "Guest", FLEET_USERS[0]])
		)
		# The batch continues, but neither built-in account is touched.
		self.assertEqual(out["assigned"], [FLEET_USERS[0]])
		self.assertIn("built-in", out["skipped"]["Administrator"])
		self.assertIn("built-in", out["skipped"]["Guest"])
		self.assertEqual(
			frappe.db.get_value("YRP UI Preference", "Administrator", "layout"), prior
		)
		self.assertFalse(frappe.db.exists("YRP UI Preference", "Guest"))

	def test_non_string_entries_skip_with_reason(self):
		out = assign_layout(FLEET_LAYOUT, json.dumps([42, FLEET_USERS[0]]))
		self.assertEqual(out["assigned"], [FLEET_USERS[0]])
		self.assertIn("42", out["skipped"])

	# ── layout validation is a hard gate BEFORE the batch ─────────────────

	def test_disabled_layout_is_rejected_before_touching_anyone(self):
		with self.assertRaises(frappe.ValidationError) as ctx:
			assign_layout(DISABLED_LAYOUT, json.dumps([FLEET_USERS[0]]))
		self.assertIn("disabled", str(ctx.exception))
		self.assertFalse(frappe.db.exists("YRP UI Preference", FLEET_USERS[0]))

	def test_unknown_layout_is_rejected(self):
		with self.assertRaises(frappe.ValidationError):
			assign_layout("No Such Layout XXXXX", json.dumps([FLEET_USERS[0]]))
		with self.assertRaises(frappe.ValidationError):
			assign_layout(None, json.dumps([FLEET_USERS[0]]))

	def test_users_param_is_validated(self):
		with self.assertRaises(frappe.ValidationError):  # unparseable
			assign_layout(FLEET_LAYOUT, "{not json")
		with self.assertRaises(frappe.ValidationError):  # valid JSON, not a list
			assign_layout(FLEET_LAYOUT, json.dumps({"user": FLEET_USERS[0]}))
		with self.assertRaises(frappe.ValidationError):  # empty list = SM mistake
			assign_layout(FLEET_LAYOUT, json.dumps([]))
		with self.assertRaises(frappe.ValidationError):  # None
			assign_layout(FLEET_LAYOUT, None)

	# ── permission gate ───────────────────────────────────────────────────

	def test_non_system_manager_caller_is_rejected(self):
		frappe.set_user(FLEET_USERS[0])  # bare user, no System Manager role
		with self.assertRaises(frappe.PermissionError):
			assign_layout(FLEET_LAYOUT, json.dumps([FLEET_USERS[1]]))
		frappe.set_user("Administrator")
		self.assertFalse(frappe.db.exists("YRP UI Preference", FLEET_USERS[1]))

	# ── upsert semantics: only the layout field is written ────────────────

	def test_reassign_updates_layout_and_preserves_overrides_and_notes(self):
		overrides = {"schema_version": 1, "theme": {"accent": "#EA580C"}}
		frappe.get_doc(
			{
				"doctype": "YRP UI Preference",
				"user": FLEET_USERS[2],
				"layout": FLEET_LAYOUT,
				"overrides": json.dumps(overrides),
				"notes": "hand-tuned by SM",
			}
		).insert(ignore_permissions=True)

		out = assign_layout(SECOND_LAYOUT, json.dumps([FLEET_USERS[2]]))
		self.assertEqual(out["assigned"], [FLEET_USERS[2]])

		row = frappe.db.get_value(
			"YRP UI Preference",
			FLEET_USERS[2],
			["layout", "overrides", "notes"],
			as_dict=True,
		)
		self.assertEqual(row.layout, SECOND_LAYOUT)
		self.assertEqual(json.loads(row.overrides), overrides)
		self.assertEqual(row.notes, "hand-tuned by SM")
