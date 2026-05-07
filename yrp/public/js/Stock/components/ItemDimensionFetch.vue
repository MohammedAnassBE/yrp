<template>
    <div ref="root">
        <table class="table table-sm table-bordered">
            <tr v-for="(i, item_index) in items" :key="item_index">
                <td v-if="i.primary_attribute && i.primary_attribute_values && i.primary_attribute_values.length">
                    <table class="table table-sm table-bordered" v-if="i.items && i.items.length > 0">
                        <tr>
                            <th>S.No.</th>
                            <th>Item</th>
                            <th v-for="dim in dimensions" :key="dim.fieldname">{{ dim.label }}</th>
                            <th v-for="attr in i.attributes" :key="attr">{{ attr }}</th>
                            <th v-for="attr in i.primary_attribute_values" :key="attr">{{ attr }}</th>
                            <th v-for="a in other_table_fields" :key="a.name">{{ a.label }}</th>
                            <th v-if="edit"></th>
                        </tr>
                        <tr v-for="(j, item1_index) in i.items" :key="item1_index">
                            <td>{{ item1_index + 1 }}</td>
                            <td>{{ j.name }}</td>
                            <td v-for="dim in dimensions" :key="dim.fieldname">{{ (j.dimensions || {})[dim.fieldname] }}</td>
                            <td v-for="attr in i.attributes" :key="attr">{{ j.attributes[attr] }}</td>
                            <td v-for="(attr, key) in j.values" :key="key">
                                <div v-if="attr && attr.qty">
                                    {{ attr.qty }}<span v-if="j.default_uom">{{ ' ' + j.default_uom }}</span>
                                    <span v-for="a in table_qty_fields" :key="a.name">
                                        <br>
                                        <span>{{ a.label }}: {{ a.format ? a.format(attr[a.name]) : attr[a.name] }}</span>
                                    </span>
                                </div>
                                <div v-else class="text-center">---</div>
                            </td>
                            <td v-for="a in other_table_fields" :key="a.name">{{ a.format ? a.format(j[a.name]) : j[a.name] }}</td>
                            <td v-if="edit">
                                <div v-if="can_remove" class="pull-right cursor-pointer" @click="remove_item(item_index, item1_index)" v-html="frappe.utils.icon('delete', 'md')"></div>
                                <div v-if="can_edit" class="pull-right cursor-pointer" @click="edit_item(item_index, item1_index)" v-html="frappe.utils.icon('edit', 'md', 'mr-1')"></div>
                            </td>
                        </tr>
                    </table>
                </td>
                <td v-else>
                    <table class="table table-sm table-bordered" v-if="i.items && i.items.length > 0">
                        <tr>
                            <th>S.No.</th>
                            <th>Item</th>
                            <th v-for="dim in dimensions" :key="dim.fieldname">{{ dim.label }}</th>
                            <th v-for="attr in i.attributes" :key="attr">{{ attr }}</th>
                            <th>Quantity</th>
                            <th v-for="a in table_qty_fields" :key="a.name">{{ a.label }}</th>
                            <th v-for="a in other_table_fields" :key="a.name">{{ a.label }}</th>
                            <th v-if="edit"></th>
                        </tr>
                        <tr v-for="(j, item1_index) in i.items" :key="item1_index">
                            <td>{{ item1_index + 1 }}</td>
                            <td>{{ j.name }}</td>
                            <td v-for="dim in dimensions" :key="dim.fieldname">{{ (j.dimensions || {})[dim.fieldname] }}</td>
                            <td v-for="attr in i.attributes" :key="attr">{{ j.attributes[attr] }}</td>
                            <td>
                                {{ (j.values && j.values['default'] && j.values['default'].qty) || 0 }}<span v-if="j.default_uom">{{ ' ' + j.default_uom }}</span>
                            </td>
                            <td v-for="a in table_qty_fields" :key="a.name">
                                <span>{{ (j.values && j.values['default']) ? (a.format ? a.format(j.values['default'][a.name]) : j.values['default'][a.name]) : '' }}</span>
                            </td>
                            <td v-for="a in other_table_fields" :key="a.name">{{ a.format ? a.format(j[a.name]) : j[a.name] }}</td>
                            <td v-if="edit">
                                <div v-if="can_remove" class="pull-right cursor-pointer" @click="remove_item(item_index, item1_index)" v-html="frappe.utils.icon('delete', 'md')"></div>
                                <div v-if="can_edit" class="pull-right cursor-pointer" @click="edit_item(item_index, item1_index)" v-html="frappe.utils.icon('edit', 'md', 'mr-1')"></div>
                            </td>
                        </tr>
                    </table>
                </td>
            </tr>
        </table>

        <form v-show="can_create && edit && dimensions_loaded" name="formp" class="form-horizontal" autocomplete="off" @submit.prevent="get_item_details()">
            <div class="row">
                <div v-for="dim in dimensions" :key="dim.fieldname"
                     :class="['dimension-control', 'dimension-control-' + dim.fieldname, 'col-md-' + dim_col_md]"></div>
                <div class="item-control col-md-4"></div>
                <div class="col-md-2 mt-4">
                    <button type="submit" class="btn btn-success">Fetch Item</button>
                </div>
            </div>
        </form>

        <form name="formp" class="form-horizontal new-item-form" autocomplete="off" @submit.prevent="add_item()" v-show="edit && !cur_item_changed && cur_item.item && cur_item.item != ''">
            <div v-show="cur_item.dependent_attribute" class="row">
                <div class="dependent-attribute-controls col-md-6"></div>
            </div>
            <div class="row">
                <div class="item-attribute-controls col-md-6"></div>
                <div class="item-attribute-controls-right col-md-6"></div>
            </div>
            <div v-if="cur_item.item && cur_item.item != '' && show_qty_fields()">
                <div class="d-flex flex-row flex-wrap" v-if="cur_item.primary_attribute && cur_item.primary_attribute_values && cur_item.primary_attribute_values.length">
                    <div class="m-1" v-for="(attr, index) in cur_item.primary_attribute_values" :key="attr">
                        <div><label>{{ attr }}</label></div>
                        <div>
                            <label class="small text-muted">{{ item.default_uom || '' }}</label>
                            <input class="form-control" :id="'qty_control_'+index" :ref="'qty_control_'+index" type="number" min="0" v-model.number="item.values[attr]['qty']">
                        </div>
                        <div v-for="field in editable_qty_fields" :key="field.name">
                            <label class="small text-muted">{{ field.label }}</label>
                            <input class="form-control" type="number" min="0.000" step="0.001" v-model.number="item.values[attr][field.name]">
                        </div>
                    </div>
                </div>
                <div class="row" v-else>
                    <div class="col col-md-6">
                        <label class="small">{{ item.default_uom || 'Qty' }}</label>
                        <input class="form-control" id="qty_control" ref="qty_control" type="number" min="0.000" step="0.001" v-model.number="item.values['default']['qty']" required>
                    </div>
                    <div v-for="field in editable_qty_fields" :key="field.name" class="col">
                        <label class="small text-muted">{{ field.label }}</label>
                        <input class="form-control" type="number" min="0.000" step="0.001" v-model.number="item.values['default'][field.name]">
                    </div>
                </div>
            </div>

            <div class="row" v-if="other_inputs_keys.length">
                <div v-for="key in other_inputs_keys" :class="[key, other_fields_class]" :key="key"></div>
            </div>
            <div>
                <button v-if="!is_edit" type="submit" class="btn btn-success pull-right">Add Item</button>
                <button v-if="is_edit" type="submit" class="btn btn-warning pull-right">Update Item</button>
                <button v-if="is_edit" type="button" @click.prevent="cancel_edit()" class="btn">Cancel</button>
            </div>
        </form>
    </div>
