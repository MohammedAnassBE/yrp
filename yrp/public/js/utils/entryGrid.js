import { computed, h, nextTick, ref, toValue } from "vue"

const CONTROL_SELECTOR = [
	"input:not([type=hidden]):not(:disabled):not([readonly]):not([data-row-select])",
	"textarea:not(:disabled):not([readonly])",
	"[role=combobox]:not(input):not([aria-disabled=true])",
	"input[role=combobox][data-pc-section=hiddeninput]:not(:disabled)",
].join(",")
const state = new WeakMap()
const wait = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds))

/** Next cell in reading order, or "add" from the last editable cell when a non-blank row may grow the table. */
export function enterTarget(position, bounds, isEditable, { canAdd = false, rowBlank = true } = {}) {
	for (let index = position.row * bounds.cols + position.col + 1; index < bounds.rows * bounds.cols; index += 1) {
		const cell = { row: Math.floor(index / bounds.cols), col: index % bounds.cols }
		if (isEditable(cell.row, cell.col)) return { action: "move", cell }
	}
	return canAdd && !rowBlank ? { action: "add" } : { action: "none" }
}

function rowControls(row) {
	return [...row.querySelectorAll(CONTROL_SELECTOR)].filter((control) => control.closest("tr") === row && control.getClientRects().length)
}

function bodyRows(table) {
	return [...(table?.tBodies?.[0]?.rows || [])].filter((row) => rowControls(row).length || row.cells.length > 1)
}

function controlValueBlank(control) {
	if (control.type === "checkbox" || control.type === "radio") return true
	if ("value" in control && control.tagName !== "SPAN") return !String(control.value ?? "").trim() || control.inputMode === "decimal" && Number(control.value) === 0
	return Boolean(control.querySelector?.(".p-placeholder")) || control.classList.contains("p-placeholder")
}

/** A DOM row is blank when every text control is empty; zero numbers and checks do not count as data. */
export function isDomRowBlank(controls) {
	const text = controls.filter((control) => control.type !== "checkbox" && control.inputMode !== "decimal" && control.inputMode !== "numeric")
	return (text.length ? text : controls).every(controlValueBlank)
}

function focusControl(table, rowIndex, colIndex) {
	const control = rowControls(bodyRows(table)[rowIndex] || document.createElement("tr"))[colIndex]
	control?.focus()
	control?.select?.()
	return Boolean(control)
}

async function focusNewRow(table, rowCount) {
	for (let attempt = 0; attempt < 5; attempt += 1) {
		await nextTick()
		await new Promise((resolve) => requestAnimationFrame(resolve))
		const rows = bodyRows(table)
		if (rows.length > rowCount && focusControl(table, rows.length - 1, 0)) return
	}
}

function tableOf(element) {
	return element.tagName === "TABLE" ? element : element.querySelector("table")
}

function locate(table, target) {
	const rows = bodyRows(table)
	const controls = rows.map(rowControls)
	const row = rows.findIndex((candidate) => candidate.contains(target))
	const col = row < 0 ? -1 : controls[row].findIndex((control) => control === target || control.contains(target))
	return { rows, controls, row, col }
}

async function overlayClosed(target) {
	for (let attempt = 0; attempt < 15; attempt += 1) {
		if (target.getAttribute("aria-expanded") !== "true") return true
		await wait(20)
	}
	return false
}

async function advance(element, target, wasExpanded) {
	await nextTick()
	await wait(0)
	if (wasExpanded && !(await overlayClosed(target))) return
	const options = state.get(element)?.options || {}
	const table = tableOf(element)
	const { rows, controls, row, col } = locate(table, target)
	if (row < 0 || col < 0) return
	const bounds = { rows: rows.length, cols: Math.max(...controls.map((cells) => cells.length)) }
	const canAdd = typeof options.add === "function" && options.canAdd !== false
	const rowBlank = options.isRowBlank ? options.isRowBlank(row) : isDomRowBlank(controls[row])
	const next = enterTarget({ row, col }, bounds, (r, c) => Boolean(controls[r]?.[c]), { canAdd, rowBlank })
	if (next.action === "move") focusControl(table, next.cell.row, next.cell.col)
	if (next.action === "add") {
		await options.add()
		await focusNewRow(table, rows.length)
	}
}

function onKeydown(event) {
	if (event.key !== "Enter" || event.shiftKey || event.ctrlKey || event.metaKey || event.altKey || event.isComposing) return
	const target = event.target
	if (target.tagName === "TEXTAREA" || target.tagName === "BUTTON" || !target.closest?.("tbody tr")) return
	if (!target.matches(CONTROL_SELECTOR) && !target.closest("[role=combobox]")) return
	const wasExpanded = target.getAttribute("aria-expanded") === "true"
	if (!wasExpanded && target.tagName === "INPUT" && target.type !== "checkbox") event.preventDefault()
	advance(event.currentTarget, target, wasExpanded)
}

