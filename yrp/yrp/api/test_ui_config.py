# Copyright (c) 2026, Essdee and contributors
# For license information, please see license.txt

"""Colocated tests for ``yrp.yrp.api.ui_config`` — spec §4, §5, §14.

House rule: NO ``frappe.db.commit()`` anywhere in this file; the test runner
rolls everything back. Records that must bypass save-time validation (broken
JSON, too-new schema_version, dangling layout links) are planted with
``frappe.db.set_value`` — exactly how such defects arrive in real life
(drift, manual SQL, a bad sync) — and every test starts from the canonical
state re-asserted in ``setUp``.
"""

import json
from typing import ClassVar
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from yrp.yrp.api import ui_config
from yrp.yrp.api.ui_config import (
	CURRENT_SCHEMA_VERSION,
	DEFAULT_LAYOUT_NAME,
	KILL_SWITCH_KEY,
	OVERRIDABLE_KEYS,
	get_config_for_boot,
	get_my_ui_config,
	get_my_ui_overrides,
	get_skeleton,
	get_ui_config_for,
	merge,
	reset_my_ui_overrides,
	resolve_config,
	save_my_ui_overrides,
)

TEST_USER = "yrp-ui-config-test@essdee.local"
TEST_LAYOUT = "UI Config Test Layout"

LAYOUT_CONFIG = {
	"schema_version": 1,
	"nav": {
		"groups": [
			{
				"id": "Production",
				"label": "Production",
				"items": [
					{"doctype": "Lot", "icon": "pi pi-inbox"},
					{"doctype": "Work Order", "icon": "pi pi-bars"},
				],
			}
		],
		"hidden": {"Work Order": True},
	},
	"screens": {
		"home": {
			"blocks": [{"id": "greet", "type": "home-greeting", "size": "full", "props": {}}],
			"hidden": {},
		}
	},
	"listViews": {},
	"quickCreate": ["Lot"],
	"theme": {"mode": "user", "accent": None},
}

BASE_OVERRIDES = {
	"schema_version": 1,
	"theme": {"accent": "#2563EB"},
	"nav": {"hidden": {"Stock Entry": True}},
}


def _plant_layout_config(value, layout=TEST_LAYOUT):
	"""Bypass save-time validation, like real-life drift would.

	NOTE: MariaDB puts a JSON_VALID CHECK constraint on JSON columns, so only
	syntactically valid JSON (e.g. a non-dict array/scalar) can be planted
	here; the unparseable-JSON branch is covered by calling ``_prepare_layer``
	directly (it can still see raw strings from non-MariaDB sources).
	"""
	frappe.db.set_value("UI Layout", layout, "config", value, update_modified=False)


def _plant_overrides(value, user=TEST_USER):
	frappe.db.set_value("YRP UI Preference", user, "overrides", value, update_modified=False)


def _ui_error_log_count():
	return frappe.db.count("Error Log", {"method": ("like", "UI config:%")})


class TestUIConfigMerge(IntegrationTestCase):
	"""§5 merge properties — pure function, no DB / session / clock."""

	BASE = {
		"schema_version": 1,
		"nav": {"groups": [{"id": "A", "items": [{"doctype": "Lot"}]}], "hidden": {"Stock Entry": True}},
		"quickCreate": ["Lot", "Work Order"],
		"theme": {"mode": "user", "accent": "#111111"},
	}
	DELTA = {
		"schema_version": 1,
		"nav": {"hidden": {"Stock Entry": False, "Lot": True}},
		"quickCreate": ["Delivery Challan"],
		"theme": {"accent": "#2563EB"},
	}

	def test_identity(self):
		self.assertEqual(merge(self.BASE, {}), self.BASE)
		self.assertEqual(merge(self.BASE, None), self.BASE)

	def test_determinism(self):
		self.assertEqual(merge(self.BASE, self.DELTA), merge(self.BASE, self.DELTA))

	def test_idempotence(self):
		once = merge(self.BASE, self.DELTA)
		self.assertEqual(merge(once, self.DELTA), once)

	def test_purity_inputs_never_mutated_and_output_never_aliased(self):
		base = json.loads(json.dumps(self.BASE))
		delta = json.loads(json.dumps(self.DELTA))
		out = merge(base, delta)
		self.assertEqual(base, self.BASE)
		self.assertEqual(delta, self.DELTA)
		# Mutating the output must not reach back into either input.
		out["nav"]["groups"][0]["items"].append({"doctype": "HACK"})
		out["theme"]["mode"] = "dark"
		self.assertEqual(base, self.BASE)
		self.assertEqual(delta, self.DELTA)

	def test_dicts_merge_recursively_upper_layer_wins(self):
		out = merge(self.BASE, self.DELTA)
		# theme merged key-by-key: accent overridden, mode inherited.
		self.assertEqual(out["theme"], {"mode": "user", "accent": "#2563EB"})
		# nav merged: groups inherited from base, hidden composed.
		self.assertEqual(out["nav"]["groups"], self.BASE["nav"]["groups"])

	def test_arrays_replace_wholesale(self):
		out = merge(self.BASE, self.DELTA)
		self.assertEqual(out["quickCreate"], ["Delivery Challan"])

	def test_null_skip_means_no_opinion(self):
		out = merge(self.BASE, {"theme": {"accent": None}, "quickCreate": None})
		self.assertEqual(out["theme"]["accent"], "#111111")
		self.assertEqual(out["quickCreate"], ["Lot", "Work Order"])

	def test_whitelist_filters_unknown_top_level_keys(self):
		delta = {"schema_version": 99, "evil": {"x": 1}, "theme": {"accent": "#2563EB"}}
		out = merge(self.BASE, delta, OVERRIDABLE_KEYS)
		self.assertNotIn("evil", out)
		self.assertEqual(out["schema_version"], 1)  # not in whitelist → base wins
		self.assertEqual(out["theme"]["accent"], "#2563EB")
		# Whitelist applies at the TOP level only; nested keys pass through.
		nested = merge(self.BASE, {"nav": {"custom": 1}}, OVERRIDABLE_KEYS)
		self.assertEqual(nested["nav"]["custom"], 1)

	def test_hidden_reshow_through_dict_merge(self):
		out = merge(self.BASE, self.DELTA)
		# Upper layer re-shows Stock Entry (false wins) and hides Lot; composes.
		self.assertEqual(out["nav"]["hidden"], {"Stock Entry": False, "Lot": True})

	def test_skeleton_guarantees_every_renderer_key(self):
		skeleton = get_skeleton()
		self.assertEqual(
			set(skeleton), {"schema_version", "nav", "screens", "listViews", "quickCreate", "theme"}
		)
		self.assertEqual(skeleton["schema_version"], CURRENT_SCHEMA_VERSION)
		self.assertEqual(skeleton["nav"], {"groups": [], "hidden": {}})
		self.assertEqual(skeleton["screens"], {"home": {"blocks": [], "hidden": {}}})
		self.assertEqual(skeleton["theme"], {"mode": "user", "accent": None})
		# Fresh object every call — callers may mutate their copy.
		self.assertIsNot(get_skeleton()["nav"], skeleton["nav"])


class TestUIConfigUpgraders(IntegrationTestCase):
	"""§2.3 upgrader machinery: key-locality, version stamping, gap handling."""

	@staticmethod
	def _fake_upgrade_v1_to_v2(cfg):
		out = dict(cfg)
		if "quickCreate" in out:
			out["quickCreate"] = [d.upper() for d in out["quickCreate"]]
		return out

	def test_upgrader_is_key_local_on_full_document_and_sparse_delta(self):
		with (
			patch.object(ui_config, "CURRENT_SCHEMA_VERSION", 2),
			patch.dict(ui_config.UPGRADERS, {1: self._fake_upgrade_v1_to_v2}),
		):
			warnings = []
			full = ui_config._prepare_layer(
				json.dumps({"schema_version": 1, "quickCreate": ["lot"], "theme": {"mode": "dark"}}),
				"layout 'x'",
				warnings,
			)
			self.assertEqual(full["quickCreate"], ["LOT"])
			self.assertEqual(full["schema_version"], 2)  # stamped by the machinery
			self.assertEqual(full["theme"], {"mode": "dark"})  # untouched
			self.assertEqual(warnings, [])

			# Sparse delta WITHOUT the key the upgrader targets: key-local means
			# no key appears that was not in the blob (other than the stamp).
			delta = ui_config._prepare_layer(
				json.dumps({"schema_version": 1, "theme": {"accent": "#112233"}}),
				"overrides",
				warnings,
			)
			self.assertEqual(set(delta), {"schema_version", "theme"})
			self.assertEqual(delta["schema_version"], 2)
			self.assertEqual(delta["theme"], {"accent": "#112233"})

	def test_version_gap_without_upgrader_drops_layer(self):
		with patch.object(ui_config, "CURRENT_SCHEMA_VERSION", 2):
			warnings = []
			out = ui_config._prepare_layer(json.dumps({"schema_version": 1}), "overrides", warnings)
			self.assertIsNone(out)
			self.assertTrue(any("no upgrader" in w for w in warnings))


