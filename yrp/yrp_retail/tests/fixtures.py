"""Small fictional masters; callers own the rollback transaction."""
import frappe


def sales_person(party):
    """A salesperson assigned only to the caller's fictional customer."""
    return frappe.get_doc(dict(doctype="Sales Person",
        sales_person_name="Test Person " + frappe.generate_hash(length=10), enabled=1,
        parent_sales_person=frappe.db.get_value("Sales Person", {"lft": 1}, "name"),
        yrp_sales_partner=frappe.db.get_value("Customer", party.name, "default_sales_partner"),
        yrp_customers=[{"customer": party.name}])).insert()


def sales_partner(territory):
    """One fictional partner; no production master is modified by the fixture."""
    return frappe.get_doc(dict(doctype="Sales Partner",
        partner_name="Test Partner " + frappe.generate_hash(length=10),
        territory=territory, commission_rate=1)).insert()


def customer():
    suffix = frappe.generate_hash(length=10)
    group = frappe.get_doc(dict(doctype='Customer Group',customer_group_name='Test Group '+suffix,
        parent_customer_group=frappe.db.get_value('Customer Group',{'lft':1},'name'),is_group=0)).insert()
    territory = frappe.get_doc(dict(doctype='Territory',territory_name='Test Territory '+suffix,
        parent_territory=frappe.db.get_value('Territory',{'lft':1},'name'),is_group=0)).insert()
    return frappe.get_doc(dict(doctype='Customer',customer_name='Test Customer '+suffix,
        customer_type='Individual',customer_group=group.name,territory=territory.name,
        default_sales_partner=sales_partner(territory.name).name)).insert()
