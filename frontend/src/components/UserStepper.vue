<script setup lang="ts">
import { saveApiConfig } from "@/apis/apiKeyApi";
import { submitModelingTask } from "@/apis/submitModelingApi";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import {
	Select,
	SelectContent,
	SelectGroup,
	SelectItem,
	SelectLabel,
	SelectTrigger,
	SelectValue,
} from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { useToast } from "@/components/ui/toast";
import { useApiKeyStore } from "@/stores/apiKeys";
import { useTaskStore } from "@/stores/task";
import { FileUp, Rocket, X } from "lucide-vue-next";
import { ref } from "vue";
import { useRouter } from "vue-router";
import FileConfirmDialog from "./FileConfirmDialog.vue";

const taskStore = useTaskStore();
const { toast } = useToast();
const apiKeyStore = useApiKeyStore();
const fileConfirmDialog = ref<InstanceType<typeof FileConfirmDialog> | null>(
	null,
);

const uploadedFiles = ref<File[]>([]);
const question = ref("");
const selectedOptions = ref({
	template: "国赛",
	language: "中文",
	format: "Markdown",
});
const isDragging = ref(false);
const showUploadSuccess = ref(false);
const showSubmitSuccess = ref(false);
const taskId = ref<string | null>(null);
const fileInput = ref<HTMLInputElement | null>(null);

const ACCEPTED_EXT = [".txt", ".csv", ".xlsx"];

const isAcceptedFile = (file: File) => {
	const name = file.name.toLowerCase();
	return ACCEPTED_EXT.some((ext) => name.endsWith(ext));
};

const applyFiles = (files: File[]) => {
	const next = files.filter(isAcceptedFile);
	if (next.length === 0) return;
	uploadedFiles.value = next;
	showUploadSuccess.value = true;
	setTimeout(() => {
		showUploadSuccess.value = false;
	}, 1000);
};

const handleFileUpload = (event: Event) => {
	const input = event.target as HTMLInputElement;
	if (input.files && input.files.length > 0) {
		applyFiles(Array.from(input.files));
	}
};

const handleDragOver = (event: DragEvent) => {
	event.preventDefault();
	isDragging.value = true;
};

const handleDragLeave = () => {
	isDragging.value = false;
};

const handleDrop = (event: DragEvent) => {
	event.preventDefault();
	isDragging.value = false;
	const files = event.dataTransfer?.files;
	if (files && files.length > 0) {
		applyFiles(Array.from(files));
	}
};

const removeFile = (index: number) => {
	uploadedFiles.value = uploadedFiles.value.filter((_, i) => i !== index);
};

const router = useRouter();

const handleSubmit = async () => {
	try {
		if (apiKeyStore.isEmpty) {
			toast({
				title: "请先配置 API Key",
				description: "点击顶部「配置」填写 API Key",
				variant: "destructive",
			});
			return;
		}

		await saveApiConfig({
			coordinator: apiKeyStore.coordinatorConfig,
			modeler: apiKeyStore.modelerConfig,
			coder: apiKeyStore.coderConfig,
			writer: apiKeyStore.writerConfig,
			openalex_email: apiKeyStore.openalexEmail,
		});

		if (uploadedFiles.value.length === 0) {
			if (!fileConfirmDialog.value) return;

			const shouldContinue = await fileConfirmDialog.value.openConfirmDialog();

			if (!shouldContinue) {
				toast({
					title: "请先上传文件",
					description: "请先上传文件",
					variant: "destructive",
				});
				return;
			}
		}
		console.log(selectedOptions.value);
		console.log(question.value);
		console.log(uploadedFiles.value);
		const response = await submitModelingTask(
			{
				ques_all: question.value,
				comp_template: selectedOptions.value.template,
				format_output: selectedOptions.value.format,
			},
			uploadedFiles.value,
		);

		taskId.value = response?.data?.task_id ?? null;
		taskStore.addUserMessage(question.value);

		showSubmitSuccess.value = true;
		setTimeout(() => {
			showSubmitSuccess.value = false;
		}, 3000);
		router.push(`/task/${taskId.value}`);
		toast({
			title: "任务提交成功",
			description: `任务提交成功，编号为：${taskId.value}`,
		});
	} catch (error) {
		console.error("任务提交失败:", error);
		toast({
			title: "任务提交失败",
			description: "请检查 API Key 是否正确",
			variant: "destructive",
		});
	}
};
</script>

