# Copyright (c) 2026, Essdee and contributors
# For license information, please see license.txt

"""Colocated tests for ``yrp.yrp.api.ui_fleet`` (bulk layout assignment +
the floor-role verify user's DB helpers).

House rule: NO ``frappe.db.commit()`` anywhere in this file; the test runner
rolls everything back. The top-level seeders ``seed_verify_user`` /
``seed_floor_verify_user`` are deliberately NOT exercised here — they write a
credentials file (and set a password) OUTSIDE the transaction (the file write
would survive the DB rollback and desync from the rolled-back password), and
are run for real via ``bench execute`` as their own deliverables. Their
transaction-safe DB helpers (role + permission + user wiring) ARE tested.
"""

import json

import frappe
from frappe.tests import IntegrationTestCase

from yrp.yrp.api.ui_config import DEFAULT_LAYOUT_NAME, get_skeleton
from yrp.yrp.api.ui_fleet import (
	FLOOR_ROLE,
	FLOOR_VERIFY_USER,
	_ensure_floor_role,
	_ensure_floor_user,
	_floor_catalog_doctypes,
	_repair_floor_user,
	assign_layout,
)

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


class TestFloorVerifyRole(IntegrationTestCase):
	"""Transaction-safe tests for ``seed_floor_verify_user``'s DB helpers.

	The floor verify user exists so a layout can be checked AS a permission-
	restricted assignee — the emptier reality an SM verification is blind to.
	These tests cover the role/permission/user wiring (pure DB writes the
	runner rolls back); the file-write + password path is bench-execute only.
	"""

	def _user_roles(self):
		# Read Has Role straight from the DB — authoritative, no role cache.
		return set(
			frappe.get_all(
				"Has Role",
				filters={"parent": FLOOR_VERIFY_USER, "parenttype": "User"},
				pluck="role",
			)
		)

	def setUp(self):
		frappe.set_user("Administrator")
		# Order-independent methods: reset the floor user + role + its grants.
		# Drop the user first so no Has Role blocks deleting the role.
		if frappe.db.exists("YRP UI Preference", FLOOR_VERIFY_USER):
			frappe.delete_doc(
				"YRP UI Preference", FLOOR_VERIFY_USER, ignore_permissions=True, force=True
			)
		if frappe.db.exists("User", FLOOR_VERIFY_USER):
			frappe.delete_doc("User", FLOOR_VERIFY_USER, ignore_permissions=True, force=True)
		for name in frappe.get_all("Custom DocPerm", filters={"role": FLOOR_ROLE}, pluck="name"):
			frappe.delete_doc("Custom DocPerm", name, ignore_permissions=True, force=True)
		if frappe.db.exists("Role", FLOOR_ROLE):
			frappe.delete_doc("Role", FLOOR_ROLE, ignore_permissions=True, force=True)

	def tearDown(self):
		frappe.set_user("Administrator")

	# ── the role: read-only over every catalog doctype, nothing else ──────

	def test_catalog_covers_the_sm_only_doctype(self):
		dts = _floor_catalog_doctypes()
		if not dts:
			self.skipTest("no /web catalog declared on this site (bare-yrp)")
		# Item Production Detail grants read to System Manager ONLY today — the
		# very reason a bespoke read-only floor role is required.
		self.assertIn("Item Production Detail", dts)

	def test_ensure_floor_role_grants_readonly_over_every_catalog_doctype(self):
		dts = _floor_catalog_doctypes()
		if not dts:
			self.skipTest("no /web catalog declared on this site (bare-yrp)")
		self.assertTrue(_ensure_floor_role())  # created
		self.assertTrue(frappe.db.exists("Role", FLOOR_ROLE))
		self.assertFalse(frappe.db.get_value("Role", FLOOR_ROLE, "disabled"))
		for dt in dts:
			perm = frappe.db.get_value(
				"Custom DocPerm",
				{"parent": dt, "role": FLOOR_ROLE, "permlevel": 0},
				["read", "write", "create", "delete", "submit"],
				as_dict=True,
			)
			self.assertIsNotNone(perm, f"no floor grant on {dt}")
			self.assertEqual(perm.read, 1, f"floor role must READ {dt}")
			for ptype in ("write", "create", "delete", "submit"):
				self.assertFalse(perm.get(ptype), f"floor role must NOT {ptype} {dt}")

	def test_ensure_floor_role_is_idempotent(self):
		dts = _floor_catalog_doctypes()
		if not dts:
			self.skipTest("no /web catalog declared on this site (bare-yrp)")
		self.assertTrue(_ensure_floor_role())  # first: created
		self.assertFalse(_ensure_floor_role())  # second: already exists
		for dt in dts:
			rows = frappe.get_all(
				"Custom DocPerm",
				filters={"parent": dt, "role": FLOOR_ROLE, "permlevel": 0},
			)
			self.assertEqual(len(rows), 1, f"duplicate floor grant on {dt}")

	# ── the user: floor role, System User, NEVER System Manager ───────────

	def test_floor_user_holds_floor_role_never_system_manager(self):
		_ensure_floor_role()
		self.assertTrue(_ensure_floor_user())  # created
		self.assertEqual(
			frappe.db.get_value("User", FLOOR_VERIFY_USER, "user_type"), "System User"
		)
		self.assertTrue(frappe.db.get_value("User", FLOOR_VERIFY_USER, "enabled"))
		roles = self._user_roles()
		self.assertIn(FLOOR_ROLE, roles)
		self.assertNotIn("System Manager", roles)

	def test_repair_strips_a_stray_system_manager_grant(self):
		_ensure_floor_role()
		_ensure_floor_user()
		doc = frappe.get_doc("User", FLOOR_VERIFY_USER)
		doc.append("roles", {"role": "System Manager"})
		doc.save(ignore_permissions=True)
		self.assertIn("System Manager", self._user_roles())

		_repair_floor_user()
		roles = self._user_roles()
		self.assertNotIn("System Manager", roles)  # the whole point
		self.assertIn(FLOOR_ROLE, roles)

	def test_repair_re_enables_and_restores_the_floor_role(self):
		_ensure_floor_role()
		_ensure_floor_user()
		doc = frappe.get_doc("User", FLOOR_VERIFY_USER)
		doc.enabled = 0
		doc.set("roles", [])
		doc.save(ignore_permissions=True)

		_repair_floor_user()
		self.assertTrue(frappe.db.get_value("User", FLOOR_VERIFY_USER, "enabled"))
		self.assertIn(FLOOR_ROLE, self._user_roles())
