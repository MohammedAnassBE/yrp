// Fill draft party addresses without overwriting an operator's selection.
for (const [doctype, mappings] of Object.entries({
    'YRP Work Order': [['supplier', 'supplier_address', 'Billing'], ['delivery_location', 'delivery_address', 'Shipping']],
    'YRP Delivery Challan': [['supplier', 'supplier_address', 'Billing'], ['from_location', 'from_address', 'Shipping']],
    'YRP Goods Received Note': [['supplier', 'supplier_address', 'Billing'], ['delivery_location', 'delivery_address', 'Shipping']],
})) {
    async function fill(frm, mapping, changed = false) {
        if (frm.doc.docstatus !== 0) return;
        const [partyField, addressField, preferred_type] = mapping;
        if (!frm.fields_dict[addressField]) return;
        const party = frm.doc[partyField];
        if (!changed && frm.doc[addressField]) return;
        if (!party) {
            if (changed) await frm.set_value(addressField, '');
            return;
        }
        const {message} = await frappe.call({method:'yrp.party_addresses.get_party_address',args:{supplier:party,preferred_type}});
        if (frm.doc[partyField] === party && frm.doc.docstatus === 0 && (changed || !frm.doc[addressField])) {
            await frm.set_value(addressField, message || '');
        }
    }
    const events = {refresh(frm) { for (const mapping of mappings) fill(frm, mapping); }};
    for (const mapping of mappings) events[mapping[0]] = frm => fill(frm, mapping, true);
    frappe.ui.form.on(doctype, events);
}
