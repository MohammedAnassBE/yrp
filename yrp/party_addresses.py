import frappe


@frappe.whitelist()
def get_party_address(supplier, preferred_type='Billing'):
	if not supplier:
		return None
	frappe.get_doc('Supplier', supplier).check_permission('read')
	links = frappe.get_all('Dynamic Link', filters={'parenttype':'Address', 'link_doctype':'Supplier', 'link_name':supplier}, pluck='parent')
	if not links:
		return None
	rows = frappe.get_list('Address', filters={'name':['in',links], 'disabled':0}, fields=['name','address_type','is_primary_address','is_shipping_address'], limit_page_length=0)
	for kind in [preferred_type, 'Shipping' if preferred_type == 'Billing' else 'Billing']:
		matches = [r for r in rows if r.address_type == kind]
		primary = [r for r in matches if r.get('is_shipping_address' if kind == 'Shipping' else 'is_primary_address')]
		if len(primary) == 1:
			return primary[0].name
		if len(matches) == 1:
			return matches[0].name
	return rows[0].name if len(rows) == 1 else None
