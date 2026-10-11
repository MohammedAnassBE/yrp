import frappe


def execute():
	"""Keep existing Partner users as explicit members and give them their member roles; remove none."""
	from yrp.yrp_partner.member_roles import sync_member_roles

	if not frappe.db.table_exists("YRP Partner User"):
		return
	users = frappe.get_all("YRP Partner User", filters={"parenttype": "YRP Partner"}, pluck="user", distinct=True)
	sync_member_roles(users, removable=set())
