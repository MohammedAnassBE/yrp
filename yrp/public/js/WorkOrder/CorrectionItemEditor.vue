<template>
    <div>
        <div v-if="!blocks.length" class="text-muted">
            No correction items
        </div>
        <div v-for="(block, block_index) in blocks"
             :key="block.work_order_correction || block_index"
             class="correction-block mb-4">
            <div class="text-muted mb-2 correction-block-title">{{ block.title }}</div>
            <item-dimension-fetcher
                :items="block.item_details"
                :other-inputs="otherInputs"
                :table-fields="tableFields"
                :qty-fields="qtyFields"
                :inline-qty-edit="allowEdit"
                :inline-qty-max-field="inlineQtyMaxField"
                :args="args"
                :edit="docstatus === 0"
                :validate-qty="true"
                :show-dimensions="showDimensions"
                @itemadded="updated"
                @itemupdated="updated"
                @itemremoved="updated">
            </item-dimension-fetcher>
        </div>
    </div>
</template>

<script setup>
import { computed, ref } from 'vue';
import EventBus from '../Stock/bus.js';
import ItemDimensionFetcher from '../Stock/components/ItemDimensionFetch.vue';

const props = defineProps({
    editorType: { type: String, required: true },
    showDimensions: { type: Boolean, default: true },
    allowEdit: { type: Boolean, default: true },
    showSecondary: { type: Boolean, default: false },
});

const SECONDARY_COLUMNS = [
    { name: 'secondary_qty', label: 'Sec Qty', uses_primary_attribute: 1 },
    { name: 'secondary_uom', label: 'Sec UOM', uses_primary_attribute: 1 },
];

const docstatus = ref(cur_frm.doc.docstatus || 0);
// Each block: { work_order_correction, title, item_details: [ grouped item rows ] }
const blocks = ref([]);

const baseTableFields = computed(() => {
    if (props.editorType === 'goods_received_note') {
        return [
            { name: 'pending_quantity', label: 'Pending', uses_primary_attribute: 1 },
            { name: 'max_receivable_quantity', label: 'Allowed', uses_primary_attribute: 1 },
            { name: 'rate', label: 'Rate', uses_primary_attribute: 1 },
        ];
    }
    // delivery_challan
    return [
        { name: 'pending_quantity', label: 'Pending', uses_primary_attribute: 1 },
        { name: 'rate', label: 'Rate', uses_primary_attribute: 1 },
    ];
});

const tableFields = computed(() => (
    props.showSecondary ? [...baseTableFields.value, ...SECONDARY_COLUMNS] : baseTableFields.value
));

const qtyFields = computed(() => (props.showSecondary ? ['secondary_qty'] : []));

const inlineQtyMaxField = computed(() => (
    props.editorType === 'goods_received_note' ? 'max_receivable_quantity' : 'pending_quantity'
));

const otherInputs = computed(() => ([
    {
        name: 'comments',
        parent: 'comments-control',
        df: {
            fieldtype: 'Data',
            fieldname: 'comments',
            label: 'Comments',
        },
    },
]));

// Correction rows are pre-loaded from the source document; the user only adjusts
// quantities inline. Row create/edit/remove actions stay disabled.
const args = computed(() => ({
    docstatus: docstatus.value,
    can_create: () => false,
    can_edit: () => false,
    can_remove: () => false,
    item_query: () => ({ filters: { disabled: 0 } }),
}));

function update_status() {
    docstatus.value = cur_frm.doc.docstatus || 0;
}

function load_data(data) {
    blocks.value = Array.isArray(data) ? data : [];
}

function get_items() {
    return blocks.value || [];
}

function updated() {
    EventBus.$emit('work_order_items_updated', props.editorType);
}

defineExpose({ load_data, get_items, update_status });
</script>