class TestUIConfigResolver(IntegrationTestCase):
	"""resolve_config + endpoints against real records — §4 and every §14 row
	the resolver owns (1–6, 8, 14, 15, 17)."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		if not frappe.db.exists("User", TEST_USER):
			frappe.get_doc(
				{
					"doctype": "User",
					"email": TEST_USER,
					"first_name": "YRP UI Config Test",
					"send_welcome_email": 0,
					"enabled": 1,
				}
			).insert(ignore_permissions=True)
		if not frappe.db.exists("UI Layout", DEFAULT_LAYOUT_NAME):
			frappe.get_doc(
				{
					"doctype": "UI Layout",
					"layout_name": DEFAULT_LAYOUT_NAME,
					"config": json.dumps(get_skeleton()),
				}
			).insert(ignore_permissions=True)
		if not frappe.db.exists("UI Layout", TEST_LAYOUT):
			frappe.get_doc(
				{
					"doctype": "UI Layout",
					"layout_name": TEST_LAYOUT,
					"config": json.dumps(LAYOUT_CONFIG),
				}
			).insert(ignore_permissions=True)
		if not frappe.db.exists("YRP UI Preference", TEST_USER):
			frappe.get_doc(
				{
					"doctype": "YRP UI Preference",
					"user": TEST_USER,
					"layout": TEST_LAYOUT,
					"overrides": json.dumps(BASE_OVERRIDES),
				}
			).insert(ignore_permissions=True)

	def setUp(self):
		frappe.set_user("Administrator")
		# Canonical state, re-asserted so the defect-planting tests stay independent.
		frappe.db.set_value(
			"UI Layout",
			TEST_LAYOUT,
			{"config": json.dumps(LAYOUT_CONFIG), "disabled": 0},
			update_modified=False,
		)
		frappe.db.set_value(
			"YRP UI Preference",
			TEST_USER,
			{"layout": TEST_LAYOUT, "overrides": json.dumps(BASE_OVERRIDES)},
			update_modified=False,
		)

	def tearDown(self):
		frappe.set_user("Administrator")

	# ── §14 rows 1–3: the normal states ─────────────────────────────────

	def test_no_preference_record_resolves_default(self):
		config, meta = resolve_config("no-such-user@example.com")
		self.assertEqual(meta["layout"], DEFAULT_LAYOUT_NAME)
		self.assertFalse(meta["has_preference"])
		self.assertEqual(meta["schema_version"], CURRENT_SCHEMA_VERSION)
		for key in get_skeleton():
			self.assertIn(key, config)

	def test_preference_with_layout_and_overrides_merges_all_layers(self):
		config, meta = resolve_config(TEST_USER)
		self.assertEqual(meta["layout"], TEST_LAYOUT)
		self.assertTrue(meta["has_preference"])
		self.assertEqual(meta["warnings"], [])
		# Layout layer applied…
		self.assertEqual(config["nav"]["groups"], LAYOUT_CONFIG["nav"]["groups"])
		self.assertEqual(config["quickCreate"], ["Lot"])
		# …overrides on top: accent replaced, hidden dicts composed (rule 1).
		self.assertEqual(config["theme"], {"mode": "user", "accent": "#2563EB"})
		self.assertEqual(config["nav"]["hidden"], {"Work Order": True, "Stock Entry": True})

	def test_layout_link_empty_falls_to_default_with_overrides_on_top(self):
		frappe.db.set_value("YRP UI Preference", TEST_USER, "layout", "", update_modified=False)
		config, meta = resolve_config(TEST_USER)
		self.assertEqual(meta["layout"], DEFAULT_LAYOUT_NAME)
		self.assertTrue(meta["has_preference"])
		self.assertEqual(config["theme"]["accent"], "#2563EB")  # overrides still apply

	def test_empty_overrides_resolve_to_pure_layout(self):
		_plant_overrides(None)
		config, meta = resolve_config(TEST_USER)
		self.assertEqual(meta["warnings"], [])
		self.assertIsNone(config["theme"]["accent"])
		self.assertEqual(config["nav"]["hidden"], {"Work Order": True})

	# ── §14 rows 4–6, 8, 14: degradations — always with a trace ─────────

	def test_unparseable_overrides_json_drops_that_layer_with_trace(self):
		before = _ui_error_log_count()
		warnings = []
		self.assertIsNone(ui_config._prepare_layer("{this is not json", "overrides", warnings))
		self.assertTrue(any("overrides" in w and "invalid JSON" in w for w in warnings))
		self.assertGreater(_ui_error_log_count(), before)

	def test_non_dict_overrides_drop_that_layer_only(self):
		before = _ui_error_log_count()
		_plant_overrides("[1, 2, 3]")
		config, meta = resolve_config(TEST_USER)
		self.assertEqual(meta["layout"], TEST_LAYOUT)
		self.assertIsNone(config["theme"]["accent"])  # un-tweaked layout
		self.assertEqual(config["nav"]["groups"], LAYOUT_CONFIG["nav"]["groups"])
		self.assertTrue(any("overrides" in w for w in meta["warnings"]))
		self.assertGreater(_ui_error_log_count(), before)

	def test_broken_layout_config_falls_back_to_default_keeping_overrides(self):
		before = _ui_error_log_count()
		_plant_layout_config(json.dumps([1, 2, 3]))  # valid JSON, not an object
		config, meta = resolve_config(TEST_USER)
		self.assertEqual(meta["layout"], DEFAULT_LAYOUT_NAME)
		self.assertEqual(config["theme"]["accent"], "#2563EB")  # overrides on the fallback
		self.assertTrue(any(TEST_LAYOUT in w for w in meta["warnings"]))
		self.assertGreater(_ui_error_log_count(), before)

	def test_missing_layout_record_falls_back_to_default(self):
		frappe.db.set_value(
			"YRP UI Preference", TEST_USER, "layout", "No Such Layout", update_modified=False
		)
		config, meta = resolve_config(TEST_USER)
		self.assertEqual(meta["layout"], DEFAULT_LAYOUT_NAME)
		self.assertTrue(any("No Such Layout" in w for w in meta["warnings"]))

	def test_disabled_layout_falls_back_to_default(self):
		frappe.db.set_value("UI Layout", TEST_LAYOUT, "disabled", 1, update_modified=False)
		config, meta = resolve_config(TEST_USER)
		self.assertEqual(meta["layout"], DEFAULT_LAYOUT_NAME)
		self.assertTrue(any("disabled" in w for w in meta["warnings"]))

	def test_unknown_override_keys_are_dropped_and_warned(self):
		_plant_overrides(
			json.dumps({"schema_version": 1, "navv": {"hidden": {}}, "theme": {"accent": "#2563EB"}})
		)
		config, meta = resolve_config(TEST_USER)
		self.assertNotIn("navv", config)
		self.assertEqual(config["theme"]["accent"], "#2563EB")  # rest of overrides applies
		self.assertTrue(any("navv" in w for w in meta["warnings"]))

	def test_too_new_layout_schema_version_drops_layer_with_trace(self):
		before = _ui_error_log_count()
		newer = dict(LAYOUT_CONFIG, schema_version=CURRENT_SCHEMA_VERSION + 98)
		_plant_layout_config(json.dumps(newer))
		config, meta = resolve_config(TEST_USER)
		self.assertEqual(meta["layout"], DEFAULT_LAYOUT_NAME)  # never guess-interpreted forward
		self.assertTrue(any("newer" in w for w in meta["warnings"]))
		self.assertGreater(_ui_error_log_count(), before)

	def test_missing_schema_version_treated_as_current_with_warning(self):
		legacy = {k: v for k, v in LAYOUT_CONFIG.items() if k != "schema_version"}
		_plant_layout_config(json.dumps(legacy))
		config, meta = resolve_config(TEST_USER)
		self.assertEqual(meta["layout"], TEST_LAYOUT)  # still applied
		self.assertEqual(config["nav"]["groups"], LAYOUT_CONFIG["nav"]["groups"])
		self.assertTrue(any("missing schema_version" in w for w in meta["warnings"]))

	# ── never-raises + kill switch (§4, §14 row 17) ──────────────────────

	def test_resolve_config_never_raises_on_garbage_identities(self):
		for garbage in (None, "", 0, 123, 4.5, object(), ["Administrator"], {"user": "x"}):
			config, meta = resolve_config(garbage)
			for key in get_skeleton():
				self.assertIn(key, config)
			self.assertIn("warnings", meta)

	def test_resolve_config_never_raises_when_db_explodes(self):
		with patch.object(frappe.db, "get_value", side_effect=RuntimeError("boom")):
			config, meta = resolve_config("Administrator")
		self.assertEqual(config, get_skeleton())
		self.assertIsNone(meta["layout"])
		self.assertTrue(any("resolver failed" in w for w in meta["warnings"]))

	def test_kill_switch_serves_skeleton_and_reads_no_records(self):
		original = frappe.conf.get(KILL_SWITCH_KEY)
		frappe.conf[KILL_SWITCH_KEY] = 1  # monkeypatch, never a site-config write
		try:
			with patch.object(frappe.db, "get_value", side_effect=AssertionError("record read")) as db_read:
				config, meta = resolve_config(TEST_USER)
			self.assertEqual(db_read.call_count, 0)  # "skip all records" (§4.1)
			self.assertEqual(config, get_skeleton())
			self.assertIsNone(meta["layout"])
			self.assertFalse(meta["has_preference"])
			self.assertIn("ui config disabled by site config", meta["warnings"])
		finally:
			if original is None:
				frappe.conf.pop(KILL_SWITCH_KEY, None)
			else:
				frappe.conf[KILL_SWITCH_KEY] = original

	# ── whitelisted endpoints (§4.1, §4.2) + boot hook (§4.3) ────────────

	def test_get_my_ui_config_uses_session_identity(self):
		frappe.set_user(TEST_USER)
		payload = get_my_ui_config()
		self.assertEqual(set(payload), {"config", "meta"})
		self.assertEqual(payload["meta"]["layout"], TEST_LAYOUT)
		self.assertTrue(payload["meta"]["has_preference"])
		self.assertEqual(payload["config"]["theme"]["accent"], "#2563EB")

	def test_get_ui_config_for_is_sm_only(self):
		frappe.set_user("Guest")
		with self.assertRaises(frappe.PermissionError):
			get_ui_config_for(user=TEST_USER)

	def test_get_ui_config_for_params_are_mutually_exclusive(self):
		with self.assertRaises(frappe.ValidationError):
			get_ui_config_for(user=TEST_USER, layout=TEST_LAYOUT)
		with self.assertRaises(frappe.ValidationError):
			get_ui_config_for()

	def test_get_ui_config_for_unknown_or_disabled_user_throws(self):
		with self.assertRaises(frappe.ValidationError):
			get_ui_config_for(user="no-such-user@example.com")
		frappe.db.set_value("User", TEST_USER, "enabled", 0, update_modified=False)
		try:
			with self.assertRaises(frappe.ValidationError):
				get_ui_config_for(user=TEST_USER)
		finally:
			frappe.db.set_value("User", TEST_USER, "enabled", 1, update_modified=False)

	def test_get_ui_config_for_user_returns_their_layers_and_their_perm_hints(self):
		payload = get_ui_config_for(user=TEST_USER)
		self.assertEqual(set(payload), {"config", "meta", "perm_hints"})
		self.assertEqual(payload["meta"]["layout"], TEST_LAYOUT)
		self.assertEqual(payload["config"]["theme"]["accent"], "#2563EB")
		hints = payload["perm_hints"]
		self.assertEqual(set(hints), {"can_read", "can_create"})
		# Computed AS the target user, never as the SM caller: Lot/Work Order
		# grant read to role "All" (so the roleless user may read), but create
		# needs a real role — Administrator (the caller) would have both full.
		self.assertEqual(hints["can_create"], [])
		self.assertTrue(set(hints["can_read"]) <= {"Lot", "Work Order"})

	def test_get_ui_config_for_layout_previews_bare_layout(self):
		payload = get_ui_config_for(layout=TEST_LAYOUT)
		self.assertEqual(payload["meta"]["layout"], TEST_LAYOUT)
		self.assertFalse(payload["meta"]["has_preference"])
		# No overrides layer: layout verbatim over the skeleton.
		self.assertIsNone(payload["config"]["theme"]["accent"])
		self.assertEqual(payload["config"]["nav"]["groups"], LAYOUT_CONFIG["nav"]["groups"])
		# Perm hints = the caller's own (Administrator sees everything) over
		# nav + quickCreate doctypes of the RESOLVED config.
		self.assertEqual(payload["perm_hints"]["can_read"], ["Lot", "Work Order"])
		self.assertEqual(payload["perm_hints"]["can_create"], ["Lot", "Work Order"])

	def test_get_ui_config_for_unknown_disabled_or_broken_layout_fails_loudly(self):
		with self.assertRaises(frappe.ValidationError):
			get_ui_config_for(layout="No Such Layout")
		frappe.db.set_value("UI Layout", TEST_LAYOUT, "disabled", 1, update_modified=False)
		with self.assertRaises(frappe.ValidationError):
			get_ui_config_for(layout=TEST_LAYOUT)
		frappe.db.set_value("UI Layout", TEST_LAYOUT, "disabled", 0, update_modified=False)
		_plant_layout_config(json.dumps("just a string"))  # valid JSON, not an object
		with self.assertRaises(frappe.ValidationError):
			get_ui_config_for(layout=TEST_LAYOUT)

	def test_get_config_for_boot_wraps_resolver_and_never_raises(self):
		frappe.set_user(TEST_USER)
		payload = get_config_for_boot()
		self.assertEqual(set(payload), {"config", "meta"})
		self.assertEqual(payload["meta"]["layout"], TEST_LAYOUT)
		frappe.set_user("Administrator")
		with patch.object(ui_config, "resolve_config", side_effect=RuntimeError("boom")):
			self.assertIsNone(get_config_for_boot())


class TestUIConfigSelfService(IntegrationTestCase):
	"""Self-service Knobs endpoints (locked decision 2026-07-15):
	``save_my_ui_overrides`` / ``reset_my_ui_overrides`` write ONLY the session
	user's own ``YRP UI Preference.overrides`` — never another user's record,
	never the ``layout``/``notes`` fields, never non-whitelisted keys."""

	SELF_USER = "yrp-ui-selfservice@essdee.local"
	OTHER_USER = "yrp-ui-selfservice-other@essdee.local"
	OTHER_OVERRIDES = {"schema_version": 1, "theme": {"accent": "#654321"}}

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		for email in (cls.SELF_USER, cls.OTHER_USER):
			if not frappe.db.exists("User", email):
				frappe.get_doc(
					{
						"doctype": "User",
						"email": email,
						"first_name": "YRP UI Self-Service Test",
						"send_welcome_email": 0,
						"enabled": 1,
					}
				).insert(ignore_permissions=True)
		if not frappe.db.exists("UI Layout", DEFAULT_LAYOUT_NAME):
			frappe.get_doc(
				{
					"doctype": "UI Layout",
					"layout_name": DEFAULT_LAYOUT_NAME,
					"config": json.dumps(get_skeleton()),
				}
			).insert(ignore_permissions=True)
		if not frappe.db.exists("UI Layout", TEST_LAYOUT):
			frappe.get_doc(
				{
					"doctype": "UI Layout",
					"layout_name": TEST_LAYOUT,
					"config": json.dumps(LAYOUT_CONFIG),
				}
			).insert(ignore_permissions=True)

	def setUp(self):
		frappe.set_user("Administrator")
		# Canonical state: SELF_USER starts with NO preference (each test builds
		# the record it needs); OTHER_USER always holds the untouchable record.
		if frappe.db.exists("YRP UI Preference", self.SELF_USER):
			frappe.delete_doc(
				"YRP UI Preference", self.SELF_USER, ignore_permissions=True, force=True
			)
		if not frappe.db.exists("YRP UI Preference", self.OTHER_USER):
			frappe.get_doc(
				{
					"doctype": "YRP UI Preference",
					"user": self.OTHER_USER,
					"overrides": json.dumps(self.OTHER_OVERRIDES),
				}
			).insert(ignore_permissions=True)
		else:
			frappe.db.set_value(
				"YRP UI Preference",
				self.OTHER_USER,
				{"layout": "", "overrides": json.dumps(self.OTHER_OVERRIDES), "notes": ""},
				update_modified=False,
			)

	def tearDown(self):
		frappe.set_user("Administrator")

	def _make_own_preference(self, layout="", overrides=None, notes=""):
		"""Plant SELF_USER's record as an SM would (Desk path), for update tests."""
		frappe.get_doc(
			{
				"doctype": "YRP UI Preference",
				"user": self.SELF_USER,
				"layout": layout,
				"overrides": json.dumps(overrides) if overrides else None,
				"notes": notes,
			}
		).insert(ignore_permissions=True)

	def _stored(self, field, user=None):
		return frappe.db.get_value("YRP UI Preference", user or self.SELF_USER, field)

	# ── identity: Guest rejected, other users unreachable ────────────────

	def test_guest_is_rejected_by_both_endpoints(self):
		frappe.set_user("Guest")
		with self.assertRaises(frappe.PermissionError):
			save_my_ui_overrides({"schema_version": 1, "theme": {"accent": "#2563EB"}})
		with self.assertRaises(frappe.PermissionError):
			reset_my_ui_overrides()

	def test_another_users_record_is_unreachable(self):
		frappe.set_user(self.SELF_USER)
		# No user parameter exists; the save lands on the CALLER's record only.
		save_my_ui_overrides({"schema_version": 1, "theme": {"accent": "#111111"}})
		self.assertEqual(
			json.loads(self._stored("overrides", self.OTHER_USER)), self.OTHER_OVERRIDES
		)
		# Reset likewise touches only the caller's record.
		reset_my_ui_overrides()
		self.assertTrue(frappe.db.exists("YRP UI Preference", self.OTHER_USER))
		self.assertEqual(
			json.loads(self._stored("overrides", self.OTHER_USER)), self.OTHER_OVERRIDES
		)

	# ── save: create → update, JSON-string input, resolved payload back ──

	def test_save_creates_own_record_with_layout_left_empty(self):
		frappe.set_user(self.SELF_USER)
		# String input = the over-the-wire shape frappe hands a whitelisted fn.
		payload = save_my_ui_overrides(
			json.dumps({"schema_version": 1, "theme": {"accent": "#123456"}})
		)
		row = frappe.db.get_value(
			"YRP UI Preference", self.SELF_USER, ["user", "layout", "overrides"], as_dict=True
		)
		self.assertIsNotNone(row)
		self.assertEqual(row.user, self.SELF_USER)
		self.assertFalse(row.layout)  # left empty → resolver falls back to Default
		self.assertEqual(
			json.loads(row.overrides), {"schema_version": 1, "theme": {"accent": "#123456"}}
		)
		# Resolved payload comes back so the client can re-render immediately.
		self.assertEqual(set(payload), {"config", "meta"})
		self.assertEqual(payload["meta"]["layout"], DEFAULT_LAYOUT_NAME)
		self.assertTrue(payload["meta"]["has_preference"])
		self.assertEqual(payload["config"]["theme"]["accent"], "#123456")

	def test_save_then_save_again_updates_the_same_record(self):
		frappe.set_user(self.SELF_USER)
		save_my_ui_overrides({"schema_version": 1, "theme": {"accent": "#111111"}})
		payload = save_my_ui_overrides({"schema_version": 1, "theme": {"accent": "#222222"}})
		self.assertEqual(frappe.db.count("YRP UI Preference", {"user": self.SELF_USER}), 1)
		self.assertEqual(
			json.loads(self._stored("overrides"))["theme"]["accent"], "#222222"
		)
		self.assertEqual(payload["config"]["theme"]["accent"], "#222222")

	def test_save_survives_losing_the_first_insert_race(self):
		# The savepoint-upsert branch: exists() says no record, but the insert
		# collides on the PK (autoname field:user) — the save must still land.
		frappe.set_user(self.SELF_USER)
		self._make_own_preference(overrides={"schema_version": 1, "theme": {"accent": "#333333"}})
		real_exists = frappe.db.exists

		def exists_missing_own_pref(doctype, name=None, *args, **kwargs):
			if doctype == "YRP UI Preference" and name == self.SELF_USER:
				return None
			return real_exists(doctype, name, *args, **kwargs)

		with patch.object(frappe.db, "exists", side_effect=exists_missing_own_pref):
			save_my_ui_overrides({"schema_version": 1, "theme": {"accent": "#444444"}})
		self.assertEqual(frappe.db.count("YRP UI Preference", {"user": self.SELF_USER}), 1)
		self.assertEqual(
			json.loads(self._stored("overrides"))["theme"]["accent"], "#444444"
		)

	# ── bounding: whitelist filter, layout/notes untouched, hard errors ──

	def test_save_filters_non_whitelisted_keys_and_warns(self):
		frappe.set_user(self.SELF_USER)
		payload = save_my_ui_overrides(
			{
				"schema_version": 1,
				"evil": {"x": 1},
				"layout": "Sneaky Layout In JSON",
				"theme": {"accent": "#2563EB"},
			}
		)
		stored = json.loads(self._stored("overrides"))
		self.assertEqual(set(stored), {"schema_version", "theme"})  # evil + layout dropped
		self.assertNotIn("evil", payload["config"])
		self.assertEqual(payload["config"]["theme"]["accent"], "#2563EB")
		self.assertTrue(any("evil" in w for w in payload["meta"]["warnings"]))

	def test_save_never_touches_layout_or_notes_fields(self):
		self._make_own_preference(
			layout=TEST_LAYOUT,
			overrides={"schema_version": 1, "theme": {"accent": "#111111"}},
			notes="assigned by SM",
		)
		frappe.set_user(self.SELF_USER)
		payload = save_my_ui_overrides({"schema_version": 1, "theme": {"accent": "#2563EB"}})
		row = frappe.db.get_value(
			"YRP UI Preference",
			self.SELF_USER,
			["layout", "notes", "overrides"],
			as_dict=True,
		)
		self.assertEqual(row.layout, TEST_LAYOUT)  # untouched
		self.assertEqual(row.notes, "assigned by SM")  # untouched
		self.assertEqual(json.loads(row.overrides)["theme"]["accent"], "#2563EB")
		self.assertEqual(payload["meta"]["layout"], TEST_LAYOUT)

	def test_save_hard_validation_errors_throw_and_store_nothing(self):
		frappe.set_user(self.SELF_USER)
		for bad in (
			None,  # not an object
			"{this is not json",  # unparseable string
			json.dumps([1, 2, 3]),  # valid JSON, not an object
			{"theme": {"accent": "#2563EB"}},  # missing schema_version
			{"schema_version": 1, "theme": {"accent": "red"}},  # bad accent format
			{"schema_version": 1, "nav": "sidebar"},  # nav must be an object
		):
			with self.assertRaises(frappe.ValidationError):
				save_my_ui_overrides(bad)
		self.assertFalse(frappe.db.exists("YRP UI Preference", self.SELF_USER))

	def test_save_rejects_oversize_overrides_and_stores_nothing(self):
		# M6: any authenticated user reaches this endpoint — a >256 KB payload
		# is hard-rejected BEFORE validation/storage, for both wire shapes.
		frappe.set_user(self.SELF_USER)
		big_hidden = {f"DocType {i}": True for i in range(20000)}  # ~ 400 KB serialized
		big = {"schema_version": 1, "nav": {"hidden": big_hidden}}
		self.assertGreater(
			len(json.dumps(big, ensure_ascii=False).encode("utf-8")), ui_config.MAX_OVERRIDES_BYTES
		)
		for shape in (json.dumps(big), big):  # over-the-wire string AND direct dict
			with self.assertRaises(frappe.ValidationError):
				save_my_ui_overrides(shape)
		self.assertFalse(frappe.db.exists("YRP UI Preference", self.SELF_USER))
		# An in-budget save on the same session still lands.
		save_my_ui_overrides({"schema_version": 1, "theme": {"accent": "#123456"}})
		self.assertTrue(frappe.db.exists("YRP UI Preference", self.SELF_USER))

	def test_write_endpoints_are_post_only_reads_stay_gettable(self):
		# M5: a GET save/reset would return "saved" config and then be rolled
		# back by Frappe's end-of-request handling (app.py commits only UNSAFE
		# methods) — the client would render a write that never happened.
		methods_of = frappe.allowed_http_methods_for_whitelisted_func
		self.assertEqual(methods_of[save_my_ui_overrides], ["POST"])
		self.assertEqual(methods_of[reset_my_ui_overrides], ["POST"])
		self.assertIn("GET", methods_of[get_my_ui_overrides])
		self.assertIn("GET", methods_of[get_my_ui_config])

	# ── reset: delete bare records, blank the rest, no-op without one ────

	def test_reset_deletes_record_holding_nothing_but_overrides(self):
		frappe.set_user(self.SELF_USER)
		save_my_ui_overrides({"schema_version": 1, "theme": {"accent": "#123456"}})
		payload = reset_my_ui_overrides()
		self.assertFalse(frappe.db.exists("YRP UI Preference", self.SELF_USER))
		self.assertFalse(payload["meta"]["has_preference"])
		self.assertEqual(payload["meta"]["layout"], DEFAULT_LAYOUT_NAME)
		self.assertIsNone(payload["config"]["theme"]["accent"])

	def test_reset_keeps_record_with_layout_and_blanks_only_overrides(self):
		self._make_own_preference(
			layout=TEST_LAYOUT, overrides={"schema_version": 1, "theme": {"accent": "#111111"}}
		)
		frappe.set_user(self.SELF_USER)
		payload = reset_my_ui_overrides()
		row = frappe.db.get_value(
			"YRP UI Preference", self.SELF_USER, ["layout", "overrides"], as_dict=True
		)
		self.assertIsNotNone(row)  # record survives — it still carries the layout link
		self.assertEqual(row.layout, TEST_LAYOUT)
		self.assertFalse(row.overrides)
		self.assertEqual(payload["meta"]["layout"], TEST_LAYOUT)
		self.assertTrue(payload["meta"]["has_preference"])
		self.assertIsNone(payload["config"]["theme"]["accent"])

	def test_reset_keeps_record_with_notes_and_blanks_only_overrides(self):
		self._make_own_preference(
			overrides={"schema_version": 1, "theme": {"accent": "#111111"}},
			notes="SM breadcrumb — keep",
		)
		frappe.set_user(self.SELF_USER)
		reset_my_ui_overrides()
		row = frappe.db.get_value(
			"YRP UI Preference", self.SELF_USER, ["notes", "overrides"], as_dict=True
		)
		self.assertIsNotNone(row)
		self.assertEqual(row.notes, "SM breadcrumb — keep")
		self.assertFalse(row.overrides)

	def test_reset_without_a_record_is_a_clean_no_op(self):
		frappe.set_user(self.SELF_USER)
		payload = reset_my_ui_overrides()
		self.assertFalse(frappe.db.exists("YRP UI Preference", self.SELF_USER))
		self.assertEqual(set(payload), {"config", "meta"})
		self.assertFalse(payload["meta"]["has_preference"])
		self.assertEqual(payload["meta"]["layout"], DEFAULT_LAYOUT_NAME)

	# ── get: the stored sparse delta, session-scoped (Knobs panel hydration) ──

	def test_get_my_ui_overrides_returns_the_stored_delta(self):
		frappe.set_user(self.SELF_USER)
		# No record → empty delta, not an error (the panel starts pristine).
		self.assertEqual(get_my_ui_overrides(), {"overrides": {}, "warnings": []})
		delta = {"schema_version": 1, "theme": {"accent": "#2563EB"}}
		save_my_ui_overrides(delta)
		# Round-trip: exactly what was stored comes back — the RAW delta, not
		# the resolved config (no layout keys mixed in).
		self.assertEqual(get_my_ui_overrides(), {"overrides": delta, "warnings": []})

	def test_get_my_ui_overrides_is_session_scoped_and_rejects_guest(self):
		frappe.set_user("Guest")
		with self.assertRaises(frappe.PermissionError):
			get_my_ui_overrides()
		# No user parameter exists: SELF_USER can never read OTHER_USER's delta.
		frappe.set_user(self.SELF_USER)
		self.assertEqual(get_my_ui_overrides()["overrides"], {})

	def test_get_my_ui_overrides_degrades_broken_layer_to_empty_with_warning(self):
		self._make_own_preference()
		_plant_overrides(json.dumps([1, 2, 3]), user=self.SELF_USER)  # valid JSON, not a dict
		frappe.set_user(self.SELF_USER)
		payload = get_my_ui_overrides()
		self.assertEqual(payload["overrides"], {})
		self.assertTrue(payload["warnings"])  # the drop reason is surfaced


