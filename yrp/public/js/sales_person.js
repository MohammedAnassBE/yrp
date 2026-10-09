// Customer choices follow the Sales Person's single Sales Partner. The server
// validates the same relationship; changing a partner never erases saved rows.
frappe.ui.form.on("Sales Person", {
	setup(frm) {
		frm.set_query("customer", "yrp_customers", () => ({
			filters: frm.doc.yrp_sales_partner
				? { default_sales_partner: frm.doc.yrp_sales_partner }
				: { name: ["=", ""] },
		}));
	},
	yrp_sales_partner(frm) {
		frm.refresh_field("yrp_customers");
	},
});
