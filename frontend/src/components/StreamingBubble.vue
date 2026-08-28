<script setup lang="ts">
import { agentMetaOf } from "@/utils/agentMeta";
import type { AgentType } from "@/utils/enum";
import { renderMarkdown } from "@/utils/markdown";
import { Brain, ChevronDown, LoaderCircle } from "lucide-vue-next";
import { computed, ref, watch } from "vue";

// 流式过程气泡：思维链折叠行（运行中滚动显示最后一行 + shimmer 扫光）
// 与正文打字机。形态参考 deepseek-harness 的 ReasoningRow：默认收起、
// 思考体轻量纯文本；正文用与终稿一致的富文本渲染。

const props = defineProps<{
	agentType: AgentType;
	thinking: string;
	text: string;
}>();

const expanded = ref(false);
const thinkBodyRef = ref<HTMLPreElement | null>(null);
const summaryRef = ref<HTMLDivElement | null>(null);

const lastLine = computed(() => {
	const content = props.thinking.trim();
	if (!content) return "";
	const lines = content.split("\n").filter(Boolean);
	return lines[lines.length - 1] ?? "";
});

/** 正文富文本渲染：增量冻结解析——已完成块缓存 HTML，仅重解析尾部块。
 *  流式高频 delta 下避免全量 markdown 重解析；块边界只在 fenced code
 *  闭合处生效（``` 计数为偶），防止把代码块从中间劈开冻结成怪 HTML。 */
const FROZEN_TAIL_BLOCKS = 2;

function countFences(s: string): number {
	return (s.match(/^\s*(?:```|~~~)/gm) ?? []).length;
}

function splitBlocks(text: string): string[] {
	const parts = text.split(/\n\n+/);
	const blocks: string[] = [];
	let pending: string | null = null;
	let fences = 0;
	for (const part of parts) {
		const c = countFences(part);
		if (pending !== null && fences % 2 !== 0) {
			// 上一段落在未闭合代码块内：边界无效，续接到同一块
			pending = `${pending}\n\n${part}`;
			fences += c;
			continue;
		}
		if (pending !== null) blocks.push(pending);
		pending = part;
		fences = c;
	}
	if (pending !== null) blocks.push(pending);
	return blocks;
}

const frozenHtml = ref("");
const frozenBlocks = ref(0);
const tailHtml = ref("");

const renderedText = computed(() => frozenHtml.value + tailHtml.value);

watch(
	() => props.text,
	async (text) => {
		if (!text) {
			frozenHtml.value = "";
			frozenBlocks.value = 0;
			tailHtml.value = "";
			return;
		}
		const blocks = splitBlocks(text);
		const freezable = Math.max(0, blocks.length - FROZEN_TAIL_BLOCKS);
		if (freezable > frozenBlocks.value) {
			const newly = blocks.slice(frozenBlocks.value, freezable).join("\n\n");
			frozenHtml.value += await renderMarkdown(newly);
			frozenBlocks.value = freezable;
		}
		const tail = blocks.slice(frozenBlocks.value).join("\n\n");
		tailHtml.value = tail ? await renderMarkdown(tail) : "";
	},
	{ immediate: true },
);

const agentMeta = computed(() => agentMetaOf(props.agentType));

watch(
	() => [props.thinking, props.text],
	() => {
		requestAnimationFrame(() => {
			if (summaryRef.value) {
				summaryRef.value.scrollLeft = summaryRef.value.scrollWidth;
			}
			const body = thinkBodyRef.value;
			if (expanded.value && body) {
				// 仅当用户本来就停在展开体底部时才跟随增量；
				// 用户上滑回看思考过程时不拉回
				const atBottom =
					body.scrollHeight - body.scrollTop - body.clientHeight <= 40;
				if (atBottom) body.scrollTop = body.scrollHeight;
			}
		});
	},
);
</script>

<template>
  <div class="mb-3 flex w-full flex-col gap-1">
    <!-- 头像行 -->
    <div class="flex items-center gap-1.5 select-none">
      <component :is="agentMeta.icon" class="h-3.5 w-3.5 text-muted-foreground" />
      <span class="text-xs font-medium text-muted-foreground">{{ agentMeta.label }}</span>
    </div>

    <!-- 思维链折叠行 -->
    <div v-if="props.thinking"
      class="overflow-hidden rounded-xl border border-black/5 bg-muted/40 text-xs dark:border-white/10">
      <button type="button"
        class="streaming-think-header flex w-full items-center gap-1.5 px-2.5 py-1.5 text-left"
        @click="expanded = !expanded">
        <Brain class="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
        <span class="shrink-0 font-medium text-muted-foreground">思考中</span>
        <div ref="summaryRef" class="min-w-0 flex-1 overflow-hidden">
          <span class="block truncate text-muted-foreground/80">{{ lastLine }}</span>
        </div>
        <ChevronDown class="h-3.5 w-3.5 shrink-0 text-muted-foreground transition-transform"
          :class="{ 'rotate-180': expanded }" />
      </button>
      <pre v-show="expanded" ref="thinkBodyRef"
        class="max-h-64 overflow-y-auto whitespace-pre-wrap break-words px-3 pb-2 font-sans text-muted-foreground">{{ props.thinking }}</pre>
    </div>

    <!-- 正文打字机（markdown 富文本渲染，光标右下角） -->
    <div v-if="props.text"
      class="relative max-w-none rounded-xl border border-black/5 bg-muted/60 px-3.5 py-2.5 text-sm shadow-sm dark:border-white/10">
      <div class="prose prose-sm prose-slate max-w-none" v-html="renderedText"></div>
      <span class="streaming-cursor"></span>
    </div>

    <!-- 还没有任何增量：等待模型首 token -->
    <div v-if="!props.text && !props.thinking" class="flex items-center gap-1.5 py-1 text-xs text-muted-foreground">
      <LoaderCircle class="h-3 w-3 animate-spin" />
      <span>{{ agentMeta.label }}正在思考…</span>
    </div>
  </div>
</template>

<style scoped>
/* 运行中折叠行的 shimmer 扫光（prefers-reduced-motion 时关闭） */
.streaming-think-header {
	position: relative;
	overflow: hidden;
}

.streaming-think-header::after {
	content: "";
	position: absolute;
	inset: 0;
	background: linear-gradient(
		100deg,
		transparent 30%,
		hsl(var(--muted-foreground) / 0.08) 50%,
		transparent 70%
	);
	transform: translateX(-100%);
	animation: think-shimmer 2.6s infinite;
	pointer-events: none;
}

@keyframes think-shimmer {
	to {
		transform: translateX(100%);
	}
}

@media (prefers-reduced-motion: reduce) {
	.streaming-think-header::after {
		animation: none;
	}
}
</style>
