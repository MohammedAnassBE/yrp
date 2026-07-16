<!-- @yrp/web-engine — BlockBoundary (spec §7.1).

     onErrorCaptured returns false (stops propagation) and flips to an inline
     "Widget failed to load" card carrying the block id, console.error-ing the
     original error. One broken block never takes down the screen — the single
     most important renderer rule (§14 row 10). -->
<script setup>
import { onErrorCaptured, ref } from "vue"

const props = defineProps({
	blockId: { type: String, default: "" },
})

const failed = ref(false)

onErrorCaptured((err) => {
	console.error(`[yrp-web] block "${props.blockId}" failed to render:`, err)
	failed.value = true
	return false // stop propagation — siblings and the screen stay alive
})
</script>

<template>
	<div class="yrp-block-boundary">
		<div v-if="failed" class="yrp-block-error">
			<span class="yrp-block-error__text">Widget failed to load</span>
			<code class="yrp-block-error__id">{{ blockId }}</code>
		</div>
		<slot v-else />
	</div>
</template>

<style scoped>
/* Collapse EMPTY boundaries: a block whose root v-if renders nothing (e.g.
   home-queues/home-recent with no visible entries) leaves only a comment node
   inside the wrapper — without this, the wrapper still occupies a zero-height
   grid track in .yrp-screen-grid and the row gap doubles (18px + 0 + 18px).
   :has(*) is true only when an element child exists, so non-empty blocks (and
   the failed-state card) are untouched — rendering parity is preserved.
   Engine contract: a block must render an ELEMENT root or nothing — a block
   whose root renders bare text nodes would be wrongly collapsed by this rule.
   Browser support: Chromium 105+, Firefox 121+, Safari 15.4+ — all current. */
.yrp-block-boundary:not(:has(*)) {
	display: none;
}

.yrp-block-error {
	display: flex;
	align-items: center;
	gap: var(--space-2, 8px);
	padding: var(--space-4, 16px);
	border: 1px dashed var(--esd-line, #dfe8e4);
	border-radius: var(--radius, 12px);
	background: var(--esd-card, #ffffff);
	color: var(--esd-muted, #5f6e68);
	font-size: 13px;
}
.yrp-block-error__id {
	font-size: 12px;
	opacity: 0.8;
}
</style>
