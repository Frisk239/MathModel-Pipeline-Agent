<script setup lang="ts">
import { ScrollArea } from "@/components/ui/scroll-area";
import { renderMarkdown } from "@/utils/markdown";
import type { WriterMessage } from "@/utils/response";
import { computed, ref, watch } from "vue";

// ---- Types ----

/** 内容段落数据结构 */
interface ContentSection {
	id: number;
	content: string;
	renderedContent: string;
	sub_title?: string;
}

// ---- Props ----

const props = defineProps<{
	messages: WriterMessage[];
	writerSequence: string[];
}>();

// ---- Reactive State ----

const sections = ref<ContentSection[]>([]);
let nextId = 0;

// ---- Methods ----

/** 添加新的内容段落 */
const appendContent = async (content: string, sub_title?: string) => {
	const renderedContent = await renderMarkdown(content);
	sections.value.push({
		id: nextId++,
		content,
		renderedContent,
		sub_title,
	});
};

// ---- Computed ----

/** 根据 writerSequence 排序内容 */
const sortedSections = computed(() => {
	if (!props.writerSequence.length) return sections.value;

	return [...sections.value].sort((a, b) => {
		const aIndex = a.sub_title
			? props.writerSequence.indexOf(a.sub_title)
			: Number.POSITIVE_INFINITY;
		const bIndex = b.sub_title
			? props.writerSequence.indexOf(b.sub_title)
			: Number.POSITIVE_INFINITY;

		if (
			aIndex === Number.POSITIVE_INFINITY &&
			bIndex === Number.POSITIVE_INFINITY
		)
			return 0;
		if (aIndex === Number.POSITIVE_INFINITY) return 1;
		if (bIndex === Number.POSITIVE_INFINITY) return -1;

		return aIndex - bIndex;
	});
});

// ---- Watch ----

/** 监听消息变化，重新渲染内容 */
watch(
	() => props.messages,
	async (messages) => {
		// 清空现有内容
		sections.value = [];
		nextId = 0;

		// 按顺序添加每个消息的内容
		for (const msg of messages) {
			if (msg.content) {
				await appendContent(msg.content, msg.sub_title);
			}
		}
	},
	{ immediate: true },
);
</script>

<template>
  <div class="h-full flex flex-col p-4">
    <div class="h-full bg-card rounded-lg border shadow-sm">
      <div class="border-b px-4 py-3">
        <h2 class="text-lg font-semibold text-foreground">论文内容</h2>
      </div>
      <div class="h-full pb-14">
        <ScrollArea class="h-full overflow-y-auto">
          <div class="p-6">
            <div class="max-w-4xl mx-auto overflow-y-auto">
              <TransitionGroup name="section" tag="div">
                <div v-for="section in sortedSections" :key="section.id"
                  class="border-b border-black/5 pb-6 mb-6 last:border-0 dark:border-white/10">
                  <div class="prose prose-doc max-w-none" v-html="section.renderedContent"></div>
                </div>
              </TransitionGroup>
            </div>
          </div>
        </ScrollArea>
      </div>
    </div>
  </div>
</template>

<style>
/* 论文排版在全局 .prose（Bubble.vue 统一定义）基础上放大标题层级。
   基础规则（p/ul/table/code/...）禁止在此重复定义——历史上双全局
   .prose 互相污染全站样式。KaTeX CSS 已上移 main.ts。 */
.prose.prose-doc h1 {
  @apply text-2xl font-bold mb-3;
}

.prose.prose-doc h2 {
  @apply text-xl font-semibold my-2.5;
}

.prose.prose-doc h3 {
  @apply text-lg font-semibold my-2;
}

.prose.prose-doc p {
  @apply my-2.5 leading-relaxed;
}

.prose.prose-doc .katex {
  font-size: 1.05em;
}

.section-enter-active,
.section-leave-active {
  transition: opacity 0.2s ease;
}

.section-enter-from,
.section-leave-to {
  opacity: 0;
}
</style>