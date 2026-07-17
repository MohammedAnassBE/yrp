# Copyright (c) 2026, Essdee and contributors
# For license information, please see license.txt

"""Fleet tooling for the per-user /web UI — bulk layout assignment + the
dedicated verification user ("custom ui/BASE_FINALIZATION_PLAN.md" §5 item 7).

Two affordances, both SM-territory, neither touching any real user's personal
layer (the LIVE-site rule: the owner switches his own YRP UI Preference at
will — tooling must never write records it wasn't explicitly pointed at):

- ``assign_layout`` — whitelisted, POST-only, SM-only: point N existing
  enabled users' ``YRP UI Preference.layout`` at ONE enabled layout in a
  single call (kills the per-user Desk clicking). Each user's ``overrides``
  and ``notes`` stay untouched — ONLY the ``layout`` field is written.
- ``seed_verify_user`` — NOT whitelisted (bench execute only): idempotent
  seeder for the dedicated verification user, so layout verification never
  has to log in as (or mutate) a real person.

PERMISSION RULE (spec §15): arrangement never grants capability — assigning
a layout changes what a user's /web LOOKS like, never what they may read or
do; every render still passes the client's canRead/canCreate/... gates and
the server's real permission checks.

Colocated tests: ``test_ui_fleet.py``.
"""

import json
import os

import frappe
from frappe import _
from frappe.rate_limiter import rate_limit
from frappe.utils.password import update_password

from yrp.yrp.api.ui_config import DEFAULT_LAYOUT_NAME

# Rate limit for the assign endpoint (house style: frappe.rate_limiter,
# per-IP, like ui_config's save endpoints). Assignment is a deliberate SM
# batch action — 10 calls/minute is far above any human workflow.
ASSIGN_RATE_LIMIT = 10
ASSIGN_RATE_WINDOW_SECONDS = 60

# The dedicated verification user. Exists so checking a layout NEVER touches
# a real person's account or preference record (LIVE-site protocol).
VERIFY_USER = "ui-verify@essdee.fit"
# Credentials land ONLY here (two lines: email, password; chmod 600) — never
# in any repo file, never in a return value, never in a log.
VERIFY_CREDS_FILE = "~/.frappe-ui-verify-creds"


# ── bulk layout assignment (whitelisted, SM-only) ────────────────────────────


@frappe.whitelist(methods=["POST"])
@rate_limit(limit=ASSIGN_RATE_LIMIT, seconds=ASSIGN_RATE_WINDOW_SECONDS)
def assign_layout(layout=None, users=None):
	"""Point every user in ``users`` at ``layout`` (SM-only bulk assign).

	POST-only for the same reason as ``save_my_ui_overrides``: a GET would
	report an assignment that Frappe's end-of-request rollback then undoes.

	``layout`` must name an existing, enabled ``UI Layout`` — a bad layout is
	a hard error BEFORE any user is touched (nothing to half-apply).
	``users`` is a JSON list (or in-process list) of user ids. Per-user
	failures NEVER abort the batch: unknown users, disabled users and
	unexpected save failures are reported in ``skipped`` with a reason while
	the rest of the batch proceeds.

	Upsert semantics (the ``save_my_ui_overrides`` savepoint pattern): an
	existing ``YRP UI Preference`` gets ONLY its ``layout`` field updated —
	``overrides`` and ``notes`` survive verbatim; a missing record is created
	fresh. Full doc API throughout, so ``track_changes`` keeps its audit trail
	and the controller's ``validate()`` runs.

	Returns ``{"assigned": [user, ...], "skipped": {user: reason, ...}}`` in
	input order (duplicates are processed once).
	"""
	frappe.only_for("System Manager")

	layout = _require_enabled_layout(layout)
	users = _parse_users(users)

	assigned = []
	skipped = {}
	seen = set()

	for entry in users:
		if not isinstance(entry, str) or not entry.strip():
			skipped[str(entry)] = _("not a user id string")
			continue
		user = entry.strip()
		if user in seen:
			continue  # duplicate input row — already handled, same outcome
		seen.add(user)

		# Never bulk-overwrite the built-in accounts: Administrator's LIVE
		# preference is the owner's personal layer (the LIVE-site rule — a
		# batch that happens to include it must not repoint it), and Guest
		# never renders /web layouts at all. Explicit skip with a reason.
		if user in ("Administrator", "Guest"):
			skipped[user] = _("built-in account — assign its layout individually, not in a batch")
			continue

		enabled = frappe.db.get_value("User", user, "enabled")
		if enabled is None:
			skipped[user] = _("unknown user")
			continue
		if not enabled:
			skipped[user] = _("user is disabled")
			continue

		# Savepoint around the whole per-user write: an unexpected save
		# failure rolls back ITS OWN partial writes only — never the batch,
		# never the request transaction (the never-hard-error-mid-batch rule).
		savepoint = "yrp_ui_fleet_assign"
		frappe.db.savepoint(savepoint)
		try:
			_upsert_layout(user, layout)
		except Exception:
			frappe.db.rollback(save_point=savepoint)
			skipped[user] = _("failed to save preference (see Error Log)")
			_log_fleet_error(f"UI fleet: assign_layout failed for {user}")
			continue
		assigned.append(user)

	return {"assigned": assigned, "skipped": skipped}