</template>

<script setup>
import { ref, onMounted, computed, nextTick } from 'vue';

const root = ref(null);

const props = defineProps([
    'items', 'edit', 'otherInputs', 'tableFields', 'qtyFields',
    'args', 'validateQty', 'validate', 'showDimensions', 'lockDimensionsOnEdit'
]);
const emit = defineEmits(['itemupdated', 'itemadded', 'itemremoved']);

const dimensions = ref([]);
const dimensions_loaded = ref(false);

const item = ref({
    name: "",
    dimensions: {},
    attributes: {},
    primary_attribute: "",
    values: {},
    default_uom: "",
});
const cur_item = ref({
    item: "",
    attributes: [],
    primary_attribute: "",
    primary_attribute_values: [],
    default_uom: "",
});
const is_edit = ref(false);
const edit_index = ref(-1);
const edit_index1 = ref(-1);
const cur_item_changed = ref(false);
const sample_doc = ref({});
const cur_dependent_attribute_value = ref(null);

let dimension_inputs = {};   // fieldname -> control
let item_input = null;
let attribute_inputs = null;
let other_input_controls = null;
let dependent_attribute_input = null;

const dim_col_md = computed(() => {
    const n = dimensions.value.length || 1;
    const span = Math.max(2, Math.floor(6 / n));
    return span;
});

