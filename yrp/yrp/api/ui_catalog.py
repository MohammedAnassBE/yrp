# Copyright (c) 2026, Essdee and contributors
# For license information, please see license.txt

"""LAYOUT_SCHEMA.json generator — the layout vocabulary as machine-readable
ground truth (USE_CASE "custom ui/USE_CASE.md" §4 Track 2 item 12).

NOT whitelisted — bench execute only (like ``ui_fleet.seed_verify_user``):

    bench --site <site> execute yrp.yrp.api.ui_catalog.generate
    bench --site <site> execute yrp.yrp.api.ui_catalog.check_drift

``generate`` derives the ENTIRE layout vocabulary from the live code — the
``ui_config`` constants + ``validate_config`` rules, the ``ui_metrics``
METRICS/CALCULATIONS registries, and the block-type/prop vocabulary
(``BLOCK_PROP_KEYS``, the server mirror of the consumer's ``blocks/index.js``
registrations) — and writes it to ``custom ui/catalog/LAYOUT_SCHEMA.json``.
An authoring agent reads THIS file instead of reverse-reading the engine.

Every key carries: ``type``, legal values (``enum`` / ``pattern`` / ``range``),
``tier`` (where the key may live), ``status`` (consumed vs reserved),
``validation`` (hard vs soft) and a short ``effect`` description. Enum VALUES
and registry names are imported, never retyped — they cannot drift from the
validator. Effect prose is curated here; when it goes stale the drift check
still fires because the enums move with the code.

Regeneration is deterministic (sorted keys, no timestamps, trailing newline)
so any vocabulary change shows up as a plain git diff of the JSON.
``check_drift`` regenerates in memory, byte-compares against the committed
file, prints a short diff and raises on mismatch (bench exits non-zero) — the
thin wrapper ``.claude/hooks/gen-layout-schema.sh`` maps that to exit code 1
via the ``LAYOUT-SCHEMA-DRIFT`` sentinel line.

Colocated with ``ui_config.py`` / ``ui_metrics.py``; read-only against the
site (no record reads at all — the only site-shaped input is the
``yrp_web_doctype_catalog`` hook, which is code, not data).
"""

import difflib
import json
import os

import frappe
from frappe.utils import get_bench_path

from yrp.yrp.api.ui_config import (
	ACCENT_RE,
	ACTION_ITEMS,
	ACTIONS_PLACEMENTS,
	BLOCK_PROP_KEYS,
	BLOCK_SIZES,
	CHROME_KEYS,
	CONFIG_SIZE_WARN_BYTES,
	CURRENT_SCHEMA_VERSION,
	DATE_FORMATS,
	DC_ENTRY_QTY_CONTROLS,
	DC_ENTRY_SUPPLIER_PICKERS,
	DC_ENTRY_VARIANTS,
	DEFAULT_LAYOUT_NAME,
	DETAIL_POSITIONS,
	ENTRY_MODES,
	HOME_QUEUE_METRICS,
	ICON_RE,
	LAYOUT_KEYS,
	LAYOUT_ONLY_KEYS,
	LIST_VIEW_VARIANTS,
	MAX_OVERRIDES_BYTES,
	NAV_POSITIONS,
	OVERLAY_POSITIONS,
	OVERRIDABLE_KEYS,
	STRUCTURAL_KEYS,
	THEME_ARROWS,
	THEME_COLOR_KEYS,
	THEME_DENSITIES,
	THEME_FONT_RE,
	THEME_MODES,
	THEME_RGBA_RE,
	THEME_SECTION_HEADERS,
	_web_doctype_catalog,
)
from yrp.yrp.api.ui_metrics import CALCULATIONS, METRICS

# Default output target, relative to the BENCH root (never sites/).
DEFAULT_RELATIVE_PATH = os.path.join("custom ui", "catalog", "LAYOUT_SCHEMA.json")

# Sentinel printed by check_drift so the shell wrapper can distinguish
# "vocabulary drifted" (exit 1) from a tooling failure (exit 2).
DRIFT_SENTINEL = "LAYOUT-SCHEMA-DRIFT"

