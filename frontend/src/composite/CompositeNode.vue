<!-- @yrp/web-engine — CompositeNode (Track 1 item 1).

     Recursive renderer for ONE node of a composite tree. The 13 §3(c)
     primitives are template branches below — small token-themed leaves on the
     host token vocabulary (--esd-*/--radius/--space-*, plain fallbacks), no
     third-party widgets (STACK_DECISION §3 item 1: hand-rolled), no HTML
     pass-through anywhere (every bound value renders through {{ }} text
     interpolation — stored-XSS is structurally impossible).

     Unknown primitive types render the PATH-LABELLED honest fallback (the
     UnknownBlock pattern: generic wording for everyone, the type + tree path
     for managers, console.warn always). showIf triples hide nodes
     declaratively; a malformed triple fails open (binding.js).

     Caps (node count / depth) are enforced by CompositeTree.vue before the
     first node renders; the depth guard here is a belt-and-braces stop. -->
<script setup>
import { computed } from "vue"
import { getContext } from "../context.js"
import { statusChipStyle } from "../statusColors.js"
import {
	COMPOSITE_ICON_RE,
	COMPOSITE_MAX_DEPTH,
	COMPOSITE_PRIMITIVES,
	isSiteFileSrc,
} from "./grammar.js"
import { evalShowIf, resolveDisplay, resolveNumber } from "./binding.js"

defineOptions({ name: "CompositeNode" })

const props = defineProps({
	node: { type: Object, required: true },
	scope: { type: Object, default: () => ({}) },
	ctx: { type: Object, default: () => ({}) }, // { dateFormat, dark }
	path: { type: String, default: "tree" },
	depth: { type: Number, default: 1 },
})

const showType = getContext().isManager()

const type = computed(() => props.node?.type)
const spec = computed(() => COMPOSITE_PRIMITIVES[type.value] || null)
const p = computed(() => (props.node?.props && typeof props.node.props === "object" ? props.node.props : {}))

const visible = computed(() => evalShowIf(props.node?.showIf, props.scope))

const overDepth = computed(() => props.depth > COMPOSITE_MAX_DEPTH)

const children = computed(() =>
	spec.value?.container && Array.isArray(props.node?.children) ? props.node.children : []
)

// ── prop resolution helpers (token enums fall back to their defaults) ──────
function en(name) {
	const def = spec.value?.props?.[name]
	if (!def || def.kind !== "enum") return undefined
	return def.values.includes(p.value[name]) ? p.value[name] : def.default
}
function int(name) {
	const def = spec.value?.props?.[name]
	if (!def || def.kind !== "int") return undefined
	const v = p.value[name]
	if (typeof v === "number" && Number.isInteger(v) && v >= def.min && v <= def.max) return v
	return def.default
}
function bool(name) {
	const v = p.value[name]
	return typeof v === "boolean" ? v : (spec.value?.props?.[name]?.default ?? false)
}
const disp = (name) => resolveDisplay(p.value[name], props.scope, props.ctx)

// ── per-primitive computed bits ─────────────────────────────────────────────
const stackStyle = computed(() => {
	if (type.value !== "stack") return null
	const justify = { start: "flex-start", center: "center", end: "flex-end", between: "space-between" }
	const align = { start: "flex-start", center: "center", end: "flex-end", stretch: "stretch" }
	return {
		flexDirection: en("direction"),
		justifyContent: justify[en("justify")],
		alignItems: align[en("align")],
		flexWrap: bool("wrap") ? "wrap" : "nowrap",
	}
})

const gridStyle = computed(() =>
	type.value === "grid" ? { gridTemplateColumns: `repeat(${int("columns")}, minmax(0, 1fr))` } : null
)

const badgeStatus = computed(() => {
	if (type.value !== "badge") return ""
	const v = disp("status")
	return v && v !== "—" ? String(v) : ""
})
const badgeStyle = computed(() =>
	badgeStatus.value ? statusChipStyle(badgeStatus.value, !!props.ctx.dark) : null
)
const badgeText = computed(() => {
	const t = disp("text")
	return t === "" || t === null || t === undefined ? badgeStatus.value : t
})

const progressValue = computed(() => {
	if (type.value !== "progress") return null
	const n = resolveNumber(p.value.value, props.scope)
	return n === null ? null : Math.min(100, Math.max(0, n))
})

