import assert from "node:assert/strict"
import test from "node:test"

import { groupItemsForDisplay } from "../src/stock/groupItemsForDisplay.js"

const group = (items) => [{
	attributes: ["Dia", "Colour"],
	primary_attribute: "",
	primary_attribute_values: [],
	items,
}]

const item = (name, attributes, qty, reference, extra = {}) => ({
	name,
	attributes,
	dimensions: {},
	primary_attribute: "",
	default_uom: "Kg",
	fabric_reference_variant: reference,
	values: {
		default: {
			qty,
			pending_quantity: qty,
			cost: 2,
			...extra,
		},
	},
})

test("groups route-split rows without mutating the source", () => {
	const source = group([
		item("Cloth", { Dia: "36 Dia", Colour: "Greige" }, 41, "Cloth-36-Grey"),
		item("Cloth", { Dia: "36 Dia", Colour: "Greige" }, 41, "Cloth-36-Red"),
		item("Cloth", { Dia: "34 Dia", Colour: "Greige" }, 68, "Cloth-34-Red"),
	])

	const result = groupItemsForDisplay(source)

	assert.equal(result[0].items.length, 2)
	assert.deepEqual(result[0].items[0].values.default, {
		qty: 82,
		pending_quantity: 82,
		cost: 2,
	})
	assert.equal(result[0].items[0].fabric_reference_variant, undefined)
	assert.equal(source[0].items.length, 3)
	assert.equal(source[0].items[0].fabric_reference_variant, "Cloth-36-Grey")
})

test("does not merge rows with different unit costs", () => {
	const source = group([
		item("Cloth", { Dia: "36 Dia", Colour: "Greige" }, 41, "Cloth-36-Grey"),
		item(
			"Cloth",
			{ Dia: "36 Dia", Colour: "Greige" },
			41,
			"Cloth-36-Red",
			{ cost: 3 },
		),
	])

	assert.equal(groupItemsForDisplay(source)[0].items.length, 2)
})

test("accepts reactive-style Proxy rows", () => {
	const proxied = new Proxy(
		item("Yarn", {}, 10, "Cloth-36-Grey"),
		{},
	)
	const source = group([
		proxied,
		item("Yarn", {}, 15, "Cloth-36-Red"),
	])

	assert.equal(groupItemsForDisplay(source)[0].items[0].values.default.qty, 25)
})