# Curated per-calculation params documentation (the CALCULATIONS registry
# stores only label + run; params are validated inside each run function).
# A calculation missing here still lands in the catalog with params: None.
_CALC_PARAMS_DOC = {
	"lot_balance": {
		"lot": {
			"type": "string",
			"required": True,
			"effect": "Name of the Lot to balance (row-level read permission enforced).",
		}
	},
}


def _color_token(effect):
	return {
		"type": "string",
		"validation": "soft",
		"pattern": {"any_of": ["accent", "rgba"]},
		"effect": effect + " Off-form values are ignored by the client (shipped fallback kept).",
	}


def _enum(values, effect, fallback=None, validation="soft", value_type="string"):
	out = {"type": value_type, "validation": validation, "enum": list(values), "effect": effect}
	if fallback is not None:
		out["fallback"] = fallback
	return out


def _doctype_name(effect, catalog_gated=True):
	out = {
		"type": "string",
		"validation": "soft",
		"values": "a DocType name; must exist on the site",
		"effect": effect,
	}
	if catalog_gated:
		out["values"] = "a DocType name from web_doctype_catalog (see top-level key)"
	return out


def _theme_token_keys(overlay=False):
	"""theme / theme.dark share one token vocabulary (the overlay adds accent
	as SOFT instead of hard and forbids nesting another dark)."""
	keys = {}
	for key in THEME_COLOR_KEYS:
		effects = {
			"bg": "Page background color token.",
			"surface": "Card/panel surface color token.",
			"text": "Primary text color token.",
			"muted": "Secondary/muted text color token.",
			"line": "Border/divider color token.",
			"surface2": "Secondary surface (stripes, wells) color token.",
		}
		keys[key] = _color_token(effects[key])
	keys["radius"] = {
		"type": "number",
		"validation": "soft",
		"range": {"min": 0, "max": 60},
		"effect": "Corner radius in px for cards/controls. Out-of-range is ignored by the client.",
	}
	keys["density"] = _enum(
		THEME_DENSITIES,
		"Spacing density token. INERT: emitted as CSS tokens but host CSS does not consume "
		"density yet (Track 1 item 10) — authoring it draws a lint warning; don't author it.",
	)
	keys["fontScale"] = {
		"type": "number",
		"validation": "soft",
		"range": {"min": 0.5, "max": 2},
		"effect": "Global font-size multiplier. Out-of-range is ignored by the client.",
	}
	keys["font"] = {
		"type": "string",
		"validation": "soft",
		"pattern": "font",
		"effect": "CSS font-family stack (plain ASCII letters, spaces, commas, quotes only).",
	}
	if overlay:
		keys["accent"] = {
			"type": "string",
			"validation": "soft",
			"pattern": "accent",
			"effect": "Dark-scheme accent override. Malformed values keep the shipped palette "
			"(soft here, unlike the top-level accent which is hard).",
		}
	return keys


