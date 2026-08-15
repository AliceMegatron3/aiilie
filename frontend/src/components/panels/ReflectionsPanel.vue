<template>
  <div class="space-y-4 p-4">
    <div class="flex justify-between items-center mb-4">
      <h2 class="text-xs font-semibold text-gray-500 uppercase tracking-wider">反思记录</h2>
      <button
        @click="triggerReflection"
        :disabled="isTriggering"
        class="text-xs bg-purple-600 hover:bg-purple-500 text-white px-2 py-1 rounded transition-colors shadow disabled:opacity-40"
      >
        {{ isTriggering ? '触发中...' : '触发反思' }}
      </button>
    </div>

    <!-- Tab 切换 -->
    <div class="flex gap-1 bg-[#0f0f11] p-1 rounded-lg">
      <button
        v-for="tab in tabs"
        :key="tab.key"
        @click="activeTab = tab.key"
        class="flex-1 text-xs py-1.5 rounded-md transition-colors"
        :class="activeTab === tab.key ? 'bg-[#27272a] text-gray-200' : 'text-gray-500 hover:text-gray-300'"
      >
        {{ tab.label }}
      </button>
    </div>

    <!-- 反馈提示 -->
    <div v-if="feedback" class="text-xs px-2 py-1.5 rounded border" :class="feedbackType === 'success' ? 'bg-green-900/20 border-green-800 text-green-400' : 'bg-red-900/20 border-red-800 text-red-400'">
      {{ feedback }}
    </div>

    <!-- 加载状态 -->
    <div v-if="isLoading" class="text-xs text-gray-500 text-center py-4">
      <span class="animate-pulse">加载中...</span>
    </div>

    <!-- 会话列表 Tab -->
    <div v-else-if="activeTab === 'sessions'" class="space-y-2">
      <div v-if="sessions.length === 0" class="text-xs text-gray-600 text-center py-4">暂无反思会话</div>
      <div
        v-for="sess in sessions"
        :key="sess.session_id"
        class="p-2.5 bg-[#18181b] rounded-lg border border-[#2a2a30] hover:border-gray-500 transition-colors cursor-pointer"
      >
        <div class="flex items-center justify-between">
          <h3 class="font-medium text-sm text-gray-300 truncate">{{ sess.summary || '反思会话 ' + (sess.session_id || '').substring(0, 8) }}</h3>
          <span class="text-[10px] px-1.5 py-0.5 rounded"
            :class="sess.status === 'COMPLETED' ? 'bg-green-900/40 text-green-400' : sess.status === 'RUNNING' ? 'bg-yellow-900/40 text-yellow-400' : 'bg-gray-700 text-gray-400'">
            {{ sess.status || 'UNKNOWN' }}
          </span>
        </div>
        <p class="text-[11px] text-gray-500 mt-1">
          类型: {{ sess.trigger_type || 'N/A' }}
          <span v-if="sess.start_time"> | 时间: {{ formatTime(sess.start_time) }}</span>
        </p>
      </div>
    </div>

    <!-- 优化规则 Tab -->
    <div v-else-if="activeTab === 'rules'" class="space-y-2">
      <div v-if="rules.length === 0" class="text-xs text-gray-600 text-center py-4">暂无激活的优化规则</div>
      <div
        v-for="rule in rules"
        :key="rule.rule_id"
        class="p-2.5 bg-[#18181b] rounded-lg border border-[#2a2a30] hover:border-gray-500 transition-colors"
      >
        <div class="flex items-center justify-between mb-1">
          <h3 class="font-medium text-xs text-gray-300 truncate">{{ rule.scope || '通用' }} 规则</h3>
          <div class="flex items-center gap-2">
            <span class="text-[10px] text-gray-500">置信度: {{ (rule.confidence * 100).toFixed(0) }}%</span>
            <button
              @click="toggleRule(rule)"
              class="text-[10px] px-2 py-0.5 rounded transition-colors"
              :class="rule.is_active ? 'bg-green-900/40 text-green-400 hover:bg-red-900/40 hover:text-red-400' : 'bg-gray-700 text-gray-400'"
            >
              {{ rule.is_active ? '启用中' : '已禁用' }}
            </button>
          </div>
        </div>
        <p class="text-[11px] text-gray-500 line-clamp-2">
          条件: {{ JSON.stringify(rule.condition).substring(0, 80) }}
        </p>
        <p class="text-[11px] text-gray-500 line-clamp-2 mt-1">
          动作: {{ JSON.stringify(rule.action).substring(0, 80) }}
        </p>
      </div>
    </div>

    <!-- 通用技能 Tab -->
    <div v-else-if="activeTab === 'skills'" class="space-y-2">
      <div v-if="skills.length === 0" class="text-xs text-gray-600 text-center py-4">暂无沉淀的通用技能</div>
      <div
        v-for="skill in skills"
        :key="skill.skill_id"
        class="p-2.5 bg-[#18181b] rounded-lg border border-[#2a2a30] hover:border-gray-500 transition-colors cursor-pointer"
      >
        <div class="flex items-center justify-between mb-1">
          <h3 class="font-medium text-xs text-gray-300">{{ skill.name }}</h3>
          <span class="text-[10px] px-1.5 py-0.5 rounded bg-indigo-900/40 text-indigo-400">{{ skill.type }}</span>
        </div>
        <p class="text-[11px] text-gray-500 line-clamp-2">{{ JSON.stringify(skill.content).substring(0, 100) }}</p>
        <p class="text-[10px] text-gray-600 mt-1">适用: {{ skill.applicability }}</p>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted, watch } from 'vue'
