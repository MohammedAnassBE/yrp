<template>
    <div class="inspection-editor">
        <div v-if="!sources.length" class="ie-empty">
            Select a submitted Goods Received Note in <b>Against ID</b> above to load items.
        </div>

        <div v-else>
            <!-- TOP: one block per group (parent_item + warehouse + dims + non-primary attrs) -->
            <div class="ie-section-header">Source Items</div>

            <div
                v-for="(group, gIdx) in groups"
                :key="`grp-${gIdx}`"
                class="ie-group"
            >
                <div class="ie-group-header">
                    <div class="ie-group-label">{{ group.group_label }}</div>
                    <div class="ie-group-sub">
                        {{ group.warehouse }}
                        <span v-if="group.primary_attribute" class="ie-group-pa">
                            · primary attr: <b>{{ group.primary_attribute }}</b>
                        </span>
                    </div>
                </div>

                <table class="ie-pivot-table">
                    <thead>
                        <tr>
                            <th class="ie-col-sno">S.No.</th>
                            <th class="ie-col-rt">Received Type</th>
                            <th
                                v-for="pv in group.primary_attribute_values"
                                :key="pv"
                                class="ie-col-num ie-col-pv"
                            >{{ pv }}</th>
                            <th class="ie-col-num">Total</th>
                            <th class="ie-col-num">Remaining</th>
                            <th class="ie-col-actions"></th>
                        </tr>
                    </thead>
                    <tbody>
                        <tr
                            v-for="(row, rIdx) in group.rows"
                            :key="`grp-${gIdx}-row-${rIdx}`"
                            :class="rowClass(row)"
                        >
                            <td class="ie-col-sno">{{ rIdx + 1 }}</td>
                            <td>
                                <span class="ie-pill ie-pill-src">
                                    {{ row.source_received_type || '—' }}
                                </span>
                            </td>
                            <td
                                v-for="pv in group.primary_attribute_values"
                                :key="`cell-${pv}`"
                                class="ie-col-num"
                                :class="cellClass(row.cells[pv])"
                            >
                                <span v-if="row.cells[pv]">
                                    {{ fmt(remaining(row.cells[pv])) }}
                                </span>
                                <span v-else class="ie-cell-empty">—</span>
                            </td>
                            <td class="ie-col-num"><b>{{ fmt(rowSourceTotal(row)) }}</b></td>
                            <td class="ie-col-num">
                                <b :class="rowRemainingClass(row)">{{ fmt(rowRemaining(row)) }}</b>
                            </td>
                            <td class="ie-col-actions">
                                <button
                                    class="btn btn-xs btn-default ie-btn-edit"
                                    type="button"
                                    :disabled="locked || !hasAnyRemaining(row)"
                                    @click="openEdit(gIdx, rIdx)"
                                >Edit</button>
                            </td>
                        </tr>
                    </tbody>
                </table>
            </div>

            <!-- BOTTOM: GRN-style grouped pivot of explicit splits -->
            <div
                class="ie-section-header ie-bottom-header"
                v-if="hasBottomRows || !locked"
            >Splits ({{ bottomCount }})</div>

            <div
                v-for="(group, gIdx) in groupsForBottom"
                :key="`btmgrp-${group.key}`"
                class="ie-group"
            >
                <div class="ie-group-header">
                    <div class="ie-group-label">{{ group.group_label }}</div>
                    <div class="ie-group-sub">{{ group.warehouse }}</div>
                </div>
                <table class="ie-pivot-table">
                    <thead>
                        <tr>
                            <th class="ie-col-sno">S.No.</th>
                            <th class="ie-col-rt">Source → Target</th>
                            <th
                                v-for="pv in group.primary_attribute_values"
                                :key="`bth-${pv}`"
                                class="ie-col-num ie-col-pv"
                            >{{ pv }}</th>
                            <th class="ie-col-num">Total</th>
                            <th class="ie-col-comments">Comments</th>
                            <th class="ie-col-actions"></th>
                        </tr>
                    </thead>
                    <tbody>
                        <tr
                            v-for="(row, rIdx) in group.rows"
                            :key="`btm-${gIdx}-${rIdx}`"
                        >
                            <td class="ie-col-sno">{{ rIdx + 1 }}</td>
                            <td class="ie-rt-pair">
                                <span class="ie-pill ie-pill-src">{{ row.source_received_type }}</span>
                                <span class="ie-arrow">→</span>
                                <span class="ie-pill ie-pill-tgt">{{ row.target_received_type }}</span>
                            </td>
                            <td
                                v-for="pv in group.primary_attribute_values"
                                :key="`btmcell-${pv}`"
                                class="ie-col-num"
                            >
                                <span v-if="row.cells[pv]">
                                    {{ fmt(row.cells[pv].qty) }}
                                </span>
                                <span v-else class="ie-cell-empty">—</span>
                            </td>
                            <td class="ie-col-num"><b>{{ fmt(bottomRowTotal(row)) }}</b></td>
                            <td class="ie-col-comments">{{ bottomRowComments(row) || '—' }}</td>
                            <td class="ie-col-actions">
                                <button
                                    class="ie-btn-remove"
                                    type="button"
                                    :disabled="locked"
                                    title="Move qty back to top"
                                    @click="deleteBottomRow(group, row)"
                                >✕</button>
                            </td>
                        </tr>
                    </tbody>
                </table>
            </div>

            <div v-if="!hasBottomRows && !locked" class="ie-bottom-empty">
                No splits yet. Click <b>Edit</b> on a row above to split into target receipt types.
            </div>
        </div>

        <!-- MODAL: per-row multi-target / multi-size split -->
        <teleport to="body" v-if="modalCtx">
            <div class="ie-modal-backdrop" @click.self="cancelEdit">
                <div class="ie-modal" role="dialog">
                    <div class="ie-modal-header">
                        <div>
                            <div class="ie-modal-title">
                                Split: {{ modalCtx.group_label }}
                            </div>
                            <div class="ie-modal-sub">
                                {{ modalCtx.warehouse }} ·
                                Source RT:
                                <span class="ie-pill ie-pill-src">{{ modalCtx.source_received_type }}</span>
                            </div>
                        </div>
                        <button
                            class="ie-modal-close"
                            type="button"
                            @click="cancelEdit"
                            aria-label="Close"
                        >✕</button>
                    </div>
                    <div class="ie-modal-body">
                        <!-- Source row summary -->
                        <table class="ie-modal-source">
                            <thead>
                                <tr>
                                    <th class="ie-col-rt">Source RT</th>
                                    <th
                                        v-for="pv in modalCtx.primary_attribute_values"
                                        :key="`src-${pv}`"
                                        class="ie-col-num"
                                    >{{ pv }}</th>
                                </tr>
                            </thead>
                            <tbody>
                                <tr>
                                    <td>
                                        <b>Available</b>
                                    </td>
                                    <td
                                        v-for="pv in modalCtx.primary_attribute_values"
                                        :key="`avl-${pv}`"
                                        class="ie-col-num"
                                        :class="availableCellClass(pv)"
                                    >
                                        <span v-if="modalCtx.sourceCells[pv]">{{ fmt(availableBySize(pv)) }}</span>
                                        <span v-else class="ie-cell-empty">—</span>
                                    </td>
                                </tr>
                            </tbody>
                        </table>

                        <!-- Drafts: one row per (target RT + per-size qty + date + comments) -->
                        <div class="ie-modal-sub-header">Add splits</div>
                        <table class="ie-modal-drafts">
                            <thead>
                                <tr>
                                    <th class="ie-col-rt">Target RT</th>
                                    <th
                                        v-for="pv in modalCtx.primary_attribute_values"
                                        :key="`hd-${pv}`"
                                        class="ie-col-num"
                                    >{{ pv }}</th>
                                    <th class="ie-col-num">Total</th>
                                    <th>Comments</th>
                                    <th class="ie-col-actions"></th>
                                </tr>
                            </thead>
                            <tbody>
                                <tr v-for="(d, dIdx) in drafts" :key="`d-${dIdx}`">
                                    <td>
                                        <select v-model="d.target_received_type">
                                            <option value="" disabled>Select target…</option>
                                            <option
                                                v-for="rt in receivedTypes"
                                                :key="rt"
                                                :value="rt"
                                                :disabled="rt === modalCtx.source_received_type"
                                            >
                                                {{ rt === modalCtx.source_received_type ? `${rt} (= source)` : rt }}
                                            </option>
                                        </select>
                                    </td>
                                    <td
                                        v-for="pv in modalCtx.primary_attribute_values"
                                        :key="`d-${dIdx}-${pv}`"
                                        class="ie-col-num"
                                    >
                                        <input
                                            v-if="modalCtx.sourceCells[pv]"
                                            type="number"
                                            step="0.001"
                                            min="0"
                                            v-model.number="d.cells[pv]"
                                            class="ie-cell-input"
                                            :class="{ 'is-over': overByDraftCell(dIdx, pv) }"
                                        >
                                        <span v-else class="ie-cell-empty">—</span>
                                    </td>
                                    <td class="ie-col-num"><b>{{ fmt(draftTotal(d)) }}</b></td>
                                    <td>
                                        <input type="text" v-model="d.comments">
                                    </td>
                                    <td class="ie-col-actions">
                                        <button
                                            class="ie-btn-remove"
                                            type="button"
                                            :disabled="drafts.length === 1"
                                            @click="removeDraft(dIdx)"
                                        >✕</button>
                                    </td>
                                </tr>
                            </tbody>
                            <tfoot>
                                <tr class="ie-totals-row">
                                    <td><b>Sum being added</b></td>
                                    <td
                                        v-for="pv in modalCtx.primary_attribute_values"
                                        :key="`sum-${pv}`"
                                        class="ie-col-num"
                                        :class="{ 'ie-over': draftsSumBySize(pv) > availableBySize(pv) + 0.0005 }"
                                    >
                                        <b>{{ fmt(draftsSumBySize(pv)) }}</b>
                                        <span class="ie-of-avail">/{{ fmt(availableBySize(pv)) }}</span>
                                    </td>
                                    <td class="ie-col-num"><b>{{ fmt(draftsGrandTotal) }}</b></td>
                                    <td colspan="2"></td>
                                </tr>
                            </tfoot>
                        </table>

                        <button
                            class="btn btn-xs btn-default ie-modal-add"
                            type="button"
                            @click="addDraft"
                        >+ Add Another Target</button>

                        <div v-if="modalError" class="ie-modal-error">{{ modalError }}</div>
                    </div>
                    <div class="ie-modal-footer">
                        <button
                            class="btn btn-default"
                            type="button"
                            @click="cancelEdit"
                        >Cancel</button>
                        <button
                            class="btn btn-primary"
                            type="button"
                            :disabled="!canSubmitModal"
                            @click="submitEdit"
                        >Submit</button>
                    </div>
                </div>
            </div>
        </teleport>
    </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue';

