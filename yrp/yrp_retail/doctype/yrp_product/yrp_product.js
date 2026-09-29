// Product values are independent snapshots; only selecting a template resets them.
frappe.ui.form.on('YRP Product', {
    setup(frm) {
        frm.set_query('item_type', () => ({filters: {node_type: 'Item Type', is_group: 1, disabled: 0}}));
    },
    refresh(frm) {
        update_mapping_links(frm);
        return render_categories(frm);
    },
    after_save(frm) {
        frm._product_mappings_pending_save = false;
        update_mapping_links(frm);
    },
    async item_template(frm) {
        const template = frm.doc.item_template;
        const request = (frm._product_template_request || 0) + 1;
        frm._product_template_request = request;
        if (!template) return;
        // Prefill contains source references; they become private on save.
        // Never let an unsaved Product open and edit the Template's mapping.
        frm._product_mappings_pending_save = true;
        update_mapping_links(frm);
        const result = await frappe.call({
            method: 'yrp.yrp_retail.doctype.yrp_product.yrp_product.get_template_defaults',
            args: {template_name: template}, freeze: true,
            freeze_message: __('Loading template defaults')
        });
        if (request !== frm._product_template_request || frm.doc.item_template !== template) return;
        frm._applying_product_template = true;
        try {
            for (const [field, value] of Object.entries(result.message)) {
                if (Array.isArray(value)) {
                    frm.clear_table(field);
                    value.forEach(row => frm.add_child(field, row));
                    frm.refresh_field(field);
                } else {
                    await frm.set_value(field, value);
                }
            }
            frm.dirty();
        } finally { frm._applying_product_template = false; }
        return render_categories(frm);
    },
    item_type(frm) {
        if (frm._applying_product_template) return;
        frm.clear_table('categories');
        frm.refresh_field('categories');
        return render_categories(frm);
    }
});

function update_mapping_links(frm) {
    const pending = frm.is_new() || frm._product_mappings_pending_save;
    frm.fields_dict.attributes.grid.update_docfield_property('mapping', 'hidden', pending ? 1 : 0);
    frm.set_df_property('attributes', 'description', pending
        ? __('Save the Product to create its own attribute mappings.') : '');
}

function set_category_value(frm, category, value) {
    const rows = frm.doc.categories || [];
    const existing = rows.find(row => row.category === category);
    if (!value) {
        if (!existing) return;
        frappe.model.clear_doc(existing.doctype, existing.name);
        frm.doc.categories = rows.filter(row => row.category !== category);
        frm.doc.categories.forEach((row, index) => { row.idx = index + 1; });
    } else if (existing) {
        if (existing.value === value) return;
        existing.value = value;
    } else {
        frm.add_child('categories', {category, value});
    }
    frm.refresh_field('categories');
    frm.dirty();
}

async function render_categories(frm) {
    const wrapper = frm.fields_dict.category_controls.$wrapper;
    const generation = (frm._category_render || 0) + 1;
    frm._category_render = generation;
    wrapper.empty();
    if (!frm.doc.item_type) return;
    const root = frm.doc.item_type;
    const response = await frappe.call({
        method: 'yrp.yrp_retail.doctype.yrp_product.yrp_product.get_product_categories', args: {item_type: root}
    });
    if (frm._category_render !== generation || frm.doc.item_type !== root) return;
    const categories = response.message || [];
    // Preserve existing disabled assignments for display/removal.
    for (const row of frm.doc.categories || []) {
        if (!categories.some(category => category.name === row.category)) {
            categories.push({name: row.category, category_name: row.category});
        }
    }
    if (!categories.length) $('<p>').addClass('text-muted').text(__('No categories configured for this Item Type.')).appendTo(wrapper);
    const editable = Boolean(frm.perm && frm.perm[0] && frm.perm[0].write);
    categories.forEach(category => {
        const value = (frm.doc.categories || []).find(row => row.category === category.name)?.value || '';
        let initializing = true;
        const control = frappe.ui.form.make_control({parent: wrapper, render_input: true, df: {
            fieldtype: 'Link', fieldname: category.name, label: category.category_name,
            options: 'YRP Item Category', read_only: !editable,
            get_query: () => ({filters: {node_type: 'Value', parent_yrp_item_category: category.name, disabled: 0}}),
            onchange() {
                if (initializing || !editable || frm._category_render !== generation) return;
                set_category_value(frm, category.name, control.get_value());
            }
        }});
        Promise.resolve(control.set_value(value)).finally(() => { initializing = false; });
    });
}