class TestUIConfigShellKeys(IntegrationTestCase):
	"""Layout-tier shell knobs the engine consumes (2026-07-15 Demo-7 review,
	IMPORTANT-1): ``chrome``/``realtime``/``dateFormat`` are real LAYOUT_KEYS —
	a layout carrying them saves warning-free — while genuinely-dead keys keep
	warning, and the personal overrides layer still filters all three."""

	# The live Demo 7 shapes (chrome strip, Live indicator, dd-mm-yyyy). Since
	# item 17, realtime carries only `enabled` here: intervalMs/toast are
	# RESERVED knob names whose presence now draws the explicit notice — the
	# Demo 7 record was cleaned of them the same day (they were dead keys).
	# The reserved-notice family has its own tests in TestUIConfigItem17*.
	DEMO7_SHELL = {
		"chrome": {"themeToggle": True, "search": True},
		"realtime": {"enabled": True},
		"dateFormat": "dd-mm-yyyy",
	}

	@staticmethod
	def _layout_warnings(extra):
		return ui_config.validate_config(dict(LAYOUT_CONFIG, **extra), layer="layout")

	def test_layout_with_chrome_realtime_dateformat_saves_warning_free(self):
		self.assertEqual(self._layout_warnings(self.DEMO7_SHELL), [])

	def test_genuinely_dead_top_level_keys_still_warn(self):
		# The Demo 7 record's dead keys — the engine reads none of these; the
		# warning is the SM's only signal that they do nothing. (2026-07-16:
		# detail/entry/dcEntry/actions were promoted to real STRUCTURAL_KEYS
		# and moved to TestUIConfigStructuralKnobs.)
		dead = {"blocks": [], "links": {}}
		warnings = self._layout_warnings(dict(self.DEMO7_SHELL, **dead))
		self.assertEqual(len(warnings), len(dead))
		for key in dead:
			self.assertTrue(
				any(f"unknown top-level key '{key}'" in w for w in warnings),
				f"missing dead-key warning for {key}",
			)

	def test_nav_position_off_vocabulary_warns_softly(self):
		for good in ("sidebar", "topbar"):
			nav = dict(LAYOUT_CONFIG["nav"], position=good)
			self.assertEqual(self._layout_warnings({"nav": nav}), [])
		nav = dict(LAYOUT_CONFIG["nav"], position="bottom")
		warnings = self._layout_warnings({"nav": nav})
		self.assertEqual(len(warnings), 1)
		self.assertIn("nav.position", warnings[0])
		self.assertIn("'bottom'", warnings[0])
		# Same soft rule on the overrides layer (the Knobs panel writes nav.position).
		warnings = ui_config.validate_config(
			{"schema_version": 1, "nav": {"position": "bottom"}}, layer="overrides"
		)
		self.assertEqual(len(warnings), 1)
		self.assertIn("nav.position", warnings[0])

	def test_shell_key_shapes_get_soft_checks(self):
		cases = {
			"chrome must be an object": {"chrome": "yes"},
			"chrome.search should be a boolean": {"chrome": {"search": "yes"}},
			"unknown key 'sparkles' inside chrome": {"chrome": {"sparkles": True}},
			"realtime must be an object": {"realtime": 1},
			"realtime.enabled should be a boolean": {"realtime": {"enabled": "on"}},
			"unknown key 'pulse' inside realtime": {"realtime": {"pulse": 1}},
			"dateFormat 'mm/dd/yyyy' is not one of": {"dateFormat": "mm/dd/yyyy"},
		}
		for fragment, extra in cases.items():
			warnings = self._layout_warnings(extra)
			self.assertEqual(len(warnings), 1, f"{extra}: {warnings}")
			self.assertIn(fragment, warnings[0])
		# A mis-typed RESERVED knob still gets its type check — alongside the
		# item-17 reserved notice (two warnings, both true).
		warnings = self._layout_warnings({"realtime": {"intervalMs": "fast"}})
		self.assertEqual(len(warnings), 2, warnings)
		self.assertTrue(any("realtime.intervalMs should be a number" in w for w in warnings))
		self.assertTrue(any("realtime.intervalMs is RESERVED" in w for w in warnings))

	def test_overrides_layer_still_rejects_the_shell_keys(self):
		# NOT overridable: one unknown-key warning each (no duplicate shape
		# warnings), and the save-side whitelist filter drops them from storage.
		warnings = ui_config.validate_config(
			{"schema_version": 1, **self.DEMO7_SHELL}, layer="overrides"
		)
		self.assertEqual(len(warnings), 3)
		for key in ("chrome", "realtime", "dateFormat"):
			self.assertTrue(any(f"unknown key '{key}'" in w for w in warnings), key)
		for key in self.DEMO7_SHELL:
			self.assertNotIn(key, ui_config.OVERRIDABLE_KEYS)
			self.assertIn(key, ui_config.LAYOUT_KEYS)


