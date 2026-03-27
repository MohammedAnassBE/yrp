<template>
    <div ref="root">
        <div v-for="(block, blockIdx) in items" :key="'block-'+blockIdx"
             class="item-block" style="margin-bottom:18px; border:1px solid #d1d8dd; border-radius:4px; padding:12px;">

            <!-- Item header -->
            <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:10px;">
                <div style="display:flex; align-items:center; gap:10px;">
                    <strong>{{ blockIdx + 1 }}.</strong>
                    <div :class="get_item_input_class(blockIdx)" style="min-width:220px;"></div>
                </div>
                <button v-if="edit" class="btn btn-xs btn-danger" @click="remove_block(blockIdx)">&times;</button>
            </div>

            <!-- Grid with row attributes + grid attribute columns -->
            <template v-if="block.item && block.grid_attribute && has_row_attributes(block)">
                <table class="table table-sm table-bordered" style="margin-bottom:8px;">
                    <thead>
                        <tr>
                            <th v-for="ra in row_attr_names(block)" :key="'rh-'+ra">{{ ra }}</th>
                            <th v-for="gv in block.grid_attribute_values" :key="'gh-'+gv">{{ gv }}</th>
                            <th style="width:80px;">Total</th>
                            <th v-if="edit" style="width:40px;"></th>
                        </tr>
                    </thead>
                    <tbody>
                        <tr v-for="(row, rowIdx) in block.rows" :key="'row-'+blockIdx+'-'+rowIdx">
                            <td v-for="ra in row_attr_names(block)" :key="'rv-'+ra">
                                {{ row.attrs[ra] || '' }}
                            </td>
                            <td v-for="(gv, colIdx) in block.grid_attribute_values" :key="'qc-'+colIdx">
                                <div :class="get_qty_class(blockIdx, rowIdx, colIdx)"></div>
                            </td>
                            <td class="text-right">{{ get_row_total(block, rowIdx) }}</td>
                            <td v-if="edit">
                                <button class="btn btn-xs btn-danger" @click="remove_row(blockIdx, rowIdx)">&times;</button>
                            </td>
                        </tr>
                    </tbody>
                    <tfoot>
                        <tr>
                            <td :colspan="row_attr_names(block).length" class="text-right"><strong>Column Total</strong></td>
                            <td v-for="gv in block.grid_attribute_values" :key="'ct-'+gv" class="text-right">
                                <strong>{{ get_col_total(block, gv) }}</strong>
                            </td>
                            <td class="text-right"><strong>{{ get_block_total(block) }}</strong></td>
                            <td v-if="edit"></td>
                        </tr>
                    </tfoot>
                </table>
                <!-- Add Row: inline attribute selectors -->
                <div v-if="edit" style="display:flex; align-items:flex-end; gap:8px; margin-top:4px;">
                    <div v-for="ra in row_attr_names(block)" :key="'add-ra-'+blockIdx+'-'+ra"
                         :class="get_add_row_attr_class(blockIdx, ra)" style="min-width:150px;"></div>
                    <button class="btn btn-xs btn-default" @click="add_attribute_row(blockIdx)">+ Add Row</button>
                </div>
            </template>

            <!-- Grid attribute only (no row attributes) — single row of qty inputs -->
            <template v-else-if="block.item && block.grid_attribute && !has_row_attributes(block)">
                <table class="table table-sm table-bordered" style="margin-bottom:8px;">
                    <thead>
                        <tr>
                            <th v-for="gv in block.grid_attribute_values" :key="'gh1-'+gv">{{ gv }}</th>
                            <th style="width:80px;">Total</th>
                        </tr>
                    </thead>
                    <tbody>
                        <tr>
                            <td v-for="(gv, colIdx) in block.grid_attribute_values" :key="'sq-'+colIdx">
                                <div :class="get_qty_class(blockIdx, 0, colIdx)"></div>
                            </td>
                            <td class="text-right">{{ get_row_total(block, 0) }}</td>
                        </tr>
                    </tbody>
                </table>
            </template>

            <!-- No grid attribute on item — single qty -->
            <template v-else-if="block.item && !block.grid_attribute">
                <div style="max-width:200px;">
                    <div :class="get_qty_class(blockIdx, 0, 0)"></div>
                </div>
            </template>
        </div>

        <button v-if="edit" class="btn btn-xs btn-default" @click="add_block()">+ Add Item</button>
    </div>
