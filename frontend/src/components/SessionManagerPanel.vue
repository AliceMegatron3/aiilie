<template>
  <div class="bg-[#18181b] border border-gray-800 rounded-lg p-4">
    <div class="flex justify-between items-center mb-4">
      <h3 class="text-sm font-bold text-gray-300">🧊 LLM 记忆池会话监控</h3>
      <span class="text-xs text-green-500 bg-green-900/30 px-2 py-1 rounded">池化开启</span>
    </div>

    <!-- 状态面板 -->
    <div class="grid grid-cols-4 gap-3 mb-6">
      <div class="bg-[#27272a] p-3 rounded-lg border border-gray-700">
        <div class="text-xs text-gray-500 mb-1">热缓存 (内存)</div>
        <div class="text-lg font-bold text-gray-200">{{ stats.hot_cache_sessions || 0 }}</div>
      </div>
      <div class="bg-[#27272a] p-3 rounded-lg border border-gray-700">
        <div class="text-xs text-gray-500 mb-1">冷缓存 (磁盘)</div>
        <div class="text-lg font-bold text-gray-200">{{ stats.cold_cache_sessions || 0 }} 个</div>
      </div>
      <div class="bg-[#27272a] p-3 rounded-lg border border-gray-700">
        <div class="text-xs text-gray-500 mb-1">总消息复用数</div>
        <div class="text-lg font-bold text-blue-400">{{ stats.total_reused_messages || 0 }}</div>
      </div>
      <div class="bg-[#27272a] p-3 rounded-lg border border-gray-700">
        <div class="text-xs text-gray-500 mb-1">冻结归档态</div>
        <div class="text-lg font-bold text-purple-400">{{ stats.frozen_sessions || 0 }}</div>
      </div>
    </div>

    <!-- 活跃会话列表 -->
    <div class="text-xs font-bold text-gray-500 mb-2">活跃上下文 (Active Contexts)</div>
    <div class="space-y-2 max-h-64 overflow-y-auto pr-2">
      <div v-for="sess in sessions" :key="sess.session_id" class="bg-[#1f1f22] border border-gray-700 rounded p-3 flex justify-between items-center hover:border-gray-500 transition-colors">
        <div>
          <div class="flex items-center space-x-2 mb-1">
            <span class="text-sm text-gray-200 font-mono">{{ sess.session_id.substring(0,8) }}...</span>
            <span class="px-1.5 py-0.5 bg-blue-900/40 text-blue-400 text-[10px] rounded">{{ sess.model_key }}</span>
            <span v-if="sess.frozen" class="px-1.5 py-0.5 bg-purple-900/40 text-purple-400 text-[10px] rounded">Frozen</span>
          </div>
          <div class="text-[10px] text-gray-500 space-x-2">
            <span>项目: <span class="text-gray-400">{{ sess.bind_project_id || 'None' }}</span></span>
            <span>文档: <span class="text-gray-400">{{ sess.bind_doc_id || 'None' }}</span></span>
            <span>分支: <span class="text-gray-400">{{ sess.bind_branch_id || 'None' }}</span></span>
          </div>
          <div class="text-[10px] text-gray-600 mt-0.5">
            消息轮数: {{ sess.messages ? sess.messages.length : 0 }}/{{ sess.max_message_count }}
          </div>
        </div>
        <div class="flex space-x-2">
          <button @click="freezeSession(sess.session_id)" v-if="!sess.frozen" class="text-gray-400 hover:text-white px-2 py-1 bg-gray-700 rounded text-xs transition-colors">冻结</button>
          <button @click="destroySession(sess.session_id)" class="text-red-400 hover:text-red-300 px-2 py-1 bg-red-900/40 rounded text-xs transition-colors">销毁</button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { api } from '../api'
import { useVisibilityPolling } from '../composables/useVisibilityPolling'

const stats = ref({})
const sessions = ref([])

const loadData = async () => {
  try {
    const statsRes = await api.sessions.stats()
    if (statsRes.data.status === 'success') {
      stats.value = statsRes.data.data
    }
    
    const listRes = await api.sessions.list()
    if (listRes.data.status === 'success') {
      sessions.value = listRes.data.data
    }
  } catch (error) {
    console.error("Failed to load session data:", error)
  }
}

// 页面可见性感知轮询：标签页隐藏自动暂停，组件卸载自动清理定时器
useVisibilityPolling(loadData, 5000)

const freezeSession = async (id) => {
  await api.sessions.freeze(id)
  loadData()
}

const destroySession = async (id) => {
  await api.sessions.destroy(id)
  loadData()
}
</script>