class TestUIConfigStructuralKnobs(IntegrationTestCase):
	"""Structural knobs (2026-07-16, spec §6.4): ``detail``/``entry``/``dcEntry``/
	``actions`` are real layout-tier STRUCTURAL_KEYS — a layout carrying them
	saves warning-free; unknown enum values soft-warn (the client falls back to
	today's behaviour); only structurally-wrong TYPES hard-error; and the
	personal overrides layer still warns on + filters all four (they are
	deliberately NOT in OVERRIDABLE_KEYS this iteration)."""

	# A fully-populated, all-valid structural config (every CONSUMED enum
	# exercised). `actions.dialogPosition` left out deliberately: it is a
	# RESERVED knob since item 17 — carrying it draws the explicit notice
	# (covered below and in TestUIConfigItem17ReservedKnobs).
	VALID_STRUCTURAL: ClassVar[dict] = {
		"detail": {"position": "right"},
		"entry": {"mode": "popup", "popupPosition": "top-right"},
		"dcEntry": {"variant": "size-matrix", "qtyControl": "input", "supplierPicker": "chips"},
		"actions": {
			"placement": "floating",
			"items": [
				"create_grn",
				"create_dc",
				"more_menu",
				"ewaybill_menu",
				"send_sms",
				"send_whatsapp",
				"cancel_doc",
			],
		},
	}

	@staticmethod
	def _layout_warnings(extra):
		return ui_config.validate_config(dict(LAYOUT_CONFIG, **extra), layer="layout")

	# ── accept-valid + absent-keys-silent ─────────────────────────────────

	def test_layout_with_all_four_structural_knobs_saves_warning_free(self):
		self.assertEqual(self._layout_warnings(self.VALID_STRUCTURAL), [])

	def test_every_enum_value_in_every_vocabulary_is_accepted(self):
		for position in ui_config.DETAIL_POSITIONS:
			self.assertEqual(self._layout_warnings({"detail": {"position": position}}), [], position)
		for mode in ui_config.ENTRY_MODES:
			self.assertEqual(self._layout_warnings({"entry": {"mode": mode}}), [], mode)
		for anchor in ui_config.OVERLAY_POSITIONS:
			self.assertEqual(
				self._layout_warnings({"entry": {"mode": "popup", "popupPosition": anchor}}),
				[],
				anchor,
			)
			# dialogPosition: every anchor is vocabulary-ACCEPTED (no off-form
			# warning) but the knob is RESERVED — exactly one item-17 notice.
			warnings = self._layout_warnings({"actions": {"dialogPosition": anchor}})
			self.assertEqual(len(warnings), 1, f"{anchor}: {warnings}")
			self.assertIn("actions.dialogPosition is RESERVED", warnings[0])
		for variant in ui_config.DC_ENTRY_VARIANTS:
			self.assertEqual(self._layout_warnings({"dcEntry": {"variant": variant}}), [], variant)
		for picker in ui_config.DC_ENTRY_SUPPLIER_PICKERS:
			self.assertEqual(
				self._layout_warnings({"dcEntry": {"supplierPicker": picker}}), [], picker
			)
		for placement in ui_config.ACTIONS_PLACEMENTS:
			self.assertEqual(
				self._layout_warnings({"actions": {"placement": placement}}), [], placement
			)

	def test_absent_structural_keys_stay_silent(self):
		# PARITY LAW: a layout without any knob validates exactly as today.
		self.assertEqual(ui_config.validate_config(dict(LAYOUT_CONFIG), layer="layout"), [])

	def test_partial_knobs_are_legal(self):
		# Every sub-key is optional — a knob carrying only one of them is clean.
		for extra in (
			{"detail": {}},
			{"entry": {"mode": "popup"}},
			{"dcEntry": {"variant": "inline-grid"}},
			{"actions": {"items": ["cancel_doc"]}},
		):
			self.assertEqual(self._layout_warnings(extra), [], extra)

	# ── soft-warn on unknown enum values (client falls back / ignores) ─────

	def test_unknown_enum_values_warn_softly_and_never_block(self):
		cases = {
			"detail.position 'popup' is not one of": {"detail": {"position": "popup"}},
			"entry.mode 'drawer' is not one of": {"entry": {"mode": "drawer"}},
			"entry.popupPosition 'middle' is not one of": {
				"entry": {"mode": "popup", "popupPosition": "middle"}
			},
			"dcEntry.variant 'wizard' is not one of": {"dcEntry": {"variant": "wizard"}},
			"dcEntry.qtyControl 'stepper' is not one of": {"dcEntry": {"qtyControl": "stepper"}},
			"dcEntry.supplierPicker 'dropdown' is not one of": {
				"dcEntry": {"supplierPicker": "dropdown"}
			},
			"actions.placement 'sidebar' is not one of": {"actions": {"placement": "sidebar"}},
		}
		for fragment, extra in cases.items():
			warnings = self._layout_warnings(extra)
			self.assertEqual(len(warnings), 1, f"{extra}: {warnings}")
			self.assertIn(fragment, warnings[0])
		# dialogPosition is RESERVED: an off-form value draws BOTH the item-17
		# notice and the vocabulary warning.
		warnings = self._layout_warnings({"actions": {"dialogPosition": "top-center"}})
		self.assertEqual(len(warnings), 2, warnings)
		self.assertTrue(any("actions.dialogPosition 'top-center' is not one of" in w for w in warnings))
		self.assertTrue(any("actions.dialogPosition is RESERVED" in w for w in warnings))

	def test_unknown_action_item_names_warn_softly_and_never_block(self):
		warnings = self._layout_warnings(
			{"actions": {"items": ["create_grn", "delete_everything", "cancel_doc"]}}
		)
		self.assertEqual(len(warnings), 1)
		self.assertIn("delete_everything", warnings[0])
		self.assertIn("the client ignores it", warnings[0])

	def test_non_string_action_item_entry_warns_softly(self):
		warnings = self._layout_warnings({"actions": {"items": ["create_grn", 7]}})
		self.assertEqual(len(warnings), 1)
		self.assertIn("is not a string", warnings[0])

	def test_popup_position_without_popup_mode_warns_softly(self):
		for entry in (
			{"popupPosition": "center"},  # mode absent → client defaults to page
			{"mode": "page", "popupPosition": "center"},
		):
			warnings = self._layout_warnings({"entry": entry})
			self.assertEqual(len(warnings), 1, entry)
			self.assertIn("no effect unless entry.mode is 'popup'", warnings[0])

	def test_unknown_keys_inside_each_knob_warn(self):
		for knob, extra in (
			("detail", {"detail": {"position": "page", "sparkles": 1}}),
			("entry", {"entry": {"mode": "page", "sparkles": 1}}),
			("dcEntry", {"dcEntry": {"variant": "form-grid", "sparkles": 1}}),
			("actions", {"actions": {"placement": "header", "sparkles": 1}}),
		):
			warnings = self._layout_warnings(extra)
			self.assertEqual(len(warnings), 1, knob)
			self.assertIn(f"unknown key 'sparkles' inside {knob}", warnings[0])

	# ── hard errors ONLY for structurally-wrong types ──────────────────────

	def test_non_object_knobs_hard_error(self):
		for extra in (
			{"detail": "right"},
			{"detail": ["right"]},
			{"entry": "popup"},
			{"entry": [1]},
			{"dcEntry": 1},
			{"dcEntry": ["size-matrix"]},
			{"actions": "header"},
			{"actions": ["create_grn"]},
		):
			with self.assertRaises(frappe.ValidationError, msg=str(extra)):
				self._layout_warnings(extra)

	def test_actions_items_must_be_a_list_hard_error(self):
		for bad in ("create_grn", {"create_grn": True}, 7):
			with self.assertRaises(frappe.ValidationError, msg=str(bad)):
				self._layout_warnings({"actions": {"items": bad}})

	# ── layout-tier only: the overrides layer filters all four ─────────────

	def test_overrides_layer_still_rejects_the_structural_keys(self):
		# NOT overridable this iteration: one unknown-key warning each (no
		# duplicate shape warnings), and the save-side whitelist drops them.
		warnings = ui_config.validate_config(
			{"schema_version": 1, **self.VALID_STRUCTURAL}, layer="overrides"
		)
		self.assertEqual(len(warnings), 4)
		for key in ui_config.STRUCTURAL_KEYS:
			self.assertTrue(any(f"unknown key '{key}'" in w for w in warnings), key)
			self.assertNotIn(key, ui_config.OVERRIDABLE_KEYS)
			self.assertIn(key, ui_config.LAYOUT_KEYS)


