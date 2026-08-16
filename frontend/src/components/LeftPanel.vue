<template>
  <div class="h-full flex flex-col th-panel">
    <!-- 顶部品牌占位 -->
    <div class="px-4 py-3 border-b border-[#1f1f25] shrink-0">
      <div class="text-sm font-bold text-gray-200">No.0 AI</div>
      <div class="text-[9px] text-gray-600">小说创作系统 · 功能域导航</div>
    </div>

    <!-- 分组导航(以后端功能域组织) -->
    <div class="flex-1 overflow-y-auto px-2 py-2 space-y-3">
      <!-- 创作 -->
      <div v-for="group in groups" :key="group.title">
        <div class="px-2 py-1 text-[9px] font-semibold text-gray-600 uppercase">{{ group.title }}</div>
        <button
          v-for="item in group.items"
          :key="item.module"
          @click="appStore.setActiveModule(item.module)"
          class="w-full text-left px-3 py-1.5 rounded text-[13px] transition-colors mb-0.5"
          :class="isActive(item.module) ? 'bg-indigo-900/40 text-indigo-200' : 'text-gray-400 hover:text-gray-200 hover:bg-[#1e1e24]'"
        >
          <span class="mr-1.5">{{ item.icon }}</span>{{ item.label }}
        </button>
      </div>
    </div>

    <!-- 底部版本 -->
    <div class="px-4 py-2 border-t border-[#1f1f25] shrink-0 text-[9px] text-gray-600">v4.0 · 后端功能域驱动</div>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { useAppStore } from '../stores/useAppStore'

const appStore = useAppStore()

const groups = [
  {
    title: '创作',
    items: [
      { module: 'projects', label: '项目库', icon: '📁' },
      { module: 'narrative', label: '叙事结构', icon: '📖' },
      { module: 'lockfield', label: '世界观锁定', icon: '🔒' },
      { module: 'ensemble-history', label: '群像时间线', icon: '🎭' },
    ],
  },
  {
    title: '知识',
    items: [
      { module: 'library', label: '书库', icon: '📚' },
      { module: 'knowledge-gaps', label: '知识缺口', icon: '🧩' },
      { module: 'emotion', label: '情感引擎', icon: '💗' },
      { module: 'timeline', label: '叙事时间轴', icon: '🕰️' },
    ],
  },
  {
    title: '智能',
    items: [
      { module: 'agents', label: '智能体', icon: '🤖' },
      { module: 'deep-think', label: '深度思考', icon: '🧠' },
      { module: 'sessions', label: '会话池', icon: '💬' },
      { module: 'reflections', label: '反思记录', icon: '♻️' },
    ],
  },
  {
    title: '系统',
    items: [
      { module: 'plugins', label: '插件', icon: '🧩' },
      { module: 'workspace', label: '工作区', icon: '🗂️' },
      { module: 'templates', label: '提示词模板', icon: '📝' },
      { module: 'settings', label: '系统设置', icon: '⚙️' },
    ],
  },
]

const isActive = (module: string) => appStore.activeModule === module
</script>