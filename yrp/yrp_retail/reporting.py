"""Sales/retail detail reports; never total incompatible transaction UOMs.

Use permission-filtered parent queries plus document permission checks. Packing
also requires access to its Delivery Note. No ledger, pricing or workflow writes.
"""
import frappe
from frappe import _
from frappe.utils import cint, flt, getdate

SOURCES = {
    'demand': ('YRP Retail Order', 'order_date'),
    'allocation': ('YRP Retail Order Summary', 'to_date'),
    'fulfilment': ('Sales Order', 'transaction_date'),
    'packing': ('Packing Slip', 'creation'),
}


def column(field, label, fieldtype='Data', options=None):
    result = dict(fieldname=field, label=_(label), fieldtype=fieldtype, width=145)
    if options:
        result['options'] = options
    return result


def documents(doctype, filters, date_field):
    """Page through native permission-filtered records without a silent row cap."""
    start = 0
    while True:
        batch = frappe.get_list(doctype, filters=filters, fields=['name'],
                               order_by=f'{date_field} asc, name asc',
                               limit_start=start, limit_page_length=200)
        for row in batch:
            doc = frappe.get_doc(doctype, row.name)
            if doc.has_permission('read'):
                yield doc
        if len(batch) < 200:
            break
        start += len(batch)


def run(kind, filters=None):
    f = frappe._dict(filters or {})
    doctype, date_field = SOURCES[kind]
    frappe.has_permission(doctype, 'read', throw=True)
    if not f.from_date or not f.to_date or getdate(f.from_date) > getdate(f.to_date):
        frappe.throw(_('Provide a valid From Date and To Date.'))
    # Datetime bounds include the entire final day, without fractional-second gaps.
    from frappe.utils import add_days
    query = [[date_field, '>=', str(getdate(f.from_date))],
             [date_field, '<', str(add_days(getdate(f.to_date), 1))],
             ['docstatus', '<', 2]]
    if kind != 'demand' and not cint(f.include_drafts):
        query.append(['docstatus', '=', 1])
    for field in ('customer', 'sales_person', 'company'):
        if f.get(field) and kind != 'packing':
            if frappe.get_meta(doctype).has_field(field):
                query.append([field, '=', f[field]])
    columns = [column('document', 'Document', 'Link', doctype),
               column('date', 'Date', 'Date'), column('status', 'Status'),
               column('customer', 'Customer', 'Link', 'Customer'),
               column('item_code', 'Item', 'Link', 'Item'),
               column('uom', 'UOM', 'Link', 'UOM')]
    quantities = {
        'demand': [('qty', 'Requested Qty'), ('ordered_qty', 'Allocated to Sales Orders')],
        'allocation': [('requested_qty', 'Requested Qty'), ('customer_stock_qty', 'Customer Supply Qty'),
                       ('company_qty', 'Company Demand Qty'), ('ordered_qty', 'Allocated to Sales Orders'), ('pending_qty', 'Unallocated Company Qty')],
        'fulfilment': [('qty', 'Ordered Qty'), ('delivered_qty', 'ERP Delivered Qty'), ('pending_qty', 'Pending Delivery Qty')],
        'packing': [('qty', 'Packed Qty'), ('delivered_qty', 'Physically Delivered Qty'), ('pending_qty', 'Pending Physical Delivery Qty')],
    }[kind]
    if kind in ('demand', 'allocation'):
        columns.append(column('sales_person', 'Sales Person', 'Link', 'Sales Person'))
    if kind == 'demand':
        columns.extend([column('order_type', 'Order Type'), column('retailer', 'Retailer', 'Link', 'YRP Retailer')])
    if kind == 'packing':
        columns.extend([column('delivery_note', 'Delivery Note', 'Link', 'Delivery Note'),
                        column('from_case_no', 'From Case', 'Int'), column('to_case_no', 'To Case', 'Int'),
                        column('delivered_at', 'Delivered At', 'Datetime')])
    columns.extend(column(field, label, 'Float') for field, label in quantities)
    result = []
    for doc in documents(doctype, query, date_field):
        dn = None
        if kind == 'packing':
            dn = frappe.get_doc('Delivery Note', doc.delivery_note)
            if not dn.has_permission('read') or dn.docstatus == 2:
                continue
            if any(f.get(key) and dn.get(key) != f[key] for key in ('customer', 'company')):
                continue
        for item in doc.items:
            row = make_row(kind, doc, item, date_field, dn)
            if (not f.item_code or row['item_code'] == f.item_code) and (not f.uom or row['uom'] == f.uom):
                result.append(row)
    message = _('Quantities are in each row UOM; no mixed-UOM grand total. Cancelled documents are excluded. ')
    if kind in ('demand', 'allocation'):
        message += _('Allocated quantities include draft and submitted Sales Orders. Summary date is the demand period end date.')
    if kind == 'packing':
        message += _('Date filter uses Packing Slip creation date. Delivered At is shown separately. Draft quantities are planned packing only.')
    if kind == 'fulfilment':
        message += _('ERP delivered quantity is not proof of physical receipt. Closed orders may retain an undelivered balance.')
    return columns, result, message


def make_row(kind, doc, item, date_field, dn=None):
    """One row per source child preserves UOM and repeated-item identity."""
    row = dict(document=doc.name, date=getdate(doc.get(date_field)),
               status=('Draft' if doc.docstatus == 0 else 'Submitted') if kind != 'demand' else 'Recorded',
               customer=(dn.customer if dn else doc.get('customer')),
               item_code=item.item_code, uom=item.get('uom'),
               sales_person=doc.get('sales_person'), order_type=doc.get('order_type'), retailer=doc.get('retailer'))
    if kind == 'allocation':
        for field in ('requested_qty', 'customer_stock_qty', 'company_qty', 'ordered_qty'):
            row[field] = flt(item.get(field))
        row['pending_qty'] = max(0, row['company_qty'] - row['ordered_qty'])
    elif kind == 'packing':
        # Packing Slip's stock_uom label actually holds the DN transaction UOM.
        row.update(uom=item.stock_uom, qty=flt(item.qty), delivery_note=dn.name,
                   from_case_no=doc.from_case_no, to_case_no=doc.to_case_no,
                   delivered_at=doc.get('yrp_delivered_at'))
        row['delivered_qty'] = row['qty'] if doc.docstatus == 1 and dn.docstatus == 1 and doc.get('yrp_delivered') else 0
        row['pending_qty'] = row['qty'] - row['delivered_qty']
    else:
        row['qty'] = flt(item.qty)
        if kind == 'demand':
            row['ordered_qty'] = flt(item.get('ordered_qty'))
        else:
            row['status'] = doc.get('status') or row['status']
            row['delivered_qty'] = flt(item.get('delivered_qty'))
            row['pending_qty'] = max(0, row['qty'] - row['delivered_qty'])
    return row
