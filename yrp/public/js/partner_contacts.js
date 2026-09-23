// Use Frappe's standard Address/Contact panels and reflect partner read-only access.
for (const doctype of ["Sales Person", "Employee", "YRP Retailer", "Customer", "Sales Partner", "Contact", "Address", "YRP Partner"]) {
	frappe.ui.form.on(doctype, {
		refresh(frm) {
			if (!frm.is_new()) {
				frappe.contacts.render_address_and_contact(frm);
			}
			if (frappe.user.has_role("YRP Partner") && !frappe.user.has_role("System Manager")) {
				frm.set_read_only();
				frm.disable_save();
				for (const fieldname of ["contact_html", "address_html"]) {
					const wrapper = frm.fields_dict[fieldname]?.wrapper;
					if (wrapper) $(wrapper).find(".btn-contact, .btn-address").hide();
				}
			}
		},
	});
}
