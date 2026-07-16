// @yrp/web-engine — named status-colour registry + chip helpers.
//
// Single source for status colours across every layout. Hex families are the
// demo engine's ("custom ui/demos/_template.html" STATUS_COLORS — validated
// for light AND dark surfaces); the status→family mapping is the locked user
// decision of 2026-07-15:
//
//   Draft / Pending            = orange
//   In Process / Submitted     = blue
//   Fully Received / Completed = green
//   Cancelled                  = grey
//   Delayed                    = red   (demo family, kept for list alerts)
//
// Registry law: a layout may only NAME a status — the colour pairs live here,
// never in layout JSON. Unknown statuses fall back to grey.

const ORANGE = { light: "#b45309", dark: "#f59e0b" }
const BLUE = { light: "#2563eb", dark: "#60a5fa" }
const GREEN = { light: "#047857", dark: "#34d399" }
const RED = { light: "#dc2626", dark: "#f87171" }
const GREY = { light: "#6b7280", dark: "#9ca3af" }

export const STATUS_FALLBACK = GREY

export const STATUS_COLORS = {
	Draft: ORANGE,
	Pending: ORANGE,
	"In Process": BLUE,
	Submitted: BLUE,
	"Fully Received": GREEN,
	Completed: GREEN,
	Cancelled: GREY,
	Delayed: RED,
}

/** Ink colour for a status chip/tile. `dark` = the active scheme is dark. */
export function statusColor(status, dark = false) {
	return (STATUS_COLORS[status] || STATUS_FALLBACK)[dark ? "dark" : "light"]
}

// Demo chip wash: rgba(colour, .14) — same alpha on light and dark surfaces.
const TINT_ALPHA = 0.14

/** Translucent wash of the status colour (chip/tile background). */
export function statusTint(status, dark = false, alpha = TINT_ALPHA) {
	const hex = statusColor(status, dark)
	const n = parseInt(hex.slice(1), 16)
	return `rgba(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}, ${alpha})`
}

/** Inline-style pair for a status chip (demo statusChip): ink + tinted wash. */
export function statusChipStyle(status, dark = false) {
	return { color: statusColor(status, dark), background: statusTint(status, dark) }
}
