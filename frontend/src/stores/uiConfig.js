// @yrp/web-engine — the UI-config Pinia store (spec §8.2).
//
// Hydrated pre-mount by the host's main.js: loadFromBoot(frappe.boot.ui_config,
// compiledFallback). Every consumer (sidebar, palette, home, list columns)
// reads arrangement from here and NOWHERE else. Arrangement never grants
// capability: hosts AND canRead()/canCreate() on top at render (§15).

import { defineStore } from "pinia"
import { getContext } from "../context.js"

// Engine copy of the config schema version — bumped in LOCKSTEP with
// CURRENT_SCHEMA_VERSION in apps/yrp/yrp/yrp/api/ui_config.py (§2.3 rule 1).
export const ENGINE_SCHEMA_VERSION = 1

function isPlainObject(v) {
	return v !== null && typeof v === "object" && !Array.isArray(v)
}

/**
 * Guard a {config, meta} payload (boot or refresh). Returns the payload when
 * usable, else null after a console.warn — the caller stays on its current
 * config/fallback (§2.3 rule 7; §14 rows 14/16/17).
 */
function guardPayload(payload, source) {
	const cfg = payload?.config
	if (!isPlainObject(cfg)) {
		if (payload != null) console.warn(`[yrp-web] ${source}: no usable config — using fallback`)
		return null
	}
	// Rule 7: never guess-interpret a NEWER schema (old bundle, newer server).
	const version = cfg.schema_version
	if (typeof version === "number" && version > ENGINE_SCHEMA_VERSION) {
		console.warn(
			`[yrp-web] ${source}: schema_version ${version} > engine ${ENGINE_SCHEMA_VERSION} — using fallback`
		)
		return null
	}
	// Sanity: an empty-nav-AND-empty-home config (e.g. kill-switch skeleton,
	// §14 row 17) renders nothing useful — the compiled fallback is today's UI.
	const hasNav = Boolean(cfg.nav?.groups?.length)
	const hasHome = Boolean(cfg.screens?.home?.blocks?.length)
	if (!hasNav && !hasHome) {
		console.warn(`[yrp-web] ${source}: config has empty nav and empty home — using fallback`)
		return null
	}
	return payload
}

