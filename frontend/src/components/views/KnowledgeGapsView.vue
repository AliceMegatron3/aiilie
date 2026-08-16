<template>
  <div class="h-full flex flex-col">
    <div class="px-6 pt-4 pb-3 border-b border-[#2a2a30] shrink-0 flex items-center gap-3">
      <h2 class="text-sm font-semibold text-gray-200">知识缺口与补全</h2>
      <select v-model="projectId" class="text-xs bg-[#121212] border border-[#3f3f46] rounded px-2 py-1 text-gray-200">
        <option value="">全部项目</option>
        <option v-for="p in projects" :key="p.project_id" :value="p.project_id">{{ p.project_name }}</option>
      </select>
      <button class="text-xs bg-[#2a2a30] hover:bg-gray-600 text-gray-300 rounded px-3 py-1" @click="loadGaps">刷新缺口</button>
    </div>

    <div class="flex-1 overflow-y-auto px-6 py-4">
      <!-- 缺口聚账 -->
      <div class="mb-6">
        <div class="text-xs text-gray-300 mb-2">检索未命中缺口(累计 {{ gaps.total_misses || 0 }} 次)</div>
        <div v-for="g in (gaps.projects || [])" :key="g.project_id" class="border-b border-[#1f1f25] py-1.5">
          <div class="text-[11px] text-gray-300">{{ g.project_id.slice(0,12) }} · 未命中 {{ g.miss_count }} 次</div>
          <div v-for="q in (g.queries || [])" :key="q" class="text-[10px] text-gray-500 pl-2 truncate">「{{ q }}」</div>
        </div>
      </div>

      <!-- 补全建议 -->
      <div class="mb-6">
        <div class="text-xs text-gray-300 mb-2">补全建议(PENDING)</div>
        <div v-for="p in proposals" :key="p.proposal_id" class="text-[11px] text-gray-400 py-0.5 border-b border-[#1f1f25]">
          {{ p.topic }}<span v-if="p.description" class="text-gray-600 ml-1">— {{ p.description }}</span>
        </div>
      </div>

      <!-- 手动补全 -->
      <div class="border border-[#2a2a30] rounded p-3">
        <div class="text-xs text-gray-300 mb-2">发起补全(LLM降级→draft卡待审)</div>
        <div class="space-y-2">
          <input v-model="completeForm.topic" placeholder="要补全的主题,如:东汉官制" class="w-full bg-[#121212] border border-[#3f3f46] rounded px-2 py-1 text-sm text-gray-200" />
          <input v-model="completeForm.description" placeholder="补充说明(可选)" class="w-full bg-[#121212] border border-[#3f3f46] rounded px-2 py-1 text-sm text-gray-200" />
          <button class="bg-emerald-700/70 hover:bg-emerald-600 text-white rounded px-3 py-1 text-sm" @click="doComplete">执行补全</button>
        </div>
        <div v-if="completeResult" class="mt-2 text-xs">
          <div class="text-emerald-500">已生成 draft 卡({{ completeResult.card_id }})</div>
          <div class="text-gray-400 mt-1">Needs author review: {{ completeResult.needs_author_review }}</div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { api } from '../../api'

const projects = ref<any[]>([])
const projectId = ref('')
const gaps = ref<any>({})
const proposals = ref<any[]>([])
const completeForm = ref({ topic: '', description: '' })
const completeResult = ref<any>(null)

async function loadProjects() {
  try {
    const res = await api.projects.list()
    projects.value = res.data || []
  } catch { projects.value = [] }
}

async function loadGaps() {
  try {
    const res = await api.knowledge.gaps(projectId.value || undefined)
    gaps.value = res.data || {}
    const pr = await api.knowledge.proposals(projectId.value || undefined)
    proposals.value = pr.data?.proposals || []
  } catch {}
}

async function doComplete() {
  const pid = projectId.value
  if (!pid || !completeForm.value.topic) { alert('请选择一个项目并填写主题'); return }
  try {
    const res = await api.knowledge.complete(pid, completeForm.value.topic, completeForm.value.description)
    completeResult.value = res.data
    completeForm.value.topic = ''
    completeForm.value.description = ''
  } catch (e: any) { alert(e?.response?.data?.detail ?? '补全失败') }
}

onMounted(() => { loadProjects() })
</script>