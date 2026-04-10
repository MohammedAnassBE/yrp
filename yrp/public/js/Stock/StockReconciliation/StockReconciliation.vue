<template>
    <div>
        <item-dimension-fetcher
            :items="items"
            :other-inputs="otherInputs"
            :table-fields="table_fields"
            :args="args"
            :edit="docstatus == 0"
            :validate-qty="false"
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
import { ref } from 'vue';

const docstatus = ref(cur_frm.doc.docstatus);
const items = ref([]);
const otherInputs = ref([
    {
        name: 'allow_zero_valuation_rate',
        parent: 'zero-valuation-control',
        df: { fieldtype: 'Check', fieldname: 'allow_zero_valuation_rate', label: 'Allow Zero Valuation Rate' }
    },
    {
        name: 'make_qty_zero',
        parent: 'make-qty-zero',
        df: { fieldtype: 'Check', fieldname: 'make_qty_zero', label: 'Make Qty Zero' }
    },
]);
const table_fields = ref([
    { name: 'rate', label: 'Rate', uses_primary_attribute: 1 },
]);
const qty_fields = ref(['rate']);
const args = ref({
    docstatus: cur_frm.doc.docstatus,
    item_query: function () { return { filters: { is_stock_item: 1 } }; }
});

function update_status() {
    docstatus.value = cur_frm.doc.docstatus;
    args.value['docstatus'] = cur_frm.doc.docstatus;
}

function load_data(all_items) { items.value = all_items || []; }
function get_items() { return items.value; }
function updated() { EventBus.$emit('stock_updated', true); }

defineExpose({ items, load_data, update_status, get_items });
</script>
