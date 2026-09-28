frappe.provide('frappe.yrp');
frappe.yrp.attribute_value = function (value) {
    if (typeof value !== 'string' || !value.startsWith('IAV-')) return value;
    const row = (frappe.boot.yrp_attribute_values || {})[value];
    if (!row) frappe.throw(__('Attribute Value information changed. Reload this page before continuing.'));
    return row[1];
};

// Standard Desk controls store scoped IDs. Existing item-specific queries take
// precedence; this supplies attribute filtering where the old Data field had none.
const contextFields = ['doctype', 'name', 'attribute', 'dependent_attribute',
    'po_dependent_attribute', 'packing_attribute', 'primary_attribute',
    'set_item_attribute', 'stiching_attribute', 'production_detail',
    'item_production_detail', 'lot', 'cutting_plan'];
const linkContext = (doc) => Object.fromEntries(contextFields.map(key => [key, doc[key]]));
function configureAttributeLinks(frm) {
    const registry = frappe.boot.yrp_attribute_link_fields || {};
    for (const fieldname of registry[frm.doctype] || []) {
        const field = frm.fields_dict[fieldname];
        if (field && !field.get_query) frm.set_query(fieldname, () => ({
            query: 'yrp.attribute_links.search_values',
            filters: {fieldname, row: linkContext(frm.doc)},
        }));
    }
    for (const df of frm.meta.fields.filter(df => df.fieldtype === 'Table')) {
        for (const fieldname of registry[df.options] || []) {
            const field = frm.fields_dict[df.fieldname]?.grid?.get_field(fieldname);
            if (field && !field.get_query) frm.set_query(fieldname, df.fieldname, (doc, cdt, cdn) => ({
                query: 'yrp.attribute_links.search_values',
                filters: {fieldname, row: linkContext(locals[cdt][cdn]), parent: linkContext(doc)},
            }));
        }
    }
}
// Hook registration is handled per owning DocType below; do not replace Desk
// framework methods or intercept unrelated field controls.

for (const doctype of (frappe.boot.yrp_attribute_link_forms || [])) {
    frappe.ui.form.on(doctype, {refresh: configureAttributeLinks});
}