onMounted(async () => {
    await load_dimensions();
    create_dimension_item_inputs();
});

async function load_dimensions() {
    if (props.showDimensions === false) {
        dimensions.value = [];
        dimensions_loaded.value = true;
        return;
    }
    return new Promise((resolve) => {
        frappe.call({
            method: 'yrp.stock.api.get_stock_dimensions_for_ui',
            callback: (r) => {
                dimensions.value = r.message || [];
                dimensions_loaded.value = true;
                resolve();
            }
        });
    });
}

const can_create = computed(() => _resolve_arg('can_create', true));
const can_edit = computed(() => _resolve_arg('can_edit', true));
const can_remove = computed(() => _resolve_arg('can_remove', true));

function _resolve_arg(key, def) {
    if (props.args && Object.prototype.hasOwnProperty.call(props.args, key)) {
        const v = props.args[key];
        return v instanceof Function ? Boolean(v()) : Boolean(v);
    }
    return def;
}

function show_qty_fields() {
    if (!cur_item.value.dependent_attribute) return true;
    return Boolean(cur_dependent_attribute_value.value);
}

const other_inputs_keys = computed(() => {
    const x = [];
    if (!props.otherInputs) return x;
    for (const oi of props.otherInputs) {
        if (!x.includes(oi.parent)) x.push(oi.parent);
    }
    return x;
});

const table_qty_fields = computed(() => {
    const out = [];
    const seen = new Set();
    if (props.tableFields) {
        for (const tf of props.tableFields) {
            if (tf.uses_primary_attribute && !seen.has(tf.name)) {
                seen.add(tf.name);
                out.push({ ...tf });
            }
        }
    }
    return out;
});

const other_table_fields = computed(() => {
    const out = [];
    const seen = new Set();
    if (props.tableFields) {
        for (const tf of props.tableFields) {
            if (!tf.uses_primary_attribute && !seen.has(tf.name)) {
                seen.add(tf.name);
                out.push({ ...tf });
            }
        }
    }
    return out;
});

const other_fields_class = computed(() => {
    const n = other_inputs_keys.value.length;
    if (!n) return "col";
    const l = 12 / n;
    if (l >= 6) return "col-md-6";
    if (l == 4) return "col-md-4";
    return "col-md-3";
});

function has_qty_field(field) {
    return props.qtyFields && props.qtyFields.includes(field);
}

const editable_qty_fields = computed(() => {
    const fields = [];
    if (!props.qtyFields || !props.tableFields) return fields;
    for (const tf of props.tableFields) {
        if (tf.uses_primary_attribute && props.qtyFields.includes(tf.name)) {
            fields.push(tf);
        }
    }
    return fields;
});

