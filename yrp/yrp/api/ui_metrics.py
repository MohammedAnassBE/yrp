# Copyright (c) 2026, Essdee and contributors
# For license information, please see license.txt

"""Server-side METRICS + CALCULATIONS registries for the per-user /web UI.

Mirrors the demo engine's section-2 named registries ("custom ui/demos/
_template.html": ``METRICS`` and ``CALCULATIONS``) with REAL queries against
this site's schema. A layout may only reference these by NAME — it can never
define logic (spec "custom ui/PER_USER_UI_SPEC.md" §6.4 knobs-are-props).

Design rules:

- **Parity with the live /web home.** Base queue metrics use the exact filters
  ``useHomeQueues.js`` deep-links and counts with today, and the counts run
  through ``frappe.get_list`` — the same permission-aware DatabaseQuery
  machinery ``frappe.desk.reportview.get_count`` (the frontend's count
  endpoint) executes. Same filters + same machinery = same numbers.
- **Permission gating.** A metric is OMITTED (silently, mirroring the
  frontend's ``visibleQueues()`` gate) when the caller lacks read permission
  on any DocType it queries. Arrangement never grants capability (spec §15):
  referencing a metric in a layout cannot leak counts the user could not
  read from the list view itself.
- **Never an error.** Unknown keys are omitted with an entry in ``warnings``;
  a compute failure degrades that one metric (warning + Error Log), exactly
  like the home cards degrade to an em dash. ``run_ui_calculation`` is the
  opposite by contract: unknown names / bad params throw clean messages.
- **goto** is the engine-owned deep-link target for a stat card click:
  ``{doctype, filters}`` where ``filters`` is a JSON array of
  ``[field, operator, value]`` triples — the exact shape the /web
  DynamicListPage ``?filters=`` base filter parses.

Colocated tests: ``test_ui_metrics.py``.
"""

import json
from copy import deepcopy

import frappe
from frappe import _
from frappe.utils import cint, flt, nowdate

# ── shared filter triples (single source of truth for value + goto) ─────────

# Exact parity with useHomeQueues.js for base-owned transactions.
OPEN_WO_FILTERS = [["docstatus", "=", 1], ["status", "not in", ["Closed", "Cancelled"]]]
DRAFT_DC_FILTERS = [["docstatus", "=", 0]]
DRAFT_GRN_FILTERS = [["docstatus", "=", 0]]

# Work Order statuses that mean "nothing left to chase" (work_order.py
# set_status vocabulary) — used by the `delayed` metric.
WO_SETTLED_STATUSES = ["Fully Received", "Fully Billed", "Closed", "Cancelled"]


def _delayed_wo_filters():
	"""Submitted, still open, past its expected delivery date, not settled.

	Built per call because "today" moves. The Work Order carries no "Delayed"
	status (unlike the demo dataset), so delay is derived from
	``expected_delivery_date`` — the WO's own promised date field.
	"""
	return [
		["docstatus", "=", 1],
		["open_status", "=", "Open"],
		["status", "not in", list(WO_SETTLED_STATUSES)],
		["expected_delivery_date", "is", "set"],
		["expected_delivery_date", "<", nowdate()],
	]


# ── query helpers ────────────────────────────────────────────────────────────


def _count(doctype, filters):
	"""Permission-aware count via ``frappe.get_list`` — the same
	DatabaseQuery-with-user-permissions path reportview.get_count runs for the
	frontend, so numbers match the live home cards for the same user."""
	rows = frappe.get_list(
		doctype, filters=deepcopy(filters), fields=[{"COUNT": "name", "as": "value"}]
	)
	return cint(rows[0]["value"]) if rows else 0


def _received_from_rows(rows):
	"""The Work Order engine's own received/delivered math, reused verbatim.

	``work_order.py set_status`` (the authoritative status engine) computes
	per WO: ``received = Σ qty − Σ max(pending_quantity, 0)`` — pending is
	floored PER ROW at 0 so an excess receipt on one row (negative pending)
	can never mask another row's genuinely-owed pending. Grouping is per
	parent WO, then each WO's total is floored at 0 (the engine only acts on
	``received_qty > 0``). Same formula for deliverables (delivered side).
	"""
	per_wo = {}
	for row in rows:
		bucket = per_wo.setdefault(row.parent, {"qty": 0.0, "pending": 0.0})
		bucket["qty"] += flt(row.qty)
		bucket["pending"] += max(flt(row.pending_quantity), 0)
	return sum(max(b["qty"] - b["pending"], 0) for b in per_wo.values())


