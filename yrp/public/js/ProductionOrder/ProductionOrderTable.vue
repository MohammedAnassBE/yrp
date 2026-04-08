<template>
    <div ref="root" class="po-root">
        <div v-for="(block, blockIdx) in items" :key="'block-'+blockIdx" class="po-card shadow-sm">
            <!-- Item header bar -->
            <div class="po-card-header">
                <div class="header-left">
                    <div class="index-badge">{{ blockIdx + 1 }}</div>
                    <div :class="get_item_input_class(blockIdx)" class="item-selector"></div>
                </div>
                <div class="header-right">
                    <div v-if="block.item && get_block_total(block) > 0" class="total-badge animate-fade">
                        <span class="label">Total Qty:</span>
                        <span class="value">{{ get_block_total(block) }}</span>
                    </div>
                    <button v-if="edit" class="btn-remove-block" @click="remove_block(blockIdx)" title="Remove Item">
                        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"></line><line x1="6" y1="6" x2="18" y2="18"></line></svg>
                    </button>
                </div>
            </div>

            <div class="po-card-body">
                <!-- Grid with row attributes + grid attribute columns -->
                <template v-if="block.item && block.grid_attribute && has_row_attributes(block)">
                    <div class="table-container">
                        <table class="grid-table">
                            <thead>
                                <tr>
                                    <th v-for="ra in row_attr_names(block)" :key="'rh-'+ra" class="th-attr">{{ ra }}</th>
                                    <th v-for="gv in block.grid_attribute_values" :key="'gh-'+gv" class="th-size">{{ gv }}</th>
                                    <th class="th-total">Total</th>
                                    <th v-if="edit" class="th-action"></th>
                                </tr>
                            </thead>
                            <tbody>
                                <tr v-for="(row, rowIdx) in block.rows" :key="'row-'+blockIdx+'-'+rowIdx">
                                    <td v-for="ra in row_attr_names(block)" :key="'rv-'+ra" class="td-attr">
                                        <div class="attr-pill">{{ row.attrs[ra] || '' }}</div>
                                    </td>
                                    <td v-for="(gv, colIdx) in block.grid_attribute_values" :key="'qc-'+colIdx" class="td-qty">
                                        <div :class="get_qty_class(blockIdx, rowIdx, colIdx)" class="qty-field-container"></div>
                                    </td>
                                    <td class="td-total">{{ get_row_total(block, rowIdx) }}</td>
                                    <td v-if="edit" class="td-action">
                                        <button class="btn-delete-row" @click="remove_row(blockIdx, rowIdx)" title="Remove Row">
                                            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"></polyline><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path><line x1="10" y1="11" x2="10" y2="17"></line><line x1="14" y1="11" x2="14" y2="17"></line></svg>
                                        </button>
                                    </td>
                                </tr>
                            </tbody>
                            <tfoot v-if="block.rows.length > 1">
                                <tr class="footer-row">
                                    <td :colspan="row_attr_names(block).length" class="td-footer-label">Grand Total</td>
                                    <td v-for="gv in block.grid_attribute_values" :key="'ct-'+gv" class="td-footer-val">
                                        {{ get_col_total(block, gv) }}
                                    </td>
                                    <td class="td-footer-grand">{{ get_block_total(block) }}</td>
                                    <td v-if="edit" class="td-action"></td>
                                </tr>
                            </tfoot>
                        </table>
                    </div>
                    
                    <!-- Add Row -->
                    <div v-if="edit" class="add-row-section">
                        <div class="add-row-inputs">
                            <div v-for="ra in row_attr_names(block)" :key="'add-ra-'+blockIdx+'-'+ra"
                                 :class="get_add_row_attr_class(blockIdx, ra)" class="add-attr-control"></div>
                        </div>
                        <button class="btn-add-row" @click="add_attribute_row(blockIdx)">
                            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="12" y1="5" x2="12" y2="19"></line><line x1="5" y1="12" x2="19" y2="12"></line></svg>
                            Add Combination
                        </button>
                    </div>
                </template>

                <!-- Grid attribute only (no row attributes) — single row -->
                <template v-else-if="block.item && block.grid_attribute && !has_row_attributes(block)">
                    <div class="table-container">
                        <table class="grid-table">
                            <thead>
                                <tr>
                                    <th v-for="gv in block.grid_attribute_values" :key="'gh1-'+gv" class="th-size">{{ gv }}</th>
                                    <th class="th-total">Total</th>
                                </tr>
                            </thead>
                            <tbody>
                                <tr>
                                    <td v-for="(gv, colIdx) in block.grid_attribute_values" :key="'sq-'+colIdx" class="td-qty">
                                        <div :class="get_qty_class(blockIdx, 0, colIdx)" class="qty-field-container"></div>
                                    </td>
                                    <td class="td-total">{{ get_row_total(block, 0) }}</td>
                                </tr>
                            </tbody>
                        </table>
                    </div>
                </template>

                <!-- No grid attribute — single qty -->
                <template v-else-if="block.item && !block.grid_attribute">
                    <div class="single-qty-box">
                        <label class="qty-label">Enter Quantity</label>
                        <div :class="get_qty_class(blockIdx, 0, 0)" class="single-qty-input-wrapper"></div>
                    </div>
                </template>
                
                <!-- Initial state -->
                <div v-else-if="!block.item" class="empty-state">
                    <div class="empty-icon">
                        <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="#d1d8dd" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"></circle><line x1="12" y1="8" x2="12" y2="12"></line><line x1="12" y1="16" x2="12.01" y2="16"></line></svg>
                    </div>
                    <p>Select an item to see the production grid</p>
                </div>
            </div>
        </div>

        <div v-if="edit" class="footer-actions">
            <button class="btn-add-item-grand" @click="add_block()">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><line x1="12" y1="5" x2="12" y2="19"></line><line x1="5" y1="12" x2="19" y2="12"></line></svg>
                Add Another Item
            </button>
        </div>
    </div>