// ---------- dimension + item input creation ----------
function create_dimension_item_inputs() {
    const el = root.value;
    dimension_inputs = {};
    for (const dim of dimensions.value) {
        const sel = `.dimension-control-${dim.fieldname}`;
        $(el).find(sel).html("");
        dimension_inputs[dim.fieldname] = frappe.ui.form.make_control({
            parent: $(el).find(sel),
            df: {
                fieldtype: 'Link',
                fieldname: dim.fieldname,
                options: dim.options,
                label: dim.label,
                reqd: dim.mandatory ? 1 : 0,
                onchange: () => onchange_dim_or_item(),
            },
            doc: sample_doc.value,
            render_input: true,
        });
    }

    $(el).find('.item-control').html("");
    item_input = frappe.ui.form.make_control({
        parent: $(el).find('.item-control'),
        df: {
            fieldtype: 'Link',
            fieldname: 'item',
            options: 'Item',
            label: 'Item',
            reqd: true,
            get_query: function () {
                if (props.args && props.args.item_query instanceof Function) {
                    return props.args.item_query() || {};
                }
                return {};
            },
            onchange: () => onchange_dim_or_item(),
        },
        doc: sample_doc.value,
        render_input: true,
    });
}

function onchange_dim_or_item() {
    if (!cur_item.value.item || cur_item.value.item == '') return;
    const item_value = item_input.get_value();
    let dim_changed = false;
    for (const dim of dimensions.value) {
        const v = dimension_inputs[dim.fieldname].get_value();
        if (v != (item.value.dimensions || {})[dim.fieldname]) {
            dim_changed = true;
            break;
        }
    }
    if (dim_changed || (item_value != item.value.name && item_value != cur_item.value.item)) {
        cur_item_changed.value = true;
    } else {
        cur_item_changed.value = false;
    }
}

function clear_dimension_item_inputs() {
    for (const fn of Object.keys(dimension_inputs)) dimension_inputs[fn].set_value('');
    if (item_input) item_input.set_value('');
    cur_item.value = {
        item: "",
        attributes: [],
        primary_attribute: "",
        primary_attribute_values: [],
        default_uom: "",
    };
}

function set_dimension_item_inputs() {
    for (const dim of dimensions.value) {
        dimension_inputs[dim.fieldname].set_value((item.value.dimensions || {})[dim.fieldname] || '');
    }
    item_input.set_value(item.value.name);
}

function get_item_details() {
    if (!item_input.get_value()) return;
    // require all mandatory dimensions
    for (const dim of dimensions.value) {
        if (dim.mandatory && !dimension_inputs[dim.fieldname].get_value()) {
            frappe.show_alert({ message: __(dim.label + ' is required'), indicator: 'red' });
            return;
        }
    }
    cur_item.value = {};
    frappe.call({
        method: 'yrp.yrp.doctype.item.item.get_attribute_details',
        args: { item_name: item_input.get_value() },
        callback: function (r) {
            if (r.message) {
                cur_item.value = r.message;
                set_item_details(r.message, null);
            }
        }
    });
}

function _collect_dimension_values() {
    const out = {};
    for (const dim of dimensions.value) {
        out[dim.fieldname] = dimension_inputs[dim.fieldname].get_value() || '';
    }
    return out;
}

function set_item_details(item_details, item1) {
    cur_item_changed.value = false;
    const isEdit = !!(item1 && Object.keys(item1).length > 0);
    if (!isEdit) {
        item.value = {
            name: item_details.item,
            dimensions: _collect_dimension_values(),
            attributes: {},
            primary_attribute: item_details.primary_attribute,
            dependent_attribute: item_details.dependent_attribute,
            values: {},
            default_uom: item_details.default_uom,
            additional_parameters: item_details.additional_parameters,
        };
        for (let i = 0; i < item_details.attributes.length; i++) {
            item.value.attributes[item_details.attributes[i]] = "";
        }
        if (item_details.primary_attribute && item_details.primary_attribute_values && item_details.primary_attribute_values.length) {
            for (let i = 0; i < item_details.primary_attribute_values.length; i++) {
            item.value.values[item_details.primary_attribute_values[i]] = { qty: 0 };
            for (const field of editable_qty_fields.value) item.value.values[item_details.primary_attribute_values[i]][field.name] = 0;
        }
    } else {
            item.value.values['default'] = { qty: 0 };
            for (const field of editable_qty_fields.value) item.value.values['default'][field.name] = 0;
        }
        if (item_details.dependent_attribute) {
            item.value.attributes[item_details.dependent_attribute] = "";
        }
    } else {
        item.value = item1;
        set_dimension_item_inputs();
    }
    if (item_details.dependent_attribute) {
        create_item_dependent_attribute_input(isEdit);
    } else {
        create_item_attribute_inputs();
        if (props.otherInputs) createOtherInputs();
    }
}

