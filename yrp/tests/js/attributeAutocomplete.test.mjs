import assert from 'node:assert/strict'
import test from 'node:test'
import fs from 'node:fs'
const path = new URL('../../public/js/utils/attributeAutocomplete.js', import.meta.url)
const source = fs.readFileSync(path, 'utf8')
const { protectAttributeAutocomplete } = await import('data:text/javascript;base64,' + Buffer.from(source).toString('base64'))
const deferred = () => { let resolve; const promise = new Promise(r => resolve=r); return {promise,resolve} }

test('per-control requests stay immediate, share pending duplicates and reject stale input/context', async () => {
 const pending=[]; globalThis.frappe={session:{user:'one'},call: args=>{const d=deferred();pending.push({...d,args});return d.promise}}
 let term='', item='Item', focus=true
 const control={df:{get_query:()=>({query:'search',params:{item,attribute:'Colour'}})},$input:{val:()=>term,is:()=>focus},set_data: data=>{control.data=data}}
 protectAttributeAutocomplete(control)
 const a=control.execute_query_if_exists('');const b=control.execute_query_if_exists('')
 assert.equal(pending.length,1)
 pending[0].resolve({message:['initial']});await Promise.all([a,b]);assert.deepEqual(control.data,['initial'])
 term='B';const c=control.execute_query_if_exists(term)
 term='Bl';const d=control.execute_query_if_exists(term)
 assert.equal(pending.length,3)
 pending[2].resolve({message:['Black']});await d
 pending[1].resolve({message:['Blue']});await c;assert.deepEqual(control.data,['Black'])
 const e=control.execute_query_if_exists(term);item='Other';pending[3].resolve({message:['wrong item']});await e;assert.deepEqual(control.data,['Black'])
 const f=control.execute_query_if_exists(term);focus=false;pending[4].resolve({message:['blurred']});await f;assert.deepEqual(control.data,['Black'])
})

test('failure does not poison retry and local controls are unchanged', async () => {
 globalThis.frappe={call:async()=>{throw new Error('offline')}}
 const local={};assert.equal(protectAttributeAutocomplete(local),local)
 const c={df:{get_query:()=>({query:'search',params:{}})},$input:{val:()=>'',is:()=>true},set_data:data=>{c.data=data}}
 protectAttributeAutocomplete(c);await c.execute_query_if_exists('')
 globalThis.frappe.call=async()=>({message:['retry']});await c.execute_query_if_exists('');assert.deepEqual(c.data,['retry'])
})

test('returning to a pending earlier term displays its options for the latest intent', async () => {
 const pending=[]; globalThis.frappe={session:{user:'one'},call:()=>{const d=deferred();pending.push(d);return d.promise}}
 let term='A'
 const c={df:{get_query:()=>({query:'search',params:{}})},$input:{val:()=>term,is:()=>true},set_data:data=>{c.data=data}}
 protectAttributeAutocomplete(c)
 const a=c.execute_query_if_exists('A');term='B';const b=c.execute_query_if_exists('B');term='A';const again=c.execute_query_if_exists('A')
 assert.equal(pending.length,2)
 pending[0].resolve({message:['Alpha']});pending[1].resolve({message:['Beta']});await Promise.all([a,b,again])
 assert.deepEqual(c.data,['Alpha'])
 term='C';const switched=c.execute_query_if_exists('C');frappe.session.user='two';pending[2].resolve({message:['old user']});await switched
 assert.deepEqual(c.data,['Alpha'])
})
