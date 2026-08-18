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
        <button @click="navigateTo('settings'); isDropdownOpen = false" class="w-full text-left px-4 py-2 text-sm text-gray-300 hover:bg-[#2a2a30] hover:text-white">⚙️ 系统设置</button>
        <button @click="navigateTo('plugins'); isDropdownOpen = false" class="w-full text-left px-4 py-2 text-sm text-gray-300 hover:bg-[#2a2a30] hover:text-white">🧩 插件管理</button>
        <button @click="navigateTo('ledger'); isDropdownOpen = false" class="w-full text-left px-4 py-2 text-sm text-gray-300 hover:bg-[#2a2a30] hover:text-white">📚 Ledger 与法则</button>
        <button @click="navigateTo('code'); isDropdownOpen = false" class="w-full text-left px-4 py-2 text-sm text-gray-300 hover:bg-[#2a2a30] hover:text-white">💻 编程副驾驶</button>
        <button @click="navigateTo('templates'); isDropdownOpen = false" class="w-full text-left px-4 py-2 text-sm text-gray-300 hover:bg-[#2a2a30] hover:text-white">📝 提示词模板</button>
        <button @click="navigateTo('deep-think'); isDropdownOpen = false" class="w-full text-left px-4 py-2 text-sm text-gray-300 hover:bg-[#2a2a30] hover:text-white">🧠 深度思考</button>
        <button @click="navigateTo('reflections'); isDropdownOpen = false" class="w-full text-left px-4 py-2 text-sm text-gray-300 hover:bg-[#2a2a30] hover:text-white">♻️ 反思记录</button>
        <button @click="navigateTo('sessions'); isDropdownOpen = false" class="w-full text-left px-4 py-2 text-sm text-gray-300 hover:bg-[#2a2a30] hover:text-white">💬 会话池</button>
        <button @click="navigateTo('experience'); isDropdownOpen = false" class="w-full text-left px-4 py-2 text-sm text-gray-300 hover:bg-[#2a2a30] hover:text-white">📖 经验域</button>
        <div class="h-px bg-[#2a2a30] my-1"></div>
        <button @click="triggerSystemCheck" class="w-full text-left px-4 py-2 text-sm text-gray-300 hover:bg-[#2a2a30] hover:text-white">运行前后端对接自检</button>
        <button @click="showAbout = true; isDropdownOpen = false" class="w-full text-left px-4 py-2 text-sm text-gray-300 hover:bg-[#2a2a30] hover:text-white">关于</button>
      </div>
    </div>

    <SystemCheckModal v-if="showSystemCheck" @close="showSystemCheck = false" />

    <!-- 关于弹窗 -->
    <div v-if="showAbout" class="fixed inset-0 z-50 flex items-center justify-center bg-black/60" @click.self="showAbout = false">
      <div class="w-80 rounded-lg border border-[#2a2a30] bg-[#18181b] p-5 shadow-2xl">
        <div class="flex items-center gap-3">
          <div class="w-9 h-9 rounded bg-gradient-to-br from-indigo-500 to-purple-600 flex items-center justify-center text-white font-bold">N</div>
          <div>
            <div class="text-sm font-semibold text-gray-200">No0 AI V4.0</div>
            <div class="text-[11px] text-zinc-500">版本 4.0.0 · 作者智能体创作工作台</div>
          </div>
        </div>
        <p class="mt-3 text-xs leading-relaxed text-zinc-400">
          面向长篇小说的多智能体辅助创作平台：世界观一致性、情绪量化、叙事结构、
          群像关系与版本化管理。本机模式运行，代码执行与插件执行默认关闭。
        </p>
        <button class="mt-4 w-full rounded bg-[#2a2a30] px-3 py-1.5 text-xs text-zinc-200 hover:bg-[#3a3a40]" @click="showAbout = false">关闭</button>
      </div>
    </div>
  </header>
</template>

<script setup>
import { ref, onMounted, onUnmounted } from 'vue'
import { useAppStore } from '../stores/useAppStore'
import SystemCheckModal from './SystemCheckModal.vue'

const appStore = useAppStore()
const isDropdownOpen = ref(false)
const showSystemCheck = ref(false)
const showAbout = ref(false)

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
