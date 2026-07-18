// @yrp/web-engine — composite binding resolver (USE_CASE §3(c), Track 1 item 1).
//
// Dot-path bindings into HOST-SUPPLIED data only, four named formatters, and
// declarative showIf triples. Explicitly NOT here, by design (§3(d)):
// expressions, template strings, queries, method names, HTML — a binding can
// only READ a path out of what the permissioned host already fetched.

import { formatDate } from "../format.js"
import {
	BIND_PATH_RE,
	COMPOSITE_FORMATS,
	FORBIDDEN_PATH_SEGMENTS,
	SHOWIF_OPS,
} from "./grammar.js"

const EMPTY = "—"

const isPlainObject = (v) => v !== null && typeof v === "object" && !Array.isArray(v)

/** True for the {bind, format?} binding-object shape (vs a literal scalar). */
export function isBinding(value) {
	return isPlainObject(value) && typeof value.bind === "string"
}

/**
 * Read `path` ("rows.0.status", "metrics.open_lots.value") out of `scope`.
 * Own-property walk only; malformed / forbidden / missing paths → undefined
 * (rendered as the em-dash — honest, never a crash).
 */
export function resolvePath(scope, path) {
	if (typeof path !== "string" || !BIND_PATH_RE.test(path)) return undefined
	let cur = scope
	for (const seg of path.split(".")) {
		if (FORBIDDEN_PATH_SEGMENTS.includes(seg)) return undefined
		if (cur === null || cur === undefined) return undefined
		if (Array.isArray(cur)) {
			if (!/^\d+$/.test(seg)) return undefined
			cur = cur[Number(seg)]
		} else if (isPlainObject(cur) && Object.prototype.hasOwnProperty.call(cur, seg)) {
			cur = cur[seg]
		} else {
			return undefined
		}
	}
	return cur
}

// ── the named formatter registry (COMPOSITE_FORMATS — nothing else exists) ──

/** docstatus → label; string statuses pass through untouched. */
const DOCSTATUS_LABELS = { 0: "Draft", 1: "Submitted", 2: "Cancelled" }

function formatQty(value) {
	const n = Number(value)
	if (!Number.isFinite(n)) return String(value)
	// Quantity display: localized, up to 3 fraction digits, no trailing zeros.
	return n.toLocaleString("en-IN", { maximumFractionDigits: 3 })
}

function formatNumber(value) {
	const n = Number(value)
	return Number.isFinite(n) ? n.toLocaleString("en-IN") : String(value)
}

function formatStatusLabel(value) {
	if (typeof value === "string" && value) return value
	if (value in DOCSTATUS_LABELS) return DOCSTATUS_LABELS[value]
	return String(value)
}

/**
 * Apply a NAMED formatter. `ctx.dateFormat` is the layout's dateFormat knob
 * (absent → the shipped dd-mm-yyyy). Unknown format names are ignored with a
 * warning (the value still renders raw — honest, and item-3 lint will catch it).
 */
export function formatValue(value, format, ctx = {}) {
	if (value === null || value === undefined || value === "") return EMPTY
	if (!format) return value
	if (!COMPOSITE_FORMATS.includes(format)) {
		console.warn(`[yrp-web] composite: unknown format "${format}" — rendering raw value`)
		return value
	}
	if (format === "date") return formatDate(value, ctx.dateFormat)
	if (format === "qty") return formatQty(value)
	if (format === "number") return formatNumber(value)
	return formatStatusLabel(value) // status-label
}

/**
 * Resolve a bindable prop value: literal scalars pass through; a binding
 * object reads its dot-path from scope and applies the named format. A bound
 * value that is missing/empty renders the em-dash.
 */
export function resolveDisplay(value, scope, ctx = {}) {
	if (isBinding(value)) {
		return formatValue(resolvePath(scope, value.bind), value.format, ctx)
	}
	if (value === null || value === undefined) return ""
	if (typeof value === "object") return "" // non-binding objects render nothing
	return value
}

/** resolveDisplay for numeric consumers (progress): returns a finite number or null. */
export function resolveNumber(value, scope) {
	const raw = isBinding(value) ? resolvePath(scope, value.bind) : value
	const n = Number(raw)
	return Number.isFinite(n) ? n : null
}

/**
 * Evaluate a declarative showIf triple {field, op, value} against the scope.
 * Ops: = != > < set not-set. Malformed triples FAIL OPEN (render the node,
 * console.warn) — showIf is presentation, never a permission gate (§15:
 * permissions are enforced by the host/server regardless).
 */
export function evalShowIf(showIf, scope) {
	if (showIf === null || showIf === undefined) return true
	if (!isPlainObject(showIf) || typeof showIf.field !== "string" || !SHOWIF_OPS.includes(showIf.op)) {
		console.warn("[yrp-web] composite: malformed showIf — rendering the node", showIf)
		return true
	}
	const actual = resolvePath(scope, showIf.field)
	const isSet = !(actual === null || actual === undefined || actual === "")
	switch (showIf.op) {
		case "set":
			return isSet
		case "not-set":
			return !isSet
		case ">":
		case "<": {
			const a = Number(actual)
			const b = Number(showIf.value)
			if (!Number.isFinite(a) || !Number.isFinite(b)) return false
			return showIf.op === ">" ? a > b : a < b
		}
		case "=":
		case "!=": {
			const eq = looseEquals(actual, showIf.value)
			return showIf.op === "=" ? eq : !eq
		}
		default:
			return true
	}
}

/** Deterministic scalar equality: numeric when both sides parse as finite
 *  numbers (so 1 = "1" and Check fields compare naturally), else string. */
function looseEquals(a, b) {
	const an = Number(a)
	const bn = Number(b)
	if (a !== "" && b !== "" && a != null && b != null && Number.isFinite(an) && Number.isFinite(bn)) {
		return an === bn
	}
	return String(a ?? "") === String(b ?? "")
}

/**
 * Collect every dot-path a tree reads (bind paths + showIf fields) — the host
 * uses this to derive WHICH FIELDS to fetch (the JSON names fields, exactly
 * like record-list columns; it never feeds a query). Bounded walk: stops at
 * `maxNodes` so a hostile tree can't spin the collector.
 */
export function collectBindPaths(tree, maxNodes = 500) {
	const paths = new Set()
	let seen = 0
	const walk = (node) => {
		if (!isPlainObject(node) || ++seen > maxNodes) return
		const props = isPlainObject(node.props) ? node.props : {}
		for (const value of Object.values(props)) {
			if (isBinding(value) && BIND_PATH_RE.test(value.bind)) paths.add(value.bind)
		}
		if (isPlainObject(node.showIf) && typeof node.showIf.field === "string") {
			if (BIND_PATH_RE.test(node.showIf.field)) paths.add(node.showIf.field)
		}
		if (Array.isArray(node.children)) node.children.forEach(walk)
	}
	walk(tree)
	return [...paths]
}

/** Node count + max depth of a tree (caps live in grammar.js; CompositeTree
 *  enforces them; the item-3 server validator re-enforces them at save). */
export function treeStats(tree, hardStop = 10000) {
	let nodes = 0
	let depth = 0
	const walk = (node, d) => {
		if (!isPlainObject(node) || nodes >= hardStop) return
		nodes += 1
		if (d > depth) depth = d
		if (Array.isArray(node.children)) for (const c of node.children) walk(c, d + 1)
	}
	walk(tree, 1)
	return { nodes, depth }
}
