import assert from 'node:assert/strict';
import { setSplitComment, buildLogicalReceiptRows, allowedLotsForEntry, lotChoicesForEntry, canEditLotForEntry, itemsNeedLotEditor, unusedLotsForEntry } from './grnReceiptRows.mjs';

const lots = { 'PO-1': ['OWN-1', 'LINKED', 'SECOND'], 'PO-2': ['OWN-2', 'LINKED', 'SECOND'], 'PO-3': ['ONLY'], 'PO-4': [] }
const entry = { dimensions: { lot: 'OWN-1' }, values: { S: { ref_docname: 'PO-1' } } }
assert.deepEqual(allowedLotsForEntry(entry, lots), ['OWN-1', 'LINKED', 'SECOND'], 'C7/C8: own and linked lots, no other row or header lots')
assert.deepEqual(allowedLotsForEntry({ values: { S: { ref_docname: 'PO-1' }, M: { ref_docname: 'PO-2' } } }, lots), ['LINKED', 'SECOND'], 'matrix cells intersect their allowed sets')
assert.deepEqual(allowedLotsForEntry({ ref_docname: 'PO-1', values: { S: {}, M: { ref_docname: 'PO-2' } } }, lots), ['OWN-2', 'LINKED', 'SECOND'], 'referenced cells take precedence over the entry reference')
assert.deepEqual(allowedLotsForEntry({ ref_docname: 'PO-3' }, lots), ['ONLY'], 'entry reference is used without cells')
assert.deepEqual(allowedLotsForEntry({ values: { S: { ref_docname: 'PO-1', qty: 2 }, M: { qty: 0 } } }, lots), ['OWN-1', 'LINKED', 'SECOND'], 'padded cells without references are ignored')
assert.deepEqual(allowedLotsForEntry({ ref_docname: 'PO-3', values: { S: { qty: 0 }, M: { qty: 0 } } }, lots), ['ONLY'], 'only padded cells fall back to the entry reference')
assert.deepEqual(allowedLotsForEntry({ ref_docname: 'MISSING', values: { S: { qty: 0 } } }, lots), [], 'unknown fallback entry reference fails closed')
assert.deepEqual(allowedLotsForEntry({ values: { S: { ref_docname: 'PO-1' }, M: { ref_docname: 'MISSING' } } }, lots), [], 'unknown cell reference fails closed')
assert.deepEqual(allowedLotsForEntry({ values: { S: {} } }, lots), [], 'missing references fail closed')
assert.deepEqual(allowedLotsForEntry({ ref_docname: 'PO-4' }, lots), [], 'empty allowed list stays empty')
assert.deepEqual(allowedLotsForEntry(entry, {}), [], 'empty map fails closed')
const choices = [
 { label: 'OWN-1', value: 'OWN-1', disabled: false },
 { label: 'LINKED', value: 'LINKED', disabled: false },
 { label: 'SECOND', value: 'SECOND', disabled: false },
]
assert.deepEqual(lotChoicesForEntry(entry, lots, 'lot'), choices, 'valid current lot is not duplicated')
const invalid = { ...entry, dimensions: { lot: 'BAD' } }
assert.deepEqual(lotChoicesForEntry(invalid, lots, 'lot'), [...choices, { label: 'BAD', value: 'BAD', disabled: true }], 'invalid current lot is visible but disabled')
for (const current of ['', null, undefined]) {
 assert.deepEqual(lotChoicesForEntry({ ...entry, dimensions: { lot: current } }, lots, 'lot'), choices, 'blank current lot is a placeholder, never an option')
}
assert.deepEqual(lots['PO-1'], ['OWN-1', 'LINKED', 'SECOND'], 'helpers leave allowed options unchanged')
assert.deepEqual(allowedLotsForEntry({ ref_docname: 'constructor' }, lots), [], 'prototype names are unknown references')
const single = { ref_docname: 'PO-3', dimensions: { lot: 'ONLY' } }
const stale = { ...single, dimensions: { lot: 'UNLINKED' } }
assert.equal(canEditLotForEntry(entry, lots, 'lot'), true, 'multiple allowed lots are editable')
assert.equal(canEditLotForEntry(single, lots, 'lot'), false, 'valid single lot needs no selector')
assert.equal(canEditLotForEntry(stale, lots, 'lot'), true, 'stale single lot remains correctable')
assert.equal(canEditLotForEntry({ ...single, dimensions: {} }, lots, 'lot'), true, 'missing lot remains correctable')
assert.equal(canEditLotForEntry({ ref_docname: 'PO-4', dimensions: { lot: 'BAD' } }, lots, 'lot'), true, 'empty allowed set keeps stale value visible')
for (const current of ['', null, undefined]) {
 const blank = { ref_docname: 'PO-4', dimensions: { lot: current } }
 assert.equal(canEditLotForEntry(blank, lots, 'lot'), false, 'blank current lot with no allowed lots is not editable')
 assert.equal(canEditLotForEntry({ ...blank, ref_docname: 'PO-3' }, lots, 'lot'), true, 'blank current lot with one allowed lot is editable')
 assert.equal(canEditLotForEntry({ ...blank, ref_docname: 'PO-1' }, lots, 'lot'), true, 'blank current lot with multiple allowed lots is editable')
 assert.equal(itemsNeedLotEditor([{ items: [single, blank] }], lots, 'lot'), false, 'blank lot with no allowed lots does not enable the editor')
}
assert.equal(itemsNeedLotEditor([{ items: [{ values: { S: { qty: 0 } } }] }], {}, 'lot'), false, 'rows without lots or links do not need the editor')
assert.equal(itemsNeedLotEditor([{ items: [single] }], lots, 'lot'), false, 'valid single-lot items use the simple editor')
assert.equal(itemsNeedLotEditor([{ items: [single] }, { items: [stale] }], lots, 'lot'), true, 'loaded stale item enables the lot editor')
assert.equal(itemsNeedLotEditor([], lots, 'lot'), false, 'no loaded rows need a lot editor')
assert.equal(itemsNeedLotEditor([{ items: [stale] }], lots, ''), false, 'no lot dimension needs no lot editor')
assert.deepEqual(unusedLotsForEntry(entry, lots, new Set(['OWN-1'])), ['LINKED', 'SECOND'], 'Add lot uses only this row set')
assert.deepEqual(unusedLotsForEntry(entry, lots, new Set(['OWN-1', 'LINKED', 'SECOND'])), [], 'Add lot hides when the row set is exhausted')
assert.deepEqual(unusedLotsForEntry(entry, lots, new Set(['OTHER-ROW', 'BAD'])), ['OWN-1', 'LINKED', 'SECOND'], 'unrelated and invalid used lots do not consume allowed lots')
assert.deepEqual(unusedLotsForEntry({ ref_docname: 'MISSING' }, lots, new Set()), [], 'Add lot fails closed for unknown references')
const commentItems = [{ primary_attribute: 'Size', items: [
 { name: 'Vest', dimensions: { received_type: 'Accepted' }, attributes: {}, values: { S: { qty: 1 } } },
 { name: 'Vest', dimensions: { received_type: 'Accepted' }, attributes: {}, values: { S: { qty: 2 } } },
 { name: 'Vest', dimensions: { received_type: 'Adas' }, attributes: {}, values: { S: { qty: 1 } }, comments: 'Keep' },
] }]
const [commentRow] = buildLogicalReceiptRows(commentItems, { aggregatePhysicalRows: true })
setSplitComment(commentRow.splits[0], 'Pressed')
assert.deepEqual(commentItems[0].items.map((item) => item.comments), ['Pressed', 'Pressed', 'Keep'], 'a split comment reaches every saved row of that Received Type only')
assert.equal(commentRow.splits[0].entry.comments, 'Pressed', 'the shown split keeps the typed comment')
console.log('Desk GRN lot options and comments: 46 assertions passed')
