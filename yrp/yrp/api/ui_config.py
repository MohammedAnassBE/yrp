"""Per-user UI config — constants, validation, merge/resolver, endpoints.

Spec: "custom ui/PER_USER_UI_SPEC.md" §2 (schema + upgraders), §3 (DocTypes),
§3.3 (User lifecycle), §4 (API contracts), §5 (merge), §14 (failure modes).

Layout (top-to-bottom readable, per §4): constants → validation →
pure merge/skeleton/upgrade machinery → resolver → User doc-event handlers →
whitelisted endpoints + boot hook. Colocated tests: ``test_ui_config.py``.

No caching in v1 — deliberately (§4.4): resolution is two indexed point-reads
plus pure dict merging, once per full /web page load. Propagation rule:
SM saves → user's next page load shows it.
"""

import json
import re
from copy import deepcopy

import frappe
from frappe import _
from frappe.rate_limiter import rate_limit

CURRENT_SCHEMA_VERSION = 1

# The only top-level keys the personal overrides layer may carry (§2.2).
OVERRIDABLE_KEYS = ("nav", "screens", "listViews", "quickCreate", "theme")

DEFAULT_LAYOUT_NAME = "Default"

# Incident lever (§4.1, §14 row 17): `bench --site <site> set-config
# yrp_disable_ui_config 1` → the resolver skips all records and serves the
# skeleton (client falls back to the compiled default) on next page load.
# No build, no deploy. Unset to restore.
KILL_SWITCH_KEY = "yrp_disable_ui_config"  # site_config.json flag

# Schema upgraders (§2.3 rule 3). A breaking shape change bumps
# CURRENT_SCHEMA_VERSION and adds ONE pure function here: ``UPGRADERS[N]``
# takes a version-N blob and returns its version-N+1 shape, e.g.
#   UPGRADERS = {1: _upgrade_v1_to_v2}
# Upgraders MUST be key-local (touch only keys present in the blob) because
# they run on sparse overrides deltas as well as complete layout documents.
# The resolver machinery stamps the new ``schema_version`` after each step —
# upgraders never write it. Layers are upgraded in memory at read time;
# stored records are rewritten only when an SM next saves them.
UPGRADERS = {}

# Layout-tier-ONLY keys the engine consumes today (2026-07-15 Demo-7 shell):
# `chrome` (AppLayout/ChromeBar strip), `realtime` (ChromeBar Live indicator),
# `dateFormat` (format.js/HomeRecent). NOT in OVERRIDABLE_KEYS — the personal
# overrides layer still warns on + filters them.
LAYOUT_ONLY_KEYS = ("chrome", "realtime", "dateFormat")

# Structural knobs (2026-07-16, spec §6.4): how detail renders, how entry
# opens, the Delivery Challan entry variant, and action placement/filtering.
# Layout tier ONLY in this iteration — deliberately NOT added to
# OVERRIDABLE_KEYS (the personal overrides layer warns on + filters them,
# same as the shell knobs above). All four are optional and additive per
# §2.3 — no schema/engine version bump.
STRUCTURAL_KEYS = ("detail", "entry", "dcEntry", "actions")

# Complete top-level vocabulary of a layout config (§2.1 / skeleton keys).
LAYOUT_KEYS = ("schema_version", *LAYOUT_ONLY_KEYS, *STRUCTURAL_KEYS, *OVERRIDABLE_KEYS)

# Soft-checked vocabularies for the shell knobs (engine consumption mirrored:
# AppLayout.vue topbarNav, format.js formatDate, ChromeBar.vue chrome knobs).
NAV_POSITIONS = ("sidebar", "topbar")
DATE_FORMATS = ("dd-mm-yyyy", "yyyy-mm-dd")
CHROME_KEYS = ("search", "themeToggle")
REALTIME_KEYS = ("enabled", "intervalMs", "toast")

# Soft-checked vocabularies for the structural knobs. An off-vocabulary value
# never blocks a save — the client warns/ignores it and keeps today's
# behaviour (PARITY: every knob's absence/default = the current UI).
DETAIL_KEYS = ("position",)
DETAIL_POSITIONS = ("page", "right", "center", "bottom-sheet")
ENTRY_KEYS = ("mode", "popupPosition")
ENTRY_MODES = ("page", "popup")
# 9-position overlay anchor grid, shared by entry.popupPosition and the
# reserved actions.dialogPosition (PrimeVue Dialog/Drawer position vocabulary).
OVERLAY_POSITIONS = (
	"top-left",
	"top",
	"top-right",
	"left",
	"center",
	"right",
	"bottom-left",
	"bottom",
	"bottom-right",
)
DC_ENTRY_KEYS = ("variant", "qtyControl", "supplierPicker")
DC_ENTRY_VARIANTS = ("form-grid", "size-matrix", "inline-grid")
DC_ENTRY_QTY_CONTROLS = ("input",)
DC_ENTRY_SUPPLIER_PICKERS = ("select", "chips")
# `dialogPosition` is a RESERVED knob name (same status as realtime.intervalMs/
# toast): validated against OVERLAY_POSITIONS so layouts can already carry it,
# but NO client consumes it yet — all action dialogs open center today. Wire a
# consumer (the actions dialog/drawer anchor) before documenting it as live.
ACTIONS_KEYS = ("placement", "dialogPosition", "items")
ACTIONS_PLACEMENTS = ("header", "inline", "floating")
# actions.items is a FILTER over the EXISTING header affordances only (§15:
# arrangement never grants capability — every listed item still renders
# through the client's canRead/canCreate/canSubmit/canCancel gates, and an
# unknown name is ignored client-side, so it soft-warns here).
ACTION_ITEMS = (
	"create_grn",
	"create_dc",
	"more_menu",
	"ewaybill_menu",
	"send_sms",
	"send_whatsapp",
	"cancel_doc",
)

# Theme token vocabulary — exactly what the engine renders today (frontend
# theme/applyTheme.js tokenVars). mode/accent keep their HARD rules in
# _validate_theme; every other token gets SOFT validation mirroring the
# engine's warn-and-ignore guards (an off-form value never blocks a save —
# the client drops it and keeps the shipped fallback).
THEME_KEYS = (
	"mode",  # hard: THEME_MODES
	"accent",  # hard: ACCENT_RE
	"bg",
	"surface",
	"text",
	"muted",
	"line",
	"surface2",
	"radius",
	"density",
	"fontScale",
	"font",
	"dark",  # overlay palette for the .dark scheme: {...theme, ...theme.dark}
)
THEME_MODES = ("user", "light", "dark")

# Soft-checked token groups (engine applyTheme.js guards, mirrored).
THEME_COLOR_KEYS = ("bg", "surface", "text", "muted", "line", "surface2")
THEME_DENSITIES = ("compact", "comfortable", "spacious")

# Security-labeled formats (spec §15) — checked with re.fullmatch, never
# re.match + '$' (Python '$' also matches before a trailing newline, so
# "#2563EB\n" would slip through a match()-based check).
ACCENT_RE = re.compile(r"#[0-9a-fA-F]{6}")
ICON_RE = re.compile(r"pi pi-[a-z0-9-]+")
# muted/line/surface2 may carry an rgba() wash — same conservative form the
# engine accepts (applyTheme.js RGBA_RE; values land inside a client <style>).
THEME_RGBA_RE = re.compile(r"rgba?\(\s*\d{1,3}\s*,\s*\d{1,3}\s*,\s*\d{1,3}\s*(?:,\s*(?:0|1|0?\.\d{1,4})\s*)?\)")
# Font stacks: ASCII letters/digits/underscore, spaces, commas, quotes, hyphens.
# Deliberately NOT Python \w — JS \w is ASCII-only, so a Unicode-lettered value
# ("Ariál") passes a \w-based server check yet gets dropped by the client's
# FONT_RE at render. Keep the server at least as strict so the SM hears at save.
THEME_FONT_RE = re.compile(r"[A-Za-z0-9_\s,'\"-]{1,200}")

