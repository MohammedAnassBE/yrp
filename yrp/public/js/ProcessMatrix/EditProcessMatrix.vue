<template>
    <div class="process-matrix-editor">
        <div class="matrix-toolbar">
            <button v-if="!readonly" class="btn btn-sm btn-primary" @click="addGroup">+ Add Group</button>
            <span class="toolbar-meta">{{ groups.length }} group(s) · {{ totalCombos }} combination(s)<span v-if="readonly"> · <em>read-only (submitted)</em></span></span>
        </div>

        <div v-for="(group, gi) in groups" :key="group.group_index" class="matrix-group">
            <div class="group-header">
                <strong>Group {{ group.group_index }}</strong>
                <input class="group-name-input" v-model="group.group_name" :disabled="readonly" @input="scheduleSync" placeholder="Group name (optional)" />
                <button v-if="!readonly" class="btn btn-xs btn-danger" @click="deleteGroup(gi)" title="Delete group">×</button>
            </div>

            <div class="side-block input-side">
                <div class="side-heading">
                    <span>Inputs</span>
                    <button v-if="!readonly" class="btn btn-xs btn-default" @click="addRow(group, 'Input')">+ Add Input</button>
                </div>
                <table class="table table-sm table-bordered combo-table">
                    <thead>
                        <tr>
                            <th v-for="a in input_attributes" :key="'i-h-' + a">{{ a }}</th>
                            <th class="qty-col">Qty</th>
                            <th class="uom-col">UOM</th>
                            <th class="row-actions"></th>
                        </tr>
                    </thead>
                    <tbody>
                        <tr v-for="(row, ri) in group.inputs" :key="'i-' + ri">
                            <td v-for="a in input_attributes" :key="'i-c-' + ri + '-' + a">
                                <select v-model="row.attrs[a]" :disabled="readonly" @change="scheduleSync">
                                    <option :value="null">--</option>
                                    <option v-for="v in attribute_values_input[a] || []" :key="v" :value="v">{{ v }}</option>
                                </select>
                            </td>
                            <td><input class="form-control compact-input" type="number" step="any" :disabled="readonly" v-model.number="row.qty" @input="scheduleSync" /></td>
                            <td><input class="form-control compact-input" type="text" :disabled="readonly" v-model="row.uom" @input="scheduleSync" placeholder="UOM" /></td>
                            <td><button v-if="!readonly" class="btn btn-xs btn-danger" @click="deleteRow(group, 'inputs', ri)">×</button></td>
                        </tr>
                        <tr v-if="group.inputs.length === 0">
                            <td :colspan="input_attributes.length + 3" class="empty-row">No input rows. Click "+ Add Input" above.</td>
                        </tr>
                    </tbody>
                </table>
            </div>

            <div class="side-block output-side">
                <div class="side-heading">
                    <span>Outputs</span>
                    <button v-if="!readonly" class="btn btn-xs btn-default" @click="addRow(group, 'Output')">+ Add Output</button>
                </div>
                <table class="table table-sm table-bordered combo-table">
                    <thead>
                        <tr>
                            <th v-for="a in output_attributes" :key="'o-h-' + a">{{ a }}</th>
                            <th class="qty-col">Qty</th>
                            <th class="uom-col">UOM</th>
                            <th class="row-actions"></th>
                        </tr>
                    </thead>
                    <tbody>
                        <tr v-for="(row, ri) in group.outputs" :key="'o-' + ri">
                            <td v-for="a in output_attributes" :key="'o-c-' + ri + '-' + a">
                                <select v-model="row.attrs[a]" :disabled="readonly" @change="scheduleSync">
                                    <option :value="null">--</option>
                                    <option v-for="v in attribute_values_output[a] || []" :key="v" :value="v">{{ v }}</option>
                                </select>
                            </td>
                            <td><input class="form-control compact-input" type="number" step="any" :disabled="readonly" v-model.number="row.qty" @input="scheduleSync" /></td>
                            <td><input class="form-control compact-input" type="text" :disabled="readonly" v-model="row.uom" @input="scheduleSync" placeholder="UOM" /></td>
                            <td><button v-if="!readonly" class="btn btn-xs btn-danger" @click="deleteRow(group, 'outputs', ri)">×</button></td>
                        </tr>
                        <tr v-if="group.outputs.length === 0">
                            <td :colspan="output_attributes.length + 3" class="empty-row">No output rows. Click "+ Add Output" above.</td>
                        </tr>
                    </tbody>
                </table>
            </div>
        </div>

        <div v-if="groups.length === 0" class="empty-state">
            No groups yet. Click <strong>+ Add Group</strong> above, or use <strong>Generate Combinations</strong> in the form toolbar to seed from the cross-product.
        </div>
    </div>
</template>

<script>
const SYNC_DELAY_MS = 200;