class TestUIPreferenceUserLifecycle(IntegrationTestCase):
	"""§3.3 User doc-event handlers (wired in yrp/hooks.py ``doc_events["User"]``):
	delete / rename / merge keep the ``autoname: field:user`` preference storage
	truthful. Throwaway users are created per test — the runner rolls them back."""

	def setUp(self):
		frappe.set_user("Administrator")

	@staticmethod
	def _make_user(email):
		if not frappe.db.exists("User", email):
			frappe.get_doc(
				{
					"doctype": "User",
					"email": email,
					"first_name": "YRP UI Lifecycle Test",
					"send_welcome_email": 0,
					"enabled": 1,
				}
			).insert(ignore_permissions=True)
		return email

	@staticmethod
	def _make_preference(user, accent):
		"""Distinct accent per record so the merge test can prove WHOSE record survived."""
		frappe.get_doc(
			{
				"doctype": "YRP UI Preference",
				"user": user,
				"overrides": json.dumps({"schema_version": 1, "theme": {"accent": accent}}),
			}
		).insert(ignore_permissions=True)

	@staticmethod
	def _accent_of(pref_name):
		overrides = frappe.db.get_value("YRP UI Preference", pref_name, "overrides")
		return json.loads(overrides)["theme"]["accent"]

	def test_deleting_user_with_preference_is_not_blocked_and_removes_it(self):
		user = self._make_user("yrp-ui-lifecycle-delete@essdee.local")
		self._make_preference(user, "#111111")
		# Without the on_trash hook, Frappe's check_if_doc_is_linked raises
		# LinkExistsError here — offboarding blocked by a cosmetic record.
		frappe.delete_doc("User", user, ignore_permissions=True)
		self.assertFalse(frappe.db.exists("User", user))
		self.assertFalse(frappe.db.exists("YRP UI Preference", user))

	def test_renaming_user_makes_preference_docname_follow(self):
		old = self._make_user("yrp-ui-lifecycle-rename-old@essdee.local")
		new = "yrp-ui-lifecycle-rename-new@essdee.local"
		self._make_preference(old, "#222222")
		frappe.rename_doc("User", old, new)
		self.assertFalse(frappe.db.exists("YRP UI Preference", old))
		pref = frappe.db.get_value("YRP UI Preference", new, ["name", "user"], as_dict=True)
		self.assertIsNotNone(pref)
		self.assertEqual(pref.user, new)  # docname AND user Link both follow
		self.assertEqual(self._accent_of(new), "#222222")

	def test_merging_users_who_both_own_preferences_keeps_exactly_the_survivor(self):
		merged_away = self._make_user("yrp-ui-lifecycle-merge-a@essdee.local")
		survivor = self._make_user("yrp-ui-lifecycle-merge-b@essdee.local")
		self._make_preference(merged_away, "#333333")
		self._make_preference(survivor, "#444444")
		# Without the before_rename hook this dies mid-transaction with an
		# IntegrityError on the UNIQUE ``user`` column when rename_doc
		# bulk-updates Link values before after_rename can dedup.
		frappe.rename_doc("User", merged_away, survivor, merge=True)
		self.assertFalse(frappe.db.exists("YRP UI Preference", merged_away))
		self.assertEqual(
			frappe.db.count("YRP UI Preference", {"user": survivor}), 1
		)  # exactly one record left
		# …and it is the SURVIVOR's own preference, not the merged-away user's.
		self.assertEqual(self._accent_of(survivor), "#444444")


class TestUIConfigThemeValidation(IntegrationTestCase):
	"""Save-time theme validation (§3.1) over the FULL engine token set
	(applyTheme.js tokenVars): mode/accent stay HARD, every other token is
	SOFT — warn-and-ignore, mirroring the client, never blocking a save."""

	@staticmethod
	def _warnings(theme):
		return ui_config.validate_config({"schema_version": 1, "theme": theme}, layer="overrides")

	def test_full_engine_token_theme_passes_with_zero_warnings(self):
		# The Demo 7 shape: every token the engine renders, all well-formed.
		warnings = self._warnings(
			{
				"mode": "user",
				"accent": "#E23744",
				"bg": "#fff6ec",
				"surface": "#ffffff",
				"text": "#26180d",
				"muted": "rgba(38, 24, 13, 0.62)",
				"line": "rgba(38, 24, 13, 0.12)",
				"surface2": "#f7ede1",
				"radius": 14,
				"fontScale": 1,
				"font": "Inter, 'Segoe UI', sans-serif",
				"dark": {
					"mode": "dark",
					"accent": "#ff6b5e",
					"bg": "#1b120b",
					"surface": "#271b10",
					"text": "#f6ecdf",
				},
			}
		)
		self.assertEqual(warnings, [])

	def test_unknown_key_inside_theme_still_warns(self):
		warnings = self._warnings({"sparkles": True})
		self.assertEqual(len(warnings), 1)
		self.assertIn("unknown key 'sparkles' inside theme", warnings[0])

	def test_mode_and_accent_hard_rules_unchanged(self):
		with self.assertRaises(frappe.ValidationError):
			self._warnings({"mode": "midnight"})
		with self.assertRaises(frappe.ValidationError):
			self._warnings({"accent": "tomato"})

	def test_off_form_token_values_warn_softly_and_never_block(self):
		warnings = self._warnings(
			{
				"bg": "url(javascript:alert(1))",  # CSS-injection shape → dropped client-side
				"radius": 999,
				"density": "cozy",
				"fontScale": "huge",
				"font": "Inter; } body { display: none",
			}
		)
		# One per bad token, nothing raised — density draws its inert notice
		# PLUS the vocabulary warning (house style: realtime.intervalMs et al).
		self.assertEqual(len(warnings), 6, warnings)
		for fragment in ("theme.bg", "theme.radius", "theme.density", "theme.fontScale", "theme.font"):
			self.assertTrue(
				any(fragment in w for w in warnings), f"missing soft warning for {fragment}"
			)

	def test_density_present_draws_the_inert_notice(self):
		# 2026-07-17 drill: density is accepted but visually inert (Track 1
		# item 10) — CATALOG says "don't author it", so lint must say so too.
		warnings = self._warnings({"density": "compact"})
		self.assertEqual(len(warnings), 1, warnings)
		self.assertIn("theme.density is accepted but visually INERT", warnings[0])
		# Same notice for the dark-overlay spelling.
		warnings = self._warnings({"dark": {"density": "compact"}})
		self.assertEqual(len(warnings), 1, warnings)
		self.assertIn("theme.dark.density is accepted but visually INERT", warnings[0])

	def test_numeric_strings_pass_like_the_engines_number_coercion(self):
		# Number("14") is finite in the client — the server must not warn on it.
		self.assertEqual(self._warnings({"radius": "14", "fontScale": "1.1"}), [])

	def test_booleans_are_not_numbers_for_radius_or_font_scale(self):
		warnings = self._warnings({"radius": True, "fontScale": False})
		self.assertEqual(len(warnings), 2)

	def test_dark_must_be_an_object_soft_warning(self):
		warnings = self._warnings({"dark": "#1b120b"})
		self.assertEqual(len(warnings), 1)
		self.assertIn("theme.dark must be an object", warnings[0])

	def test_quick_create_unknown_doctype_warns_softly(self):
		# M13: same soft rule as nav items — the client catalog drops a typo'd
		# entry silently, so the save must surface it.
		warnings = ui_config.validate_config(
			{"schema_version": 1, "quickCreate": ["Lot", "No Such DocType"]}, layer="overrides"
		)
		self.assertEqual(len(warnings), 1)
		self.assertIn("No Such DocType", warnings[0])

	def test_dark_overlay_tokens_get_the_same_soft_checks(self):
		warnings = self._warnings(
			{"dark": {"accent": "not-a-hex", "bg": "black", "dark": {}, "sparkles": 1}}
		)
		self.assertEqual(len(warnings), 4)
		self.assertTrue(any("theme.dark.accent" in w for w in warnings))  # soft, unlike top-level
		self.assertTrue(any("theme.dark.bg" in w for w in warnings))
		self.assertTrue(any("unknown key 'dark' inside theme.dark" in w for w in warnings))
		self.assertTrue(any("unknown key 'sparkles' inside theme.dark" in w for w in warnings))

	def test_layout_layer_gets_the_same_token_vocabulary(self):
		# dark supplies a palette so the light-only-palette warning stays silent
		# (that rule has its own tests below).
		layout_cfg = dict(
			LAYOUT_CONFIG,
			theme={"mode": "user", "bg": "#fff6ec", "radius": 14, "dark": {"bg": "#1b120b"}},
		)
		self.assertEqual(ui_config.validate_config(layout_cfg, layer="layout"), [])

	# ── light-only palette vs dark mode (IMPORTANT-2, 2026-07-15 review) ──
	# The client refuses to carry light color tokens into .dark without a
	# dark{} overlay, so dark mode keeps the shipped dark palette — the save
	# must SAY so, or the author ships an unstyled dark mode unknowingly.

	def test_light_colors_without_dark_overlay_warn_when_dark_is_reachable(self):
		for mode in ("user", "dark", None):
			theme = {"bg": "#ffffff", "surface": "#ffffff", "text": "#26180d"}
			if mode is not None:
				theme["mode"] = mode
			warnings = self._warnings(theme)
			self.assertEqual(len(warnings), 1, f"mode={mode!r}: {warnings}")
			self.assertIn("shipped dark palette", warnings[0])
			self.assertIn("bg, surface, text", warnings[0])

	def test_light_colors_with_forced_light_mode_do_not_warn(self):
		self.assertEqual(
			self._warnings({"mode": "light", "bg": "#ffffff", "surface": "#ffffff"}), []
		)

	def test_light_colors_with_dark_palette_do_not_warn(self):
		self.assertEqual(
			self._warnings(
				{"mode": "user", "surface": "#ffffff", "dark": {"surface": "#271b10"}}
			),
			[],
		)

	def test_non_color_tokens_alone_never_trigger_the_dark_palette_warning(self):
		# radius/fontScale/font carry into .dark safely — no palette, no warning.
		# (density is deliberately absent: it draws the inert notice now.)
		self.assertEqual(self._warnings({"mode": "user", "radius": 14, "fontScale": 1.1}), [])

	def test_unicode_font_warns_like_the_clients_ascii_regex(self):
		# JS \w is ASCII-only: the client drops "Ariál" at render. Python \w is
		# Unicode — the old server regex silently passed it. M9: server warns now.
		warnings = self._warnings({"font": "Ariál, sans-serif"})
		self.assertEqual(len(warnings), 1)
		self.assertIn("theme.font", warnings[0])