function freezeWidths(element) {
	const table = tableOf(element)
	const headers = [...(table?.tHead?.rows?.[0]?.cells || [])]
	const current = state.get(element)
	const signature = `${headers.length}:${headers.map((cell) => cell.textContent.trim()).join("|")}`
	if (!current || !headers.length || current.signature === signature || !table.tBodies[0]?.rows.length || !table.offsetWidth) return
	table.style.tableLayout = "auto"
	table.style.width = ""
	for (const cell of headers) cell.style.width = ""
	const widths = headers.map((cell) => Math.ceil(cell.getBoundingClientRect().width))
	headers.forEach((cell, index) => { cell.style.width = `${widths[index]}px` })
	table.style.tableLayout = "fixed"
	table.style.width = `${widths.reduce((sum, width) => sum + width, 0)}px`
	table.classList.add("entry-table--fixed")
	current.signature = signature
}

/**
 * v-entry-table on an editable table (or its wrapper): Enter moves to the next input and adds a row from the last
 * input of the last row; widths are measured once and fixed so focus or validation never shifts columns.
 * Value: { add?: () => void, canAdd?: boolean, isRowBlank?: (rowIndex) => boolean, fixed?: boolean }.
 */
export const vEntryTable = {
	mounted(element, binding) {
		state.set(element, { options: binding.value || {}, signature: "" })
		element.addEventListener("keydown", onKeydown, true)
		if (binding.value?.fixed !== false) requestAnimationFrame(() => freezeWidths(element))
	},
	updated(element, binding) {
		state.get(element).options = binding.value || {}
		if (binding.value?.fixed !== false) requestAnimationFrame(() => freezeWidths(element))
	},
	beforeUnmount(element) {
		element.removeEventListener("keydown", onKeydown, true)
		state.delete(element)
	},
}

/** Selected keys ordered last row first, so removing one never shifts the index-based keys still to remove. */
export function removalOrder(keys, selected) {
	const chosen = new Set(selected)
	return keys.filter((key) => chosen.has(key)).reverse()
}

/** Desk-style row checkboxes: `selectableKeys` lists the rows that may be removed, in display order. */
export function useRowSelection(selectableKeys) {
	const picked = ref([])
	const keys = computed(() => toValue(selectableKeys) || [])
	const selected = computed(() => {
		const allowed = new Set(keys.value)
		return picked.value.filter((key) => allowed.has(key))
	})
	const isAllSelected = computed(() => keys.value.length > 0 && selected.value.length === keys.value.length)
	const isPartlySelected = computed(() => selected.value.length > 0 && !isAllSelected.value)

	function isSelected(key) {
		return selected.value.includes(key)
	}

	function toggle(key, on = !isSelected(key)) {
		const rest = selected.value.filter((candidate) => candidate !== key)
		picked.value = on && keys.value.includes(key) ? [...rest, key] : rest
	}

	function toggleAll(on = !isAllSelected.value) {
		picked.value = on ? [...keys.value] : []
	}

	/** Removes each selected row through `remove(key)`, stopping once `canRemove()` turns false. */
	async function removeSelected(remove, canRemove = () => true) {
		const order = removalOrder(keys.value, selected.value)
		picked.value = []
		for (const key of order) {
			if (!canRemove()) break
			await remove(key)
			await nextTick()
		}
	}

	return { selected, isAllSelected, isPartlySelected, isSelected, toggle, toggleAll, removeSelected }
}

/** Frappe grid footer shown while rows are selected: "N selected" and a red Delete button. */
export const SelectionFooter = {
	name: "SelectionFooter",
	props: { count: { type: Number, default: 0 } },
	emits: ["delete"],
	setup(props, { emit }) {
		return () => props.count ? h("div", { class: "grid-footer d-flex justify-content-end align-items-center small", style: "gap: 10px; padding: 6px 10px" }, [
			h("span", { class: "text-muted" }, `${props.count} ${__("selected")}`),
			h("button", { type: "button", class: "btn btn-xs btn-danger", onClick: () => emit("delete") }, __("Delete")),
		]) : null
	},
}

/** Enter in the last input of `element` calls `onSubmit`, for "fill a form row, then Add" layouts. Returns a cleanup. */
export function enterSubmits(element, onSubmit) {
	function onKeydown(event) {
		if (event.key !== "Enter" || event.shiftKey || event.ctrlKey || event.metaKey || event.altKey || event.isComposing) return
		const controls = [...element.querySelectorAll(CONTROL_SELECTOR)].filter((control) => control.tagName !== "TEXTAREA" && control.getClientRects().length)
		const last = controls.at(-1)
		if (!last || !(last === event.target || last.contains(event.target)) || event.target.getAttribute("aria-expanded") === "true") return
		event.preventDefault()
		onSubmit()
	}
	element.addEventListener("keydown", onKeydown, true)
	return () => element.removeEventListener("keydown", onKeydown, true)
}

const submitters = new WeakMap()

/** v-enter-submits="add": directive form of enterSubmits. */
export const vEnterSubmits = {
	mounted(element, binding) {
		submitters.set(element, { cleanup: enterSubmits(element, () => submitters.get(element).submit()), submit: binding.value })
	},
	updated(element, binding) {
		submitters.get(element).submit = binding.value
	},
	beforeUnmount(element) {
		submitters.get(element)?.cleanup()
		submitters.delete(element)
	},
}