export default {
    name: 'EditProcessMatrix',
    data() {
        return {
            groups: [],
            input_attributes: [],
            output_attributes: [],
            attribute_values_input: {},
            attribute_values_output: {},
            readonly: false,
            _syncTimer: null,
        };
    },
    computed: {
        totalCombos() {
            let n = 0;
            for (const g of this.groups) n += g.inputs.length + g.outputs.length;
            return n;
        },
    },
    methods: {
        load(payload) {
            this.input_attributes = payload.input_attributes || [];
            this.output_attributes = payload.output_attributes || [];
            this.attribute_values_input = payload.attribute_values_input || payload.attribute_values || {};
            this.attribute_values_output = payload.attribute_values_output || payload.attribute_values || {};
            this.readonly = !!payload.readonly;

            const combos = payload.combinations || [];
            const attrs = payload.combination_attributes || [];

            const attrLookup = {};
            for (const a of attrs) {
                const k = a.group_index + '|' + a.side + '|' + a.combo_index;
                if (!attrLookup[k]) attrLookup[k] = {};
                attrLookup[k][a.attribute] = a.attribute_value;
            }

            const grouped = {};
            for (const c of combos) {
                if (!grouped[c.group_index]) {
                    grouped[c.group_index] = {
                        group_index: c.group_index,
                        group_name: c.group_name || '',
                        inputs: [],
                        outputs: [],
                    };
                } else if (c.group_name && !grouped[c.group_index].group_name) {
                    grouped[c.group_index].group_name = c.group_name;
                }
                const k = c.group_index + '|' + c.side + '|' + c.combo_index;
                const attrSet = attrLookup[k] || {};
                const list = c.side === 'Input' ? this.input_attributes : this.output_attributes;
                const attrs = {};
                for (const a of list) attrs[a] = attrSet[a] || null;

                const row = {
                    qty: c.quantity,
                    uom: c.uom || '',
                    attrs,
                };
                if (c.side === 'Input') grouped[c.group_index].inputs.push(row);
                else grouped[c.group_index].outputs.push(row);
            }

            this.groups = Object.values(grouped).sort((a, b) => a.group_index - b.group_index);
        },

        addGroup() {
            const next = (this.groups.reduce((m, g) => Math.max(m, g.group_index), 0)) + 1;
            this.groups.push({ group_index: next, group_name: '', inputs: [], outputs: [] });
            this.scheduleSync();
        },

        deleteGroup(gi) {
            this.groups.splice(gi, 1);
            this.scheduleSync();
        },

        addRow(group, side) {
            const arr = side === 'Input' ? group.inputs : group.outputs;
            const attrs = {};
            const list = side === 'Input' ? this.input_attributes : this.output_attributes;
            for (const a of list) attrs[a] = null;
            arr.push({ qty: 0, uom: '', attrs });
            this.scheduleSync();
        },

        deleteRow(group, key, ri) {
            group[key].splice(ri, 1);
            this.scheduleSync();
        },

        scheduleSync() {
            if (this.readonly) return;
            if (this._syncTimer) clearTimeout(this._syncTimer);
            this._syncTimer = setTimeout(() => {
                this._syncTimer = null;
                this.syncBack();
            }, SYNC_DELAY_MS);
        },

        syncBack() {
            if (!window.cur_frm) return;
            // Clear existing child rows so add_child rebuilds with proper framework metadata
            cur_frm.clear_table('combinations');
            cur_frm.clear_table('combination_attributes');
            for (const g of this.groups) {
                const sides = [['Input', g.inputs], ['Output', g.outputs]];
                for (const [side, arr] of sides) {
                    arr.forEach((row, idx) => {
                        const ci = idx + 1;
                        const c = cur_frm.add_child('combinations');
                        c.group_index = g.group_index;
                        c.group_name = g.group_name || null;
                        c.side = side;
                        c.combo_index = ci;
                        c.quantity = row.qty || 0;
                        c.uom = row.uom || null;
                        for (const [attr, val] of Object.entries(row.attrs || {})) {
                            if (!val) continue;
                            const ca = cur_frm.add_child('combination_attributes');
                            ca.group_index = g.group_index;
                            ca.side = side;
                            ca.combo_index = ci;
                            ca.attribute = attr;
                            ca.attribute_value = val;
                        }
                    });
                }
            }
            cur_frm.refresh_field('combinations');
            cur_frm.refresh_field('combination_attributes');
            cur_frm.dirty();
        },
    },
};
</script>

<style scoped>
.process-matrix-editor { padding: 8px 4px; }
.matrix-toolbar { display: flex; align-items: center; gap: 12px; margin-bottom: 12px; }
.toolbar-meta { color: #888; font-size: 12px; }
.matrix-group { border: 1px solid #d1d8dd; border-radius: 4px; padding: 12px; margin-bottom: 16px; background: #fafbfc; }
.group-header { display: flex; align-items: center; gap: 12px; margin-bottom: 8px; }
.group-name-input { flex: 1; max-width: 280px; padding: 2px 6px; border: 1px solid #d1d8dd; border-radius: 3px; }
.side-block { margin-top: 8px; }
.side-heading { display: flex; justify-content: space-between; align-items: center; margin-bottom: 4px; font-weight: 600; }
.combo-table { margin-bottom: 4px; font-size: 13px; }
.combo-table th { background: #f3f3f3; }
.combo-table select, .combo-table .compact-input {
    width: 100%;
    padding: 2px 6px;
    height: auto;
    font-size: 13px;
}
.qty-col { width: 90px; }
.uom-col { width: 120px; }
.row-actions { width: 36px; }
.empty-row { text-align: center; color: #999; font-style: italic; }
.empty-state { padding: 24px; text-align: center; color: #888; border: 1px dashed #ccc; border-radius: 4px; }
.input-side { border-left: 3px solid #5e64ff; padding-left: 8px; }
.output-side { border-left: 3px solid #28a745; padding-left: 8px; }
</style>