const iconName = computed(() => {
	if (type.value !== "icon") return ""
	const name = p.value.name
	return typeof name === "string" && COMPOSITE_ICON_RE.test(name) ? name : ""
})

// image.src is STATIC-ONLY and site-files-only; anything else → honest fallback.
const imageSrc = computed(() =>
	type.value === "image" && isSiteFileSrc(p.value.src) ? p.value.src : ""
)
const imageStyle = computed(() => {
	const h = int("height")
	return {
		height: h ? `${h}px` : undefined,
		objectFit: en("fit"),
	}
})

const unknownReason = computed(() => {
	if (!spec.value) return `unknown primitive "${String(type.value)}"`
	if (type.value === "image" && !imageSrc.value) return "image src must be a site /files/ path"
	return ""
})
if (unknownReason.value) {
	console.warn(`[yrp-web] composite: ${unknownReason.value} at ${props.path}`)
}
if (type.value === "icon" && !iconName.value) {
	console.warn(`[yrp-web] composite: icon name must match "pi pi-..." at ${props.path} — rendering nothing`)
}
if (overDepth.value) {
	console.warn(`[yrp-web] composite: depth cap exceeded at ${props.path} (max ${COMPOSITE_MAX_DEPTH})`)
}
</script>

<template>
	<!-- honest, path-labelled fallbacks first (hidden nodes render nothing) -->
	<span v-if="visible && (!spec || (type === 'image' && !imageSrc))" class="yc-unknown">
		This piece isn't available.
		<code v-if="showType" class="yc-unknown__detail">{{ unknownReason }} at {{ path }}</code>
	</span>

	<template v-else-if="!visible || overDepth"></template>

	<!-- containers -->
	<div v-else-if="type === 'stack'" class="yc-stack" :class="`yc-gap-${en('gap')}`" :style="stackStyle">
		<CompositeNode
			v-for="(child, i) in children"
			:key="i"
			:node="child"
			:scope="scope"
			:ctx="ctx"
			:path="`${path}.children.${i}`"
			:depth="depth + 1"
		/>
	</div>

	<div v-else-if="type === 'grid'" class="yc-grid" :class="`yc-gap-${en('gap')}`" :style="gridStyle">
		<CompositeNode
			v-for="(child, i) in children"
			:key="i"
			:node="child"
			:scope="scope"
			:ctx="ctx"
			:path="`${path}.children.${i}`"
			:depth="depth + 1"
		/>
	</div>

	<div
		v-else-if="type === 'card'"
		class="yc-card"
		:class="[`yc-pad-${en('padding')}`, `yc-card--${en('tone')}`]"
	>
		<CompositeNode
			v-for="(child, i) in children"
			:key="i"
			:node="child"
			:scope="scope"
			:ctx="ctx"
			:path="`${path}.children.${i}`"
			:depth="depth + 1"
		/>
	</div>

	<!-- leaves -->
	<component
		:is="`h${2 + int('level')}`"
		v-else-if="type === 'heading'"
		class="yc-heading"
		:class="[`yc-heading--l${int('level')}`, `yc-align-${en('align')}`]"
	>
		{{ disp("text") }}
	</component>

	<span
		v-else-if="type === 'text'"
		class="yc-text"
		:class="[
			`yc-text--${en('tone')}`,
			`yc-text--${en('size')}`,
			`yc-text--w-${en('weight')}`,
			`yc-align-${en('align')}`,
			{ 'yc-mono': bool('mono') },
		]"
	>
		{{ disp("value") }}
	</span>

	<span v-else-if="type === 'kv-row'" class="yc-kv">
		<span class="yc-kv__label">{{ disp("label") }}</span>
		<span class="yc-kv__value" :class="{ 'yc-mono': bool('mono') }">{{ disp("value") }}</span>
	</span>

	<span
		v-else-if="type === 'badge'"
		class="yc-badge"
		:class="badgeStatus ? '' : `yc-badge--${en('tone')}`"
		:style="badgeStyle"
	>
		<i v-if="badgeStatus" class="yc-badge__dot" />
		{{ badgeText }}
	</span>

	<span v-else-if="type === 'stat'" class="yc-stat" :class="`yc-align-${en('align')}`">
		<span class="yc-stat__value">{{ disp("value") }}</span>
		<span class="yc-stat__label">{{ disp("label") }}</span>
	</span>

	<hr v-else-if="type === 'divider'" class="yc-divider" />

	<i
		v-else-if="type === 'icon' && iconName"
		:class="[iconName, 'yc-icon', `yc-icon--${en('size')}`, `yc-icon--${en('tone')}`]"
		aria-hidden="true"
	/>

	<span
		v-else-if="type === 'progress'"
		class="yc-progress"
		:class="`yc-progress--${en('tone')}`"
		role="progressbar"
		:aria-valuenow="progressValue ?? undefined"
		aria-valuemin="0"
		aria-valuemax="100"
	>
		<span class="yc-progress__bar" :style="{ width: `${progressValue ?? 0}%` }" />
	</span>

	<img
		v-else-if="type === 'image'"
		class="yc-image"
		:src="imageSrc"
		:alt="typeof p.alt === 'string' ? p.alt : ''"
		:style="imageStyle"
		loading="lazy"
	/>

	<span v-else-if="type === 'spacer'" class="yc-spacer" :class="`yc-spacer--${en('size')}`" aria-hidden="true" />
