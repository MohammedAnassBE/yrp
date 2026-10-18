// Scope protection to app-owned attribute controls; keep immediate native input.
export function protectAttributeAutocomplete(control) {
	if (!control.df?.get_query) return control
	const pending = new Map()
	let sequence = 0
	const queryArguments = (term) => {
		const query = control.df.get_query((control.frm && control.frm.doc) || control.doc, control.doctype, control.docname)
		return { txt: term, query: query.query, ...query.params }
	}
	control.execute_query_if_exists = (term) => {
		const args = queryArguments(term)
		const key = JSON.stringify([frappe.session?.user, args])
		const current = ++sequence
		if (pending.has(key)) {
			const entry = pending.get(key)
			entry.sequence = current
			return entry.promise
		}
		const entry = { sequence: current }
		const request = Promise.resolve(frappe.call({ method: args.query, args })).then(({ message }) => {
			if (entry.sequence === sequence && key === JSON.stringify([frappe.session?.user, args]) && control.$input.is(":focus") && control.$input.val() === term
				&& JSON.stringify(queryArguments(term)) === JSON.stringify(args)) control.set_data(message)
		}, () => {}).finally(() => pending.delete(key))
		entry.promise = request
		pending.set(key, entry)
		return request
	}
	return control
}
