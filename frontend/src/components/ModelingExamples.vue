<script setup lang="ts">
import { Button } from "@/components/ui/button";
import { ref } from "vue";
import { useRouter } from "vue-router";

import { exampleAPI } from "@/apis/commonApi";
import mcmCupC from "@/assets/example/2024高教杯C题.png";
import wuyiCupC from "@/assets/example/2025五一杯C题.png";
import huashuCupC from "@/assets/example/华数杯2023年C题.png";

interface ModelingExample {
	id: number;
	title: string;
	source: string;
	description: string;
	tags: string[];
	problemText: string;
	image: string;
}

const router = useRouter();

const examples = ref<ModelingExample[]>([
	{
		id: 1,
		title: "母亲身心健康对婴儿成长的影响",
		source: "2023华数杯C题",
		description: "研究母亲身心健康对婴儿成长的影响",
		tags: ["分类问题", "成长", "健康"],
		problemText: "给定母亲身心健康数据，建立一个预测模型，预测婴儿成长情况。",
		image: huashuCupC,
	},
	{
		id: 2,
		title: "社交媒体平台用户分析问题",
		source: "2025五一杯C题",
		description: "分析社交媒体平台用户行为特征",
		tags: ["社交媒体", "用户行为"],
		problemText: "分析社交媒体平台用户行为特征，构建用户画像模型。",
		image: wuyiCupC,
	},
	{
		id: 3,
		title: "农作物的种植策略",
		source: "2024高教杯C题",
		description: "研究农作物的种植策略",
		tags: ["种植策略", "农作物", "生长"],
		problemText:
			"研究农作物的种植策略，建立一个优化模型，使得农作物产量最大化。",
		image: mcmCupC,
	},
]);

const selectExample = async (example: ModelingExample) => {
	const res = await exampleAPI(example.id.toString(), example.source);
	const task_id = res?.data?.task_id;
	router.push(`/task/${task_id}`);
};
</script>

<template>
  <div class="mt-5">
    <h2 class="text-xs font-medium text-muted-foreground mb-2">案例</h2>
    <div class="border rounded-md divide-y">
      <div v-for="example in examples" :key="example.id"
        class="flex flex-col gap-2 px-3 py-2 sm:flex-row sm:items-center sm:gap-3">
        <div class="flex min-w-0 flex-1 items-center gap-3">
        <img :src="example.image" alt=""
          class="h-10 w-16 rounded border object-cover object-top shrink-0 bg-muted" />
        <div class="min-w-0 flex-1">
          <div class="text-sm font-medium truncate">{{ example.title }}</div>
          <div class="text-xs text-muted-foreground">{{ example.source }}</div>
        </div>
        </div>
        <Button variant="outline" size="sm" class="w-full shrink-0 sm:w-auto" @click="selectExample(example)">
          使用该案例
        </Button>
      </div>
    </div>
  </div>
</template>