# Save-time soft-warning threshold for a serialized layer (§3.1: the resolved
# payload rides in boot on every /web page load; §13 budget).
CONFIG_SIZE_WARN_BYTES = 32 * 1024

# Hard ceiling for the SELF-SERVICE save endpoint only (save_my_ui_overrides):
# any authenticated user can hit it, so an unbounded payload is a cheap
# storage/boot-bloat DoS. 8× the soft-warn budget — nothing legitimate gets
# near it. The SM Desk path keeps the soft warning only (SMs are trusted).
MAX_OVERRIDES_BYTES = 256 * 1024
# Save endpoint rate limit (frappe.rate_limiter): per-IP, sized to the Knobs
# panel's serialized queueSave (a human clicking knobs stays far under it).
SAVE_RATE_LIMIT = 30
SAVE_RATE_WINDOW_SECONDS = 60


def validate_config(config, layer):
	"""Validate one config layer at save time.

	``layer`` is ``"layout"`` (``UI Layout.config``, a complete document) or
	``"overrides"`` (``YRP UI Preference.overrides``, a sparse delta).

	Hard errors (spec §3.1) raise ``frappe.ValidationError`` and block the
	save. Soft issues are returned as a list of warning strings for the
	controller to ``msgprint``.
	"""
	warnings = []

	if config is None or (isinstance(config, str) and not config.strip()):
		if layer == "layout":
			_hard(layer, _("config is required and must be a JSON object"))
		return warnings

	if isinstance(config, str):
		serialized = config
		try:
			cfg = json.loads(config)
		except ValueError:
			_hard(layer, _("config is not valid JSON"))
	elif isinstance(config, dict):
		cfg = config
		# ensure_ascii=False so dict input is measured in real UTF-8 bytes,
		# consistent with raw-string input (ensure_ascii=True would inflate
		# non-ASCII to \uXXXX escapes and skew the 32 KB warning).
		serialized = json.dumps(config, default=str, ensure_ascii=False)
	else:
		_hard(layer, _("config must be a JSON object"))

	if not isinstance(cfg, dict):
		_hard(layer, _("config must be a JSON object, not {0}").format(type(cfg).__name__))

	if not cfg:
		# An empty overrides delta is a harmless no-op; a layout must be a
		# complete document (it will fail the schema_version check below).
		if layer == "overrides":
			return warnings

	_validate_schema_version(cfg, layer)
	_warn_unknown_top_level_keys(cfg, layer, warnings)
	_validate_nav(cfg.get("nav"), layer, warnings)
	_validate_screens(cfg.get("screens"), layer, warnings)
	_validate_list_views(cfg.get("listViews"), layer)
	_validate_quick_create(cfg.get("quickCreate"), layer, warnings)
	_validate_theme(cfg.get("theme"), layer, warnings)
	if layer == "layout":
		# Layout-tier-only shell knobs; in an overrides delta these keys already
		# got the unknown-key warning above — no second shape warning.
		_validate_chrome(cfg.get("chrome"), layer, warnings)
		_validate_realtime(cfg.get("realtime"), layer, warnings)
		_validate_date_format(cfg.get("dateFormat"), layer, warnings)
		# Structural knobs (layout-tier only, same rule as the shell knobs).
		_validate_detail(cfg.get("detail"), layer, warnings)
		_validate_entry(cfg.get("entry"), layer, warnings)
		_validate_dc_entry(cfg.get("dcEntry"), layer, warnings)
		_validate_actions(cfg.get("actions"), layer, warnings)

	if len(serialized.encode("utf-8")) > CONFIG_SIZE_WARN_BYTES:
		warnings.append(
			_("{0}: serialized config exceeds 32 KB — it rides in boot on every /web page load; consider trimming").format(layer)
		)

	return warnings


def _hard(layer, message):
	frappe.throw(
		_("UI config ({0}): {1}").format(layer, message),
		frappe.ValidationError,
		title=_("Invalid UI Config"),
	)


def _validate_schema_version(cfg, layer):
	version = cfg.get("schema_version")
	if isinstance(version, bool) or not isinstance(version, int) or version < 1:
		_hard(layer, _("schema_version must be a positive integer (current: {0})").format(CURRENT_SCHEMA_VERSION))
	if version > CURRENT_SCHEMA_VERSION:
		_hard(
			layer,
			_("schema_version {0} is newer than this server understands ({1})").format(
				version, CURRENT_SCHEMA_VERSION
			),
		)


def _warn_unknown_top_level_keys(cfg, layer, warnings):
	if layer == "overrides":
		for key in cfg:
			if key != "schema_version" and key not in OVERRIDABLE_KEYS:
				warnings.append(
					_("{0}: unknown key '{1}' ignored (not in OVERRIDABLE_KEYS)").format(layer, key)
				)
	else:
		for key in cfg:
			if key not in LAYOUT_KEYS:
				warnings.append(_("{0}: unknown top-level key '{1}'").format(layer, key))


def _validate_nav(nav, layer, warnings):
	if nav is None:
		return
	if not isinstance(nav, dict):
		_hard(layer, _("nav must be an object"))

	# Soft: the engine treats anything other than "topbar" as the sidebar shell
	# (AppLayout.vue topbarNav === strict compare), so an off-vocabulary value
	# silently renders the sidebar — warn the author instead of ignoring.
	position = nav.get("position")
	if position is not None and position not in NAV_POSITIONS:
		warnings.append(
			_("{0}: nav.position {1!r} is not one of {2} — the client renders the sidebar shell").format(
				layer, position, ", ".join(NAV_POSITIONS)
			)
		)

	hidden = nav.get("hidden")
	if hidden is not None and not isinstance(hidden, dict):
		_hard(layer, _("nav.hidden must be an object of booleans"))
	_warn_non_boolean_hidden(hidden, "nav.hidden", layer, warnings)

	groups = nav.get("groups")
	if groups is None:
		return
	if not isinstance(groups, list):
		_hard(layer, _("nav.groups must be a list"))

	for group in groups:
		if not isinstance(group, dict):
			_hard(layer, _("every nav group must be an object"))
		group_id = group.get("id")
		if not isinstance(group_id, str) or not group_id.strip():
			# Soft: the client keys sidebar-collapse persistence on group id;
			# a missing id only degrades that, it doesn't break rendering.
			warnings.append(
				_("{0}: nav group {1!r} has no string 'id' — sidebar collapse state will not persist for it").format(
					layer, group.get("label") or group_id
				)
			)
		items = group.get("items")
		if items is None:
			continue
		if not isinstance(items, list):
			_hard(layer, _("nav group items must be a list"))
		for item in items:
			if not isinstance(item, dict):
				_hard(layer, _("every nav item must be an object"))
			doctype = item.get("doctype")
			if not isinstance(doctype, str) or not doctype.strip():
				_hard(layer, _("every nav item must carry a non-empty string 'doctype'"))
			icon = item.get("icon")
			if icon is not None and (not isinstance(icon, str) or not ICON_RE.fullmatch(icon)):
				_hard(
					layer,
					_("nav item icon '{0}' must match '^pi pi-[a-z0-9-]+$'").format(icon),
				)
			# Soft: the client catalog is the real gate; a typo just drops the item.
			if not frappe.db.exists("DocType", doctype):
				warnings.append(
					_("{0}: nav doctype '{1}' does not exist as a DocType").format(layer, doctype)
				)