def _block_type_specs():
	"""Per-block-type prop schemas. The type list and every prop NAME come from
	BLOCK_PROP_KEYS (the server mirror of the consumer blocks/index.js
	registrations + each block's defineProps); value rules mirror
	_check_block_props. Unknown block types are NOT validated server-side (the
	client bundle may be newer) — UnknownBlock renders a labelled placeholder."""
	prop_details = {
		"home-greeting": {
			"greetingName": {
				"type": "string",
				"validation": "soft",
				"effect": "Display name in the greeting headline (default: session user's first name).",
			},
			"sub": {
				"type": "string",
				"validation": "soft",
				"effect": "Sub-headline text under the greeting.",
			},
			"newCta": {
				"type": "object",
				"validation": "soft",
				"effect": "The '+ New' split CTA. Entries without a /web catalog route are dropped by the client.",
				"keys": {
					"primary": _doctype_name("DocType the primary '+ New' button creates."),
					"menu": {
						"type": "array",
						"items": _doctype_name("DocType entries for the CTA dropdown menu."),
						"validation": "soft",
						"effect": "DocTypes listed in the CTA's dropdown.",
					},
				},
			},
		},
		"home-queues": {
			"stats": {
				"type": "array",
				"items": _enum(
					HOME_QUEUE_METRICS,
					"Queue-backed metric names ONLY (HomeQueues METRIC_TO_QUEUE). A registered "
					"KPI metric (e.g. completion, delayed) renders NOTHING here — put those in "
					"a summary-tiles block. Anything else is a typo.",
				),
				"validation": "soft",
				"effect": "Which queue cards render, in order.",
			},
			"maxCards": {
				"type": "integer",
				"status": "reserved",
				"validation": "soft",
				"range": {"min": 1, "max": 10},
				"effect": "RESERVED — accepted and range-checked since day one, consumed by no "
				"client (HomeQueues reads only stats). Does nothing today.",
			},
		},
		"home-recent": {
			"doctypes": {
				"type": "array",
				"items": _doctype_name("Tabs of the recent-documents table."),
				"validation": "soft",
				"effect": "Which DocTypes get a recent-documents tab, in order.",
			},
			"recentStyle": _enum(
				("table", "tiles"),
				"Rendering of the recent list.",
				fallback="table",
			),
		},
		"home-quick-create": {
			"doctypes": {
				"type": "array",
				"items": _doctype_name("Quick-create buttons on the standalone card."),
				"validation": "soft",
				"effect": "Which DocTypes get a create button (client also gates on canCreate).",
			},
		},
		"summary-tiles": {
			"metrics": {
				"type": "array",
				"items": _enum(
					tuple(sorted(METRICS)),
					"Registered ui_metrics names. Tiles the user lacks read permission for are "
					"omitted silently (arrangement never grants capability).",
				),
				"validation": "soft",
				"effect": "Which KPI tiles render, in order. Each tile deep-links its metric's goto list.",
			},
		},
		"record-list": {
			"doctype": {
				"type": "string",
				"required": True,
				"validation": "soft",
				"values": "any DocType existing on the site (catalog NOT required — an "
				"off-catalog doctype only loses its 'View all' link)",
				"effect": "The DocType this embedded list renders.",
			},
			"variant": _enum(
				LIST_VIEW_VARIANTS, "List presentation for this block.", fallback="table"
			),
			"columns": {
				"type": "array",
				"items": {
					"any_of": [
						{"type": "string", "values": "a renderable fieldname on the block's doctype"},
						{
							"type": "object",
							"keys": {
								"field": {"type": "string", "required": True},
								"label": {"type": "string"},
							},
						},
					]
				},
				"validation": "soft",
				"effect": "Columns to show. Fieldname strings and {field, label} objects are both "
				"legal IN THIS BLOCK ONLY (listViews columns take objects only). Hidden fields, "
				"non-listable fieldtypes and frappe default fields (name/modified/owner/...) "
				"render nothing — pick from CATALOG.md's fieldname tables; lint warns on the rest.",
			},
			"groupBy": {
				"type": "string",
				"validation": "soft",
				"values": "a renderable fieldname on the block's doctype",
				"effect": "Kanban grouping field (kanban falls back to status when it names no meta field).",
			},
			"titleField": {
				"type": "string",
				"validation": "soft",
				"values": "a renderable fieldname on the block's doctype",
				"effect": "Card/row title field (falls back to the meta title_field).",
			},
			"pageSize": {
				"type": "integer",
				"validation": "soft",
				"range": {"min": 1, "max": 50},
				"effect": "Rows fetched per page.",
			},
			"title": {
				"type": "string",
				"validation": "soft",
				"effect": "Card heading text (default: the DocType's plural label).",
			},
		},
		"calculator-panel": {
			"calculation": {
				"type": "string",
				"required": True,
				"validation": "soft",
				"enum": sorted(CALCULATIONS),
				"effect": "Registered calculation this panel runs (run_ui_calculation registry).",
			},
			"params": {
				"type": "object",
				"validation": "soft",
				"effect": "Default parameters pre-filled into the panel (see registries.calculations "
				"for each calculation's params).",
			},
		},
	}

	out = {}
	for block_type in sorted(BLOCK_PROP_KEYS):
		props = {}
		for prop in BLOCK_PROP_KEYS[block_type]:
			props[prop] = prop_details.get(block_type, {}).get(
				prop, {"type": "unknown", "effect": "(no curated doc — update ui_catalog.py)"}
			)
		out[block_type] = {
			"registered_in": "essdee_yrp frontend/src/blocks/index.js (registerBlock)",
			"props": props,
		}
	return out


