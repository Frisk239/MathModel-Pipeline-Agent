<script setup lang="ts">
import type { AgentType } from "@/utils/enum";
import { renderMarkdown } from "@/utils/markdown";
import { agentMetaOf } from "@/utils/agentMeta";
import type { HTMLAttributes } from "vue";
import { computed, ref, watch } from "vue";

// ---- Props ----

interface BubbleProps {
	type: "user" | "agent";
	agentType?: AgentType;
	class?: HTMLAttributes["class"];
	content: string;
	/** 消息时间（HH:mm），Agent 角色行展示 */
	time?: string;
}

const props = withDefaults(defineProps<BubbleProps>(), {
	type: "user",
});

// ---- Render ----

/** 富文本渲染（KaTeX 公式 / 相对图片 / GFM 表格），renderMarkdown 为异步 */
const renderedContent = ref("");
watch(
	() => props.content,
	async (content) => {
		if (!content) {
			renderedContent.value = "";
			return;
		}
		renderedContent.value = await renderMarkdown(content);
	},
	{ immediate: true },
);

// ---- Agent 元信息 ----

const agentMeta = computed(() => agentMetaOf(props.agentType));
</script>

<template>
  <div :class="[
    'bubble',
    props.type === 'user' ? 'bubble-user' : '',
    props.type === 'agent' ? 'bubble-agent' : '',
    props.class
  ]">
    <!-- 用户消息：右对齐主色底 -->
    <div v-if="props.type === 'user'" class="flex flex-col items-end gap-1">
      <div class="prose prose-sm max-w-[80%] rounded-2xl bg-primary px-4 py-2 text-sm text-primary-foreground shadow-sm"
        v-html="renderedContent"></div>
    </div>
    <!-- Agent 消息：角色行 + 内容卡 -->
    <div v-else class="flex w-full flex-col gap-1">
      <div class="flex items-center gap-1.5 select-none">
        <component :is="agentMeta.icon" class="h-3.5 w-3.5 text-muted-foreground" />
        <span class="text-xs font-medium text-muted-foreground">{{ agentMeta.label }}</span>
        <span v-if="props.time" class="text-[10px] tabular-nums text-muted-foreground/50">{{ props.time }}</span>
      </div>
      <div
        class="prose prose-sm prose-slate max-w-none rounded-xl border border-black/5 bg-muted/60 px-3.5 py-2.5 text-sm shadow-sm dark:border-white/10"
        v-html="renderedContent"></div>
    </div>
  </div>
</template>

<style>
.bubble {
	display: flex;
	flex: 1 1 0%;
}

.bubble-user {
	justify-content: flex-end;
}

/* ---- 富文本排版（用户气泡内白字覆盖） ---- */

.prose {
	@apply text-inherit;
}

.prose p {
	@apply my-1;
}

.prose p:first-child {
	@apply mt-0;
}

.prose p:last-child {
	@apply mb-0;
}

.prose h1,
.prose h2,
.prose h3,
.prose h4 {
	@apply my-1.5 font-semibold;
}

.prose h1 {
	@apply text-lg;
}

.prose h2 {
	@apply text-base;
}

.prose h3,
.prose h4 {
	@apply text-sm;
}

.prose ul,
.prose ol {
	@apply my-1 pl-4;
}

.prose ul {
	@apply list-disc;
}

.prose ol {
	@apply list-decimal;
}

.prose li {
	@apply my-0.5;
}

.prose code {
	@apply rounded bg-black/[0.07] px-1 py-0.5 font-mono text-[0.85em] dark:bg-white/10;
}

/* 代码块：GitHub Dark 风格深色块，与正文形成对比 */
.prose pre {
	@apply my-1.5 overflow-x-auto rounded-lg p-3;
	background: #0f172a;
	color: #e2e8f0;
	max-width: 100%;
	width: 100%;
	font-size: 0.8rem;
	line-height: 1.55;
}

.prose pre code {
	@apply bg-transparent p-0;
	color: inherit;
	white-space: pre-wrap;
	word-break: break-word;
}

.prose blockquote {
	@apply my-1 border-l-2 border-current pl-3 italic opacity-75;
}

.prose a {
	@apply underline underline-offset-2 opacity-80 hover:opacity-100;
}

.prose img {
	@apply my-1 rounded-lg;
}

.prose hr {
	@apply my-2 border-current opacity-10;
}

.prose table {
	@apply my-1.5 w-full border-collapse text-xs;
}

.prose th,
.prose td {
	@apply border-b border-black/10 px-2 py-1 text-left dark:border-white/15;
}

.prose th {
	@apply bg-black/[0.04] font-semibold dark:bg-white/[0.06];
}

/* 数学公式 */
.prose .math-block {
	@apply my-1.5 overflow-x-auto text-center;
}

/* 用户气泡内反色 */
.bubble-user .prose {
	color: #fff;
}

.bubble-user .prose a {
	color: #fff;
	text-decoration-color: rgb(255 255 255 / 0.6);
}

.bubble-user .prose code {
	background: rgb(255 255 255 / 0.18);
}

/* 流式光标（StreamingBubble 复用全局 prose 样式） */
.streaming-cursor {
	position: absolute;
	right: 0.5rem;
	bottom: 0.5rem;
	display: inline-block;
	width: 2px;
	height: 1em;
	background: hsl(var(--foreground) / 0.7);
	animation: cursor-blink 1s step-end infinite;
}

@keyframes cursor-blink {
	50% {
		opacity: 0;
	}
}
</style>