def _warn_non_boolean_hidden(hidden, path, layer, warnings):
	"""Soft warning (never a hard error) for non-boolean values in a hidden
	dict — the renderer checks ``=== true`` strictly (store ``navGroups`` and
	``ScreenRenderer.visibleBlocks``), so a truthy non-boolean like ``1`` or
	``"yes"`` silently fails to hide anything. Off-vocabulary (§2.1: visibility
	is a dict of booleans), hence the warning."""
	if not isinstance(hidden, dict):
		return
	for key, value in hidden.items():
		if not isinstance(value, bool):
			warnings.append(
				_("{0}: {1}['{2}'] should be a boolean, got {3}").format(
					layer, path, key, type(value).__name__
				)
			)


def _validate_screens(screens, layer, warnings):
	if screens is None:
		return
	if not isinstance(screens, dict):
		_hard(layer, _("screens must be an object"))

	home = screens.get("home")
	if home is None:
		# Unknown/reserved screen keys (list:<DocType>, detail:<DocType>) are
		# ignored by the renderer — no validation, no warning (§2.1).
		return
	if not isinstance(home, dict):
		_hard(layer, _("screens.home must be an object"))

	hidden = home.get("hidden")
	if hidden is not None and not isinstance(hidden, dict):
		_hard(layer, _("screens.home.hidden must be an object of booleans"))
	_warn_non_boolean_hidden(hidden, "screens.home.hidden", layer, warnings)

	blocks = home.get("blocks")
	if blocks is None:
		return
	if not isinstance(blocks, list):
		_hard(layer, _("screens.home.blocks must be a list"))

	for block in blocks:
		if not isinstance(block, dict):
			_hard(layer, _("every home block must be an object"))
		for key in ("id", "type"):
			value = block.get(key)
			if not isinstance(value, str) or not value.strip():
				_hard(layer, _("every home block must carry a non-empty string '{0}'").format(key))
		_check_block_props(block, layer, warnings)


def _known_metric_keys():
	"""Metric names the ``ui_metrics`` registry serves, for the summary-tiles
	soft check. Lazily imported so a defect in that module can NEVER hard-fail
	config validation — ``None`` means "unknown, skip the registry check"."""
	try:
		from yrp.yrp.api.ui_metrics import METRICS
	except Exception:
		return None
	return set(METRICS)


def _check_block_props(block, layer, warnings):
	"""Per-type prop schemas for the shipped block types (§15 item 3c).

	Failures are soft warnings; unknown block types skip prop validation
	entirely (the client bundle may be newer than this server).
	"""
	block_type = block["type"]
	props = block.get("props")
	if props is not None and not isinstance(props, dict):
		warnings.append(
			_("{0}: block '{1}' props must be an object").format(layer, block["id"])
		)
		return
	if props is None:
		# Absent props stay legal for every optional-prop block, but the
		# REQUIRED prop checks below (record-list doctype, calculator-panel
		# calculation) must still run — continue over an empty dict.
		props = {}

	if block_type == "home-queues":
		max_cards = props.get("maxCards")
		if max_cards is not None and (
			isinstance(max_cards, bool) or not isinstance(max_cards, int) or not (1 <= max_cards <= 10)
		):
			warnings.append(
				_("{0}: block '{1}' maxCards must be an integer between 1 and 10").format(
					layer, block["id"]
				)
			)
	elif block_type in ("home-recent", "home-quick-create"):
		doctypes = props.get("doctypes")
		if doctypes is not None and (
			not isinstance(doctypes, list) or not all(isinstance(d, str) for d in doctypes)
		):
			warnings.append(
				_("{0}: block '{1}' doctypes must be a list of strings").format(layer, block["id"])
			)
		if block_type == "home-recent":
			recent_style = props.get("recentStyle")
			if recent_style is not None and recent_style not in ("table", "tiles"):
				warnings.append(
					_("{0}: block '{1}' recentStyle must be 'table' or 'tiles'").format(
						layer, block["id"]
					)
				)
	elif block_type == "home-greeting":
		for key in ("greetingName", "sub"):
			value = props.get(key)
			if value is not None and not isinstance(value, str):
				warnings.append(
					_("{0}: block '{1}' {2} must be a string").format(layer, block["id"], key)
				)
		new_cta = props.get("newCta")
		if new_cta is not None and not isinstance(new_cta, dict):
			warnings.append(
				_("{0}: block '{1}' newCta must be an object").format(layer, block["id"])
			)
	elif block_type == "summary-tiles":
		metrics = props.get("metrics")
		if metrics is not None and (
			not isinstance(metrics, list) or not all(isinstance(m, str) for m in metrics)
		):
			warnings.append(
				_("{0}: block '{1}' metrics must be a list of strings").format(layer, block["id"])
			)
		elif metrics:
			known = _known_metric_keys()
			for name in metrics:
				if known is not None and name not in known:
					warnings.append(
						_("{0}: block '{1}' metric '{2}' is not a registered metric").format(
							layer, block["id"], name
						)
					)
	elif block_type == "record-list":
		doctype = props.get("doctype")
		if not isinstance(doctype, str) or not doctype.strip():
			warnings.append(
				_("{0}: block '{1}' requires a non-empty string 'doctype'").format(
					layer, block["id"]
				)
			)
		variant = props.get("variant")
		if variant is not None and variant not in ("table", "cards", "kanban"):
			warnings.append(
				_("{0}: block '{1}' variant must be 'table', 'cards' or 'kanban'").format(
					layer, block["id"]
				)
			)
		columns = props.get("columns")
		if columns is not None and (
			not isinstance(columns, list) or not all(isinstance(c, str) for c in columns)
		):
			warnings.append(
				_("{0}: block '{1}' columns must be a list of strings").format(layer, block["id"])
			)
		page_size = props.get("pageSize")
		if page_size is not None and (
			isinstance(page_size, bool) or not isinstance(page_size, int) or not (1 <= page_size <= 50)
		):
			warnings.append(
				_("{0}: block '{1}' pageSize must be an integer between 1 and 50").format(
					layer, block["id"]
				)
			)
		for key in ("groupBy", "titleField", "title"):
			value = props.get(key)
			if value is not None and not isinstance(value, str):
				warnings.append(
					_("{0}: block '{1}' {2} must be a string").format(layer, block["id"], key)
				)
	elif block_type == "calculator-panel":
		calculation = props.get("calculation")
		if not isinstance(calculation, str) or not calculation.strip():
			warnings.append(
				_("{0}: block '{1}' requires a non-empty string 'calculation'").format(
					layer, block["id"]
				)
			)
		params = props.get("params")
		if params is not None and not isinstance(params, dict):
			warnings.append(
				_("{0}: block '{1}' params must be an object").format(layer, block["id"])
			)


def _validate_list_views(list_views, layer):
	if list_views is not None and not isinstance(list_views, dict):
		_hard(layer, _("listViews must be an object keyed by DocType"))