def _metrics_registry():
	out = {}
	for key in sorted(METRICS):
		spec = METRICS[key]
		out[key] = {
			"label": spec["label"],
			"doctypes": list(spec["doctypes"]),
			"home_queue": key in HOME_QUEUE_METRICS,
			"effect": "Omitted silently when the user lacks read permission on any listed DocType. "
			"Tile click deep-links the metric's goto list.",
		}
	return out


def _calculations_registry():
	out = {}
	for key in sorted(CALCULATIONS):
		out[key] = {
			"label": CALCULATIONS[key]["label"],
			"params": _CALC_PARAMS_DOC.get(key),
		}
	return out


def build_catalog():
	"""Assemble the full catalog dict. Pure w.r.t. site DATA — the only
	site-shaped input is the yrp_web_doctype_catalog hook (code)."""
	catalog_hook = _web_doctype_catalog()

	hidden_map = {
		"type": "object",
		"validation": "soft (non-boolean values warn; dict shape is hard)",
		"values": "{<key>: true|false} — strictly boolean; truthy non-booleans fail to hide",
		"effect": "Visibility map. Hides compose across layers; an upper layer re-shows with false.",
	}

	keys = {
		"schema_version": {
			"type": "integer",
			"tier": "both",
			"status": "consumed",
			"required": True,
			"validation": "hard",
			"values": f"a positive integer <= {CURRENT_SCHEMA_VERSION} (current: {CURRENT_SCHEMA_VERSION})",
			"effect": "Config schema version. Newer-than-server versions are rejected at save and "
			"dropped at resolve (never guess-interpreted forward).",
		},
		"nav": {
			"type": "object",
			"tier": "overridable",
			"status": "consumed",
			"validation": "hard shape; soft vocabulary",
			"effect": "Navigation shell: position, grouped doctype items, per-doctype hiding.",
			"keys": {
				"position": _enum(
					NAV_POSITIONS,
					"Which nav shell renders. Anything except 'topbar' renders the sidebar (strict compare).",
					fallback="sidebar",
				),
				"groups": {
					"type": "array",
					"validation": "hard shape; soft vocabulary",
					"effect": "Ordered nav groups. Duplicate group ids / duplicate doctypes across groups warn.",
					"items": {
						"type": "object",
						"keys": {
							"id": {
								"type": "string",
								"validation": "soft",
								"effect": "Group key — sidebar collapse state persists per id; must be unique.",
							},
							"label": {"type": "string", "effect": "Group heading text."},
							"items": {
								"type": "array",
								"items": {
									"type": "object",
									"keys": {
										"doctype": {
											"type": "string",
											"required": True,
											"validation": "hard presence; soft existence/catalog",
											"values": "a DocType name from web_doctype_catalog",
											"effect": "The routed list this nav item opens. Off-catalog names are "
											"dropped by the client. {view: 'home'} items are redundant — Home "
											"always renders first and the item is ignored (soft warning).",
										},
										"icon": {
											"type": "string",
											"validation": "hard",
											"pattern": "icon",
											"effect": "PrimeIcons class ('pi pi-...'). Sidebar shows it; topbar pills are wording-only.",
										},
									},
								},
								"effect": "Nav items — the client reads ONLY doctype + icon.",
							},
						},
					},
				},
				"hidden": dict(
					hidden_map,
					effect="Per-doctype nav hiding. Layout-layer keys naming no nav item warn as dead.",
				),
				"home": {
					"type": "object",
					"status": "reserved",
					"validation": "soft",
					"effect": "RESERVED (demo vocab) — stored but no client reads it; Home always renders first.",
				},
			},
		},
		"screens": {
			"type": "object",
			"tier": "overridable",
			"status": "consumed",
			"validation": "hard shape; soft vocabulary",
			"effect": "Screen composition. ONLY screens.home renders today; any other key "
			"(incl. reserved list:<DocType>/detail:<DocType>) renders nothing and warns.",
			"keys": {
				"home": {
					"type": "object",
					"status": "consumed",
					"effect": "The home screen: ordered blocks + block hiding.",
					"keys": {
						"blocks": {
							"type": "array",
							"validation": "hard shape (id/type required); soft vocabulary",
							"effect": "Ordered home blocks rendered by ScreenRenderer.",
							"items": {
								"type": "object",
								"keys": {
									"id": {
										"type": "string",
										"required": True,
										"validation": "hard",
										"effect": "Unique block instance id (hidden targeting + render keys). Duplicates warn.",
									},
									"type": {
										"type": "string",
										"required": True,
										"validation": "hard presence; soft registry",
										"enum": sorted(BLOCK_PROP_KEYS),
										"effect": "Registered block type (see top-level block_types). Unknown types "
										"render a labelled UnknownBlock placeholder.",
									},
									"size": _enum(
										BLOCK_SIZES,
										"Grid span of the block.",
										fallback="full",
									),
									"props": {
										"type": "object",
										"validation": "soft, per block type",
										"effect": "Block props — see block_types.<type>.props. Unknown props on a "
										"known type are ignored by Vue and warn at save.",
									},
								},
							},
						},
						"hidden": dict(
							hidden_map,
							effect="Per-block-id hiding. Layout-layer keys naming no block id warn as dead.",
						),
					},
				},
			},
		},
		"listViews": {
			"type": "object",
			"tier": "overridable",
			"status": "consumed",
			"validation": "hard shape; soft vocabulary",
			"values": "keyed by DocType name (must exist on site + be in web_doctype_catalog)",
			"effect": "Per-doctype list presentation for the routed /web list pages. Precedence: "
			"the user's own saved User Listview record > this config > DocType meta.",
			"keys": {
				"<DocType>": {
					"type": "object",
					"keys": {
						"variant": _enum(
							LIST_VIEW_VARIANTS,
							"List presentation for this doctype's routed list page.",
							fallback="table",
						),
						"columns": {
							"type": "array",
							"items": "{field, label} objects ONLY — the routed list page DROPS bare "
							"fieldname strings (strings are legal only in record-list block columns); "
							"lint warns on every string entry",
							"validation": "soft",
							"effect": "Columns to show. Hidden fields, non-listable fieldtypes and "
							"frappe default fields (name/modified/owner/...) render nothing — pick "
							"from CATALOG.md's fieldname tables; lint warns on the rest.",
						},
						"groupBy": {
							"type": "string",
							"validation": "soft",
							"values": "a renderable fieldname on the doctype",
							"effect": "Kanban grouping field (falls back to status).",
						},
						"titleField": {
							"type": "string",
							"validation": "soft",
							"values": "a renderable fieldname on the doctype",
							"effect": "Card/row title field (falls back to meta title_field).",
						},
					},
				}
			},
		},
		"quickCreate": {
			"type": "array",
			"tier": "overridable",
			"status": "consumed",
			"validation": "hard shape; soft entries",
			"items": _doctype_name("Entries of the global quick-create menu."),
			"effect": "DocTypes offered in the quick-create affordance. Client drops off-catalog "
			"names and gates every entry on canCreate.",
		},
		"theme": {
			"type": "object",
			"tier": "overridable",
			"status": "consumed",
			"validation": "mode/accent hard; every other token soft",
			"effect": "Design tokens applied by the engine (applyTheme). A knob's absence keeps "
			"the shipped look byte-identical (parity law).",
			"keys": {
				"mode": _enum(
					THEME_MODES,
					"Color scheme: 'user' honors the user's stored/OS choice; 'light'/'dark' force.",
					validation="hard",
				),
				"accent": {
					"type": "string",
					"validation": "hard",
					"pattern": "accent",
					"effect": "Light-scheme accent (#rrggbb). The engine derives the hover/tint family.",
				},
				**_theme_token_keys(),
				"arrows": _enum(
					THEME_ARROWS,
					"Decorative-arrow presentation (DESIGN_PREMIUM §4(i) item 1). 'quiet' mutes/hides "
					"the decorative arrows (queue-card + KPI-tile arrows, 'View all' arrows, Submit "
					"trailing arrow, row chevrons, linked-doc arrows) and makes LinkField-goto + sort "
					"icons hover-OR-focus revealed (~40% opacity on touch, never removed). Functional "
					"arrows (prev/next record, dropdown caret) are untouched. TOP level only — inside "
					"theme.dark it does nothing and warns. Absent = shipped look, byte-identical.",
					fallback="default",
				),
				"sectionHeaders": _enum(
					THEME_SECTION_HEADERS,
					"Detail-card section-header presentation (DESIGN_PREMIUM §4(i) item 2). 'plain' "
					"retires the accent-tinted band + leading dot + accent uppercase title on "
					".esd-card__head: transparent head, muted title, hairline border-bottom stays. "
					"TOP level only — inside theme.dark it does nothing and warns. Absent = shipped "
					"banded look, byte-identical.",
					fallback="banded",
				),
				"dark": {
					"type": "object",
					"validation": "soft",
					"effect": "Dark-scheme palette overlay: effective dark theme = {...theme, ...theme.dark}. "
					"Custom light colors WITHOUT a dark overlay while dark mode is reachable warn "
					"(dark mode would keep the shipped dark palette). Nested 'dark' is meaningless.",
					"keys": _theme_token_keys(overlay=True),
				},
			},
		},
		"chrome": {
			"type": "object",
			"tier": "layout-only",
			"status": "consumed",
			"validation": "soft",
			"effect": "Demo-7-style chrome strip replacing the standard topbar. Mounted only when "
			"chrome is a plain object; flags are read with strict !== false.",
			"keys": {
				key: {
					"type": "boolean",
					"validation": "soft",
					"effect": {
						"search": "Show the search affordance in the chrome strip.",
						"themeToggle": "Show the light/dark toggle in the chrome strip.",
					}[key],
				}
				for key in CHROME_KEYS
			},
		},
		"realtime": {
			"type": "object",
			"tier": "layout-only",
			"status": "partially reserved",
			"validation": "soft",
			"effect": "Realtime indicator knobs. ONLY 'enabled' is consumed (ChromeBar Live dot).",
			"keys": {
				"enabled": {
					"type": "boolean",
					"validation": "soft",
					"status": "consumed",
					"effect": "Show the Live indicator in the chrome strip.",
				},
				"intervalMs": {
					"type": "number",
					"validation": "soft",
					"status": "reserved",
					"effect": "RESERVED — no client consumes it; does nothing today (Track 1 item 11).",
				},
				"toast": {
					"type": "boolean",
					"validation": "soft",
					"status": "reserved",
					"effect": "RESERVED — no client consumes it; does nothing today (Track 1 item 11).",
				},
			},
		},
		"dateFormat": dict(
			_enum(
				DATE_FORMATS,
				"Date rendering across format.js consumers (lists, recents).",
				fallback="dd-mm-yyyy",
			),
			tier="layout-only",
			status="consumed",
		),
		"detail": {
			"type": "object",
			"tier": "structural",
			"status": "consumed",
			"validation": "hard shape; soft vocabulary",
			"effect": "How a document's detail view (DocDetail) is hosted.",
			"keys": {
				"position": _enum(
					DETAIL_POSITIONS,
					"Detail host: full page, right drawer, center dialog, or bottom sheet.",
					fallback="page",
				),
				"rich": {
					"type": "unknown",
					"status": "reserved",
					"validation": "soft",
					"effect": "RESERVED — stored, consumed by nothing yet; does nothing today.",
				},
			},
		},
		"entry": {
			"type": "object",
			"tier": "structural",
			"status": "consumed",
			"validation": "hard shape; soft vocabulary",
			"effect": "How document creation opens.",
			"keys": {
				"mode": _enum(
					ENTRY_MODES, "Full-page create vs popup create.", fallback="page"
				),
				"popupPosition": _enum(
					OVERLAY_POSITIONS,
					"Popup anchor on the 9-position overlay grid. Only meaningful with mode 'popup' "
					"(warns otherwise).",
					fallback="center",
				),
			},
		},
		"dcEntry": {
			"type": "object",
			"tier": "structural",
			"status": "consumed",
			"validation": "hard shape; soft vocabulary",
			"effect": "The Delivery Challan entry presentation (WO -> items -> quantities -> "
			"job-worker -> save).",
			"keys": {
				"variant": _enum(
					DC_ENTRY_VARIANTS, "DC entry flow presentation.", fallback="form-grid"
				),
				"qtyControl": _enum(
					DC_ENTRY_QTY_CONTROLS,
					"Quantity input control. Vocabulary is currently input-only (stepper/big-touch "
					"are Track 1 item 5).",
					fallback="input",
				),
				"supplierPicker": _enum(
					DC_ENTRY_SUPPLIER_PICKERS, "Supplier selection control.", fallback="select"
				),
			},
		},
		"actions": {
			"type": "object",
			"tier": "structural",
			"status": "partially reserved",
			"validation": "hard shape; soft vocabulary",
			"effect": "Where document actions render and which of the EXISTING affordances show. "
			"Arrangement never grants capability — every item still passes the client's "
			"permission gates.",
			"keys": {
				"placement": _enum(
					ACTIONS_PLACEMENTS, "Action bar placement on the detail view.", fallback="header"
				),
				"dialogPosition": {
					"type": "string",
					"status": "reserved",
					"validation": "soft",
					"enum": list(OVERLAY_POSITIONS),
					"effect": "RESERVED — vocabulary-checked so layouts may carry it, but no client "
					"consumes it; all action dialogs open center today.",
				},
				"items": {
					"type": "array",
					"validation": "hard shape; soft entries",
					"items": _enum(
						ACTION_ITEMS,
						"FILTER over the existing header affordances only. Unknown names are "
						"ignored by the client.",
					),
					"effect": "Which action affordances render (subset filter; never adds capability).",
				},
			},
		},
	}

	return {
		"_meta": {
			"title": "YRP /web layout vocabulary — machine-readable catalog",
			"generated_by": "bench --site <site> execute yrp.yrp.api.ui_catalog.generate",
			"drift_check": "bash .claude/hooks/gen-layout-schema.sh --check",
			"do_not_edit": "GENERATED FILE — edit ui_config.py/ui_metrics.py/ui_catalog.py and regenerate.",
			"schema_version": CURRENT_SCHEMA_VERSION,
			"default_layout": DEFAULT_LAYOUT_NAME,
			"renderer_agnostic": "This schema is RENDERER-AGNOSTIC (STACK_DECISION.md §4): layout "
			"JSON names, arranges and parameterizes code-owned registry entries and validated "
			"tokens — it never encodes a framework or component detail (no component names, no "
			"third-party prop surfaces, no HTML/CSS/JS). A second renderer (e.g. React Native) "
			"may bind the same JSON contract without any schema change.",
			"sources": [
				"apps/yrp/yrp/yrp/api/ui_config.py (constants + validate_config rules)",
				"apps/yrp/yrp/yrp/api/ui_metrics.py (METRICS + CALCULATIONS registries)",
				"apps/essdee_yrp/essdee_yrp/hooks.py (yrp_web_doctype_catalog hook)",
				"apps/essdee_yrp/frontend/src/blocks/index.js (block registrations, mirrored by BLOCK_PROP_KEYS)",
			],
			"reading_rules": [
				"NEVER read custom ui/demos/ for vocabulary — demo vocab != live vocab.",
				"tier 'overridable' keys work in BOTH a UI Layout config and a user's personal overrides; "
				"'layout-only' and 'structural' keys work ONLY in a UI Layout config (the overrides layer "
				"warns on and filters them).",
				"status 'reserved' = accepted + stored + shape-checked, but consumed by NO client — it "
				"does nothing today; never author it into a new layout.",
				"validation 'hard' blocks the save; 'soft' warns and the client falls back. The authoring "
				"bar is ZERO warnings (lint-layout exit 0).",
				"A knob's ABSENCE always means: byte-identical to the shipped default rendering (parity law).",
			],
		},
		"layers": {
			"layout": {
				"storage": "UI Layout.config (complete document; layout_name is the record name)",
				"top_level_keys": list(LAYOUT_KEYS),
				"effect": "The per-layout complete config. Resolution: merge(merge(skeleton, layout), "
				"overrides, OVERRIDABLE_KEYS).",
			},
			"overrides": {
				"storage": "YRP UI Preference.overrides (sparse delta, self-service Knobs panel)",
				"top_level_keys": ["schema_version", *OVERRIDABLE_KEYS],
				"effect": "Personal layer. Unknown/structural keys are warned on and filtered — "
				"structural looks always go in a layout record, never personal overrides.",
			},
		},
		"tiers": {
			"overridable": list(OVERRIDABLE_KEYS),
			"layout_only": list(LAYOUT_ONLY_KEYS),
			"structural": list(STRUCTURAL_KEYS),
		},
		"keys": keys,
		"block_types": _block_type_specs(),
		"registries": {
			"metrics": _metrics_registry(),
			"home_queue_metrics": list(HOME_QUEUE_METRICS),
			"calculations": _calculations_registry(),
			"action_items": list(ACTION_ITEMS),
		},
		"web_doctype_catalog": sorted(catalog_hook) if catalog_hook else None,
		"formats": {
			"accent": ACCENT_RE.pattern,
			"icon": ICON_RE.pattern,
			"rgba": THEME_RGBA_RE.pattern,
			"font": THEME_FONT_RE.pattern,
		},
		"limits": {
			"config_size_warn_bytes": CONFIG_SIZE_WARN_BYTES,
			"max_overrides_bytes": MAX_OVERRIDES_BYTES,
		},
	}