const sources = ref([]);
const docstatus = ref(
    typeof cur_frm !== 'undefined' && cur_frm.doc ? cur_frm.doc.docstatus : 0
);
const receivedTypes = ref([]);

// Modal state — null when closed; { groupIdx, rowIdx, ...context, drafts } when open.
const modalCtx = ref(null);
const drafts = ref([]);

const locked = computed(() => docstatus.value !== 0);

// =====================================================================
// Helpers
// =====================================================================
function fmt(n) {
    const v = Number(n || 0);
    if (Math.abs(v) < 0.0005) return '0';
    return Number.isInteger(v) ? String(v) : v.toFixed(3).replace(/\.?0+$/, '');
}
function remaining(src) {
    if (!src) return 0;
    const sum = (src.explicit_splits || []).reduce(
        (s, r) => s + Number(r.qty || 0), 0
    );
    return Number((Number(src.grn_qty || 0) - sum).toFixed(6));
}
function defaultDate() {
    if (typeof cur_frm !== 'undefined' && cur_frm.doc && cur_frm.doc.posting_date) {
        return cur_frm.doc.posting_date;
    }
    return new Date().toISOString().slice(0, 10);
}

// =====================================================================
// Grouping — derive blocks from sources via display_meta
// =====================================================================
const groups = computed(() => {
    const byKey = new Map();
    const order = [];
    for (const s of sources.value) {
        const meta = s.display_meta || {};
        // Fallback when an item has no parent (no display_meta) — group per source
        const groupKey = meta.group_key || `__solo__${s.item_variant}|${s.warehouse}`;
        if (!byKey.has(groupKey)) {
            byKey.set(groupKey, {
                key: groupKey,
                group_label: meta.group_label
                    || `${s.item_variant}`,
                warehouse: s.warehouse,
                primary_attribute: meta.primary_attribute || '',
                primary_attribute_values: (meta.primary_attribute_values && meta.primary_attribute_values.length)
                    ? [...meta.primary_attribute_values]
                    : ['default'],
                rows: new Map(), // source_received_type -> { source_received_type, cells: {pv: src} }
            });
            order.push(groupKey);
        }
        const grp = byKey.get(groupKey);
        const rt = s.source_received_type || '—';
        if (!grp.rows.has(rt)) {
            grp.rows.set(rt, { source_received_type: rt, cells: {} });
        }
        const pv = (meta.primary_attribute_value && meta.primary_attribute_value !== '')
            ? meta.primary_attribute_value
            : 'default';
        grp.rows.get(rt).cells[pv] = s;
    }
    return order.map(k => {
        const grp = byKey.get(k);
        return { ...grp, rows: Array.from(grp.rows.values()) };
    });
});