def _validate_quick_create(quick_create, layer, warnings):
	if quick_create is None:
		return
	if not isinstance(quick_create, list):
		_hard(layer, _("quickCreate must be a list of DocType names"))
	for entry in quick_create:
		if not isinstance(entry, str):
			warnings.append(_("{0}: quickCreate entry {1!r} is not a string").format(layer, entry))
		# Soft, same rule as nav items: the client catalog is the real gate; a
		# typo just drops the entry — but the SM should hear about it at save.
		elif not frappe.db.exists("DocType", entry):
			warnings.append(
				_("{0}: quickCreate doctype '{1}' does not exist as a DocType").format(layer, entry)
			)


def _validate_chrome(chrome, layer, warnings):
	"""Soft shape checks for the `chrome` shell knob (ChromeBar.vue).

	The client mounts ChromeBar only when chrome is a plain object
	(AppLayout.vue hasChrome) and reads `search`/`themeToggle` with a strict
	`!== false` — everything here is warn-only, mirroring those guards."""
	if chrome is None:
		return
	if not isinstance(chrome, dict):
		warnings.append(
			_("{0}: chrome must be an object — the client ignores it and keeps the standard topbar").format(layer)
		)
		return
	for key, value in chrome.items():
		if key not in CHROME_KEYS:
			warnings.append(_("{0}: unknown key '{1}' inside chrome").format(layer, key))
		elif not isinstance(value, bool):
			warnings.append(
				_("{0}: chrome.{1} should be a boolean, got {2}").format(layer, key, type(value).__name__)
			)


def _validate_realtime(realtime, layer, warnings):
	"""Soft shape checks for the `realtime` knob (ChromeBar.vue Live indicator
	consumes `enabled` today; `intervalMs`/`toast` are reserved knob names)."""
	if realtime is None:
		return
	if not isinstance(realtime, dict):
		warnings.append(_("{0}: realtime must be an object — the client ignores it").format(layer))
		return
	for key, value in realtime.items():
		if key not in REALTIME_KEYS:
			warnings.append(_("{0}: unknown key '{1}' inside realtime").format(layer, key))
	enabled = realtime.get("enabled")
	if enabled is not None and not isinstance(enabled, bool):
		warnings.append(
			_("{0}: realtime.enabled should be a boolean, got {1}").format(layer, type(enabled).__name__)
		)
	interval = realtime.get("intervalMs")
	if interval is not None and (isinstance(interval, bool) or not isinstance(interval, (int, float))):
		warnings.append(
			_("{0}: realtime.intervalMs should be a number, got {1}").format(layer, type(interval).__name__)
		)
	toast = realtime.get("toast")
	if toast is not None and not isinstance(toast, bool):
		warnings.append(
			_("{0}: realtime.toast should be a boolean, got {1}").format(layer, type(toast).__name__)
		)


def _validate_date_format(date_format, layer, warnings):
	"""Soft: format.js treats anything other than 'yyyy-mm-dd' as the shipped
	dd-mm-yyyy default, so an off-vocabulary value silently does nothing."""
	if date_format is None:
		return
	if date_format not in DATE_FORMATS:
		warnings.append(
			_("{0}: dateFormat {1!r} is not one of {2} — the client falls back to dd-mm-yyyy").format(
				layer, date_format, ", ".join(DATE_FORMATS)
			)
		)


def _warn_off_vocabulary(layer, path, value, vocabulary, fallback, warnings):
	"""Shared soft warning for a structural-knob enum: an off-vocabulary value
	never blocks the save — the client ignores it and keeps ``fallback``
	(mirrors the nav.position / dateFormat house style)."""
	warnings.append(
		_("{0}: {1} {2!r} is not one of {3} — the client falls back to {4}").format(
			layer, path, value, ", ".join(vocabulary), fallback
		)
	)


def _validate_detail(detail, layer, warnings):
	"""Structural knob: where a document's detail view renders (DocDetail —
	page today; right drawer / center dialog / bottom sheet as opt-ins).
	Unknown position = soft (the client falls back to the page render)."""
	if detail is None:
		return
	if not isinstance(detail, dict):
		_hard(layer, _("detail must be an object"))

	position = detail.get("position")
	if position is not None and position not in DETAIL_POSITIONS:
		_warn_off_vocabulary(layer, "detail.position", position, DETAIL_POSITIONS, "page", warnings)

	for key in detail:
		if key not in DETAIL_KEYS:
			warnings.append(_("{0}: unknown key '{1}' inside detail").format(layer, key))


def _validate_entry(entry, layer, warnings):
	"""Structural knob: how document creation opens (full page today; popup as
	the opt-in). ``popupPosition`` anchors the popup on the 9-position overlay
	grid and is only meaningful with ``mode: "popup"`` — soft warnings all."""
	if entry is None:
		return
	if not isinstance(entry, dict):
		_hard(layer, _("entry must be an object"))

	mode = entry.get("mode")
	if mode is not None and mode not in ENTRY_MODES:
		_warn_off_vocabulary(layer, "entry.mode", mode, ENTRY_MODES, "page", warnings)

	popup_position = entry.get("popupPosition")
	if popup_position is not None:
		if popup_position not in OVERLAY_POSITIONS:
			_warn_off_vocabulary(
				layer, "entry.popupPosition", popup_position, OVERLAY_POSITIONS, "center", warnings
			)
		if mode != "popup":
			warnings.append(
				_("{0}: entry.popupPosition has no effect unless entry.mode is 'popup'").format(layer)
			)

	for key in entry:
		if key not in ENTRY_KEYS:
			warnings.append(_("{0}: unknown key '{1}' inside entry").format(layer, key))


def _validate_dc_entry(dc_entry, layer, warnings):
	"""Structural knob: the Delivery Challan entry presentation (WO → items →
	quantities → job-worker → save). Unknown values = soft (client ignores
	the knob and keeps today's form-grid entry)."""
	if dc_entry is None:
		return
	if not isinstance(dc_entry, dict):
		_hard(layer, _("dcEntry must be an object"))

	variant = dc_entry.get("variant")
	if variant is not None and variant not in DC_ENTRY_VARIANTS:
		_warn_off_vocabulary(
			layer, "dcEntry.variant", variant, DC_ENTRY_VARIANTS, "form-grid", warnings
		)

	qty_control = dc_entry.get("qtyControl")
	if qty_control is not None and qty_control not in DC_ENTRY_QTY_CONTROLS:
		_warn_off_vocabulary(
			layer, "dcEntry.qtyControl", qty_control, DC_ENTRY_QTY_CONTROLS, "input", warnings
		)

	supplier_picker = dc_entry.get("supplierPicker")
	if supplier_picker is not None and supplier_picker not in DC_ENTRY_SUPPLIER_PICKERS:
		_warn_off_vocabulary(
			layer,
			"dcEntry.supplierPicker",
			supplier_picker,
			DC_ENTRY_SUPPLIER_PICKERS,
			"select",
			warnings,
		)

	for key in dc_entry:
		if key not in DC_ENTRY_KEYS:
			warnings.append(_("{0}: unknown key '{1}' inside dcEntry").format(layer, key))


