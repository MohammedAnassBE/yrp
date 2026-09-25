"""Fictional report fixtures: quantity semantics and native permission filtering."""
import unittest
from unittest.mock import patch
import frappe
from frappe.utils import today
from yrp.yrp_retail import reporting
from yrp.yrp_retail.test_sales_sources import TestSalesSources

class TestReportRows(unittest.TestCase):
    def doc(self, **values):
        return frappe._dict(dict(name='Example', order_date=today(), transaction_date=today(),
                                 creation=today(), docstatus=1, customer='Example Customer', **values))

    def test_uoms_are_preserved_without_conversion(self):
        rows = [reporting.make_row('demand', self.doc(), frappe._dict(item_code='Example Item', qty=qty, uom=uom), 'order_date')
                for qty, uom in ((2, 'Box'), (12, 'Unit'))]
        self.assertEqual([(r['qty'], r['uom']) for r in rows], [(2, 'Box'), (12, 'Unit')])

    def test_summary_remainder_and_allocations(self):
        row = reporting.make_row('allocation', self.doc(), frappe._dict(item_code='Example Item',uom='Box', requested_qty=10,customer_stock_qty=4,company_qty=6,ordered_qty=2), 'order_date')
        self.assertEqual((row['customer_stock_qty'],row['company_qty'],row['pending_qty']), (4,6,4))

    def test_physical_delivery_requires_both_submitted_documents(self):
        item=frappe._dict(item_code='Example Item', stock_uom='Box',qty=3)
        dn=self.doc()
        for ps_status,dn_status,marked,expected in ((1,1,1,3),(0,1,1,0),(1,0,1,0),(1,1,0,0)):
            doc=self.doc(yrp_delivered=marked,from_case_no=1,to_case_no=1)
            doc.docstatus=ps_status;dn.docstatus=dn_status
            row=reporting.make_row('packing',doc,item,'creation',dn)
            self.assertEqual(row['delivered_qty'],expected)
            self.assertEqual(row['pending_qty'],3-expected)

    def test_hidden_parents_are_not_returned(self):
        doc=unittest.mock.Mock()
        doc.has_permission.return_value=False
        with patch.object(reporting.frappe,'get_list',return_value=[frappe._dict(name='Hidden')]), patch.object(reporting.frappe,'get_doc',return_value=doc):
            self.assertEqual(list(reporting.documents('Sales Order', [], 'transaction_date')), [])

    def test_report_query_excludes_cancellations_and_defaults_to_submitted(self):
        filters=dict(from_date=today(),to_date=today())
        with patch.object(reporting.frappe,'has_permission',return_value=True), patch.object(reporting,'documents',return_value=[]) as docs:
            reporting.run('packing',filters)
            conditions=docs.call_args.args[1]
            self.assertIn(['docstatus','<',2],conditions)
            self.assertIn(['docstatus','=',1],conditions)
            reporting.run('packing',dict(filters,include_drafts=1))
            self.assertNotIn(['docstatus','=',1],docs.call_args.args[1])

    def test_packing_does_not_expose_inaccessible_delivery_note(self):
        ps=self.doc(delivery_note='Private Delivery')
        dn=unittest.mock.Mock()
        dn.has_permission.return_value=False
        with patch.object(reporting.frappe,'has_permission',return_value=True), patch.object(reporting,'documents',return_value=[ps]), patch.object(reporting.frappe,'get_doc',return_value=dn):
            self.assertEqual(reporting.run('packing',dict(from_date=today(),to_date=today()))[1],[])

class TestReportIntegration(TestSalesSources):
    def test_reports_use_real_documents_and_draft_filter(self):
        order=self.make_so(qty=4)
        filters=dict(from_date=today(),to_date=today(),customer=self.customer.name)
        columns, rows, message=reporting.run('demand',filters)
        row=next(r for r in rows if r['document']==self.primary)
        self.assertEqual(row['ordered_qty'],4)
        self.assertEqual(row['uom'],self.uom.name)
        self.assertEqual(reporting.run('fulfilment',filters)[1],[])
        rows=reporting.run('fulfilment',dict(filters,include_drafts=1))[1]
        self.assertEqual([(r['document'],r['qty'],r['pending_qty']) for r in rows],[(order.name,4,4)])
        order.delete()
        self.assertEqual(reporting.run('fulfilment',dict(filters,include_drafts=1))[1],[])

    def test_date_range_is_required(self):
        with self.assertRaises(frappe.ValidationError):
            reporting.run('demand',{})

    def test_partner_scope_revoked_membership_hides_demand(self):
        filters=dict(from_date=today(),to_date=today(),customer=self.customer.name)
        frappe.set_user(self.user.name)
        self.assertIn(self.primary,[r['document'] for r in reporting.run('demand',filters)[1]])
        frappe.set_user('Administrator')
        contact=self.contact.reload()
        contact.set('links',[])
        contact.save()
        frappe.set_user(self.user.name)
        self.assertEqual(reporting.run('demand',filters)[1],[])