</template>

<script setup>
import { ref, nextTick } from 'vue';

const root = ref(null);
const settings = ref({ attributes: [], grid_attribute: null });
const items = ref([]);
const block_inputs = ref([]);
const edit = ref(true);
let loading = false;

function set_settings(s) {
    settings.value = s || { attributes: [], grid_attribute: null };
}

function set_edit(flag) {
    edit.value = flag;
}

function mark_dirty() {
    if (loading) return;
    if (cur_frm) cur_frm.dirty();
}

function has_row_attributes(block) {
    return Object.keys(block.row_attributes || {}).length > 0;
}

function row_attr_names(block) {
    return Object.keys(block.row_attributes || {});
}

function get_row_total(block, rowIdx) {
    let row = block.rows[rowIdx];
    if (!row) return 0;
    let total = 0;
    for (let key in row.qty) {
        total += flt(row.qty[key] || 0);
    }
    return total;
}

function get_col_total(block, gv) {
    let total = 0;
    for (let row of block.rows) {
        total += flt(row.qty[gv] || 0);
    }
    return total;
}

function get_block_total(block) {
    let total = 0;
    if (!block.rows) return 0;
    for (let row of block.rows) {
        for (let key in row.qty) {
            total += flt(row.qty[key] || 0);
        }
    }
    return total;
}

function get_item_input_class(blockIdx) {
    return 'po-item-cell-' + blockIdx;
}

function get_qty_class(blockIdx, rowIdx, colIdx) {
    return 'po-qty-' + blockIdx + '-' + rowIdx + '-' + colIdx;
}

function get_add_row_attr_class(blockIdx, attr) {
    return 'po-add-attr-' + blockIdx + '-' + attr.replace(/\s+/g, '_');
}

function $root() {
    return $(root.value);
}

function add_block() {
    let blockIdx = items.value.length;
    items.value.push({
        item: null,
        grid_attribute: null,
        grid_attribute_values: [],
        row_attributes: {},
        rows: [],
    });
    mark_dirty();
    nextTick(() => {
        create_item_input(blockIdx);
        clean_labels();
    });
}

