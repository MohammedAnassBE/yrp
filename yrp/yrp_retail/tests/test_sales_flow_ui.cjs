const test=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs'),path=require('node:path');
const code=fs.readFileSync(path.join(__dirname,'../../public/js/retail_sales_flow.js'),'utf8');
function setup(){
 const handlers={},dialogs=[],calls=[],routes=[];
 const context={locals:{},yrp:{},__:s=>s,frappe:{provide(){},session:{user:'Administrator'},user:{has_role:()=>false},
  model:{can_create:()=>true},defaults:{get_user_default:()=>null},datetime:{get_today:()=> '2026-09-22'},
  db:{get_doc:async()=>({sales_person:'Test Person',customer:'Test Customer',visit_type:'Secondary',visit_datetime:'2026-09-22 10:00:00'})},
  set_route(...args){routes.push(args)},call:async args=>{calls.push(args);return {message:{name:'Test Document',doctype:'Sales Order'}}},
  ui:{form:{on(dt,events){handlers[dt]=events}},Dialog:class{constructor(opts){Object.assign(this,opts);dialogs.push(this)}show(){}hide(){}}}}};
 vm.runInNewContext(code,context);return {handlers,dialogs,calls,routes,context};
}
test('Visit populates required read-only headers and clears them when removed',async()=>{
 const {handlers}=setup(),doc={visit:'Test Visit'};const frm={doc,set_value:async v=>Object.assign(doc,v)};
 await handlers['YRP Retail Order'].visit(frm);assert.equal(doc.customer,'Test Customer');assert.equal(doc.order_type,'Secondary');
 doc.visit='';await handlers['YRP Retail Order'].visit(frm);assert.equal(doc.customer,'');assert.equal(doc.sales_person,'');
});
test('Late Visit response cannot overwrite a newer selection',async()=>{
 const {handlers,context}=setup();let resolve;context.frappe.db.get_doc=()=>new Promise(r=>resolve=r);
 const frm={doc:{visit:'Old'},set_value:()=>assert.fail('stale write')};const promise=handlers['YRP Retail Order'].visit(frm);
 frm.doc.visit='New';resolve({visit_datetime:'2026-09-22 10:00:00'});await promise;
});
test('Primary order action uses remaining exact source rows and validated API',async()=>{
 const {handlers,dialogs,calls}=setup();let click;
 handlers['YRP Retail Order'].refresh({doctype:'YRP Retail Order',is_new:()=>false,is_dirty:()=>false,
 doc:{name:'Test Order',order_type:'Primary',per_ordered:40,items:[{name:'Test Row',item_code:'Test Item',qty:10,ordered_qty:4}]},add_custom_button:(label,fn)=>click=fn});
 click();assert.equal(dialogs[0].fields[3].data[0].qty,6);
 await dialogs[0].primary_action({company:'Test Company',delivery_date:'2026-09-22',items:[{source_row:'Test Row',qty:6,extra:'not sent'}]});
 assert.deepEqual(JSON.parse(JSON.stringify(calls[0].args.items)),[{source_row:'Test Row',qty:6}]);
});
test('Partner editing roles do not expose processing buttons',()=>{
 const {handlers,context}=setup();context.frappe.session.user='test@example.invalid';context.frappe.user.has_role=r=>r==='YRP Partner';
 handlers['YRP Retail Order'].refresh({is_new:()=>false,is_dirty:()=>false,doc:{order_type:'Primary',per_ordered:0},add_custom_button:()=>assert.fail('write button exposed')});
});
test('Carton creation navigates to Packing Slip using endpoint name response',async()=>{
 const {handlers,dialogs,calls,routes}=setup();let click;
 handlers['Delivery Note'].refresh({is_new:()=>false,is_dirty:()=>false,
 doc:{name:'Test DN',docstatus:0,items:[{name:'Test DN Row',item_code:'Test Item',qty:10}]},add_custom_button:(label,fn)=>click=fn});
 click();await dialogs[0].primary_action({case_no:1,items:[{dn_detail:'Test DN Row',qty:4,item_code:'Test Item'}]});
 assert.equal(calls[0].args.delivery_note_name,'Test DN');assert.deepEqual(routes[0],['Form','Packing Slip','Test Document']);
});
test('Secondary summary starts with a native child source row',()=>{
 const {handlers,context}=setup();let click,created;
 context.frappe.new_doc=(doctype,values,initialize)=>{created={doctype,...values};initialize(created)};
 context.frappe.model.add_child=(doc,doctype,field)=>{const row={doctype,name:'new-test-row'};doc[field].push(row);return row};
 handlers['YRP Retail Order'].refresh({is_new:()=>false,is_dirty:()=>false,
 doc:{name:'Test Secondary',order_type:'Secondary',customer:'Test Customer',sales_person:'Test Person',order_date:'2026-09-22'},add_custom_button:(label,fn)=>click=fn});
 click();assert.equal(created.order_type,'Secondary');assert.equal(created.source_orders[0].doctype,'YRP Retail Order Summary Source');assert.equal(created.source_orders[0].order,'Test Secondary');
});
test('Carton saves mark cached delivery progress stale without deleting unsaved edits',()=>{
 const {handlers,context}=setup();
 const doc={name:'Test DN'};context.locals['Delivery Note']={'Test DN':doc};
 const frm={doc:{delivery_note:'Test DN'}};
 handlers['Packing Slip'].after_save(frm);assert.equal(doc.__needs_refresh,true);
 doc.__unsaved=1;doc.note='Local edit';handlers['Packing Slip'].after_save(frm);
 assert.equal(doc.note,'Local edit');assert.equal(context.locals['Delivery Note']['Test DN'],doc);
});