def _serialize(catalog):
	"""Deterministic serialization: sorted keys, 1-space indent (house style of
	the template exports), trailing newline, real UTF-8."""
	return json.dumps(catalog, indent=1, sort_keys=True, ensure_ascii=False) + "\n"


def _resolve_path(path=None):
	"""Default: <bench>/custom ui/catalog/LAYOUT_SCHEMA.json. A relative
	``path`` argument is resolved against the BENCH root (bench execute's cwd
	is sites/, which is never a sane target)."""
	if not path:
		return os.path.join(get_bench_path(), DEFAULT_RELATIVE_PATH)
	if not os.path.isabs(path):
		return os.path.join(get_bench_path(), path)
	return path


def generate(path=None):
	"""Write the catalog. Bench execute only:

	    bench --site <site> execute yrp.yrp.api.ui_catalog.generate
	    bench --site <site> execute yrp.yrp.api.ui_catalog.generate \\
	        --kwargs '{"path": "custom ui/catalog/LAYOUT_SCHEMA.json"}'
	"""
	frappe.only_for("System Manager")
	target = _resolve_path(path)
	os.makedirs(os.path.dirname(target), exist_ok=True)
	content = _serialize(build_catalog())
	with open(target, "w", encoding="utf-8") as f:
		f.write(content)
	print(f"LAYOUT_SCHEMA written: {target} ({len(content.encode('utf-8'))} bytes)")
	return target


