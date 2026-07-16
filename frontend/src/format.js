// @yrp/web-engine — layout-aware display formatting.
//
// formatDate / formatDateTime honour the layout's `dateFormat` knob (demo
// _template.html fmtDate). The DEFAULT — knob absent — is the current /web
// display format: dd-mm-yyyy with an em-dash empty placeholder, copied
// verbatim from the host's DynamicListPage/DocDetail formatters so call sites
// can migrate here with ZERO pixel change. Passing "yyyy-mm-dd" renders the
// raw ISO date part instead.

const EMPTY = "—"

/**
 * value: "yyyy-mm-dd" or "yyyy-mm-dd hh:mm:ss" (Frappe Date/Datetime).
 * dateFormat: "dd-mm-yyyy" (also the default when absent) | "yyyy-mm-dd".
 */
export function formatDate(value, dateFormat) {
	if (!value) return EMPTY
	const datePart = String(value).split(" ")[0]
	const [y, m, d] = datePart.split("-")
	if (!(y && m && d)) return value
	if (dateFormat === "yyyy-mm-dd") return datePart
	return `${d}-${m}-${y}` // "dd-mm-yyyy" — and the shipped default
}

/** Datetime: formatted date + "hh:mm" (host DocDetail formatDateTime). */
export function formatDateTime(value, dateFormat) {
	if (!value) return EMPTY
	const [datePart, timePart] = String(value).split(" ")
	const dateStr = formatDate(datePart, dateFormat)
	const timeStr = timePart ? timePart.slice(0, 5) : ""
	return timeStr ? `${dateStr} ${timeStr}` : dateStr
}
