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
                        <td>{{ split.receivedType || 'Received' }}</td>
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
                        <td v-if="splitIndex === 0"
                            :rowspan="row.splits.length"
                            :class="{ 'text-danger': rowBalance(row) < 0 }">
                            {{ formatQty(rowBalance(row)) }}
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

onMounted(() => {
    frappe.call({
        method: 'yrp.stock.api.get_stock_dimensions_for_ui',
        callback: (r) => {
            dimensions.value = r.message || [];
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
    const pending = pendingQty(row, key);
    if (!pending) {
        return null;
    }
    return Math.max(pending - otherSplitQty(row, split, key), 0);
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

function rowBalance(row) {
    return rowPending(row) - rowReceived(row);
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
</style>
