// Native form event tests with fictional tree responses, no site data.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../../yrp/doctype/yrp_item_master_template/yrp_item_master_template.js'), 'utf8');
function fixture(call) {
	let event;
	const context = {frappe: {ui: {form: {on(dt, handlers) {if (handlers.item_type) event = handlers.item_type;}}}, call}};
	vm.runInNewContext(source, context);
	const frm = {doc: {item_type: 'fictional-cloth'}, rows: [{category: 'old-season', value: 'old-value'}],
		clear_table() {this.rows=[];}, add_child(table, row) {this.rows.push({...row});}, refresh_field() {}};
	return {event, frm};
}
test('prefills categories without choosing a season for the user', async () => {
	const {event, frm} = fixture(async () => ({message: [{name:'season'}, {name:'fabric'}]}));
	await event(frm);
	assert.deepEqual(frm.rows, [{category:'season'}, {category:'fabric'}]);
});
test('late response cannot replace a newly selected item type', async () => {
	let resolve;
	const {event, frm} = fixture(() => new Promise(r => resolve=r));
	const pending = event(frm);
	frm.doc.item_type = 'fictional-mobile';
	frm.rows = [{category:'storage'}];
	resolve({message:[{name:'season'}]});
	await pending;
	assert.deepEqual(frm.rows,[{category:'storage'}]);
});
test('clearing the item type clears old classification rows', async () => {
	const {event, frm} = fixture(() => {throw Error('No request expected');});
	frm.doc.item_type = null;
	await event(frm);
	assert.deepEqual(frm.rows,[]);
});
