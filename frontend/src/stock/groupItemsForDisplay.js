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

const ROUTE_ONLY_FIELDS = new Set([
	"fabric_reference_variant",
	"fabric_reference_allocations",
	"row_index",
	"table_index",
])

function clone(value) {
	// Vue stores loaded table rows as reactive Proxy objects. Browsers expose
	// structuredClone(), but it throws DataCloneError for a Proxy and prevents
	// the entire grouped table from rendering. JSON data is the editor's actual
	// contract, so a JSON clone is both sufficient and Proxy-safe.
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

function displayIdentity(item) {
	const entry = {}
	for (const [key, value] of Object.entries(item || {})) {
		if (key === "values" || ADDITIVE_FIELDS.has(key) || ROUTE_ONLY_FIELDS.has(key)) continue
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
 * Aggregate route-split Work Order rows for read-only presentation.
 *
 * Fabric calculations intentionally retain one flat child row per
 * `fabric_reference_variant`, because later cloth-program tracking needs that
 * route reference. The item editor does not need to repeat the same physical
 * yarn/cloth variant once per route, so this function sums display-equivalent
 * entries while leaving the source grouped JSON untouched for round-tripping.
 */
export function groupItemsForDisplay(groups) {
	return (groups || []).map((group) => {
		const byIdentity = new Map()
		const items = []

		for (const source of group.items || []) {
			const identity = displayIdentity(source)
			let target = byIdentity.get(identity)
			if (!target) {
				target = clone(source)
				delete target.fabric_reference_variant
				delete target.fabric_reference_allocations
				delete target.row_index
				delete target.table_index
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
