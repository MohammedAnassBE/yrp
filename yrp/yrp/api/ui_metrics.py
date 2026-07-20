# Copyright (c) 2026, Essdee and contributors
# For license information, please see license.txt

"""Server-side METRICS + CALCULATIONS registries for the per-user /web UI.

Mirrors the demo engine's section-2 named registries ("custom ui/demos/
_template.html": ``METRICS`` and ``CALCULATIONS``) with REAL queries against
this site's schema. A layout may only reference these by NAME — it can never
define logic (spec "custom ui/PER_USER_UI_SPEC.md" §6.4 knobs-are-props).

Design rules:

- **Parity with the live /web home.** The four queue metrics (``open_lots``,
  ``open_wos``, ``draft_dcs``, ``draft_grns``) use the EXACT filter triples
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

# Exact parity with useHomeQueues.js — these four MUST match the live home.
OPEN_LOT_FILTERS = [["status", "=", "Open"]]
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
	filters = {"parenttype": "Work Order", "docstatus": 1}
	filters.update(extra_filters or {})
	return frappe.get_all(
		child_doctype,
		filters=filters,
		fields=["parent", "qty", "pending_quantity"],
		parent_doctype="Work Order",
	)


# ── per-metric computes (named functions for junior readability) ────────────


def _open_lots():
	return _count("Lot", OPEN_LOT_FILTERS)


def _open_wos():
	return _count("Work Order", OPEN_WO_FILTERS)


def _draft_dcs():
	return _count("Delivery Challan", DRAFT_DC_FILTERS)


def _draft_grns():
	return _count("Goods Received Note", DRAFT_GRN_FILTERS)


def _stock_entries():
	return _count("Stock Entry", [])


def _total_wo():
	return _count("Work Order", [])


def _permitted_submitted_wos():
	"""Submitted Work Orders the SESSION user may read, with the fields the
	quantity metrics aggregate. ``frappe.get_list`` is the same User-Permission-
	aware DatabaseQuery machinery every other metric here runs, so the ordered
	and produced sides always aggregate the SAME scope (2026-07-16 review) — a
	restricted user can never see global produced totals or >100% completion."""
	return frappe.get_list(
		"Work Order",
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
			"Work Order Receivables", {"parent": ["in", [row.name for row in wos]]}
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
	return _count("Work Order", _delayed_wo_filters())


def _active_lot_names():
	"""Distinct lots referenced by open WOs (demo semantics: lots that still
	have unfinished work), via permission-aware ``frappe.get_list`` — the
	caller only ever counts/sees lots from Work Orders they may read. ``lot``
	on Work Order is the stock-dimension Custom Field — absent when the site
	has no production-group dimension, in which case the metric degrades to
	an empty list (count 0) instead of erroring. Shared by the compute AND
	the tile's goto, so the deep-linked list always shows exactly the counted
	lots."""
	if not frappe.get_meta("Work Order").has_field("lot"):
		return []
	return frappe.get_list(
		"Work Order",
		filters=deepcopy(OPEN_WO_FILTERS) + [["lot", "is", "set"]],
		pluck="lot",
		distinct=True,
		limit=0,
	)


def _active_lots():
	return len(_active_lot_names())


# ── METRICS registry ─────────────────────────────────────────────────────────
# Each entry: label (card text), doctypes (ALL DocTypes the compute reads —
# the permission gate), compute (returns a number), goto (deep-link target;
# a callable so date-dependent filters are built per call).

METRICS = {
	"open_lots": {
		"label": "Open Lots",
		"doctypes": ["Lot"],
		"compute": _open_lots,
		"goto": lambda: {"doctype": "Lot", "filters": deepcopy(OPEN_LOT_FILTERS)},
	},
	"open_wos": {
		"label": "Open Work Orders",
		"doctypes": ["Work Order"],
		"compute": _open_wos,
		"goto": lambda: {"doctype": "Work Order", "filters": deepcopy(OPEN_WO_FILTERS)},
	},
	"draft_dcs": {
		"label": "Draft Delivery Challans",
		"doctypes": ["Delivery Challan"],
		"compute": _draft_dcs,
		"goto": lambda: {"doctype": "Delivery Challan", "filters": deepcopy(DRAFT_DC_FILTERS)},
	},
	"draft_grns": {
		"label": "Draft GRNs",
		"doctypes": ["Goods Received Note"],
		"compute": _draft_grns,
		"goto": lambda: {"doctype": "Goods Received Note", "filters": deepcopy(DRAFT_GRN_FILTERS)},
	},
	"stock_entries": {
		"label": "Stock Entries",
		"doctypes": ["Stock Entry"],
		"compute": _stock_entries,
		"goto": lambda: {"doctype": "Stock Entry", "filters": []},
	},
	"total_wo": {
		"label": "Work Orders",
		"doctypes": ["Work Order"],
		"compute": _total_wo,
		"goto": lambda: {"doctype": "Work Order", "filters": []},
	},
	"ordered_qty": {
		"label": "Pieces Ordered",
		"doctypes": ["Work Order"],
		"compute": _ordered_qty,
		"goto": lambda: {"doctype": "Work Order", "filters": [["docstatus", "=", 1]]},
	},
	"produced_qty": {
		"label": "Pieces Produced",
		"doctypes": ["Work Order"],
		"compute": _produced_qty,
		"goto": lambda: {"doctype": "Work Order", "filters": [["docstatus", "=", 1]]},
	},
	"completion": {
		"label": "Completion %",
		"doctypes": ["Work Order"],
		"compute": _completion,
		"goto": lambda: {"doctype": "Work Order", "filters": [["docstatus", "=", 1]]},
	},
	"delayed": {
		"label": "Delayed WOs",
		"doctypes": ["Work Order"],
		"compute": _delayed,
		"goto": lambda: {"doctype": "Work Order", "filters": _delayed_wo_filters()},
	},
	"active_lots": {
		# goto lands on the LOT list (2026-07-16 cleanup — the tile used to
		# deep-link the open-WO list). "lots with open WOs" is not a static
		# Lot-list filter, so the callable mirrors the metric's own query into
		# a name-in filter: tile count == list count for the same user (the
		# names come from Work Orders the caller may read; goto is built per
		# call, like _delayed_wo_filters). doctypes gates BOTH sides of the
		# tile: Work Order (what the compute queries) AND Lot (where the goto
		# lands) — a user who can't read Lot must not get a tile whose click
		# deep-links a list they can't open.
		"label": "Active Lots",
		"doctypes": ["Work Order", "Lot"],
		"compute": _active_lots,
		"goto": lambda: {"doctype": "Lot", "filters": [["name", "in", _active_lot_names()]]},
	},
}


def _parse_keys(keys):
	"""``keys`` over the wire: None (= all), a JSON list string, a
	comma-separated string, or an in-process list/tuple. Returns an ordered,
	de-duplicated list of requested key strings."""
	if keys is None or keys == "":
		return list(METRICS)
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

	for key in _parse_keys(keys):
		# Type-check BEFORE the dict lookup (2026-07-16 review): an unhashable
		# entry (list/dict) in the keys array would raise TypeError inside
		# METRICS.get() — degradation contract says warn, never error.
		if not isinstance(key, str) or key not in METRICS:
			warnings.append(_("unknown metric key {0!r} ignored").format(key))
			continue
		spec = METRICS[key]

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


def _calc_lot_balance(params):
	"""Balance of a Lot across its submitted Work Orders.

	No single authoritative "lot balance" function exists in yrp/essdee_yrp
	(``fabric_tracking``'s balance is per fabric-program row, and the Lot
	controller only totals its own order items), so this is a read-only
	aggregation that REUSES the Work Order status engine's row math
	(``work_order.py set_status`` — see ``_received_from_rows``). Formula:

	    ordered   = Σ planned_quantity          over WOs (lot = X, docstatus 1)
	    produced  = Σ per-WO max(Σqty − Σmax(pending, 0), 0)   over receivables
	    delivered = same formula                                over deliverables
	    balance   = max(ordered − produced, 0)   (pieces still to receive)

	"delivered" is materials sent TO suppliers (the real DC direction in this
	outsourced-production model), not finished goods going out — hence the
	line labels differ from the demo's customer-delivery framing.
	"""
	known = {"lot"}
	unknown = set(params) - known
	if unknown:
		frappe.throw(
			_("Unknown parameter(s) for lot_balance: {0}").format(", ".join(sorted(unknown))),
			title=_("Invalid Calculation Params"),
		)

	lot = params.get("lot")
	if not lot or not isinstance(lot, str):
		frappe.throw(
			_("lot_balance requires a 'lot' parameter (a Lot name)"),
			title=_("Invalid Calculation Params"),
		)

	if not frappe.db.exists("DocType", "Lot"):
		frappe.throw(_("The Lot DocType is not installed on this site"))
	for doctype in ("Lot", "Work Order"):
		frappe.has_permission(doctype, "read", throw=True)
	if not frappe.db.exists("Lot", lot):
		frappe.throw(_("Lot {0} not found").format(frappe.bold(lot)), frappe.DoesNotExistError)
	# Row-level gate (2026-07-16 review): doctype-level read alone would let a
	# User-Permission-restricted user compute ANY Lot's balance by name.
	frappe.has_permission("Lot", "read", doc=lot, throw=True)
	if not frappe.get_meta("Work Order").has_field("lot"):
		frappe.throw(_("Work Order has no 'lot' dimension field on this site"))

	# frappe.get_list (never get_all) so the aggregation covers exactly the
	# Work Orders this user may read — same row-level scope as the metrics.
	wo_rows = frappe.get_list(
		"Work Order",
		filters=[["lot", "=", lot], ["docstatus", "=", 1]],
		fields=["name", "planned_quantity"],
		limit=0,
	)
	wo_names = [row.name for row in wo_rows]
	ordered = sum(flt(row.planned_quantity) for row in wo_rows)

	produced = delivered = 0.0
	if wo_names:
		produced = _received_from_rows(
			_wo_child_rows("Work Order Receivables", {"parent": ["in", wo_names]})
		)
		delivered = _received_from_rows(
			_wo_child_rows("Work Order Deliverables", {"parent": ["in", wo_names]})
		)

	balance = max(ordered - produced, 0)
	return {
		"name": "lot_balance",
		"label": _("Lot balance"),
		"params": {"lot": lot},
		"value": balance,
		"lines": [
			[_("Work orders"), len(wo_names)],
			[_("Ordered"), ordered],
			[_("Produced (received back)"), produced],
			[_("Materials delivered to supplier"), delivered],
			[_("Balance to receive"), balance],
		],
	}


CALCULATIONS = {
	"lot_balance": {"label": "Lot balance", "run": _calc_lot_balance},
}


@frappe.whitelist()
def run_ui_calculation(name=None, params=None):
	"""Run one named calculation from the CALCULATIONS registry.

	Opposite degradation contract to ``get_ui_metrics`` (a calculation is an
	explicit user action, not passive furniture): unknown ``name`` and bad
	``params`` THROW with a clean message. Each calculation validates its own
	params and enforces read permission on every DocType it touches.
	"""
	if not name or not isinstance(name, str) or name not in CALCULATIONS:
		frappe.throw(
			_("Unknown calculation {0!r}. Available: {1}").format(
				name, ", ".join(sorted(CALCULATIONS))
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

	return CALCULATIONS[name]["run"](params)
