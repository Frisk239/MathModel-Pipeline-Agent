<script setup lang="ts">
import {
	SidebarMenu,
	SidebarMenuButton,
	SidebarMenuItem,
} from "@/components/ui/sidebar";
import ApiKeyDialog from "@/pages/chat/components/ApiDialog.vue";
import { Settings2 } from "lucide-vue-next";
import { ref } from "vue";

const isApiKeyDialogOpen = ref(false);

const openApiKeyDialog = () => {
	isApiKeyDialogOpen.value = true;
};
</script>

<template>
  <SidebarMenu>
    <SidebarMenuItem>
      <SidebarMenuButton tooltip="配置" @click="openApiKeyDialog">
        <Settings2 />
        <span>配置</span>
      </SidebarMenuButton>
    </SidebarMenuItem>
  </SidebarMenu>
  <ApiKeyDialog v-model:open="isApiKeyDialogOpen" />
</template>
