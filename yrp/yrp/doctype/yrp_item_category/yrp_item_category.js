// Native classification form; server validation remains authoritative.
frappe.ui.form.on('YRP Item Category', {
	setup(frm) {
		frm.set_query('parent_yrp_item_category', () => ({filters: {
			node_type: frm.doc.node_type === 'Value' ? 'Category' : 'Item Type',
			disabled: 0,
		}}));
	},
	refresh(frm) {
		if (frm.is_new() && frm.doc.node_type) {
			frm.set_value('is_group', Number(frm.doc.node_type !== 'Value'));
		}
	},
	node_type(frm) {
		frm.set_value('is_group', Number(frm.doc.node_type !== 'Value'));
		if (frm.doc.node_type === 'Item Type') frm.set_value('parent_yrp_item_category', null);
	},
});
