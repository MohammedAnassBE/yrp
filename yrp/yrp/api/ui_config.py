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
from frappe.model import no_value_fields
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
# nav.position — which nav shell the engine renders. "sidebar" is the shipped
# default; a value the client doesn't recognise renders the sidebar shell
# (AppLayout.vue strict compare), so an off-vocabulary value warns softly.
# USE_CASE §4 Track 1 item 4 grew the original sidebar/topbar pair with the
# desktop/mobile shells bottom-tabs, sidebar-right and icon-rail.
NAV_POSITIONS = ("sidebar", "topbar", "bottom-tabs", "sidebar-right", "icon-rail")
# The sidebar-FAMILY positions nav.sidebar ("pinned") actually modifies — the
# three left/right rail shells (a position of None also means "sidebar"). On
# topbar / bottom-tabs a sidebar mode does nothing (soft "no effect" warning).
NAV_SIDEBAR_POSITIONS = ("sidebar", "sidebar-right", "icon-rail")
# nav.sidebar — resting state of the sidebar-family shells. "flyout" (default)
# = today's slim icon rail that expands on hover; "pinned" = the opt-in
# always-expanded labelled sidebar (USE_CASE 2026-07-17 Track 1 item 4,
# Claude-style). Soft: an off-vocabulary value keeps today's flyout.
NAV_SIDEBAR_MODES = ("flyout", "pinned")
# nav.shell — overall app chrome. "standard" (default) = the desktop chrome;
# "mobile-shell" = the compact phone-chrome anchor (USE_CASE §4 item 4 / review
# amendment 8), which pairs naturally with nav.position "bottom-tabs". Soft.
NAV_SHELLS = ("standard", "mobile-shell")
# nav.overflow — max PRIMARY tabs the bottom-tabs shell shows before the rest
# collapse behind a trailing "More" tab (first N + More). Soft int; only
# meaningful with nav.position "bottom-tabs" (soft "no effect" warning else).
NAV_OVERFLOW_MIN = 2
NAV_OVERFLOW_MAX = 8
NAV_OVERFLOW_DEFAULT = 5
DATE_FORMATS = ("dd-mm-yyyy", "yyyy-mm-dd")
CHROME_KEYS = ("search", "themeToggle")
REALTIME_KEYS = ("enabled", "intervalMs", "toast")
# realtime knobs that NO client consumes yet (ChromeBar reads only `enabled`).
# Presence gets the explicit RESERVED notice (USE_CASE §4 item 17).
REALTIME_RESERVED_KEYS = ("intervalMs", "toast")

# ── Deep-vocabulary constants (USE_CASE §4 item 17: no silent drops) ────────
# Every constant here mirrors EXACTLY what a client consumes today; an
# off-vocabulary value is silently dropped/defaulted client-side, so the save
# must say so out loud. All checks below are SOFT — hard errors stay hard only
# where they already were.

# nav object vocabulary (store.navGroups + AppSidebar/NavTopbar consumption).
# `home` is RESERVED: Demo-7-style {label, icon, view: "home"} is stored but
# no client reads it — Home is always rendered first, unconditionally.
# `sidebar`/`shell`/`footer`/`overflow` are the Track 1 item 4 nav-family knobs.
NAV_KEYS = ("position", "sidebar", "shell", "footer", "overflow", "groups", "hidden", "home")
NAV_RESERVED_KEYS = ("home",)
NAV_GROUP_KEYS = ("id", "label", "items")
# Nav items: the clients read ONLY doctype (catalog route/label) + icon.
NAV_ITEM_KEYS = ("doctype", "icon")

# screens: uiConfig store exposes only screens.home; every other screen key
# (incl. the §2.1-reserved list:<DocType>/detail:<DocType>) renders nothing.
KNOWN_SCREEN_KEYS = ("home",)
SCREEN_KEYS = ("blocks", "hidden")
BLOCK_KEYS = ("id", "type", "size", "props")
# ScreenRenderer spanClass: anything else silently renders full-width.
BLOCK_SIZES = ("full", "half", "third")

# Metric names the home-queues block can actually render — the server mirror
# of HomeQueues.vue METRIC_TO_QUEUE. A REGISTERED but non-queue metric (a KPI
# like "completion") in home-queues stats renders NOTHING with zero warnings —
# the 2026-07-17 owner bite this whole item exists to kill.
HOME_QUEUE_METRICS = ("open_lots", "open_wos", "draft_dcs", "draft_grns")

# Per-block-type prop vocabulary (the defineProps list of every registered
# block). Unknown block types stay unvalidated (the client bundle may be
# newer than this server); unknown props on a KNOWN type are silently ignored
# by Vue, so they warn here. `maxCards` is RESERVED (validated since day one,
# consumed by nothing — HomeQueues reads only `stats`).
BLOCK_PROP_KEYS = {
	"home-greeting": ("greetingName", "sub", "newCta"),
	"home-queues": ("stats", "maxCards"),
	"home-recent": ("doctypes", "recentStyle"),
	"home-quick-create": ("doctypes",),
	"summary-tiles": ("metrics",),
	# `cardTemplate` (Track 1 item 2): an optional composite tree rendered as
	# each card's interior in the cards/kanban variants — scope = the ROW
	# record, same grammar as the composite block's `tree`. Absent → the
	# shipped card look, byte-identical (parity law).
	"record-list": (
		"doctype",
		"variant",
		"columns",
		"groupBy",
		"titleField",
		"pageSize",
		"title",
		"cardTemplate",
	),
	"calculator-panel": ("calculation", "params"),
	# Bounded composition layer (USE_CASE §3(c)/(d), Track 1 item 1). `source`
	# declares which permission-gated registry data feeds the binding scope
	# (metrics via ui_metrics + recent records of one doctype via the list
	# API); `tree` is the validated primitive tree the engine renders. Deep
	# TREE validation (primitive whitelist, token enums, bind grammar, caps)
	# is LIVE since Track 1 item 3 — see _validate_composite_tree.
	"composite": ("source", "tree"),
	# story-scroller (USE_CASE §4 Track 1 item 7): the last missing demo
	# topology — a horizontal/vertical rail of recent records of ONE doctype,
	# each a "story" chip (name + a few fields + status dot). Same host-owned
	# getList + subscribeList + statusColors plumbing as record-list; the JSON
	# names `source` (doctype), `fields`, `limit`, `orientation` — never a query.
	"story-scroller": ("source", "fields", "limit", "orientation"),
}
# story-scroller prop vocabulary (StoryScroller.vue defineProps mirror).
STORY_SCROLLER_ORIENTATIONS = ("horizontal", "vertical")
STORY_SCROLLER_LIMIT_MIN = 1
STORY_SCROLLER_LIMIT_MAX = 30
STORY_SCROLLER_LIMIT_DEFAULT = 12
NEWCTA_KEYS = ("primary", "menu")
# composite.source vocabulary (Composite.vue reads exactly these keys).
COMPOSITE_SOURCE_KEYS = ("metrics", "doctype", "limit")
# Engine grammar caps (apps/yrp/frontend/src/composite/grammar.js mirror —
# enforced as HARD errors by the item-3 tree validator below; the engine
# re-enforces them at render, where an over-cap tree draws the honest card).
COMPOSITE_MAX_NODES = 100
COMPOSITE_MAX_DEPTH = 6

# ── Composite grammar server mirror (USE_CASE §4 Track 1 item 3) ────────────
# The deep-tree vocabulary of the `composite` block and BOTH cardTemplate
# seams (record-list block props + listViews[<DocType>]) — a hand-maintained
# mirror of the ENGINE grammar (apps/yrp/frontend/src/composite/grammar.js,
# the single ground truth; essdee_yrp/api/test_ui_mirror.py drift-guards every
# constant in this section against the parsed grammar.js). validate_config
# enforces it at save time wherever a composite tree can appear.
#
# HARD vs SOFT (the item-3 rule): the node/depth caps and injection-shaped
# values — markup/script-shaped literal strings, prototype-shaped or
# expression-shaped bind paths, scheme/traversal image srcs — BLOCK the save;
# taste mistakes (unknown primitive, off-vocabulary token, bad formatter name,
# malformed showIf, dead bindings) warn softly and the client keeps its honest
# path-labelled fallback.

COMPOSITE_GRAMMAR_VERSION = 1
# Grammar upgraders (§3(d) / review amendment 4 — the composite twin of
# UPGRADERS above). A BREAKING grammar change (prop rename, enum-member
# removal, node-shape change) bumps COMPOSITE_GRAMMAR_VERSION and registers
# ONE pure function here: ``COMPOSITE_TREE_UPGRADERS[N]`` takes a version-N
# tree ROOT and returns its version-N+1 shape (the machinery stamps the new
# ``version`` after each step — upgraders never write it), then every stored
# layout is re-validated before shipping. Purely ADDITIVE growth (a new
# primitive, a new enum member) needs NO upgrader — old trees render
# identically. Trees carry the grammar version as an OPTIONAL root-node
# ``version`` int (absent = 1); ``_upgrade_composite_trees`` applies pending
# upgraders at read time in every seam; stored records are rewritten only
# when an SM next saves them (same contract as the schema upgraders).
COMPOSITE_TREE_UPGRADERS = {}

COMPOSITE_SHOWIF_OPS = ("=", "!=", ">", "<", "set", "not-set")
COMPOSITE_FORMATS = ("date", "qty", "number", "status-label")
COMPOSITE_NODE_KEYS = ("type", "props", "children", "showIf")
COMPOSITE_ROOT_KEYS = (*COMPOSITE_NODE_KEYS, "version")
COMPOSITE_BINDING_KEYS = ("bind", "format")
COMPOSITE_SHOWIF_KEYS = ("field", "op", "value")
COMPOSITE_CONTAINER_PRIMITIVES = ("stack", "grid", "card")

# Dot-path grammar (grammar.js BIND_PATH_RE / FORBIDDEN_PATH_SEGMENTS).
# Checked with re.fullmatch, never match + '$' (house security-format rule).
COMPOSITE_BIND_PATH_RE = re.compile(r"[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)*")
# Charset probe ABOVE the path grammar: a path outside this alphabet carries
# expression/markup machinery — (), [], quotes, spaces, '<' — and hard-fails
# as injection-shaped. Inside the alphabet but failing COMPOSITE_BIND_PATH_RE
# (empty segment, leading/trailing dot) is a typo — soft, the client resolves
# nothing and renders the em-dash.
COMPOSITE_BIND_CHARSET_RE = re.compile(r"[A-Za-z0-9_.-]*")
COMPOSITE_FORBIDDEN_PATH_SEGMENTS = ("__proto__", "prototype", "constructor")

# image.src (grammar.js SITE_FILE_RE): same-origin /files/ or /private/files/
# paths ONLY — private files still 403 server-side without permission.
COMPOSITE_SITE_FILE_RE = re.compile(r"/(?:private/)?files/[A-Za-z0-9][A-Za-z0-9 ._()/-]*")

# Markup/script-shaped literal strings — the §3(d) "no HTML/CSS/JS strings"
# boundary enforced at save time. The engine renders every value through text
# interpolation (inert), but such strings have NO legitimate place in a
# layout: '<tag' / '</' / a javascript: scheme hard-fails wherever a literal
# string lands in a tree. Plain prose with a spaced '<' ("qty < 5") stays
# legal (an HTML tag never has whitespace after '<').
COMPOSITE_MARKUP_RE = re.compile(r"</?[A-Za-z!]|javascript\s*:", re.IGNORECASE)

# The primitive registry — names, container-ness and the full token-typed prop
# vocabulary of each (grammar.js COMPOSITE_PRIMITIVES, kind-for-kind):
#   {"kind": "enum", "values": (...), "default": ...}  token enum
#   {"kind": "bindable"}                literal scalar OR {bind, format}
#   {"kind": "bindable-number"}         same, rendered as a number
#   {"kind": "boolean", "default": ...}
#   {"kind": "int", "min": .., "max": .., "default": ...}
#   {"kind": "string"}                  plain text (never HTML)
#   {"kind": "icon"}                    must fullmatch ICON_RE
#   {"kind": "site-file"}               static site-file path (bindings refused)
_COMPOSITE_GAP = {"kind": "enum", "values": ("none", "xs", "sm", "md", "lg"), "default": "md"}
_COMPOSITE_ALIGN = {"kind": "enum", "values": ("start", "center", "end"), "default": "start"}

