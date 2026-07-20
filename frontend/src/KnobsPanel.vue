<!-- @yrp/web-engine — KnobsPanel (self-service knobs, locked decision 2026-07-15).

     The floating 🎛 button bottom-left + panel, modelled on the demo engine's
     renderKnobs() ("custom ui/demos/_template.html" .knobs/.fab/.knob). SHOWN
     to every authenticated user; there is deliberately NO "View layout JSON"
     button (locked decision — that stays an SM/Desk concern).

     Offers ONLY knobs the engine renders today:
       Navigation      nav.position         sidebar | topbar
       Recent records  home-recent props    table | tiles (only when the active
                       .recentStyle         home has a home-recent block)
       Theme           theme.mode           user | light | dark
       Accent          theme.accent         small preset palette + shipped teal
     (No density knob: the host CSS consumes no --yrp-pad/gap/row tokens yet,
     so it would be a lying control.)

     Every change applies LIVE (optimistic store patch — the host's theme watch
     re-runs applyTheme; the shell/blocks react off the store) AND persists via
     yrp.yrp.api.ui_config.save_my_ui_overrides. That endpoint REPLACES the
     stored overrides wholesale, so the panel first hydrates the user's stored
     sparse delta (get_my_ui_overrides) and always re-saves the FULL delta —
     knobs it didn't touch (e.g. SM-planted listViews overrides) survive.
     Defaults mirror the ACTIVE layout: until the user touches a knob nothing
     is written and nothing changes (parity law). Reset calls
     reset_my_ui_overrides and restores the pure layout. Errors surface as a
     small toast, never a crash. Hidden during ANY §10 preview (user OR bare
     layout) — a save there would write the SM's own record while showing a
     config that isn't theirs. -->
<template>
	<div v-if="!ui.previewing" class="yrp-knobs">
		<div v-if="open" class="yrp-knobs__panel" role="dialog" aria-label="My UI settings">
			<div class="yrp-knobs__title">
				<span aria-hidden="true">🎛</span>
				<span>My UI</span>
				<span v-if="busy" class="yrp-knobs__busy">Saving…</span>
				<button class="yrp-knobs__x" type="button" aria-label="Close" @click="open = false">✕</button>
			</div>

			<div class="yrp-knob">
				<label for="yrp-knob-nav">Navigation</label>
				<select id="yrp-knob-nav" :value="navPosition" @change="setNav($event.target.value)">
					<option value="sidebar">Left sidebar</option>
					<option value="topbar">Top bar</option>
				</select>
			</div>

			<div v-if="recentBlock" class="yrp-knob">
				<label for="yrp-knob-recent">Recent records</label>
				<select id="yrp-knob-recent" :value="recentStyle" @change="setRecent($event.target.value)">
					<option value="table">Table</option>
					<option value="tiles">Tiles</option>
				</select>
			</div>

			<div class="yrp-knob">
				<label for="yrp-knob-mode">Theme</label>
				<select id="yrp-knob-mode" :value="themeMode" @change="setMode($event.target.value)">
					<option value="user">My device setting</option>
					<option value="light">Light</option>
					<option value="dark">Dark</option>
				</select>
			</div>

			<div class="yrp-knob">
				<label id="yrp-knob-accent-label">Accent</label>
				<div class="yrp-knobs__swatches" role="group" aria-labelledby="yrp-knob-accent-label">
					<button
						v-for="p in ACCENT_PRESETS"
						:key="p.value"
						type="button"
						class="yrp-knobs__swatch"
						:class="{ active: isActiveAccent(p.value) }"
						:style="{ background: p.value }"
						:title="p.label"
						:aria-label="`Accent: ${p.label}`"
						:aria-pressed="isActiveAccent(p.value) ? 'true' : 'false'"
						@click="setAccent(p.value)"
					/>
				</div>
			</div>

			<button class="yrp-knobs__reset" type="button" :disabled="busy" @click="reset">
				Reset to layout default
			</button>

			<div class="yrp-knobs__note">
				Only changes how the app looks for you — saved automatically to your login.
			</div>
		</div>

		<button
			class="yrp-knobs__fab"
			type="button"
			:aria-expanded="open ? 'true' : 'false'"
			@click="toggle"
		>
			<span aria-hidden="true">🎛</span> Knobs <span aria-hidden="true">{{ open ? "▾" : "▴" }}</span>
		</button>

		<div v-if="toastMsg" class="yrp-knobs__toast" role="status">{{ toastMsg }}</div>
	</div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from "vue"