def _validate_actions(actions, layer, warnings):
	"""Structural knob: where document actions render and which of the EXISTING
	affordances show. ``items`` is a FILTER only (§15: arrangement never grants
	capability — every item still passes the client's permission gates; an
	unknown name is ignored client-side, so it soft-warns here).
	``dialogPosition`` is reserved: accepted + vocabulary-checked, not yet
	consumed by any client (see the ACTIONS_KEYS comment)."""
	if actions is None:
		return
	if not isinstance(actions, dict):
		_hard(layer, _("actions must be an object"))

	placement = actions.get("placement")
	if placement is not None and placement not in ACTIONS_PLACEMENTS:
		_warn_off_vocabulary(
			layer, "actions.placement", placement, ACTIONS_PLACEMENTS, "header", warnings
		)

	dialog_position = actions.get("dialogPosition")
	if dialog_position is not None and dialog_position not in OVERLAY_POSITIONS:
		_warn_off_vocabulary(
			layer, "actions.dialogPosition", dialog_position, OVERLAY_POSITIONS, "center", warnings
		)

	items = actions.get("items")
	if items is not None:
		if not isinstance(items, list):
			_hard(layer, _("actions.items must be a list of action names"))
		for item in items:
			if not isinstance(item, str):
				warnings.append(
					_("{0}: actions.items entry {1!r} is not a string").format(layer, item)
				)
			elif item not in ACTION_ITEMS:
				warnings.append(
					_(
						"{0}: actions.items entry '{1}' is not one of {2} — the client ignores it"
					).format(layer, item, ", ".join(ACTION_ITEMS))
				)

	for key in actions:
		if key not in ACTIONS_KEYS:
			warnings.append(_("{0}: unknown key '{1}' inside actions").format(layer, key))


def _validate_theme(theme, layer, warnings):
	if theme is None:
		return
	if not isinstance(theme, dict):
		_hard(layer, _("theme must be an object"))

	# Hard rules — UNCHANGED (spec §3.1): mode and the light accent block the save.
	mode = theme.get("mode")
	if mode is not None and mode not in THEME_MODES:
		_hard(layer, _("theme.mode must be one of {0}").format(", ".join(THEME_MODES)))

	accent = theme.get("accent")
	if accent is not None and (not isinstance(accent, str) or not ACCENT_RE.fullmatch(accent)):
		_hard(layer, _("theme.accent '{0}' must match '^#[0-9a-fA-F]{{6}}$'").format(accent))

	_soft_validate_theme_tokens(theme, "theme", layer, warnings)

	# dark overlay: the engine builds the effective dark theme as
	# {...theme, ...theme.dark} and silently ignores a non-object — soft here.
	dark = theme.get("dark")
	if dark is not None and not isinstance(dark, dict):
		warnings.append(_("{0}: theme.dark must be an object — the client ignores it").format(layer))
	elif isinstance(dark, dict):
		# Overlay accent has NO hard rule (mirror applyTheme normalizeAccent:
		# the client warns and keeps the shipped palette).
		dark_accent = dark.get("accent")
		if dark_accent is not None and (
			not isinstance(dark_accent, str) or not ACCENT_RE.fullmatch(dark_accent)
		):
			warnings.append(
				_("{0}: theme.dark.accent {1!r} is not '#rrggbb' — the client will keep the shipped palette").format(
					layer, dark_accent
				)
			)
		_soft_validate_theme_tokens(dark, "theme.dark", layer, warnings)
		for key in dark:
			# Overlay vocabulary = the theme's own; a nested dark is meaningless.
			if key == "dark" or key not in THEME_KEYS:
				warnings.append(_("{0}: unknown key '{1}' inside theme.dark").format(layer, key))

	for key in theme:
		if key not in THEME_KEYS:
			warnings.append(_("{0}: unknown key '{1}' inside theme").format(layer, key))

	_warn_light_only_palette(theme, mode, dark, layer, warnings)


def _warn_light_only_palette(theme, mode, dark, layer, warnings):
	"""Soft (IMPORTANT-2, 2026-07-15 review): custom light colors + reachable
	dark mode + no dark{} palette. The client deliberately does NOT carry light
	color tokens into the .dark scheme without an overlay (a light-only theme
	would render white cards + light text = illegible dark mode), so dark mode
	silently keeps the SHIPPED dark palette — almost certainly not what the
	author meant. mode "light" never reaches .dark → no warning."""
	if mode == "light":
		return
	anchors = [
		key
		for key in ("bg", "surface", "text")
		if isinstance(theme.get(key), str)
		and (ACCENT_RE.fullmatch(theme[key]) or THEME_RGBA_RE.fullmatch(theme[key]))
	]
	if not anchors:
		return
	dark_has_colors = isinstance(dark, dict) and any(
		dark.get(key) is not None for key in THEME_COLOR_KEYS
	)
	if not dark_has_colors:
		warnings.append(
			_(
				"{0}: theme sets light colors ({1}) but no theme.dark palette while dark mode is reachable (mode {2!r}) — dark mode will keep the shipped dark palette; add a dark{{...}} overlay or set mode to 'light'"
			).format(layer, ", ".join(anchors), mode or "user")
		)


def _soft_validate_theme_tokens(t, path, layer, warnings):
	"""Mirror the engine's applyTheme.js warn-and-ignore guards as save-time
	soft warnings, so the SM hears at save what the client would drop at render.
	Never a hard error: an off-form token costs the layout that token only
	(the client keeps the shipped fallback), not the save."""

	def warn(key, value, expected):
		warnings.append(
			_("{0}: {1}.{2} {3!r} is not {4} — the client will ignore it").format(
				layer, path, key, value, expected
			)
		)

	for key in THEME_COLOR_KEYS:
		value = t.get(key)
		if value is None:
			continue
		if not isinstance(value, str) or not (
			ACCENT_RE.fullmatch(value) or THEME_RGBA_RE.fullmatch(value)
		):
			warn(key, value, _("'#rrggbb' or 'rgba(r, g, b[, a])'"))

	radius = t.get("radius")
	if radius is not None:
		number = _theme_number(radius)
		if number is None or not (0 <= number <= 60):
			warn("radius", radius, _("a number between 0 and 60"))

	density = t.get("density")
	if density is not None and density not in THEME_DENSITIES:
		warn("density", density, _("one of {0}").format(", ".join(THEME_DENSITIES)))

	font_scale = t.get("fontScale")
	if font_scale is not None:
		number = _theme_number(font_scale)
		if number is None or not (0.5 <= number <= 2):
			warn("fontScale", font_scale, _("a number between 0.5 and 2"))

	font = t.get("font")
	if font is not None and (not isinstance(font, str) or not THEME_FONT_RE.fullmatch(font)):
		warn("font", font, _("a plain font stack (letters, spaces, commas, quotes)"))


def _theme_number(value):
	"""Numeric coercion matching the engine's Number(): int/float/numeric string
	→ float, anything else (incl. booleans — valid JS Numbers but never a sane
	radius/fontScale) → None."""
	if isinstance(value, bool):
		return None
	if isinstance(value, (int, float)):
		return float(value)
	if isinstance(value, str):
		try:
			return float(value.strip())
		except ValueError:
			return None
	return None


# ── Skeleton + merge — pure functions (§4, §5) ──────────────────────────────


def get_skeleton():
	"""App-agnostic structural floor (§4; locked decision 7: base yrp never
	says "Essdee"). Guarantees every key the renderer reads EXISTS. Returned
	fresh on every call so callers may mutate their copy freely."""
	return {
		"schema_version": CURRENT_SCHEMA_VERSION,
		"nav": {"groups": [], "hidden": {}},
		"screens": {"home": {"blocks": [], "hidden": {}}},
		"listViews": {},
		"quickCreate": [],
		"theme": {"mode": "user", "accent": None},
	}


