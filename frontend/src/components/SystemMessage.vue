<script setup lang="ts">
import { cn } from "@/lib/utils";
import { CircleCheck, CircleX, Info, TriangleAlert } from "lucide-vue-next";
import type { HTMLAttributes } from "vue";
import type { FunctionalComponent } from "vue";

// ---- Props ----

interface SystemMessageProps {
	class?: HTMLAttributes["class"];
	content: string;
	type?: "info" | "warning" | "success" | "error";
}

const props = withDefaults(defineProps<SystemMessageProps>(), {
	type: "info",
});

/** 图标与语义色只落在图标上，正文保持 muted（边界通知，不是对话内容） */
const typeMeta: Record<
	NonNullable<SystemMessageProps["type"]>,
	{ icon: FunctionalComponent; iconClass: string }
> = {
	info: { icon: Info, iconClass: "text-primary" },
	warning: { icon: TriangleAlert, iconClass: "text-amber-500" },
	success: { icon: CircleCheck, iconClass: "text-emerald-500" },
	error: { icon: CircleX, iconClass: "text-red-500" },
};
</script>

<template>
  <div class="flex justify-center my-1.5">
    <div :class="cn('inline-flex max-w-full items-center gap-1.5 px-1 text-xs text-muted-foreground', props.class)"
      :title="props.content">
      <component :is="typeMeta[props.type].icon" :class="cn('h-3.5 w-3.5 shrink-0', typeMeta[props.type].iconClass)" />
      <span class="truncate">{{ props.content }}</span>
    </div>
  </div>
</template>
