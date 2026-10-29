<template>
    <div class="grn-compact-editor">
        <div v-if="!logicalRows.length" class="text-muted small">
			{{ returnMode ? 'No return items' : 'No pending receivables' }}
		</div>
        <div v-for="(row, rowIndex) in logicalRows" :key="row.key" class="grn-receive-block">
            <table class="table table-sm table-bordered grn-receive-table" v-entry-table="{ fixed: false }">
                <thead>
                    <tr>
                        <th v-if="edit" class="grn-select"><input type="checkbox" data-row-select aria-label="Select all rows"
                            :disabled="!removableKeys(row).length" :checked="isRowAllSelected(row)" :indeterminate="isRowPartlySelected(row)"
                            @change="toggleRow(row, $event.target.checked)"></th>
                        <th class="grn-sno">S.No.</th>
                        <th class="grn-item">Item</th>
                        <th class="grn-type">Received Type</th>
						<th v-if="lotFieldname">Lot</th>
                        <th v-for="col in row.columns" :key="col.key" class="grn-qty-col">
                            {{ col.label }}
                        </th>
                        <th class="grn-total">Total</th>
						<th class="grn-comments">Comments</th>
						<template v-if="returnMode">
							<th class="grn-total">Returnable</th>
							<th class="grn-total">Remaining</th>
						</template>
						<template v-else>
							<th class="grn-total">Pending</th>
							<th class="grn-total">Allowed</th>
							<th class="grn-total">Bal.</th>
						</template>
                    </tr>
                </thead>
                <tbody>
                    <tr v-for="(split, splitIndex) in row.splits" :key="split.key">
                        <td v-if="edit" class="grn-select"><input type="checkbox" data-row-select tabindex="-1"
                            aria-label="Select row" :disabled="!canRemoveSplit(row, split)" :checked="selection.isSelected(split.key)"
                            @change="selection.toggle(split.key, $event.target.checked)"></td>
                        <td v-if="splitIndex === 0" :rowspan="row.splits.length">{{ rowIndex + 1 }}</td>
                        <td v-if="splitIndex === 0" :rowspan="row.splits.length">
                            <div class="grn-item-title">{{ row.name }}</div>
                            <div v-if="rowMeta(row)" class="text-muted small grn-item-meta">
                                {{ rowMeta(row) }}
                            </div>
                            <div v-if="row.defaultUom" class="text-muted small">{{ row.defaultUom }}</div>
                        </td>
                        <td>
                            <span>{{ split.receivedType || 'Received' }}</span>
                        </td>
						<td v-if="lotFieldname">
							<select v-if="edit && !returnMode && canEditLotForEntry(split.entry, lotOptions, lotFieldname)" class="form-control form-control-sm"
								:value="split.entry.dimensions?.[lotFieldname] || ''" aria-label="Receipt lot"
								@change="setLot(split, $event.target.value)">
								<option v-if="!split.entry.dimensions?.[lotFieldname]" value="" disabled hidden>Choose lot</option>
								<option v-for="lot in lotChoicesForEntry(split.entry, lotOptions, lotFieldname)" :key="lot.value" :value="lot.value" :disabled="lot.disabled">{{ lot.label }}</option>
							</select>
							<span v-else>{{ split.entry.dimensions?.[lotFieldname] }}</span>
							<button v-if="edit && !returnMode && unusedLots(row, split).length" type="button"
								class="btn btn-xs btn-default" @click="addLot(row, split)">Add lot</button>
						</td>
                        <td v-for="col in row.columns" :key="col.key">
                            <input v-if="edit"
                                   class="form-control form-control-sm grn-qty-input"
                                   type="number"
                                   min="0"
                                   step="0.001"
								   :max="allowExcess ? undefined : maxQty(row, split, col.key)"
                                   :value="qty(split.entry, col.key)"
                                   @input="onQtyInput(row, split, col.key, $event)">
                            <span v-else>{{ formatQty(qty(split.entry, col.key)) }}</span>
							<div v-if="secondaryUom(split.entry, col.key)">
								<input v-if="edit" v-model.number="valueDetail(split.entry, col.key).secondary_qty"
									type="number" min="0" step="0.001" aria-label="Secondary quantity"
									class="form-control form-control-sm grn-qty-input" @input="emit('itemupdated', true)">
								<span v-else>{{ valueDetail(split.entry, col.key).secondary_qty }}</span>
								<small>{{ secondaryUom(split.entry, col.key) }}</small>
							</div>
                        </td>
                        <td>{{ formatQty(splitTotal(split, row.columns)) }}</td>
						<td class="grn-comments">
							<input v-if="edit" class="form-control form-control-sm" type="text" aria-label="Row comments"
								:value="split.entry.comments || ''" @input="onCommentInput(split, $event)">
							<span v-else>{{ split.entry.comments }}</span>
						</td>
						<td v-if="returnMode && splitIndex === 0" :rowspan="row.splits.length">
							{{ formatQty(rowAllowed(row)) }}
						</td>
						<td v-if="returnMode && splitIndex === 0"
							:rowspan="row.splits.length"
							:class="{ 'text-danger': rowBalance(row) < 0 }">
							{{ formatQty(rowBalance(row)) }}
						</td>
						<td v-if="!returnMode && splitIndex === 0" :rowspan="row.splits.length">
                            {{ formatQty(rowPending(row)) }}
                        </td>
						<td v-if="!returnMode && splitIndex === 0" :rowspan="row.splits.length">
                            {{ formatQty(rowAllowed(row)) }}
                        </td>
						<td v-if="!returnMode && splitIndex === 0"
                            :rowspan="row.splits.length"
                            :class="{ 'text-danger': rowBalance(row) < 0 }">
                            {{ formatQty(rowBalance(row)) }}
                        </td>
                    </tr>
					<tr v-if="edit && !returnMode && unusedRTs(row).length" class="grn-rt-add-row">
                        <td></td>
                        <td></td>
                        <td></td>
                        <td :colspan="row.columns.length + 6 + (lotFieldname ? 1 : 0)">
                            <span class="text-muted small mr-2">Add Received Type:</span>
                            <button v-for="rt in unusedRTs(row)"
                                    :key="rt"
                                    type="button"
                                    class="btn btn-xs btn-default grn-rt-add"
                                    @click="addSplit(row, rt)">+ {{ rt }}</button>
                        </td>
                    </tr>
                </tbody>
            </table>
        </div>
        <SelectionFooter :count="selection.selected.value.length" @delete="deleteSelected" />
    </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue';