class TestUIConfigBlockProps(IntegrationTestCase):
	"""Per-type home-block prop schemas (``_check_block_props``) — all SOFT
	warnings, mirroring the home-queues.maxCards house style. 2026-07-16
	review finding 4 added schemas for the three shipped block types
	summary-tiles / record-list / calculator-panel; unknown block types keep
	skipping prop validation entirely (the client bundle may be newer)."""

	@staticmethod
	def _block_warnings(block):
		cfg = dict(LAYOUT_CONFIG, screens={"home": {"blocks": [block], "hidden": {}}})
		return ui_config.validate_config(cfg, layer="layout")

	# ── parity: pre-existing types + unknown types behave as before ──────

	def test_existing_and_unknown_types_stay_warning_free(self):
		for block in (
			{"id": "q", "type": "home-queues", "props": {"stats": ["open_lots", "draft_dcs"]}},
			{"id": "q2", "type": "home-queues"},  # no props at all
			{"id": "g", "type": "home-greeting", "props": {"greetingName": "Anna"}},
			{"id": "x", "type": "some-future-block", "props": {"anything": ["goes"]}},
		):
			self.assertEqual(self._block_warnings(block), [], block["id"])

	def test_home_queues_max_cards_is_reserved_and_bounds_checked(self):
		# maxCards has been validated since day one but is consumed by NOTHING
		# (HomeQueues reads only `stats`) — item 17 makes it a RESERVED knob:
		# presence always draws the notice, the bounds check stays on top.
		warnings = self._block_warnings(
			{"id": "q", "type": "home-queues", "props": {"maxCards": 4}}
		)
		self.assertEqual(len(warnings), 1, warnings)
		self.assertIn("maxCards is RESERVED", warnings[0])
		warnings = self._block_warnings(
			{"id": "q", "type": "home-queues", "props": {"maxCards": 99}}
		)
		self.assertEqual(len(warnings), 2, warnings)
		self.assertTrue(any("maxCards is RESERVED" in w for w in warnings))
		self.assertTrue(any("maxCards must be an integer between 1 and 10" in w for w in warnings))

	def test_non_object_props_warn_once_and_skip_per_type_checks(self):
		warnings = self._block_warnings(
			{"id": "r", "type": "record-list", "props": "doctype=Lot"}
		)
		self.assertEqual(len(warnings), 1)
		self.assertIn("props must be an object", warnings[0])

	# ── summary-tiles ─────────────────────────────────────────────────────

	def test_summary_tiles_with_registered_metric_names_is_warning_free(self):
		block = {
			"id": "s",
			"type": "summary-tiles",
			"props": {"metrics": ["open_wos", "open_lots"]},
		}
		self.assertEqual(self._block_warnings(block), [])
		# metrics is optional — a bare summary-tiles block stays clean.
		self.assertEqual(self._block_warnings({"id": "s", "type": "summary-tiles"}), [])

	def test_summary_tiles_metrics_must_be_a_list_of_strings(self):
		for bad in ("open_wos", {"a": 1}, ["open_wos", 7]):
			warnings = self._block_warnings(
				{"id": "s", "type": "summary-tiles", "props": {"metrics": bad}}
			)
			self.assertEqual(len(warnings), 1, bad)
			self.assertIn("metrics must be a list of strings", warnings[0])

	def test_summary_tiles_unregistered_metric_name_warns_softly(self):
		warnings = self._block_warnings(
			{
				"id": "s",
				"type": "summary-tiles",
				"props": {"metrics": ["open_wos", "no_such_metric"]},
			}
		)
		self.assertEqual(len(warnings), 1)
		self.assertIn("no_such_metric", warnings[0])

	def test_summary_tiles_registry_check_never_hard_fails_validation(self):
		# _known_metric_keys returns None when the ui_metrics import breaks —
		# the registry check is skipped, the save is never blocked.
		with patch.object(ui_config, "_known_metric_keys", return_value=None):
			warnings = self._block_warnings(
				{"id": "s", "type": "summary-tiles", "props": {"metrics": ["anything"]}}
			)
		self.assertEqual(warnings, [])

	def test_known_metric_keys_helper_reflects_the_registry(self):
		from yrp.yrp.api.ui_metrics import METRICS

		self.assertEqual(ui_config._known_metric_keys(), set(METRICS))

	# ── record-list ───────────────────────────────────────────────────────

	def test_record_list_full_valid_props_is_warning_free(self):
		# NOTE deliberately RENDERABLE meta fields only: "name" is NOT legal —
		# neither client resolves it as a column/titleField (RecordList byName
		# misses it, the routed page always shows Name first anyway).
		block = {
			"id": "r",
			"type": "record-list",
			"props": {
				"doctype": "Work Order",
				"variant": "kanban",
				"columns": ["item", "status"],
				"pageSize": 25,
				"groupBy": "status",
				"titleField": "item",
				"title": "Orders",
			},
		}
		self.assertEqual(self._block_warnings(block), [])

	def test_record_list_requires_a_doctype_even_without_props(self):
		for block in (
			{"id": "r", "type": "record-list"},  # no props at all
			{"id": "r", "type": "record-list", "props": {}},
			{"id": "r", "type": "record-list", "props": {"doctype": "  "}},
			{"id": "r", "type": "record-list", "props": {"doctype": 7}},
		):
			warnings = self._block_warnings(block)
			self.assertEqual(len(warnings), 1, block)
			self.assertIn("doctype", warnings[0])

	def test_record_list_variant_vocabulary(self):
		for good in ("table", "cards", "kanban"):
			block = {
				"id": "r",
				"type": "record-list",
				"props": {"doctype": "Lot", "variant": good},
			}
			self.assertEqual(self._block_warnings(block), [], good)
		warnings = self._block_warnings(
			{"id": "r", "type": "record-list", "props": {"doctype": "Lot", "variant": "list"}}
		)
		self.assertEqual(len(warnings), 1)
		self.assertIn("variant", warnings[0])

	def test_record_list_columns_shape_families_warn(self):
		# Non-list → one shape warning; list with a non-string/non-object entry
		# → one per-entry warning. Both messages now name the {field,label}
		# object form the client ALSO accepts (item 17 mismatch fix).
		warnings = self._block_warnings(
			{"id": "r", "type": "record-list", "props": {"doctype": "Lot", "columns": "name,status"}}
		)
		self.assertEqual(len(warnings), 1, warnings)
		self.assertIn("columns must be a list of fieldname strings or {field, label} objects", warnings[0])
		warnings = self._block_warnings(
			{"id": "r", "type": "record-list", "props": {"doctype": "Lot", "columns": ["status", 7]}}
		)
		self.assertEqual(len(warnings), 1, warnings)
		self.assertIn("neither a fieldname string nor a {field, label} object", warnings[0])

	def test_record_list_accepts_field_label_column_objects(self):
		# The client/server mismatch item 17 closes: RecordList.vue accepts
		# {field,label} objects; the server used to warn on them.
		block = {
			"id": "r",
			"type": "record-list",
			"props": {
				"doctype": "Work Order",
				"columns": ["status", {"field": "item", "label": "Item"}],
			},
		}
		self.assertEqual(self._block_warnings(block), [])

	def test_record_list_column_fieldnames_checked_against_meta(self):
		warnings = self._block_warnings(
			{
				"id": "r",
				"type": "record-list",
				"props": {
					"doctype": "Work Order",
					"columns": ["status", "no_such_field", {"field": "also_missing"}],
				},
			}
		)
		self.assertEqual(len(warnings), 2, warnings)
		for fragment in ("no_such_field", "also_missing"):
			self.assertTrue(
				any(fragment in w and "is not a field on 'Work Order'" in w for w in warnings),
				fragment,
			)

	def test_record_list_unrenderable_columns_warn(self):
		# 2026-07-17 review: frappe default fields (modified/owner/name) and
		# hidden/non-listable meta fields lint-passed but render nothing —
		# RecordList.vue resolves columns strictly against visible meta fields.
		for field in ("modified", "owner", "name"):
			warnings = self._block_warnings(
				{"id": "r", "type": "record-list", "props": {"doctype": "Work Order", "columns": [field]}}
			)
			self.assertEqual(len(warnings), 1, f"{field}: {warnings}")
			self.assertIn(f"column '{field}' is not a field on 'Work Order'", warnings[0])
		# groupBy on a default field silently regroups by status client-side.
		warnings = self._block_warnings(
			{"id": "r", "type": "record-list", "props": {"doctype": "Work Order", "groupBy": "owner"}}
		)
		self.assertEqual(len(warnings), 1, warnings)
		self.assertIn("groupBy 'owner' is not a field on 'Work Order'", warnings[0])

	def test_record_list_group_by_and_title_field_checked_against_meta(self):
		for key in ("groupBy", "titleField"):
			warnings = self._block_warnings(
				{
					"id": "r",
					"type": "record-list",
					"props": {"doctype": "Work Order", key: "no_such_field"},
				}
			)
			self.assertEqual(len(warnings), 1, f"{key}: {warnings}")
			self.assertIn("is not a field on 'Work Order'", warnings[0])
		# `title` is a free heading — never meta-checked.
		self.assertEqual(
			self._block_warnings(
				{
					"id": "r",
					"type": "record-list",
					"props": {"doctype": "Work Order", "title": "Anything Goes"},
				}
			),
			[],
		)

	def test_record_list_unknown_doctype_warns_and_skips_field_checks(self):
		warnings = self._block_warnings(
			{
				"id": "r",
				"type": "record-list",
				"props": {"doctype": "No Such DocType", "columns": ["whatever"]},
			}
		)
		self.assertEqual(len(warnings), 1, warnings)
		self.assertIn("doctype 'No Such DocType' does not exist as a DocType", warnings[0])

	def test_record_list_page_size_bounds(self):
		for bad in (0, 51, True, "10"):
			warnings = self._block_warnings(
				{"id": "r", "type": "record-list", "props": {"doctype": "Lot", "pageSize": bad}}
			)
			self.assertEqual(len(warnings), 1, bad)
			self.assertIn("pageSize", warnings[0])
		for good in (1, 50):
			block = {
				"id": "r",
				"type": "record-list",
				"props": {"doctype": "Lot", "pageSize": good},
			}
			self.assertEqual(self._block_warnings(block), [], good)

	def test_record_list_string_props(self):
		for key in ("groupBy", "titleField", "title"):
			warnings = self._block_warnings(
				{"id": "r", "type": "record-list", "props": {"doctype": "Lot", key: 1}}
			)
			self.assertEqual(len(warnings), 1, key)
			self.assertIn(f"{key} must be a string", warnings[0])

	# ── calculator-panel ──────────────────────────────────────────────────

	def test_calculator_panel_valid_props_is_warning_free(self):
		block = {
			"id": "c",
			"type": "calculator-panel",
			"props": {"calculation": "lot_balance", "params": {"lot": "X"}},
		}
		self.assertEqual(self._block_warnings(block), [])

	def test_calculator_panel_requires_a_calculation(self):
		for block in (
			{"id": "c", "type": "calculator-panel"},  # no props at all
			{"id": "c", "type": "calculator-panel", "props": {}},
			{"id": "c", "type": "calculator-panel", "props": {"calculation": ""}},
			{"id": "c", "type": "calculator-panel", "props": {"calculation": 7}},
		):
			warnings = self._block_warnings(block)
			self.assertEqual(len(warnings), 1, block)
			self.assertIn("calculation", warnings[0])

	def test_calculator_panel_params_must_be_an_object(self):
		warnings = self._block_warnings(
			{
				"id": "c",
				"type": "calculator-panel",
				"props": {"calculation": "lot_balance", "params": [1]},
			}
		)
		self.assertEqual(len(warnings), 1)
		self.assertIn("params must be an object", warnings[0])


