<script setup lang="ts">
import NotebookCell from "@/components/NotebookCell.vue";
import { useStickyScroll } from "@/composables/useStickyScroll";
import { useTaskStore } from "@/stores/task";
import type { CodeCell, NoteCell, ResultCell } from "@/utils/interface";
import { LoaderCircle, SquareCode } from "lucide-vue-next";
import { computed, ref } from "vue";

// ---- Reactive State ----

const taskStore = useTaskStore();
const scrollRef = ref<HTMLDivElement | null>(null);

// ---- Computed ----

/** 将代码执行消息转换为 Notebook 单元格列表 */
const cells = computed<NoteCell[]>(() => {
	const notebookCells: NoteCell[] = [];

	// 获取代码执行工具消息，按顺序处理
	for (const toolMsg of taskStore.interpreterMessage) {
		console.log("Code execute message:", toolMsg);

		// 处理代码输入消息
		if (toolMsg.input?.code) {
			const codeCell: CodeCell = {
				type: "code",
				content: toolMsg.input.code,
			};
			notebookCells.push(codeCell);
		}

		// 处理执行结果消息
		if (toolMsg.output && toolMsg.output.length > 0) {
			const resultCell: ResultCell = {
				type: "result",
				code_results: toolMsg.output,
			};
			notebookCells.push(resultCell);
		}
	}

	return notebookCells;
});

// ---- Scroll ----

const { onScroll } = useStickyScroll(scrollRef, () => cells.value);
</script>

<template>
  <div ref="scrollRef" class="notebook-scroll flex-1 px-1 pt-1 pb-4 h-full overflow-y-auto" @scroll="onScroll">
    <!-- 遍历所有单元格 -->
    <div v-for="(cell, index) in cells" :key="index" :class="cell.type === 'code' ? 'pt-2' : 'pt-0'">
      <NotebookCell :cell="cell" />
    </div>

    <!-- 末尾代码单元尚无结果回传且任务运行中：执行中指示 -->
    <div v-if="cells.length > 0 && cells[cells.length - 1].type === 'code' && taskStore.isRunning"
      class="mx-3 mb-2 flex items-center gap-1.5 rounded-md border bg-muted/40 px-2.5 py-1.5 text-xs text-muted-foreground">
      <LoaderCircle class="h-3 w-3 animate-spin" />
      正在执行代码…
    </div>

    <!-- 无内容时的提示 -->
    <div v-if="cells.length === 0" class="flex items-center justify-center h-full">
      <div class="text-muted-foreground/70 text-center p-8">
        <SquareCode class="mx-auto mb-2 h-8 w-8 opacity-60" />
        <div class="text-sm font-medium">暂无代码执行结果</div>
        <div class="text-xs mt-1">执行代码后将在此显示结果</div>
      </div>
    </div>
    <!-- 添加底部空间 -->
    <div class="h-4"></div>
  </div>
</template>

<style>
/* 滚动条样式限定本容器，避免污染全站 */
.notebook-scroll::-webkit-scrollbar {
  width: 0.375rem;
  height: 0.375rem;
}

.notebook-scroll::-webkit-scrollbar-track {
  background-color: rgb(243 244 246 / 0.6);
  border-radius: 9999px;
}

.notebook-scroll::-webkit-scrollbar-thumb {
  background-color: rgb(209 213 219);
  border-radius: 9999px;
}

.notebook-scroll::-webkit-scrollbar-thumb:hover {
  background-color: rgb(156 163 175);
  transition-property: background-color;
  transition-duration: 200ms;
}

</style>