COMPOSITE_PRIMITIVES = {
	# containers (the only nodes whose children the engine renders)
	"stack": {
		"container": True,
		"props": {
			"direction": {"kind": "enum", "values": ("column", "row"), "default": "column"},
			"gap": _COMPOSITE_GAP,
			"align": {
				"kind": "enum",
				"values": ("start", "center", "end", "stretch"),
				"default": "stretch",
			},
			"justify": {
				"kind": "enum",
				"values": ("start", "center", "end", "between"),
				"default": "start",
			},
			"wrap": {"kind": "boolean", "default": False},
		},
	},
	"grid": {
		"container": True,
		"props": {
			"columns": {"kind": "int", "min": 1, "max": 6, "default": 2},
			"gap": _COMPOSITE_GAP,
		},
	},
	"card": {
		"container": True,
		"props": {
			"padding": {"kind": "enum", "values": ("none", "sm", "md", "lg"), "default": "md"},
			"tone": {"kind": "enum", "values": ("default", "tint", "muted"), "default": "default"},
		},
	},
	# leaves
	"heading": {
		"container": False,
		"props": {
			"text": {"kind": "bindable"},
			"level": {"kind": "int", "min": 1, "max": 3, "default": 2},
			"align": _COMPOSITE_ALIGN,
		},
	},
	"text": {
		"container": False,
		"props": {
			"value": {"kind": "bindable"},
			"tone": {
				"kind": "enum",
				"values": ("default", "muted", "accent", "danger"),
				"default": "default",
			},
			"size": {"kind": "enum", "values": ("xs", "sm", "md", "lg"), "default": "md"},
			"weight": {
				"kind": "enum",
				"values": ("regular", "medium", "bold"),
				"default": "regular",
			},
			"mono": {"kind": "boolean", "default": False},
			"align": _COMPOSITE_ALIGN,
		},
	},
	"kv-row": {
		"container": False,
		"props": {
			"label": {"kind": "bindable"},
			"value": {"kind": "bindable"},
			"mono": {"kind": "boolean", "default": False},
		},
	},
	"badge": {
		"container": False,
		"props": {
			"text": {"kind": "bindable"},
			"status": {"kind": "bindable"},
			"tone": {"kind": "enum", "values": ("neutral", "accent"), "default": "neutral"},
		},
	},
	"stat": {
		"container": False,
		"props": {
			"value": {"kind": "bindable"},
			"label": {"kind": "bindable"},
			"align": _COMPOSITE_ALIGN,
		},
	},
	"divider": {"container": False, "props": {}},
	"icon": {
		"container": False,
		"props": {
			"name": {"kind": "icon"},
			"size": {"kind": "enum", "values": ("sm", "md", "lg"), "default": "md"},
			"tone": {"kind": "enum", "values": ("default", "muted", "accent"), "default": "default"},
		},
	},
	"progress": {
		"container": False,
		"props": {
			"value": {"kind": "bindable-number"},
			"tone": {"kind": "enum", "values": ("accent", "muted"), "default": "accent"},
		},
	},
	"image": {
		"container": False,
		"props": {
			"src": {"kind": "site-file"},
			"alt": {"kind": "string"},
			"height": {"kind": "int", "min": 16, "max": 480, "default": None},
			"fit": {"kind": "enum", "values": ("cover", "contain"), "default": "cover"},
		},
	},
	"spacer": {
		"container": False,
		"props": {
			"size": {"kind": "enum", "values": ("xs", "sm", "md", "lg"), "default": "md"},
		},
	},
}

# listViews[<DocType>] vocabulary (DynamicListPage + store.listColumns).
# `cardTemplate` (Track 1 item 2): optional composite tree rendered as each
# card's interior on the routed list page's cards/kanban variants — scope =
# the ROW record; absent → the shipped card markup, byte-identical.
# ── listViews table-renderer flags (USE_CASE §4 Track 1 item 6) ─────────────
# Presentation flags the ROUTED list page's TABLE renderer honours. ALL SOFT:
# absent = today's table, byte-identical (parity law); an off-vocabulary value
# is ignored client-side and the shipped look is kept. cards/kanban absorb the
# same looks via cardTemplate, so a table flag set on those variants is dead
# config and warns (item-17 no-silent-drop posture).
LIST_TABLE_FLAGS = ("rowSize", "colourBy", "monoId", "chipStyle", "headerBand", "edgeStatus")
LIST_ROW_SIZES = ("compact", "cozy", "comfortable")  # "cozy" == today's row height
LIST_CHIP_STYLES = ("chip", "tabs")  # "chip" == today's status pills
# colourBy names the field whose value tints each row, OR the "status" keyword
# (colour by document status via the registry status-colour map). A fieldname
# is meta-checked against the doctype's renderable fields, same as groupBy.
LIST_COLOUR_STATUS = "status"
LIST_VIEW_KEYS = ("variant", "columns", "groupBy", "titleField", "cardTemplate", *LIST_TABLE_FLAGS)
LIST_VIEW_VARIANTS = ("table", "cards", "kanban")
# Column entries: {field, label} objects work EVERYWHERE; bare "fieldname"
# strings work ONLY in record-list home blocks (RecordList.vue colDescs
# accepts both forms) — the ROUTED list page (DynamicListPage layoutColumns)
# reads only `.field` and silently skips string entries, so the listViews
# path warns on every string (2026-07-17 review: the validator used to
# certify strings there while the client dropped the whole columns override).
# The clients read only field + label.
COLUMN_KEYS = ("field", "label")

# Fieldtypes NO list renderer shows as a column — the server mirror of
# DynamicListPage.vue NON_LISTABLE. Routed lists additionally exclude hidden
# fields and 'name' (always the first column); RecordList's meta-default path
# filters the same way. A column/groupBy/titleField naming one of these (or a
# hidden field, or a Frappe default field like modified/owner) lint-passed
# before 2026-07-17 yet rendered nothing — _doctype_fieldnames now excludes
# them all so the save warns instead.
NON_LISTABLE_FIELDTYPES = (
	"Table",
	"Table MultiSelect",
	"Text Editor",
	"Long Text",
	"Small Text",
	"Text",
	"HTML",
	"HTML Editor",
	"Code",
	"Markdown Editor",
	"Section Break",
	"Column Break",
	"Tab Break",
	"Fold",
	"Heading",
	"Button",
	"Image",
	"Geolocation",
	"Signature",
)

# Soft-checked vocabularies for the structural knobs. An off-vocabulary value
# never blocks a save — the client warns/ignores it and keeps today's
# behaviour (PARITY: every knob's absence/default = the current UI).
DETAIL_KEYS = ("position", "related")
# `rich` is a RESERVED detail knob name (USE_CASE item 17 vocabulary): stored,
# consumed by nothing yet — presence gets the explicit RESERVED notice.
DETAIL_RESERVED_KEYS = ("rich",)
DETAIL_POSITIONS = ("page", "right", "center", "bottom-sheet")

# `detail.related` (2026-07-21): the single-record cross-DocType workbench
# (USE_CASE case (b)). `detail.related` is an object keyed by the SOURCE
# DocType (like listViews) whose value is a list of related-record sets; each
# set composes the open document's linked records of ONE other DocType into the
# same detail screen. A set NAMES fields, never a query — the client fetches via
# the permission-gated `get_related` API (frappe.has_permission + get_list) and
# renders each returned row with the SAME row-scoped composite `cardTemplate`
# grammar the list cards use. Absent → nothing renders (parity law). Keys of one
# related-set entry:
#   doctype      the linked DocType to fetch (required)
#   fromField    fieldname ON THE SOURCE DOCTYPE whose value is the filter value
#                (required) — e.g. Lot.production_detail
#   filterField  fieldname ON THE LINKED DOCTYPE to match against (required) —
#                e.g. Item Production Detail.name
#   title        section heading text (optional; host-styled, minimal text)
#   limit        max linked rows to fetch, 1..DETAIL_RELATED_MAX_LIMIT (optional)
#   cardTemplate row-scoped composite tree shaping each linked card's interior
#                (optional; same grammar/caps as listViews cardTemplate)
DETAIL_RELATED_ENTRY_KEYS = (
	"doctype",
	"fromField",
	"filterField",
	"title",
	"limit",
	"cardTemplate",
)
DETAIL_RELATED_MAX_LIMIT = 20
DETAIL_RELATED_DEFAULT_LIMIT = 5
# Soft ceiling on how many related-record sets one source doctype may declare —
# each set is a separate fetch fired on detail open, so a runaway fan-out warns.
DETAIL_RELATED_MAX_SETS = 8
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
# DC entry presentation variants. USE_CASE §4 Track 1 item 5 grew the original
# three with the entry-side mobile/touch topologies (STACK_DECISION: a Stepper
# IS wizard-steps, a bottom Drawer IS sheet-tiles — but the layout JSON names
# only the SEMANTIC variant, never a PrimeVue prop). Every variant is a NEW
# presentation over the SAME embedded-DocDetail + get_work_order_defaults +
# buildPayload/onSave save path — never a new save path.
DC_ENTRY_VARIANTS = (
	"form-grid",
	"size-matrix",
	"inline-grid",
	"wizard-steps",
	"sheet-tiles",
	"touch-rows",
)
# Quantity-input control. USE_CASE §4 item 5 grew the original input-only
# vocabulary with the touch controls: "stepper" (InputNumber showButtons — a
# +/- stepper) and "big-touch" (large finger-target field for the floor).
DC_ENTRY_QTY_CONTROLS = ("input", "stepper", "big-touch")
# Supplier/job-worker picker control. USE_CASE §4 item 5 added "buttons" (a
# button group) alongside the select dropdown and the chips.
DC_ENTRY_SUPPLIER_PICKERS = ("select", "chips", "buttons")
# `dialogPosition` anchors the action dialog/drawer on the 9-position overlay
# grid (OVERLAY_POSITIONS). USE_CASE §4 item 9 promoted it from RESERVED to
# CONSUMED (the actions dialog/drawer anchor now reads it), so it no longer
# draws the item-17 reserved notice — an off-vocabulary value soft-warns.
ACTIONS_KEYS = ("placement", "dialogPosition", "items")
# Action-bar placement. USE_CASE §4 item 9 grew the original three with
# "action-sheet" (a bottom sheet/drawer of the same existing affordances —
# STACK_DECISION: Drawer-bottom IS action-sheet; still a FILTER over capability,
# never a grant).
ACTIONS_PLACEMENTS = ("header", "inline", "floating", "action-sheet")
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
	"focus",  # soft color — focus-ring token (USE_CASE §4 item 10; live — emits --yrp-focus)
	"fontScale",
	"font",
	"arrows",  # soft enum THEME_ARROWS — presentation mode, TOP level only
	"sectionHeaders",  # soft enum THEME_SECTION_HEADERS — presentation mode, TOP level only
	"dark",  # overlay palette for the .dark scheme: {...theme, ...theme.dark}
)
THEME_MODES = ("user", "light", "dark")

# Soft-checked token groups (engine applyTheme.js guards, mirrored).
THEME_COLOR_KEYS = ("bg", "surface", "text", "muted", "line", "surface2")
THEME_DENSITIES = ("compact", "comfortable", "spacious")

# Presentation-mode enums (2026-07-17, DESIGN_PREMIUM §4(i)): scheme-neutral
# data-attribute knobs (html[data-yrp-arrows] / html[data-yrp-section-headers])
# applied by the engine's applyTheme from the TOP-level theme only. First value
# = the shipped default (attribute absent, byte-identical rendering); only the
# second is ever worth authoring. Inside theme.dark they do nothing and warn.
THEME_ARROWS = ("default", "quiet")
THEME_SECTION_HEADERS = ("banded", "plain")

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
	catalog = _web_doctype_catalog()
	_validate_nav(cfg.get("nav"), layer, warnings, catalog)
	_validate_screens(cfg.get("screens"), layer, warnings, catalog)
	_validate_list_views(cfg.get("listViews"), layer, warnings, catalog)
	_validate_quick_create(cfg.get("quickCreate"), layer, warnings, catalog)
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


