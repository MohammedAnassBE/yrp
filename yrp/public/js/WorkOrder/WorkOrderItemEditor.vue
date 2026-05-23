<template>
    <div>
        <div v-if="title" class="text-muted mb-2">{{ title }}</div>
        <goods-received-note-editor
            v-if="useReceivedTypeGrnEditor"
            :items="items"
            :edit="docstatus === 0"
            @itemupdated="updated">
        </goods-received-note-editor>
        <item-dimension-fetcher
            v-else
            :items="items"
            :other-inputs="otherInputs"
            :table-fields="tableFields"
            :qty-fields="qtyFields"
            :inline-qty-edit="inlineQtyEdit"
            :inline-qty-max-field="inlineQtyMaxField"
            :args="args"
            :validate="validateItem"
            :edit="docstatus === 0"
            :validate-qty="true"
            :show-dimensions="showDimensions"
            :lock-dimensions-on-edit="lockDimensionsOnEdit"
            @itemadded="updated"
            @itemupdated="updated"
            @itemremoved="updated">
        </item-dimension-fetcher>
    </div>
</template>

<script setup>
import { computed, ref } from 'vue';
import EventBus from '../Stock/bus.js';
import ItemDimensionFetcher from '../Stock/components/ItemDimensionFetch.vue';
import GoodsReceivedNoteEditor from './GoodsReceivedNoteEditor.vue';

const props = defineProps({
    title: { type: String, default: '' },
    editorType: { type: String, required: true },
    showDimensions: { type: Boolean, default: true },
    allowCreate: { type: Boolean, default: true },
    allowEdit: { type: Boolean, default: true },
    allowRemove: { type: Boolean, default: true },
    lockDimensionsOnEdit: { type: Boolean, default: false },
    sourceType: { type: String, default: '' },
    showSecondary: { type: Boolean, default: false },
});

const SECONDARY_COLUMNS = [
    { name: 'secondary_qty', label: 'Sec Qty', uses_primary_attribute: 1 },
    { name: 'secondary_uom', label: 'Sec UOM', uses_primary_attribute: 1 },
];

const docstatus = ref(cur_frm.doc.docstatus || 0);
const items = ref([]);
const allowedItems = ref([]);

const baseTableFields = computed(() => {
    if (props.editorType === 'work_order_receivables') {
        return [
            { name: 'cost', label: 'Cost', uses_primary_attribute: 1 },
            { name: 'pending_quantity', label: 'Pending', uses_primary_attribute: 1 },
        ];
    }
    if (props.editorType === 'work_order_deliverables') {
        return [
            { name: 'pending_quantity', label: 'Pending', uses_primary_attribute: 1 },
        ];
    }
    if (props.editorType === 'goods_received_note') {
        return [
            { name: 'pending_quantity', label: 'Pending', uses_primary_attribute: 1 },
            { name: 'max_receivable_quantity', label: 'Allowed', uses_primary_attribute: 1 },
            { name: 'rate', label: 'Rate', uses_primary_attribute: 1 },
        ];
    }
    if (props.editorType === 'delivery_challan') {
        return [
            { name: 'pending_quantity', label: 'Pending', uses_primary_attribute: 1 },
            { name: 'rate', label: 'Rate', uses_primary_attribute: 1 },
        ];
    }
    if (props.editorType === 'purchase_order') {
        return [
            { name: 'rate', label: 'Rate', uses_primary_attribute: 1 },
            { name: 'pending_quantity', label: 'Pending', uses_primary_attribute: 1 },
            { name: 'total_amount', label: 'Amount', uses_primary_attribute: 1 },
        ];
    }
    return [
        { name: 'rate', label: 'Rate', uses_primary_attribute: 1 },
        { name: 'pending_quantity', label: 'Pending', uses_primary_attribute: 1 },
    ];
});

const tableFields = computed(() => (
    props.showSecondary ? [...baseTableFields.value, ...SECONDARY_COLUMNS] : baseTableFields.value
));

const baseQtyFields = computed(() => {
    if (props.editorType === 'work_order_receivables') return ['cost'];
    if (props.editorType === 'work_order_deliverables') return [];
    if (props.editorType === 'delivery_challan') return [];
    if (props.editorType === 'goods_received_note') return [];
    if (props.editorType === 'purchase_order') return [];
    return ['rate'];
});