function remove_block(blockIdx) {
    destroy_block_inputs(blockIdx);
    items.value.splice(blockIdx, 1).forEach((block, idx) => {
        // cleanup if needed
    });
    block_inputs.value.splice(blockIdx, 1);
    mark_dirty();
    nextTick(() => {
        rebuild_all_inputs();
    });
}

function on_item_selected(blockIdx, item_name) {
    if (!item_name || loading) return;
    frappe.call({
        method: "yrp.yrp.doctype.production_order.production_order.get_item_production_attributes",
        args: { item: item_name },
        async: false,
        callback(r) {
            if (r.message) {
                let d = r.message;
                let block = items.value[blockIdx];
                block.item = item_name;
                block.grid_attribute = d.grid_attribute;
                block.grid_attribute_values = d.grid_attribute_values || [];
                block.row_attributes = d.row_attributes || {};
                block.rows = [];

                if (!has_row_attributes(block)) {
                    if (block.grid_attribute) {
                        let qty = {};
                        for (let gv of block.grid_attribute_values) {
                            qty[gv] = 0;
                        }
                        block.rows.push({ attrs: {}, qty: qty });
                    } else {
                        block.rows.push({ attrs: {}, qty: { _default: 0 } });
                    }
                }

                mark_dirty();
                nextTick(() => {
                    create_qty_inputs_for_block(blockIdx);
                    create_add_row_inputs(blockIdx);
                    clean_labels();
                });
            }
        },
    });
}

function add_attribute_row(blockIdx) {
    let block = items.value[blockIdx];
    let bi = block_inputs.value[blockIdx];
    if (!bi || !bi.add_row_attrs) return;

    let attrs = {};
    let valid = true;
    for (let ra of row_attr_names(block)) {
        let ctrl = bi.add_row_attrs[ra];
        let val = ctrl ? ctrl.get_value() : null;
        if (!val) {
            frappe.show_alert({ message: __("Please select {0}", [ra]), indicator: "orange" });
            valid = false;
            break;
        }
        attrs[ra] = val;
    }
    if (!valid) return;

    for (let row of block.rows) {
        let dup = true;
        for (let ra of row_attr_names(block)) {
            if (row.attrs[ra] !== attrs[ra]) {
                dup = false;
                break;
            }
        }
        if (dup) {
            frappe.show_alert({ message: __("This combination already exists"), indicator: "orange" });
            return;
        }
    }

    let qty = {};
    for (let gv of block.grid_attribute_values) {
        qty[gv] = 0;
    }
    block.rows.push({ attrs: { ...attrs }, qty: qty });
    mark_dirty();

    for (let ra of row_attr_names(block)) {
        let ctrl = bi.add_row_attrs[ra];
        if (ctrl) ctrl.set_value('');
    }

    nextTick(() => {
        let rowIdx = block.rows.length - 1;
        create_qty_inputs_for_row(blockIdx, rowIdx);
        clean_labels();
    });
}

function remove_row(blockIdx, rowIdx) {
    let block = items.value[blockIdx];
    block.rows.splice(rowIdx, 1);
    mark_dirty();
    nextTick(() => {
        rebuild_block_qty_inputs(blockIdx);
    });
}

function ensure_block_inputs(blockIdx) {
    if (!block_inputs.value[blockIdx]) {
        block_inputs.value[blockIdx] = { item: null, rows_qty: [], add_row_attrs: {} };
    }
}

function create_item_input(blockIdx) {
    let parent = $root().find("." + get_item_input_class(blockIdx));
    if (parent.length === 0) return;
    parent.empty();

    let control = frappe.ui.form.make_control({
        parent: parent,
        df: {
            fieldtype: 'Link',
            fieldname: 'item_' + blockIdx,
            options: 'Item',
            placeholder: __('Select Item'),
            onchange() {
                on_item_selected(blockIdx, this.get_value());
            },
        },
        render_input: true,
    });

    ensure_block_inputs(blockIdx);
    block_inputs.value[blockIdx].item = control;

    if (items.value[blockIdx].item) {
        control.set_input(items.value[blockIdx].item);
    }
}