class TestUIConfigItem17ReservedKnobs(IntegrationTestCase):
	"""USE_CASE §4 item 17: every knob that is stored/validated but consumed by
	NO client gets an explicit RESERVED notice — an authoring agent must never
	emit a knob that silently does nothing. The notice is SOFT: it never blocks
	a save, and Track 1 item 11 later wires-or-deletes each of these names."""

	@staticmethod
	def _layout_warnings(extra):
		return ui_config.validate_config(dict(LAYOUT_CONFIG, **extra), layer="layout")

	def test_every_reserved_knob_draws_exactly_one_notice(self):
		cases = {
			"realtime.intervalMs": {"realtime": {"enabled": True, "intervalMs": 10000}},
			"realtime.toast": {"realtime": {"enabled": True, "toast": True}},
			"actions.dialogPosition": {"actions": {"dialogPosition": "bottom"}},
			"detail.rich": {"detail": {"position": "page", "rich": True}},
			"nav.home": {
				"nav": dict(
					LAYOUT_CONFIG["nav"],
					home={"label": "Home", "icon": "pi pi-th-large", "view": "home"},
				)
			},
		}
		for path, extra in cases.items():
			warnings = self._layout_warnings(extra)
			self.assertEqual(len(warnings), 1, f"{path}: {warnings}")
			self.assertIn(f"{path} is RESERVED", warnings[0])
			self.assertIn("does nothing today", warnings[0])

	def test_reserved_notices_never_block_the_save(self):
		# All five reserved names together: five notices, zero exceptions.
		cfg = dict(
			LAYOUT_CONFIG,
			realtime={"enabled": True, "intervalMs": 10000, "toast": True},
			actions={"placement": "header", "dialogPosition": "bottom"},
			detail={"position": "page", "rich": True},
			nav=dict(LAYOUT_CONFIG["nav"], home={"view": "home"}),
		)
		warnings = ui_config.validate_config(cfg, layer="layout")
		self.assertEqual(len([w for w in warnings if "RESERVED" in w]), 5, warnings)
		self.assertEqual(len(warnings), 5, warnings)


class TestUIConfigItem17HomeQueuesStats(IntegrationTestCase):
	"""The 2026-07-17 owner bite: home-queues renders ONLY the four queue-backed
	metrics (HomeQueues.vue METRIC_TO_QUEUE). A registered KPI key placed there
	rendered NOTHING with zero warnings; a typo likewise. Both warn now."""

	@staticmethod
	def _block_warnings(block):
		cfg = dict(LAYOUT_CONFIG, screens={"home": {"blocks": [block], "hidden": {}}})
		return ui_config.validate_config(cfg, layer="layout")

	def test_all_four_queue_metrics_stay_warning_free(self):
		block = {
			"id": "q",
			"type": "home-queues",
			"props": {"stats": list(ui_config.HOME_QUEUE_METRICS)},
		}
		self.assertEqual(self._block_warnings(block), [])

	def test_registered_kpi_metric_in_home_queues_warns_with_guidance(self):
		# The literal Cutting Supervisor defect: delayed/completion are real
		# registry metrics but NOT queues — the cards rendered nothing.
		warnings = self._block_warnings(
			{
				"id": "q",
				"type": "home-queues",
				"props": {"stats": ["open_lots", "delayed", "completion"]},
			}
		)
		self.assertEqual(len(warnings), 2, warnings)
		for name in ("delayed", "completion"):
			match = [w for w in warnings if f"'{name}'" in w]
			self.assertEqual(len(match), 1, name)
			self.assertIn("is not a home-queue metric", match[0])
			self.assertIn("renders NOTHING", match[0])
			self.assertIn("summary-tiles", match[0])

	def test_unregistered_stat_name_warns_as_typo(self):
		warnings = self._block_warnings(
			{"id": "q", "type": "home-queues", "props": {"stats": ["delayed_wos"]}}
		)
		self.assertEqual(len(warnings), 1, warnings)
		self.assertIn("'delayed_wos' is not a registered metric", warnings[0])

	def test_stats_must_be_a_list_of_strings(self):
		for bad in ("open_lots", {"a": 1}, ["open_lots", 7]):
			warnings = self._block_warnings(
				{"id": "q", "type": "home-queues", "props": {"stats": bad}}
			)
			self.assertEqual(len(warnings), 1, bad)
			self.assertIn("stats must be a list of metric names", warnings[0])

	def test_registry_outage_still_flags_non_queue_names(self):
		# METRICS import broken → the typo/KPI distinction collapses but the
		# non-queue name STILL warns (never silent, never a hard failure).
		with patch.object(ui_config, "_known_metric_keys", return_value=None):
			warnings = self._block_warnings(
				{"id": "q", "type": "home-queues", "props": {"stats": ["delayed_wos"]}}
			)
		self.assertEqual(len(warnings), 1, warnings)
		self.assertIn("is not a home-queue metric", warnings[0])


class TestUIConfigItem17CalculatorRegistry(IntegrationTestCase):
	"""calculator-panel `calculation` validated against the CALCULATIONS
	registry (item 17) — same lazy fail-safe contract as the metrics check."""

	@staticmethod
	def _block_warnings(block):
		cfg = dict(LAYOUT_CONFIG, screens={"home": {"blocks": [block], "hidden": {}}})
		return ui_config.validate_config(cfg, layer="layout")

	def test_registered_calculation_stays_warning_free(self):
		block = {"id": "c", "type": "calculator-panel", "props": {"calculation": "lot_balance"}}
		self.assertEqual(self._block_warnings(block), [])

	def test_unregistered_calculation_warns_softly(self):
		warnings = self._block_warnings(
			{"id": "c", "type": "calculator-panel", "props": {"calculation": "rate_estimate"}}
		)
		self.assertEqual(len(warnings), 1, warnings)
		self.assertIn("'rate_estimate' is not a registered calculation", warnings[0])

	def test_registry_check_never_hard_fails_validation(self):
		with patch.object(ui_config, "_known_calculation_keys", return_value=None):
			warnings = self._block_warnings(
				{"id": "c", "type": "calculator-panel", "props": {"calculation": "anything"}}
			)
		self.assertEqual(warnings, [])

	def test_known_calculation_keys_helper_reflects_the_registry(self):
		from yrp.yrp.api.ui_metrics import CALCULATIONS

		self.assertEqual(ui_config._known_calculation_keys(), set(CALCULATIONS))


class TestUIConfigItem17ListViews(IntegrationTestCase):
	"""Deep listViews validation (item 17): doctype keys vs site + catalog,
	object shape, variant vocabulary, columns/groupBy/titleField vs DocType
	meta — every family the client silently drops now warns at save."""

	@staticmethod
	def _warnings(list_views, layer="layout"):
		if layer == "layout":
			return ui_config.validate_config(
				dict(LAYOUT_CONFIG, listViews=list_views), layer="layout"
			)
		return ui_config.validate_config(
			{"schema_version": 1, "listViews": list_views}, layer="overrides"
		)

	def test_fully_valid_deep_list_view_is_warning_free(self):
		# {field, label} objects ONLY — the routed list page drops bare strings
		# (string entries have their own warning test below).
		self.assertEqual(
			self._warnings(
				{
					"Work Order": {
						"variant": "kanban",
						"columns": [{"field": "item"}, {"field": "supplier", "label": "Job-worker"}],
						"groupBy": "process_name",
						"titleField": "item",
					}
				}
			),
			[],
		)

	def test_string_column_entries_warn_as_dropped(self):
		# 2026-07-17 review (CRITICAL): DynamicListPage.layoutColumns iterates
		# `if (!lc || !lc.field) continue` — a plain string has no .field, so
		# EVERY string entry is skipped and an all-string list silently falls
		# back to the meta defaults. The validator used to certify strings here.
		warnings = self._warnings({"Work Order": {"columns": ["lot", "item", "status"]}})
		self.assertEqual(len(warnings), 3, warnings)
		for w in warnings:
			self.assertIn("DROPS string entries", w)
		# The {field, label} spelling of the same columns is clean.
		self.assertEqual(
			self._warnings(
				{"Work Order": {"columns": [{"field": "lot"}, {"field": "item"}, {"field": "status"}]}}
			),
			[],
		)
		# A string entry that is ALSO a fieldname typo draws both warnings.
		warnings = self._warnings({"Work Order": {"columns": ["no_such_field"]}})
		self.assertEqual(len(warnings), 2, warnings)

	def test_default_and_unrenderable_fields_warn_as_columns(self):
		# 2026-07-17 review flip: name/modified/owner are real row keys but NOT
		# renderable columns — both clients build their column maps strictly
		# from visible meta fields, so every one of these renders nothing.
		warnings = self._warnings(
			{
				"Work Order": {
					"columns": [{"field": "name"}, {"field": "modified"}, {"field": "owner"}]
				}
			}
		)
		self.assertEqual(len(warnings), 3, warnings)
		for fragment in ("'name'", "'modified'", "'owner'"):
			self.assertTrue(
				any(fragment in w and "is not a field on 'Work Order'" in w for w in warnings),
				fragment,
			)

	def test_hidden_and_non_listable_meta_fields_warn_as_columns(self):
		# Item Production Detail: `version` is a hidden meta field, and
		# `item_details_tab` is a Tab Break (NON_LISTABLE_FIELDTYPES) — the
		# routed list drops both, so the save must warn (2026-07-17 review).
		warnings = self._warnings(
			{
				"Item Production Detail": {
					"columns": [{"field": "version"}, {"field": "item_details_tab"}]
				}
			}
		)
		self.assertEqual(len(warnings), 2, warnings)
		for fragment in ("'version'", "'item_details_tab'"):
			self.assertTrue(
				any(fragment in w and "is not a field on 'Item Production Detail'" in w for w in warnings),
				fragment,
			)
		# groupBy on a default field falls back to status client-side — warns.
		warnings = self._warnings({"Work Order": {"groupBy": "owner"}})
		self.assertEqual(len(warnings), 1, warnings)
		self.assertIn("groupBy 'owner' is not a field on 'Work Order'", warnings[0])

	def test_unknown_doctype_key_warns_and_skips_field_checks(self):
		warnings = self._warnings({"No Such DocType": {"columns": [{"field": "whatever"}]}})
		self.assertEqual(len(warnings), 1, warnings)
		self.assertIn("'No Such DocType' does not exist as a DocType", warnings[0])

	def test_off_catalog_doctype_key_warns(self):
		# Catalog keeps the base config's nav doctypes so ONLY the listViews
		# key under test ("Item" — real, off-catalog) warns.
		with patch.object(ui_config, "_web_doctype_catalog", return_value={"Lot", "Work Order"}):
			warnings = self._warnings({"Item": {"variant": "cards"}})
		self.assertEqual(len(warnings), 1, warnings)
		self.assertIn("'Item' is not in the /web doctype catalog", warnings[0])

	def test_no_catalog_hook_skips_the_catalog_check_only(self):
		with patch.object(ui_config, "_web_doctype_catalog", return_value=None):
			self.assertEqual(self._warnings({"Work Order": {"variant": "cards"}}), [])
			warnings = self._warnings({"No Such DocType": {}})
		self.assertEqual(len(warnings), 1, warnings)  # existence check still runs

	def test_non_object_value_warns_and_null_stays_silent(self):
		warnings = self._warnings({"Lot": "cards"})
		self.assertEqual(len(warnings), 1, warnings)
		self.assertIn("listViews['Lot'] must be an object", warnings[0])
		self.assertEqual(self._warnings({"Lot": None}), [])  # null = no opinion

	def test_unknown_key_inside_a_list_view_warns(self):
		warnings = self._warnings({"Lot": {"pageSize": 5}})
		self.assertEqual(len(warnings), 1, warnings)
		self.assertIn("unknown key 'pageSize' inside listViews['Lot']", warnings[0])

	def test_variant_vocabulary(self):
		for good in ui_config.LIST_VIEW_VARIANTS:
			self.assertEqual(self._warnings({"Lot": {"variant": good}}), [], good)
		warnings = self._warnings({"Lot": {"variant": "grid"}})
		self.assertEqual(len(warnings), 1, warnings)
		self.assertIn("listViews['Lot'].variant 'grid' is not one of", warnings[0])

	def test_column_fieldname_typo_warns(self):
		warnings = self._warnings(
			{"Lot": {"columns": [{"field": "lot_name"}, {"field": "no_such_field"}]}}
		)
		self.assertEqual(len(warnings), 1, warnings)
		self.assertIn("column 'no_such_field' is not a field on 'Lot'", warnings[0])

	def test_column_object_families(self):
		# Dead annotation key ("type" — the client reads only field/label).
		warnings = self._warnings(
			{"Lot": {"columns": [{"field": "lot_name", "label": "Lot", "type": "Date"}]}}
		)
		self.assertEqual(len(warnings), 1, warnings)
		self.assertIn("key 'type' is ignored", warnings[0])
		# Object without a usable field.
		warnings = self._warnings({"Lot": {"columns": [{"label": "X"}]}})
		self.assertEqual(len(warnings), 1, warnings)
		self.assertIn("needs a non-empty string 'field'", warnings[0])
		# Non-string label.
		warnings = self._warnings({"Lot": {"columns": [{"field": "lot_name", "label": 7}]}})
		self.assertEqual(len(warnings), 1, warnings)
		self.assertIn("label must be a string", warnings[0])

	def test_group_by_and_title_field_checked_against_meta(self):
		for key in ("groupBy", "titleField"):
			warnings = self._warnings({"Lot": {key: "no_such_field"}})
			self.assertEqual(len(warnings), 1, f"{key}: {warnings}")
			self.assertIn(f"listViews['Lot'].{key} 'no_such_field' is not a field on 'Lot'", warnings[0])
			warnings = self._warnings({"Lot": {key: 7}})
			self.assertEqual(len(warnings), 1, f"{key}: {warnings}")
			self.assertIn(f"listViews['Lot'].{key} must be a fieldname string", warnings[0])

	def test_overrides_layer_gets_the_same_deep_checks(self):
		warnings = self._warnings({"Lot": {"variant": "grid"}}, layer="overrides")
		self.assertEqual(len(warnings), 1, warnings)
		self.assertIn("overrides: listViews['Lot'].variant 'grid'", warnings[0])