export const useUiConfigStore = defineStore("yrpUiConfig", {
	state: () => ({
		config: null, // server-resolved config, or null → getters fall back
		meta: null, // { layout, has_preference, schema_version, warnings } | null
		fallback: null, // compiled-in Default (host-supplied via loadFromBoot, §12.3)
		previewUser: null, // §10 View-as — target user while previewing, else null
		previewLayout: null, // §10 sandbox — bare layout name while previewing, else null
		previewPermHints: null, // { can_read, can_create } computed AS the target
		stash: null, // SM's own {config, meta} parked during preview
	}),

	getters: {
		/** The config every consumer renders from — never null once hydrated. */
		active(state) {
			return state.config || state.fallback || {}
		},

		/** True while ANY §10 preview (user or bare layout) is active. */
		previewing(state) {
			return Boolean(state.previewUser || state.previewLayout)
		},

		/** Nav groups with hidden items filtered out; empty groups dropped. */
		navGroups() {
			const nav = this.active.nav || {}
			const hidden = nav.hidden || {}
			return (nav.groups || [])
				.map((g) => ({
					...g,
					items: (g.items || []).filter((item) => hidden[item.doctype] !== true),
				}))
				.filter((g) => g.items.length > 0)
		},

		homeScreen() {
			return this.active.screens?.home || null
		},

		quickCreate() {
			return this.active.quickCreate || []
		},

		/** Layout-tier list columns for a DocType, or null → caller's fallback. */
		listColumns() {
			return (doctype) => this.active.listViews?.[doctype]?.columns || null
		},

		// ── Structural knobs (spec §6.4, server STRUCTURAL_KEYS) ──────────────
		// Null-safe accessors: each returns the layout's knob object or null.
		// PARITY LAW: null (knob absent) means "today's behaviour exactly" —
		// consumers must treat null as the current full-page/header rendering.

		/** `detail` knob ({position: "page"|"right"|"center"|"bottom-sheet"}) or null. */
		detailKnob() {
			return this.active.detail || null
		},

		/** `entry` knob ({mode: "page"|"popup", popupPosition}) or null. */
		entryKnob() {
			return this.active.entry || null
		},

		/** `dcEntry` knob ({variant, qtyControl, supplierPicker}) or null. */
		dcEntryKnob() {
			return this.active.dcEntry || null
		},

		/**
		 * `actions` knob ({placement, items}) or null. `dialogPosition` is a
		 * RESERVED knob name — the server validates it (ui_config.ACTIONS_KEYS)
		 * but no consumer reads it yet; all action dialogs open center today.
		 */
		actionsKnob() {
			return this.active.actions || null
		},
	},

	actions: {
		/**
		 * Hydrate from the boot payload (§8.2): `boot` is frappe.boot.ui_config
		 * ({config, meta} or null — §8.1) and `fallback` the compiled-in Default
		 * config object. A rejected/missing boot leaves `config` null so `active`
		 * serves the fallback — today's UI, structurally unreachable white screen.
		 */
		loadFromBoot(boot, fallback) {
			if (fallback !== undefined) this.fallback = fallback
			const payload = guardPayload(boot, "boot ui_config")
			if (!payload) return
			this.config = payload.config
			this.meta = payload.meta || null
		},

		/**
		 * Apply a server-returned {config, meta} payload through the SAME
		 * guardPayload gate as boot/refresh. Backs the Knobs panel's save/reset
		 * reconciliation — a degraded resolution (kill-switch skeleton, empty
		 * nav+home) must never blank the rendered UI just because it arrived via
		 * a save response instead of boot. Returns true when applied.
		 *
		 * No-op during §10 preview: any own-config payload arriving mid-preview
		 * (a racing realtime ui-config refresh, a stray save response) must not
		 * clobber the previewed config — it lands in the STASH instead, so the
		 * fresher own config is what Exit restores.
		 */
		applyServerPayload(payload, source = "server payload") {
			const guarded = guardPayload(payload, source)
			if (!guarded) return false
			if (this.previewing) {
				this.stash = { config: guarded.config, meta: guarded.meta || null }
				console.warn(`[yrp-web] ${source}: preview active — stashed, not applied`)
				return false
			}
			this.config = guarded.config
			this.meta = guarded.meta || null
			return true
		},

		/** Re-fetch the session user's config ("Refresh UI"). No-op in preview. */
		async refresh() {
			if (this.previewing) return
			const result = await getContext().callMethod("yrp.yrp.api.ui_config.get_my_ui_config", {})
			const payload = guardPayload(result, "refresh")
			if (!payload) return
			this.config = payload.config
			this.meta = payload.meta || null
		},

		/**
		 * §10 View-as: swap in the target's resolved config + perm hints.
		 * `target` is either a user id string (back-compat) or `{ user }` /
		 * `{ layout }` — exactly one, mirroring the server's mutually-exclusive
		 * params. `user=` previews a person (their layers, hints computed AS
		 * them); `layout=` previews a bare layout with no overrides (sandbox
		 * mode; hints = the caller's own). Appearance only — session, data and
		 * real permissions stay the SM's. The previewed config is applied even
		 * when meta.warnings is non-empty: preview is diagnostic, the SM must
		 * SEE what a warned layout renders. Throws through to the caller on
		 * PermissionError / unknown user / unknown-disabled-broken layout:
		 * nothing changes on failure.
		 */
		async previewAs(target) {
			const opts = typeof target === "string" ? { user: target } : target || {}
			const args = opts.layout ? { layout: opts.layout } : { user: opts.user }
			const result = await getContext().callMethod(
				"yrp.yrp.api.ui_config.get_ui_config_for",
				args
			)
			// Stash the SM's own config only when ENTERING preview — switching
			// target mid-preview must keep the original stash intact.
			if (!this.previewing) this.stash = { config: this.config, meta: this.meta }
			this.config = result?.config || null
			this.meta = result?.meta || null
			this.previewUser = opts.layout ? null : opts.user || null
			this.previewLayout = opts.layout || null
			this.previewPermHints = result?.perm_hints || null
		},

		/** Restore the SM's own config. Memory-only — a full reload also exits. */
		exitPreview() {
			if (!this.previewing) return
			this.config = this.stash?.config ?? null
			this.meta = this.stash?.meta ?? null
			this.stash = null
			this.previewUser = null
			this.previewLayout = null
			this.previewPermHints = null
		},
	},
})