import { getContext } from "./context.js"
import { ENGINE_SCHEMA_VERSION, useUiConfigStore } from "./stores/uiConfig.js"
import { SHIPPED_ACCENT } from "./theme/applyTheme.js"

const API = {
	get: "yrp.yrp.api.ui_config.get_my_ui_overrides",
	save: "yrp.yrp.api.ui_config.save_my_ui_overrides",
	reset: "yrp.yrp.api.ui_config.reset_my_ui_overrides",
}

// Small preset palette + the shipped default (all pass the server ACCENT_RE).
const ACCENT_PRESETS = [
	{ label: "Teal (default)", value: SHIPPED_ACCENT },
	{ label: "Red", value: "#E23744" },
	{ label: "Blue", value: "#2563EB" },
	{ label: "Violet", value: "#7C3AED" },
	{ label: "Orange", value: "#EA580C" },
	{ label: "Green", value: "#15803D" },
]

const ui = useUiConfigStore()
const open = ref(false)
const busy = ref(false)
const toastMsg = ref("")
let toastTimer = null

const HEX_RE = /^#[0-9a-fA-F]{6}$/
const clone = (v) => JSON.parse(JSON.stringify(v ?? null))

// ── current knob values, read from the ACTIVE resolved config ───────────────
const navPosition = computed(() => (ui.active.nav?.position === "topbar" ? "topbar" : "sidebar"))
const recentBlock = computed(
	() => (ui.active.screens?.home?.blocks || []).find((b) => b && b.type === "home-recent") || null
)
const recentStyle = computed(() =>
	recentBlock.value?.props?.recentStyle === "tiles" ? "tiles" : "table"
)
const themeMode = computed(() => {
	const m = ui.active.theme?.mode
	return m === "light" || m === "dark" ? m : "user"
})
const activeAccent = computed(() => {
	const a = ui.active.theme?.accent
	return (typeof a === "string" && HEX_RE.test(a) ? a : SHIPPED_ACCENT).toLowerCase()
})
const isActiveAccent = (v) => v.toLowerCase() === activeAccent.value

// ── stored overrides hydration (server truth of the sparse delta) ───────────
// save_my_ui_overrides REPLACES the field wholesale, and the resolved config
// can't tell layout values from personal ones — so the panel loads the raw
// stored delta once and always re-saves the whole thing.
let stored = null
let hydrating = null

function hydrate() {
	if (stored) return Promise.resolve()
	if (!hydrating) {
		hydrating = getContext()
			.callMethod(API.get, {})
			.then((res) => {
				const o = res?.overrides
				stored = o && typeof o === "object" && !Array.isArray(o) ? clone(o) : {}
			})
			.catch(() => {
				hydrating = null // retry on the next attempt; stored stays null
			})
	}
	return hydrating
}

function toggle() {
	open.value = !open.value
	if (open.value) hydrate()
}

// ── change plumbing: optimistic store patch + serialized full-delta save ────
async function change(applyToOverrides, applyToConfig) {
	await hydrate()
	if (!stored) {
		toast("Couldn't load your saved UI settings — try again")
		return
	}
	// 1. Persisted sparse delta (what save_my_ui_overrides stores).
	const next = clone(stored)
	applyToOverrides(next)
	next.schema_version = ENGINE_SCHEMA_VERSION
	stored = next
	// 2. Live: patch the resolved config in the store. The host's theme watch
	//    re-runs applyTheme; shell/blocks react off the store getters.
	const cfg = clone(ui.active)
	applyToConfig(cfg)
	ui.config = cfg
	queueSave()
}

let saving = false
let dirty = false
async function queueSave() {
	if (saving) {
		dirty = true
		return
	}
	saving = true
	busy.value = true
	try {
		do {
			dirty = false
			const payload = await getContext().callMethod(API.save, { overrides: stored })
			// Reconcile with the server-resolved truth — unless another change
			// landed mid-flight (the next loop pass will bring a fresher one).
			// Through the store's guardPayload gate (same as boot/refresh): a
			// degraded resolution must never blank nav/home; on rejection the
			// optimistic patch stays rendered.
			if (!dirty) ui.applyServerPayload(payload, "knobs save")
		} while (dirty)
	} catch (e) {
		toast(e?.message ? `Couldn't save: ${e.message}` : "Couldn't save your UI settings")
		// Drop the optimistic state and restore server truth so the UI never lies.
		stored = null
		hydrating = null
		try {
			await ui.refresh()
		} catch {
			/* keep the current view — refresh degrades silently */
		}
	} finally {
		saving = false
		busy.value = false
	}
}

