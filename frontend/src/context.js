// @yrp/web-engine — module-level injected host context (spec §6.1).
//
// The engine's only bare imports are `vue` and `pinia`. Everything else it
// needs from the host app — the manager gate, the theme-mode setter, the API
// client — is INJECTED here by installEngine() and read back via getContext().
// The engine never imports `@/...` or any app path (dependency direction stays
// engine ← app), and it never *imports* a network client: the injected
// `callMethod` is the single sanctioned channel to the server (§19 nit 1).

const defaults = {
	// Host contract: () => boolean — usePermissions' isAdmin || hasRole("System Manager").
	isManager: () => false,
	// Host contract: (mode: "light"|"dark"|"user") => void — toggles the `.dark`
	// class only, NEVER writes localStorage["essdee-theme"]; "user" re-applies
	// the user's own stored/OS choice (§9 item 1, §19 nit 2).
	applyMode: () => {},
	// Host contract: (method: string, args: object) => Promise — the app's
	// existing API client function, verbatim. Backs store.refresh()/previewAs().
	callMethod: async () => {
		throw new Error(
			"[yrp-web] callMethod was not injected — call installEngine(app, { callMethod }) before using the engine"
		)
	},
	// Host contract: (target: { doctype: string, filters?: [[field, op, value], …] }) => void
	// — the host's router navigation (vue-router push under the hood; route to
	// the doctype's list with `filters` as the ?filters= base filter, the exact
	// triple shape DynamicListPage parses — see ui_metrics.py `goto`). Engine
	// blocks never import vue-router — a metric's `goto` deep-link
	// (summary-tiles ↗) flows through here verbatim. Default is a safe no-op so
	// a host that hasn't wired navigation yet degrades to inert tiles.
	goto: (target) => {
		console.warn(
			"[yrp-web] goto was not injected — call installEngine(app, { goto }) to enable block navigation",
			target
		)
	},
}

let context = { ...defaults }

/** Called by installEngine(); unknown keys are carried through untouched. */
export function setContext(injected) {
	context = { ...defaults, ...(injected || {}) }
}

/** The live host context. Engine modules read services from here, lazily. */
export function getContext() {
	return context
}
