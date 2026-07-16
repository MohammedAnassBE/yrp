<!-- @yrp/web-engine — SummaryTiles ("summary-tiles" block; demo _template.html
     section 8 "summary-tiles (metrics from the named registry)").

     KPI stat tiles: big value + muted label, plus the demo home-view stat-card
     behaviour for metrics that carry a `goto` deep-link — those tiles render
     the ↗ arrow and navigate through the injected context.goto() on click
     (engine imports stay vue-only; no vue-router here).

     Layout contract (knobs are props, spec §6.4):
       { "type": "summary-tiles", "props": { "metrics": ["open_lots", …] } }
     A layout may only NAME metrics — the registry (label, computation, goto
     target) is server-owned; the layout can never define logic.

     Server contract (apps/yrp/yrp/yrp/api/ui_metrics.py):
       get_ui_metrics(keys: list[str]) -> {
         "metrics": [ { "key": str, "label": str, "value": number|str,
                        "goto": { "doctype": str, "filters": [[f, op, v], …] } } ],
         "warnings": [str],   # unknown / not-installed / failed keys — logged
       }
     Metrics the server omits (unknown key, no read permission on the metric's
     DocTypes, compute failure) simply don't render — arrangement never grants
     capability (spec §15). A bare array response is tolerated too.

     Graceful degradation, in order:
       - while loading: skeleton shimmer, one placeholder per requested metric
       - metric omitted from the response: that tile is hidden, order preserved
       - endpoint missing / request fails: console.warn + render NOTHING — the
         BlockBoundary :has() rule collapses the empty boundary, and a genuine
         render throw is still caught by BlockBoundary (spec §7.1)

     Registration (host's blocks/index.js, next phase):
       registerBlock("summary-tiles", { component: SummaryTiles, label: "KPI summary tiles" })

     Styling rides the host tokens (--esd-*/--radius/--space-*, themable via
     the --yrp-* layout knobs) with plain fallbacks so the engine renders in
     any host. Compact auto-fit grid — fills full/half/third spans cleanly. -->
<script setup>
import { computed, onMounted, ref, watch } from "vue"
import { getContext } from "./context.js"

// The metrics endpoint (apps/yrp/yrp/yrp/api/ui_metrics.py — landed alongside
// this block; this constant is the single line to touch if the path moves).
const METRICS_METHOD = "yrp.yrp.api.ui_metrics.get_ui_metrics"

const props = defineProps({
	// Metric-registry keys, in display order (layout knob).
	metrics: { type: Array, default: () => [] },
})

const loading = ref(true)
const rows = ref([])
let requestSeq = 0 // stale-response guard (metrics knob can change live)

const wantedKeys = computed(() =>
	(props.metrics || []).filter((k) => typeof k === "string" && k)
)
// One shimmer per requested metric (capped so a typo'd layout can't flood).
const skeletonCount = computed(() => Math.min(Math.max(wantedKeys.value.length, 1), 12))

function normalizeResponse(res) {
	const list = Array.isArray(res) ? res : Array.isArray(res?.metrics) ? res.metrics : []
	return list.filter((m) => m && typeof m === "object" && m.key != null)
}

async function load() {
	const seq = ++requestSeq
	const wanted = wantedKeys.value
	if (!wanted.length) {
		rows.value = []
		loading.value = false
		return
	}
	loading.value = true
	try {
		const res = await getContext().callMethod(METRICS_METHOD, { keys: wanted })
		if (seq !== requestSeq) return
		if (Array.isArray(res?.warnings) && res.warnings.length)
			console.warn("[yrp-web] summary-tiles: server warnings:", res.warnings)
		const byKey = new Map(normalizeResponse(res).map((m) => [String(m.key), m]))
		// Props order, omitted metrics hidden — never a crash, never a blank tile.
		rows.value = wanted.map((k) => byKey.get(k)).filter(Boolean)
	} catch (err) {
		if (seq !== requestSeq) return
		// Endpoint missing / rejected → hide the whole block (boundary collapses).
		console.warn("[yrp-web] summary-tiles: get_ui_metrics failed — hiding tiles", err)
		rows.value = []
	} finally {
		if (seq === requestSeq) loading.value = false
	}
}

function displayValue(v) {
	if (typeof v === "number" && Number.isFinite(v)) return v.toLocaleString("en-IN")
	return v === null || v === undefined || v === "" ? "—" : String(v)
}

function open(metric) {
	if (metric?.goto && typeof metric.goto === "object") getContext().goto(metric.goto)
}

watch(wantedKeys, load)
onMounted(load)
</script>

<template>
	<div v-if="loading" class="yrp-tiles" aria-busy="true">
		<div v-for="i in skeletonCount" :key="i" class="yrp-tile yrp-tile--skeleton">
			<span class="yrp-shimmer yrp-shimmer--val" />
			<span class="yrp-shimmer yrp-shimmer--label" />
		</div>
	</div>
	<div v-else-if="rows.length" class="yrp-tiles">
		<component
			:is="m.goto ? 'button' : 'div'"
			v-for="m in rows"
			:key="m.key"
			class="yrp-tile"
			:class="{ 'yrp-tile--link': m.goto }"
			:type="m.goto ? 'button' : undefined"
			:aria-label="m.goto ? `Open ${m.label || m.key}` : undefined"
			@click="m.goto ? open(m) : undefined"
		>
			<span v-if="m.goto" class="yrp-tile__arrow" aria-hidden="true">↗</span>
			<span class="yrp-tile__val">{{ displayValue(m.value) }}</span>
			<span class="yrp-tile__label">{{ m.label || m.key }}</span>
		</component>
	</div>
	<!-- not loading + no rows → nothing renders; BlockBoundary collapses it -->
</template>

<style scoped>
/* Demo .tiles/.tile, translated onto the host token vocabulary. Compact:
   auto-fit columns pack 6/3/2-track spans alike. */
.yrp-tiles {
	display: grid;
	grid-template-columns: repeat(auto-fit, minmax(152px, 1fr));
	gap: var(--space-3, 12px);
}

.yrp-tile {
	display: flex;
	flex-direction: column;
	gap: 3px;
	padding: var(--space-4, 16px);
	background: var(--esd-card, #ffffff);
	border: 1px solid var(--esd-line, #dfe8e4);
	border-radius: var(--radius, 12px);
	box-shadow: var(--esd-shadow-card, 0 1px 2px rgba(16, 32, 28, 0.05));
	/* button reset (goto tiles render as <button>) */
	font: inherit;
	color: inherit;
	text-align: left;
	min-width: 0;
}

.yrp-tile--link {
	position: relative;
	cursor: pointer;
	transition:
		transform 0.14s,
		box-shadow 0.14s;
}
.yrp-tile--link:hover {
	transform: translateY(-1px);
	box-shadow: var(--esd-shadow-pop, 0 12px 32px rgba(16, 32, 28, 0.12));
}

.yrp-tile__arrow {
	position: absolute;
	top: var(--space-3, 12px);
	right: var(--space-3, 12px);
	font-size: 13px;
	color: var(--esd-muted-2, #98a5a0);
	transition: color 0.14s;
}
.yrp-tile--link:hover .yrp-tile__arrow {
	color: var(--esd-accent, #0e8c7f);
}

.yrp-tile__val {
	font-size: 1.62rem;
	font-weight: 700;
	letter-spacing: -0.02em;
	line-height: 1.15;
	color: var(--esd-ink, #0f1613);
}
.yrp-tile__label {
	font-size: 0.75rem;
	font-weight: 600;
	color: var(--esd-muted, #5f6e68);
}

/* Skeleton shimmer — token-tinted bars with a low-alpha sweep (reads in both
   light and dark schemes; no hardcoded palette). */
.yrp-shimmer {
	display: block;
	position: relative;
	overflow: hidden;
	background: var(--esd-line, #dfe8e4);
	border-radius: 6px;
}
.yrp-shimmer--val {
	width: 56%;
	height: 1.62rem;
}
.yrp-shimmer--label {
	width: 78%;
	height: 0.75rem;
	margin-top: 3px;
}
.yrp-shimmer::after {
	content: "";
	position: absolute;
	inset: 0;
	transform: translateX(-100%);
	background: linear-gradient(90deg, transparent, rgba(255, 255, 255, 0.35), transparent);
	animation: yrp-shimmer-sweep 1.4s ease-in-out infinite;
}
@keyframes yrp-shimmer-sweep {
	100% {
		transform: translateX(100%);
	}
}
@media (prefers-reduced-motion: reduce) {
	.yrp-shimmer::after {
		animation: none;
	}
}
</style>