// =====================================================================
// Bottom table — GRN-style grouped pivot of explicit splits
// One block per group_key; rows per (source_RT → target_RT); columns by
// primary attribute value (S/M/L/XL); cells aggregate qty across all
// underlying explicit_splits matching that (block, src_rt, tgt_rt, pv).
// =====================================================================
const groupsForBottom = computed(() => {
    const byKey = new Map();
    const order = [];
    sources.value.forEach((s, sIdx) => {
        const splits = s.explicit_splits || [];
        if (!splits.length) return;
        const meta = s.display_meta || {};
        const groupKey = meta.group_key || `__solo__${s.item_variant}|${s.warehouse}`;
        if (!byKey.has(groupKey)) {
            byKey.set(groupKey, {
                key: groupKey,
                group_label: meta.group_label || `${s.item_variant}`,
                warehouse: s.warehouse,
                primary_attribute: meta.primary_attribute || '',
                primary_attribute_values: (meta.primary_attribute_values && meta.primary_attribute_values.length)
                    ? [...meta.primary_attribute_values]
                    : ['default'],
                rows: new Map(),
            });
            order.push(groupKey);
        }
        const grp = byKey.get(groupKey);
        const pv = (meta.primary_attribute_value && meta.primary_attribute_value !== '')
            ? meta.primary_attribute_value
            : 'default';
        splits.forEach((sp, splitIdx) => {
            const rowKey = `${s.source_received_type || '—'}|${sp.target_received_type}`;
            if (!grp.rows.has(rowKey)) {
                grp.rows.set(rowKey, {
                    source_received_type: s.source_received_type,
                    target_received_type: sp.target_received_type,
                    cells: {},
                    refs: [], // [{sourceIdx, splitIdx}] for delete back-references
                });
            }
            const row = grp.rows.get(rowKey);
            if (!row.cells[pv]) row.cells[pv] = { qty: 0, comments: [] };
            row.cells[pv].qty += Number(sp.qty || 0);
            if (sp.comments) row.cells[pv].comments.push(sp.comments);
            row.refs.push({ sourceIdx: sIdx, splitIdx });
        });
    });
    return order.map(k => {
        const grp = byKey.get(k);
        return { ...grp, rows: Array.from(grp.rows.values()) };
    });
});
const hasBottomRows = computed(() =>
    groupsForBottom.value.some(g => g.rows.length > 0)
);
const bottomCount = computed(() =>
    groupsForBottom.value.reduce((n, g) => n + g.rows.length, 0)
);
function bottomRowTotal(row) {
    return Object.values(row.cells).reduce((s, c) => s + Number(c.qty || 0), 0);
}
function bottomRowComments(row) {
    const seen = new Set();
    const out = [];
    for (const cell of Object.values(row.cells)) {
        for (const c of (cell.comments || [])) {
            if (c && !seen.has(c)) { seen.add(c); out.push(c); }
        }
    }
    return out.join(', ');
}
function deleteBottomRow(_group, row) {
    if (locked.value) return;
    // Collect refs by sourceIdx and splice descending so indices stay valid.
    const bySrc = new Map();
    for (const ref of row.refs) {
        if (!bySrc.has(ref.sourceIdx)) bySrc.set(ref.sourceIdx, []);
        bySrc.get(ref.sourceIdx).push(ref.splitIdx);
    }
    for (const [srcIdx, idxs] of bySrc) {
        idxs.sort((a, b) => b - a);
        const src = sources.value[srcIdx];
        for (const j of idxs) src.explicit_splits.splice(j, 1);
    }
    if (typeof cur_frm !== 'undefined') cur_frm.dirty();
}