def _require_enabled_layout(layout):
	"""Hard gate, BEFORE the batch starts: the target layout must exist and
	be enabled. (Assigning a disabled layout would silently resolve every
	user to Default — §14 row 6 — which is never what the SM meant.)"""
	if not layout or not isinstance(layout, str):
		frappe.throw(
			_("layout is required and must be a UI Layout name"),
			title=_("Invalid Layout Assignment"),
		)
	row = frappe.db.get_value("UI Layout", layout, ["disabled"], as_dict=True)
	if not row:
		frappe.throw(
			_("UI Layout {0} does not exist").format(frappe.bold(layout)),
			title=_("Invalid Layout Assignment"),
		)
	if row.disabled:
		frappe.throw(
			_(
				"UI Layout {0} is disabled — users assigned to it would fall back "
				"to the {1} layout. Enable it first, or pick another layout."
			).format(frappe.bold(layout), frappe.bold(DEFAULT_LAYOUT_NAME)),
			title=_("Invalid Layout Assignment"),
		)
	return layout


def _parse_users(users):
	"""``users`` over the wire: a JSON list string; in-process: list/tuple.
	Anything else (incl. an empty list — an SM calling a bulk assign with
	nobody in it is a mistake worth hearing about) is a hard error."""
	if isinstance(users, str):
		try:
			users = json.loads(users)
		except ValueError:
			frappe.throw(_("users is not valid JSON"), title=_("Invalid Layout Assignment"))
	if not isinstance(users, (list, tuple)) or not users:
		frappe.throw(
			_("users must be a non-empty JSON list of user ids"),
			title=_("Invalid Layout Assignment"),
		)
	return list(users)


def _upsert_layout(user, layout):
	"""Write ONLY the ``layout`` field of ``user``'s preference, creating the
	record if missing — the ``ui_config._upsert_my_overrides`` savepoint-upsert
	pattern (§3.2): ``autoname: field:user`` makes a concurrent first save
	collide on the primary key instead of silently duplicating; the loser
	rolls back its failed insert only and updates the now-existing row."""
	if frappe.db.exists("YRP UI Preference", user):
		_update_layout_only(user, layout)
		return

	savepoint = "yrp_ui_fleet_upsert"
	frappe.db.savepoint(savepoint)
	try:
		frappe.get_doc(
			{"doctype": "YRP UI Preference", "user": user, "layout": layout}
		).insert(ignore_permissions=True)
	except frappe.DuplicateEntryError:
		# Lost the race — the row now exists. Undo the failed insert, then
		# update the winner's row so this assignment still lands.
		frappe.db.rollback(save_point=savepoint)
		_update_layout_only(user, layout)


def _update_layout_only(user, layout):
	doc = frappe.get_doc("YRP UI Preference", user)
	doc.layout = layout
	doc.save(ignore_permissions=True)


def _log_fleet_error(title):
	"""Error Log write that can itself never break the batch."""
	try:
		frappe.log_error(title=title[:140], message=frappe.get_traceback())
	except Exception:
		pass