function create_qty_inputs_for_block(blockIdx) {
    let block = items.value[blockIdx];
    ensure_block_inputs(blockIdx);
    block_inputs.value[blockIdx].rows_qty = [];
    for (let rowIdx = 0; rowIdx < block.rows.length; rowIdx++) {
        create_qty_inputs_for_row(blockIdx, rowIdx);
    }
}

function create_qty_inputs_for_row(blockIdx, rowIdx) {
    let block = items.value[blockIdx];
    let row = block.rows[rowIdx];
    if (!row) return;

    ensure_block_inputs(blockIdx);
    if (!block_inputs.value[blockIdx].rows_qty[rowIdx]) {
        block_inputs.value[blockIdx].rows_qty[rowIdx] = {};
    }

    if (block.grid_attribute) {
        for (let colIdx = 0; colIdx < block.grid_attribute_values.length; colIdx++) {
            let gv = block.grid_attribute_values[colIdx];
            let cls = "." + get_qty_class(blockIdx, rowIdx, colIdx);
            let parent = $root().find(cls);
            if (parent.length === 0) continue;
            parent.empty();

            let control = frappe.ui.form.make_control({
                parent: parent,
                df: {
                    fieldtype: 'Float',
                    fieldname: 'qty_' + blockIdx + '_' + rowIdx + '_' + colIdx,
                    onchange() {
                        items.value[blockIdx].rows[rowIdx].qty[gv] = flt(this.get_value());
                        mark_dirty();
                    },
                },
                render_input: true,
            });

            block_inputs.value[blockIdx].rows_qty[rowIdx][gv] = control;

            let saved = row.qty[gv];
            if (saved !== undefined) {
                control.set_input(saved);
            }
        }
    } else {
        let cls = "." + get_qty_class(blockIdx, 0, 0);
        let parent = $root().find(cls);
        if (parent.length === 0) return;
        parent.empty();

        let control = frappe.ui.form.make_control({
            parent: parent,
            df: {
                fieldtype: 'Float',
                fieldname: 'qty_' + blockIdx + '_0_0',
                label: __('Quantity'),
                onchange() {
                    items.value[blockIdx].rows[0].qty._default = flt(this.get_value());
                    mark_dirty();
                },
            },
            render_input: true,
        });

        block_inputs.value[blockIdx].rows_qty[0] = { _default: control };

        let saved = row.qty._default;
        if (saved !== undefined) {
            control.set_input(saved);
        }
    }
}

function create_add_row_inputs(blockIdx) {
    let block = items.value[blockIdx];
    if (!has_row_attributes(block)) return;
    ensure_block_inputs(blockIdx);
    block_inputs.value[blockIdx].add_row_attrs = {};

    for (let ra of row_attr_names(block)) {
        let cls = "." + get_add_row_attr_class(blockIdx, ra);
        let parent = $root().find(cls);
        if (parent.length === 0) continue;
        parent.empty();

        let item_name = block.item;
        let attr_name = ra;

        let control = frappe.ui.form.make_control({
            parent: parent,
            df: {
                fieldtype: 'Link',
                fieldname: 'add_' + ra + '_' + blockIdx,
                options: 'Item Attribute Value',
                placeholder: ra,
                get_query() {
                    return {
                        query: 'yrp.yrp.doctype.item.item.get_item_attribute_values',
                        filters: { item: item_name, attribute: attr_name },
                    };
                },
            },
            render_input: true,
        });

        block_inputs.value[blockIdx].add_row_attrs[ra] = control;
    }
}

function rebuild_all_inputs() {
    remove_all_inputs();
    block_inputs.value = [];
    nextTick(() => {
        for (let i = 0; i < items.value.length; i++) {
            create_item_input(i);
            create_qty_inputs_for_block(i);
            create_add_row_inputs(i);
        }
        clean_labels();
        nextTick(() => { loading = false; });
    });
}