// =====================================================================
// Row helpers (top)
// =====================================================================
function rowSourceTotal(row) {
    return Object.values(row.cells).reduce(
        (s, src) => s + Number(src.grn_qty || 0), 0
    );
}
function rowRemaining(row) {
    return Object.values(row.cells).reduce(
        (s, src) => s + remaining(src), 0
    );
}
function hasAnyRemaining(row) {
    return Object.values(row.cells).some(src => remaining(src) > 0.0005);
}
function rowClass(row) {
    const rem = rowRemaining(row);
    const tot = rowSourceTotal(row);
    if (rem < -0.0005) return 'ie-row-over';
    if (Math.abs(rem) < 0.0005) return 'ie-row-allocated';
    if (rem < tot - 0.0005) return 'ie-row-partial';
    return '';
}
function rowRemainingClass(row) {
    const rem = rowRemaining(row);
    const tot = rowSourceTotal(row);
    if (rem < -0.0005) return 'ie-rem-over';
    if (Math.abs(rem) < 0.0005) return 'ie-rem-zero';
    if (rem < tot - 0.0005) return 'ie-rem-partial';
    return 'ie-rem-full';
}
function cellClass(src) {
    if (!src) return '';
    const rem = remaining(src);
    if (rem < -0.0005) return 'ie-cell-over';
    if (Math.abs(rem) < 0.0005) return 'ie-cell-zero';
    if (rem < Number(src.grn_qty || 0) - 0.0005) return 'ie-cell-partial';
    return '';
}

