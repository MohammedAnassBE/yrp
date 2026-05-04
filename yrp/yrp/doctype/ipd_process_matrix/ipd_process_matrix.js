// Common IPD - Process Matrix UI (Vue-driven)

frappe.ui.form.on("IPD Process Matrix", {
	refresh(frm) {
		if (frm.doc.docstatus !== 1) {
			frm.add_custom_button("Generate Combinations", () => generate_combinations(frm));
		}
		frm.add_custom_button("Reload Editor", () => mount_editor(frm));
		mount_editor(frm);
	},
	ipd(frm) {
		mount_editor(frm);
	},
	input_item(frm) {
		mount_editor(frm);
	},
});

function mount_editor(frm) {
	const wrapper = frm.fields_dict.combination_html.wrapper;
	$(wrapper).empty();
	frm.matrixEditor = new frappe.production.ui.EditProcessMatrix(wrapper);
	if (frm.doc.ipd) {
		frappe.db.get_value("Item Production Detail", frm.doc.ipd, "dependent_attribute").then((r) => {
			frm.doc._dependent_attribute = r && r.message ? r.message.dependent_attribute : null;
			load_editor(frm);
		});
	} else {
		load_editor(frm);
	}
}

function load_editor(frm) {
	if (!frm.matrixEditor) return;
	let input_attributes = (frm.doc.input_attributes || []).map((r) => r.attribute).filter(Boolean);
	let output_attributes = (frm.doc.output_attributes || []).map((r) => r.attribute).filter(Boolean);
	// Strip the IPD's dependent attribute (e.g. Stage) — engine assigns it from process in/out stage.
	const dep = frm.doc._dependent_attribute;
	if (dep) {
		input_attributes = input_attributes.filter((a) => a !== dep);
		output_attributes = output_attributes.filter((a) => a !== dep);
	}
	const readonly = frm.doc.docstatus === 1 || frm.doc.docstatus === 2;
	const baseLoad = (values) =>
		frm.matrixEditor.load({
			input_attributes,
			output_attributes,
			attribute_values_input: values.input || {},
			attribute_values_output: values.output || {},
			combinations: frm.doc.combinations || [],
			combination_attributes: frm.doc.combination_attributes || [],
			readonly,
		});
	if (!frm.doc.ipd || (input_attributes.length === 0 && output_attributes.length === 0)) {
		baseLoad({ input: {}, output: {} });
		return;
	}
	frappe.call({
		method: "yrp.yrp.api.matrix.get_matrix_attribute_values",
		args: {
			ipd: frm.doc.ipd,
			input_attributes,
			output_attributes,
			input_item: frm.doc.input_item || null,
		},
		callback: (r) => baseLoad(r.message || { input: {}, output: {} }),
	});
}

function generate_combinations(frm) {
	if (!frm.doc.ipd) {
		frappe.msgprint("Set IPD first.");
		return;
	}
	const inputs = (frm.doc.input_attributes || []).map((r) => r.attribute);
	const outputs = (frm.doc.output_attributes || []).map((r) => r.attribute);
	if (inputs.length === 0 && outputs.length === 0) {
		frappe.msgprint("Add at least one input or output attribute.");
		return;
	}
	frappe.call({
		method: "yrp.yrp.api.matrix.generate_cross_product",
		args: {
			ipd: frm.doc.ipd,
			input_attributes: inputs,
			output_attributes: outputs,
			input_item: frm.doc.input_item || null,
		},
		callback: (r) => {
			if (!r.message) return;
			const next_ci = (side) => {
				let max = 0;
				(frm.doc.combinations || []).forEach((c) => {
					if (c.side === side && c.combo_index > max) max = c.combo_index;
				});
				return max + 1;
			};
			(r.message.input || []).forEach((row) => {
				const ci = next_ci("Input");
				const c = frm.add_child("combinations");
				c.group_index = 0;
				c.side = "Input";
				c.combo_index = ci;
				c.quantity = 0;
				row.attrs.forEach((a) => {
					const ca = frm.add_child("combination_attributes");
					ca.group_index = 0;
					ca.side = "Input";
					ca.combo_index = ci;
					ca.attribute = a.attribute;
					ca.attribute_value = a.attribute_value;
				});
			});
			(r.message.output || []).forEach((row) => {
				const ci = next_ci("Output");
				const c = frm.add_child("combinations");
				c.group_index = 0;
				c.side = "Output";
				c.combo_index = ci;
				c.quantity = 0;
				row.attrs.forEach((a) => {
					const ca = frm.add_child("combination_attributes");
					ca.group_index = 0;
					ca.side = "Output";
					ca.combo_index = ci;
					ca.attribute = a.attribute;
					ca.attribute_value = a.attribute_value;
				});
			});
			frm.refresh_field("combinations");
			frm.refresh_field("combination_attributes");
			mount_editor(frm);
			frappe.show_alert({ message: "Combinations generated. Assign group indices in the editor.", indicator: "green" });
		},
	});
}
