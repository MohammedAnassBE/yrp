frappe.listview_settings["Bill Tracking"] = {
	onload: (listview) => {
		listview.page.add_action_item(__("Bulk Assign"), () => {
			const dialog = new frappe.ui.Dialog({
				title: __("Bulk Assignment"),
				fields: [
					{
						label: "Assign To",
						fieldname: "assign_to",
						fieldtype: "Link",
						options: "Department",
						reqd: 1,
					},
					{
						label: "Remarks",
						fieldname: "remarks",
						fieldtype: "Small Text",
					},
				],
				primary_action_label: __("Assign All"),
				primary_action: (values) => {
					dialog.hide();
					frappe.call({
						method: "yrp.yrp.doctype.bill_tracking.bill_tracking.bulk_assign_bills",
						args: {
							assign_to: values.assign_to,
							selected_docs: listview.get_checked_items(),
							remarks: values.remarks,
						},
						freeze: true,
						freeze_msg: __("Bulk Assigning Bills"),
						callback: () => {
							cur_list.refresh();
						},
					});
				},
			});
			dialog.show();
		});
	},
	refresh: (listview) => {
		listview.page.add_inner_button(__("Show Assigned To Me"), async () => {
			await listview.filter_area.clear();
			frappe.call({
				method: "yrp.yrp.doctype.department.department.get_user_departments",
				callback: async (r) => {
					await listview.filter_area.set([
						["Bill Tracking", "assigned_to", "in", r.message || []],
						["Bill Tracking", "docstatus", "=", 1],
						["Bill Tracking", "form_status", "!=", "Closed"],
					]);
					listview.refresh();
				},
			});
		});
	},
};