def merge(base, delta, whitelist=None):
	"""The normative §5 merge — pure. Three rules a junior can recite:

	1. Dicts merge key-by-key, recursively; a key present in the upper layer
	   wins. (This one rule also powers visibility: ``hidden`` maps are dicts
	   of booleans, so hides compose across layers and an upper layer can
	   re-show with ``false``.)
	2. Everything else — arrays, strings, numbers, booleans — replaces
	   wholesale. Arrays are never element-merged.
	3. ``null`` values are skipped ("no opinion" — the key falls through to
	   the layer below); with a ``whitelist``, unknown top-level keys are
	   dropped (bounded personal layer — the caller logs them into
	   ``meta.warnings``).

	Left fold is normative: ``merge(merge(skeleton, layout), overrides,
	OVERRIDABLE_KEYS)``. No associativity claim.
	"""
	out = deepcopy(base)
	for key, val in (delta or {}).items():
		if whitelist is not None and key not in whitelist:
			continue  # bounded personal layer
		if val is None:
			continue  # null = no opinion
		if isinstance(val, dict) and isinstance(out.get(key), dict):
			out[key] = merge(out[key], val)
		else:
			out[key] = deepcopy(val)  # arrays & scalars replace wholesale
	return out


# ── Resolver (§4, §14) — every degradation leaves a trace ───────────────────


def _log_degradation(title, message=None):
	"""Error Log write that can itself never break resolution (§4 never-raises)."""
	try:
		frappe.log_error(title=str(title)[:140], message=message)
	except Exception:
		pass


def _drop(label, reason, warnings, sample=None):
	"""Record a dropped layer: ``meta.warnings`` entry + Error Log trace (§14)."""
	message = _("{0} dropped: {1}").format(label, reason)
	warnings.append(message)
	_log_degradation(_("UI config: {0}").format(message), sample)


def _prepare_layer(raw, label, warnings, required=False):
	"""Parse + version-gate one stored config layer for the resolver.

	Returns the (possibly upgraded) dict, or ``None`` when there is no layer
	or the layer must be dropped. Every DROP appends to ``warnings`` and
	writes an Error Log entry. An absent/empty layer is a normal state (§14
	rows 1–3) and stays silent — unless ``required`` (the layout layer, whose
	``config`` field is mandatory), where emptiness is itself a degradation.
	"""
	if raw is None or (isinstance(raw, str) and not raw.strip()):
		if required:
			_drop(label, _("config is empty"), warnings)
		return None

	if isinstance(raw, str):
		try:
			cfg = json.loads(raw)
		except ValueError:
			_drop(label, _("invalid JSON"), warnings, sample=raw)
			return None
	else:
		cfg = raw

	if not isinstance(cfg, dict):
		_drop(label, _("config must be a JSON object, not {0}").format(type(cfg).__name__), warnings)
		return None

	if not cfg:
		# Empty overrides delta = harmless no-op; empty layout = degradation.
		if required:
			_drop(label, _("config is empty"), warnings)
		return None

	version = cfg.get("schema_version")
	if version is None:
		# §2.3 rule 5: missing schema_version on a non-empty blob → 1 + warning.
		warnings.append(_("{0}: missing schema_version — treated as 1").format(label))
		version = 1
	elif isinstance(version, bool) or not isinstance(version, int) or version < 1:
		_drop(label, _("schema_version {0!r} is not a positive integer").format(version), warnings)
		return None

	if version > CURRENT_SCHEMA_VERSION:
		# §2.3 rule 4: never guess-interpreted forward.
		_drop(
			label,
			_("schema_version {0} is newer than this server understands ({1})").format(
				version, CURRENT_SCHEMA_VERSION
			),
			warnings,
		)
		return None

	while version < CURRENT_SCHEMA_VERSION:
		upgrader = UPGRADERS.get(version)
		if upgrader is None:
			_drop(label, _("no upgrader from schema_version {0}").format(version), warnings)
			return None
		try:
			cfg = upgrader(cfg)
		except Exception:
			_drop(
				label,
				_("upgrader from schema_version {0} failed").format(version),
				warnings,
				sample=frappe.get_traceback(),
			)
			return None
		version += 1
		cfg["schema_version"] = version

	return cfg


def _meta(layout, has_preference, warnings):
	return {
		"layout": layout,
		"has_preference": has_preference,
		"schema_version": CURRENT_SCHEMA_VERSION,
		"warnings": warnings,
	}


def _load_layout_config(requested, warnings):
	"""Load + prepare the layout layer, cascading requested → Default → skeleton.

	Returns ``(config_dict_or_None, applied_layout_name_or_None)`` — §14 rows
	5/6: a missing/disabled/broken layout drops to ``Default``; a broken
	``Default`` drops to the skeleton. Every hop is warned + Error-Logged.
	"""
	candidates = [requested] if requested else []
	if DEFAULT_LAYOUT_NAME not in candidates:
		candidates.append(DEFAULT_LAYOUT_NAME)

	for name in candidates:
		label = _("layout '{0}'").format(name)
		row = frappe.db.get_value("UI Layout", name, ["config", "disabled"], as_dict=True)
		if not row:
			_drop(label, _("record not found"), warnings)
			continue
		if row.disabled:
			_drop(label, _("layout is disabled"), warnings)
			continue
		cfg = _prepare_layer(row.config, label, warnings, required=True)
		if cfg is None:
			continue  # _prepare_layer already warned + logged the drop
		return cfg, name

	return None, None


def _resolve_config(user):
	warnings = []

	if frappe.conf.get(KILL_SWITCH_KEY):
		# §14 row 17: skip all records; client falls back to compiled default.
		warnings.append(_("ui config disabled by site config"))
		return get_skeleton(), _meta(None, False, warnings)

	pref = None
	if user and isinstance(user, str):
		# Point-read 1 of 2 (docname == user; §4.1). SM-only DocTypes read in
		# code, scoped to the passed user — the sidebar_view isolation pattern.
		pref = frappe.db.get_value(
			"YRP UI Preference", user, ["layout", "overrides"], as_dict=True
		)

	# Point-read 2 of 2 (+ fallback hops only on degradation).
	layout_cfg, layout_name = _load_layout_config(pref.layout if pref else None, warnings)

	overrides = _prepare_layer(pref.overrides if pref else None, "overrides", warnings)
	if overrides:
		for key in overrides:
			if key != "schema_version" and key not in OVERRIDABLE_KEYS:
				# §14 row 8 — the merge whitelist drops it; leave the trace here.
				warnings.append(_("overrides: unknown key '{0}' ignored").format(key))

	resolved = merge(merge(get_skeleton(), layout_cfg), overrides, OVERRIDABLE_KEYS)
	return resolved, _meta(layout_name, bool(pref), warnings)


def resolve_config(user):
	"""Resolve the effective /web UI config for ``user`` (§4, §5, §14).

	Returns ``(merged_config, meta)`` and NEVER raises: every data defect
	drops that layer only, appends to ``meta["warnings"]`` and writes an
	Error Log entry (title prefix ``UI config:``) so ops sees it.
	``meta`` = ``{layout, has_preference, schema_version, warnings}``.
	"""
	try:
		return _resolve_config(user)
	except Exception:
		try:
			detail = f"user: {user!r}\n{frappe.get_traceback()}"
		except Exception:
			detail = None
		_log_degradation("UI config: resolver crashed — serving skeleton", detail)
		return get_skeleton(), _meta(None, False, [_("ui config resolver failed — serving skeleton")])


