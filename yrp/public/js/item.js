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
		if (frm.doc.variant_of && !frm.is_new()) {
			frm.page.add_menu_item(__("Rename Variant"), () => {
				return rename_variant_from_attributes(frm).catch((error) => {
					frappe.msgprint({
						title: __("Rename failed"),
						message: error.message || error,
						indicator: "red",
					});
				});
			});
		}

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

function rename_variant_from_attributes(frm) {
	const docname = frm.doc.name;
	const doctype = frm.doctype;

	return frappe
		.xcall("yrp.yrp.doctype.yrp_item_variant.yrp_item_variant.rename_item_variant", {
			variant: docname,
			freeze: true,
			freeze_message: __("Renaming..."),
		})
		.then((newDocname) => {
			if (newDocname !== docname) {
				$(document).trigger("rename", [doctype, docname, newDocname]);
				if (locals[doctype] && locals[doctype][docname]) {
					delete locals[doctype][docname];
				}
			}
			return frm.reload_doc();
		});
}
