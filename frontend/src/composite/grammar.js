// @yrp/web-engine — bounded composition grammar (USE_CASE §3(c)/(d), Track 1 item 1).
//
// THE code-owned vocabulary of the `composite` block: exactly the ~14
// primitives named in USE_CASE §3(c) — stack, grid, card, heading, text,
// kv-row, badge, stat, divider, icon, progress, image (site files only),
// spacer — each a small token-themed leaf rendered by CompositeNode.vue.
// A layout composes a VALIDATED TREE of these; it can never define DOM:
// no HTML/CSS/selector/JS strings, no expressions, no loops, no queries
// (§3(d) boundary — Stance B2 stays rejected).
//
// This file is the single ground truth the server-side tree validator
// (Track 1 item 3, ui_config.py) mirrors — keep it declarative and boring.
// GRAMMAR-CHANGE RULE (USE_CASE review amendment 4): any change to this
// grammar (a prop rename, an enum member removal, a node-shape change) ships
// a schema_version upgrader + re-validation of stored layouts. Purely
// ADDITIVE growth (a new primitive, a new enum member) needs no upgrader —
// old trees keep rendering identically.
//
// Node shape (the whole grammar — there is nothing else):
//   { "type": "<primitive>",
//     "props": { ...token-typed props below... },
//     "children": [ <node>, ... ],            // container primitives only
//     "showIf": { "field": "<dot-path>", "op": "=", "value": <scalar> } }
//
// Bindable props accept either a literal scalar or a BINDING OBJECT:
//   { "bind": "<dot-path into the host-fetched scope>", "format": "date" }
// Dot-paths read host-supplied data ONLY (the permissioned host block fetched
// it); formats are the named registry below — never an expression.

/** Hard caps enforced by CompositeTree.vue (and mirrored server-side, item 3). */
export const COMPOSITE_MAX_NODES = 100
export const COMPOSITE_MAX_DEPTH = 6

/** Declarative showIf ops — the ONLY conditional vocabulary (§3(c)). */
export const SHOWIF_OPS = ["=", "!=", ">", "<", "set", "not-set"]

/** Named display formatters (binding.js implements them — nothing else exists). */
export const COMPOSITE_FORMATS = ["date", "qty", "number", "status-label"]

/** Dot-path grammar: identifier/index segments only — no expressions, and the
 *  dunder guard keeps prototype-shaped segments out of scope reads. */
export const BIND_PATH_RE = /^[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)*$/
export const FORBIDDEN_PATH_SEGMENTS = ["__proto__", "prototype", "constructor"]

/** Icon vocabulary — same shape as the nav icon rule (ui_config ICON_RE). */
export const COMPOSITE_ICON_RE = /^pi pi-[a-z0-9-]+$/

/** image.src — SITE FILES ONLY (§3(c) "image[site files]"): a same-origin
 *  /files/ or /private/files/ path. No external URLs, no protocols, no
 *  traversal, no quotes/whitespace. Private files still 403 server-side for
 *  users without permission — arrangement never grants capability. */
export const SITE_FILE_RE = /^\/(?:private\/)?files\/[A-Za-z0-9][A-Za-z0-9 ._()/-]*$/
export const isSiteFileSrc = (src) =>
	typeof src === "string" && SITE_FILE_RE.test(src) && !src.includes("..")

// Prop spec kinds used below (the item-3 validator consumes these):
//   { kind: "enum", values: [...], default }   token enum
//   { kind: "bindable" }                       literal scalar OR {bind, format}
//   { kind: "bindable-number" }                same, rendered as a number
//   { kind: "boolean", default }
//   { kind: "int", min, max, default }
//   { kind: "string" }                         plain text (never HTML)
//   { kind: "icon" }                           must match COMPOSITE_ICON_RE
//   { kind: "site-file" }                      must pass isSiteFileSrc (STATIC only)
const GAP = { kind: "enum", values: ["none", "xs", "sm", "md", "lg"], default: "md" }
const ALIGN = { kind: "enum", values: ["start", "center", "end"], default: "start" }

