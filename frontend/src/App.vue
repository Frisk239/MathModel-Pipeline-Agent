<script setup lang="ts">
import ApprovalDialog from "@/components/ApprovalDialog.vue";
import Toaster from "@/components/ui/toast/Toaster.vue";
import { ref, watch } from "vue";
import { useRoute } from "vue-router";

// 全局审批弹窗：任务详情页/会话页存在活跃任务时轮询挂起审批
const route = useRoute();
const activeTaskId = ref<string | null>(
	(localStorage.getItem("currentTaskId") as string | null) ?? null,
);

watch(
	() => route.params.task_id,
	(v) => {
		if (typeof v === "string" && v) activeTaskId.value = v;
	},
	{ immediate: true },
);
</script>

<template>
  <Toaster />
  <router-view />
  <ApprovalDialog :task-id="activeTaskId" />
</template>