function get_attribute_field(attribute, attribute_name, default_value, classname, on_change) {
    const $el = root.value;
    const field = frappe.ui.form.make_control({
        parent: $($el).find(classname),
        df: {
            fieldtype: 'Link',
            fieldname: attribute_name,
            options: 'Item Attribute Value',
            label: attribute_name,
            only_select: true,
            get_query: function () {
                return {
                    query: "yrp.yrp.doctype.item.item.get_item_attribute_values",
                    filters: { "item": cur_item.value.item, "attribute": attribute_name }
                };
            },
            reqd: true,
            onchange: function () {
                const df_me = this;
                const value = df_me.get_value();
                if (!value) {
                    if (on_change) on_change(value);
                    return;
                }
                if (on_change) on_change(value);
            }
        },
        doc: sample_doc,
        render_input: true,
    });
    field.set_value(default_value);
    return field;
}

function _apply_stage(value, preserveValues = false) {
    // Configure cur_item + item.value.values for the chosen dependent stage.
    const d_attr_values = cur_item.value.dependent_attribute_details.attr_list[value];
    if (!d_attr_values) return;
    const stage_attrs = d_attr_values.attributes || [];
    item.value.default_uom = d_attr_values.uom;

    // Use per-stage primary metadata from the backend (enriched in get_attribute_details).
    const stage_primary = d_attr_values.primary_attribute || "";
    const stage_primary_values = d_attr_values.primary_attribute_values || [];
    const stage_uses_primary = !!(stage_primary && stage_primary_values.length);

    if (stage_uses_primary) {
        cur_item.value.primary_attribute = stage_primary;
        cur_item.value.primary_attribute_values = stage_primary_values;
        item.value.primary_attribute = stage_primary;
        if (!preserveValues) {
            item.value.values = {};
            for (const pv of stage_primary_values) {
                item.value.values[pv] = { qty: 0 };
                for (const field of editable_qty_fields.value) item.value.values[pv][field.name] = 0;
            }
        }
    } else {
        cur_item.value.primary_attribute = "";
        cur_item.value.primary_attribute_values = [];
        item.value.primary_attribute = "";
        if (!preserveValues) {
            item.value.values = { default: { qty: 0 } };
            for (const field of editable_qty_fields.value) item.value.values.default[field.name] = 0;
        }
    }

    create_item_attribute_inputs(stage_attrs, 1);
    if (props.otherInputs) createOtherInputs();
}

function create_item_dependent_attribute_input(isEdit = false) {
    dependent_attribute_input = null;
    const $el = root.value;
    $($el).find('.dependent-attribute-controls').html("");
    const dep_attr = cur_item.value.dependent_attribute || item.value.dependent_attribute;
    if (dep_attr) {
        const attribute = dep_attr;
        const attribute_name = attribute.charAt(0).toUpperCase() + attribute.slice(1);
        const default_value = item.value.attributes[attribute];
        dependent_attribute_input = get_attribute_field(attribute, attribute_name, default_value, '.dependent-attribute-controls', (value) => {
            if (value == cur_dependent_attribute_value.value) return;
            if (!value) {
                clear_dependent_attribute_inputs();
            } else {
                // User explicitly changed the stage — always reset values
                _apply_stage(value, false);
            }
            cur_dependent_attribute_value.value = value;
        });
        if (default_value) {
            // On initial load / edit: preserve loaded values when isEdit
            _apply_stage(default_value, isEdit);
            cur_dependent_attribute_value.value = default_value;
        } else {
            clear_dependent_attribute_inputs();
        }
    }
}

