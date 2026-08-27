<script setup lang="ts">
import { GITHUB_LINK, QQ_GROUP } from "@/utils/const";
import { ArrowRight, Github, MessageCircle } from "lucide-vue-next";
import { useRouter } from "vue-router";

const router = useRouter();
</script>

<template>
  <div class="flex min-h-svh flex-col items-center justify-center gap-8 bg-background px-6 text-center">
    <div class="space-y-3">
      <h1 class="text-4xl font-semibold tracking-tight">MathModelAgent</h1>
      <p class="mx-auto max-w-xl text-muted-foreground">
        国赛数学建模端到端管道：四 Agent 协作，从题面到论文全程可审、可回溯。
      </p>
    </div>
    <button
      class="inline-flex items-center gap-2 rounded-lg bg-primary px-6 py-3 text-sm font-medium text-primary-foreground shadow-sm transition-colors hover:bg-primary/90"
      @click="router.push('/chat')"
    >
      开始建模
      <ArrowRight class="h-4 w-4" />
    </button>
    <div class="flex items-center gap-5 text-muted-foreground/70">
      <a :href="QQ_GROUP" target="_blank" class="hover:text-foreground" title="QQ 交流群">
        <MessageCircle class="h-4 w-4" />
      </a>
      <a :href="GITHUB_LINK" target="_blank" class="hover:text-foreground" title="GitHub">
        <Github class="h-4 w-4" />
      </a>
    </div>
  </div>
</template>
