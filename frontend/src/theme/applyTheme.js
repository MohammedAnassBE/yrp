// @yrp/web-engine — theme applier (spec §9, extended to the Demo-7 token set).
//
// applyTheme(theme) is the ONLY code that touches the document for layout
// theming, and it is idempotent: the token <style> element is replaced
// wholesale on every run (re-runs reactively via the host main.js watch —
// preview enter/exit, refresh).
//
// PARITY LAW: a CSS custom property is written ONLY for keys PRESENT in the
// layout theme. Absent keys emit nothing — and because the element is rebuilt
// wholesale, a removed key also removes its property — so the host's CSS
// fallbacks (var(--yrp-*, <shipped exact value>)) rule. A layout with no theme
// knobs (the Default) therefore renders pixel-identical to the shipped UI.
//
// Token semantics mirror the demo engine ("custom ui/demos/_template.html"
// applyTheme):
//   bg / surface / text      page ground, card surface, primary ink (#rrggbb)
//   muted / line / surface2  explicit, else derived from present anchors:
//                            rgba(text,.62) / rgba(text, dark ? .15 : .12) /
//                            blend(surface, bg, .55)
//   radius                   card radius px (+ derived -sm / -lg host steps)
//   density                  compact|comfortable|spacious → pad/gap/row px
//   fontScale                body type multiplier (1 = shipped size)
//   font                     font-stack override
//   dark {…}                 overlay palette for the .dark scheme: the
//                            effective dark theme is {...theme, ...theme.dark}
//                            (the demo's effTheme()), written into a `.dark {}`
//                            block so the host's mode class picks it up.

import { getContext } from "../context.js"
import { deriveAccentFamily, deriveDarkAccentFamily, hexToRgb } from "./derive.js"

// The shipped teal anchor (--esd-accent in the host's global.css). An accent
// equal to it — like absent/null — must be a pixel-parity no-op: no derivation
// ever runs at go-live.
export const SHIPPED_ACCENT = "#0E8C7F"