// =====================================================================
// Modal logic
// =====================================================================
function openEdit(gIdx, rIdx) {
    if (locked.value) return;
    const grp = groups.value[gIdx];
    const row = grp.rows[rIdx];
    modalCtx.value = {
        groupIdx: gIdx,
        rowIdx: rIdx,
        group_label: grp.group_label,
        warehouse: grp.warehouse,
        source_received_type: row.source_received_type,
        primary_attribute: grp.primary_attribute,
        primary_attribute_values: grp.primary_attribute_values,
        sourceCells: row.cells, // pv -> source bin
    };
    drafts.value = [_makeBlankDraft()];
}
function cancelEdit() {
    modalCtx.value = null;
    drafts.value = [];
}
function _makeBlankDraft() {
    const cells = {};
    for (const pv of modalCtx.value.primary_attribute_values) {
        cells[pv] = 0;
    }
    // Pick first unused, non-source target.
    const used = new Set(drafts.value.map(d => d.target_received_type));
    used.add(modalCtx.value.source_received_type);
    const nextRT = receivedTypes.value.find(rt => !used.has(rt)) || '';
    return {
        target_received_type: nextRT,
        cells,
        comments: '',
    };
}
function addDraft() {
    drafts.value.push(_makeBlankDraft());
}
function removeDraft(idx) {
    if (drafts.value.length === 1) return;
    drafts.value.splice(idx, 1);
}
function availableBySize(pv) {
    const src = modalCtx.value.sourceCells[pv];
    return src ? remaining(src) : 0;
}
function draftTotal(d) {
    return Object.values(d.cells).reduce((s, v) => s + Number(v || 0), 0);
}
function draftsSumBySize(pv) {
    return drafts.value.reduce((s, d) => s + Number(d.cells[pv] || 0), 0);
}
const draftsGrandTotal = computed(() =>
    drafts.value.reduce((s, d) => s + draftTotal(d), 0)
);
function overByDraftCell(_dIdx, pv) {
    return draftsSumBySize(pv) > availableBySize(pv) + 0.0005;
}
function availableCellClass(pv) {
    const src = modalCtx.value.sourceCells[pv];
    if (!src) return '';
    const rem = remaining(src);
    if (rem < 0.0005) return 'ie-cell-zero';
    return '';
}
const modalError = computed(() => {
    if (!modalCtx.value) return null;
    for (const d of drafts.value) {
        if (!d.target_received_type) return 'Each split needs a target Received Type.';
        if (d.target_received_type === modalCtx.value.source_received_type) {
            return `Target cannot equal source (${modalCtx.value.source_received_type}). Source-side qty stays in the top row as Remaining.`;
        }
        if (draftTotal(d) <= 0) return 'Each split needs at least one cell with qty > 0.';
        for (const pv of modalCtx.value.primary_attribute_values) {
            const v = Number(d.cells[pv] || 0);
            if (v < 0) return `Qty cannot be negative (${pv}).`;
            if (!modalCtx.value.sourceCells[pv] && v > 0) {
                return `No source bin exists for ${pv} in this row; qty must be 0.`;
            }
        }
    }
    for (const pv of modalCtx.value.primary_attribute_values) {
        if (draftsSumBySize(pv) > availableBySize(pv) + 0.0005) {
            return `Total qty for ${pv} (${fmt(draftsSumBySize(pv))}) exceeds available (${fmt(availableBySize(pv))}).`;
        }
    }
    if (draftsGrandTotal.value <= 0) return null;
    return null;
});
const canSubmitModal = computed(() => !modalError.value && draftsGrandTotal.value > 0);

