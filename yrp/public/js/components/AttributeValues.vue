<template>
    <div class="attribute-value-template frappe-control">
        <table class="table table-sm table-bordered" v-if="attr_values">
            <tr>
                <th>S.No</th>
                <th>Attribute Value</th>
            </tr>
            <tr v-for="(attr, index) in attr_values" :key="attr">
                <td>{{ index + 1 }}</td>
                <td>{{ attr.attribute_value }}</td>
            </tr>
        </table>
        <p v-else>No available values for {{ attr_name }}</p>
    </div>
</template>

<script setup>
// Used in Item Attribute to list all the values of an attribute
import { ref } from 'vue'
const attr_values = ref(getAttrValues())
const attr_name = ref(cur_frm.doc.attribute_name);

function getAttrValues() {
    if(cur_frm.doc.__onload.attr_values && cur_frm.doc.__onload.attr_values.length != 0) {
        return cur_frm.doc.__onload["attr_values"];
    } else {
        return null;
    }
}
</script>