# ── verification-user seeder (NOT whitelisted — bench execute only) ─────────


def seed_verify_user():
	"""Create/refresh the dedicated layout-verification user. NOT whitelisted
	— run it from the bench:

	    bench --site <site> execute yrp.yrp.api.ui_fleet.seed_verify_user

	(``bench execute`` runs as Administrator and commits on success.)

	What it guarantees, idempotently (re-runs refresh the password + file):

	- ``User`` ``ui-verify@essdee.fit`` exists, enabled, System User, named
	  "UI Verify", with the System Manager role. **Why System Manager in v1:**
	  the verify user must READ all 9 /web catalog doctypes (Lot, Work Order,
	  Work Order Correction, Delivery Challan, Goods Received Note, Stock
	  Entry, Item, Item Production Detail, Terms and Condition) so every
	  layout's nav/list/detail render is exercisable; no narrower shipped
	  role covers that set today. Tightening to a dedicated floor-role
	  profile (read-only over exactly those 9) is a future kit item.
	- A fresh random password, written ONLY to ``~/.frappe-ui-verify-creds``
	  (two lines: email, password; chmod 600). Never into a repo file, never
	  returned, never logged. Existing sessions are logged out on refresh.
	- A ``YRP UI Preference`` exists for it (layout Default on creation). An
	  existing preference is KEPT as-is, so a layout an SM deliberately put
	  under verification survives a password refresh.
	"""
	frappe.only_for("System Manager")

	created = not frappe.db.exists("User", VERIFY_USER)
	if created:
		frappe.get_doc(
			{
				"doctype": "User",
				"email": VERIFY_USER,
				"first_name": "UI",
				"last_name": "Verify",
				"user_type": "System User",
				"enabled": 1,
				"send_welcome_email": 0,
				"roles": [{"role": "System Manager"}],
			}
		).insert(ignore_permissions=True)
	else:
		_repair_verify_user()

	password = frappe.generate_hash(length=32)
	update_password(VERIFY_USER, password, logout_all_sessions=True)
	creds_file = _write_creds_file(VERIFY_USER, password)

	preference_created = _ensure_verify_preference()

	# NEVER include the password here — bench execute prints the return value.
	return {
		"user": VERIFY_USER,
		"user_created": created,
		"preference_created": preference_created,
		"creds_file": creds_file,
	}


def _repair_verify_user():
	"""Idempotent re-run path: put an existing verify user back into the
	guaranteed state (enabled System User with the System Manager role) —
	e.g. after someone disabled it between verification rounds."""
	doc = frappe.get_doc("User", VERIFY_USER)
	dirty = False
	if not doc.enabled:
		doc.enabled = 1
		dirty = True
	if doc.user_type != "System User":
		doc.user_type = "System User"
		dirty = True
	if "System Manager" not in {row.role for row in doc.roles}:
		doc.append("roles", {"role": "System Manager"})
		dirty = True
	if dirty:
		doc.save(ignore_permissions=True)


def _ensure_verify_preference():
	"""Create the verify user's YRP UI Preference if missing (layout Default,
	guarded on the record existing so a fixture-less site still seeds); an
	existing preference is kept verbatim. Returns whether it was created."""
	if frappe.db.exists("YRP UI Preference", VERIFY_USER):
		return False
	layout = (
		DEFAULT_LAYOUT_NAME if frappe.db.exists("UI Layout", DEFAULT_LAYOUT_NAME) else None
	)
	frappe.get_doc(
		{"doctype": "YRP UI Preference", "user": VERIFY_USER, "layout": layout}
	).insert(ignore_permissions=True)
	return True


def _write_creds_file(email, password):
	"""Two lines (email, password), owner-read/write only. The fd is opened
	0o600 so the secret never has a wider-mode window; the explicit chmod
	covers a pre-existing file that carried a looser mode."""
	path = os.path.expanduser(VERIFY_CREDS_FILE)
	fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
	with os.fdopen(fd, "w") as handle:
		handle.write(f"{email}\n{password}\n")
	os.chmod(path, 0o600)
	return path
