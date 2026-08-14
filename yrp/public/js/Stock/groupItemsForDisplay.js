// Generic display-only aggregation for the base Desk stock editor.
const ADDITIVE_FIELDS = new Set([
	"qty",
	"pending_quantity",
	"pending_qty",
	"stock_update",
	"total_cost",
	"secondary_qty",
	"cancelled_quantity",
	"cancelled_qty",
])

function clone(value) {
	return JSON.parse(JSON.stringify(value))
}

function canonical(value) {
	if (Array.isArray(value)) return value.map(canonical)
	if (value && typeof value === "object") {
		return Object.fromEntries(
			Object.keys(value).sort().map((key) => [key, canonical(value[key])]),
		)
	}
	return value
}

function add(left, right) {
	return Math.round(((Number(left) || 0) + (Number(right) || 0)) * 1e9) / 1e9
}

function displayIdentity(item, routeOnlyFields) {
	const entry = {}
	for (const [key, value] of Object.entries(item || {})) {
		if (key === "values" || ADDITIVE_FIELDS.has(key) || routeOnlyFields.has(key)) continue
		entry[key] = value
	}

	const values = {}
	for (const [primaryValue, cell] of Object.entries(item?.values || {})) {
		values[primaryValue] = Object.fromEntries(
			Object.entries(cell || {}).filter(([key]) => !ADDITIVE_FIELDS.has(key)),
		)
	}
	return JSON.stringify(canonical({ entry, values }))
}

function mergeAdditiveFields(target, source) {
	for (const fieldname of ADDITIVE_FIELDS) {
		if (
			Object.prototype.hasOwnProperty.call(target || {}, fieldname)
			|| Object.prototype.hasOwnProperty.call(source || {}, fieldname)
		) {
			target[fieldname] = add(target[fieldname], source[fieldname])
		}
	}
}

/**
 * Aggregate equivalent rows for read-only display without mutating the source.
 * The consuming app names any business-specific route fields to ignore.
 */
export function groupItemsForDisplay(groups, routeFields = []) {
	const routeOnlyFields = new Set(["row_index", "table_index", ...routeFields])
	return (groups || []).map((group) => {
		const byIdentity = new Map()
		const items = []

		for (const source of group.items || []) {
			const identity = displayIdentity(source, routeOnlyFields)
			let target = byIdentity.get(identity)
			if (!target) {
				target = clone(source)
				for (const fieldname of routeOnlyFields) delete target[fieldname]
				byIdentity.set(identity, target)
				items.push(target)
				continue
			}

			mergeAdditiveFields(target, source)
			for (const [primaryValue, sourceCell] of Object.entries(source.values || {})) {
				if (!target.values[primaryValue]) {
					target.values[primaryValue] = clone(sourceCell)
					continue
				}
				mergeAdditiveFields(target.values[primaryValue], sourceCell)
			}
		}

		return { ...clone(group), items }
	})
}