function create_item_attribute_inputs(attributes = null, start_index = 0) {
    if (!cur_item.value.item || cur_item.value.item == '') return;
    attribute_inputs = [];
    const $el = root.value;
    $($el).find('.item-attribute-controls').html("");
    $($el).find('.item-attribute-controls-right').html("");
    if (!attributes || attributes.length == 0) {
        attributes = cur_item.value.attributes;
    }
    if (cur_item.value.primary_attribute) {
        attributes = attributes.filter((v) => {
            if (v == cur_item.value.primary_attribute) return false;
            if (v == cur_item.value.dependent_attribute) return false;
            return true;
        });
    }
    for (let i = 0; i < attributes.length; i++) {
        const attribute = attributes[i];
        const attribute_name = attribute.charAt(0).toUpperCase() + attribute.slice(1);
        const classname = (i % 2 == 0) ? '.item-attribute-controls' : '.item-attribute-controls-right';
        attribute_inputs[i] = get_attribute_field(attribute, attribute_name, item.value.attributes[attribute], classname, null);
    }
}

function createOtherInputs() {
    if (!cur_item.value.item || cur_item.value.item == '') return;
    if (!props.otherInputs) return;
    const $el = root.value;
    for (const key of other_inputs_keys.value) {
        $($el).find('.' + key).html("");
    }
    other_input_controls = {};
    for (const data of props.otherInputs) {
        const parent_class = '.' + data.parent;
        if (data['query']) {
            data.df['get_query'] = function () { return data.query(item.value, other_input_controls); };
        }
        other_input_controls[data.name] = frappe.ui.form.make_control({
            parent: $($el).find(parent_class),
            df: data.df,
            doc: sample_doc.value,
            render_input: true,
        });
        if (data.df.default) other_input_controls[data.name].set_value(data.df.default);
        if (item.value[data.name] !== undefined) other_input_controls[data.name].set_value(item.value[data.name]);
    }
}

function get_item_attributes() {
    if (!attribute_inputs) return false;
    const attributes = [];
    const attribute_values = {};
    let dependent_attribute = null;
    let dependent_attribute_value = null;
    if (cur_item.value.dependent_attribute) {
        const attribute = dependent_attribute_input.df.label;
        const value = dependent_attribute_input.get_value();
        attributes.push(attribute);
        if (!value) {
            dependent_attribute_input.$input.select();
            frappe.show_alert({ message: __('Attribute ' + attribute + ' does not have a value'), indicator: 'red' });
            return false;
        }
        attribute_values[attribute] = value;
        dependent_attribute = attribute;
        dependent_attribute_value = value;
    }
    for (let i = 0; i < attribute_inputs.length; i++) {
        const attribute = attribute_inputs[i].df.label;
        attributes.push(attribute);
        const value = attribute_inputs[i].get_value();
        if (!value) {
            attribute_inputs[i].$input.select();
            frappe.show_alert({ message: __('Attribute ' + attribute + ' does not have a value'), indicator: 'red' });
            return false;
        }
        attribute_values[attribute] = value;
    }
    let attr_list = cur_item.value.attributes;
    if (cur_item.value.dependent_attribute) {
        const d_attr_values = cur_item.value.dependent_attribute_details.attr_list[dependent_attribute_value];
        if (!d_attr_values) return false;
        attr_list = d_attr_values.attributes;
        attr_list = attr_list.filter((v) => v !== cur_item.value.primary_attribute);
        attr_list.push(dependent_attribute);
    }
    if (!arrays_equal(attributes, attr_list)) {
        frappe.show_alert({ message: __('Attributes might have changed. Please try again'), indicator: 'red' });
        return false;
    } else {
        item.value.attributes = { ...attribute_values };
    }
    return true;
}