class TestUIConfigItem17NavAndCatalog(IntegrationTestCase):
	"""Nav deep checks (item 17): doctypes vs the consumer-declared /web
	catalog, the {view:'home'} special case, duplicate detection, unknown-key
	warnings at every level, and dead nav.hidden targets."""

	@staticmethod
	def _nav_warnings(nav, layer="layout"):
		if layer == "layout":
			return ui_config.validate_config(dict(LAYOUT_CONFIG, nav=nav), layer="layout")
		return ui_config.validate_config({"schema_version": 1, "nav": nav}, layer="overrides")

	@staticmethod
	def _items_nav(items, hidden=None):
		return {
			"groups": [{"id": "G", "label": "G", "items": items}],
			"hidden": hidden or {},
		}

	def test_web_doctype_catalog_helper_reads_the_hook_fail_safe(self):
		with patch.object(frappe, "get_hooks", return_value=["Lot", "Item"]):
			self.assertEqual(ui_config._web_doctype_catalog(), {"Lot", "Item"})
		with patch.object(frappe, "get_hooks", return_value=[]):
			self.assertIsNone(ui_config._web_doctype_catalog())
		with patch.object(frappe, "get_hooks", side_effect=RuntimeError):
			self.assertIsNone(ui_config._web_doctype_catalog())

	def test_site_catalog_hook_is_declared_on_this_bench(self):
		# essdee_yrp declares yrp_web_doctype_catalog (hooks.py) — the nine
		# /web doctypes. On a bare-yrp site this returns None and checks skip.
		catalog = ui_config._web_doctype_catalog()
		if catalog is None:
			self.skipTest("no yrp_web_doctype_catalog hook on this site")
		self.assertIn("Lot", catalog)
		self.assertIn("Terms and Condition", catalog)

	def test_existing_but_off_catalog_nav_doctype_warns(self):
		with patch.object(ui_config, "_web_doctype_catalog", return_value={"Lot"}):
			warnings = self._nav_warnings(
				self._items_nav([{"doctype": "Lot"}, {"doctype": "Work Order"}])
			)
		self.assertEqual(len(warnings), 1, warnings)
		self.assertIn("nav doctype 'Work Order' is not in the /web doctype catalog", warnings[0])

	def test_view_home_item_is_soft_not_a_hard_error(self):
		warnings = self._nav_warnings(
			self._items_nav([{"view": "home"}, {"doctype": "Lot"}])
		)
		self.assertEqual(len(warnings), 1, warnings)
		self.assertIn("{'view': 'home'} is redundant", warnings[0])
		# Any OTHER doctype-less item keeps the pre-existing hard error.
		with self.assertRaises(frappe.ValidationError):
			self._nav_warnings(self._items_nav([{"icon": "pi pi-th-large"}]))

	def test_duplicate_nav_doctypes_and_group_ids_warn(self):
		nav = {
			"groups": [
				{"id": "A", "label": "A", "items": [{"doctype": "Lot"}, {"doctype": "Lot"}]},
				{"id": "A", "label": "Again", "items": [{"doctype": "Lot"}]},
			],
			"hidden": {},
		}
		warnings = self._nav_warnings(nav)
		self.assertEqual(len(warnings), 2, warnings)
		self.assertTrue(any("nav doctype 'Lot' appears 3 times" in w for w in warnings))
		self.assertTrue(any("nav group id 'A' appears 2 times" in w for w in warnings))

	def test_unknown_keys_warn_at_every_nav_level(self):
		nav = {
			"position": "sidebar",
			"sparkles": 1,
			"groups": [
				{
					"id": "G",
					"label": "G",
					"colour": "red",
					"items": [{"doctype": "Lot", "label": "My Lots"}],
				}
			],
			"hidden": {},
		}
		warnings = self._nav_warnings(nav)
		self.assertEqual(len(warnings), 3, warnings)
		self.assertTrue(any("unknown key 'sparkles' inside nav" in w for w in warnings))
		self.assertTrue(any("unknown key 'colour' inside nav group" in w for w in warnings))
		self.assertTrue(
			any(
				"unknown key 'label' inside nav item 'Lot' — the client reads only doctype/icon" in w
				for w in warnings
			)
		)

	def test_dead_nav_hidden_target_warns_on_layout_layer_only(self):
		nav = self._items_nav([{"doctype": "Lot"}], hidden={"Delivery Challan": True})
		warnings = self._nav_warnings(nav)
		self.assertEqual(len(warnings), 1, warnings)
		self.assertIn(
			"nav.hidden['Delivery Challan'] matches no nav item doctype", warnings[0]
		)
		# Overrides legitimately hide doctypes that live in the LAYOUT's groups.
		self.assertEqual(
			self._nav_warnings({"hidden": {"Delivery Challan": True}}, layer="overrides"), []
		)

	def test_quick_create_off_catalog_warns(self):
		# Catalog keeps the base config's nav doctypes (Lot, Work Order) so
		# only the off-catalog quickCreate entry warns.
		with patch.object(ui_config, "_web_doctype_catalog", return_value={"Lot", "Work Order"}):
			warnings = ui_config.validate_config(
				dict(LAYOUT_CONFIG, quickCreate=["Lot", "Item"]), layer="layout"
			)
		self.assertEqual(len(warnings), 1, warnings)
		self.assertIn("quickCreate doctype 'Item' is not in the /web doctype catalog", warnings[0])


class TestUIConfigItem17ScreensAndBlocks(IntegrationTestCase):
	"""Screens/blocks deep checks (item 17): unknown screen keys, unknown keys
	inside screens.home/blocks, off-vocabulary block size, duplicate block ids,
	dead hidden targets, unknown props on known block types, and the
	doctype-naming props (home-recent doctypes, home-greeting newCta)."""

	@staticmethod
	def _screens_warnings(screens, layer="layout"):
		if layer == "layout":
			return ui_config.validate_config(dict(LAYOUT_CONFIG, screens=screens), layer="layout")
		return ui_config.validate_config(
			{"schema_version": 1, "screens": screens}, layer="overrides"
		)

	def _block_warnings(self, block):
		return self._screens_warnings({"home": {"blocks": [block], "hidden": {}}})

	def test_unknown_screen_key_warns(self):
		warnings = self._screens_warnings(
			{"home": {"blocks": [], "hidden": {}}, "hme": {"blocks": []}}
		)
		self.assertEqual(len(warnings), 1, warnings)
		self.assertIn("screens['hme'] is not rendered by any client today", warnings[0])

	def test_unknown_key_inside_screens_home_warns(self):
		warnings = self._screens_warnings({"home": {"blocks": [], "hidden": {}, "layout": "grid"}})
		self.assertEqual(len(warnings), 1, warnings)
		self.assertIn("unknown key 'layout' inside screens.home", warnings[0])

	def test_block_size_vocabulary(self):
		for good in ui_config.BLOCK_SIZES:
			block = {"id": "g", "type": "home-greeting", "size": good, "props": {}}
			self.assertEqual(self._block_warnings(block), [], good)
		warnings = self._block_warnings(
			{"id": "g", "type": "home-greeting", "size": "wide", "props": {}}
		)
		self.assertEqual(len(warnings), 1, warnings)
		self.assertIn("size 'wide' is not one of full, half, third", warnings[0])

	def test_unknown_key_inside_a_block_warns(self):
		warnings = self._block_warnings(
			{"id": "g", "type": "home-greeting", "span": "full", "props": {}}
		)
		self.assertEqual(len(warnings), 1, warnings)
		self.assertIn("unknown key 'span' inside block 'g'", warnings[0])

	def test_duplicate_block_ids_warn(self):
		warnings = self._screens_warnings(
			{
				"home": {
					"blocks": [
						{"id": "greet", "type": "home-greeting", "props": {}},
						{"id": "greet", "type": "home-queues", "props": {}},
					],
					"hidden": {},
				}
			}
		)
		self.assertEqual(len(warnings), 1, warnings)
		self.assertIn("block id 'greet' appears 2 times", warnings[0])

	def test_dead_home_hidden_target_warns_on_layout_layer_only(self):
		screens = {
			"home": {
				"blocks": [{"id": "greet", "type": "home-greeting", "props": {}}],
				"hidden": {"queues": True},
			}
		}
		warnings = self._screens_warnings(screens)
		self.assertEqual(len(warnings), 1, warnings)
		self.assertIn("screens.home.hidden['queues'] matches no block id", warnings[0])
		# Overrides legitimately hide LAYOUT block ids (no blocks of their own).
		self.assertEqual(
			self._screens_warnings({"home": {"hidden": {"queues": True}}}, layer="overrides"), []
		)

	def test_unknown_prop_on_a_known_block_type_warns(self):
		warnings = self._block_warnings(
			{"id": "r", "type": "record-list", "props": {"doctype": "Lot", "pagesize": 8}}
		)
		self.assertEqual(len(warnings), 1, warnings)
		self.assertIn(
			"prop 'pagesize' is not a prop of block type 'record-list'", warnings[0]
		)
		# Unknown block types still skip prop validation (client may be newer).
		self.assertEqual(
			self._block_warnings(
				{"id": "x", "type": "some-future-block", "props": {"anything": 1}}
			),
			[],
		)

	def test_home_recent_doctypes_checked_against_site_and_catalog(self):
		warnings = self._block_warnings(
			{
				"id": "recent",
				"type": "home-recent",
				"props": {"doctypes": ["Work Order", "No Such DocType"]},
			}
		)
		self.assertEqual(len(warnings), 1, warnings)
		self.assertIn("'No Such DocType' does not exist as a DocType", warnings[0])
		# Catalog keeps the base config's nav doctypes so only the block warns.
		with patch.object(ui_config, "_web_doctype_catalog", return_value={"Lot", "Work Order"}):
			warnings = self._block_warnings(
				{"id": "recent", "type": "home-recent", "props": {"doctypes": ["Item"]}}
			)
		self.assertEqual(len(warnings), 1, warnings)
		self.assertIn("'Item' is not in the /web doctype catalog", warnings[0])

	def test_new_cta_deep_checks(self):
		# Valid shape (the live Demo 7 / Warm Tiles form) stays warning-free.
		self.assertEqual(
			self._block_warnings(
				{
					"id": "g",
					"type": "home-greeting",
					"props": {"newCta": {"primary": "Lot", "menu": ["Work Order"]}},
				}
			),
			[],
		)
		cases = {
			"newCta.primary must be a DocType name": {"primary": 7},
			"'No Such DocType' does not exist as a DocType": {"primary": "No Such DocType"},
			"newCta.menu must be a list of DocType names": {"menu": "Work Order"},
			"unknown key 'colour' inside newCta": {"primary": "Lot", "colour": "red"},
		}
		for fragment, new_cta in cases.items():
			warnings = self._block_warnings(
				{"id": "g", "type": "home-greeting", "props": {"newCta": new_cta}}
			)
			self.assertEqual(len(warnings), 1, f"{new_cta}: {warnings}")
			self.assertIn(fragment, warnings[0])
		# Catalog keeps the base config's nav doctypes so only newCta warns.
		with patch.object(ui_config, "_web_doctype_catalog", return_value={"Lot", "Work Order"}):
			warnings = self._block_warnings(
				{
					"id": "g",
					"type": "home-greeting",
					"props": {"newCta": {"primary": "Lot", "menu": ["Item"]}},
				}
			)
		self.assertEqual(len(warnings), 1, warnings)
		self.assertIn("newCta doctype 'Item' is not in the /web doctype catalog", warnings[0])
