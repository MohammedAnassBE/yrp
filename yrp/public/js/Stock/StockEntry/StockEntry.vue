<template>
    <div>
        <item-dimension-fetcher
            :items="items"
            :other-inputs="otherInputs"
            :table-fields="table_fields"
            :args="args"
            :edit="docstatus == 0"
            :validate-qty="true"
            :qty-fields="qty_fields"
            @itemadded="updated"
            @itemupdated="updated"
            @itemremoved="updated">
        </item-dimension-fetcher>
    </div>
</template>

<script setup>
import EventBus from '../bus.js';
import ItemDimensionFetcher from '../components/ItemDimensionFetch.vue';
import { ref, onMounted, onUnmounted } from 'vue';

const docstatus = ref(cur_frm.doc.docstatus);
const items = ref([]);
const can_create = ref(true);
const otherInputs = ref([]);
const table_fields = ref([
    { name: 'rate', label: 'Rate', uses_primary_attribute: 1 },
]);
const qty_fields = ref([]);
const args = ref({
    docstatus: cur_frm.doc.docstatus,
    can_create: function () { return can_create; },
    item_query: function () { return { filters: { is_stock_item: 1 } }; }
});

// Store handler reference so we can clean it up on unmount
const _purposeHandler = (purpose) => {
    can_create.value = purpose !== "Receive at Warehouse";
};

onMounted(() => {
    if (cur_frm.doc.purpose === "Receive at Warehouse") {
        can_create.value = false;
    }
    EventBus.$on("purpose_updated", _purposeHandler);
});

onUnmounted(() => {
    EventBus.$off("purpose_updated", _purposeHandler);
});

function update_status() {
    docstatus.value = cur_frm.doc.docstatus;
    args.value['docstatus'] = cur_frm.doc.docstatus;
}

function load_data(all_items) {
    (all_items || []).forEach((element) => {
        if (element.primary_attribute) {
            element.items.forEach((row) => {
                let qty = 0;
                Object.keys(row.values || {}).forEach((key) => { qty += (row.values[key].qty || 0); });
                row.total_qty = qty;
            });
        } else {
            element.items.forEach((row) => {
                row.total_qty = (row.values && row.values['default'] && row.values['default'].qty) || 0;
            });
        }
    });
    items.value = all_items || [];
}

function get_items() { return items.value; }

function updated() { EventBus.$emit('stock_updated', true); }

defineExpose({ items, load_data, update_status, get_items });
</script>
