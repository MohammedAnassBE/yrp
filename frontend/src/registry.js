// @yrp/web-engine — block registry (spec §6.3). The whole API. No magic.
//
// Registration is a build-time static import executed at module load (the
// host's `src/blocks/index.js` side-effect import) — no dynamic plugin
// loading, no config-driven imports (that would be code injection, §15).
// Block `type` strings from config resolve ONLY against this Map.

const registry = new Map()

export function registerBlock(type, { component, label }) {
	if (registry.has(type)) console.warn(`[yrp-web] block "${type}" re-registered — overriding`)
	registry.set(type, { component, label: label || type })
}

export const resolveBlock = (type) => registry.get(type) || null

export const listBlocks = () => [...registry.keys()] // v2 editor palette source