// ── the knobs ────────────────────────────────────────────────────────────────
function setNav(v) {
	change(
		(o) => {
			o.nav = { ...(o.nav || {}), position: v }
		},
		(cfg) => {
			cfg.nav = cfg.nav || {}
			cfg.nav.position = v
		}
	)
}

function setRecent(v) {
	// screens.home.blocks is an ARRAY — arrays replace wholesale in the §5
	// merge, so the override must carry the whole active blocks list with just
	// the home-recent props patched.
	const blocks = clone(ui.active.screens?.home?.blocks || [])
	const target = blocks.find((b) => b && b.type === "home-recent")
	if (!target) return
	target.props = { ...(target.props || {}), recentStyle: v }
	change(
		(o) => {
			o.screens = { ...(o.screens || {}) }
			o.screens.home = { ...(o.screens.home || {}) }
			o.screens.home.blocks = blocks
		},
		(cfg) => {
			cfg.screens = cfg.screens || {}
			cfg.screens.home = cfg.screens.home || {}
			cfg.screens.home.blocks = clone(blocks)
		}
	)
}

function setMode(v) {
	change(
		(o) => {
			o.theme = { ...(o.theme || {}), mode: v }
		},
		(cfg) => {
			cfg.theme = cfg.theme || {}
			cfg.theme.mode = v
		}
	)
}

function setAccent(v) {
	// When the layout ships an explicit dark accent (e.g. Demo 7's #ff6b5e),
	// it would shadow the user's pick in dark mode — override it too so the
	// chosen accent wins in BOTH schemes. Layouts without a dark overlay keep
	// the nicer derived dark family (no dark key is written).
	const hasDarkAccent = typeof ui.active.theme?.dark?.accent === "string"
	change(
		(o) => {
			o.theme = { ...(o.theme || {}), accent: v }
			if (hasDarkAccent) o.theme.dark = { ...(o.theme.dark || {}), accent: v }
		},
		(cfg) => {
			cfg.theme = cfg.theme || {}
			cfg.theme.accent = v
			if (hasDarkAccent && cfg.theme.dark) cfg.theme.dark.accent = v
		}
	)
}

async function reset() {
	busy.value = true
	try {
		const payload = await getContext().callMethod(API.reset, {})
		stored = {}
		hydrating = Promise.resolve()
		// Same guardPayload gate as the save (and boot/refresh) — a degraded
		// resolution keeps the current view instead of blanking nav/home.
		ui.applyServerPayload(payload, "knobs reset")
		toast("Back to the layout's own settings")
	} catch (e) {
		toast(e?.message ? `Couldn't reset: ${e.message}` : "Couldn't reset your UI settings")
	} finally {
		busy.value = false
	}
}

// ── tiny self-contained toast (the engine has no host toast service) ────────
function toast(msg) {
	toastMsg.value = msg
	if (toastTimer) clearTimeout(toastTimer)
	toastTimer = setTimeout(() => {
		toastMsg.value = ""
	}, 3200)
}

function onKeydown(e) {
	if (e.key === "Escape" && open.value) open.value = false
}
onMounted(() => document.addEventListener("keydown", onKeydown))
onBeforeUnmount(() => {
	document.removeEventListener("keydown", onKeydown)
	if (toastTimer) clearTimeout(toastTimer)
})
</script>

<style scoped>
/* Demo .knobs / .fab / .knobs-panel / .knob, on the host's theme tokens with
   plain fallbacks so the engine renders in any host (and in dark mode, where
   the host flips the --esd-* values under .dark). */
