// @yrp/web-engine — public exports (spec §6.1).
//
// Source-only package: no build step, no dist — raw .js/.vue compiled by the
// consuming app's Vite. Bare imports are `vue` and `pinia` ONLY (both
// peerDependencies, deduped by the host); every host service arrives through
// installEngine() below.

import { getContext, setContext } from "./context.js"

export { registerBlock, resolveBlock, listBlocks } from "./registry.js"
export { useUiConfigStore, ENGINE_SCHEMA_VERSION } from "./stores/uiConfig.js"
export { applyTheme, SHIPPED_ACCENT } from "./theme/applyTheme.js"
export { deriveAccentFamily, deriveDarkAccentFamily } from "./theme/derive.js"
export { STATUS_COLORS, STATUS_FALLBACK, statusColor, statusTint, statusChipStyle } from "./statusColors.js"
export { formatDate, formatDateTime } from "./format.js"
export { default as ScreenRenderer } from "./ScreenRenderer.vue"
export { default as BlockBoundary } from "./BlockBoundary.vue"
export { default as UnknownBlock } from "./UnknownBlock.vue"
export { default as KnobsPanel } from "./KnobsPanel.vue"
// Registered-block components (host's blocks/index.js imports + registerBlock's
// them — the engine only EXPORTS; registration stays a host decision, §6.3):
//   registerBlock("summary-tiles",    { component: SummaryTiles,    label: "KPI summary tiles" })
//   registerBlock("calculator-panel", { component: CalculatorPanel, label: "Calculator" })
export { default as SummaryTiles } from "./SummaryTiles.vue"
export { default as CalculatorPanel } from "./CalculatorPanel.vue"
export { getContext }

/** Injection key for components that prefer inject() over getContext(). */
export const ENGINE_CONTEXT_KEY = Symbol("yrp-web-engine-context")

/**
 * Install the engine into the host Vue app (§6.1 rule 2). Called once from the
 * host's main.js, BEFORE the ui-config store is first used:
 *
 *   installEngine(app, { isManager, applyMode, callMethod, goto })
 *
 * - isManager: () => boolean            — usePermissions' isAdmin || hasRole("System Manager")
 * - applyMode: (mode) => void           — class-only mode toggle; "user" restores the
 *                                         stored/OS choice; NEVER writes localStorage
 * - callMethod: (method, args) => Promise — the host's API client, verbatim; the
 *                                         engine's only sanctioned network channel
 * - goto: (target) => void              — host router navigation for block deep-links
 *                                         (target: { doctype, filters? } — filters in
 *                                         DynamicListPage's ?filters= triple shape);
 *                                         optional — defaults to a warning no-op
 */
export function installEngine(app, hostContext) {
	setContext(hostContext)
	app.provide(ENGINE_CONTEXT_KEY, getContext())
	return app
}
