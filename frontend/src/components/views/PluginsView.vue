<template>
  <div class="h-full flex flex-col">
    <div class="px-6 pt-4 pb-3 border-b border-[#2a2a30] shrink-0">
      <h2 class="text-sm font-semibold text-gray-200">插件</h2>
      <div class="text-[10px] text-gray-600 mt-0.5">后端插件注册表 + 行为插件治理(治理面板右侧浮动入口)</div>
    </div>

    <div class="flex-1 overflow-y-auto px-6 py-4">
      <div class="mb-4">
        <div class="flex items-center gap-2 mb-2">
          <div class="text-xs text-gray-300">行为插件(编辑通行)</div>
          <button class="text-[10px] bg-[#2a2a30] hover:bg-gray-600 text-gray-300 rounded px-2 py-0.5" @click="loadPlugins">刷新</button>
        </div>
        <div v-for="p in plugins" :key="p.plugin_id" class="border-b border-[#1f1f25] py-1.5 flex items-center justify-between">
          <div>
            <div class="text-xs text-gray-300">{{ p.name }}
              <span class="ml-1 text-[9px]" :class="statusClass(p.status)">{{ p.status }}</span>
              <span class="ml-1 text-[9px] text-gray-600">{{ p.source }}</span>
            </div>
            <div v-if="p.stats" class="text-[10px] text-gray-500">验收率 {{ fmt(p.stats.acceptance_rate) }} · 命中 {{ p.stats.triggered ?? 0 }} 次</div>
          </div>
          <div class="flex gap-1">
            <button class="text-[10px] bg-[#2a2a30] hover:bg-gray-600 text-gray-300 rounded px-2 py-0.5" @click="setStatus(p, 'GRAY', 30)">灰度30</button>
            <button class="text-[10px] bg-[#2a2a30] hover:bg-gray-600 text-gray-300 rounded px-2 py-0.5" @click="setStatus(p, 'ACTIVE')">转正</button>
            <button class="text-[10px] bg-[#2a2a30] hover:bg-gray-600 text-gray-500 rounded px-2 py-0.5" @click="setStatus(p, 'RETIRED')">退役</button>
          </div>
        </div>
      </div>

      <!-- 技能候选治理 -->
      <div>
        <div class="flex items-center gap-2 mb-2">
          <div class="text-xs text-gray-300">技能候选治理(反思/量化产物)</div>
          <button class="text-[10px] bg-[#2a2a30] hover:bg-gray-600 text-gray-300 rounded px-2 py-0.5" @click="loadCandidates">刷新</button>
        </div>
        <div v-for="c in candidates" :key="c.candidate_id" class="border-b border-[#1f1f25] py-1.5 flex items-center justify-between">
          <div class="text-xs text-gray-300">{{ c.name }}
            <span class="ml-1 text-[9px]" :class="candStatusClass(c.status)">{{ c.status }}</span>
            <span class="ml-1 text-[9px] text-gray-600">置信{{ (c.confidence || 0).toFixed(2) }}</span>
          </div>
          <div class="flex gap-1">
            <button class="text-[10px] bg-[#2a2a30] hover:bg-gray-600 text-gray-300 rounded px-2 py-0.5" @click="promote(c.candidate_id)">晋升</button>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { api } from '../../api'

const plugins = ref<any[]>([])
const candidates = ref<any[]>([])

const fmt = (v: any) => (v == null ? '—' : `${Math.round(v * 100)}%`)
const statusClass = (s: string) =>
  s === 'ACTIVE' ? 'text-emerald-400' : s === 'GRAY' ? 'text-amber-400' : s === 'CANDIDATE' ? 'text-gray-400' : 'text-gray-600'
const candStatusClass = (s: string) => s === 'FULL' ? 'text-emerald-400' : s === 'MANUAL_APPROVED' ? 'text-amber-400' : 'text-gray-500'

async function loadPlugins() {
  try {
    const res = await api.governance.plugins()
    plugins.value = res.data?.plugins || []
  } catch { plugins.value = [] }
}

async function setStatus(p: any, status: string, gray = 0) {
  try {
    await api.governance.setPluginStatus(p.plugin_id, status, gray)
    await loadPlugins()
  } catch (e: any) { alert(e?.response?.data?.detail ?? '操作失败') }
}

async function loadCandidates() {
  try {
    const res = await api.reflection.skillCandidates()
    // 兼容 {"candidates":[...]} 与 list 包裹
    const raw = res.data?.candidates ?? res.data?.data ?? []
    candidates.value = raw || []
  } catch { candidates.value = [] }
}

async function promote(id: string) {
  try {
    await api.reflection.promoteSkillCandidate(id)
    await loadCandidates()
  } catch (e: any) { alert(e?.response?.data?.detail ?? '晋升失败') }
}

onMounted(() => { loadPlugins(); loadCandidates() })
</script>