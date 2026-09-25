"""Customer-derived retailer ownership with fictional native records."""
import frappe
from unittest.mock import patch
from yrp.yrp_retail.test_retail_flow import TestRetailFlow

class TestRetailerPartner(TestRetailFlow):
    def setUp(self):
        super().setUp()
        frappe.set_user('Administrator')
        self.partners=[]
        self.users=[]
        # Keep the initial scan off copied business records; subsequent fictional
        # Sales Partner/Contact inserts run normal membership hooks.
        get_all = frappe.get_all
        with patch.object(frappe, 'get_all', side_effect=lambda dt, *args, **kw: [] if dt == 'Sales Partner' else get_all(dt, *args, **kw)):
            frappe.get_doc(dict(doctype='YRP Partner Type',partner_type_name=self.label('Partner Kind'),reference_doctype='Sales Partner')).insert()
        for index in range(2):
            partner=frappe.get_doc(dict(doctype='Sales Partner',partner_name=self.label('Partner'),territory=self.customer.territory,commission_rate=1)).insert()
            user=frappe.get_doc(dict(doctype='User',email=frappe.generate_hash(length=12)+'@example.invalid',first_name='Fictional Partner',send_welcome_email=0,roles=[dict(role='YRP Partner')])).insert()
            frappe.get_doc(dict(doctype='Contact',first_name=self.label('Partner Contact'),user=user.name,email_ids=[dict(email_id=user.email,is_primary=1)],links=[dict(link_doctype='Sales Partner',link_name=partner.name)])).insert()
            self.partners.append(partner);self.users.append(user)
        self.customer.default_sales_partner=self.partners[0].name
        self.customer.save()
        self.person.yrp_sales_partner=self.partners[0].name
        self.person.save()
        self.outlet=frappe.get_doc(dict(doctype='YRP Retailer',retailer_name=self.label('Outlet'),shop_name=self.label('Shop'),sales_person=self.person.name,customer=self.customer.name)).insert()

    def test_customer_required_and_partner_cannot_be_forged(self):
        with self.assertRaises(frappe.ValidationError):
            frappe.get_doc(dict(doctype='YRP Retailer',retailer_name=self.label('Orphan'),gstin='29'+frappe.generate_hash(length=13).upper())).insert()
        self.assertEqual(self.outlet.sales_partner,self.partners[0].name)
        self.outlet.sales_partner=self.partners[1].name
        self.outlet.save()
        self.assertEqual(self.outlet.sales_partner,self.partners[0].name)

    def test_customer_reassignment_and_clear_sync_existing_retailer(self):
        for value in (self.partners[1].name,None):
            self.customer.default_sales_partner=value
            self.customer.save()
            self.outlet.reload()
            self.assertEqual(self.outlet.sales_partner or None,value)

    def test_partner_list_and_direct_access_follow_live_customer(self):
        for index,expected in ((0,True),(1,False)):
            frappe.set_user(self.users[index].name)
            self.assertEqual(self.outlet.name in frappe.get_list('YRP Retailer',pluck='name'),expected)
            self.assertEqual(bool(frappe.has_permission('YRP Retailer','read',self.outlet)),expected)
        frappe.set_user('Administrator')
        self.customer.default_sales_partner=self.partners[1].name
        self.customer.save()
        frappe.db.set_value('YRP Retailer',self.outlet.name,'sales_partner',self.partners[0].name)
        for index,expected in ((0,False),(1,True)):
            frappe.set_user(self.users[index].name)
            self.assertEqual(self.outlet.name in frappe.get_list('YRP Retailer',pluck='name'),expected)
            self.assertEqual(bool(frappe.has_permission('YRP Retailer','read',self.outlet)),expected)
            if expected:
                self.outlet.reload()
                self.outlet.retailer_name='Forbidden'
                with self.assertRaises(frappe.PermissionError):self.outlet.save()
        frappe.set_user('Administrator')

    def test_cleared_customer_partner_revokes_access(self):
        self.customer.default_sales_partner=None
        self.customer.save()
        frappe.set_user(self.users[0].name)
        self.assertNotIn(self.outlet.name,frappe.get_list('YRP Retailer',pluck='name'))
        self.assertFalse(frappe.has_permission('YRP Retailer','read',self.outlet))
