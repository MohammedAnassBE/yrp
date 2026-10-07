"""Sales/retail detail reports; never total incompatible transaction UOMs.

Rows come from native permission-filtered `get_list` reads (parent plus item
fields). Packing also requires read access to its Delivery Note. No ledger,
pricing or workflow writes.
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


PARENT_FIELDS = ('customer', 'sales_person', 'order_type', 'retailer', 'status', 'delivery_note',
                 'from_case_no', 'to_case_no', 'yrp_delivered_at', 'yrp_delivered')
ITEM_FIELDS = ('item_code', 'uom', 'qty', 'ordered_qty', 'requested_qty', 'customer_stock_qty',
               'company_qty', 'delivered_qty', 'stock_uom')
LINE = 'line_'


def documents(doctype, filters, date_field):
    """Parents with their item rows from one native permission-filtered get_list, in report order."""
    meta = frappe.get_meta(doctype)
    item_meta = frappe.get_meta(meta.get_field('items').options)
    fields = ['name', 'docstatus', date_field, *(f for f in PARENT_FIELDS if meta.has_field(f))]
    fields += [f'items.{f} as {LINE}{f}' for f in ('name', *ITEM_FIELDS) if f == 'name' or item_meta.has_field(f)]
    docs = {}
    for row in frappe.get_list(doctype, filters=filters, fields=fields, limit_page_length=0,
                               order_by=f'{date_field} asc, name asc, items.idx asc'):
        doc = docs.get(row.name)
        if doc is None:
            doc = docs[row.name] = frappe._dict({k: v for k, v in row.items() if not k.startswith(LINE)}, lines=[])
        if row.get(f'{LINE}name'):
            doc.lines.append(frappe._dict({k[len(LINE):]: v for k, v in row.items() if k.startswith(LINE)}))
    return list(docs.values())


def delivery_notes(names):
    """Readable, named Delivery Notes; one the user cannot read is simply absent."""
    if not names or not frappe.has_permission('Delivery Note', 'read'):
        return {}
    rows = frappe.get_list('Delivery Note', filters={'name': ['in', sorted(names)]},
                           fields=['name', 'docstatus', 'customer', 'company'], limit_page_length=0)
    return {row.name: row for row in rows}


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
    docs = documents(doctype, query, date_field)
    notes = delivery_notes({doc.delivery_note for doc in docs if doc.get('delivery_note')})
    for doc in docs:
        dn = None
        if kind == 'packing':
            dn = notes.get(doc.delivery_note)
            if not dn or dn.docstatus == 2:
                continue
            if any(f.get(key) and dn.get(key) != f[key] for key in ('customer', 'company')):
                continue
        for item in doc.lines:
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
