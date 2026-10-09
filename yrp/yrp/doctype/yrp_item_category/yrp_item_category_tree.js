// Standard Frappe tree renderer and native create/edit permissions.
frappe.treeview_settings['YRP Item Category'] = {
	// The selected parent determines the child's level and group/leaf status.
	ignore_fields: ['parent_yrp_item_category', 'node_type', 'is_group'],
	fields: [{fieldname: 'category_name', fieldtype: 'Data', label: __('Name'), reqd: 1}],
	add_tree_node: 'yrp.yrp.doctype.yrp_item_category.yrp_item_category.add_tree_node',
	get_tree_root: false,
	root_label: 'YRP Item Category',
	onload(treeview) {
		// Keep real Item Types editable nodes even when there is only one root.
		treeview.root_value = '';
		treeview.make_tree();
	},
	get_label(node) {
		// Display business labels, while native node values keep their stable IDs.
		return frappe.utils.escape_html(node.is_root ? __('All Item Types') : node.title || node.label);
	},
};
