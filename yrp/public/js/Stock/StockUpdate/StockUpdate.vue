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
            :validate="validate_row"
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
const otherInputs = ref([]);
// Rate is intentionally NOT shown or entered here. It is auto-valued on the
// server from the last Stock Ledger Entry (before_validate ->
// set_rate_from_last_sle -> get_last_sle_rate). Showing it as a column only
// displayed "Rate: 0" until save and read as a field the user had to fill in.
// The child `rate` field is also read_only as a safeguard.
const table_fields = ref([
    { name: 'secondary_qty', label: 'Sec Qty', uses_primary_attribute: 1 },
    { name: 'secondary_uom', label: 'Sec UOM', uses_primary_attribute: 1 },
]);
const qty_fields = ref(['secondary_qty']);
const args = ref({
    docstatus: cur_frm.doc.docstatus,
    item_query: function () { return { filters: { is_stock_item: 1 } }; }
});

async function validate_row(row) {
    // For "Reduce" updates, check available stock against entered qty per dimension combo
    if (cur_frm.doc.update_type !== 'Reduce') return true;
    if (!cur_frm.doc.warehouse) {
        frappe.show_alert({ message: __('Set Warehouse on the form first'), indicator: 'red' });
        return false;
    }
    let qty = 0;
    Object.keys(row.values || {}).forEach((k) => { qty += (row.values[k].qty || 0); });
    if (qty <= 0) return true;
    const args = { item: row.name, warehouse: cur_frm.doc.warehouse };
    Object.assign(args, row.dimensions || {});
    return new Promise((resolve) => {
        frappe.call({
            method: 'yrp.stock.api.get_stock_balance',
            args: args,
            callback: (r) => {
                const avail = r.message || 0;
                if (qty > avail) {
                    frappe.show_alert({
                        message: __(`Cannot reduce ${qty}, only ${avail} available`),
                        indicator: 'red'
                    });
                    resolve(false);
                } else {
                    resolve(true);
                }
            }
        });
    });
}

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