const STYLE_ID = "yrp-theme-tokens"
const HEX_RE = /^#[0-9a-fA-F]{6}$/
// Conservative explicit-colour forms for the exact tokens (muted/line/surface2
// may carry an rgba() wash). Values land inside a <style> element — anything
// looser would be a CSS-injection channel, so unknown forms are dropped.
const RGBA_RE = /^rgba?\(\s*\d{1,3}\s*,\s*\d{1,3}\s*,\s*\d{1,3}\s*(?:,\s*(?:0|1|0?\.\d{1,4})\s*)?\)$/
// Font stacks: word chars, spaces, commas, quotes, hyphens only (same guard).
const FONT_RE = /^[\w\s,'"-]{1,200}$/

// Demo density table (px) — _template.html applyTheme, verbatim.
const DENSITIES = {
	compact: { pad: 10, gap: 10, row: 38 },
	comfortable: { pad: 14, gap: 14, row: 46 },
	spacious: { pad: 18, gap: 18, row: 58 },
}

const isPlainObject = (v) => v !== null && typeof v === "object" && !Array.isArray(v)
const isColor = (v) => typeof v === "string" && (HEX_RE.test(v) || RGBA_RE.test(v))

// The color-token vocabulary (mirrored by THEME_COLOR_KEYS in ui_config.py).
// Stripped from the .dark source when the theme ships NO dark{} overlay, so a
// light-only palette never renders light cards/ink in dark mode.
const COLOR_TOKEN_KEYS = ["bg", "surface", "text", "muted", "line", "surface2"]

function withoutColorTokens(t) {
	const out = { ...t }
	for (const key of COLOR_TOKEN_KEYS) delete out[key]
	return out
}

function rgba(hex, alpha) {
	const { r, g, b } = hexToRgb(hex)
	return `rgba(${r}, ${g}, ${b}, ${alpha})`
}

// Demo blend(): mix hexA toward hexB by t (0..1) — used for the surface2 step.
function blend(hexA, hexB, t) {
	const a = hexToRgb(hexA)
	const b = hexToRgb(hexB)
	const mix = (x, y) => Math.round(x + (y - x) * t)
	return `rgb(${mix(a.r, b.r)}, ${mix(a.g, b.g)}, ${mix(a.b, b.b)})`
}

function removeTokenStyle() {
	document.getElementById(STYLE_ID)?.remove()
}

function warnInvalid(key, value) {
	console.warn(`[yrp-web] invalid theme.${key} ${JSON.stringify(value)} — ignoring`)
}

/**
 * One scheme's --yrp-* property map from an effective single-mode theme
 * (the light theme, or {...theme, ...theme.dark} for the .dark block).
 * Only keys PRESENT (or derivable from present anchors) emit a property.
 */
function tokenVars(t, dark) {
	const vars = {}
	// explicit colour token: validated + written; returns the value when usable
	const color = (key, prop) => {
		const v = t[key]
		if (v == null) return null
		if (!isColor(v)) {
			warnInvalid(key, v)
			return null
		}
		vars[prop] = v
		return v
	}

	const bg = color("bg", "--yrp-bg")
	const surface = color("surface", "--yrp-surface")
	color("text", "--yrp-text")
	const textHex = typeof t.text === "string" && HEX_RE.test(t.text) ? t.text : null

	// Derived steps (demo applyTheme) — explicit keys always win.
	if (!color("muted", "--yrp-muted") && textHex) vars["--yrp-muted"] = rgba(textHex, 0.62)
	if (!color("line", "--yrp-line") && textHex) vars["--yrp-line"] = rgba(textHex, dark ? 0.15 : 0.12)
	if (!color("surface2", "--yrp-surface-2") && surface && bg && HEX_RE.test(surface) && HEX_RE.test(bg))
		vars["--yrp-surface-2"] = blend(surface, bg, 0.55)
	// Host secondary ink steps (--esd-ink-2 / --esd-muted-2) ride the text anchor;
	// alphas measured against the shipped pairs (#33413B ≈ ink@.85, #98A5A0 ≈ ink@.45).
	if (textHex) {
		vars["--yrp-text-2"] = rgba(textHex, 0.85)
		vars["--yrp-muted-2"] = rgba(textHex, 0.45)
		const { r, g, b } = hexToRgb(textHex)
		vars["--yrp-text-rgb"] = `${r} ${g} ${b}` // host --c-ink triplet form
	}
	if (bg && HEX_RE.test(bg)) {
		const { r, g, b } = hexToRgb(bg)
		vars["--yrp-bg-rgb"] = `${r} ${g} ${b}` // host --c-surface triplet form
	}

	if (t.radius != null) {
		const r = Number(t.radius)
		if (Number.isFinite(r) && r >= 0 && r <= 60) {
			vars["--yrp-radius"] = `${r}px`
			vars["--yrp-radius-sm"] = `${Math.max(0, r - 3)}px` // shipped step: 12 → 9
			vars["--yrp-radius-lg"] = `${r + 2}px` // shipped step: 12 → 14
		} else warnInvalid("radius", t.radius)
	}

	if (t.density != null) {
		const den = DENSITIES[t.density]
		if (den) {
			vars["--yrp-pad"] = `${den.pad}px`
			vars["--yrp-gap"] = `${den.gap}px`
			vars["--yrp-row"] = `${den.row}px`
		} else warnInvalid("density", t.density)
	}

	if (t.fontScale != null) {
		const s = Number(t.fontScale)
		if (Number.isFinite(s) && s >= 0.5 && s <= 2) vars["--yrp-font-scale"] = String(s)
		else warnInvalid("fontScale", t.fontScale)
	}

	if (t.font != null) {
		if (typeof t.font === "string" && FONT_RE.test(t.font)) vars["--yrp-font"] = t.font
		else warnInvalid("font", t.font)
	}

	return vars
}

function normalizeAccent(accent, label) {
	if (!accent) return null
	// Client-side re-check as belt (server already validated at save time,
	// §14 row 13): invalid accent → shipped palette, never a broken ramp.
	if (typeof accent !== "string" || !HEX_RE.test(accent)) {
		console.warn(`[yrp-web] invalid theme ${label} ${JSON.stringify(accent)} — keeping shipped palette`)
		return null
	}
	return accent
}

const familyLines = (family) =>
	family
		? Object.entries(family).map(([suffix, value]) =>
				suffix === "accent" ? `\t--esd-accent: ${value};` : `\t--esd-${suffix}: ${value};`
			)
		: []

const varLines = (vars) => Object.entries(vars).map(([prop, value]) => `\t${prop}: ${value};`)

export function applyTheme(theme) {
	const t = isPlainObject(theme) ? theme : {}

	// 1. Mode: "light"/"dark" force the class ONLY — never localStorage, so a
	//    forced-mode layout can't ratchet the user's stored choice and View-as
	//    can't pollute the SM's. Anything else = "user": the host re-applies
	//    the stored/OS choice (§9 item 1, §19 nit 2).
	getContext().applyMode(t.mode === "light" || t.mode === "dark" ? t.mode : "user")

	// 2. Per-scheme token maps. WITH a dark{} overlay the .dark block is the
	//    OVERLAID theme (demo effTheme()): dark{} keys win, un-overridden keys
	//    keep their light values in dark — and dark-sensitive derivations (line
	//    alpha) are recomputed for the dark scheme.
	//    WITHOUT an overlay, light COLOR tokens must NOT leak into .dark: a
	//    light-only theme (surface #ffffff, mode "user") would paint white
	//    cards under the host's light dark-mode ink — illegible. Non-color
	//    tokens (radius/density/fontScale/font) are scheme-neutral and carry;
	//    colors fall back to the host's shipped dark palette.
	const overlay = isPlainObject(t.dark) ? t.dark : null
	const lightVars = tokenVars(t, false)
	const darkVars = tokenVars(overlay ? { ...t, ...overlay } : withoutColorTokens(t), true)

	// 3. Accent families (§9): absent/null/shipped stays a strict no-op for the
	//    light family; an explicit dark{} accent replaces the derived dark one.
	let accentLight = null
	let accentDark = null
	const accent = normalizeAccent(t.accent, "accent")
	if (accent && accent.toLowerCase() !== SHIPPED_ACCENT.toLowerCase()) {
		const family = deriveAccentFamily(accent)
		accentLight = family.light
		accentDark = family.dark
	}
	const darkAccent = overlay ? normalizeAccent(overlay.accent, "dark.accent") : null
	if (darkAccent && darkAccent.toLowerCase() !== SHIPPED_ACCENT.toLowerCase())
		accentDark = deriveDarkAccentFamily(darkAccent)

	const lightLines = [...familyLines(accentLight), ...varLines(lightVars)]
	const darkLines = [...familyLines(accentDark), ...varLines(darkVars)]

	// 4. Nothing to write → remove the element entirely (idempotent reset;
	//    guarantees the default path is pixel-identical).
	if (!lightLines.length && !darkLines.length) {
		removeTokenStyle()
		return
	}

	// Both blocks, scoped exactly like the shipped overrides in global.css
	// (:root + .dark), appended to <head> so it wins the cascade over the
	// bundled stylesheet at equal specificity. Dark mode keeps working.
	const css =
		(lightLines.length ? `:root {\n${lightLines.join("\n")}\n}\n` : "") +
		(darkLines.length ? `.dark {\n${darkLines.join("\n")}\n}\n` : "")

	let el = document.getElementById(STYLE_ID)
	if (!el) {
		el = document.createElement("style")
		el.id = STYLE_ID
		document.head.appendChild(el)
	}
	el.textContent = css
}