import {
	canEditLotForEntry,
	unusedLotsForEntry,
	lotChoicesForEntry,
	buildLogicalReceiptRows,
	buildReceiptRowKey,
	removeSplitSources,
	setSplitComment,
	setSplitQuantity,
} from './grnReceiptRows.mjs';
import { SelectionFooter, useRowSelection, vEntryTable } from '../utils/entryGrid.js';

const props = defineProps({
    items: { type: Array, default: () => [] },
    edit: { type: Boolean, default: true },
	returnMode: { type: Boolean, default: false },
	allowExcess: { type: Boolean, default: false },
	aggregatePhysicalRows: { type: Boolean, default: false },
	lotOptions: { type: Object, default: () => ({}) },
	lotFieldname: { type: String, default: '' },
});
const emit = defineEmits(['itemupdated']);

const dimensions = ref([]);
const availableRTs = ref([]);
const defaultRT = ref('');

onMounted(() => {
    frappe.call({
        method: 'yrp.stock.api.get_stock_dimensions_for_ui',
        callback: (r) => {
            dimensions.value = r.message || [];
        },
    });
    frappe.call({
        method: 'yrp.yrp.doctype.yrp_goods_received_note.yrp_goods_received_note.get_rework_output_received_types',
        callback: (r) => {
            if (r && r.message) {
                availableRTs.value = r.message.received_types || [];
                defaultRT.value = r.message.default_received_type || '';
            }
        },
    });
});

const dimensionLabels = computed(() => {
    const out = {};
    for (const dim of dimensions.value || []) {
        out[dim.fieldname] = dim.label;
    }
    return out;
});

const logicalRows = computed(() => buildLogicalReceiptRows(props.items, {
	aggregatePhysicalRows: props.aggregatePhysicalRows,
	lotFieldname: props.lotFieldname,
}));

function unusedLots(row, split) {
	const used = new Set(row.splits.filter((entry) => entry.receivedType === split.receivedType)
		.map((entry) => entry.entry.dimensions?.[props.lotFieldname]));
	return unusedLotsForEntry(split.entry, props.lotOptions, used);
}

function setLot(split, lot) {
	for (const entry of split.sourceEntries || [split.entry]) entry.dimensions[props.lotFieldname] = lot;
	emit('itemupdated', true);
}

