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
		frm.set_query("yrp_item_type", () => ({ filters: { node_type: "Item Type", disabled: 0 } }));
		frm.set_query("category", "yrp_categories", () => ({
			filters: { parent_yrp_item_category: frm.doc.yrp_item_type, node_type: "Category", disabled: 0 },
		}));
		frm.set_query("value", "yrp_categories", (doc, cdt, cdn) => ({
			filters: { parent_yrp_item_category: locals[cdt][cdn].category, node_type: "Value", disabled: 0 },
		}));
	},

	// Choosing an Item Type lists its categories; values are left for the user.
	async yrp_item_type(frm) {
		const itemType = frm.doc.yrp_item_type;
		frm.clear_table("yrp_categories");
		if (itemType) {
			const { message: categories } = await frappe.call({
				method: "yrp.yrp_retail.category.get_template_categories",
				args: { item_type: itemType },
			});
			if (frm.doc.yrp_item_type !== itemType) return;
			for (const category of categories || []) {
				frm.add_child("yrp_categories", { category: category.name });
			}
		}
		frm.refresh_field("yrp_categories");
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

frappe.ui.form.on("YRP Item Classification", {
	category(frm, cdt, cdn) {
		frappe.model.set_value(cdt, cdn, "value", null);
	},
});