<template>
  <div class="w-full relative">
    <Transition name="fade">
      <div v-if="showUploadSuccess" class="fixed top-4 right-4 z-50">
        <Alert>
          <Rocket class="h-4 w-4" />
          <AlertTitle>文件上传成功</AlertTitle>
          <AlertDescription>
            已上传 {{ uploadedFiles.length }} 个文件
          </AlertDescription>
        </Alert>
      </div>
    </Transition>

    <Transition name="fade">
      <div v-if="showSubmitSuccess" class="fixed top-4 right-4 z-50">
        <Alert>
          <Rocket class="h-4 w-4" />
          <AlertTitle>任务提交成功</AlertTitle>
          <AlertDescription>
            任务提交成功，编号为：{{ taskId }}。
          </AlertDescription>
        </Alert>
      </div>
    </Transition>

    <div class="border rounded-md">
      <div class="p-3 space-y-3">
        <div class="space-y-1">
          <h4 class="text-xs font-medium text-muted-foreground">题目</h4>
          <Textarea v-model="question" placeholder="粘贴完整题目背景和多个小问" class="min-h-[120px]" />
        </div>

        <div
          class="border border-dashed rounded-md px-3 py-2.5 flex items-center gap-3 cursor-pointer"
          :class="isDragging ? 'border-primary bg-primary/5' : 'hover:border-primary/50'"
          @click="() => fileInput?.click()"
          @dragover="handleDragOver"
          @dragleave="handleDragLeave"
          @drop="handleDrop"
        >
          <input type="file" ref="fileInput" class="hidden" @change="handleFileUpload" accept=".txt,.csv,.xlsx"
            multiple>
          <FileUp class="pointer-events-none size-4 text-muted-foreground shrink-0" />
          <div class="pointer-events-none min-w-0 flex-1">
            <p class="text-sm">拖入数据集或点击上传</p>
            <p class="text-xs text-muted-foreground">.txt .csv .xlsx，可多选</p>
          </div>
        </div>

        <ul v-if="uploadedFiles.length > 0" class="text-xs space-y-1">
          <li v-for="(file, index) in uploadedFiles" :key="file.name + index"
            class="flex items-center gap-2 rounded-md bg-muted px-2 py-1">
            <span class="truncate flex-1">{{ file.name }}</span>
            <button type="button" class="text-muted-foreground hover:text-foreground" @click.stop="removeFile(index)">
              <X class="size-3.5" />
            </button>
          </li>
        </ul>

        <div class="flex flex-col gap-2 sm:flex-row sm:flex-wrap sm:items-center">
          <div class="flex flex-wrap gap-2">
          <Select v-model="selectedOptions.template">
            <SelectTrigger class="h-8 w-[7.5rem]">
              <SelectValue placeholder="模板" />
            </SelectTrigger>
            <SelectContent>
              <SelectGroup>
                <SelectLabel>模板</SelectLabel>
                <SelectItem value="国赛">国赛</SelectItem>
                <SelectItem value="美赛">美赛</SelectItem>
              </SelectGroup>
            </SelectContent>
          </Select>
          <Select v-model="selectedOptions.language">
            <SelectTrigger class="h-8 w-[7.5rem]">
              <SelectValue placeholder="语言" />
            </SelectTrigger>
            <SelectContent>
              <SelectGroup>
                <SelectLabel>语言</SelectLabel>
                <SelectItem value="中文">中文</SelectItem>
                <SelectItem value="英文">英文</SelectItem>
              </SelectGroup>
            </SelectContent>
          </Select>
          <Select v-model="selectedOptions.format">
            <SelectTrigger class="h-8 w-[7.5rem]">
              <SelectValue placeholder="格式" />
            </SelectTrigger>
            <SelectContent>
              <SelectGroup>
                <SelectLabel>格式</SelectLabel>
                <SelectItem value="Markdown">Markdown</SelectItem>
                <SelectItem value="LaTeX">LaTeX</SelectItem>
              </SelectGroup>
            </SelectContent>
          </Select>
          </div>
          <Button class="w-full shrink-0 sm:ml-auto sm:w-auto" size="sm" @click="handleSubmit">
            开始分析
          </Button>
        </div>
      </div>
    </div>
  </div>
  <FileConfirmDialog ref="fileConfirmDialog" />
</template>