.yrp-knobs {
	position: fixed;
	/* Demo position is 18px from the corner. A host whose shell keeps fixed
	   chrome in that corner (e.g. the sidebar rail's footer chevron) sets
	   --yrp-knobs-left on the shell to shift the FAB clear; hosts without one
	   (topbar shell) leave the var unset and the demo position holds. */
	left: var(--yrp-knobs-left, 18px);
	bottom: 18px;
	z-index: 60;
	display: flex;
	flex-direction: column;
	gap: 10px;
	align-items: flex-start;
}

.yrp-knobs__fab {
	display: inline-flex;
	align-items: center;
	gap: 8px;
	background: #141a28;
	color: #fff;
	border: 1px solid rgba(255, 255, 255, 0.14);
	border-radius: 999px;
	padding: 11px 18px;
	font-weight: 650;
	font-size: 0.83rem;
	cursor: pointer;
	box-shadow: 0 10px 28px rgba(0, 0, 0, 0.35);
	font-family: inherit;
}
.yrp-knobs__fab:hover {
	filter: brightness(1.15);
}

.yrp-knobs__panel {
	background: var(--esd-card, #ffffff);
	border: 1px solid var(--esd-line, #dfe8e4);
	border-radius: 14px;
	box-shadow: 0 14px 44px rgba(0, 0, 0, 0.28);
	padding: 14px;
	display: flex;
	flex-direction: column;
	gap: 10px;
	width: 272px;
}

.yrp-knobs__title {
	display: flex;
	align-items: center;
	font-weight: 700;
	font-size: 0.85rem;
	gap: 8px;
	color: var(--esd-ink, #0f1613);
}
.yrp-knobs__busy {
	font-size: 0.7rem;
	font-weight: 600;
	color: var(--esd-muted, #5f6e68);
}
.yrp-knobs__x {
	margin-left: auto;
	background: none;
	border: 0;
	cursor: pointer;
	color: var(--esd-muted, #5f6e68);
	font-size: 0.85rem;
	line-height: 1;
	padding: 2px 4px;
	font-family: inherit;
}
.yrp-knobs__x:hover {
	color: var(--esd-ink, #0f1613);
}

.yrp-knob {
	display: flex;
	align-items: center;
	justify-content: space-between;
	gap: 10px;
	font-size: 0.78rem;
}
.yrp-knob > label {
	color: var(--esd-muted, #5f6e68);
	font-weight: 650;
	flex: none;
}
.yrp-knob select {
	background: var(--esd-bg, #f2f6f4);
	color: var(--esd-ink, #0f1613);
	border: 1px solid var(--esd-line, #dfe8e4);
	border-radius: 8px;
	padding: 5px 8px;
	font-size: 0.78rem;
	width: 142px;
	font-family: inherit;
	cursor: pointer;
}

.yrp-knobs__swatches {
	display: flex;
	flex-wrap: wrap;
	gap: 4px;
	width: 142px;
	justify-content: flex-start;
}
.yrp-knobs__swatch {
	width: 20px;
	height: 20px;
	border-radius: 50%;
	border: 2px solid var(--esd-line, #dfe8e4);
	padding: 0;
	cursor: pointer;
	flex: none;
}
.yrp-knobs__swatch.active {
	border-color: var(--esd-ink, #0f1613);
	box-shadow: inset 0 0 0 2px var(--esd-card, #ffffff);
}

.yrp-knobs__reset {
	border: 1px solid var(--esd-line, #dfe8e4);
	background: var(--esd-bg, #f2f6f4);
	color: var(--esd-ink-2, #33413b);
	border-radius: 8px;
	padding: 7px 10px;
	font-size: 0.78rem;
	font-weight: 650;
	cursor: pointer;
	font-family: inherit;
}
.yrp-knobs__reset:hover:not(:disabled) {
	border-color: var(--esd-accent, #0e8c7f);
	color: var(--esd-accent-700, #0a5f58);
}
.yrp-knobs__reset:disabled {
	opacity: 0.6;
	cursor: default;
}

.yrp-knobs__note {
	font-size: 0.67rem;
	color: var(--esd-muted, #5f6e68);
	line-height: 1.45;
	border-top: 1px dashed var(--esd-line, #dfe8e4);
	padding-top: 9px;
}

.yrp-knobs__toast {
	background: #141a28;
	color: #fff;
	border-radius: 10px;
	padding: 9px 12px;
	font-size: 0.78rem;
	box-shadow: 0 10px 28px rgba(0, 0, 0, 0.35);
	max-width: 280px;
}
</style>
