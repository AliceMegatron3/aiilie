<template>
  <header class="h-12 bg-[#18181b] border-b border-[#2a2a30] flex items-center justify-between px-4 shrink-0 shadow-sm z-20">
    <div class="flex items-center gap-3">
      <div class="w-6 h-6 rounded bg-gradient-to-br from-indigo-500 to-purple-600 flex items-center justify-center text-white text-xs font-bold">
        N
      </div>
      <h1 class="text-gray-200 font-semibold text-lg tracking-wide">No0 AI V4.0</h1>
    </div>

    <!-- Settings Dropdown -->
    <div class="relative">
      <button
        @click="isDropdownOpen = !isDropdownOpen"
        class="flex items-center gap-2 px-3 py-1.5 text-sm text-gray-300 hover:text-white hover:bg-[#2a2a30] rounded transition-colors"
      >
        <span>设置</span>
        <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7"></path></svg>
      </button>

      <!-- Dropdown Menu -->
      <div
        v-if="isDropdownOpen"
        class="absolute right-0 mt-2 w-56 bg-[#1e1e24] border border-[#2a2a30] rounded shadow-xl py-1 z-50"
      >
        <button @click="navigateTo('settings'); isDropdownOpen = false" class="w-full text-left px-4 py-2 text-sm text-gray-300 hover:bg-[#2a2a30] hover:text-white">项目设置</button>
        <button @click="navigateTo('library'); isDropdownOpen = false" class="w-full text-left px-4 py-2 text-sm text-gray-300 hover:bg-[#2a2a30] hover:text-white">书库设置</button>
        <button @click="navigateTo('templates'); isDropdownOpen = false" class="w-full text-left px-4 py-2 text-sm text-gray-300 hover:bg-[#2a2a30] hover:text-white">模板管理</button>
        <div class="h-px bg-[#2a2a30] my-1"></div>
        <button @click="triggerSystemCheck" class="w-full text-left px-4 py-2 text-sm text-gray-300 hover:bg-[#2a2a30] hover:text-white">运行前后端对接自检</button>
        <button @click="navigateTo('settings'); isDropdownOpen = false" class="w-full text-left px-4 py-2 text-sm text-gray-300 hover:bg-[#2a2a30] hover:text-white">系统监控</button>
        <button class="w-full text-left px-4 py-2 text-sm text-gray-300 hover:bg-[#2a2a30] hover:text-white">关于</button>
      </div>
    </div>

    <SystemCheckModal v-if="showSystemCheck" @close="showSystemCheck = false" />
  </header>
</template>

<script setup>
import { ref, onMounted, onUnmounted } from 'vue'
import { useAppStore } from '../stores/useAppStore'
import SystemCheckModal from './SystemCheckModal.vue'

const appStore = useAppStore()
const isDropdownOpen = ref(false)
const showSystemCheck = ref(false)

// Handle click outside to close dropdown
const closeDropdown = (e) => {
  if (isDropdownOpen.value && !e.target.closest('.relative')) {
    isDropdownOpen.value = false
  }
}

onMounted(() => {
  document.addEventListener('click', closeDropdown)
})

onUnmounted(() => {
  document.removeEventListener('click', closeDropdown)
})

const navigateTo = (module) => {
  appStore.setActiveModule(module)
}

const triggerSystemCheck = () => {
  isDropdownOpen.value = false
  showSystemCheck.value = true
}
</script>
