<template>
    <div class="grn-compact-editor">
        <div v-if="!logicalRows.length" class="text-muted small">No pending receivables</div>
        <div v-for="(row, rowIndex) in logicalRows" :key="row.key" class="grn-receive-block">
            <table class="table table-sm table-bordered grn-receive-table">
                <thead>
                    <tr>
                        <th class="grn-sno">S.No.</th>
                        <th class="grn-item">Item</th>
                        <th class="grn-type">Received Type</th>
                        <th v-for="col in row.columns" :key="col.key" class="grn-qty-col">
                            {{ col.label }}
                        </th>
                        <th class="grn-total">Total</th>
                        <th class="grn-total">Pending</th>
                        <th class="grn-total">Allowed</th>
                        <th class="grn-total">Bal.</th>
                    </tr>
                </thead>
                <tbody>
                    <tr v-for="(split, splitIndex) in row.splits" :key="split.key">
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
                            <button v-if="edit && canRemoveSplit(row, split)"
                                    type="button"
                                    class="grn-rt-remove"
                                    title="Remove this Received Type row"
                                    @click="removeSplit(row, split)">×</button>
                        </td>
                        <td v-for="col in row.columns" :key="col.key">
                            <input v-if="edit"
                                   class="form-control form-control-sm grn-qty-input"
                                   type="number"
                                   min="0"
                                   step="0.001"
                                   :max="maxQty(row, split, col.key)"
                                   :value="qty(split.entry, col.key)"
                                   @input="onQtyInput(row, split, col.key, $event)">
                            <span v-else>{{ formatQty(qty(split.entry, col.key)) }}</span>
                        </td>
                        <td>{{ formatQty(splitTotal(split, row.columns)) }}</td>
                        <td v-if="splitIndex === 0" :rowspan="row.splits.length">
                            {{ formatQty(rowPending(row)) }}
                        </td>
                        <td v-if="splitIndex === 0" :rowspan="row.splits.length">
                            {{ formatQty(rowAllowed(row)) }}
                        </td>
                        <td v-if="splitIndex === 0"
                            :rowspan="row.splits.length"
                            :class="{ 'text-danger': rowBalance(row) < 0 }">
                            {{ formatQty(rowBalance(row)) }}
                        </td>
                    </tr>
                    <tr v-if="edit && unusedRTs(row).length" class="grn-rt-add-row">
                        <td></td>
                        <td></td>
                        <td :colspan="row.columns.length + 5">
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
    </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue';

const props = defineProps({
    items: { type: Array, default: () => [] },
    edit: { type: Boolean, default: true },
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
        method: 'yrp.yrp.doctype.goods_received_note.goods_received_note.get_rework_output_received_types',
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

const logicalRows = computed(() => {
    const rows = [];
    const byKey = new Map();
    for (const group of props.items || []) {
        for (const entry of group.items || []) {
            const dimensionsWithoutType = stripReceivedType(entry.dimensions || {});
            const attributes = entry.attributes || {};
            const columns = getColumns(group, entry);
            const key = stableKey({
                name: entry.name,
                dimensions: dimensionsWithoutType,
                attributes,
                columns: columns.map((col) => col.key),
            });
            if (!byKey.has(key)) {
                const row = {
                    key,
                    name: entry.name,
                    dimensions: dimensionsWithoutType,
                    dimensionFields: Object.keys(dimensionsWithoutType).filter(
                        (fieldname) => dimensionsWithoutType[fieldname],
                    ),
                    attributes,
                    attributeFields: Object.keys(attributes).filter(
                        (fieldname) => attributes[fieldname],
                    ),
                    columns,
                    defaultUom: entry.default_uom || '',
                    splits: [],
                };
                byKey.set(key, row);
                rows.push(row);
            }
            const row = byKey.get(key);
            row.splits.push({
                key: `${key}::${receivedType(entry)}`,
                receivedType: receivedType(entry),
                entry,
            });
        }
    }
    for (const row of rows) {
        row.splits.sort((a, b) => (a.receivedType || '').localeCompare(b.receivedType || ''));
    }
    return rows;
});

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

function stableKey(value) {
    return JSON.stringify(sortObject(value));
}

function sortObject(value) {
    if (Array.isArray(value)) {
        return value.map((item) => sortObject(item));
    }
    if (!value || typeof value !== 'object') {
        return value;
    }
    const out = {};
    for (const key of Object.keys(value).sort()) {
        out[key] = sortObject(value[key]);
    }
    return out;
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
    return parts.join(' | ');
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
        if (allowed !== undefined && allowed !== null && allowed !== '') {
            return Math.max(toNumber(allowed), 0);
        }
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
    const detail = valueDetail(split.entry, key);
    let nextQty = toNumber(event.target.value);
    if (nextQty < 0) {
        nextQty = 0;
    }
    const maxValue = maxQty(row, split, key);
    if (maxValue !== null && nextQty > maxValue) {
        nextQty = maxValue;
    }
    detail.qty = nextQty;
    event.target.value = nextQty;
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
    return splitTotal(split, row.columns) === 0;
}

function addSplit(row, rt) {
    // Find the source group + a template entry to clone the shape.
    for (const group of props.items || []) {
        for (const entry of (group.items || [])) {
            const stripped = stripReceivedType(entry.dimensions || {});
            const dimsWithoutTypeKey = stableKey({
                name: entry.name,
                dimensions: stripped,
                attributes: entry.attributes || {},
                columns: getColumns(group, entry).map((col) => col.key),
            });
            if (dimsWithoutTypeKey !== row.key) continue;
            const clone = JSON.parse(JSON.stringify(entry));
            clone.dimensions = { ...stripped, received_type: rt };
            clone.values = {};
            const cols = getColumns(group, entry);
            for (const col of cols) {
                const src = (entry.values || {})[col.key] || {};
                clone.values[col.key] = { ...src, qty: 0 };
            }
            group.items.push(clone);
            emit('itemupdated', true);
            return;
        }
    }
}

function removeSplit(row, split) {
    for (const group of props.items || []) {
        const idx = (group.items || []).indexOf(split.entry);
        if (idx !== -1) {
            group.items.splice(idx, 1);
            emit('itemupdated', true);
            return;
        }
    }
}

function formatQty(value) {
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

.grn-qty-input {
    width: 50px;
    min-width: 48px;
}

.grn-rt-remove {
    background: none;
    border: none;
    color: #999;
    font-size: 14px;
    line-height: 1;
    margin-left: 4px;
    padding: 0 4px;
    cursor: pointer;
}
.grn-rt-remove:hover {
    color: #c00;
}

.grn-rt-add-row {
    background-color: #fafbfc;
}
.grn-rt-add {
    margin-right: 4px;
    margin-bottom: 2px;
}
</style>
