<template>
	<div ref="root">
		<div v-if="supplier" class="mb-3">
			<input
				v-model="searchQuery"
				type="text"
				class="form-control"
				placeholder="Search delivery person..."
				@input="queueSearch"
			>
		</div>

		<div v-if="supplier && suggestedDeliveryPersons.length" class="card shadow-sm border mb-4">
			<div class="card-header bg-light font-weight-bold">
				Delivery Persons
			</div>
			<div class="card-body p-3">
				<div
					v-for="person in suggestedDeliveryPersons"
					:key="person.delivery_mob_no"
					class="delivery-person-item border-bottom pb-3 d-flex justify-content-between align-items-center"
				>
					<div>
						<div><strong>Name:</strong> {{ person.delivery_person }}</div>
						<div class="text-muted"><small>Mobile:</small> {{ person.delivery_mob_no }}</div>
					</div>
					<button
						class="btn btn-sm btn-outline-primary"
						type="button"
						@click="setCurrentFormDeliveryPerson(person.delivery_mob_no)"
					>
						Apply
					</button>
				</div>
			</div>
		</div>
		<div v-else-if="supplier && searchQuery" class="card shadow-sm border mb-4">
			<div class="card-header bg-light font-weight-bold">
				Delivery Persons
			</div>
			<div class="card-body p-3 text-muted">
				No delivery persons found for "{{ searchQuery }}".
			</div>
		</div>
		<div v-else-if="supplier" class="card shadow-sm border mb-4">
			<div class="card-header bg-light font-weight-bold">
				Delivery Persons
			</div>
			<div class="card-body p-3 text-muted">
				Start typing to search for delivery persons or create a new one.
			</div>
		</div>

		<div v-if="supplier" class="text-end mt-2">
			<button class="btn btn-sm btn-secondary" type="button" @click="createNewDeliveryPerson">
				<i class="fa fa-plus me-1"></i> Create New Delivery Person
			</button>
		</div>
	</div>
</template>

<script setup>
import { ref } from "vue";

const suggestedDeliveryPersons = ref([]);
const supplier = ref(null);
const searchQuery = ref("");
let searchTimer = null;

function fetchSuggestedDeliveryPersons(query = "") {
	frappe.call({
		method: "yrp.yrp.doctype.vendor_bill_delivery_person.vendor_bill_delivery_person.get_last_ten_delivery_persons",
		args: {
			supplier: supplier.value,
			search: query,
		},
		callback: (r) => {
			suggestedDeliveryPersons.value = r.message || [];
		},
	});
}

function setCurrentFormDeliveryPerson(mobileNo) {
	cur_frm.set_value("delivery_mob_no", mobileNo);
}

function createNewDeliveryPerson() {
	frappe.ui.form.make_quick_entry("Vendor Bill Delivery Person", {
		callback: (doc) => {
			cur_frm.set_value("delivery_mob_no", doc.mobile_no || doc.delivery_mob_no || doc.name);
			fetchSuggestedDeliveryPersons();
		},
	});
}

function update_for_new_supplier(newSupplier) {
	supplier.value = newSupplier;
	fetchSuggestedDeliveryPersons();
}

function queueSearch() {
	window.clearTimeout(searchTimer);
	searchTimer = window.setTimeout(() => {
		fetchSuggestedDeliveryPersons(searchQuery.value);
	}, 300);
}

defineExpose({ update_for_new_supplier });
</script>

<style scoped>
.card-header {
	font-size: 1rem;
	font-weight: 600;
}

.delivery-person-item:last-child {
	border-bottom: none !important;
	padding-bottom: 0 !important;
}

.delivery-person-item {
	transition: background-color 0.2s ease-in-out;
}

.delivery-person-item:hover {
	background-color: #f8f9fa;
}
</style>