function rebuild_block_qty_inputs(blockIdx) {
    if (block_inputs.value[blockIdx]) {
        block_inputs.value[blockIdx].rows_qty = [];
    }
    nextTick(() => {
        create_qty_inputs_for_block(blockIdx);
        clean_labels();
    });
}

function clean_labels() {
    $root().find(".control-label").remove();
    $root().find(".frappe-control .clearfix").remove();
    $root().find(".like-disabled-input").remove();
}

function destroy_block_inputs(blockIdx) {
    $root().find(".po-item-cell-" + blockIdx).empty();
}

function remove_all_inputs() {
    for (let i = 0; i < block_inputs.value.length; i++) {
        destroy_block_inputs(i);
    }
}

function get_final_output() {
    let output = [];
    for (let block of items.value) {
        if (!block.item) continue;
        let group = { item: block.item, entries: [] };

        for (let row of block.rows) {
            if (block.grid_attribute) {
                for (let gv of block.grid_attribute_values) {
                    let qty = flt(row.qty[gv] || 0);
                    if (qty <= 0) continue;
                    let attrs = { ...row.attrs };
                    attrs[block.grid_attribute] = gv;
                    group.entries.push({ attributes: attrs, qty: qty });
                }
            } else {
                let qty = flt(row.qty._default || 0);
                if (qty <= 0) continue;
                group.entries.push({ attributes: { ...row.attrs }, qty: qty });
            }
        }

        if (group.entries.length > 0) {
            output.push(group);
        }
    }
    return output;
}

function load_data(data) {
    if (!data || data.length === 0) return;
    loading = true;

    for (let group of data) {
        let block = {
            item: group.item,
            grid_attribute: group.grid_attribute,
            grid_attribute_values: group.grid_attribute_values || [],
            row_attributes: group.row_attributes || {},
            rows: [],
        };

        let grid_attr = group.grid_attribute;
        let row_attr_keys = Object.keys(group.row_attributes || {});

        if (grid_attr && row_attr_keys.length > 0) {
            let row_map = {};
            for (let entry of group.entries || []) {
                let row_key_parts = [];
                let row_attrs = {};
                for (let ra of row_attr_keys) {
                    let val = entry.attributes[ra] || '';
                    row_key_parts.push(ra + '=' + val);
                    row_attrs[ra] = val;
                }
                let row_key = row_key_parts.join('|');
                if (!row_map[row_key]) {
                    let qty = {};
                    for (let gv of block.grid_attribute_values) {
                        qty[gv] = 0;
                    }
                    row_map[row_key] = { attrs: row_attrs, qty: qty };
                }
                let gv_val = entry.attributes[grid_attr];
                if (gv_val) {
                    row_map[row_key].qty[gv_val] = flt(entry.qty);
                }
            }
            block.rows = Object.values(row_map);
        } else if (grid_attr) {
            let qty = {};
            for (let gv of block.grid_attribute_values) {
                qty[gv] = 0;
            }
            for (let entry of group.entries || []) {
                let gv_val = entry.attributes[grid_attr];
                if (gv_val) {
                    qty[gv_val] = flt(entry.qty);
                }
            }
            block.rows.push({ attrs: {}, qty: qty });
        } else {
            let total = 0;
            for (let entry of group.entries || []) {
                total += flt(entry.qty);
            }
            block.rows.push({ attrs: {}, qty: { _default: total } });
        }

        items.value.push(block);
    }

    nextTick(() => {
        rebuild_all_inputs();
        nextTick(() => { loading = false; });
    });
}

defineExpose({
    set_settings,
    set_edit,
    get_final_output,
    load_data,
});
</script>

<style scoped>
/* ── Design Tokens ── */
.po-root {
    --primary-color: #2490EF;
    --primary-hover: #1a73e8;
    --bg-light: #f8fafc;
    --border-color: #e2e8f0;
    --text-main: #334155;
    --text-muted: #64748b;
    --success-bg: #ecfdf5;
    --success-text: #059669;
    --danger-bg: #fef2f2;
    --danger-text: #dc2626;
    --card-shadow: 0 4px 6px -1px rgb(0 0 0 / 0.1), 0 2px 4px -2px rgb(0 0 0 / 0.1);
    
    padding: 8px 0;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    color: var(--text-main);
}