def _web_doctype_catalog():
	"""DocTypes the consumer /web SPA can actually route — declared by the
	customization app via the ``yrp_web_doctype_catalog`` hook (essdee_yrp
	mirrors its frontend ``doctypes.js`` GROUPS / ``www/web.py`` WEB_DOCTYPES).
	``None`` = no catalog declared (bare-yrp site) or the hook read failed:
	catalog checks are skipped, the site-existence check still runs. Lazy +
	fail-safe like ``_known_metric_keys`` — a hooks defect can NEVER hard-fail
	config validation."""
	try:
		names = frappe.get_hooks("yrp_web_doctype_catalog")
	except Exception:
		return None
	return set(names) if names else None


def _doctype_fieldnames(doctype):
	"""Fieldnames a list column/groupBy/titleField may legally name for
	``doctype``: the RENDERABLE meta fields only — hidden fields and the
	client's non-listable fieldtypes (``NON_LISTABLE_FIELDTYPES``) are
	excluded, and Frappe's default fields (name/modified/owner/…) are NOT
	included. Both list consumers build their column maps strictly from
	visible DocType meta fields (RecordList.vue colDescs/byName + its
	``in_list_view && !hidden`` default path, DynamicListPage
	``eligibleColumns``), so naming anything outside this set renders nothing
	— it must warn at save instead (2026-07-17 review; the old
	meta-plus-default-fields set certified columns both clients drop).
	``None`` = meta unavailable — field-level checks are skipped (the doctype
	itself already warned; a meta defect must never hard-fail validation,
	same contract as ``_known_metric_keys``)."""
	try:
		meta = frappe.get_meta(doctype)
		return {
			df.fieldname
			for df in meta.fields
			if not df.hidden and df.fieldtype not in NON_LISTABLE_FIELDTYPES
		}
	except Exception:
		return None


def _warn_reserved(layer, path, warnings):
	"""USE_CASE §4 item 17 rule: a knob that is stored and shape-validated but
	consumed by NO client yet must say so out loud — an authoring agent must
	never ship a knob that silently does nothing (Track 1 item 11 later wires
	or deletes every one of these)."""
	warnings.append(
		_(
			"{0}: {1} is RESERVED — accepted and stored, but no client consumes it yet; it does nothing today"
		).format(layer, path)
	)


def _warn_unusable_doctype(layer, path, doctype, warnings, catalog):
	"""Shared soft check for every config value that NAMES a DocType the /web
	client must route (nav items, quickCreate, home-block doctype lists,
	newCta entries, listViews keys). The client catalog is the real gate — a
	typo or an off-catalog name is silently dropped at render, so the save
	must surface it. Returns True when a warning was emitted (callers skip
	their deeper per-doctype checks)."""
	if not frappe.db.exists("DocType", doctype):
		warnings.append(
			_("{0}: {1} doctype '{2}' does not exist as a DocType").format(layer, path, doctype)
		)
		return True
	if catalog is not None and doctype not in catalog:
		warnings.append(
			_(
				"{0}: {1} doctype '{2}' is not in the /web doctype catalog — the client cannot route it and drops it"
			).format(layer, path, doctype)
		)
		return True
	return False


