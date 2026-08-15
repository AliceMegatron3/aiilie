<template>
  <div class="h-full relative flex flex-col bg-[#121212]">
    <router-view></router-view>
  </div>
</template>

<script setup lang="ts">
import { onMounted } from 'vue'
import { useProjectStore } from '../stores/useProjectStore'

const projectStore = useProjectStore()

onMounted(() => {
  // 加载项目列表
  projectStore.fetchProjects()
})
</script>