/* ── Card Layout ── */
.po-card {
    background: #ffffff;
    border: 1px solid var(--border-color);
    border-radius: 12px;
    margin-bottom: 24px;
    overflow: visible; /* Changed from hidden to allow dropdowns */
    transition: transform 0.2s ease, box-shadow 0.2s ease;
    position: relative;
    z-index: 1;
}

.po-card:focus-within {
    z-index: 10; /* Bring card to front when interacting with inputs */
}

.po-card:hover {
    box-shadow: 0 10px 15px -3px rgb(0 0 0 / 0.1);
}

/* ── Card Header ── */
.po-card-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 14px 20px;
    background: #fdfdfe;
    border-bottom: 1px solid var(--border-color);
}

.header-left {
    display: flex;
    align-items: center;
    gap: 12px;
    flex: 1;
}

.index-badge {
    width: 28px;
    height: 28px;
    background: var(--primary-color);
    color: white;
    border-radius: 50%;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 14px;
    font-weight: 700;
    box-shadow: 0 2px 4px rgba(36, 144, 239, 0.3);
}

.item-selector {
    min-width: 280px;
    max-width: 400px;
}

.header-right {
    display: flex;
    align-items: center;
    gap: 16px;
}

.total-badge {
    background: var(--success-bg);
    color: var(--success-text);
    padding: 6px 14px;
    border-radius: 9999px;
    font-size: 13px;
    font-weight: 600;
    display: flex;
    align-items: center;
    gap: 6px;
    border: 1px solid #d1fae5;
}

.total-badge .label {
    opacity: 0.8;
    text-transform: uppercase;
    font-size: 11px;
    letter-spacing: 0.5px;
}

.btn-remove-block {
    color: #94a3b8;
    background: transparent;
    border: 1px solid transparent;
    padding: 6px;
    border-radius: 8px;
    cursor: pointer;
    transition: all 0.2s;
}

.btn-remove-block:hover {
    background: var(--danger-bg);
    color: var(--danger-text);
    border-color: #fee2e2;
}

/* ── Card Body ── */
.po-card-body {
    padding: 0;
}

.table-container {
    padding: 20px;
    overflow: visible; /* Changed from overflow-x: auto to prevent clipping dropdowns */
}

/* ── Grid Table ── */
.grid-table {
    width: 100%;
    border-collapse: separate;
    border-spacing: 0;
    font-size: 13px;
}

.grid-table th {
    background: #f8fafc;
    padding: 12px 14px;
    color: var(--text-muted);
    font-weight: 600;
    text-transform: uppercase;
    font-size: 11px;
    letter-spacing: 0.05em;
    border-bottom: 2px solid var(--border-color);
    text-align: center;
}

.grid-table .th-attr {
    text-align: left;
    border-radius: 8px 0 0 0;
}

.grid-table .th-total {
    background: #f1f5f9;
}

.grid-table .th-action {
    background: transparent;
    border-bottom: none;
    width: 44px;
}

.grid-table td {
    padding: 8px;
    border-bottom: 1px solid var(--border-color);
    vertical-align: middle;
}

.td-attr {
    background: #fcfdfe;
}

.attr-pill {
    display: inline-block;
    padding: 4px 10px;
    background: #eff6ff;
    color: #1e40af;
    border-radius: 6px;
    font-weight: 600;
    font-size: 12px;
}

.td-qty {
    padding: 4px !important;
    min-width: 80px;
}

.qty-field-container :deep(.frappe-control) {
    margin: 0;
}

.qty-field-container :deep(.form-control) {
    height: 36px !important;
    background: #ffffff !important;
    border: 1px solid #e2e8f0 !important;
    border-radius: 8px !important;
    text-align: center !important;
    font-weight: 600 !important;
    color: var(--text-main) !important;
    transition: all 0.2s !important;
}