function submitEdit() {
    if (!canSubmitModal.value) return;
    for (const d of drafts.value) {
        for (const pv of modalCtx.value.primary_attribute_values) {
            const qty = Number(d.cells[pv] || 0);
            if (qty <= 0) continue;
            const src = modalCtx.value.sourceCells[pv];
            if (!src) continue;
            src.explicit_splits.push({
                target_received_type: d.target_received_type,
                qty,
                comments: d.comments || '',
            });
        }
    }
    cancelEdit();
    if (typeof cur_frm !== 'undefined') cur_frm.dirty();
}

// =====================================================================
// Received types dropdown (from server)
// =====================================================================
async function loadReceivedTypes() {
    try {
        const r = await frappe.call({
            method: 'yrp.yrp.doctype.inspection_entry.inspection_entry.get_received_types',
        });
        receivedTypes.value = r.message || [];
    } catch (_) {
        receivedTypes.value = [];
    }
}

// =====================================================================
// load_data / get_items — same JSON shape as Python expects
// =====================================================================
function load_data(payload) {
    const arr = Array.isArray(payload)
        ? JSON.parse(JSON.stringify(payload))
        : [];
    sources.value = arr.map(s => {
        const all = s.splits || [];
        const sourceRT = s.source_received_type;
        const explicit = [];
        for (const sp of all) {
            // target == source rows collapse into implicit "remaining" on the top cell
            if (sp.target_received_type === sourceRT) continue;
            explicit.push({
                target_received_type: sp.target_received_type,
                qty: Number(sp.qty || 0),
                comments: sp.comments || '',
            });
        }
        return {
            ...s,
            grn_qty: Number(s.grn_qty || 0),
            explicit_splits: explicit,
        };
    });
}

function get_items() {
    // Every displayed source row is persisted. The user's explicit splits go through
    // as-is; whatever qty is still unallocated for a bin gets a `target = source`
    // row (no-op from the SLE engine's point of view, but it keeps the IE a faithful
    // record of every GRN line it covers).
    // Per-row dates always come from the parent posting_date — see
    // docs/claude/conventions.md (2026-05-19, "Don't ask the user for per-row dates").
    const postingDate = defaultDate();
    return sources.value.map(s => {
        const splits = (s.explicit_splits || []).map(sp => ({
            target_received_type: sp.target_received_type,
            qty: Number(sp.qty),
            received_date: postingDate,
            comments: sp.comments || '',
        }));
        const rem = remaining(s);
        if (rem > 0.0005) {
            splits.push({
                target_received_type: s.source_received_type,
                qty: rem,
                received_date: postingDate,
                comments: '',
            });
        }
        // eslint-disable-next-line no-unused-vars
        const { explicit_splits, ...rest } = s;
        return { ...rest, splits };
    });
}

function update_status() {
    if (typeof cur_frm !== 'undefined' && cur_frm.doc) {
        docstatus.value = cur_frm.doc.docstatus;
    }
}

onMounted(() => {
    loadReceivedTypes();
});

defineExpose({ load_data, get_items, update_status });
</script>

<style scoped>
.inspection-editor {
    display: flex;
    flex-direction: column;
    gap: 8px;
}
.ie-empty {
    padding: 16px;
    text-align: center;
    color: #6b7280;
    background: #f9fafb;
    border: 1px dashed #d1d5db;
    border-radius: 6px;
}
.ie-section-header {
    font-size: 11px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    color: #4b5563;
    padding: 8px 2px 4px;
    border-bottom: 1px solid #e5e7eb;
}
.ie-bottom-header { margin-top: 16px; }