</template>

<style scoped>
/* Token leaves: --esd-* colors, --radius, --space-* steps (all themable via
   the --yrp-* layout bridge) with plain fallbacks — same vocabulary as
   SummaryTiles/CalculatorPanel. */

/* honest fallback */
.yc-unknown {
	display: inline-flex;
	flex-direction: column;
	gap: 2px;
	padding: var(--space-2, 8px);
	border: 1px dashed var(--esd-line, #dfe8e4);
	border-radius: calc(var(--radius, 12px) - 4px);
	color: var(--esd-muted, #5f6e68);
	font-size: 12px;
}
.yc-unknown__detail {
	font-size: 11px;
	opacity: 0.8;
}

/* containers */
.yc-stack {
	display: flex;
	min-width: 0;
}
.yc-grid {
	display: grid;
	min-width: 0;
}
.yc-gap-none {
	gap: 0;
}
.yc-gap-xs {
	gap: var(--space-1, 4px);
}
.yc-gap-sm {
	gap: var(--space-2, 8px);
}
.yc-gap-md {
	gap: var(--space-3, 12px);
}
.yc-gap-lg {
	gap: var(--space-4, 16px);
}

.yc-card {
	background: var(--esd-card, #ffffff);
	border: 1px solid var(--esd-line, #dfe8e4);
	border-radius: var(--radius, 12px);
	box-shadow: var(--esd-shadow-card, 0 1px 2px rgba(16, 32, 28, 0.05));
	display: flex;
	flex-direction: column;
	gap: var(--space-2, 8px);
	min-width: 0;
}
.yc-card--tint {
	background: var(--esd-accent-50, #e7f3f1);
	border-color: transparent;
}
.yc-card--muted {
	background: var(--esd-slate-50, #e9efec);
	border-color: transparent;
}
.yc-pad-none {
	padding: 0;
}
.yc-pad-sm {
	padding: var(--space-2, 8px);
}
.yc-pad-md {
	padding: var(--space-4, 16px);
}
.yc-pad-lg {
	padding: var(--space-5, 20px);
}

/* alignment utility (heading/text/stat) */
.yc-align-start {
	text-align: left;
	align-items: flex-start;
}
.yc-align-center {
	text-align: center;
	align-items: center;
}
.yc-align-end {
	text-align: right;
	align-items: flex-end;
}

/* heading */
.yc-heading {
	margin: 0;
	color: var(--esd-ink, #0f1613);
	line-height: 1.25;
}
.yc-heading--l1 {
	font-size: 1.15rem;
	font-weight: 700;
	letter-spacing: -0.01em;
}
.yc-heading--l2 {
	font-size: 0.95rem;
	font-weight: 700;
}
.yc-heading--l3 {
	font-size: 0.78rem;
	font-weight: 700;
	text-transform: uppercase;
	letter-spacing: 0.05em;
	color: var(--esd-muted, #5f6e68);
}

/* text */
.yc-text {
	display: block;
	color: var(--esd-ink, #0f1613);
	line-height: 1.4;
	min-width: 0;
	overflow-wrap: anywhere;
}
.yc-text--muted {
	color: var(--esd-muted, #5f6e68);
}
.yc-text--accent {
	color: var(--esd-accent-700, #0a5f58);
}
.yc-text--danger {
	color: var(--esd-danger, #b3403a);
}
.yc-text--xs {
	font-size: 0.7rem;
}
.yc-text--sm {
	font-size: 0.8rem;
}
.yc-text--md {
	font-size: 0.9rem;
}
.yc-text--lg {
	font-size: 1.05rem;
}
.yc-text--w-medium {
	font-weight: 600;
}
.yc-text--w-bold {
	font-weight: 700;
}
.yc-mono {
	font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
	font-size: 0.92em;
	letter-spacing: 0.01em;
}

/* kv-row */
.yc-kv {
	display: flex;
	align-items: baseline;
	justify-content: space-between;
	gap: var(--space-3, 12px);
	font-size: 0.82rem;
	min-width: 0;
}
.yc-kv__label {
	color: var(--esd-muted, #5f6e68);
	white-space: nowrap;
}
.yc-kv__value {
	color: var(--esd-ink, #0f1613);
	font-weight: 600;
	text-align: right;
	overflow-wrap: anywhere;
	min-width: 0;
}

/* badge — status chips take registry colours via inline style */
.yc-badge {
	display: inline-flex;
	align-items: center;
	gap: 6px;
	font-size: 11px;
	font-weight: 700;
	padding: 3px 10px;
	border-radius: 999px;
	white-space: nowrap;
	width: fit-content;
}
.yc-badge--neutral {
	color: var(--esd-muted, #5f6e68);
	background: var(--esd-slate-50, #e9efec);
}
.yc-badge--accent {
	color: var(--esd-accent-700, #0a5f58);
	background: var(--esd-accent-50, #e7f3f1);
}
.yc-badge__dot {
	width: 7px;
	height: 7px;
	border-radius: 50%;
	background: currentColor;
	flex: none;
}

/* stat */
.yc-stat {
	display: flex;
	flex-direction: column;
	gap: 3px;
	min-width: 0;
}
.yc-stat__value {
	font-size: 1.62rem;
	font-weight: 700;
	letter-spacing: -0.02em;
	line-height: 1.15;
	color: var(--esd-ink, #0f1613);
}
.yc-stat__label {
	font-size: 0.75rem;
	font-weight: 600;
	color: var(--esd-muted, #5f6e68);
}

/* divider */
.yc-divider {
	border: 0;
	border-top: 1px solid var(--esd-line, #dfe8e4);
	margin: var(--space-1, 4px) 0;
	width: 100%;
}

/* icon */
.yc-icon--sm {
	font-size: 12px;
}
.yc-icon--md {
	font-size: 16px;
}
.yc-icon--lg {
	font-size: 22px;
}
.yc-icon--default {
	color: var(--esd-ink, #0f1613);
}
.yc-icon--muted {
	color: var(--esd-muted, #5f6e68);
}
.yc-icon--accent {
	color: var(--esd-accent, #0e8c7f);
}

/* progress */
.yc-progress {
	display: block;
	height: 6px;
	border-radius: 999px;
	background: var(--esd-line, #dfe8e4);
	overflow: hidden;
	min-width: 48px;
}
.yc-progress__bar {
	display: block;
	height: 100%;
	border-radius: inherit;
	transition: width 0.2s;
}
.yc-progress--accent .yc-progress__bar {
	background: var(--esd-accent, #0e8c7f);
}
.yc-progress--muted .yc-progress__bar {
	background: var(--esd-muted-2, #98a5a0);
}

/* image */
.yc-image {
	display: block;
	max-width: 100%;
	border-radius: calc(var(--radius, 12px) - 4px);
}

/* spacer */
.yc-spacer {
	display: block;
	flex: none;
}
.yc-spacer--xs {
	height: var(--space-1, 4px);
}
.yc-spacer--sm {
	height: var(--space-2, 8px);
}
.yc-spacer--md {
	height: var(--space-4, 16px);
}
.yc-spacer--lg {
	height: var(--space-6, 24px);
}
</style>
