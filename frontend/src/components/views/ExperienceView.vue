<template>
  <div class="h-full flex flex-col">
    <div class="px-6 pt-4 pb-3 border-b border-[#2a2a30] shrink-0">
      <h2 class="text-sm font-semibold text-gray-200">经验域</h2>
      <p class="text-[10px] text-gray-600 mt-0.5">智能体总结的经验与人类审批</p>
    </div>

    <div class="px-6 py-4 flex gap-2 mb-3 shrink-0">
      <select v-model="statusFilter" class="bg-[#121212] border border-[#3f3f46] rounded px-2 py-1 text-xs text-gray-200 focus:outline-none focus:border-indigo-500">
        <option :value="null">全部</option>
        <option value="approved">已批准</option>
        <option value="pending">待审批</option>
        <option value="rejected">已拒绝</option>
      </select>
      <button @click="loadExperiences" class="text-xs bg-[#2a2a30] hover:bg-gray-600 text-gray-300 rounded px-2 py-1">刷新</button>
    </div>

    <div v-if="loading" class="text-xs text-gray-500 text-center py-8">加载中...</div>

    <div v-else-if="experiences.length === 0" class="text-xs text-gray-600 text-center py-8">
      暂无经验记录
    </div>

    <div v-else class="flex-1 overflow-y-auto px-6 pb-4 space-y-2">
      <div
        v-for="exp in experiences"
        :key="exp.id"
        class="p-3 rounded-lg border transition-all"
        :class="{
          'bg-[#18181b] border-[#2a2a30]': true,
          'border-emerald-800/50 bg-emerald-900/5': exp.status === 'approved',
          'border-amber-800/50 bg-amber-900/5': exp.status === 'pending',
        }"
      >
        <div class="flex items-start justify-between gap-3">
          <div class="flex-1">
            <div class="flex items-center gap-2 mb-1">
              <span class="text-[10px] font-medium text-gray-400 uppercase">{{ exp.exp_type }}</span>
              <span
                class="text-[9px] px-1.5 py-0.5 rounded"
                :class="{
                  'bg-emerald-900/30 text-emerald-400': exp.status === 'approved',
                  'bg-amber-900/30 text-amber-400': exp.status === 'pending',
                  'bg-red-900/30 text-red-400': exp.status === 'rejected',
                }"
              >{{ exp.status }}</span>
            </div>
            <p class="text-xs text-gray-300 leading-relaxed">{{ exp.content }}</p>
            <div class="text-[10px] text-gray-600 mt-1">{{ exp.created_at || '' }}</div>
          </div>
          <div v-if="exp.status === 'pending'" class="flex gap-1 shrink-0">
            <button
              @click="approve(exp.id, true)"
              class="text-[10px] bg-emerald-900/30 hover:bg-emerald-800/40 text-emerald-400 rounded px-2 py-0.5"
            >✓ 批准</button>
            <button
              @click="approve(exp.id, false)"
              class="text-[10px] bg-red-900/30 hover:bg-red-800/40 text-red-400 rounded px-2 py-0.5"
            >✗ 拒绝</button>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, watch } from 'vue'
import { api } from '../../api'

const experiences = ref<any[]>([])
const loading = ref(false)
const statusFilter = ref<string | null>(null)

const loadExperiences = async () => {
  loading.value = true
  try {
    const res = await api.experience.list(statusFilter.value)
    experiences.value = res.data?.data || []
  } catch (e) {
    console.error('加载经验失败', e)
  } finally {
    loading.value = false
  }
}

const approve = async (expId: string, approved: boolean) => {
  try {
    await api.experience.approve(expId, approved)
    await loadExperiences()
  } catch (e: any) {
    alert(e?.response?.data?.detail || '审批失败')
  }
}

onMounted(() => loadExperiences())
watch(statusFilter, () => loadExperiences())
</script>