def _validate_nav(nav, layer, warnings, catalog=None):
	if nav is None:
		return
	if not isinstance(nav, dict):
		_hard(layer, _("nav must be an object"))

	for key in nav:
		if key in NAV_RESERVED_KEYS:
			_warn_reserved(layer, "nav.{0}".format(key), warnings)
		elif key not in NAV_KEYS:
			warnings.append(_("{0}: unknown key '{1}' inside nav").format(layer, key))

	# Soft: an unrecognised shell renders the sidebar (AppLayout.vue strict
	# compare), so an off-vocabulary value silently renders the sidebar — warn
	# the author instead of ignoring.
	position = nav.get("position")
	if position is not None and position not in NAV_POSITIONS:
		warnings.append(
			_("{0}: nav.position {1!r} is not one of {2} — the client renders the sidebar shell").format(
				layer, position, ", ".join(NAV_POSITIONS)
			)
		)

	# nav.sidebar — the sidebar-family resting state (flyout | pinned). Soft:
	# the client falls back to today's flyout on an off-vocabulary value; the
	# knob is dead on the non-sidebar shells (topbar / bottom-tabs), so say so.
	sidebar_mode = nav.get("sidebar")
	if sidebar_mode is not None:
		if sidebar_mode not in NAV_SIDEBAR_MODES:
			_warn_off_vocabulary(
				layer, "nav.sidebar", sidebar_mode, NAV_SIDEBAR_MODES, "flyout", warnings
			)
		if position is not None and position not in NAV_SIDEBAR_POSITIONS:
			warnings.append(
				_(
					"{0}: nav.sidebar has no effect with nav.position {1!r} — it modifies the sidebar shells ({2}) only"
				).format(layer, position, ", ".join(NAV_SIDEBAR_POSITIONS))
			)

	# nav.shell — overall app chrome (standard | mobile-shell). Soft enum.
	shell = nav.get("shell")
	if shell is not None and shell not in NAV_SHELLS:
		_warn_off_vocabulary(layer, "nav.shell", shell, NAV_SHELLS, "standard", warnings)

	# nav.overflow — max primary bottom-tabs before the trailing More tab. Soft
	# int; only meaningful with nav.position "bottom-tabs" (dead otherwise).
	overflow = nav.get("overflow")
	if overflow is not None:
		if (
			isinstance(overflow, bool)
			or not isinstance(overflow, int)
			or not (NAV_OVERFLOW_MIN <= overflow <= NAV_OVERFLOW_MAX)
		):
			warnings.append(
				_(
					"{0}: nav.overflow must be an integer between {1} and {2} (max primary bottom-tabs before the More tab) — the client falls back to {3}"
				).format(layer, NAV_OVERFLOW_MIN, NAV_OVERFLOW_MAX, NAV_OVERFLOW_DEFAULT)
			)
		if position != "bottom-tabs":
			warnings.append(
				_("{0}: nav.overflow has no effect unless nav.position is 'bottom-tabs'").format(layer)
			)

	_validate_nav_footer(nav.get("footer"), layer, warnings, catalog)

	hidden = nav.get("hidden")
	if hidden is not None and not isinstance(hidden, dict):
		_hard(layer, _("nav.hidden must be an object of booleans"))
	_warn_non_boolean_hidden(hidden, "nav.hidden", layer, warnings)

	groups = nav.get("groups")
	if groups is None:
		return
	if not isinstance(groups, list):
		_hard(layer, _("nav.groups must be a list"))

	seen_doctypes = {}  # doctype -> occurrence count (duplicate detection)
	seen_group_ids = {}
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
		else:
			seen_group_ids[group_id] = seen_group_ids.get(group_id, 0) + 1
		for key in group:
			if key not in NAV_GROUP_KEYS:
				warnings.append(
					_("{0}: unknown key '{1}' inside nav group {2!r}").format(
						layer, key, group_id or group.get("label")
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
			if item.get("view") == "home" and "doctype" not in item:
				# The demo-vocab home entry, special-cased so it never trips
				# the doctype hard error — but the client always renders Home
				# first on its own and IGNORES such an item entirely.
				warnings.append(
					_(
						"{0}: nav item {{'view': 'home'}} is redundant — the client always renders Home first and ignores the item"
					).format(layer)
				)
				continue
			doctype = item.get("doctype")
			if not isinstance(doctype, str) or not doctype.strip():
				_hard(layer, _("every nav item must carry a non-empty string 'doctype'"))
			icon = item.get("icon")
			if icon is not None and (not isinstance(icon, str) or not ICON_RE.fullmatch(icon)):
				_hard(
					layer,
					_("nav item icon '{0}' must match '^pi pi-[a-z0-9-]+$'").format(icon),
				)
			for key in item:
				if key not in NAV_ITEM_KEYS:
					warnings.append(
						_(
							"{0}: unknown key '{1}' inside nav item '{2}' — the client reads only doctype/icon"
						).format(layer, key, doctype)
					)
			if doctype not in seen_doctypes:
				# Soft: the client catalog is the real gate; a typo or an
				# off-catalog doctype just drops the item (checked once per
				# unique doctype — the duplicate warning covers repeats).
				_warn_unusable_doctype(layer, "nav", doctype, warnings, catalog)
			seen_doctypes[doctype] = seen_doctypes.get(doctype, 0) + 1

	for doctype, count in seen_doctypes.items():
		if count > 1:
			warnings.append(
				_("{0}: nav doctype '{1}' appears {2} times across nav groups").format(
					layer, doctype, count
				)
			)
	for group_id, count in seen_group_ids.items():
		if count > 1:
			warnings.append(
				_(
					"{0}: nav group id '{1}' appears {2} times — collapse state and rendering keys collide"
				).format(layer, group_id, count)
			)

	# Layout layer only: hidden keys that target no nav item are dead — a
	# doctype typo silently fails to hide anything. The overrides layer stays
	# unchecked (it legitimately hides items that live in the layout's groups).
	if layer == "layout" and isinstance(hidden, dict):
		for key in hidden:
			if key not in seen_doctypes:
				warnings.append(
					_(
						"{0}: nav.hidden['{1}'] matches no nav item doctype in this layout — it hides nothing"
					).format(layer, key)
				)


def _validate_nav_footer(footer, layer, warnings, catalog=None):
	"""nav.footer (USE_CASE §4 Track 1 item 4) — a pinned footer region of the
	sidebar / nav shell: a list of ``{doctype, icon}`` nav items with the SAME
	grammar as nav.groups items (doctype required = HARD, icon must fullmatch
	ICON_RE = HARD), catalog-gated softly. A duplicate footer doctype warns.
	Absent → no footer, byte-identical (parity law)."""
	if footer is None:
		return
	if not isinstance(footer, list):
		_hard(layer, _("nav.footer must be a list of nav items"))

	seen = {}
	for item in footer:
		if not isinstance(item, dict):
			_hard(layer, _("every nav.footer item must be an object"))
		doctype = item.get("doctype")
		if not isinstance(doctype, str) or not doctype.strip():
			_hard(layer, _("every nav.footer item must carry a non-empty string 'doctype'"))
		icon = item.get("icon")
		if icon is not None and (not isinstance(icon, str) or not ICON_RE.fullmatch(icon)):
			_hard(layer, _("nav.footer item icon '{0}' must match '^pi pi-[a-z0-9-]+$'").format(icon))
		for key in item:
			if key not in NAV_ITEM_KEYS:
				warnings.append(
					_(
						"{0}: unknown key '{1}' inside nav.footer item '{2}' — the client reads only doctype/icon"
					).format(layer, key, doctype)
				)
		if doctype not in seen:
			_warn_unusable_doctype(layer, "nav.footer", doctype, warnings, catalog)
		seen[doctype] = seen.get(doctype, 0) + 1

	for doctype, count in seen.items():
		if count > 1:
			warnings.append(
				_("{0}: nav.footer doctype '{1}' appears {2} times").format(layer, doctype, count)
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


def _validate_screens(screens, layer, warnings, catalog=None):
	if screens is None:
		return
	if not isinstance(screens, dict):
		_hard(layer, _("screens must be an object"))

	# Item 17 supersedes the old §2.1 silence: a screen key other than "home"
	# (incl. the reserved list:<DocType>/detail:<DocType> forms) renders
	# NOTHING today — a typo like "hme" must not die silently.
	for key in screens:
		if key not in KNOWN_SCREEN_KEYS:
			warnings.append(
				_("{0}: screens['{1}'] is not rendered by any client today (only 'home' is)").format(
					layer, key
				)
			)

	home = screens.get("home")
	if home is None:
		return
	if not isinstance(home, dict):
		_hard(layer, _("screens.home must be an object"))

	for key in home:
		if key not in SCREEN_KEYS:
			warnings.append(_("{0}: unknown key '{1}' inside screens.home").format(layer, key))

	hidden = home.get("hidden")
	if hidden is not None and not isinstance(hidden, dict):
		_hard(layer, _("screens.home.hidden must be an object of booleans"))
	_warn_non_boolean_hidden(hidden, "screens.home.hidden", layer, warnings)

	blocks = home.get("blocks")
	if blocks is None:
		return
	if not isinstance(blocks, list):
		_hard(layer, _("screens.home.blocks must be a list"))

	seen_ids = {}
	for block in blocks:
		if not isinstance(block, dict):
			_hard(layer, _("every home block must be an object"))
		for key in ("id", "type"):
			value = block.get(key)
			if not isinstance(value, str) or not value.strip():
				_hard(layer, _("every home block must carry a non-empty string '{0}'").format(key))
		block_id = block["id"]
		seen_ids[block_id] = seen_ids.get(block_id, 0) + 1
		for key in block:
			if key not in BLOCK_KEYS:
				warnings.append(
					_("{0}: unknown key '{1}' inside block '{2}'").format(layer, key, block_id)
				)
		size = block.get("size")
		if size is not None and size not in BLOCK_SIZES:
			warnings.append(
				_("{0}: block '{1}' size {2!r} is not one of {3} — the client renders it full-width").format(
					layer, block_id, size, ", ".join(BLOCK_SIZES)
				)
			)
		_check_block_props(block, layer, warnings, catalog)

	for block_id, count in seen_ids.items():
		if count > 1:
			warnings.append(
				_(
					"{0}: block id '{1}' appears {2} times — ids must be unique (hidden targeting and rendering keys collide)"
				).format(layer, block_id, count)
			)

	# Layout layer only: a hidden key that names no block id is dead (an id
	# typo silently fails to hide). Overrides legitimately hide LAYOUT blocks,
	# so that layer stays unchecked.
	if layer == "layout" and isinstance(hidden, dict):
		for key in hidden:
			if key not in seen_ids:
				warnings.append(
					_(
						"{0}: screens.home.hidden['{1}'] matches no block id in this layout — it hides nothing"
					).format(layer, key)
				)


def _known_metric_keys():
	"""Metric names the ``ui_metrics`` registry serves, for the summary-tiles
	soft check. Lazily imported so a defect in that module can NEVER hard-fail
	config validation — ``None`` means "unknown, skip the registry check"."""
	try:
		from yrp.yrp.api.ui_metrics import METRICS
	except Exception:
		return None
	return set(METRICS)


def _known_calculation_keys():
	"""Calculation names ``run_ui_calculation`` accepts, for the
	calculator-panel soft check. Same lazy fail-safe contract as
	``_known_metric_keys`` — ``None`` = skip the registry check."""
	try:
		from yrp.yrp.api.ui_metrics import CALCULATIONS
	except Exception:
		return None
	return set(CALCULATIONS)


def _validate_columns(columns, fieldnames, doctype, path, layer, warnings, strings_legal=True):
	"""Column lists (``listViews[dt].columns`` / record-list ``props.columns``).

	The two consumers differ (2026-07-17 review correction): the record-list
	HOME BLOCK accepts "fieldname" strings AND ``{field, label}`` objects
	(RecordList.vue colDescs handles both), but the ROUTED list page reads
	ONLY objects — DynamicListPage ``layoutColumns`` skips any entry without a
	``.field`` property, so a bare string there is silently dropped (and an
	all-string list collapses the whole columns override back to the meta
	defaults). The listViews path passes ``strings_legal=False`` and every
	string entry warns. Both clients silently drop a column whose field is
	not a renderable meta field, so the fieldname typo must die here instead.
	All soft."""
	if not isinstance(columns, list):
		warnings.append(
			_("{0}: {1} columns must be a list of fieldname strings or {{field, label}} objects").format(
				layer, path
			)
		)
		return
	for entry in columns:
		if isinstance(entry, str):
			field = entry
			if not strings_legal:
				warnings.append(
					_(
						"{0}: {1} column '{2}' is a bare fieldname string — the routed list page reads only {{field, label}} objects and DROPS string entries (write {{\"field\": \"{2}\"}}; strings work only in record-list block columns)"
					).format(layer, path, entry)
				)
		elif isinstance(entry, dict):
			field = entry.get("field")
			if not isinstance(field, str) or not field.strip():
				warnings.append(
					_("{0}: {1} column {2!r} needs a non-empty string 'field'").format(
						layer, path, entry
					)
				)
				continue
			label = entry.get("label")
			if label is not None and not isinstance(label, str):
				warnings.append(
					_("{0}: {1} column '{2}' label must be a string").format(layer, path, field)
				)
			for key in entry:
				if key not in COLUMN_KEYS:
					warnings.append(
						_(
							"{0}: {1} column '{2}' key '{3}' is ignored — the client reads only field/label"
						).format(layer, path, field, key)
					)
		else:
			warnings.append(
				_(
					"{0}: {1} column entry {2!r} is neither a fieldname string nor a {{field, label}} object"
				).format(layer, path, entry)
			)
			continue
		if fieldnames is not None and field not in fieldnames:
			warnings.append(
				_("{0}: {1} column '{2}' is not a field on '{3}' — the client drops the column").format(
					layer, path, field, doctype
				)
			)


def _check_block_props(block, layer, warnings, catalog=None):
	"""Per-type prop schemas for the shipped block types (§15 item 3c).

	Failures are soft warnings; unknown block types skip prop validation
	entirely (the client bundle may be newer than this server). Item 17
	deepened the checks: unknown props on a KNOWN type warn (Vue silently
	ignores them), registry names are checked against their registries, and
	doctype-naming props go through the shared catalog gate.
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

	known_props = BLOCK_PROP_KEYS.get(block_type)
	if known_props is not None:
		for key in props:
			if key not in known_props:
				warnings.append(
					_(
						"{0}: block '{1}' prop '{2}' is not a prop of block type '{3}' — the client ignores it"
					).format(layer, block["id"], key, block_type)
				)

	if block_type == "home-queues":
		max_cards = props.get("maxCards")
		if max_cards is not None:
			# Validated since day one, consumed by nothing (HomeQueues reads
			# only `stats`) — RESERVED until Track 1 item 11 wires or deletes it.
			_warn_reserved(layer, _("block '{0}' maxCards").format(block["id"]), warnings)
			if (
				isinstance(max_cards, bool)
				or not isinstance(max_cards, int)
				or not (1 <= max_cards <= 10)
			):
				warnings.append(
					_("{0}: block '{1}' maxCards must be an integer between 1 and 10").format(
						layer, block["id"]
					)
				)
		stats = props.get("stats")
		if stats is not None and (
			not isinstance(stats, list) or not all(isinstance(s, str) for s in stats)
		):
			warnings.append(
				_("{0}: block '{1}' stats must be a list of metric names").format(layer, block["id"])
			)
		elif stats:
			# The 2026-07-17 owner bite: home-queues renders ONLY the four
			# queue-backed metrics (HOME_QUEUE_METRICS = HomeQueues.vue
			# METRIC_TO_QUEUE). A registered KPI key here renders nothing; an
			# unregistered name is a typo. Both must warn, never drop silently.
			known = _known_metric_keys()
			for name in stats:
				if name in HOME_QUEUE_METRICS:
					continue
				if known is not None and name not in known:
					warnings.append(
						_("{0}: block '{1}' stat '{2}' is not a registered metric").format(
							layer, block["id"], name
						)
					)
				else:
					warnings.append(
						_(
							"{0}: block '{1}' stat '{2}' is not a home-queue metric ({3}) — home-queues renders NOTHING for it; put KPI metrics in a summary-tiles block"
						).format(layer, block["id"], name, ", ".join(HOME_QUEUE_METRICS))
					)
	elif block_type in ("home-recent", "home-quick-create"):
		doctypes = props.get("doctypes")
		if doctypes is not None and (
			not isinstance(doctypes, list) or not all(isinstance(d, str) for d in doctypes)
		):
			warnings.append(
				_("{0}: block '{1}' doctypes must be a list of strings").format(layer, block["id"])
			)
		elif doctypes:
			for name in doctypes:
				_warn_unusable_doctype(
					layer, _("block '{0}'").format(block["id"]), name, warnings, catalog
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
		elif isinstance(new_cta, dict):
			# newCta names route through the same catalog gate as quickCreate
			# (HomeGreeting drops entries without a catalog route silently).
			for key in new_cta:
				if key not in NEWCTA_KEYS:
					warnings.append(
						_("{0}: block '{1}' unknown key '{2}' inside newCta").format(
							layer, block["id"], key
						)
					)
			primary = new_cta.get("primary")
			if primary is not None:
				if not isinstance(primary, str) or not primary.strip():
					warnings.append(
						_("{0}: block '{1}' newCta.primary must be a DocType name").format(
							layer, block["id"]
						)
					)
				else:
					_warn_unusable_doctype(
						layer, _("block '{0}' newCta").format(block["id"]), primary, warnings, catalog
					)
			menu = new_cta.get("menu")
			if menu is not None and (
				not isinstance(menu, list) or not all(isinstance(m, str) for m in menu)
			):
				warnings.append(
					_("{0}: block '{1}' newCta.menu must be a list of DocType names").format(
						layer, block["id"]
					)
				)
			elif menu:
				for name in menu:
					_warn_unusable_doctype(
						layer, _("block '{0}' newCta").format(block["id"]), name, warnings, catalog
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
		fieldnames = None
		if not isinstance(doctype, str) or not doctype.strip():
			warnings.append(
				_("{0}: block '{1}' requires a non-empty string 'doctype'").format(
					layer, block["id"]
				)
			)
			doctype = None
		elif not frappe.db.exists("DocType", doctype):
			# No catalog check here — record-list renders any readable DocType
			# (an off-catalog one just loses its "View all" link).
			warnings.append(
				_("{0}: block '{1}' doctype '{2}' does not exist as a DocType").format(
					layer, block["id"], doctype
				)
			)
		else:
			fieldnames = _doctype_fieldnames(doctype)
		variant = props.get("variant")
		if variant is not None and variant not in ("table", "cards", "kanban"):
			warnings.append(
				_("{0}: block '{1}' variant must be 'table', 'cards' or 'kanban'").format(
					layer, block["id"]
				)
			)
		columns = props.get("columns")
		if columns is not None:
			_validate_columns(
				columns, fieldnames, doctype, _("block '{0}'").format(block["id"]), layer, warnings
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
			if value is None:
				continue
			if not isinstance(value, str):
				warnings.append(
					_("{0}: block '{1}' {2} must be a string").format(layer, block["id"], key)
				)
			elif key != "title" and fieldnames is not None and value not in fieldnames:
				# groupBy/titleField silently fall back client-side when they
				# name no meta field (kanban regroups by status, title falls
				# to meta title_field) — the typo must warn here.
				warnings.append(
					_("{0}: block '{1}' {2} '{3}' is not a field on '{4}' — the client falls back").format(
						layer, block["id"], key, value, doctype
					)
				)
		_check_card_template(
			props.get("cardTemplate"),
			props.get("variant"),
			_("block '{0}'").format(block["id"]),
			layer,
			warnings,
			doctype=doctype,
		)
	elif block_type == "composite":
		# Source shape checks, then the DEEP tree validator (Track 1 item 3):
		# primitive whitelist, token enums, bind grammar, caps — see
		# _validate_composite_tree for the hard/soft split.
		source = props.get("source")
		if source is not None and not isinstance(source, dict):
			warnings.append(
				_("{0}: block '{1}' source must be an object").format(layer, block["id"])
			)
		elif isinstance(source, dict):
			for key in source:
				if key not in COMPOSITE_SOURCE_KEYS:
					warnings.append(
						_(
							"{0}: block '{1}' source key '{2}' is ignored — the client reads only {3}"
						).format(layer, block["id"], key, ", ".join(COMPOSITE_SOURCE_KEYS))
					)
			metrics = source.get("metrics")
			if metrics is not None and (
				not isinstance(metrics, list) or not all(isinstance(m, str) for m in metrics)
			):
				warnings.append(
					_("{0}: block '{1}' source.metrics must be a list of strings").format(
						layer, block["id"]
					)
				)
			elif metrics:
				known = _known_metric_keys()
				for name in metrics:
					if known is not None and name not in known:
						warnings.append(
							_("{0}: block '{1}' source metric '{2}' is not a registered metric").format(
								layer, block["id"], name
							)
						)
			doctype = source.get("doctype")
			if doctype is not None:
				if not isinstance(doctype, str) or not doctype.strip():
					warnings.append(
						_("{0}: block '{1}' source.doctype must be a DocType name").format(
							layer, block["id"]
						)
					)
				elif not frappe.db.exists("DocType", doctype):
					# record-list parity: any readable site doctype is legal
					# (no catalog gate — there is no "View all" link here).
					warnings.append(
						_("{0}: block '{1}' source.doctype '{2}' does not exist as a DocType").format(
							layer, block["id"], doctype
						)
					)
			limit = source.get("limit")
			if limit is not None and (
				isinstance(limit, bool) or not isinstance(limit, int) or not (1 <= limit <= 20)
			):
				warnings.append(
					_("{0}: block '{1}' source.limit must be an integer between 1 and 20").format(
						layer, block["id"]
					)
				)
			if limit is not None and not source.get("doctype"):
				warnings.append(
					_(
						"{0}: block '{1}' source.limit does nothing without source.doctype"
					).format(layer, block["id"])
				)
		tree = props.get("tree")
		if tree is None:
			warnings.append(
				_("{0}: block '{1}' has no 'tree' — the composite block renders nothing").format(
					layer, block["id"]
				)
			)
		elif not isinstance(tree, dict) or not isinstance(tree.get("type"), str):
			warnings.append(
				_(
					"{0}: block '{1}' tree must be an object with a string 'type' root node"
				).format(layer, block["id"])
			)
		else:
			# Deep tree validation against the block's own source: the scope
			# roots are metrics.<name>.value|label and rows.<index>.<fieldname>
			# (Composite.vue), so dead bindings are checkable at save time.
			src = source if isinstance(source, dict) else {}
			src_doctype = src.get("doctype")
			if not isinstance(src_doctype, str) or not src_doctype.strip():
				src_doctype = None
			row_fieldnames = None
			if src_doctype and frappe.db.exists("DocType", src_doctype):
				row_fieldnames = _composite_fetchable_fieldnames(src_doctype)
			metrics_val = src.get("metrics")
			if isinstance(metrics_val, list):
				metric_names = {m for m in metrics_val if isinstance(m, str)}
			elif "metrics" in src:
				metric_names = None  # garbage shape already warned — skip the check
			else:
				metric_names = set()  # no metrics fetched — metric binds are dead
			limit = src.get("limit")
			row_limit = limit if isinstance(limit, int) and not isinstance(limit, bool) and 1 <= limit <= 20 else 5
			_validate_composite_tree(
				tree,
				_("block '{0}'").format(block["id"]),
				"tree",
				layer,
				warnings,
				scope="block",
				doctype=src_doctype,
				fieldnames=row_fieldnames,
				metric_names=metric_names,
				row_limit=row_limit,
			)
	elif block_type == "calculator-panel":
		calculation = props.get("calculation")
		if not isinstance(calculation, str) or not calculation.strip():
			warnings.append(
				_("{0}: block '{1}' requires a non-empty string 'calculation'").format(
					layer, block["id"]
				)
			)
		else:
			known = _known_calculation_keys()
			if known is not None and calculation not in known:
				warnings.append(
					_("{0}: block '{1}' calculation '{2}' is not a registered calculation").format(
						layer, block["id"], calculation
					)
				)
		params = props.get("params")
		if params is not None and not isinstance(params, dict):
			warnings.append(
				_("{0}: block '{1}' params must be an object").format(layer, block["id"])
			)
	elif block_type == "story-scroller":
		# USE_CASE §4 item 7. Same soft posture as record-list: a required
		# `source` doctype (exist-checked, no catalog gate — an off-catalog
		# doctype only loses its tap-through route), optional `fields`
		# (meta-checked fieldnames), `limit` (int range), `orientation` (enum).
		source = props.get("source")
		src_fieldnames = None
		if not isinstance(source, str) or not source.strip():
			warnings.append(
				_("{0}: block '{1}' requires a non-empty string 'source' (the DocType to scroll)").format(
					layer, block["id"]
				)
			)
			source = None
		elif not frappe.db.exists("DocType", source):
			warnings.append(
				_("{0}: block '{1}' source '{2}' does not exist as a DocType").format(
					layer, block["id"], source
				)
			)
		else:
			src_fieldnames = _doctype_fieldnames(source)
		fields = props.get("fields")
		if fields is not None:
			if not isinstance(fields, list) or not all(isinstance(f, str) for f in fields):
				warnings.append(
					_("{0}: block '{1}' fields must be a list of fieldname strings").format(
						layer, block["id"]
					)
				)
			elif src_fieldnames is not None:
				for field in fields:
					if field not in src_fieldnames:
						warnings.append(
							_(
								"{0}: block '{1}' field '{2}' is not a renderable field on '{3}' — the client drops it"
							).format(layer, block["id"], field, source)
						)
		limit = props.get("limit")
		if limit is not None and (
			isinstance(limit, bool)
			or not isinstance(limit, int)
			or not (STORY_SCROLLER_LIMIT_MIN <= limit <= STORY_SCROLLER_LIMIT_MAX)
		):
			warnings.append(
				_("{0}: block '{1}' limit must be an integer between {2} and {3}").format(
					layer, block["id"], STORY_SCROLLER_LIMIT_MIN, STORY_SCROLLER_LIMIT_MAX
				)
			)
		orientation = props.get("orientation")
		if orientation is not None and orientation not in STORY_SCROLLER_ORIENTATIONS:
			warnings.append(
				_(
					"{0}: block '{1}' orientation '{2}' is not one of {3} — the client falls back to horizontal"
				).format(layer, block["id"], orientation, ", ".join(STORY_SCROLLER_ORIENTATIONS))
			)


def _check_card_template(card_template, variant, context, layer, warnings, doctype=None):
	"""``cardTemplate`` (Track 1 item 2) — the per-row composite tree of the
	record-list block and ``listViews[<DocType>]``. A non-object template is
	ignored by the clients (default card look); an object without a string
	``type`` root renders the engine's honest can't-render fallback in EVERY
	card — both warn. The clients render the template only in the cards/kanban
	variants, so a template on a (default) table-variant list is dead config.
	A shape-valid root then goes through the DEEP tree validator (Track 1
	item 3) with the ROW record as binding scope: bind paths are plain
	fieldnames, checked against ``doctype``'s fetchable fields — no
	``rows.<i>.`` / ``metrics.`` roots exist here.
	"""
	if card_template is None:
		return
	if not isinstance(card_template, dict) or not isinstance(card_template.get("type"), str):
		warnings.append(
			_(
				"{0}: {1} cardTemplate must be a composite tree object with a string 'type' root node"
			).format(layer, context)
		)
		return
	if variant not in ("cards", "kanban"):
		warnings.append(
			_("{0}: {1} cardTemplate does nothing without variant 'cards' or 'kanban'").format(
				layer, context
			)
		)
	row_fieldnames = None
	if doctype and frappe.db.exists("DocType", doctype):
		row_fieldnames = _composite_fetchable_fieldnames(doctype)
	_validate_composite_tree(
		card_template,
		context,
		"cardTemplate",
		layer,
		warnings,
		scope="row",
		doctype=doctype,
		fieldnames=row_fieldnames,
	)


def _composite_fetchable_fieldnames(doctype):
	"""Fieldnames a composite/cardTemplate binding may name for ``doctype``:
	every meta field with a real DB column (``no_value_fields`` excluded — a
	Table/layout fieldname in the fields param would break the host's getList
	fetch) plus the three fields every host fetch always includes — ``name``,
	``docstatus``, ``modified`` (Composite.vue / RecordList.vue /
	DynamicListPage.vue base fields). Hidden fields ARE fetchable, unlike the
	renderable-column set of ``_doctype_fieldnames``. ``None`` = meta
	unavailable; field-level checks are skipped (same fail-safe contract as
	``_doctype_fieldnames``)."""
	try:
		meta = frappe.get_meta(doctype)
		fields = {df.fieldname for df in meta.fields if df.fieldtype not in no_value_fields}
		return fields | {"name", "docstatus", "modified"}
	except Exception:
		return None


def _composite_tree_stats(tree):
	"""Node count + max depth of a tree, mirroring the engine's treeStats walk
	(binding.js): only plain objects count; every ``children`` array is walked
	regardless of the parent's container-ness. Iterative (never recursive) and
	early-exiting once both caps are exceeded, so a hostile mega-tree cannot
	spin the validator before the hard error fires."""
	nodes = 0
	depth = 0
	stack = [(tree, 1)]
	while stack:
		node, d = stack.pop()
		if not isinstance(node, dict):
			continue
		nodes += 1
		if d > depth:
			depth = d
		if nodes > COMPOSITE_MAX_NODES and depth > COMPOSITE_MAX_DEPTH:
			break  # both caps already blown — the caller hard-errors anyway
		children = node.get("children")
		if isinstance(children, list):
			stack.extend((child, d + 1) for child in children)
	return nodes, depth


def _hard_if_injection_string(value, path, layer):
	"""HARD gate for markup/script-shaped literal strings (§3(d): a layout may
	never contain HTML/CSS/JS strings). The engine would render them inert via
	text interpolation — they are refused anyway because they can only be an
	injection attempt or a copy-paste accident, never authoring."""
	if isinstance(value, str) and COMPOSITE_MARKUP_RE.search(value):
		_hard(
			layer,
			_(
				"{0} {1!r} is markup/script-shaped — HTML/CSS/JS strings are never legal in a layout (§3(d) boundary)"
			).format(path, value),
		)


def _check_composite_path(path_value, path_label, layer, warnings, scope, doctype, fieldnames, metric_names, row_limit):
	"""Dot-path grammar + scope checks for bind paths and showIf fields.

	HARD: prototype-shaped segments (__proto__/prototype/constructor) and
	expression-shaped characters outside the dot-path alphabet — the injection
	families the client resolver refuses. SOFT: everything that merely
	resolves nothing client-side (malformed dot-path, a root outside the
	host-supplied scope, a dead metric/row binding, a fieldname typo) — the
	client renders the honest em-dash, but the save must say so."""
	segments = path_value.split(".")
	if any(seg in COMPOSITE_FORBIDDEN_PATH_SEGMENTS for seg in segments):
		_hard(
			layer,
			_(
				"{0} '{1}' contains a prototype-shaped segment ({2}) — refused"
			).format(path_label, path_value, "/".join(COMPOSITE_FORBIDDEN_PATH_SEGMENTS)),
		)
	if not COMPOSITE_BIND_CHARSET_RE.fullmatch(path_value):
		_hard(
			layer,
			_(
				"{0} '{1}' is expression-shaped — a dot-path may only carry letters, digits, '_', '-' and '.'"
			).format(path_label, path_value),
		)
	if not COMPOSITE_BIND_PATH_RE.fullmatch(path_value):
		warnings.append(
			_(
				"{0}: {1} '{2}' is a malformed dot-path — the client resolves nothing (em-dash)"
			).format(layer, path_label, path_value)
		)
		return

	if scope == "row":
		# cardTemplate scope = the flat ROW record: bind fieldnames directly.
		if len(segments) > 1:
			warnings.append(
				_(
					"{0}: {1} '{2}' — row records are flat; bind the fieldname directly (no rows./metrics. roots here)"
				).format(layer, path_label, path_value)
			)
		elif fieldnames is not None and segments[0] not in fieldnames:
			warnings.append(
				_(
					"{0}: {1} '{2}' is not a fetchable field on '{3}' — the binding renders the em-dash"
				).format(layer, path_label, path_value, doctype)
			)
		return

	# Composite-block scope: metrics.<name>.value|label and rows.<i>.<field>.
	root = segments[0]
	if root == "metrics":
		if len(segments) != 3 or segments[2] not in ("value", "label"):
			warnings.append(
				_(
					"{0}: {1} '{2}' — metric paths are metrics.<name>.value or metrics.<name>.label; anything else resolves nothing"
				).format(layer, path_label, path_value)
			)
		elif metric_names is not None and segments[1] not in metric_names:
			warnings.append(
				_(
					"{0}: {1} metric '{2}' is not in source.metrics — the binding always renders the em-dash"
				).format(layer, path_label, segments[1])
			)
	elif root == "rows":
		if doctype is None:
			warnings.append(
				_(
					"{0}: {1} '{2}' binds rows.* but source.doctype is not set — no rows are fetched; the binding renders the em-dash"
				).format(layer, path_label, path_value)
			)
		if len(segments) != 3 or not segments[1].isdigit():
			warnings.append(
				_(
					"{0}: {1} '{2}' — row paths are rows.<index>.<fieldname> (rows are flat); anything else resolves nothing"
				).format(layer, path_label, path_value)
			)
		else:
			if int(segments[1]) >= row_limit:
				warnings.append(
					_(
						"{0}: {1} row index {2} is beyond source.limit ({3}) — the binding always renders the em-dash"
					).format(layer, path_label, segments[1], row_limit)
				)
			if doctype is not None and fieldnames is not None and segments[2] not in fieldnames:
				warnings.append(
					_(
						"{0}: {1} '{2}' is not a fetchable field on '{3}' — the binding renders the em-dash"
					).format(layer, path_label, segments[2], doctype)
				)
	else:
		warnings.append(
			_(
				"{0}: {1} '{2}' — the composite scope has only metrics.* and rows.* roots; the binding resolves nothing"
			).format(layer, path_label, path_value)
		)


def _check_composite_bindable(value, prop_path, layer, warnings, path_kwargs):
	"""One bindable/bindable-number prop value: literal scalars pass (markup
	strings hard-fail); a {bind, format} object gets the path grammar + named
	formatter checks; any other object renders nothing and warns."""
	if isinstance(value, dict):
		bind = value.get("bind")
		if not isinstance(bind, str) or not bind:
			warnings.append(
				_(
					"{0}: {1} object needs a string 'bind' dot-path — the client renders nothing for it"
				).format(layer, prop_path)
			)
		else:
			_check_composite_path(bind, _("{0}.bind").format(prop_path), layer, warnings, **path_kwargs)
		fmt = value.get("format")
		if fmt is not None:
			_hard_if_injection_string(fmt, _("{0}.format").format(prop_path), layer)
			if fmt not in COMPOSITE_FORMATS:
				warnings.append(
					_(
						"{0}: {1} format {2!r} is not one of {3} — the client renders the raw value"
					).format(layer, prop_path, fmt, ", ".join(COMPOSITE_FORMATS))
				)
		for key in value:
			if key not in COMPOSITE_BINDING_KEYS:
				warnings.append(
					_("{0}: unknown key '{1}' inside {2} binding — the client reads only bind/format").format(
						layer, key, prop_path
					)
				)
	elif isinstance(value, str):
		_hard_if_injection_string(value, prop_path, layer)
	elif isinstance(value, list):
		warnings.append(
			_(
				"{0}: {1} must be a literal scalar or a {{bind}} object — the client renders nothing for it"
			).format(layer, prop_path)
		)


def _injection_scan(value, path, layer, budget=100):
	"""Bounded recursive scan of an UNKNOWN prop's value: every literal string
	still hard-fails on markup shapes and every {bind: ...} path still gets the
	prototype/expression hard gates — an unknown prop name must never become a
	smuggling lane past the injection posture (the client ignores the prop, but
	the §3(d) boundary is about what a layout may CONTAIN)."""
	stack = [(value, path)]
	seen = 0
	while stack and seen < budget:
		current, current_path = stack.pop()
		seen += 1
		if isinstance(current, str):
			_hard_if_injection_string(current, current_path, layer)
		elif isinstance(current, dict):
			bind = current.get("bind")
			if isinstance(bind, str):
				segments = bind.split(".")
				if any(seg in COMPOSITE_FORBIDDEN_PATH_SEGMENTS for seg in segments):
					_hard(
						layer,
						_("{0}.bind '{1}' contains a prototype-shaped segment — refused").format(
							current_path, bind
						),
					)
			for key, val in current.items():
				stack.append((val, _("{0}.{1}").format(current_path, key)))
		elif isinstance(current, list):
			for i, item in enumerate(current):
				stack.append((item, _("{0}.{1}").format(current_path, i)))


def _validate_composite_tree(
	tree,
	context,
	root_label,
	layer,
	warnings,
	scope,
	doctype=None,
	fieldnames=None,
	metric_names=None,
	row_limit=5,
):
	"""Deep composite-tree validation (USE_CASE §4 Track 1 item 3), applied to
	every seam a tree can appear in: the composite block's ``tree`` and both
	``cardTemplate`` seams. The caller has already checked the root shape
	(object with a string ``type``).

	HARD (blocks the save): grammar version newer than this server, node/depth
	caps, and every injection-shaped value — markup/script literal strings,
	prototype/expression-shaped paths, scheme/traversal/protocol-relative
	image srcs. SOFT (client keeps its honest fallback): unknown primitives,
	off-vocabulary tokens, bad formatter names, malformed showIf triples,
	dead bindings. ``scope`` is ``"block"`` (metrics.*/rows.* roots) or
	``"row"`` (flat row record)."""
	# Grammar version — root-node only, mirroring the schema_version posture:
	# never guess-interpreted forward.
	version = tree.get("version")
	if version is not None:
		if isinstance(version, bool) or not isinstance(version, int) or version < 1:
			_hard(
				layer,
				_(
					"{0} {1}.version must be a positive integer (current composite grammar: {2})"
				).format(context, root_label, COMPOSITE_GRAMMAR_VERSION),
			)
		if version > COMPOSITE_GRAMMAR_VERSION:
			_hard(
				layer,
				_(
					"{0} {1}.version {2} is newer than this server's composite grammar ({3})"
				).format(context, root_label, version, COMPOSITE_GRAMMAR_VERSION),
			)

	# Caps BEFORE the deep walk (hard — the engine renders NOTHING over-cap),
	# via a bounded iterative count so hostile trees cannot recurse or spin.
	nodes, depth = _composite_tree_stats(tree)
	if nodes > COMPOSITE_MAX_NODES:
		_hard(
			layer,
			_(
				"{0} {1} has more than {2} nodes — over the composite cap; the engine renders NOTHING over-cap"
			).format(context, root_label, COMPOSITE_MAX_NODES),
		)
	if depth > COMPOSITE_MAX_DEPTH:
		_hard(
			layer,
			_(
				"{0} {1} is nested deeper than {2} levels — over the composite cap; the engine renders NOTHING over-cap"
			).format(context, root_label, COMPOSITE_MAX_DEPTH),
		)

	path_kwargs = {
		"scope": scope,
		"doctype": doctype,
		"fieldnames": fieldnames,
		"metric_names": metric_names,
		"row_limit": row_limit,
	}
	prefix = _("{0} ").format(context) if context else ""
	# Recursion is safe here: the depth cap above bounds the walk at
	# COMPOSITE_MAX_DEPTH + 1 frames.
	_walk_composite_node(tree, prefix + root_label, True, layer, warnings, path_kwargs)


def _walk_composite_node(node, path, is_root, layer, warnings, path_kwargs):
	if not isinstance(node, dict):
		warnings.append(
			_(
				"{0}: {1} is not a node object — the client renders a path-labelled honest fallback"
			).format(layer, path)
		)
		return

	allowed_keys = COMPOSITE_ROOT_KEYS if is_root else COMPOSITE_NODE_KEYS
	for key in node:
		if key not in allowed_keys:
			warnings.append(
				_("{0}: unknown key '{1}' at {2} — nodes carry only {3}").format(
					layer, key, path, "/".join(allowed_keys)
				)
			)

	ntype = node.get("type")
	spec = None
	if not isinstance(ntype, str) or not ntype.strip():
		warnings.append(
			_(
				"{0}: {1} has no string 'type' — the client renders a path-labelled honest fallback"
			).format(layer, path)
		)
	else:
		_hard_if_injection_string(ntype, _("{0}.type").format(path), layer)
		spec = COMPOSITE_PRIMITIVES.get(ntype)
		if spec is None:
			warnings.append(
				_(
					"{0}: unknown primitive '{1}' at {2} — the client renders a path-labelled honest fallback"
				).format(layer, ntype, path)
			)

	props = node.get("props")
	if props is not None and not isinstance(props, dict):
		warnings.append(_("{0}: {1}.props must be an object").format(layer, path))
		props = None
	if isinstance(props, dict):
		for name, value in props.items():
			prop_path = _("{0}.props.{1}").format(path, name)
			if spec is None:
				# Unknown primitive: no prop vocabulary to check against, but
				# the injection posture still applies to everything inside.
				_injection_scan(value, prop_path, layer)
				continue
			pspec = spec["props"].get(name)
			if pspec is None:
				warnings.append(
					_(
						"{0}: '{1}' is not a prop of primitive '{2}' at {3} — the client ignores it"
					).format(layer, name, ntype, path)
				)
				_injection_scan(value, prop_path, layer)
				continue
			_validate_composite_prop(value, pspec, prop_path, layer, warnings, path_kwargs)

	children = node.get("children")
	if children is not None:
		if not isinstance(children, list):
			warnings.append(_("{0}: {1}.children must be a list of nodes").format(layer, path))
		else:
			if spec is not None and not spec["container"]:
				warnings.append(
					_(
						"{0}: '{1}' at {2} is not a container ({3}) — the client ignores its children"
					).format(layer, ntype, path, "/".join(COMPOSITE_CONTAINER_PRIMITIVES))
				)
			for i, child in enumerate(children):
				_walk_composite_node(
					child, _("{0}.children.{1}").format(path, i), False, layer, warnings, path_kwargs
				)

	_check_composite_showif(node.get("showIf"), _("{0}.showIf").format(path), layer, warnings, path_kwargs)


def _validate_composite_prop(value, pspec, prop_path, layer, warnings, path_kwargs):
	kind = pspec["kind"]
	if kind in ("bindable", "bindable-number"):
		_check_composite_bindable(value, prop_path, layer, warnings, path_kwargs)
	elif kind == "enum":
		if value not in pspec["values"]:
			_hard_if_injection_string(value, prop_path, layer)
			warnings.append(
				_("{0}: {1} {2!r} is not one of {3} — the client falls back to {4!r}").format(
					layer, prop_path, value, ", ".join(pspec["values"]), pspec["default"]
				)
			)
	elif kind == "boolean":
		if not isinstance(value, bool):
			warnings.append(
				_("{0}: {1} should be a boolean, got {2} — the client keeps the default").format(
					layer, prop_path, type(value).__name__
				)
			)
	elif kind == "int":
		if isinstance(value, bool) or not isinstance(value, int) or not (
			pspec["min"] <= value <= pspec["max"]
		):
			warnings.append(
				_(
					"{0}: {1} must be an integer between {2} and {3} — the client falls back"
				).format(layer, prop_path, pspec["min"], pspec["max"])
			)
	elif kind == "string":
		if not isinstance(value, str):
			warnings.append(_("{0}: {1} must be a string").format(layer, prop_path))
		else:
			_hard_if_injection_string(value, prop_path, layer)
	elif kind == "icon":
		if not isinstance(value, str):
			warnings.append(_("{0}: {1} must be an icon class string").format(layer, prop_path))
		else:
			_hard_if_injection_string(value, prop_path, layer)
			if not ICON_RE.fullmatch(value):
				warnings.append(
					_(
						"{0}: {1} '{2}' must match '^pi pi-[a-z0-9-]+$' — the client renders nothing for it"
					).format(layer, prop_path, value)
				)
	elif kind == "site-file":
		if isinstance(value, dict):
			warnings.append(
				_(
					"{0}: {1} is STATIC only — bindings are refused; the client renders its honest fallback"
				).format(layer, prop_path)
			)
			_injection_scan(value, prop_path, layer)
		elif not isinstance(value, str):
			warnings.append(
				_("{0}: {1} must be a static site-file path string").format(layer, prop_path)
			)
		elif ":" in value or ".." in value or "\\" in value or value.startswith("//"):
			# Scheme, traversal, backslash or protocol-relative — the injection
			# shapes: an external/derived URL can never be a site file.
			_hard(
				layer,
				_(
					"{0} '{1}' is not a site file — schemes, '..' and '//' are refused; use a static /files/ or /private/files/ path"
				).format(prop_path, value),
			)
		elif not COMPOSITE_SITE_FILE_RE.fullmatch(value):
			warnings.append(
				_(
					"{0}: {1} '{2}' is not a site /files/ path — the client renders its honest fallback"
				).format(layer, prop_path, value)
			)


def _check_composite_showif(show_if, path, layer, warnings, path_kwargs):
	"""showIf triples are presentation, never permission — the engine FAILS
	OPEN on malformed ones (renders the node + console.warn), so malformed
	shapes are soft; the field's path still gets the hard injection gates."""
	if show_if is None:
		return
	if not isinstance(show_if, dict):
		warnings.append(
			_(
				"{0}: {1} must be a {{field, op, value}} triple — malformed showIf fails OPEN (the node always renders)"
			).format(layer, path)
		)
		return
	for key in show_if:
		if key not in COMPOSITE_SHOWIF_KEYS:
			warnings.append(
				_("{0}: unknown key '{1}' inside {2} — showIf reads only field/op/value").format(
					layer, key, path
				)
			)
	field = show_if.get("field")
	if not isinstance(field, str) or not field:
		warnings.append(
			_(
				"{0}: {1}.field must be a dot-path string — malformed showIf fails OPEN (the node always renders)"
			).format(layer, path)
		)
	else:
		_check_composite_path(field, _("{0}.field").format(path), layer, warnings, **path_kwargs)
	op = show_if.get("op")
	if op not in COMPOSITE_SHOWIF_OPS:
		_hard_if_injection_string(op, _("{0}.op").format(path), layer)
		warnings.append(
			_(
				"{0}: {1}.op {2!r} is not one of {3} — malformed showIf fails OPEN (the node always renders)"
			).format(layer, path, op, ", ".join(COMPOSITE_SHOWIF_OPS))
		)
	value = show_if.get("value")
	if isinstance(value, str):
		_hard_if_injection_string(value, _("{0}.value").format(path), layer)
	elif value is not None and not isinstance(value, (int, float, bool)):
		warnings.append(
			_("{0}: {1}.value must be a scalar — the comparison never matches").format(layer, path)
		)


def _validate_list_table_flags(view, variant, fieldnames, doctype, layer, warnings):
	"""listViews table-renderer flags (USE_CASE §4 Track 1 item 6). ALL SOFT:
	absent = today's table (parity law); an off-vocabulary value is ignored
	client-side and the shipped look kept. cards/kanban absorb these looks via
	cardTemplate, so a flag set on those variants is dead config and warns
	(item-17 no-silent-drop). ``fieldnames`` is the doctype's renderable meta
	set (None = meta unavailable → the colourBy field check is skipped)."""
	present = [flag for flag in LIST_TABLE_FLAGS if view.get(flag) is not None]
	if not present:
		return

	# The table flags only shape the TABLE renderer; on an explicit card variant
	# they do nothing (cardTemplate is the seam there). variant absent/off-vocab
	# resolves to the table, so only the explicit card variants warn.
	if variant in ("cards", "kanban"):
		warnings.append(
			_(
				"{0}: listViews['{1}'] table flags ({2}) apply to the table renderer only — the '{3}' variant absorbs these looks via cardTemplate"
			).format(layer, doctype, ", ".join(present), variant)
		)

	row_size = view.get("rowSize")
	if row_size is not None and row_size not in LIST_ROW_SIZES:
		_warn_off_vocabulary(
			layer, "listViews['{0}'].rowSize".format(doctype), row_size, LIST_ROW_SIZES, "cozy", warnings
		)

	chip_style = view.get("chipStyle")
	if chip_style is not None and chip_style not in LIST_CHIP_STYLES:
		_warn_off_vocabulary(
			layer,
			"listViews['{0}'].chipStyle".format(doctype),
			chip_style,
			LIST_CHIP_STYLES,
			"chip",
			warnings,
		)

	colour_by = view.get("colourBy")
	if colour_by is not None:
		if not isinstance(colour_by, str) or not colour_by.strip():
			warnings.append(
				_("{0}: listViews['{1}'].colourBy must be a fieldname string or 'status'").format(
					layer, doctype
				)
			)
		elif colour_by != LIST_COLOUR_STATUS and fieldnames is not None and colour_by not in fieldnames:
			warnings.append(
				_(
					"{0}: listViews['{1}'].colourBy '{2}' is not a field on '{1}' (or the 'status' keyword) — the client falls back to no row colour"
				).format(layer, doctype, colour_by)
			)

	for flag in ("monoId", "headerBand", "edgeStatus"):
		value = view.get(flag)
		if value is not None and not isinstance(value, bool):
			warnings.append(
				_(
					"{0}: listViews['{1}'].{2} should be a boolean, got {3} — the client keeps the default"
				).format(layer, doctype, flag, type(value).__name__)
			)


def _validate_list_views(list_views, layer, warnings, catalog=None):
	"""Deep listViews validation (item 17). The client resolves
	``listViews[<DocType>]`` per catalog route and silently drops anything it
	cannot use (unknown doctype key, non-object value, off-vocabulary variant,
	columns/groupBy/titleField naming no meta field) — every one of those
	families warns here now. Hard error only for the pre-existing shape rule."""
	if list_views is None:
		return
	if not isinstance(list_views, dict):
		_hard(layer, _("listViews must be an object keyed by DocType"))

	for doctype, view in list_views.items():
		if _warn_unusable_doctype(layer, "listViews", doctype, warnings, catalog):
			fieldnames = None
		else:
			fieldnames = _doctype_fieldnames(doctype)
		if view is None:
			continue  # null = no opinion (the merge skips it)
		if not isinstance(view, dict):
			warnings.append(
				_("{0}: listViews['{1}'] must be an object — the client ignores it").format(
					layer, doctype
				)
			)
			continue
		for key in view:
			if key not in LIST_VIEW_KEYS:
				warnings.append(
					_("{0}: unknown key '{1}' inside listViews['{2}']").format(layer, key, doctype)
				)
		variant = view.get("variant")
		if variant is not None and variant not in LIST_VIEW_VARIANTS:
			_warn_off_vocabulary(
				layer,
				"listViews['{0}'].variant".format(doctype),
				variant,
				LIST_VIEW_VARIANTS,
				"table",
				warnings,
			)
		columns = view.get("columns")
		if columns is not None:
			# strings_legal=False: DynamicListPage.layoutColumns drops bare
			# string entries — only {field, label} objects render here.
			_validate_columns(
				columns,
				fieldnames,
				doctype,
				"listViews['{0}']".format(doctype),
				layer,
				warnings,
				strings_legal=False,
			)
		for key in ("groupBy", "titleField"):
			value = view.get(key)
			if value is None:
				continue
			if not isinstance(value, str) or not value.strip():
				warnings.append(
					_("{0}: listViews['{1}'].{2} must be a fieldname string").format(
						layer, doctype, key
					)
				)
			elif fieldnames is not None and value not in fieldnames:
				warnings.append(
					_(
						"{0}: listViews['{1}'].{2} '{3}' is not a field on '{1}' — the client falls back"
					).format(layer, doctype, key, value)
				)
		_validate_list_table_flags(view, variant, fieldnames, doctype, layer, warnings)
		_check_card_template(
			view.get("cardTemplate"),
			view.get("variant"),
			"listViews['{0}']".format(doctype),
			layer,
			warnings,
			doctype=doctype,
		)


def _validate_quick_create(quick_create, layer, warnings, catalog=None):
	if quick_create is None:
		return
	if not isinstance(quick_create, list):
		_hard(layer, _("quickCreate must be a list of DocType names"))
	for entry in quick_create:
		if not isinstance(entry, str):
			warnings.append(_("{0}: quickCreate entry {1!r} is not a string").format(layer, entry))
		else:
			# Soft, same rule as nav items: the client catalog is the real
			# gate; a typo or off-catalog name just drops the entry — but the
			# SM should hear about it at save.
			_warn_unusable_doctype(layer, "quickCreate", entry, warnings, catalog)


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
	consumes `enabled` today; `intervalMs`/`toast` are RESERVED knob names —
	their presence gets the explicit item-17 notice on top of the type check)."""
	if realtime is None:
		return
	if not isinstance(realtime, dict):
		warnings.append(_("{0}: realtime must be an object — the client ignores it").format(layer))
		return
	for key in realtime:
		if key in REALTIME_RESERVED_KEYS:
			_warn_reserved(layer, "realtime.{0}".format(key), warnings)
		elif key not in REALTIME_KEYS:
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

	_validate_detail_related(detail.get("related"), layer, warnings)

	for key in detail:
		if key in DETAIL_RESERVED_KEYS:
			_warn_reserved(layer, "detail.{0}".format(key), warnings)
		elif key not in DETAIL_KEYS:
			warnings.append(_("{0}: unknown key '{1}' inside detail").format(layer, key))


def _validate_detail_related(related, layer, warnings):
	"""``detail.related`` (2026-07-21) — the single-record cross-DocType
	workbench. An object keyed by the SOURCE DocType (the doctype whose detail
	screen the sets render on); each value is a list of related-record sets that
	compose the open document's linked rows of one OTHER DocType into the same
	screen. Arrangement never grants capability: the client fetches every set via
	the permission-gated ``get_related`` (frappe.has_permission + get_list), so a
	user without read on the linked doctype simply sees nothing.

	SOFT for every drift (unknown/unusable doctype, a fieldname naming no meta
	field, off-range limit) — the client degrades to the honest empty section.
	HARD only for the shape rule and markup-shaped title strings (§3(d))."""
	if related is None:
		return
	if not isinstance(related, dict):
		_hard(layer, _("detail.related must be an object keyed by source DocType"))

	catalog = _web_doctype_catalog()
	for source_doctype, sets in related.items():
		if _warn_unusable_doctype(layer, "detail.related", source_doctype, warnings, catalog):
			source_fieldnames = None
		else:
			source_fieldnames = _composite_fetchable_fieldnames(source_doctype)
		if sets is None:
			continue
		if not isinstance(sets, list):
			warnings.append(
				_(
					"{0}: detail.related['{1}'] must be a list of related-record sets — the client ignores it"
				).format(layer, source_doctype)
			)
			continue
		if len(sets) > DETAIL_RELATED_MAX_SETS:
			warnings.append(
				_(
					"{0}: detail.related['{1}'] has {2} sets — each is a separate fetch on detail open; keep it under {3}"
				).format(layer, source_doctype, len(sets), DETAIL_RELATED_MAX_SETS)
			)
		for i, entry in enumerate(sets):
			_validate_detail_related_entry(
				entry, source_doctype, source_fieldnames, i, layer, warnings
			)


def _validate_detail_related_entry(entry, source_doctype, source_fieldnames, index, layer, warnings):
	"""One related-record set inside ``detail.related[<SourceDocType>]``. Names a
	linked ``doctype``, the ``fromField`` (on the source) supplying the filter
	value, the ``filterField`` (on the linked doctype) to match, an optional
	``title`` and ``limit``, and an optional row-scoped ``cardTemplate`` shaping
	each linked card (same deep-validated grammar as listViews cardTemplate)."""
	context = "detail.related['{0}'][{1}]".format(source_doctype, index)
	if not isinstance(entry, dict):
		warnings.append(
			_("{0}: {1} must be an object — the client ignores it").format(layer, context)
		)
		return

	for key in entry:
		if key not in DETAIL_RELATED_ENTRY_KEYS:
			warnings.append(_("{0}: unknown key '{1}' inside {2}").format(layer, key, context))

	# Linked doctype — required; EXISTENCE-checked only (NOT catalog-gated: a
	# related doctype is fetched and rendered INLINE, never routed by /web, so it
	# needs no /web route — unlike a nav item or listViews key).
	target_doctype = entry.get("doctype")
	target_fieldnames = None
	if not isinstance(target_doctype, str) or not target_doctype.strip():
		warnings.append(
			_("{0}: {1}.doctype is required and must be a DocType name string").format(layer, context)
		)
	elif not frappe.db.exists("DocType", target_doctype):
		warnings.append(
			_("{0}: {1}.doctype '{2}' does not exist as a DocType").format(
				layer, context, target_doctype
			)
		)
	else:
		target_fieldnames = _composite_fetchable_fieldnames(target_doctype)

	# filterField — a fetchable field on the LINKED doctype ('name' always legal).
	filter_field = entry.get("filterField")
	if not isinstance(filter_field, str) or not filter_field.strip():
		warnings.append(
			_("{0}: {1}.filterField is required and must be a fieldname string").format(layer, context)
		)
	elif target_fieldnames is not None and filter_field not in target_fieldnames:
		warnings.append(
			_(
				"{0}: {1}.filterField '{2}' is not a field on '{3}' — the client fetches nothing"
			).format(layer, context, filter_field, target_doctype)
		)

	# fromField — a fetchable field on the SOURCE doctype ('name' always legal).
	from_field = entry.get("fromField")
	if not isinstance(from_field, str) or not from_field.strip():
		warnings.append(
			_("{0}: {1}.fromField is required and must be a fieldname string").format(layer, context)
		)
	elif source_fieldnames is not None and from_field not in source_fieldnames:
		warnings.append(
			_(
				"{0}: {1}.fromField '{2}' is not a field on '{3}' — the client has no value to filter on"
			).format(layer, context, from_field, source_doctype)
		)

	# title — optional plain text; markup-shaped strings hard-fail (§3(d)).
	title = entry.get("title")
	if title is not None:
		if not isinstance(title, str):
			warnings.append(
				_("{0}: {1}.title must be a string").format(layer, context)
			)
		else:
			_hard_if_injection_string(title, context + ".title", layer)

	# limit — optional int in 1..DETAIL_RELATED_MAX_LIMIT.
	limit = entry.get("limit")
	if limit is not None:
		if isinstance(limit, bool) or not isinstance(limit, int) or not (1 <= limit <= DETAIL_RELATED_MAX_LIMIT):
			warnings.append(
				_(
					"{0}: {1}.limit must be an integer 1–{2} — the client falls back to {3}"
				).format(layer, context, DETAIL_RELATED_MAX_LIMIT, DETAIL_RELATED_DEFAULT_LIMIT)
			)

	# cardTemplate — optional row-scoped composite tree over the LINKED doctype.
	card_template = entry.get("cardTemplate")
	if card_template is not None:
		if not isinstance(card_template, dict) or not isinstance(card_template.get("type"), str):
			warnings.append(
				_(
					"{0}: {1}.cardTemplate must be a composite tree object with a string 'type' root node"
				).format(layer, context)
			)
		else:
			_validate_composite_tree(
				card_template,
				context,
				"cardTemplate",
				layer,
				warnings,
				scope="row",
				doctype=target_doctype if isinstance(target_doctype, str) else None,
				fieldnames=target_fieldnames,
			)


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
	``dialogPosition`` is CONSUMED (USE_CASE §4 item 9 wired the actions
	dialog/drawer anchor): vocabulary-checked against the 9-position overlay
	grid, an off-vocabulary value soft-warns (client centers the dialog)."""
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

	# Presentation modes (DESIGN_PREMIUM §4(i)) — soft enum checks, top level
	# only (the engine reads them from the base theme; scheme-neutral).
	arrows = theme.get("arrows")
	if arrows is not None and arrows not in THEME_ARROWS:
		warnings.append(
			_("{0}: theme.arrows {1!r} is not one of {2} — the client will ignore it").format(
				layer, arrows, ", ".join(THEME_ARROWS)
			)
		)
	section_headers = theme.get("sectionHeaders")
	if section_headers is not None and section_headers not in THEME_SECTION_HEADERS:
		warnings.append(
			_("{0}: theme.sectionHeaders {1!r} is not one of {2} — the client will ignore it").format(
				layer, section_headers, ", ".join(THEME_SECTION_HEADERS)
			)
		)

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
		# Scheme-neutral presentation modes are read from the TOP level only —
		# say it out loud instead of silently dropping (item-17 posture).
		for key in ("arrows", "sectionHeaders"):
			if dark.get(key) is not None:
				warnings.append(
					_(
						"{0}: theme.dark.{1} does nothing — {1} is scheme-neutral; set theme.{1} instead"
					).format(layer, key)
				)
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

	# theme.density (USE_CASE §4 item 10, un-inerted 2026-07-18): the engine emits
	# --yrp-pad/--yrp-gap/--yrp-row and the host now consumes them (card padding +
	# stack gaps + table row height). A valid value is silent; only an off-vocab
	# value warns softly.
	density = t.get("density")
	if density is not None and density not in THEME_DENSITIES:
		warn("density", density, _("one of {0}").format(", ".join(THEME_DENSITIES)))

	# theme.focus — the focus-ring colour token (USE_CASE §4 item 10, un-inerted
	# 2026-07-18): the engine emits --yrp-focus (+ --yrp-focus-soft) and the host
	# recolours the input focus ring + a gated :focus-visible outline. Validated
	# as a colour (same forms as the palette tokens); works inside theme.dark too.
	# A valid colour is silent; an off-form value warns softly (never blocks).
	focus = t.get("focus")
	if focus is not None and (
		not isinstance(focus, str)
		or not (ACCENT_RE.fullmatch(focus) or THEME_RGBA_RE.fullmatch(focus))
	):
		warn("focus", focus, _("'#rrggbb' or 'rgba(r, g, b[, a])'"))

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

	_upgrade_composite_trees(cfg, label, warnings)

	return cfg


def _iter_composite_tree_sites(cfg):
	"""Yield ``(holder_dict, key, context_label)`` for every slot in a config
	layer that may carry a composite tree: the composite block's ``tree``, the
	record-list block's ``cardTemplate`` and ``listViews[<DocType>].cardTemplate``.
	Shape-tolerant walking only — resolver inputs are unvalidated data and this
	runs inside the never-raises path."""
	screens = cfg.get("screens")
	home = screens.get("home") if isinstance(screens, dict) else None
	blocks = home.get("blocks") if isinstance(home, dict) else None
	if isinstance(blocks, list):
		for block in blocks:
			if not isinstance(block, dict) or not isinstance(block.get("props"), dict):
				continue
			block_type = block.get("type")
			if block_type == "composite" and "tree" in block["props"]:
				yield block["props"], "tree", _("block '{0}' tree").format(block.get("id"))
			elif block_type == "record-list" and "cardTemplate" in block["props"]:
				yield (
					block["props"],
					"cardTemplate",
					_("block '{0}' cardTemplate").format(block.get("id")),
				)
	list_views = cfg.get("listViews")
	if isinstance(list_views, dict):
		for doctype, view in list_views.items():
			if isinstance(view, dict) and "cardTemplate" in view:
				yield view, "cardTemplate", _("listViews['{0}'] cardTemplate").format(doctype)


def _drop_composite_tree(holder, key, label, context, reason, warnings, sample=None):
	"""Drop ONE composite tree (set to null — the merge treats null as "no
	opinion", so the client renders the shipped default look / nothing for
	that slot) with a ``meta.warnings`` entry + Error Log trace. The rest of
	the layer survives — the layer-drop posture, one tier down."""
	holder[key] = None
	message = _("{0}: {1} dropped: {2}").format(label, context, reason)
	warnings.append(message)
	_log_degradation(_("UI config: {0}").format(message), sample)


def _upgrade_composite_trees(cfg, label, warnings):
	"""Composite-grammar version gate + upgrade at read time (§3(d) / review
	amendment 4 — the composite twin of the schema_version loop above, run on
	every prepared layer). Today ``COMPOSITE_GRAMMAR_VERSION`` is 1 and no
	upgraders exist, so every stored tree passes through UNTOUCHED (parity:
	resolution output is byte-identical); the machinery and its tests exist
	NOW so the first real grammar change only has to bump the version and
	register ``COMPOSITE_TREE_UPGRADERS[N]``. A tree that cannot be brought to
	the current grammar is dropped ALONE — never guess-interpreted forward,
	never the whole layer."""
	for holder, key, context in _iter_composite_tree_sites(cfg):
		tree = holder.get(key)
		if not isinstance(tree, dict):
			continue  # save-time validation already warns on shape defects

		version = tree.get("version")
		if version is None:
			version = 1  # absent = the grammar's first version (§2.3 rule 5 posture)
		elif isinstance(version, bool) or not isinstance(version, int) or version < 1:
			_drop_composite_tree(
				holder,
				key,
				label,
				context,
				_("composite version {0!r} is not a positive integer").format(version),
				warnings,
			)
			continue

		if version > COMPOSITE_GRAMMAR_VERSION:
			_drop_composite_tree(
				holder,
				key,
				label,
				context,
				_("composite version {0} is newer than this server understands ({1})").format(
					version, COMPOSITE_GRAMMAR_VERSION
				),
				warnings,
			)
			continue

		while version < COMPOSITE_GRAMMAR_VERSION:
			upgrader = COMPOSITE_TREE_UPGRADERS.get(version)
			if upgrader is None:
				_drop_composite_tree(
					holder,
					key,
					label,
					context,
					_("no composite upgrader from version {0}").format(version),
					warnings,
				)
				break
			try:
				tree = upgrader(tree)
			except Exception:
				_drop_composite_tree(
					holder,
					key,
					label,
					context,
					_("composite upgrader from version {0} failed").format(version),
					warnings,
					sample=frappe.get_traceback(),
				)
				break
			version += 1
			tree["version"] = version
			holder[key] = tree


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


_FIELDNAME_RE = re.compile(r"^[a-z0-9_]+$")


@frappe.whitelist()
def get_related(doctype, filter_field, filter_value, fields=None, limit=None):
	"""Permission-gated fetch of one document's linked rows for the
	``detail.related`` single-record workbench (case (b)).

	Returns the rows of ``doctype`` where ``filter_field == filter_value`` — the
	open document's related records of one OTHER DocType, composed into its
	detail screen by the client. **Arrangement never grants capability** (§15):

	* ``frappe.has_permission(doctype, "read")`` gates the whole call — no read
	  permission returns ``[]`` (the section renders nothing), never an error.
	* the fetch is ``frappe.get_list`` (the permission-RESPECTING list — unlike
	  ``get_all``), so row-level User Permissions trim the result too.

	Injection-safe by construction: ``filter_field`` and every requested
	``fields`` entry are matched against the doctype's own meta (a name outside
	it is rejected / dropped); the filter rides as a parameterized dict, never an
	f-string. ``fields`` (the columns the composite cardTemplate binds) may be a
	JSON string (bench) or a list (JS); ``name``/``docstatus``/``modified`` are
	always included so the client can key + status the cards.
	"""
	if not isinstance(doctype, str) or not frappe.db.exists("DocType", doctype):
		return []

	# Permission gate FIRST — arrangement never grants capability.
	if not frappe.has_permission(doctype, "read"):
		return []

	# No linked value on the source document → nothing to compose. Only a plain
	# scalar is a legal filter value: this enforces the equality contract
	# (filter_field == filter_value) and refuses a Frappe operator form
	# (e.g. ["like", "%x%"]) a direct API caller might send. Not a privilege
	# path (permissions are enforced regardless) — a contract guard.
	if not isinstance(filter_value, (str, int, float)) or filter_value == "":
		return []

	fetchable = _composite_fetchable_fieldnames(doctype)
	# Meta unavailable → we cannot safely whitelist the filter/columns, so return
	# the honest empty result instead of risking an off-column get_list 500
	# (keeps the "returns [], never throws" guarantee airtight).
	if fetchable is None:
		return []

	# filter_field must be a real fieldname on the doctype (defence-in-depth; the
	# client only ever sends a save-validated one).
	if not isinstance(filter_field, str) or not _FIELDNAME_RE.match(filter_field):
		return []
	if filter_field not in fetchable:
		return []

	# Requested columns → keep only real, fetchable fields; always add the three
	# base fields every card keys/statuses on.
	if isinstance(fields, str):
		try:
			fields = json.loads(fields)
		except (ValueError, TypeError):
			fields = None
	requested = fields if isinstance(fields, list) else []
	safe_fields = ["name", "docstatus", "modified"]
	for f in requested:
		if isinstance(f, str) and _FIELDNAME_RE.match(f) and f in fetchable and f not in safe_fields:
			safe_fields.append(f)

	# Clamp the page length to the same ceiling the validator enforces.
	try:
		page_length = int(limit)
	except (ValueError, TypeError):
		page_length = DETAIL_RELATED_DEFAULT_LIMIT
	page_length = max(1, min(page_length, DETAIL_RELATED_MAX_LIMIT))

	return frappe.get_list(
		doctype,
		filters={filter_field: filter_value},
		fields=safe_fields,
		order_by="modified desc",
		limit_page_length=page_length,
	)


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