</template>

<script>
export default {
    name: 'ProductionOrderTable',
    data() {
        return {
            settings: { attributes: [], grid_attribute: null },
            items: [],
            block_inputs: [],
            edit: true,
        };
    },
    methods: {
        // --- Settings ---
        set_settings(s) {
            this.settings = s || { attributes: [], grid_attribute: null };
        },

        set_edit(flag) {
            this.edit = flag;
        },

        // --- Helpers ---
        has_row_attributes(block) {
            return Object.keys(block.row_attributes || {}).length > 0;
        },

        row_attr_names(block) {
            return Object.keys(block.row_attributes || {});
        },

        get_row_total(block, rowIdx) {
            let row = block.rows[rowIdx];
            if (!row) return 0;
            let total = 0;
            for (let key in row.qty) {
                total += flt(row.qty[key] || 0);
            }
            return total;
        },

        get_col_total(block, gv) {
            let total = 0;
            for (let row of block.rows) {
                total += flt(row.qty[gv] || 0);
            }
            return total;
        },

        get_block_total(block) {
            let total = 0;
            for (let row of block.rows) {
                for (let key in row.qty) {
                    total += flt(row.qty[key] || 0);
                }
            }
            return total;
        },

        // --- CSS class generators ---
        get_item_input_class(blockIdx) {
            return 'po-item-cell-' + blockIdx;
        },

        get_qty_class(blockIdx, rowIdx, colIdx) {
            return 'po-qty-' + blockIdx + '-' + rowIdx + '-' + colIdx;
        },

        get_add_row_attr_class(blockIdx, attr) {
            return 'po-add-attr-' + blockIdx + '-' + attr.replace(/\s+/g, '_');
        },

        // --- Block management ---
        add_block() {
            let blockIdx = this.items.length;
            this.items.push({
                item: null,
                grid_attribute: null,
                grid_attribute_values: [],
                row_attributes: {},
                rows: [],
            });
            this.$nextTick(() => {
                this.create_item_input(blockIdx);
                this.clean_labels();
            });
        },

        remove_block(blockIdx) {
            this.destroy_block_inputs(blockIdx);
            this.items.splice(blockIdx, 1);
            this.block_inputs.splice(blockIdx, 1);
            this.$nextTick(() => {
                this.rebuild_all_inputs();
            });
        },

        // --- Item selection ---
        on_item_selected(blockIdx, item_name) {
            if (!item_name) return;
            let me = this;
            frappe.call({
                method: "yrp.yrp.doctype.production_order.production_order.get_item_production_attributes",
                args: { item: item_name },
                async: false,
                callback(r) {
                    if (r.message) {
                        let d = r.message;
                        let block = me.items[blockIdx];
                        block.item = item_name;
                        block.grid_attribute = d.grid_attribute;
                        block.grid_attribute_values = d.grid_attribute_values || [];
                        block.row_attributes = d.row_attributes || {};
                        block.rows = [];

                        // If no row attributes, add a single empty row
                        if (!me.has_row_attributes(block)) {
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

                        me.$nextTick(() => {
                            me.create_qty_inputs_for_block(blockIdx);
                            me.create_add_row_inputs(blockIdx);
                            me.clean_labels();
                        });
                    }
                },
            });
        },

        // --- Row management ---
        add_attribute_row(blockIdx) {
            let block = this.items[blockIdx];
            let inputs = this.block_inputs[blockIdx];
            if (!inputs || !inputs.add_row_attrs) return;

            let attrs = {};
            let valid = true;
            for (let ra of this.row_attr_names(block)) {
                let ctrl = inputs.add_row_attrs[ra];
                let val = ctrl ? ctrl.get_value() : null;
                if (!val) {
                    frappe.show_alert({ message: __("Please select {0}", [ra]), indicator: "orange" });
                    valid = false;
                    break;
                }
                attrs[ra] = val;
            }
            if (!valid) return;

            // Check duplicate
            for (let row of block.rows) {
                let dup = true;
                for (let ra of this.row_attr_names(block)) {
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

            // Clear add-row inputs
            for (let ra of this.row_attr_names(block)) {
                let ctrl = inputs.add_row_attrs[ra];
                if (ctrl) ctrl.set_value('');
            }

            this.$nextTick(() => {
                let rowIdx = block.rows.length - 1;
                this.create_qty_inputs_for_row(blockIdx, rowIdx);
                this.clean_labels();
            });
        },

        remove_row(blockIdx, rowIdx) {
            let block = this.items[blockIdx];
            block.rows.splice(rowIdx, 1);
            this.$nextTick(() => {
                this.rebuild_block_qty_inputs(blockIdx);
            });
        },

        // --- Input creation ---
        create_item_input(blockIdx) {
            let me = this;
            let parent = $(this.$refs.root).find("." + this.get_item_input_class(blockIdx));
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
                        me.on_item_selected(blockIdx, this.get_value());
                    },
                },
                render_input: true,
            });

            if (!this.block_inputs[blockIdx]) {
                this.block_inputs[blockIdx] = { item: null, rows_qty: [], add_row_attrs: {} };
            }
            this.block_inputs[blockIdx].item = control;

            if (this.items[blockIdx].item) {
                control.set_input(this.items[blockIdx].item);
            }
        },

        create_qty_inputs_for_block(blockIdx) {
            let block = this.items[blockIdx];
            if (!this.block_inputs[blockIdx]) {
                this.block_inputs[blockIdx] = { item: null, rows_qty: [], add_row_attrs: {} };
            }
            this.block_inputs[blockIdx].rows_qty = [];
            for (let rowIdx = 0; rowIdx < block.rows.length; rowIdx++) {
                this.create_qty_inputs_for_row(blockIdx, rowIdx);
            }
        },

        create_qty_inputs_for_row(blockIdx, rowIdx) {
            let me = this;
            let block = this.items[blockIdx];
            let row = block.rows[rowIdx];
            if (!row) return;

            if (!this.block_inputs[blockIdx]) {
                this.block_inputs[blockIdx] = { item: null, rows_qty: [], add_row_attrs: {} };
            }
            if (!this.block_inputs[blockIdx].rows_qty[rowIdx]) {
                this.block_inputs[blockIdx].rows_qty[rowIdx] = {};
            }

            if (block.grid_attribute) {
                for (let colIdx = 0; colIdx < block.grid_attribute_values.length; colIdx++) {
                    let gv = block.grid_attribute_values[colIdx];
                    let cls = "." + this.get_qty_class(blockIdx, rowIdx, colIdx);
                    let parent = $(this.$refs.root).find(cls);
                    if (parent.length === 0) continue;
                    parent.empty();

                    let control = frappe.ui.form.make_control({
                        parent: parent,
                        df: {
                            fieldtype: 'Float',
                            fieldname: 'qty_' + blockIdx + '_' + rowIdx + '_' + colIdx,
                            onchange() {
                                me.items[blockIdx].rows[rowIdx].qty[gv] = flt(this.get_value());
                                me.$forceUpdate();
                            },
                        },
                        render_input: true,
                    });

                    this.block_inputs[blockIdx].rows_qty[rowIdx][gv] = control;

                    let saved = row.qty[gv];
                    if (saved) {
                        control.set_value(saved);
                    }
                }
            } else {
                // Single qty (no grid attribute)
                let cls = "." + this.get_qty_class(blockIdx, 0, 0);
                let parent = $(this.$refs.root).find(cls);
                if (parent.length === 0) return;
                parent.empty();

                let control = frappe.ui.form.make_control({
                    parent: parent,
                    df: {
                        fieldtype: 'Float',
                        fieldname: 'qty_' + blockIdx + '_0_0',
                        label: __('Quantity'),
                        onchange() {
                            me.items[blockIdx].rows[0].qty._default = flt(this.get_value());
                            me.$forceUpdate();
                        },
                    },
                    render_input: true,
                });

                this.block_inputs[blockIdx].rows_qty[0] = { _default: control };

                let saved = row.qty._default;
                if (saved) {
                    control.set_value(saved);
                }
            }
        },

        create_add_row_inputs(blockIdx) {
            let me = this;
            let block = this.items[blockIdx];
            if (!this.has_row_attributes(block)) return;
            if (!this.block_inputs[blockIdx]) {
                this.block_inputs[blockIdx] = { item: null, rows_qty: [], add_row_attrs: {} };
            }
            this.block_inputs[blockIdx].add_row_attrs = {};

            for (let ra of this.row_attr_names(block)) {
                let cls = "." + this.get_add_row_attr_class(blockIdx, ra);
                let parent = $(this.$refs.root).find(cls);
                if (parent.length === 0) continue;
                parent.empty();

                let allowed_values = block.row_attributes[ra] || [];

                let control = frappe.ui.form.make_control({
                    parent: parent,
                    df: {
                        fieldtype: 'Autocomplete',
                        fieldname: 'add_' + ra + '_' + blockIdx,
                        placeholder: ra,
                        options: allowed_values,
                    },
                    render_input: true,
                });

                this.block_inputs[blockIdx].add_row_attrs[ra] = control;
            }
        },

        // --- Rebuild helpers ---
        rebuild_all_inputs() {
            this.remove_all_inputs();
            this.block_inputs = [];
            this.$nextTick(() => {
                for (let i = 0; i < this.items.length; i++) {
                    this.create_item_input(i);
                    this.create_qty_inputs_for_block(i);
                    this.create_add_row_inputs(i);
                }
                this.clean_labels();
            });
        },

        rebuild_block_qty_inputs(blockIdx) {
            // Destroy existing qty inputs for this block
            let block = this.items[blockIdx];
            if (this.block_inputs[blockIdx]) {
                this.block_inputs[blockIdx].rows_qty = [];
            }
            this.$nextTick(() => {
                this.create_qty_inputs_for_block(blockIdx);
                this.clean_labels();
            });
        },

        // --- Cleanup ---
        clean_labels() {
            $(this.$refs.root).find(".control-label").remove();
            $(this.$refs.root).find(".frappe-control .clearfix").remove();
            $(this.$refs.root).find(".like-disabled-input").remove();
        },

        destroy_block_inputs(blockIdx) {
            let cls_prefix = ".po-item-cell-" + blockIdx;
            $(this.$refs.root).find(cls_prefix).empty();
            // qty inputs will be cleaned up when DOM is removed
        },

        remove_all_inputs() {
            for (let i = 0; i < this.block_inputs.length; i++) {
                this.destroy_block_inputs(i);
            }
        },

        // --- Data API ---
        get_final_output() {
            let output = [];
            for (let block of this.items) {
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
        },

        load_data(data) {
            if (!data || data.length === 0) return;

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
                    // Group entries by row attribute combination
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
                    // Grid attribute only, single row
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
                    // No grid attribute, single qty
                    let total = 0;
                    for (let entry of group.entries || []) {
                        total += flt(entry.qty);
                    }
                    block.rows.push({ attrs: {}, qty: { _default: total } });
                }

                this.items.push(block);
            }

            this.$nextTick(() => {
                this.rebuild_all_inputs();
            });
        },
    },
}
</script>