/**
 * The primitive registry — names, container-ness, and the full token-typed
 * prop vocabulary of each. CompositeNode.vue renders exactly this; the
 * server validator (item 3) whitelists exactly this.
 */
export const COMPOSITE_PRIMITIVES = {
	// ── containers (the only nodes that take children) ──────────────────────
	stack: {
		container: true,
		props: {
			direction: { kind: "enum", values: ["column", "row"], default: "column" },
			gap: GAP,
			align: { kind: "enum", values: ["start", "center", "end", "stretch"], default: "stretch" },
			justify: { kind: "enum", values: ["start", "center", "end", "between"], default: "start" },
			wrap: { kind: "boolean", default: false },
		},
	},
	grid: {
		container: true,
		props: {
			columns: { kind: "int", min: 1, max: 6, default: 2 },
			gap: GAP,
		},
	},
	card: {
		container: true,
		props: {
			padding: { kind: "enum", values: ["none", "sm", "md", "lg"], default: "md" },
			tone: { kind: "enum", values: ["default", "tint", "muted"], default: "default" },
		},
	},
	// ── leaves ──────────────────────────────────────────────────────────────
	heading: {
		container: false,
		props: {
			text: { kind: "bindable" },
			level: { kind: "int", min: 1, max: 3, default: 2 },
			align: ALIGN,
		},
	},
	text: {
		container: false,
		props: {
			value: { kind: "bindable" },
			tone: { kind: "enum", values: ["default", "muted", "accent", "danger"], default: "default" },
			size: { kind: "enum", values: ["xs", "sm", "md", "lg"], default: "md" },
			weight: { kind: "enum", values: ["regular", "medium", "bold"], default: "regular" },
			mono: { kind: "boolean", default: false },
			align: ALIGN,
		},
	},
	"kv-row": {
		container: false,
		props: {
			label: { kind: "bindable" },
			value: { kind: "bindable" },
			mono: { kind: "boolean", default: false },
		},
	},
	badge: {
		container: false,
		props: {
			text: { kind: "bindable" },
			// When `status` resolves to a named status the chip takes the
			// engine's registry colours (statusColors.js — layouts can only
			// NAME statuses, never colour them). Absent → neutral/accent tone.
			status: { kind: "bindable" },
			tone: { kind: "enum", values: ["neutral", "accent"], default: "neutral" },
		},
	},
	stat: {
		container: false,
		props: {
			value: { kind: "bindable" },
			label: { kind: "bindable" },
			align: ALIGN,
		},
	},
	divider: { container: false, props: {} },
	icon: {
		container: false,
		props: {
			name: { kind: "icon" },
			size: { kind: "enum", values: ["sm", "md", "lg"], default: "md" },
			tone: { kind: "enum", values: ["default", "muted", "accent"], default: "default" },
		},
	},
	progress: {
		container: false,
		props: {
			// 0–100; out-of-range clamps. Colour is token-owned (accent/muted),
			// never a layout value.
			value: { kind: "bindable-number" },
			tone: { kind: "enum", values: ["accent", "muted"], default: "accent" },
		},
	},
	image: {
		container: false,
		props: {
			src: { kind: "site-file" }, // STATIC only — bindings are refused here
			alt: { kind: "string" },
			height: { kind: "int", min: 16, max: 480, default: null },
			fit: { kind: "enum", values: ["cover", "contain"], default: "cover" },
		},
	},
	spacer: {
		container: false,
		props: {
			size: { kind: "enum", values: ["xs", "sm", "md", "lg"], default: "md" },
		},
	},
}

export const isContainerPrimitive = (type) => !!COMPOSITE_PRIMITIVES[type]?.container

/** Prop names of a primitive whose values may carry a {bind} object. */
export function bindablePropNames(type) {
	const spec = COMPOSITE_PRIMITIVES[type]
	if (!spec) return []
	return Object.keys(spec.props).filter((p) =>
		["bindable", "bindable-number"].includes(spec.props[p].kind)
	)
}