import { api } from '../../api'

const tabs = [
  { key: 'sessions', label: '会话记录' },
  { key: 'rules', label: '优化规则' },
  { key: 'skills', label: '通用技能' },
]

const activeTab = ref('sessions')
const isLoading = ref(false)
const isTriggering = ref(false)
const feedback = ref('')
const feedbackType = ref('success')

const sessions = ref([])
const rules = ref([])
const skills = ref([])

const showFeedback = (msg, type = 'success') => {
  feedback.value = msg
  feedbackType.value = type
  setTimeout(() => { feedback.value = '' }, 4000)
}

const formatTime = (timestamp) => {
  if (!timestamp) return ''
  const date = new Date(timestamp * 1000)
  return date.toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
}

const loadSessions = async () => {
  isLoading.value = true
  try {
    const res = await api.reflection.listSessions(50, 0)
    sessions.value = res.data || []
  } catch (e) {
    sessions.value = []
    showFeedback('加载反思会话失败', 'error')
  } finally {
    isLoading.value = false
  }
}

const loadRules = async () => {
  isLoading.value = true
  try {
    const res = await api.reflection.activeRules()
    rules.value = res.data || []
  } catch (e) {
    rules.value = []
    showFeedback('加载优化规则失败', 'error')
  } finally {
    isLoading.value = false
  }
}

const loadSkills = async () => {
  isLoading.value = true
  try {
    const res = await api.reflection.listSkills()
    skills.value = res.data || []
  } catch (e) {
    skills.value = []
    showFeedback('加载通用技能失败', 'error')
  } finally {
    isLoading.value = false
  }
}

const triggerReflection = async () => {
  isTriggering.value = true
  try {
    const res = await api.reflection.trigger(50, 'ALL')
    showFeedback(`反思任务已投递！会话ID: ${(res.data.session_id || '').substring(0, 12)}...`)
    // 刷新会话列表
    setTimeout(() => loadSessions(), 2000)
  } catch (e) {
    const detail = e.response?.data?.detail || '触发反思失败'
    showFeedback(detail, 'error')
  } finally {
    isTriggering.value = false
  }
}

const toggleRule = async (rule) => {
  try {
    await api.reflection.toggleRule(rule.rule_id, !rule.is_active)
    rule.is_active = !rule.is_active
    showFeedback(`规则已${rule.is_active ? '启用' : '禁用'}`)
  } catch (e) {
    showFeedback('切换规则状态失败', 'error')
  }
}

// Tab 切换时加载对应数据
watch(activeTab, (newTab) => {
  if (newTab === 'sessions') loadSessions()
  else if (newTab === 'rules') loadRules()
  else if (newTab === 'skills') loadSkills()
})

onMounted(() => {
  loadSessions()
})
</script>