function get_other_details() {
    if (!props.otherInputs || props.otherInputs.length === 0) return true;
    if (!other_input_controls) return false;
    for (const data of props.otherInputs) {
        const label = data.df.label;
        const value = other_input_controls[data.name].get_value();
        if (data.df.reqd && !value) {
            other_input_controls[data.name].$input.select();
            frappe.show_alert({ message: __(label + ' does not have a value'), indicator: 'red' });
            return false;
        }
        item.value[data.name] = value;
    }
    return true;
}

function get_dimension_values_for_save() {
    // Capture current dimension control values into item.value.dimensions
    const dims = {};
    for (const dim of dimensions.value) {
        const v = dimension_inputs[dim.fieldname].get_value() || '';
        if (dim.mandatory && !v) {
            dimension_inputs[dim.fieldname].$input && dimension_inputs[dim.fieldname].$input.select();
            frappe.show_alert({ message: __(dim.label + ' is required'), indicator: 'red' });
            return null;
        }
        dims[dim.fieldname] = v;
    }
    return dims;
}

function get_item_group_index() {
    let index = -1;
    for (let i = 0; i < props.items.length; i++) {
        if (arrays_equal(props.items[i].attributes, cur_item.value.attributes)
            && props.items[i].primary_attribute === cur_item.value.primary_attribute
            && arrays_equal(props.items[i].primary_attribute_values || [], cur_item.value.primary_attribute_values || [])) {
            index = i;
            break;
        }
    }
    return index;
}

function arrays_equal(a, b) {
    const arr1 = (a || []).concat([]);
    const arr2 = (b || []).concat([]);
    arr1.sort(); arr2.sort();
    return JSON.stringify(arr1) === JSON.stringify(arr2);
}

function clear_dependent_attribute() {
    if (!dependent_attribute_input) return;
    dependent_attribute_input.set_value('');
}
function clear_item_attribute_inputs() {
    if (!attribute_inputs) return;
    for (let i = 0; i < attribute_inputs.length; i++) attribute_inputs[i].set_value('');
}
function clear_other_inputs() {
    if (!other_input_controls) return;
    for (const key in other_input_controls) {
        let value = '';
        if (other_input_controls[key].df.default) value = other_input_controls[key].df.default;
        other_input_controls[key].set_value(value);
    }
}
function clear_item_values() {
    for (let i = 0; i < (cur_item.value.attributes || []).length; i++) {
        item.value.attributes[cur_item.value.attributes[i]] = "";
    }
    const has_primary = cur_item.value.primary_attribute && cur_item.value.primary_attribute_values && cur_item.value.primary_attribute_values.length;
    if (has_primary) {
        for (let i = 0; i < cur_item.value.primary_attribute_values.length; i++) {
            item.value.values[cur_item.value.primary_attribute_values[i]] = { qty: 0 };
            for (const field of editable_qty_fields.value) item.value.values[cur_item.value.primary_attribute_values[i]][field.name] = 0;
        }
    } else {
        item.value.values['default'] = { qty: 0 };
        for (const field of editable_qty_fields.value) item.value.values['default'][field.name] = 0;
    }
}
function clear_inputs(force) {
    clear_dependent_attribute();
    clear_item_attribute_inputs();
    clear_other_inputs();
    clear_item_values();
}

function clear_dependent_attribute_inputs() {
    attribute_inputs = [];
    const $el = root.value;
    $($el).find('.item-attribute-controls').html("");
    $($el).find('.item-attribute-controls-right').html("");
    if (!props.otherInputs) return;
    for (const key of other_inputs_keys.value) {
        $($el).find('.' + key).html("");
    }
    other_input_controls = {};
}