def _wo_child_rows(child_doctype, extra_filters=None):
	"""Submitted Work Order child rows (child docstatus mirrors the parent).

	``frappe.get_all`` skips row-level permissions, so every caller MUST scope
	the rows to Work Orders the user may read with a ``{"parent": ["in",
	permitted_names]}`` filter (2026-07-16 review): the doctype-level
	``has_permission`` gate alone would let a User-Permission-restricted user
	aggregate GLOBAL child-row totals.
	"""
	filters = {"parenttype": 'YRP Work Order', "docstatus": 1}
	filters.update(extra_filters or {})
	return frappe.get_all(
		child_doctype,
		filters=filters,
		fields=["parent", "qty", "pending_quantity"],
		parent_doctype='YRP Work Order',
	)


# ── per-metric computes (named functions for junior readability) ────────────


def _open_wos():
	return _count('YRP Work Order', OPEN_WO_FILTERS)


def _draft_dcs():
	return _count('YRP Delivery Challan', DRAFT_DC_FILTERS)


def _draft_grns():
	return _count('YRP Goods Received Note', DRAFT_GRN_FILTERS)


def _stock_entries():
	return _count('YRP Stock Entry', [])


def _total_wo():
	return _count('YRP Work Order', [])


def _permitted_submitted_wos():
	"""Submitted Work Orders the SESSION user may read, with the fields the
	quantity metrics aggregate. ``frappe.get_list`` is the same User-Permission-
	aware DatabaseQuery machinery every other metric here runs, so the ordered
	and produced sides always aggregate the SAME scope (2026-07-16 review) — a
	restricted user can never see global produced totals or >100% completion."""
	return frappe.get_list(
		'YRP Work Order',
		filters=[["docstatus", "=", 1]],
		fields=["name", "planned_quantity"],
		limit=0,
	)


def _ordered_qty(wos=None):
	"""Σ planned_quantity over the caller-visible submitted WOs.
	``planned_quantity`` is the sticky original plan (``set_total_quantity``
	seeds it from total_quantity once and never zeroes it on close, unlike
	``total_quantity``)."""
	if wos is None:
		wos = _permitted_submitted_wos()
	return sum(flt(row.planned_quantity) for row in wos)


def _produced_qty(wos=None):
	"""Pieces received back from suppliers across the caller-visible submitted
	WOs — the engine's receivables math (see ``_received_from_rows``), scoped
	to the same permitted Work Orders the ordered side sums. No permitted WOs
	means 0, never an unfiltered child query."""
	if wos is None:
		wos = _permitted_submitted_wos()
	if not wos:
		return 0.0
	return _received_from_rows(
		_wo_child_rows(
			'YRP Work Order Receivables', {"parent": ["in", [row.name for row in wos]]}
		)
	)


def _completion():
	"""Produced as a % of ordered, rounded to an int. 0 when nothing ordered
	(the demo's ``ordered || 1`` guard yields the same 0). ONE permitted-WO
	fetch feeds both sides, so the ratio is always scope-consistent."""
	wos = _permitted_submitted_wos()
	ordered = _ordered_qty(wos)
	if not ordered:
		return 0
	return round(100 * _produced_qty(wos) / ordered)


def _delayed():
	return _count('YRP Work Order', _delayed_wo_filters())


# ── METRICS registry ─────────────────────────────────────────────────────────
# Each entry: label (card text), doctypes (ALL DocTypes the compute reads —
# the permission gate), compute (returns a number), goto (deep-link target;
# a callable so date-dependent filters are built per call).