/* GROUP block */
.ie-group {
    background: #ffffff;
    border: 1px solid #e5e7eb;
    border-radius: 6px;
    overflow: hidden;
    margin-bottom: 10px;
}
.ie-group-header {
    padding: 8px 12px;
    background: #f9fafb;
    border-bottom: 1px solid #e5e7eb;
}
.ie-group-label {
    font-size: 13px;
    font-weight: 600;
    color: #111827;
}
.ie-group-sub {
    font-size: 11px;
    color: #6b7280;
    margin-top: 2px;
}
.ie-group-pa b { color: #4b5563; }

/* PIVOT table */
.ie-pivot-table {
    width: 100%;
    border-collapse: collapse;
    font-size: 12px;
    table-layout: fixed;
}
.ie-pivot-table th {
    background: #fafafa;
    color: #4b5563;
    font-weight: 600;
    text-transform: uppercase;
    font-size: 10px;
    letter-spacing: 0.04em;
    padding: 6px 8px;
    text-align: center;
    border-bottom: 1px solid #e5e7eb;
}
.ie-pivot-table td {
    padding: 8px;
    border-bottom: 1px solid #f3f4f6;
    color: #111827;
    vertical-align: middle;
    text-align: center;
}
.ie-pivot-table tr:last-child td { border-bottom: none; }
.ie-row-allocated td { background: #f0fdf4; }
.ie-row-partial td { background: #fffbeb; }
.ie-row-over td { background: #fef2f2; }

.ie-col-sno { width: 50px; }
.ie-col-rt { width: 160px; }
.ie-col-num {
    text-align: center;
    white-space: nowrap;
    font-variant-numeric: tabular-nums;
    width: 80px;
}
.ie-col-pv {
    font-weight: 600;
    color: #4b5563;
}
.ie-col-actions {
    width: 80px;
    text-align: center;
}
.ie-col-warehouse { width: 180px; }
.ie-col-date { width: 130px; }
.ie-col-item { font-weight: 600; text-align: left; }
.ie-item { font-weight: 600; text-align: left; }

/* cell states */
.ie-cell-zero { color: #16a34a; }
.ie-cell-partial { color: #d97706; }
.ie-cell-over { color: #dc2626; background: #fef2f2; }
.ie-cell-empty { color: #9ca3af; }

.ie-rem-zero { color: #16a34a; }
.ie-rem-partial { color: #d97706; }
.ie-rem-full { color: #4b5563; }
.ie-rem-over { color: #dc2626; }

/* pills */
.ie-pill {
    display: inline-block;
    padding: 2px 8px;
    border-radius: 10px;
    font-size: 11px;
    font-weight: 500;
    white-space: nowrap;
}
.ie-pill-src { background: #eef2ff; color: #3730a3; }
.ie-pill-tgt { background: #ecfdf5; color: #065f46; }
.ie-arrow { color: #9ca3af; margin: 0 6px; }
.ie-rt-pair { white-space: nowrap; }

/* buttons */
.ie-btn-edit {
    padding: 3px 12px;
    font-size: 11px;
}
.ie-btn-remove {
    background: transparent;
    border: 1px solid transparent;
    color: #9ca3af;
    cursor: pointer;
    font-size: 13px;
    padding: 3px 8px;
    border-radius: 4px;
    line-height: 1;
}
.ie-btn-remove:hover:not(:disabled) {
    color: #dc2626;
    background: #fef2f2;
    border-color: #fecaca;
}
.ie-btn-remove:disabled {
    cursor: not-allowed;
    opacity: 0.35;
}

/* BOTTOM table */
.ie-bottom-table {
    width: 100%;
    border-collapse: collapse;
    font-size: 12px;
    background: #ffffff;
    border: 1px solid #e5e7eb;
    border-radius: 6px;
    overflow: hidden;
}
.ie-bottom-table th {
    background: #f9fafb;
    color: #4b5563;
    font-weight: 600;
    text-transform: uppercase;
    font-size: 10px;
    letter-spacing: 0.04em;
    padding: 8px 10px;
    text-align: center;
    border-bottom: 1px solid #e5e7eb;
}
.ie-bottom-table td {
    padding: 8px 10px;
    border-bottom: 1px solid #f3f4f6;
    text-align: center;
    vertical-align: middle;
}
.ie-bottom-table tr:last-child td { border-bottom: none; }

.ie-bottom-empty {
    padding: 12px;
    background: #fafbfc;
    border: 1px dashed #e5e7eb;
    border-radius: 6px;
    color: #6b7280;
    font-size: 12px;
    text-align: center;
}
</style>

<style>
/* MODAL (unscoped — teleported to body) */
.ie-modal-backdrop {
    position: fixed;
    inset: 0;
    background: rgba(17, 24, 39, 0.48);
    z-index: 1050;
    display: flex;
    align-items: center;
    justify-content: center;
    padding: 24px;
}
.ie-modal {
    background: #ffffff;
    border-radius: 8px;
    box-shadow: 0 10px 40px rgba(0, 0, 0, 0.25);
    width: 100%;
    max-width: 1080px;
    max-height: calc(100vh - 48px);
    display: flex;
    flex-direction: column;
    overflow: hidden;
}
.ie-modal-header {
    padding: 14px 18px;
    background: #f9fafb;
    border-bottom: 1px solid #e5e7eb;
    display: flex;
    justify-content: space-between;
    align-items: flex-start;
    gap: 12px;
}
.ie-modal-title {
    font-size: 14px;
    font-weight: 600;
    color: #111827;
}
.ie-modal-sub {
    margin-top: 4px;
    font-size: 12px;
    color: #6b7280;
}
.ie-modal-close {
    background: transparent;
    border: none;
    font-size: 18px;
    color: #6b7280;
    cursor: pointer;
    line-height: 1;
    padding: 4px 8px;
    border-radius: 4px;
}
.ie-modal-close:hover { background: #f3f4f6; color: #111827; }
.ie-modal-body {
    padding: 14px 18px;
    overflow: auto;
    flex: 1;
}
.ie-modal-sub-header {
    margin-top: 14px;
    font-size: 11px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    color: #4b5563;
    margin-bottom: 6px;
}

.ie-modal-source,
.ie-modal-drafts {
    width: 100%;
    border-collapse: collapse;
    font-size: 12px;
}
.ie-modal-source th,
.ie-modal-drafts th {
    background: #f9fafb;
    color: #4b5563;
    font-weight: 600;
    text-transform: uppercase;
    font-size: 10px;
    letter-spacing: 0.04em;
    padding: 6px 8px;
    text-align: center;
    border-bottom: 1px solid #e5e7eb;
}
.ie-modal-source td,
.ie-modal-drafts td {
    padding: 6px 8px;
    border-bottom: 1px solid #f3f4f6;
    text-align: center;
    vertical-align: middle;
}
.ie-modal-source .ie-col-num,
.ie-modal-drafts .ie-col-num {
    text-align: center;
}
.ie-modal-source { background: #fefefe; }
.ie-modal-source tbody td { background: #fafbfc; font-size: 12px; }
.ie-modal-drafts tfoot td {
    background: #fafafa;
    border-top: 1px solid #e5e7eb;
    border-bottom: none;
}
.ie-totals-row .ie-over { background: #fee2e2; color: #b91c1c; }
.ie-of-avail { color: #9ca3af; margin-left: 2px; font-weight: 400; font-size: 10px; }

.ie-modal-drafts input,
.ie-modal-drafts select,
.ie-modal-source input,
.ie-modal-source select {
    width: 100%;
    padding: 5px 8px;
    border: 1px solid #d1d5db;
    border-radius: 4px;
    font-size: 12px;
    background: white;
}
.ie-cell-input {
    text-align: center;
    width: 60px !important;
}
.ie-cell-input.is-over {
    border-color: #fca5a5;
    background: #fef2f2;
}
.ie-modal-add {
    margin-top: 10px;
    font-size: 11px;
}
.ie-modal-error {
    margin-top: 10px;
    padding: 8px 10px;
    background: #fef2f2;
    border: 1px solid #fecaca;
    border-radius: 4px;
    font-size: 12px;
    color: #991b1b;
}
.ie-modal-footer {
    padding: 12px 18px;
    background: #f9fafb;
    border-top: 1px solid #e5e7eb;
    display: flex;
    justify-content: flex-end;
    gap: 8px;
}
</style>
