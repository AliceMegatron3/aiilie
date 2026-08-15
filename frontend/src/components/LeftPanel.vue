<template>
  <div class="h-full flex flex-col bg-[#121212]">
    <!-- 固定导航区 -->
    <div class="p-4 border-b border-[#2a2a30] space-y-1">
      <button @click="appStore.setActiveModule('projects')" class="w-full text-left px-3 py-2 rounded text-sm font-medium transition-colors" :class="isActive('projects') ? 'bg-[#2a2a30] text-gray-200' : 'text-gray-400 hover:text-gray-200 hover:bg-[#1e1e24]'">
        📂 项目库
      </button>
      <button @click="appStore.setActiveModule('library')" class="w-full text-left px-3 py-2 rounded text-sm font-medium transition-colors" :class="isActive('library') ? 'bg-[#2a2a30] text-gray-200' : 'text-gray-400 hover:text-gray-200 hover:bg-[#1e1e24]'">
        📚 书库工具
      </button>
      <button @click="appStore.setActiveModule('agents')" class="w-full text-left px-3 py-2 rounded text-sm font-medium transition-colors" :class="isActive('agents') ? 'bg-[#2a2a30] text-gray-200' : 'text-gray-400 hover:text-gray-200 hover:bg-[#1e1e24]'">
        🎛️ 智能体管家
      </button>

      <button @click="appStore.toggleCopilot()" class="w-full text-left px-3 py-2 mt-4 bg-purple-900/30 border border-purple-500/50 rounded text-sm font-bold text-purple-300 hover:bg-purple-900/50 transition-colors shadow-[0_0_15px_rgba(168,85,247,0.2)]">
        🔮 创作预估沙盘
      </button>

      <!-- 更多下拉菜单 -->
      <div class="relative mt-2">
        <button @click="toggleDropdown" class="w-full text-left px-3 py-2 rounded text-sm font-medium transition-colors flex justify-between items-center text-gray-400 hover:text-gray-200 hover:bg-[#1e1e24]">
          <span>更多功能 ▼</span>
        </button>
        <div v-if="dropdownOpen" class="absolute left-0 right-0 mt-1 bg-[#1e1e24] border border-[#2a2a30] rounded-lg shadow-xl z-20 overflow-hidden">
          <button @click="selectDropdownItem('templates')" class="w-full text-left px-3 py-2 text-sm transition-colors" :class="isActive('templates') ? 'bg-[#2a2a30] text-gray-200' : 'text-gray-400 hover:text-white hover:bg-[#2a2a30]'">
            📝 模板库
          </button>
          <button @click="selectDropdownItem('sessions')" class="w-full text-left px-3 py-2 text-sm transition-colors" :class="isActive('sessions') ? 'bg-[#2a2a30] text-gray-200' : 'text-gray-400 hover:text-white hover:bg-[#2a2a30]'">
            💬 会话池
          </button>
          <button @click="selectDropdownItem('emotion')" class="w-full text-left px-3 py-2 text-sm transition-colors" :class="isActive('emotion') ? 'bg-[#2a2a30] text-gray-200' : 'text-gray-400 hover:text-white hover:bg-[#2a2a30]'">
            💗 情感引擎
          </button>
          <button @click="selectDropdownItem('timeline')" class="w-full text-left px-3 py-2 text-sm transition-colors" :class="isActive('timeline') ? 'bg-[#2a2a30] text-gray-200' : 'text-gray-400 hover:text-white hover:bg-[#2a2a30]'">
            🕰️ 叙事时间轴
          </button>
          <button @click="selectDropdownItem('deep-think')" class="w-full text-left px-3 py-2 text-sm transition-colors" :class="isActive('deep-think') ? 'bg-[#2a2a30] text-gray-200' : 'text-gray-400 hover:text-white hover:bg-[#2a2a30]'">
            🧠 深度思考
          </button>
          <button @click="selectDropdownItem('reflections')" class="w-full text-left px-3 py-2 text-sm transition-colors" :class="isActive('reflections') ? 'bg-[#2a2a30] text-gray-200' : 'text-gray-400 hover:text-white hover:bg-[#2a2a30]'">
            🧠 反思记录
          </button>
          <button @click="selectDropdownItem('settings')" class="w-full text-left px-3 py-2 text-sm transition-colors" :class="isActive('settings') ? 'bg-[#2a2a30] text-gray-200' : 'text-gray-400 hover:text-white hover:bg-[#2a2a30]'">
            ⚙ 系统设置
          </button>
        </div>
      </div>
    </div>

    <!-- 动态次级列表区 -->
    <div class="flex-1 overflow-y-auto relative">
      <ProjectPanel v-if="isActive('projects')" />
      <LibraryPanel v-if="isActive('library')" />
      <AgentDashboard v-if="isActive('agents')" />
      <TemplatesPanel v-if="isActive('templates')" />
      <EmotionFramesPanel v-if="isActive('emotion')" />
      <SessionsPanel v-if="isActive('sessions')" />
      <ReflectionsPanel v-if="isActive('reflections')" />
      <SettingsPanel v-if="isActive('settings')" />
    </div>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { useAppStore } from '../stores/useAppStore'
import ProjectPanel from './panels/ProjectPanel.vue'
import LibraryPanel from './panels/LibraryPanel.vue'
import AgentDashboard from './panels/AgentDashboard.vue'
import TemplatesPanel from './panels/TemplatesPanel.vue'
import EmotionFramesPanel from './panels/EmotionFramesPanel.vue'
import SessionsPanel from './panels/SessionsPanel.vue'
import ReflectionsPanel from './panels/ReflectionsPanel.vue'
import SettingsPanel from './panels/SettingsPanel.vue'

const appStore = useAppStore()
const dropdownOpen = ref(false)

const isActive = (moduleName) => {
  return appStore.activeModule === moduleName
}

const toggleDropdown = () => {
  dropdownOpen.value = !dropdownOpen.value
}

const selectDropdownItem = (moduleName) => {
  appStore.setActiveModule(moduleName)
  dropdownOpen.value = false
}
</script>

