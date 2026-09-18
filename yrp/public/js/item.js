// YRP extensions for ERPNext's canonical Item master.

frappe.ui.form.on("Item", {
	setup(frm) {
		frm.set_query("additional_parameter_value", "additional_parameters", (doc, cdt, cdn) => {
			const row = locals[cdt][cdn];
			if (!row.additional_parameter_key) {
				frappe.throw(__("Set Additional Parameter Key first"));
			}
			return { filters: { key: row.additional_parameter_key } };
		});

		const valueField = frm.get_docfield("additional_parameters", "additional_parameter_value");
		if (valueField) {
			valueField.get_route_options_for_new_doc = (field) => ({
				key: field.doc.additional_parameter_key,
			});
		}
		frm.set_query("stock_uom", () => ({ filters: { secondary_only: 0 } }));
	},

	refresh(frm) {
		const isTemplate = Boolean(frm.doc.has_variants && !frm.doc.variant_of);
		frm.toggle_display(["yrp_attribute_section", "attribute_list_html", "dependent_attribute_details_html"], isTemplate);
		if (!isTemplate || frm.is_new()) {
			return;
		}

		const onload = frm.doc.__onload || {};
		if (frm.fields_dict.attribute_list_html && frappe.production?.ui?.ItemAttributeList) {
			$(frm.fields_dict.attribute_list_html.wrapper).empty();
			new frappe.production.ui.ItemAttributeList({
				wrapper: frm.fields_dict.attribute_list_html.wrapper,
				attr_values: onload.attr_list || [],
			});
		}
		if (frm.fields_dict.dependent_attribute_details_html && frappe.production?.ui?.ItemDependentAttributeDetail) {
			$(frm.fields_dict.dependent_attribute_details_html.wrapper).empty();
			new frappe.production.ui.ItemDependentAttributeDetail(
				frm.fields_dict.dependent_attribute_details_html.wrapper
			);
		}
	},
});