const qtyFields = computed(() => (
    props.showSecondary ? [...baseQtyFields.value, 'secondary_qty'] : baseQtyFields.value
));

const grnSourceType = computed(() => {
    if (props.sourceType) return props.sourceType;
    if (typeof cur_frm !== 'undefined' && cur_frm.doc) return cur_frm.doc.against || '';
    return '';
});
const useReceivedTypeGrnEditor = computed(() => (
    props.editorType === 'goods_received_note' && grnSourceType.value === 'Work Order'
));
const useInlineReceiveEditor = computed(() => (
    props.editorType === 'goods_received_note' && grnSourceType.value === 'Purchase Order'
));
const inlineQtyEdit = computed(() => (
    props.editorType === 'delivery_challan' || useInlineReceiveEditor.value
));
const inlineQtyMaxField = computed(() => {
    if (props.editorType === 'delivery_challan') return 'pending_quantity';
    if (useInlineReceiveEditor.value) return 'max_receivable_quantity';
    return '';
});

const otherInputs = computed(() => {
    return [
        {
            name: 'comments',
            parent: 'comments-control',
            df: {
                fieldtype: 'Data',
                fieldname: 'comments',
                label: 'Comments',
            },
        },
    ];
});

const args = computed(() => ({
    docstatus: docstatus.value,
    can_create: () => docstatus.value === 0 && props.allowCreate,
    can_edit: () => docstatus.value === 0 && props.allowEdit,
    can_remove: () => docstatus.value === 0 && props.allowRemove,
    item_query: () => {
        if (props.editorType === 'purchase_order') {
            return { filters: { disabled: 0 } };
        }
        if (!allowedItems.value.length) {
            return { filters: { disabled: 0 } };
        }
        return { filters: { disabled: 0, name: ['in', allowedItems.value] } };
    },
}));

function update_status() {
    docstatus.value = cur_frm.doc.docstatus || 0;
}

function _set_allowed_items(data) {
    const allowed = new Set();
    for (const group of data || []) {
        for (const row of group.items || []) {
            if (row.name) allowed.add(row.name);
        }
    }
    allowedItems.value = Array.from(allowed);
}

function load_data(data) {
    const rows = data || [];
    items.value = rows;
    _set_allowed_items(rows);
}

function get_items() {
    return items.value || [];
}

async function validateItem(row) {
    if (props.editorType !== 'purchase_order') return true;
    if (!cur_frm.doc.supplier) {
        frappe.msgprint(__('Select Supplier before adding PO items.'));
        return false;
    }

    const price = await getPurchaseOrderPrice(row);
    if (!price) {
        frappe.msgprint(__('No active Item Price found for {0} and supplier {1}.', [row.name, cur_frm.doc.supplier]));
        return false;
    }
    return applyPurchaseOrderPrice(row, price);
}

function getPurchaseOrderPrice(row) {
    return new Promise((resolve) => {
        frappe.call({
            method: 'yrp.yrp.doctype.purchase_order.purchase_order.get_item_price_for_ui',
            args: {
                item_detail: JSON.stringify(row),
                supplier: cur_frm.doc.supplier,
            },
            callback: (r) => resolve(r.message || null),
            error: () => resolve(null),
        });
    });
}

function applyPurchaseOrderPrice(row, price) {
    const values = row.values || {};
    const taxRate = flt(price.tax_rate || 0);
    const missing = [];

    row.tax = price.tax || '';
    for (const [key, value] of Object.entries(values)) {
        const qty = flt(value.qty || 0);
        const rate = price.rates ? price.rates[key] : price.rate;
        if (qty > 0 && (rate === undefined || rate === null || rate === '')) {
            missing.push(key);
            continue;
        }
        value.rate = flt(rate || 0);
        value.amount = qty * value.rate;
        value.discount_amount = value.amount * flt(row.discount_percentage || 0) / 100;
        value.tax_amount = (value.amount - value.discount_amount) * taxRate / 100;
        value.total_amount = value.amount - value.discount_amount + value.tax_amount;
    }

    if (missing.length) {
        frappe.msgprint(__('No matching Item Price slab found for {0}.', [missing.join(', ')]));
        return false;
    }
    return true;
}

function updated() {
    EventBus.$emit('work_order_items_updated', props.editorType);
}

defineExpose({ load_data, get_items, update_status });
</script>
