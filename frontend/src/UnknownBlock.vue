<!-- @yrp/web-engine — UnknownBlock (spec §7.1, §14 row 9).

     A compact muted card for a block type the registry doesn't know —
     visible-but-honest, so a layout fixtured ahead of its block deployment
     doesn't silently drop content. The type string is shown only to managers
     (the person who can fix it); everyone gets the console.warn. The page
     never crashes on a typo'd layout. -->
<script setup>
import { onMounted } from "vue"
import { getContext } from "./context.js"

const props = defineProps({
	type: { type: String, default: "" },
})

const showType = getContext().isManager()

onMounted(() => {
	console.warn(
		`[yrp-web] unknown block type "${props.type}" — is the frontend build older than the layout?`
	)
})
</script>

<template>
	<div class="yrp-unknown-block">
		<span class="yrp-unknown-block__text">This widget isn't available in this version.</span>
		<code v-if="showType" class="yrp-unknown-block__type"
			>unknown type "{{ type }}" — is the frontend build older than the layout?</code
		>
	</div>
</template>

<style scoped>
.yrp-unknown-block {
	display: flex;
	flex-direction: column;
	gap: var(--space-1, 4px);
	padding: var(--space-4, 16px);
	border: 1px dashed var(--esd-line, #dfe8e4);
	border-radius: var(--radius, 12px);
	background: var(--esd-card, #ffffff);
	color: var(--esd-muted, #5f6e68);
	font-size: 13px;
}
.yrp-unknown-block__type {
	font-size: 12px;
	opacity: 0.8;
}
</style>
