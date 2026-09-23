// Copyright (c) 2023, Essdee and contributors
// For license information, please see license.txt

frappe.ui.form.on('YRP Item Master Template', {
	setup: function(frm) {
		frm.set_query('additional_parameter_value', 'additional_parameters', (doc, cdt, cdn) => {
			let child = locals[cdt][cdn]
			if (!child.additional_parameter_key){
				frappe.throw("Set Additional Parameter Key")
			}
			return {
				filters: {
					key: child.additional_parameter_key,
				}
			}
		});

		frm.get_docfield('additional_parameters', 'additional_parameter_value').get_route_options_for_new_doc = (field) => {
			return {
				key: field.doc.additional_parameter_key,
			}
		};
		frm.set_query('default_unit_of_measure', (doc) => {
			return {
				filters: {
					secondary_only: 0,
				}
			}
		});
	},

	refresh: function(frm) {
		// Duplicate/new templates may carry source IDs until normal save clones
		// them. Hide those links so editing cannot change the source's values.
		frm.fields_dict.attributes.grid.update_docfield_property('mapping', 'hidden', frm.is_new() ? 1 : 0);
		if (frm.doc.__islocal) {
			hide_field(["attribute_list_html", "dependent_attribute_details_html"]);
		} else {
			unhide_field(["attribute_list_html", "dependent_attribute_details_html"]);

			// Setting the HTML for the attribute list
			$(frm.fields_dict['attribute_list_html'].wrapper).html("");
			new frappe.production.ui.ItemAttributeList({
				wrapper: frm.fields_dict["attribute_list_html"].wrapper,
				attr_values: frm.doc.__onload["attr_list"]
			});

			// Setting the HTML for the attribute list
			$(frm.fields_dict['dependent_attribute_details_html'].wrapper).html("");
			new frappe.production.ui.ItemDependentAttributeDetail(frm.fields_dict["dependent_attribute_details_html"].wrapper);

			frm.add_custom_button(__('Create Item'), async () => {
				await frappe.model.with_doctype('Item');
				const hsn_field = frappe.meta.get_docfield('Item', 'gst_hsn_code');
				let d = new frappe.ui.Dialog({
					title: 'Create Item from Template',
					fields: [
						{
							label: 'Item Name',
							fieldname: 'item_name',
							fieldtype: 'Data',
							reqd: 1
						},
						{
							label: 'Item Group',
							fieldname: 'item_group',
							fieldtype: 'Link',
							options: 'Item Group',
							reqd: 1
						},
						...(hsn_field ? [{
							label: __(hsn_field.label),
							fieldname: 'gst_hsn_code',
							fieldtype: hsn_field.fieldtype,
							options: hsn_field.options,
							reqd: 1,
						}] : [])
					],
					primary_action_label: 'Create',
					primary_action(values) {
						frappe.call({
							method: 'yrp.yrp.doctype.yrp_item_master_template.yrp_item_master_template.create_item_from_template',
							args: {
								template_name: frm.doc.name,
								item_name: values.item_name,
								item_group: values.item_group,
								gst_hsn_code: values.gst_hsn_code,
							},
							callback: function(r) {
								if (r.message) {
									d.hide();
									frappe.set_route('Form', 'Item', r.message);
								}
							}
						});
					}
				});
				d.show();
			});
		}
	}
});


// Choosing an Item Type lists its categories; no season/value is guessed.
frappe.ui.form.on('YRP Item Master Template', {
	setup(frm) {
		frm.set_query('item_type', () => ({filters: {node_type: 'Item Type', disabled: 0}}));
		frm.set_query('category', 'categories', () => ({filters: {
			parent_yrp_item_category: frm.doc.item_type, node_type: 'Category', disabled: 0,
		}}));
		frm.set_query('value', 'categories', (doc, cdt, cdn) => ({filters: {
			parent_yrp_item_category: locals[cdt][cdn].category, node_type: 'Value', disabled: 0,
		}}));
	},
	async item_type(frm) {
		const item_type = frm.doc.item_type;
		if (!item_type) {
			frm.clear_table('categories');
			frm.refresh_field('categories');
			return;
		}
		const {message: categories} = await frappe.call({
			method: 'yrp.yrp_retail.category.get_template_categories', args: {item_type},
		});
		if (frm.doc.item_type !== item_type) return;
		frm.clear_table('categories');
		for (const category of categories || []) {
			frm.add_child('categories', {category: category.name});
		}
		frm.refresh_field('categories');
	},
});
frappe.ui.form.on('YRP Item Classification', {
	category(frm, cdt, cdn) {
		frappe.model.set_value(cdt, cdn, 'value', null);
	},
});