function addLot(row, split) {
	const source = split.sourceEntries?.[0] || split.entry;
	const group = props.items.find((group) => group.items.includes(source));
	if (!group) return;
	const clone = JSON.parse(JSON.stringify(source));
	clone.dimensions[props.lotFieldname] = unusedLots(row, split)[0];
	for (const value of Object.values(clone.values || {})) {
		value.qty = 0;
		value.secondary_qty = 0;
		value.freight_amount = 0;
	}
	group.items.push(clone);
	emit('itemupdated', true);
}

function stripReceivedType(dimensionsIn) {
    const out = {};
    for (const [fieldname, value] of Object.entries(dimensionsIn || {})) {
        if (fieldname !== 'received_type') {
            out[fieldname] = value;
        }
    }
    return out;
}

function receivedType(entry) {
    return (entry.dimensions || {}).received_type || '';
}

function getColumns(group, entry) {
    const values = entry.values || {};
    const primaryValues = group.primary_attribute_values || [];
    if (primaryValues.length && !Object.prototype.hasOwnProperty.call(values, 'default')) {
        return primaryValues.map((value) => ({ key: value, label: value }));
    }
    return [{ key: 'default', label: 'Qty' }];
}

function dimensionLabel(fieldname) {
    if (dimensionLabels.value[fieldname]) {
        return dimensionLabels.value[fieldname];
    }
    if (frappe.model && frappe.model.unscrub) {
        return frappe.model.unscrub(fieldname);
    }
    return fieldname.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
}

function rowMeta(row) {
    const parts = [];
    for (const fieldname of row.dimensionFields || []) {
        const value = row.dimensions[fieldname];
        if (value) {
            parts.push(`${dimensionLabel(fieldname)}: ${value}`);
        }
    }
    for (const fieldname of row.attributeFields || []) {
        const value = row.attributes[fieldname];
        if (value) {
            parts.push(value);
        }
    }
    const setColour = row.setCombination?.major_colour;
    const setPart = row.setCombination?.major_part;
    if (setColour || setPart) {
        parts.push(`Set: ${[setColour, setPart].filter(Boolean).join(' / ')}`);
    }
    return parts.join(' | ');
}

// Offered where the server sent a Secondary UOM; read-only cells show it only when entered.
function secondaryUom(entry, key) {
    const detail = valueDetail(entry, key);
    if (!props.edit && !toNumber(detail.secondary_qty)) return '';
    return detail.secondary_uom || entry.secondary_uom || '';
}

function valueDetail(entry, key) {
    if (!entry.values) {
        entry.values = {};
    }
    if (!entry.values[key]) {
        entry.values[key] = { qty: 0 };
    }
    return entry.values[key];
}

function toNumber(value) {
    const numberValue = Number(value || 0);
    return Number.isFinite(numberValue) ? numberValue : 0;
}

function qty(entry, key) {
    return toNumber(valueDetail(entry, key).qty);
}

function pendingQty(row, key) {
    for (const split of row.splits) {
        const pending = valueDetail(split.entry, key).pending_quantity;
        if (pending !== undefined && pending !== null && pending !== '') {
            return toNumber(pending);
        }
    }
    return 0;
}

function allowedQty(row, key) {
    for (const split of row.splits) {
        const allowed = valueDetail(split.entry, key).max_receivable_quantity;
        // A server-provided -1 denotes an uncapped receipt mode.
        if (Number(allowed) === -1) return Infinity;
        if (allowed !== undefined && allowed !== null && allowed !== '') {
			const normalized = Math.max(toNumber(allowed), 0);
			if (!props.returnMode || normalized > 0) {
				return normalized;
			}
        }
    }
	if (props.returnMode) {
		return row.splits.reduce((total, split) => total + qty(split.entry, key), 0);
	}
    return Math.max(pendingQty(row, key), 0);
}

function otherSplitQty(row, currentSplit, key) {
    let total = 0;
    for (const split of row.splits) {
        if (split === currentSplit) {
            continue;
        }
        total += qty(split.entry, key);
    }
    return total;
}

function maxQty(row, split, key) {
    return Math.max(allowedQty(row, key) - otherSplitQty(row, split, key), 0);
}

function onQtyInput(row, split, key, event) {
	const nextQty = setSplitQuantity(split, key, event.target.value, {
		allowExcess: props.allowExcess,
		maxValue: maxQty(row, split, key),
	});
	event.target.value = nextQty;
    emit('itemupdated', true);
}

function onCommentInput(split, event) {
	setSplitComment(split, event.target.value);
	emit('itemupdated', true);
}

function splitTotal(split, columns) {
    return columns.reduce((total, col) => total + qty(split.entry, col.key), 0);
}