METRICS = {
	"open_wos": {
		"label": "Open Work Orders",
		"doctypes": ['YRP Work Order'],
		"compute": _open_wos,
		"goto": lambda: {"doctype": 'YRP Work Order', "filters": deepcopy(OPEN_WO_FILTERS)},
	},
	"draft_dcs": {
		"label": "Draft Delivery Challans",
		"doctypes": ['YRP Delivery Challan'],
		"compute": _draft_dcs,
		"goto": lambda: {"doctype": 'YRP Delivery Challan', "filters": deepcopy(DRAFT_DC_FILTERS)},
	},
	"draft_grns": {
		"label": "Draft GRNs",
		"doctypes": ['YRP Goods Received Note'],
		"compute": _draft_grns,
		"goto": lambda: {"doctype": 'YRP Goods Received Note', "filters": deepcopy(DRAFT_GRN_FILTERS)},
	},
	"stock_entries": {
		"label": "Stock Entries",
		"doctypes": ['YRP Stock Entry'],
		"compute": _stock_entries,
		"goto": lambda: {"doctype": 'YRP Stock Entry', "filters": []},
	},
	"total_wo": {
		"label": "Work Orders",
		"doctypes": ['YRP Work Order'],
		"compute": _total_wo,
		"goto": lambda: {"doctype": 'YRP Work Order', "filters": []},
	},
	"ordered_qty": {
		"label": "Pieces Ordered",
		"doctypes": ['YRP Work Order'],
		"compute": _ordered_qty,
		"goto": lambda: {"doctype": 'YRP Work Order', "filters": [["docstatus", "=", 1]]},
	},
	"produced_qty": {
		"label": "Pieces Produced",
		"doctypes": ['YRP Work Order'],
		"compute": _produced_qty,
		"goto": lambda: {"doctype": 'YRP Work Order', "filters": [["docstatus", "=", 1]]},
	},
	"completion": {
		"label": "Completion %",
		"doctypes": ['YRP Work Order'],
		"compute": _completion,
		"goto": lambda: {"doctype": 'YRP Work Order', "filters": [["docstatus", "=", 1]]},
	},
	"delayed": {
		"label": "Delayed WOs",
		"doctypes": ['YRP Work Order'],
		"compute": _delayed,
		"goto": lambda: {"doctype": 'YRP Work Order', "filters": _delayed_wo_filters()},
	},
}


def _merge_hook_registry(base, hook_name):
	"""Merge code-owned downstream registry contributions deterministically.

	Base keys win. A malformed contribution or duplicate is logged and skipped,
	so a consumer hook cannot take the whole UI down.
	"""
	registry = dict(base)
	for path in frappe.get_hooks(hook_name) or []:
		try:
			contribution = frappe.get_attr(path)()
			if not isinstance(contribution, dict):
				raise TypeError(f"{path} must return a dict")
			_validate_registry_contribution(contribution, hook_name, path)
			duplicates = set(registry).intersection(contribution)
			if duplicates:
				raise ValueError(
					f"duplicate registry key(s): {', '.join(sorted(duplicates))}"
				)
			# Merge a contribution atomically. If any key is invalid/duplicated,
			# none of that consumer's keys should leak into the live registry.
			registry.update(contribution)
		except Exception:
			try:
				frappe.log_error(
					title=f"UI registry hook failed: {hook_name}"[:140],
					message=frappe.get_traceback(),
				)
			except Exception:
				pass
	return registry


def _validate_registry_contribution(contribution, hook_name, path):
	for key, spec in contribution.items():
		if not isinstance(key, str) or not key:
			raise TypeError(f"{path} returned an invalid registry key")
		if not isinstance(spec, dict) or not isinstance(spec.get("label"), str):
			raise TypeError(f"{path}.{key} must be a dict with a string label")
		if hook_name == "yrp_ui_metrics":
			if not isinstance(spec.get("doctypes"), list) or not spec["doctypes"]:
				raise TypeError(f"{path}.{key}.doctypes must be a non-empty list")
			if not callable(spec.get("compute")) or not callable(spec.get("goto")):
				raise TypeError(f"{path}.{key} must define callable compute and goto")
		elif hook_name == "yrp_ui_calculations" and not callable(spec.get("run")):
			raise TypeError(f"{path}.{key} must define a callable run")


def get_metric_registry():
	return _merge_hook_registry(METRICS, "yrp_ui_metrics")


def get_calculation_registry():
	return _merge_hook_registry(CALCULATIONS, "yrp_ui_calculations")


