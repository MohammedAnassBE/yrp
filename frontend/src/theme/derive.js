// @yrp/web-engine — accent-family derivation (spec §9).
//
// One hex anchor → the full light+dark --esd-accent* family, via a small
// self-contained HSL utility (no npm dependency; deliberately avoids
// @primeuix/themes' `palette` — runtime ramp regeneration would drift the
// hand-tuned shipped ramp). Constants are tuned against the shipped teal's
// own relationships (#0E8C7F ramp, #0E8C7F → #2FB8A6 dark lift), so a derived
// family keeps the same visual grammar as the default. The shipped default
// itself NEVER goes through this file (applyTheme's no-op guard).

// ── Tuning constants (measured from the shipped palette) ────────────────────
const LIGHT_600_DELTA = -0.04 // #0E8C7F L .302 → #0C7A6F L .263
const LIGHT_700_DELTA = -0.1 // #0E8C7F L .302 → #0A5F58 L .206
const WASH_LIGHTNESS = 0.93 // #E7F3F1 L .929
const WASH_SATURATION_RATIO = 0.4 // #E7F3F1 S .33 vs anchor S .82
const DARK_ACCENT_DELTA = 0.15 // #0E8C7F L .302 → #2FB8A6 L .453 (±5pt bar, §9)
const DARK_SATURATION_RATIO = 0.75 // #2FB8A6 S .59 vs anchor S .82
const DARK_700_DELTA = 0.12 // #2FB8A6 L .453 → #5ACCBC L .576
const DARK_WASH_ALPHA = 0.15 // --esd-accent-50 dark = rgba(accent, .15)
const MIN_LINK_CONTRAST = 4.5 // WCAG AA for accent-700 text on white

// ── Minimal HSL utility ─────────────────────────────────────────────────────

const clamp01 = (n) => Math.min(1, Math.max(0, n))

export function hexToRgb(hex) {
	const n = parseInt(hex.slice(1), 16)
	return { r: (n >> 16) & 255, g: (n >> 8) & 255, b: n & 255 }
}

function hexToHsl(hex) {
	let { r, g, b } = hexToRgb(hex)
	r /= 255
	g /= 255
	b /= 255
	const max = Math.max(r, g, b)
	const min = Math.min(r, g, b)
	const l = (max + min) / 2
	const d = max - min
	if (d === 0) return { h: 0, s: 0, l }
	const s = d / (1 - Math.abs(2 * l - 1))
	let h
	if (max === r) h = 60 * (((g - b) / d) % 6)
	else if (max === g) h = 60 * ((b - r) / d + 2)
	else h = 60 * ((r - g) / d + 4)
	return { h: (h + 360) % 360, s, l }
}

function hslToHex({ h, s, l }) {
	s = clamp01(s)
	l = clamp01(l)
	const c = (1 - Math.abs(2 * l - 1)) * s
	const x = c * (1 - Math.abs(((h / 60) % 2) - 1))
	const m = l - c / 2
	let [r, g, b] =
		h < 60 ? [c, x, 0] : h < 120 ? [x, c, 0] : h < 180 ? [0, c, x] : h < 240 ? [0, x, c] : h < 300 ? [x, 0, c] : [c, 0, x]
	const toHex = (v) =>
		Math.round((v + m) * 255)
			.toString(16)
			.padStart(2, "0")
	return `#${toHex(r)}${toHex(g)}${toHex(b)}`
}

// ── WCAG contrast (accent-700 is link-on-white text — must meet AA) ─────────

function relativeLuminance(hex) {
	const { r, g, b } = hexToRgb(hex)
	const lin = (v) => {
		const c = v / 255
		return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4
	}
	return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)
}

export function contrastOnWhite(hex) {
	return (1.0 + 0.05) / (relativeLuminance(hex) + 0.05)
}

function darkenUntilAA(hsl) {
	let out = { ...hsl }
	let hex = hslToHex(out)
	while (contrastOnWhite(hex) < MIN_LINK_CONTRAST && out.l > 0.02) {
		out = { ...out, l: out.l - 0.02 }
		hex = hslToHex(out)
	}
	return hex
}

// ── The derivation ──────────────────────────────────────────────────────────

/**
 * Explicit dark anchor (theme.dark.accent): the anchor IS the dark-scheme
 * accent — no DARK_ACCENT_DELTA lift (that lift only exists to derive a dark
 * accent from a LIGHT anchor). Only the -700/ink lift and wash alpha apply.
 * Returns one family in the same suffix-keyed shape as deriveAccentFamily's
 * `.dark` member.
 */
export function deriveDarkAccentFamily(anchor) {
	const base = hexToHsl(anchor)
	const dark700 = hslToHex({ ...base, l: clamp01(base.l + DARK_700_DELTA) })
	const { r, g, b } = hexToRgb(anchor)
	return {
		accent: anchor,
		"accent-50": `rgba(${r}, ${g}, ${b}, ${DARK_WASH_ALPHA})`, // wash on dark
		"accent-600": anchor, // shipped dark grammar: -600 == accent
		"accent-700": dark700,
		"accent-ink": dark700, // shipped dark grammar: ink == -700
	}
}

/**
 * anchor: validated "#rrggbb" → both mode families, keyed by the CSS custom
 * property suffixes applyTheme writes ("--esd-accent" + these keys).
 */
export function deriveAccentFamily(anchor) {
	const base = hexToHsl(anchor)

	const darkAccentHsl = {
		h: base.h,
		s: base.s * DARK_SATURATION_RATIO,
		l: clamp01(base.l + DARK_ACCENT_DELTA),
	}
	const darkAccent = hslToHex(darkAccentHsl)
	const dark700 = hslToHex({ ...darkAccentHsl, l: clamp01(darkAccentHsl.l + DARK_700_DELTA) })
	const { r, g, b } = hexToRgb(darkAccent)

	const light700 = darkenUntilAA({ ...base, l: clamp01(base.l + LIGHT_700_DELTA) })

	const light = {
		accent: anchor,
		"accent-50": hslToHex({ ...base, s: base.s * WASH_SATURATION_RATIO, l: WASH_LIGHTNESS }),
		"accent-600": hslToHex({ ...base, l: clamp01(base.l + LIGHT_600_DELTA) }),
		"accent-700": light700,
		"accent-ink": light700, // shipped: --esd-accent-ink = var(--esd-accent-700)
	}

	const dark = {
		accent: darkAccent,
		"accent-50": `rgba(${r}, ${g}, ${b}, ${DARK_WASH_ALPHA})`, // wash on dark
		"accent-600": darkAccent, // shipped dark: -600 == accent (#2FB8A6)
		"accent-700": dark700,
		"accent-ink": dark700, // shipped dark: ink == -700 (#5ACCBC)
	}

	return { light, dark }
}
