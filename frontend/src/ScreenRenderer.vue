<!-- @yrp/web-engine — ScreenRenderer (spec §7.1).

     Renders one entry of config.screens as a 6-track CSS grid on desktop
     (full = span 6, half = span 3, third = span 2; mobile: everything spans
     full). Blocks resolve against the compile-time registry; unknown types
     render the honest UnknownBlock card and every block sits inside a
     BlockBoundary so one broken block never takes down the screen. -->
<script setup>
import { computed } from "vue"
import { resolveBlock } from "./registry.js"
import BlockBoundary from "./BlockBoundary.vue"
import UnknownBlock from "./UnknownBlock.vue"

const props = defineProps({
	screen: { type: Object, default: null },
})

// Hide-by-block-id: dict of booleans, same semantics as nav.hidden (§2.1).
const visibleBlocks = computed(() => {
	const screen = props.screen || {}
	const hidden = screen.hidden || {}
	return (screen.blocks || []).filter((b) => b && b.id && hidden[b.id] !== true)
})

function spanClass(size) {
	if (size === "half") return "yrp-span-half"
	if (size === "third") return "yrp-span-third"
	return "yrp-span-full"
}
</script>

<template>
	<div class="yrp-screen-grid">
		<BlockBoundary
			v-for="b in visibleBlocks"
			:key="b.id"
			:block-id="b.id"
			:class="spanClass(b.size)"
		>
			<component
				v-if="resolveBlock(b.type)"
				:is="resolveBlock(b.type).component"
				v-bind="b.props || {}"
			/>
			<UnknownBlock v-else :type="b.type" />
		</BlockBoundary>
	</div>
</template>

<style scoped>
/* Spacing/radius come from the host's existing tokens (--space-*, --radius),
   with plain-pixel fallbacks so the engine renders in any host. */
.yrp-screen-grid {
	display: grid;
	grid-template-columns: repeat(6, 1fr);
	gap: var(--space-4, 16px);
	align-items: start;
}
.yrp-span-full {
	grid-column: span 6;
}
.yrp-span-half {
	grid-column: span 3;
}
.yrp-span-third {
	grid-column: span 2;
}
@media (max-width: 768px) {
	.yrp-span-full,
	.yrp-span-half,
	.yrp-span-third {
		grid-column: span 6;
	}
}
</style>
