# Copyright (c) 2026, Essdee and contributors
# For license information, please see license.txt

"""Fleet tooling for the per-user /web UI — bulk layout assignment + the
dedicated verification users ("custom ui/BASE_FINALIZATION_PLAN.md" §5 item 7;
floor user per USE_CASE review amendment 1).

Three affordances, all SM-territory, none touching any real user's personal
layer (the LIVE-site rule: the owner switches his own YRP UI Preference at
will — tooling must never write records it wasn't explicitly pointed at):

- ``assign_layout`` — whitelisted, POST-only, SM-only: point N existing
  enabled users' ``YRP UI Preference.layout`` at ONE enabled layout in a
  single call (kills the per-user Desk clicking). Each user's ``overrides``
  and ``notes`` stay untouched — ONLY the ``layout`` field is written.
- ``seed_verify_user`` — NOT whitelisted (bench execute only): idempotent
  seeder for the dedicated SM verification user, so layout verification never
  has to log in as (or mutate) a real person.
- ``seed_floor_verify_user`` — NOT whitelisted (bench execute only):
  idempotent seeder for a PERMISSION-RESTRICTED verification user (read-only
  floor role over the 9 catalog doctypes, never System Manager), so a layout
  can be verified AS a restricted assignee — the emptier reality an SM
  verification is structurally blind to.

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
from frappe.permissions import add_permission
from frappe.rate_limiter import rate_limit
from frappe.utils.password import update_password

from yrp.yrp.api.ui_config import DEFAULT_LAYOUT_NAME, _web_doctype_catalog

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

# The dedicated PERMISSION-RESTRICTED verification user (USE_CASE review
# amendment 1, 2026-07-17). The System-Manager ``VERIFY_USER`` above is blind
# to the assignee's emptier reality — "arrangement never grants capability",
# so SM verification cannot show what a restricted user will NOT see. This
# second user holds ONLY a read-only floor role over the /web catalog
# doctypes (never System Manager), so verify-ui can shoot a layout AS a
# floor worker and reveal every canRead-gated surface an SM would still see.
FLOOR_VERIFY_USER = "ui-floor-verify@essdee.fit"
# Its credentials land ONLY here (separate from VERIFY_CREDS_FILE; two lines,
# chmod 600). Same hard rule: never in a repo file, return value, or log.
FLOOR_VERIFY_CREDS_FILE = "~/.frappe-ui-floor-creds"
# The dedicated read-only role granted to FLOOR_VERIFY_USER. A bespoke role
# is required because no shipped non-SM role grants read over all catalog
# doctypes (e.g. Item Production Detail grants read to System Manager only).
FLOOR_ROLE = "YRP Floor Verify"


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
	  role covers that set today. For a PERMISSION-RESTRICTED render (what a
	  real floor worker actually sees), use ``seed_floor_verify_user`` below,
	  which seeds a read-only, non-SM floor user over exactly those 9.
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

	preference_created = _ensure_preference(VERIFY_USER)

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


def _ensure_preference(user):
	"""Create ``user``'s YRP UI Preference if missing (layout Default, guarded
	on the record existing so a fixture-less site still seeds); an existing
	preference is kept verbatim. Returns whether it was created. Shared by
	both verification-user seeders."""
	if frappe.db.exists("YRP UI Preference", user):
		return False
	layout = (
		DEFAULT_LAYOUT_NAME if frappe.db.exists("UI Layout", DEFAULT_LAYOUT_NAME) else None
	)
	frappe.get_doc(
		{"doctype": "YRP UI Preference", "user": user, "layout": layout}
	).insert(ignore_permissions=True)
	return True


def _write_creds_file(email, password, creds_file=VERIFY_CREDS_FILE):
	"""Two lines (email, password), owner-read/write only, to ``creds_file``
	(defaults to the SM verify user's file; the floor seeder passes its own).
	The fd is opened 0o600 so the secret never has a wider-mode window; the
	explicit chmod covers a pre-existing file that carried a looser mode."""
	path = os.path.expanduser(creds_file)
	fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
	with os.fdopen(fd, "w") as handle:
		handle.write(f"{email}\n{password}\n")
	os.chmod(path, 0o600)
	return path


# ── floor-role verify user (NOT whitelisted — bench execute only) ───────────


def seed_floor_verify_user():
	"""Create/refresh the PERMISSION-RESTRICTED layout-verification user (USE_CASE
	review amendment 1). NOT whitelisted — run it from the bench:

	    bench --site <site> execute yrp.yrp.api.ui_fleet.seed_floor_verify_user

	(``bench execute`` runs as Administrator and commits on success.)

	Why it exists: ``seed_verify_user`` above is a System Manager, so its /web
	render sees every catalog surface. A layout assigned to a real floor worker
	renders an EMPTIER reality — every nav item / list / home card is canRead-
	gated (``_apply_accurate_web_perms`` re-runs the authoritative
	``has_permission`` for exactly the catalog doctypes), and User Permissions /
	permlevel / if_owner further trim it. SM verification is structurally blind
	to that gap ("arrangement never grants capability"). This user reproduces
	it: a plain System User that holds ONLY a read-only floor role and NEVER
	System Manager, so verify-ui can shoot a layout AS a restricted assignee.

	What it guarantees, idempotently (re-runs refresh the password + file):

	- Role ``YRP Floor Verify`` exists, granting **read only** (no write /
	  create / delete / submit) at permlevel 0 over every /web catalog doctype
	  (the ``yrp_web_doctype_catalog`` hook — Lot, Work Order, Work Order
	  Correction, Delivery Challan, Goods Received Note, Stock Entry, Item, Item
	  Production Detail, Terms and Condition). The grant uses Frappe's own
	  ``add_permission`` (the Role Permission Manager mechanism) — SIDE EFFECT:
	  as with any Role-Permission-Manager edit, a doctype that had no Custom
	  DocPerm yet is converted to custom-perm management (its shipped DocPerms
	  are copied first, so no existing role loses access). Reversible per
	  doctype via ``frappe.permissions.reset_perms``.
	- ``User`` ``ui-floor-verify@essdee.fit`` exists, enabled, System User,
	  named "UI Floor Verify", holding ``YRP Floor Verify`` and — enforced on
	  every re-run — **never** System Manager (which would defeat the purpose).
	  Being non-SM, ``www_home`` routes it to /web like a real worker.
	- A fresh random password, written ONLY to ``~/.frappe-ui-floor-creds``
	  (two lines: email, password; chmod 600) — separate from the SM verify
	  user's file. Never into a repo file, never returned, never logged.
	- A ``YRP UI Preference`` exists for it (layout Default on creation); an
	  existing preference is kept verbatim (a layout under verification
	  survives a password refresh).
	"""
	frappe.only_for("System Manager")

	role_created = _ensure_floor_role()
	user_created = _ensure_floor_user()

	password = frappe.generate_hash(length=32)
	update_password(FLOOR_VERIFY_USER, password, logout_all_sessions=True)
	creds_file = _write_creds_file(FLOOR_VERIFY_USER, password, FLOOR_VERIFY_CREDS_FILE)

	preference_created = _ensure_preference(FLOOR_VERIFY_USER)

	# NEVER include the password here — bench execute prints the return value.
	return {
		"user": FLOOR_VERIFY_USER,
		"role": FLOOR_ROLE,
		"role_created": role_created,
		"user_created": user_created,
		"catalog_doctypes": _floor_catalog_doctypes(),
		"preference_created": preference_created,
		"creds_file": creds_file,
	}


def _floor_catalog_doctypes():
	"""The /web catalog doctypes the floor role must be able to read, from the
	same ``yrp_web_doctype_catalog`` hook ``ui_config`` validates against —
	sorted for deterministic grant order. Empty on a bare-yrp site with no
	catalog declared (the floor user is then read-restricted to nothing extra,
	which is safe: it simply can render no catalog list)."""
	catalog = _web_doctype_catalog()
	return sorted(catalog) if catalog else []


def _ensure_floor_role():
	"""Create the ``YRP Floor Verify`` role if missing and grant it read-only
	over every catalog doctype. Idempotent. Returns whether the role was
	created (the grants are re-checked either way)."""
	created = not frappe.db.exists("Role", FLOOR_ROLE)
	if created:
		frappe.get_doc(
			{
				"doctype": "Role",
				"role_name": FLOOR_ROLE,
				# Desk access on so the floor user traverses the same
				# /api/resource path as the SM verify user (apples-to-apples
				# shots); the restriction under test is doctype scope, not
				# desk-vs-website. It is still non-SM, so /web is its home.
				"desk_access": 1,
				"disabled": 0,
			}
		).insert(ignore_permissions=True)
	_grant_floor_reads()
	return created


def _grant_floor_reads():
	"""Grant the floor role read (permlevel 0) over each catalog doctype that
	exists, exactly once. ``add_permission`` defaults ptype to ``read`` and
	sets nothing else, so the floor role is strictly read-only; the pre-check
	keeps the re-run silent (no duplicate-rule msgprint)."""
	for doctype in _floor_catalog_doctypes():
		if not frappe.db.exists("DocType", doctype):
			continue
		existing = frappe.db.get_value(
			"Custom DocPerm",
			{"parent": doctype, "role": FLOOR_ROLE, "permlevel": 0, "if_owner": 0},
		)
		if not existing:
			add_permission(doctype, FLOOR_ROLE, permlevel=0)


def _ensure_floor_user():
	"""Create the floor verify user (System User, enabled, holding ONLY the
	floor role) if missing, else repair it back to the guaranteed state.
	Returns whether it was created."""
	created = not frappe.db.exists("User", FLOOR_VERIFY_USER)
	if created:
		frappe.get_doc(
			{
				"doctype": "User",
				"email": FLOOR_VERIFY_USER,
				"first_name": "UI",
				"last_name": "Floor Verify",
				"user_type": "System User",
				"enabled": 1,
				"send_welcome_email": 0,
				"roles": [{"role": FLOOR_ROLE}],
			}
		).insert(ignore_permissions=True)
	else:
		_repair_floor_user()
	return created


def _repair_floor_user():
	"""Idempotent re-run path: put an existing floor user back into the
	guaranteed state — enabled System User, holding the floor role and,
	critically, NEVER System Manager (a stray SM grant would mask exactly the
	restricted reality this user exists to expose)."""
	doc = frappe.get_doc("User", FLOOR_VERIFY_USER)
	dirty = False
	if not doc.enabled:
		doc.enabled = 1
		dirty = True
	if doc.user_type != "System User":
		doc.user_type = "System User"
		dirty = True
	roles = {row.role for row in doc.roles}
	if "System Manager" in roles:
		doc.set("roles", [row for row in doc.roles if row.role != "System Manager"])
		roles.discard("System Manager")
		dirty = True
	if FLOOR_ROLE not in roles:
		doc.append("roles", {"role": FLOOR_ROLE})
		dirty = True
	if dirty:
		doc.save(ignore_permissions=True)