# ── User lifecycle doc-event handlers (§3.3) ────────────────────────────────


def delete_ui_preference_for_user(doc, method=None):
	"""``User.on_trash`` — delete the user's YRP UI Preference row.

	Mandatory, not belt-and-suspenders: Frappe does NOT cascade third-party
	links on User deletion — ``check_if_doc_is_linked`` raises
	``LinkExistsError`` after ``on_trash`` hooks run, so without this hook
	user offboarding is blocked by a cosmetic record.
	"""
	if frappe.db.exists("YRP UI Preference", doc.name):
		frappe.delete_doc("YRP UI Preference", doc.name, ignore_permissions=True, force=True)


def merge_ui_preference_for_user(doc, method=None, old=None, new=None, merge=False):
	"""``User.before_rename`` — resolve the preference collision BEFORE a user merge.

	``frappe.model.rename_doc`` bulk-updates ``user`` Link columns
	(``update_link_field_values``) *before* ``after_rename`` fires, so when
	BOTH users own a YRP UI Preference a merge would hit the UNIQUE index on
	``user`` with an IntegrityError mid-transaction — the ``after_rename``
	dedup branch would never run. Core precedent: frappe's
	``User.before_rename`` deletes the old Notification Settings on merge.

	The surviving user keeps their own preference; the merged-away user's
	record is dropped. When only the merged-away user has a preference there
	is no collision — the normal Link relink + ``after_rename`` docname
	rename carries it over to the survivor, so nothing is deleted here.
	"""
	if not merge:
		return
	if frappe.db.exists("YRP UI Preference", old) and frappe.db.exists("YRP UI Preference", new):
		frappe.delete_doc("YRP UI Preference", old, ignore_permissions=True, force=True)


def rename_ui_preference_for_user(doc, method=None, old=None, new=None, merge=False):
	"""``User.after_rename`` — keep ``autoname: field:user`` truthful.

	Frappe's rename machinery updates the ``user`` Link value on the
	preference but not its docname; rename the record to match.
	"""
	if not old or not frappe.db.exists("YRP UI Preference", old):
		return
	if frappe.db.exists("YRP UI Preference", new):
		# Normally unreachable: merge collisions are resolved up front by
		# merge_ui_preference_for_user (before_rename). Kept as cheap defense —
		# the surviving user keeps their own preference; drop the stray record
		# instead of failing the rename.
		frappe.delete_doc("YRP UI Preference", old, ignore_permissions=True, force=True)
		return
	frappe.rename_doc("YRP UI Preference", old, new, force=True)


# ── Whitelisted endpoints + boot hook (§4) ──────────────────────────────────


@frappe.whitelist()
def get_my_ui_config():
	"""Resolved config for the SESSION user (§4.1, locked decision 9).

	Identity is ``frappe.session.user``, never an argument (locked decision
	5). Backs the store's ``refresh()`` action ("Refresh UI" user-menu item).
	Never throws — every data defect degrades per §14 with a trace.
	"""
	config, meta = resolve_config(frappe.session.user)
	return {"config": config, "meta": meta}


@frappe.whitelist(methods=["POST"])
@rate_limit(limit=SAVE_RATE_LIMIT, seconds=SAVE_RATE_WINDOW_SECONDS)
def save_my_ui_overrides(overrides=None):
	"""Self-service Knobs panel save (locked decision, 2026-07-15): persist the
	SESSION user's knob changes into their OWN ``YRP UI Preference.overrides``.

	POST-only: a GET would return the freshly resolved "saved" config and then
	Frappe would roll the write back (GET transactions never commit) — the
	client would render a save that never happened. Rate-limited per IP (the
	Knobs panel serializes saves; a human stays far under the limit).

	Bounded exactly like the SM Desk path, minus the SM:

	- identity is ``frappe.session.user``, never an argument — another user's
	  record is unreachable by construction (locked decision 5); Guest is
	  rejected outright;
	- the serialized payload is hard-capped at ``MAX_OVERRIDES_BYTES`` — any
	  authenticated user can reach this endpoint, so unbounded input is a
	  storage/boot-bloat channel;
	- the payload passes the SAME ``validate_config(layer="overrides")`` gate
	  the Desk save runs (§3.2) — hard errors throw to the caller unchanged;
	- top-level keys are filtered to ``OVERRIDABLE_KEYS`` before storage
	  (§2.2 / §14 row 8 — the merge whitelist would drop them at read time
	  anyway; filtering at write time keeps the stored delta clean);
	- ONLY the ``overrides`` field is written. An existing record keeps its
	  ``layout`` and ``notes`` verbatim; a missing record is created with
	  ``layout`` left empty, so the resolver falls back to Default (§14 row 2).

	Returns the freshly resolved ``{config, meta}`` (same shape as
	``get_my_ui_config``) so the client can re-render immediately; save-time
	validation warnings (e.g. filtered unknown keys) are prepended to
	``meta["warnings"]``.
	"""
	user = _require_logged_in_session_user()

	cfg = overrides
	if isinstance(cfg, str):
		# Cap BEFORE parsing — the over-the-wire shape is always a string, and
		# json.loads on an unbounded blob is itself the first cost.
		_reject_oversize_overrides(len(cfg.encode("utf-8")))
		try:
			cfg = json.loads(cfg)
		except ValueError:
			_hard("overrides", _("config is not valid JSON"))
	if not isinstance(cfg, dict):
		_hard("overrides", _("config must be a JSON object"))
	else:
		# Dict input (direct/in-process callers) gets the same ceiling.
		_reject_oversize_overrides(
			len(json.dumps(cfg, default=str, ensure_ascii=False).encode("utf-8"))
		)

	# Same save-time gate as the Desk path (§3.2): hard errors throw here,
	# soft issues come back as warnings for the caller.
	save_warnings = validate_config(cfg, layer="overrides")

	filtered = {
		key: value
		for key, value in cfg.items()
		if key == "schema_version" or key in OVERRIDABLE_KEYS
	}
	_upsert_my_overrides(user, json.dumps(filtered, default=str, ensure_ascii=False))

	config, meta = resolve_config(user)
	meta["warnings"] = save_warnings + meta["warnings"]
	return {"config": config, "meta": meta}


@frappe.whitelist()
def get_my_ui_overrides():
	"""Raw stored personal overrides for the SESSION user (Knobs panel hydration).

	``save_my_ui_overrides`` replaces the stored field wholesale, and the
	RESOLVED config cannot tell layout values from personal ones — so the panel
	loads the stored sparse delta once and always re-saves the FULL delta;
	overrides it didn't touch (e.g. SM-planted ``listViews``) survive a knob
	change. Same identity rule as the save/reset: session user only, Guest
	rejected. A missing or broken layer returns ``{}`` (the resolver would drop
	it at read time anyway, §14 row 7) with the drop reason in ``warnings``.
	"""
	user = _require_logged_in_session_user()
	warnings = []
	raw = frappe.db.get_value("YRP UI Preference", user, "overrides")
	overrides = _prepare_layer(raw, "overrides", warnings)
	return {"overrides": overrides or {}, "warnings": warnings}


