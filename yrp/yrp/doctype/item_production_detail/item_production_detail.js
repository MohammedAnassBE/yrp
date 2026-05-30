// Item Production Detail — Desk client script.
//
// Mirrors production_api's IPD `item` change handler
// (apps/production_api/.../item_production_detail/item_production_detail.js
// line 665) so picking the parent Item auto-fills:
//   • primary_item_attribute
//   • dependent_attribute
//   • dependent_attribute_mapping
//   • item_attributes (the child rows)
//
// The server method `yrp.yrp.doctype.item.item.get_complete_item_details`
// returns the full Item doc as a dict; we map the relevant fields onto the
// IPD form. Clearing the item resets the same fields so the form doesn't
// carry stale state across Item changes.

frappe.ui.form.on("Item Production Detail", {
	item(frm) {
		if (frm.doc.item) {
			frappe.call({
				method: "yrp.yrp.doctype.item.item.get_complete_item_details",
				args: { item_name: frm.doc.item },
				callback: function (r) {
					if (!r || !r.message) return;
					frm.set_value("primary_item_attribute", r.message.primary_attribute);
					frm.set_value("item_attributes", r.message.attributes);
					frm.set_value("dependent_attribute", r.message.dependent_attribute);
					frm.set_value("dependent_attribute_mapping", r.message.dependent_attribute_mapping);
				},
			});
		} else {
			frm.set_value("primary_item_attribute", "");
			frm.set_value("item_attributes", []);
			frm.set_value("dependent_attribute", "");
			frm.set_value("dependent_attribute_mapping", "");
		}
	},
});
