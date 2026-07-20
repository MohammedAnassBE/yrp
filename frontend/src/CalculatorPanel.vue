<!-- @yrp/web-engine — CalculatorPanel ("calculator-panel" block; demo
     _template.html section 9 "calculator-panel (named calculation registry)").

     Input panel + live result card. Exactly like the demo, the layout may only
     NAME a calculation — it can never define logic (spec §15): the input-panel
     spec lives in the engine's CALC_REGISTRY below (the demo's client-side
     CALCULATIONS registry, minus compute), and the COMPUTE runs server-side in
     yrp.yrp.api.ui_metrics.CALCULATIONS via the injected callMethod. A name
     missing from the registry renders the demo's honest muted card:
       Unknown calculation "X" — not in the registry.

     Layout contract (knobs are props, spec §6.4):
       { "type": "calculator-panel",
         "props": { "calculation": "lot_balance", "params": { "lot": "L-104" } } }
     `params` (optional) seeds initial input values; the user edits from there.

     Server contract (apps/yrp/yrp/yrp/api/ui_metrics.py):
       run_ui_calculation(name: str, params: dict) -> {
         "name": str, "label": str, "params": dict,
         "value": number|str, "lines": [[label, number|str], …] }
     Unknown name / bad params THROW clean messages (that module's documented
     contract — a calculation is an explicit user action, not passive
     furniture). An "Unknown calculation …" throw ALSO lands on the muted card
     (server registry ahead/behind this build); other throws show the server's
     own clean message inline with a Retry, keeping the last good result.

     Degradation: required input still empty → no server call, em-dash result;
     recompute failure → inline error + Retry, last good result stays; a
     genuine render throw is still caught by BlockBoundary (spec §7.1).

     Registration (host's blocks/index.js, next phase):
       registerBlock("calculator-panel", { component: CalculatorPanel, label: "Calculator" }) -->
<script setup>
import { computed, onMounted, onUnmounted, reactive, ref, watch } from "vue"
import { getContext } from "./context.js"

// The compute endpoint (apps/yrp/yrp/yrp/api/ui_metrics.py — landed alongside
// this block; this constant is the single line to touch if the path moves).
const CALC_METHOD = "yrp.yrp.api.ui_metrics.run_ui_calculation"
const RECOMPUTE_DEBOUNCE_MS = 350

// Engine-owned input panels, keyed by the server calculation name (the demo's
// CALCULATIONS registry idea: inputs here, compute server-side). Growing the
// server registry = one entry here + the server function.
//   inputs[]: { name, label, type: "number"|"text"|"select",
//               options?: [str | {label, value}], default?, required?, placeholder? }
const CALC_REGISTRY = {
	lot_balance: {
		label: "Lot balance",
		inputs: [
			{ name: "lot", label: "Lot", type: "text", required: true, placeholder: "Lot name" },
		],
	},
}

const props = defineProps({
	// Calculation-registry name (layout knob). Required in practice.
	calculation: { type: String, default: "" },
	// Optional initial parameter values ({ name: value }).
	params: { type: Object, default: null },
})

const unknown = ref(false) // not in the registry (client-side, or server said so)
const serverLabel = ref("")
const result = ref(null) // { value, lines: [{label, value}] }
const computing = ref(false)
const computeError = ref("") // clean server/client message; "" = fine
const values = reactive({})

let requestSeq = 0 // stale-response guard
let debounceTimer = null

const isPlainObject = (v) => v !== null && typeof v === "object" && !Array.isArray(v)

// The server's unknown-name throw ("Unknown calculation 'x'. Available: …")
// means the same thing as a client-registry miss — the honest muted card.
const UNKNOWN_RE = /unknown[\s_]*calculation|not in the registry/i
const looksUnknown = (err) => UNKNOWN_RE.test(String(err?.message || err || ""))

const spec = computed(() => CALC_REGISTRY[props.calculation] || null)
const title = computed(() => serverLabel.value || spec.value?.label || props.calculation)

const inputs = computed(() =>
	(spec.value?.inputs || []).map((inp) => ({
		...inp,
		type: inp.type === "select" || inp.type === "text" ? inp.type : "number",
		options: (Array.isArray(inp.options) ? inp.options : []).map((o) =>
			isPlainObject(o)
				? { label: String(o.label ?? o.value ?? ""), value: o.value }
				: { label: String(o), value: o }
		),
	}))
)

const missingRequired = computed(() =>
	inputs.value.some((inp) => inp.required && (values[inp.name] === "" || values[inp.name] == null))
)

function formatNumber(v) {
	return typeof v === "number" && Number.isFinite(v) ? v.toLocaleString("en-IN") : String(v)
}

function normalizeResult(res) {
	// Top-level {value, lines} (the real contract); {result: {…}} tolerated.
	const r = isPlainObject(res) && isPlainObject(res.result) ? res.result : res
	if (!isPlainObject(r) || (!("value" in r) && !Array.isArray(r.lines))) return null
	const lines = (Array.isArray(r.lines) ? r.lines : [])
		.map((ln) => {
			if (Array.isArray(ln)) return { label: String(ln[0] ?? ""), value: formatNumber(ln[1] ?? "") }
			if (isPlainObject(ln)) return { label: String(ln.label ?? ""), value: formatNumber(ln.value ?? "") }
			return null
		})
		.filter(Boolean)
	return { value: r.value === null || r.value === undefined ? "—" : formatNumber(r.value), lines }
}

function seedValues() {
	for (const key of Object.keys(values)) delete values[key]
	const pinned = isPlainObject(props.params) ? props.params : {}
	for (const inp of inputs.value) {
		const fallback = inp.type === "select" ? inp.options[0]?.value : ""
		values[inp.name] = pinned[inp.name] ?? inp.default ?? fallback ?? ""
	}
}

// Number inputs hold strings while typing — cast finite ones for the server.
function currentParams() {
	const out = {}
	for (const inp of inputs.value) {
		const raw = values[inp.name]
		out[inp.name] =
			inp.type === "number" && raw !== "" && raw !== null && Number.isFinite(Number(raw))
				? Number(raw)
				: raw
	}
	return out
}

async function recompute() {
	const seq = ++requestSeq
	computeError.value = ""
	if (unknown.value) return
	if (missingRequired.value) {
		result.value = null // waiting for the user — em-dash, no server call
		computing.value = false
		return
	}
	computing.value = true
	try {
		const res = await getContext().callMethod(CALC_METHOD, {
			name: props.calculation,
			params: currentParams(),
		})
		if (seq !== requestSeq) return
		if (typeof res?.label === "string" && res.label) serverLabel.value = res.label
		const normalized = normalizeResult(res)
		if (normalized) result.value = normalized // unusable shape → keep last good
	} catch (err) {
		if (seq !== requestSeq) return
		if (looksUnknown(err)) {
			unknown.value = true // server registry disagrees → same honest card
		} else {
			console.warn(`[yrp-web] calculator-panel: "${props.calculation}" failed`, err)
			// Clean server message (the host client already unwraps + strips HTML).
			computeError.value = String(err?.message || "Couldn't run this calculation.")
		}
	} finally {
		if (seq === requestSeq) computing.value = false
	}
}

function queueRecompute() {
	clearTimeout(debounceTimer)
	debounceTimer = setTimeout(recompute, RECOMPUTE_DEBOUNCE_MS)
}

function init() {
	clearTimeout(debounceTimer)
	requestSeq++ // invalidate anything in flight
	unknown.value = !props.calculation || !spec.value
	serverLabel.value = ""
	result.value = null
	computeError.value = ""
	computing.value = false
	if (unknown.value) return
	seedValues()
	recompute() // no-ops (em-dash) while a required input is still empty
}

watch(() => [props.calculation, props.params], init)
onMounted(init)
onUnmounted(() => clearTimeout(debounceTimer))
</script>

<template>
	<section class="yrp-calc">
		<!-- not in the registry — the demo's honest muted card, verbatim wording -->
		<div v-if="unknown" class="yrp-calc__empty">
			Unknown calculation "{{ calculation }}" — not in the registry.
		</div>

		<template v-else>
			<header class="yrp-calc__head">
				<span class="yrp-calc__title">🧮 {{ title }}</span>
				<span class="yrp-calc__spacer" />
				<code class="yrp-calc__method">{{ CALC_METHOD }}</code>
			</header>
			<div class="yrp-calc__grid">
				<label v-for="inp in inputs" :key="inp.name" class="yrp-calc__field">
					<span class="yrp-calc__label">{{ inp.label }}</span>
					<select v-if="inp.type === 'select'" v-model="values[inp.name]" @change="queueRecompute">
						<option v-for="o in inp.options" :key="String(o.value)" :value="o.value">
							{{ o.label }}
						</option>
					</select>
					<input
						v-else
						v-model="values[inp.name]"
						:type="inp.type"
						:min="inp.type === 'number' ? 0 : undefined"
						:placeholder="inp.placeholder || ''"
						@input="queueRecompute"
					/>
				</label>
				<div class="yrp-calc__result" :class="{ 'yrp-calc__result--busy': computing }">
					<!-- first compute in flight → shimmer; afterwards busy just dims -->
					<template v-if="computing && !result">
						<span class="yrp-shimmer" style="width: 55%; height: 1.45rem" />
						<span class="yrp-shimmer" style="width: 90%; height: 0.78rem; margin-top: 8px" />
					</template>
					<template v-else>
						<div class="yrp-calc__value">{{ result?.value ?? "—" }}</div>
						<div v-for="(ln, i) in result?.lines || []" :key="i" class="yrp-calc__line">
							<span>{{ ln.label }}</span>
							<strong>{{ ln.value }}</strong>
						</div>
					</template>
					<div v-if="computeError" class="yrp-calc__err">
						<span>{{ computeError }}</span>
						<button type="button" class="yrp-calc__retry" @click="recompute">Retry</button>
					</div>
				</div>
			</div>
		</template>
	</section>
</template>

<style scoped>
/* Demo .block + .calc-* on the host token vocabulary (the --esd-* colors plus
   --radius and --space-* steps, all themable via the --yrp-* layout knobs,
   with plain fallbacks so the engine renders in any host). Compact enough for
   a third-span track; flex-wraps wider spans. */
.yrp-calc {
	background: var(--esd-card, #ffffff);
	border: 1px solid var(--esd-line, #dfe8e4);
	border-radius: var(--radius, 12px);
	box-shadow: var(--esd-shadow-card, 0 1px 2px rgba(16, 32, 28, 0.05));
	overflow: hidden;
}

.yrp-calc__head {
	display: flex;
	align-items: center;
	gap: var(--space-2, 8px);
	padding: var(--space-3, 12px) var(--space-4, 16px);
	border-bottom: 1px solid var(--esd-line, #dfe8e4);
}
.yrp-calc__title {
	font-weight: 700;
	font-size: 0.92rem;
	color: var(--esd-ink, #0f1613);
}
.yrp-calc__spacer {
	flex: 1;
}
.yrp-calc__method {
	font-size: 0.66rem;
	color: var(--esd-muted, #5f6e68);
	background: var(--esd-slate-50, #e9efec);
	padding: 3px 9px;
	border-radius: 6px;
	white-space: nowrap;
	max-width: 45%;
	overflow: hidden;
	text-overflow: ellipsis;
}

.yrp-calc__grid {
	display: flex;
	gap: var(--space-3, 12px);
	padding: var(--space-4, 16px);
	flex-wrap: wrap;
	align-items: flex-end;
}

.yrp-calc__field {
	display: flex;
	flex-direction: column;
	gap: 5px;
	min-width: 130px;
	flex: 1;
}
.yrp-calc__label {
	font-size: 0.68rem;
	font-weight: 700;
	color: var(--esd-muted, #5f6e68);
	text-transform: uppercase;
	letter-spacing: 0.06em;
}
.yrp-calc__field input,
.yrp-calc__field select {
	background: var(--esd-bg, #f2f6f4);
	border: 1px solid var(--esd-line, #dfe8e4);
	border-radius: calc(var(--radius, 12px) - 4px);
	padding: 9px 11px;
	font: inherit;
	font-size: 0.92rem;
	color: var(--esd-ink, #0f1613);
	width: 100%;
}
.yrp-calc__field input:focus,
.yrp-calc__field select:focus {
	border-color: var(--esd-accent, #0e8c7f);
	outline: 2px solid var(--esd-accent-50, #e7f3f1);
}

.yrp-calc__result {
	flex: 1.5;
	min-width: 200px;
	background: var(--esd-accent-50, #e7f3f1);
	border-radius: calc(var(--radius, 12px) - 4px);
	padding: 13px 15px;
	transition: opacity 0.14s;
}
.yrp-calc__result--busy {
	opacity: 0.7;
}
.yrp-calc__value {
	font-size: 1.45rem;
	font-weight: 700;
	letter-spacing: -0.01em;
	color: var(--esd-accent-700, #0a5f58);
}
.yrp-calc__line {
	display: flex;
	justify-content: space-between;
	gap: var(--space-2, 8px);
	font-size: 0.78rem;
	color: var(--esd-muted, #5f6e68);
	padding-top: 4px;
}
.yrp-calc__line strong {
	color: var(--esd-ink, #0f1613);
	font-weight: 650;
}

.yrp-calc__err {
	display: flex;
	align-items: center;
	gap: var(--space-2, 8px);
	padding-top: 6px;
	font-size: 0.78rem;
	color: var(--esd-danger, #b3403a);
}

.yrp-calc__empty {
	display: flex;
	align-items: center;
	justify-content: center;
	gap: var(--space-2, 8px);
	padding: var(--space-4, 16px);
	color: var(--esd-muted, #5f6e68);
	font-size: 0.8rem;
	text-align: center;
}

.yrp-calc__retry {
	font: inherit;
	font-size: 0.75rem;
	font-weight: 600;
	color: var(--esd-accent-700, #0a5f58);
	background: transparent;
	border: 1px solid var(--esd-line, #dfe8e4);
	border-radius: 6px;
	padding: 2px 10px;
	cursor: pointer;
	white-space: nowrap;
}
.yrp-calc__retry:hover {
	background: var(--esd-slate-50, #e9efec);
}

/* Skeleton shimmer (same recipe as SummaryTiles — scoped styles don't share). */
.yrp-shimmer {
	display: block;
	position: relative;
	overflow: hidden;
	background: var(--esd-line, #dfe8e4);
	border-radius: 6px;
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
