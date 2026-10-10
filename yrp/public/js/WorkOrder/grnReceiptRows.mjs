function toNumber(value) {
	const numberValue = Number(value || 0);
	return Number.isFinite(numberValue) ? numberValue : 0;
}

function valueDetail(entry, key) {
	if (!entry.values) entry.values = {};
	if (!entry.values[key]) entry.values[key] = { qty: 0 };
	return entry.values[key];
}

function stripReceivedType(dimensions) {
	const out = {};
	for (const [fieldname, value] of Object.entries(dimensions || {})) {
		if (fieldname !== "received_type") out[fieldname] = value;
	}
	return out;
}

function receivedType(entry) {
	return (entry.dimensions || {}).received_type || "";
}

function normalizedSetCombination(value) {
	if (!value) return {};
	if (typeof value === "string") {
		try {
			return JSON.parse(value);
		} catch (_error) {
			return { value };
		}
	}
	return value;
}

function getColumns(group, entry) {
	const values = entry.values || {};
	const primaryValues = group.primary_attribute_values || [];
	if (primaryValues.length && !Object.prototype.hasOwnProperty.call(values, "default")) {
		return primaryValues.map((value) => ({ key: value, label: value }));
	}
	return [{ key: "default", label: "Qty" }];
}

function receiptReferences(entry, columns) {
	return columns.map((column) => [
		column.key,
		(entry.values || {})[column.key]?.ref_docname || entry.ref_docname || "",
	]);
}

export function buildReceiptRowKey(group, entry) {
	const dimensions = stripReceivedType(entry.dimensions || {});
	const columns = getColumns(group, entry);
	return stableKey({
		name: entry.name,
		dimensions,
		attributes: entry.attributes || {},
		setCombination: normalizedSetCombination(entry.set_combination),
		columns: columns.map((column) => column.key),
		references: receiptReferences(entry, columns),
	});
}

function sortObject(value) {
	if (Array.isArray(value)) return value.map((item) => sortObject(item));
	if (!value || typeof value !== "object") return value;
	const out = {};
	for (const key of Object.keys(value).sort()) out[key] = sortObject(value[key]);
	return out;
}

function stableKey(value) {
	return JSON.stringify(sortObject(value));
}

export function buildLogicalReceiptRows(items, { aggregatePhysicalRows = false } = {}) {
	const rows = [];
	const byKey = new Map();
	for (const group of items || []) {
		for (const entry of group.items || []) {
			const dimensions = stripReceivedType(entry.dimensions || {});
			const attributes = entry.attributes || {};
			const columns = getColumns(group, entry);
			const setCombination = normalizedSetCombination(entry.set_combination);
			const key = buildReceiptRowKey(group, entry);
			if (!byKey.has(key)) {
				const row = {
					key,
					name: entry.name,
					dimensions,
					dimensionFields: Object.keys(dimensions).filter((fieldname) => dimensions[fieldname]),
					attributes,
					setCombination,
					attributeFields: Object.keys(attributes).filter((fieldname) => attributes[fieldname]),
					columns,
					defaultUom: entry.default_uom || "",
					splits: [],
				};
				byKey.set(key, row);
				rows.push(row);
			}

			const row = byKey.get(key);
			const type = receivedType(entry);
			let split = aggregatePhysicalRows
				? row.splits.find((candidate) => candidate.receivedType === type)
				: null;
			if (!split) {
				split = {
					key: `${key}::${type}`,
					receivedType: type,
					entry: aggregatePhysicalRows
						? JSON.parse(JSON.stringify(entry))
						: entry,
					sourceEntries: [entry],
				};
				row.splits.push(split);
				continue;
			}

			split.sourceEntries.push(entry);
			for (const column of columns) {
				const target = valueDetail(split.entry, column.key);
				const source = valueDetail(entry, column.key);
				target.qty = toNumber(target.qty) + toNumber(source.qty);
				for (const fieldname of ["pending_quantity", "max_receivable_quantity"]) {
					if (source[fieldname] !== undefined && source[fieldname] !== null) {
						target[fieldname] = toNumber(target[fieldname]) + toNumber(source[fieldname]);
					}
				}
			}
		}
	}
	for (const row of rows) {
		row.splits.sort((a, b) => (a.receivedType || "").localeCompare(b.receivedType || ""));
	}
	return rows;
}

export function setSplitQuantity(split, key, value, { allowExcess = false, maxValue = Infinity } = {}) {
	let quantity = Math.max(toNumber(value), 0);
	if (!allowExcess && Number.isFinite(maxValue)) quantity = Math.min(quantity, maxValue);
	const sources = split.sourceEntries || [split.entry];
	valueDetail(sources[0], key).qty = quantity;
	for (const source of sources.slice(1)) valueDetail(source, key).qty = 0;
	valueDetail(split.entry, key).qty = quantity;
	return quantity;
}

export function removeSplitSources(items, split) {
	const sources = new Set(split.sourceEntries || [split.entry]);
	for (const group of items || []) {
		const before = (group.items || []).length;
		group.items = (group.items || []).filter((entry) => !sources.has(entry));
		if (group.items.length !== before) return true;
	}
	return false;
}
