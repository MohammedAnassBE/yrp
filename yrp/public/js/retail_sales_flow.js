/* Desk actions for the retail-to-sales flow. All mutations use permission-checked
 * server endpoints; read-only quantities remain server-derived. */
(() => {
 frappe.provide('yrp');
 if(yrp.retail_sales_flow_loaded) return;
 yrp.retail_sales_flow_loaded=true;
 const allowed = (doctype) => {
  if (!frappe.model.can_create(doctype)) return false;
  if (frappe.user.has_role('System Manager') || frappe.session.user === 'Administrator') return true;
  if (doctype === 'Sales Order' && frappe.user.has_role('YRP Sales Partner')) return true;
  if (['YRP Retail Order', 'YRP Retail Order Summary'].includes(doctype) &&
      frappe.user.has_role('YRP Sales Person')) return true;
  return !frappe.user.has_role('YRP Partner');
 };
 const saved = (frm) => !frm.is_new() && !frm.is_dirty();
 // Native Desk caches documents between routes. Keep related progress fresh
 // without discarding a user's unsaved edits.
 const invalidate = (doctype,name) => {
  const doc=locals[doctype]?.[name];
  if(doc) doc.__needs_refresh=true;
 };
 const open_result = (r) => frappe.set_route('Form', r.message.doctype, r.message.name);
 function sales_order(frm) {
  const summary = frm.doctype === 'YRP Retail Order Summary';
  const rows = (frm.doc.items || []).map(row => ({source_row: row.name, item_code: row.item_code,
   qty: Math.max(0, (summary ? row.company_qty : row.qty) - (row.ordered_qty || 0))})).filter(row => row.qty > 0);
  const dialog = new frappe.ui.Dialog({title: __('Create Sales Order'), fields: [
   {fieldname:'company', label:__('Company'), fieldtype:'Link', options:'Company', reqd:1, default:frappe.defaults.get_user_default('Company')},
   {fieldname:'delivery_date', label:__('Delivery Date'), fieldtype:'Date', reqd:1, default:frappe.datetime.get_today()},
   {fieldname:'selling_price_list', label:__('Selling Price List'), fieldtype:'Link', options:'Price List'},
   {fieldname:'items', label:__('Quantities to Order'), fieldtype:'Table', cannot_add_rows:true, data:rows,
    fields:[{fieldname:'source_row',fieldtype:'Data',hidden:1},{fieldname:'item_code',label:__('Item'),fieldtype:'Link',options:'Item',in_list_view:1,read_only:1},
     {fieldname:'qty',label:__('Quantity'),fieldtype:'Float',in_list_view:1,reqd:1}]}
  ], primary_action_label:__('Create'), async primary_action(values) {
   const result = await frappe.call({method:'yrp.yrp_retail.sales_sources.make_sales_order', freeze:true,
    args:{source_doctype:frm.doctype,source_name:frm.doc.name,...values,
     items:values.items.filter(row=>row.qty>0).map(row=>({source_row:row.source_row,qty:row.qty}))}});
   dialog.hide(); invalidate(frm.doctype,frm.doc.name); open_result(result);
  }});
  dialog.show();
 }
 frappe.ui.form.on('YRP Visit', {async refresh(frm) {
  if(saved(frm) && allowed('YRP Retail Order')) {
   const visit = frm.doc.name;
   // Use the real relationship rather than a potentially stale form flag.
   // The server also enforces one Retail Order per Visit.
   const order = await frappe.db.exists('YRP Retail Order', {visit});
   if(frm.doc.name === visit && saved(frm) && !order)
    frm.add_custom_button(__('Retail Order'),()=>frappe.new_doc('YRP Retail Order',{visit}),__('Create'));
  }
 }});
 frappe.ui.form.on('YRP Retail Order', {
  // Saving an order updates its Visit's derived flag and modified timestamp.
  after_save(frm) {if(frm.doc.visit) invalidate('YRP Visit',frm.doc.visit);},
  async visit(frm) {
   const name=frm.doc.visit;
   const result=name ? await frappe.db.get_doc('YRP Visit',name) : null;
   if(frm.doc.visit!==name) return;
   await frm.set_value({sales_person:result?.sales_person || '',customer:result?.customer || '',
    retailer:result?.retailer || '',order_type:result?.visit_type || 'Primary',
    order_date:result ? result.visit_datetime.split(' ')[0] : frappe.datetime.get_today()});
  },
  refresh(frm) {
   if(!saved(frm) || frm.doc.docstatus===2) return;
   if(frm.doc.order_type==='Primary' && allowed('Sales Order') && frm.doc.per_ordered<100)
    frm.add_custom_button(__('Sales Order'),()=>sales_order(frm),__('Create'));
   if(frm.doc.order_type==='Secondary' && !frm.doc.summary && allowed('YRP Retail Order Summary'))
    frm.add_custom_button(__('Retail Order Summary'),()=>frappe.new_doc('YRP Retail Order Summary',{
     customer:frm.doc.customer,sales_person:frm.doc.sales_person,order_type:'Secondary',
     from_date:frm.doc.order_date,to_date:frm.doc.order_date}, doc=>{
      doc.source_orders=[];
      frappe.model.add_child(doc,'YRP Retail Order Summary Source','source_orders').order=frm.doc.name;
     }),__('Create'));
  }
 });
 frappe.ui.form.on('YRP Retail Order Summary', {
  setup(frm) {frm.set_query('order','source_orders',()=>({filters:{customer:frm.doc.customer,sales_person:frm.doc.sales_person,order_type:'Secondary',docstatus:['<',2]}}));},
  refresh(frm) {
   if(saved(frm) && frm.doc.docstatus===1 && frm.doc.per_ordered<100 && allowed('Sales Order'))
    frm.add_custom_button(__('Sales Order'),()=>sales_order(frm),__('Create'));
  }
 });
 frappe.ui.form.on('Delivery Note', {refresh(frm) {
  if(!saved(frm) || frm.doc.is_return || !allowed('Packing Slip')) return;
  if(frm.doc.docstatus===0) frm.add_custom_button(__('Carton'),()=>{
   const dialog=new frappe.ui.Dialog({title:__('Create Carton'),fields:[
    {fieldname:'case_no',label:__('Carton Number'),fieldtype:'Int',reqd:1},
    {fieldname:'items',label:__('Packing Quantities'),fieldtype:'Table',cannot_add_rows:true,
     data:frm.doc.items.map(row=>({dn_detail:row.name,item_code:row.item_code,qty:0})),fields:[
      {fieldname:'dn_detail',fieldtype:'Data',hidden:1},
      {fieldname:'item_code',label:__('Item'),fieldtype:'Link',options:'Item',read_only:1,in_list_view:1},
      {fieldname:'qty',label:__('Quantity'),fieldtype:'Float',in_list_view:1}]}],
    primary_action_label:__('Create'),async primary_action(values){
     const r=await frappe.call({method:'yrp.yrp_retail.packing.create_carton',freeze:true,args:{
      delivery_note_name:frm.doc.name,case_no:values.case_no,
      items:values.items.filter(row=>row.qty>0).map(row=>({dn_detail:row.dn_detail,qty:row.qty}))}});
     dialog.hide(); frappe.set_route('Form', 'Packing Slip', r.message.name);
    }}); dialog.show();
  },__('Create'));
  if(frm.doc.docstatus===1) frm.add_custom_button(__('Mark All Cartons Delivered'),()=>frappe.confirm(
   __('Mark all cartons as delivered? Every row must be fully packed.'),async()=>{
    await frappe.call({method:'yrp.yrp_retail.packing.mark_delivery_note_delivered',args:{name:frm.doc.name},freeze:true}); await frm.reload_doc();
    Object.values(locals['Packing Slip'] || {}).filter(doc=>doc.delivery_note===frm.doc.name).forEach(doc=>invalidate('Packing Slip',doc.name));
   }));
 }});
 frappe.ui.form.on('Packing Slip', {
 after_save(frm) {if(frm.doc.delivery_note) invalidate('Delivery Note',frm.doc.delivery_note);},
 refresh(frm) {
  if(!saved(frm) || frm.doc.docstatus!==1 || !allowed('Packing Slip')) return;
  frm.add_custom_button(frm.doc.yrp_delivered ? __('Undo Delivery') : __('Mark Delivered'),async()=>{
   await frappe.call({method:'yrp.yrp_retail.packing.mark_packing_slip_delivered',freeze:true,
    args:{name:frm.doc.name,delivered:frm.doc.yrp_delivered ? false : true}}); await frm.reload_doc();
   invalidate('Delivery Note',frm.doc.delivery_note);
  });
 }});
})();