def check_drift(path=None):
	"""Drift check: regenerate in memory, byte-compare with the committed file.

	    bench --site <site> execute yrp.yrp.api.ui_catalog.check_drift

	Prints a short diff + the ``LAYOUT-SCHEMA-DRIFT`` sentinel and RAISES on
	mismatch (bench exits non-zero); the gen-layout-schema.sh wrapper maps the
	sentinel to exit code 1 (vs 2 for tooling failures)."""
	frappe.only_for("System Manager")
	target = _resolve_path(path)
	expected = _serialize(build_catalog())

	if not os.path.exists(target):
		print(DRIFT_SENTINEL)
		raise Exception(
			f"LAYOUT_SCHEMA drift: {target} does not exist — run "
			"yrp.yrp.api.ui_catalog.generate to create it"
		)

	with open(target, encoding="utf-8") as f:
		on_disk = f.read()

	if on_disk == expected:
		print(f"LAYOUT_SCHEMA in sync: {target}")
		return True

	diff = list(
		difflib.unified_diff(
			on_disk.splitlines(keepends=True),
			expected.splitlines(keepends=True),
			fromfile="committed " + target,
			tofile="regenerated (live code)",
		)
	)
	print("".join(diff[:80]))
	if len(diff) > 80:
		print(f"... ({len(diff) - 80} more diff lines)")
	print(DRIFT_SENTINEL)
	raise Exception(
		f"LAYOUT_SCHEMA drift: {target} no longer matches the live vocabulary — "
		"regenerate with yrp.yrp.api.ui_catalog.generate and commit the diff"
	)
