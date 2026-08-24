<script setup lang="ts">
import { getHelloWorld } from "@/apis/commonApi";
import AppSidebar from "@/components/AppSidebar.vue";
import ModelingExamples from "@/components/ModelingExamples.vue";
import ServiceStatus from "@/components/ServiceStatus.vue";
import UserStepper from "@/components/UserStepper.vue";
import Button from "@/components/ui/button/Button.vue";
import {
	SidebarInset,
	SidebarProvider,
	SidebarTrigger,
} from "@/components/ui/sidebar";
import ApiDialog from "@/pages/chat/components/ApiDialog.vue";
import MoreDetail from "@/pages/chat/components/MoreDetail.vue";
import { AppWindow, CircleEllipsis, Settings2 } from "lucide-vue-next";
import { onMounted, ref } from "vue";

const isMoreDetailOpen = ref(false);
const isApiDialogOpen = ref(false);

onMounted(() => {
	getHelloWorld().then((res) => {
		console.log(res.data);
	});
});
</script>

<template>
  <SidebarProvider :default-open="false">
    <MoreDetail v-model="isMoreDetailOpen" />
    <ApiDialog v-model:open="isApiDialogOpen" />
    <AppSidebar collapsible="icon" />
    <SidebarInset>
      <header class="flex min-h-12 shrink-0 flex-wrap items-center gap-2 border-b px-3 py-1.5">
        <SidebarTrigger class="-ml-1" />
        <ServiceStatus />
        <div class="ml-auto flex items-center gap-1 sm:gap-2">
          <Button variant="outline" size="sm" @click="isApiDialogOpen = true">
            <Settings2 />
            <span class="hidden sm:inline">配置</span>
          </Button>
          <Button variant="outline" size="sm" @click="isMoreDetailOpen = true">
            <CircleEllipsis />
            <span class="hidden sm:inline">更多</span>
          </Button>
          <a href="https://www.mathmodel.top/" target="_blank">
            <Button variant="outline" size="sm">
              <AppWindow />
              <span class="hidden sm:inline">官网</span>
            </Button>
          </a>
        </div>
      </header>

      <div class="flex-1 overflow-y-auto px-4 py-4">
        <UserStepper />
        <ModelingExamples />
      </div>
    </SidebarInset>
  </SidebarProvider>
</template>