function rowReceived(row) {
    return row.splits.reduce((total, split) => total + splitTotal(split, row.columns), 0);
}

function rowPending(row) {
    return row.columns.reduce((total, col) => total + pendingQty(row, col.key), 0);
}

function rowAllowed(row) {
    return row.columns.reduce((total, col) => total + allowedQty(row, col.key), 0);
}

function rowBalance(row) {
    return rowAllowed(row) - rowReceived(row);
}

function unusedRTs(row) {
    if (!availableRTs.value || !availableRTs.value.length) return [];
    const used = new Set(row.splits.map((s) => s.receivedType || ''));
    return availableRTs.value.filter((rt) => !used.has(rt));
}

function canRemoveSplit(row, split) {
    // Only allow removing a split that has no qty entered, and keep at least one split per row.
    if (!row.splits || row.splits.length <= 1) return false;
	if (props.lotFieldname && Object.values(split.entry.values || {}).some((value) => toNumber(value.secondary_qty))) return false;
    return splitTotal(split, row.columns) === 0;
}

function addSplit(row, rt) {
    // Find the source group + a template entry to clone the shape.
    for (const group of props.items || []) {
        for (const entry of (group.items || [])) {
            const stripped = stripReceivedType(entry.dimensions || {});
			const dimsWithoutTypeKey = buildReceiptRowKey(group, entry, { lotFieldname: props.lotFieldname });
            if (dimsWithoutTypeKey !== row.key) continue;
            const clone = JSON.parse(JSON.stringify(entry));
            clone.dimensions = { ...stripped, received_type: rt };
            clone.values = {};
            const cols = getColumns(group, entry);
            for (const col of cols) {
                const src = (entry.values || {})[col.key] || {};
                clone.values[col.key] = { ...src, qty: 0 };
				if (props.lotFieldname) {
					clone.values[col.key].secondary_qty = 0;
					clone.values[col.key].freight_amount = 0;
				}
            }
            group.items.push(clone);
            emit('itemupdated', true);
            return;
        }
    }
}

function removeSplit(row, split) {
	if (removeSplitSources(props.items, split)) emit('itemupdated', true);
}

const removableKeys = (row) => row.splits.filter((split) => canRemoveSplit(row, split)).map((split) => split.key);
const selection = useRowSelection(() => props.edit ? logicalRows.value.flatMap(removableKeys) : []);

function isRowAllSelected(row) {
	const keys = removableKeys(row);
	return keys.length > 0 && keys.every(selection.isSelected);
}

function isRowPartlySelected(row) {
	return !isRowAllSelected(row) && removableKeys(row).some(selection.isSelected);
}

function toggleRow(row, on) {
	for (const key of removableKeys(row)) selection.toggle(key, on);
}

// Re-checks canRemoveSplit per split, so a row never loses its last split.
function deleteSelected() {
	selection.removeSelected((key) => {
		const row = logicalRows.value.find((candidate) => candidate.splits.some((split) => split.key === key));
		const split = row?.splits.find((candidate) => candidate.key === key);
		if (split && canRemoveSplit(row, split)) removeSplit(row, split);
	}, () => props.edit);
}

function formatQty(value) {
    if (value === Infinity) return '—';
    const numberValue = toNumber(value);
    if (Number.isInteger(numberValue)) {
        return String(numberValue);
    }
    return numberValue.toFixed(3).replace(/\.?0+$/, '');
}
</script>

<style scoped>
.grn-receive-block {
    margin-bottom: 12px;
    overflow-x: auto;
    padding-bottom: 4px;
}

.grn-receive-table {
    table-layout: auto;
    width: max-content;
    min-width: 100%;
    margin-bottom: 0;
}

.grn-receive-table th,
.grn-receive-table td {
    vertical-align: middle;
    white-space: nowrap;
}

.grn-sno {
    min-width: 60px;
}

.grn-item {
    min-width: 190px;
    max-width: 215px;
    white-space: normal;
}

.grn-item-title {
    font-weight: 500;
}

.grn-item-meta {
    line-height: 1.35;
}

.grn-type {
    min-width: 100px;
}

.grn-qty-col {
    min-width: 50px;
}

.grn-total {
    min-width: 56px;
}

.grn-comments {
    min-width: 140px;
}

.grn-qty-input {
    width: 50px;
    min-width: 48px;
}

.grn-select {
    width: 28px;
    text-align: center;
}

.grn-rt-add-row {
    background-color: #fafbfc;
}
.grn-rt-add {
    margin-right: 4px;
    margin-bottom: 2px;
}
</style>
