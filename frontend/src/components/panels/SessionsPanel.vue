<template>
  <div class="space-y-4 p-4">
    <div class="flex justify-between items-center mb-4">
      <h2 class="text-xs font-semibold text-gray-500 uppercase tracking-wider">会话池</h2>
      <button @click="loadSessions" class="text-xs text-gray-400 hover:text-gray-200 transition-colors">刷新</button>
    </div>

    <!-- 加载状态 -->
    <div v-if="isLoading" class="text-xs text-gray-500 text-center py-4">
      <span class="animate-pulse">加载会话...</span>
    </div>

    <!-- 空状态 -->
    <div v-else-if="sessions.length === 0" class="text-xs text-gray-600 text-center py-8">
      暂无活跃会话
    </div>

    <!-- 会话列表 -->
    <div v-else class="space-y-2">
      <div
        v-for="sess in sessions"
        :key="sess.session_id"
        class="p-2.5 bg-[#18181b] rounded-lg border border-[#2a2a30] hover:border-gray-500 transition-colors"
      >
        <div class="flex items-center justify-between mb-1">
          <h3 class="font-medium text-xs text-gray-300 font-mono truncate">
            {{ (sess.session_id || '').substring(0, 12) }}...
          </h3>
          <div class="flex gap-1">
            <span v-if="sess.model_key" class="text-[10px] px-1.5 py-0.5 rounded bg-blue-900/40 text-blue-400">{{ sess.model_key }}</span>
            <span v-if="sess.frozen" class="text-[10px] px-1.5 py-0.5 rounded bg-purple-900/40 text-purple-400">Frozen</span>
          </div>
        </div>
        <div class="flex items-center justify-between">
          <p class="text-[11px] text-gray-500">
            消息: {{ sess.messages ? sess.messages.length : 0 }}/{{ sess.max_message_count || 25 }}
          </p>
          <div class="flex gap-1">
            <button
              v-if="!sess.frozen"
              @click="freezeSession(sess.session_id)"
              class="text-[10px] text-gray-400 hover:text-white px-2 py-0.5 bg-gray-700 rounded transition-colors"
            >
              冻结
            </button>
            <button
              @click="destroySession(sess.session_id)"
              class="text-[10px] text-red-400 hover:text-red-300 px-2 py-0.5 bg-red-900/40 rounded transition-colors"
            >
              销毁
            </button>
          </div>
        </div>
      </div>
    </div>

    <!-- 会话统计 -->
    <div v-if="stats" class="pt-4 border-t border-[#2a2a30]">
      <h3 class="text-xs font-semibold text-gray-500 uppercase tracking-wider mb-2">会话统计</h3>
      <div class="grid grid-cols-2 gap-2">
        <div class="bg-[#18181b] p-2 rounded border border-[#2a2a30]">
          <div class="text-[10px] text-gray-500">热缓存</div>
          <div class="text-sm font-bold text-gray-200">{{ stats.hot_cache_sessions || 0 }}</div>
        </div>
        <div class="bg-[#18181b] p-2 rounded border border-[#2a2a30]">
          <div class="text-[10px] text-gray-500">冻结归档</div>
          <div class="text-sm font-bold text-purple-400">{{ stats.frozen_sessions || 0 }}</div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { api } from '../../api'

const sessions = ref([])
const stats = ref(null)
const isLoading = ref(false)

const loadSessions = async () => {
  isLoading.value = true
  try {
    const [listRes, statsRes] = await Promise.all([
      api.sessions.list(),
      api.sessions.stats(),
    ])
    if (listRes.data.status === 'success') {
      sessions.value = listRes.data.data || []
    }
    if (statsRes.data.status === 'success') {
      stats.value = statsRes.data.data
    }
  } catch (e) {
    console.error('加载会话列表失败', e)
    sessions.value = []
  } finally {
    isLoading.value = false
  }
}

const freezeSession = async (id) => {
  try {
    await api.sessions.freeze(id)
    loadSessions()
  } catch (e) {
    console.error('冻结会话失败', e)
  }
}

const destroySession = async (id) => {
  try {
    await api.sessions.destroy(id)
    loadSessions()
  } catch (e) {
    console.error('销毁会话失败', e)
  }
}

onMounted(() => {
  loadSessions()
})
</script>