.qty-field-container :deep(.form-control:focus) {
    border-color: var(--primary-color) !important;
    box-shadow: 0 0 0 3px rgba(36, 144, 239, 0.1) !important;
    background: #fff !important;
}

.td-total {
    text-align: center;
    font-weight: 700;
    color: var(--primary-color);
    background: #f0f7ff;
    font-size: 14px;
}

.td-action {
    text-align: center;
    border-bottom: none !important;
}

.btn-delete-row {
    color: #cbd5e1;
    background: transparent;
    border: none;
    padding: 6px;
    border-radius: 6px;
    cursor: pointer;
    transition: all 0.2s;
}

.btn-delete-row:hover {
    background: #fff1f2;
    color: #e11d48;
}

/* ── Footer Row ── */
.footer-row td {
    background: #f8fafc !important;
    padding: 12px 14px !important;
    border-top: 2px solid var(--border-color) !important;
    border-bottom: none !important;
}

.td-footer-label {
    text-align: right;
    font-weight: 700;
    color: var(--text-muted);
}

.td-footer-val {
    text-align: center;
    font-weight: 700;
}

.td-footer-grand {
    text-align: center;
    font-weight: 800;
    color: var(--primary-color);
    background: #eff6ff !important;
    font-size: 15px;
}

/* ── Add Row Section ── */
.add-row-section {
    display: flex;
    align-items: flex-end;
    gap: 16px;
    padding: 16px 20px 24px;
    background: #fafbfc;
    border-top: 1px dashed var(--border-color);
    overflow: visible; /* Ensure link options are visible */
}

.add-row-inputs {
    display: flex;
    gap: 12px;
}

.add-attr-control {
    width: 180px;
}

.add-attr-control :deep(.form-control) {
    height: 38px !important;
    border-radius: 8px !important;
}

.btn-add-row {
    height: 38px;
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 0 20px;
    background: #ffffff;
    border: 1px solid var(--border-color);
    border-radius: 10px;
    color: var(--text-main);
    font-weight: 600;
    font-size: 13px;
    cursor: pointer;
    white-space: nowrap;
    transition: all 0.2s;
}

.btn-add-row:hover {
    background: #f1f5f9;
    border-color: #cbd5e1;
    transform: translateY(-1px);
}

/* ── Single Qty Box ── */
.single-qty-box {
    padding: 24px 20px;
    max-width: 320px;
}

.qty-label {
    font-size: 12px;
    font-weight: 700;
    color: var(--text-muted);
    text-transform: uppercase;
    margin-bottom: 8px;
    display: block;
}

.single-qty-input-wrapper :deep(.form-control) {
    height: 44px !important;
    font-size: 16px !important;
    border-radius: 12px !important;
}

/* ── Footer Actions ── */
.footer-actions {
    display: flex;
    justify-content: center;
    padding: 16px 0;
}

.btn-add-item-grand {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 14px 28px;
    background: #ffffff;
    border: 2px dashed #cbd5e1;
    border-radius: 14px;
    color: var(--text-muted);
    font-weight: 700;
    font-size: 15px;
    cursor: pointer;
    transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
}

.btn-add-item-grand:hover {
    background: #f8fafc;
    border-color: var(--primary-color);
    color: var(--primary-color);
    transform: scale(1.02);
}

/* ── Empty State ── */
.empty-state {
    padding: 40px 20px;
    text-align: center;
}

.empty-icon {
    margin-bottom: 12px;
}

.empty-state p {
    color: var(--text-muted);
    font-size: 14px;
    margin: 0;
}

/* ── Animations ── */
.animate-fade {
    animation: fadeIn 0.4s ease-out;
}

@keyframes fadeIn {
    from { opacity: 0; transform: translateY(5px); }
    to { opacity: 1; transform: translateY(0); }
}

/* Fix for Frappe components inside Scoped styles */
:deep(.frappe-control) {
    margin-bottom: 0 !important;
}

:deep(.control-label) {
    display: none !important;
}

:deep(.frappe-control .clearfix) {
    display: none !important;
}
</style>
