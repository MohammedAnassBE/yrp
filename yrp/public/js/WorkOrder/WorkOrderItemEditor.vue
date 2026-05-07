<template>
    <div>
        <div v-if="title" class="text-muted mb-2">{{ title }}</div>
        <item-dimension-fetcher
            :items="items"
            :other-inputs="otherInputs"
            :table-fields="tableFields"
            :qty-fields="qtyFields"
            :args="args"
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

const props = defineProps({
    title: { type: String, default: '' },
    editorType: { type: String, required: true },
    showDimensions: { type: Boolean, default: true },
    allowCreate: { type: Boolean, default: true },
    allowEdit: { type: Boolean, default: true },
    allowRemove: { type: Boolean, default: true },
    lockDimensionsOnEdit: { type: Boolean, default: false },
});

const docstatus = ref(cur_frm.doc.docstatus || 0);
const items = ref([]);
const allowedItems = ref([]);

const tableFields = computed(() => {
    if (props.editorType === 'work_order_receivables') {
        return [
            { name: 'cost', label: 'Cost', uses_primary_attribute: 1 },
            { name: 'pending_quantity', label: 'Pending', uses_primary_attribute: 1 },
        ];
    }
    if (props.editorType === 'goods_received_note') {
        return [
            { name: 'pending_quantity', label: 'Pending', uses_primary_attribute: 1 },
            { name: 'rate', label: 'Rate', uses_primary_attribute: 1 },
        ];
    }
    if (props.editorType === 'delivery_challan') {
        return [
            { name: 'pending_quantity', label: 'Pending', uses_primary_attribute: 1 },
            { name: 'rate', label: 'Rate', uses_primary_attribute: 1 },
        ];
    }
    return [
        { name: 'rate', label: 'Rate', uses_primary_attribute: 1 },
        { name: 'pending_quantity', label: 'Pending', uses_primary_attribute: 1 },
    ];
});

const qtyFields = computed(() => {
    if (props.editorType === 'work_order_receivables') return ['cost'];
    if (props.editorType === 'delivery_challan' || props.editorType === 'goods_received_note') return ['rate'];
    return ['rate'];
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

function updated() {
    EventBus.$emit('work_order_items_updated', props.editorType);
}

defineExpose({ load_data, get_items, update_status });
</script>