@frappe.whitelist(methods=["POST"])
def reset_my_ui_overrides():
	"""Self-service Knobs panel reset: clear the SESSION user's personal
	overrides so their knobs fall back to the layout's values.

	POST-only for the same reason as the save: a GET would report a reset that
	Frappe's end-of-request rollback then undoes.

	The record is deleted only when it carries nothing else (no ``layout``,
	no ``notes``) — an empty record and no record resolve identically (§14
	rows 1–3), and a bare leftover row would keep ``meta.has_preference``
	truthy for no reason. Otherwise ONLY the ``overrides`` field is blanked;
	``layout`` and ``notes`` are never touched. Same identity rule as the
	save: session user only, Guest rejected.

	Returns the freshly resolved ``{config, meta}``, like the save.
	"""
	user = _require_logged_in_session_user()

	if frappe.db.exists("YRP UI Preference", user):
		doc = frappe.get_doc("YRP UI Preference", user)
		if not doc.layout and not (doc.notes or "").strip():
			frappe.delete_doc("YRP UI Preference", user, ignore_permissions=True)
		elif doc.overrides:
			doc.overrides = None
			doc.save(ignore_permissions=True)

	config, meta = resolve_config(user)
	return {"config": config, "meta": meta}


def _reject_oversize_overrides(nbytes):
	"""Hard cap for the self-service save (M6, 2026-07-15 review): reject
	before validation/storage; the 32 KB soft warning still covers sane sizes."""
	if nbytes > MAX_OVERRIDES_BYTES:
		_hard(
			"overrides",
			_("payload is {0} KB — the limit is {1} KB").format(
				round(nbytes / 1024), MAX_OVERRIDES_BYTES // 1024
			),
		)


def _require_logged_in_session_user():
	"""Shared gate for the self-service endpoints: a real logged-in session.

	``@frappe.whitelist()`` (no ``allow_guest``) already blocks Guest at the
	HTTP layer; this in-function check keeps the guarantee when the function
	is reached any other way (direct call, console, future wrapper)."""
	user = frappe.session.user
	if not user or user == "Guest":
		frappe.throw(
			_("You must be logged in to change UI preferences"),
			frappe.PermissionError,
			title=_("Not Permitted"),
		)
	return user


def _upsert_my_overrides(user, serialized):
	"""Write ONLY the ``overrides`` field of ``user``'s own record, creating
	the record if missing.

	Full doc API (never ``frappe.db.set_value``) so ``track_changes`` keeps
	its audit trail (§3.2: mandatory — rollback lever for botched overrides)
	and the controller's ``validate()`` still runs. PK-race-safe via the
	``sidebar_view.py`` savepoint-upsert pattern the spec reserves for exactly
	this case (§3.2): ``autoname: field:user`` makes a concurrent first save
	collide on the primary key instead of silently duplicating; the loser
	rolls back its failed insert only (not the whole request transaction) and
	updates the now-existing row so its save is not dropped.
	"""
	if frappe.db.exists("YRP UI Preference", user):
		_update_overrides_only(user, serialized)
		return

	savepoint = "yrp_ui_pref_upsert"
	frappe.db.savepoint(savepoint)
	try:
		frappe.get_doc(
			{"doctype": "YRP UI Preference", "user": user, "overrides": serialized}
		).insert(ignore_permissions=True)
	except frappe.DuplicateEntryError:
		# Lost the race — the row now exists. Undo the failed insert, then
		# update the winner's row so this save still lands.
		frappe.db.rollback(save_point=savepoint)
		_update_overrides_only(user, serialized)


def _update_overrides_only(user, serialized):
	doc = frappe.get_doc("YRP UI Preference", user)
	doc.overrides = serialized
	doc.save(ignore_permissions=True)


@frappe.whitelist()
def get_ui_config_for(user=None, layout=None):
	"""SM-only preview (§4.2, locked decision 9).

	Params are mutually exclusive: ``user=`` previews a person (their layers,
	perm hints computed AS them); ``layout=`` previews a bare layout with no
	overrides (perm hints = the caller's own — backs the §10 sandbox preview
	for scratch layouts assigned to nobody). These throwing paths are all
	SM-facing: unknown/disabled user, unknown/disabled/broken layout, and
	passing both or neither all fail loudly.
	"""
	frappe.only_for("System Manager")

	if bool(user) == bool(layout):
		frappe.throw(
			_("Pass exactly one of user= or layout="),
			title=_("Invalid UI Config Preview"),
		)

	if user:
		if not frappe.db.get_value("User", user, "enabled"):
			frappe.throw(_("Unknown or disabled user"))
		config, meta = resolve_config(user)
		perm_user = user
	else:
		config, meta = _resolve_layout_preview(layout)
		perm_user = frappe.session.user

	return {"config": config, "meta": meta, "perm_hints": _perm_hints(config, perm_user)}


def _resolve_layout_preview(layout):
	"""§4.2 ``layout=`` branch: ``merge(skeleton, that_layout.config)``, no
	overrides. Unknown, disabled or broken layouts fail LOUDLY for the SM —
	never the resolver's silent fallback cascade."""
	if frappe.conf.get(KILL_SWITCH_KEY):
		return get_skeleton(), _meta(None, False, [_("ui config disabled by site config")])

	row = frappe.db.get_value("UI Layout", layout, ["config", "disabled"], as_dict=True)
	if not row or row.disabled:
		frappe.throw(_("Unknown or disabled layout"))

	warnings = []
	cfg = _prepare_layer(row.config, _("layout '{0}'").format(layout), warnings, required=True)
	if cfg is None:
		frappe.throw(
			_("UI Layout {0} has a broken config: {1}").format(
				frappe.bold(layout), "; ".join(warnings) or _("empty config")
			)
		)

	return merge(get_skeleton(), cfg), _meta(layout, False, warnings)


def _perm_hints(config, user):
	"""§4.2 perm hints: ``can_read`` / ``can_create`` computed as ``user``
	over the doctypes actually present in the resolved config's nav plus its
	``quickCreate`` list (never a mirrored constant), via
	``frappe.has_permission`` — the same authoritative check
	``web.py:_apply_accurate_web_perms()`` uses."""
	doctypes = []

	nav = config.get("nav") or {}
	for group in nav.get("groups") or []:
		if not isinstance(group, dict):
			continue
		for item in group.get("items") or []:
			doctype = item.get("doctype") if isinstance(item, dict) else None
			if doctype and doctype not in doctypes:
				doctypes.append(doctype)

	for doctype in config.get("quickCreate") or []:
		if isinstance(doctype, str) and doctype and doctype not in doctypes:
			doctypes.append(doctype)

	hints = {"can_read": [], "can_create": []}
	for doctype in doctypes:
		if not frappe.db.exists("DocType", doctype):
			continue  # typo'd nav entry — the client catalog drops it too (§14 row 12)
		if frappe.has_permission(doctype, "read", user=user):
			hints["can_read"].append(doctype)
		if frappe.has_permission(doctype, "create", user=user):
			hints["can_create"].append(doctype)
	return hints


def get_config_for_boot():
	"""NOT whitelisted — called by the customization app's ``www/web.py``
	``get_boot()`` (§4.3, §8.1). Any unexpected exception → Error Log +
	``None``: a UI-config bug must never 500 the /web page."""
	try:
		config, meta = resolve_config(frappe.session.user)
		return {"config": config, "meta": meta}
	except Exception:
		try:
			detail = frappe.get_traceback()
		except Exception:
			detail = None
		_log_degradation("UI config: boot resolution failed", detail)
		return None