function validate_item_values() {
    const has_primary = cur_item.value.primary_attribute && cur_item.value.primary_attribute_values && cur_item.value.primary_attribute_values.length;
    if (!has_primary) {
        if (!item.value.values['default'] || item.value.values['default'].qty == 0) {
            nextTick(() => { document.getElementById("qty_control")?.focus(); });
            frappe.show_alert({ message: __('Quantity cannot be 0'), indicator: 'red' });
            return false;
        }
    } else {
        let total_qty = 0;
        for (let i = 0; i < cur_item.value.primary_attribute_values.length; i++) {
            total_qty += item.value.values[cur_item.value.primary_attribute_values[i]].qty || 0;
        }
        if (total_qty == 0) {
            nextTick(() => { document.getElementById("qty_control_0")?.focus(); });
            frappe.show_alert({ message: __('Quantity cannot be 0'), indicator: 'red' });
            return false;
        }
    }
    return true;
}

async function add_item() {
    if (!get_item_attributes()) return;
    if (!get_other_details()) return;
    const dims = get_dimension_values_for_save();
    if (dims === null) return;
    item.value.dimensions = dims;

    if (props.validateQty && !validate_item_values()) return;
    if (props.validate) {
        const v = await props.validate(item.value);
        if (!v) return;
    }
    if (item.value.name != item_input.get_value()) {
        frappe.show_alert({ message: __('Item does not match'), indicator: 'red' });
        clear_inputs(true);
        return;
    }
    if (is_edit.value) {
        props.items[edit_index.value].items[edit_index1.value] = JSON.parse(JSON.stringify(item.value));
        cancel_edit();
        emit("itemupdated", true);
        return;
    }
    const index = get_item_group_index();
    if (index == -1) {
        props.items.push({
            attributes: cur_item.value.attributes,
            primary_attribute: cur_item.value.primary_attribute,
            dependent_attribute: cur_item.value.dependent_attribute,
            dependent_attribute_details: cur_item.value.dependent_attribute_details,
            primary_attribute_values: cur_item.value.primary_attribute_values,
            items: [JSON.parse(JSON.stringify(item.value))]
        });
    } else {
        props.items[index].items.push(JSON.parse(JSON.stringify(item.value)));
    }
    emit("itemadded", true);
    clear_inputs(false);
}

function remove_item(index, index1) {
    if (is_edit.value) {
        if (edit_index.value == index) {
            if (edit_index1.value > index1) edit_index1.value--;
            else if (edit_index1.value == index1) cancel_edit();
        }
    }
    if (props.items[index].items.length == 1) {
        if (is_edit.value) {
            if (edit_index.value == index) cancel_edit();
            else if (edit_index.value > index) edit_index.value--;
        }
        props.items.splice(index, 1);
    } else {
        props.items[index].items.splice(index1, 1);
    }
    emit("itemremoved", true);
}

function edit_item(index, index1) {
    cur_item_changed.value = false;
    if (!is_edit.value) is_edit.value = !is_edit.value;
    edit_index.value = index;
    edit_index1.value = index1;
    const grp = JSON.parse(JSON.stringify(props.items[index]));
    const row = grp.items[index1];

    // Build a synthetic cur_item from the group; set it BEFORE calling set_item_details
    // so that create_item_dependent_attribute_input has access to dependent_attribute_details.
    cur_item.value = {
        item: row.name,
        attributes: grp.attributes,
        primary_attribute: grp.primary_attribute,
        dependent_attribute: grp.dependent_attribute,
        dependent_attribute_details: grp.dependent_attribute_details,
        primary_attribute_values: grp.primary_attribute_values,
        default_uom: row.default_uom,
    };

    set_item_details(cur_item.value, row);

    if (props.lockDimensionsOnEdit !== false) {
        for (const dim of dimensions.value) {
            dimension_inputs[dim.fieldname].df.read_only = 1;
            dimension_inputs[dim.fieldname].refresh();
        }
    }
    item_input.df.read_only = 1;
    item_input.refresh();
}

function cancel_edit() {
    is_edit.value = !is_edit.value;
    edit_index.value = -1;
    edit_index1.value = -1;
    clear_inputs(true);
    for (const dim of dimensions.value) {
        dimension_inputs[dim.fieldname].df.read_only = 0;
        dimension_inputs[dim.fieldname].refresh();
    }
    if (item_input) {
        item_input.df.read_only = 0;
        item_input.refresh();
    }
}
</script>
