<script setup lang="ts">
import NavUser from "./NavUser.vue";

import {
	Sidebar,
	SidebarContent,
	SidebarFooter,
	SidebarHeader,
	SidebarRail,
	type SidebarProps,
} from "@/components/ui/sidebar";

const props = withDefaults(defineProps<SidebarProps>(), {
	collapsible: "icon",
});
</script>

<template>
  <Sidebar v-bind="props">
    <SidebarHeader class="p-1">
      <router-link to="/" class="flex items-center gap-2 overflow-hidden p-1">
        <img src="@/assets/icon.png" alt="logo" class="size-8 shrink-0">
        <span class="text-sm font-semibold truncate group-data-[collapsible=icon]:hidden">
          MathModelAgent
        </span>
      </router-link>
    </SidebarHeader>
    <SidebarContent />
    <SidebarRail />
    <SidebarFooter>
      <NavUser />
    </SidebarFooter>
  </Sidebar>
</template>
