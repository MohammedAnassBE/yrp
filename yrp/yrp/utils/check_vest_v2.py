import frappe


def run():
	ipd_name = "IPD-Item-00015-1"
	ipd = frappe.get_doc("Item Production Detail", ipd_name)
	print(f"IPD: {ipd.name}")
	print(f"  item: {ipd.item}  ({frappe.db.get_value('Item', ipd.item, 'name1')})")
	print(f"  version: {ipd.version}  status: {ipd.approval_status}")
	print(f"  primary attr: {ipd.primary_item_attribute}  dependent: {ipd.dependent_attribute}")
	print(f"  attributes: {[r.attribute for r in ipd.item_attributes]}")
	print(f"  processes ({len(ipd.ipd_processes)}):")
	for p in ipd.ipd_processes:
		matrices = frappe.get_all(
			"IPD Process Matrix",
			filters={"ipd": ipd_name, "process_name": p.process_name, "docstatus": ["<", 2]},
			pluck="name",
		)
		matrix_info = ""
		for mname in matrices:
			n_combos = len(frappe.get_all("IPD Matrix Combination", filters={"parent": mname}))
			n_attrs = len(frappe.get_all("IPD Matrix Combination Attribute", filters={"parent": mname}))
			matrix_info += f"\n      -> {mname} ({n_combos} combos, {n_attrs} attr-rows)"
		print(f"    {p.process_name}: in={p.in_stage} out={p.out_stage}{matrix_info}")
	print(f"  item_bom rows: {len(ipd.item_bom)}")
	for r in ipd.item_bom:
		name1 = frappe.db.get_value("Item", r.item, "name1")
		print(f"    {r.process_name or '-':20s} {r.qty_of_bom_item}/{r.qty_of_product} {r.uom} of {r.item} ({name1})")