def _parse_keys(keys, registry=None):
	"""``keys`` over the wire: None (= all), a JSON list string, a
	comma-separated string, or an in-process list/tuple. Returns an ordered,
	de-duplicated list of requested key strings."""
	registry = registry or get_metric_registry()
	if keys is None or keys == "":
		return list(registry)
	if isinstance(keys, str):
		stripped = keys.strip()
		if stripped.startswith("["):
			try:
				keys = json.loads(stripped)
			except ValueError:
				frappe.throw(_("keys is not valid JSON"), title=_("Invalid Metric Keys"))
		else:
			keys = [part.strip() for part in stripped.split(",") if part.strip()]
	if not isinstance(keys, (list, tuple)):
		frappe.throw(_("keys must be a list of metric names"), title=_("Invalid Metric Keys"))
	out = []
	for key in keys:
		if key not in out:
			out.append(key)
	return out


@frappe.whitelist()
def get_ui_metrics(keys=None):
	"""Compute the requested named metrics for the SESSION user.

	Returns ``{"metrics": [...], "warnings": [...]}`` where each metric is
	``{key, label, value, goto: {doctype, filters}}``. Degradation contract:

	- unknown key → omitted, one ``warnings`` entry (never an error);
	- caller lacks read permission on a metric's DocType → omitted silently
	  (mirrors the frontend's ``visibleQueues()`` gate);
	- metric's DocType not installed on this site → omitted + warning;
	- compute failure → omitted + warning + Error Log (one bad metric never
	  takes the home page down).
	"""
	warnings = []
	metrics = []
	registry = get_metric_registry()

	for key in _parse_keys(keys, registry):
		# Type-check BEFORE the dict lookup (2026-07-16 review): an unhashable
		# entry (list/dict) in the keys array would raise TypeError inside
		# METRICS.get() — degradation contract says warn, never error.
		if not isinstance(key, str) or key not in registry:
			warnings.append(_("unknown metric key {0!r} ignored").format(key))
			continue
		spec = registry[key]

		missing = [dt for dt in spec["doctypes"] if not frappe.db.exists("DocType", dt)]
		if missing:
			warnings.append(
				_("metric '{0}' skipped: DocType {1} is not installed").format(
					key, ", ".join(missing)
				)
			)
			continue

		if not all(frappe.has_permission(dt, "read") for dt in spec["doctypes"]):
			continue  # silent omission — permission gate, never an error

		try:
			value = spec["compute"]()
		except Exception:
			warnings.append(_("metric '{0}' failed to compute").format(key))
			_log_metric_error(key)
			continue

		metrics.append(
			{"key": key, "label": spec["label"], "value": value, "goto": spec["goto"]()}
		)

	return {"metrics": metrics, "warnings": warnings}


def _log_metric_error(key):
	"""Error Log write that can itself never break the metrics response."""
	try:
		frappe.log_error(
			title=f"UI metrics: '{key}' failed"[:140], message=frappe.get_traceback()
		)
	except Exception:
		pass


# ── CALCULATIONS registry ────────────────────────────────────────────────────

CALCULATIONS = {}


@frappe.whitelist()
def run_ui_calculation(name=None, params=None):
	"""Run one named calculation from the CALCULATIONS registry.

	Opposite degradation contract to ``get_ui_metrics`` (a calculation is an
	explicit user action, not passive furniture): unknown ``name`` and bad
	``params`` THROW with a clean message. Each calculation validates its own
	params and enforces read permission on every DocType it touches.
	"""
	registry = get_calculation_registry()
	if not name or not isinstance(name, str) or name not in registry:
		frappe.throw(
			_("Unknown calculation {0!r}. Available: {1}").format(
				name, ", ".join(sorted(registry))
			),
			title=_("Unknown Calculation"),
		)

	if params is None or params == "":
		params = {}
	elif isinstance(params, str):
		try:
			params = json.loads(params)
		except ValueError:
			frappe.throw(_("params is not valid JSON"), title=_("Invalid Calculation Params"))
	if not isinstance(params, dict):
		frappe.throw(_("params must be a JSON object"), title=_("Invalid Calculation Params"))

	return registry[name]["run"](params)
