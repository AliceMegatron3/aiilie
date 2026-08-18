<template>
  <div class="h-full flex flex-col">
    <div class="px-6 pt-4 pb-3 border-b border-[#2a2a30] shrink-0">
      <h2 class="text-sm font-semibold text-gray-200">插件</h2>
      <div class="text-[10px] text-gray-600 mt-0.5">行为插件 · 技能候选 · 书库插件（资料扩展）</div>
    </div>

    <div class="flex-1 overflow-y-auto px-6 py-4">
      <!-- 行为插件(编辑通行) -->
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
      <div class="mb-4">
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

      <!-- 执行前能力解析（只读计划） -->
      <div class="mb-4 border-t border-[#2a2a30] pt-3">
        <div class="flex items-center gap-2 mb-2">
          <div class="text-xs text-gray-300">执行前能力解析</div>
          <button class="text-[10px] bg-[#2a2a30] hover:bg-gray-600 text-gray-300 rounded px-2 py-0.5" @click="loadCapabilities">刷新</button>
        </div>
        <div v-if="capabilities.length === 0" class="text-[10px] text-gray-600">暂无能力清单</div>
        <div v-for="p in capabilities" :key="p.plugin_id" class="border-b border-[#1f1f25] py-1.5">
          <div class="flex justify-between text-xs text-gray-300">
            <span>{{ p.plugin_id }} <span class="text-[9px] text-gray-600">{{ p.kind }}@{{ p.version }}</span></span>
            <span :class="p.executable ? 'text-emerald-400' : 'text-gray-600'">{{ p.executable ? '可执行' : '未信任' }}</span>
          </div>
          <div class="text-[10px] text-gray-500 mt-1">{{ (p.capabilities || []).join(' · ') || '无声明能力' }}</div>
        </div>
      </div>

      <!-- 书库插件（资料扩展） -->
      <div>
        <div class="flex items-center gap-2 mb-2">
          <div class="text-xs text-gray-300">书库插件（资料扩展）</div>
          <button class="text-[10px] bg-[#2a2a30] hover:bg-gray-600 text-gray-300 rounded px-2 py-0.5" @click="loadBookPlugins">刷新</button>
        </div>
        <div v-if="bookPlugins.length === 0" class="text-[10px] text-gray-600 py-2">暂无已安装的书库插件</div>
        <div v-for="p in bookPlugins" :key="p.plugin_id || p.id" class="border-b border-[#1f1f25] py-1.5 flex items-center justify-between">
          <div>
            <div class="text-xs text-gray-300">{{ p.name || p.id }}
              <span class="ml-1 text-[9px]" :class="statusClass(p.state || p.status || 'INSTALLED')">{{ p.state || p.status || 'INSTALLED' }}</span>
              <span class="ml-1 text-[9px] text-gray-600">v{{ p.version || '?' }}</span>
            </div>
            <div v-if="p.description" class="text-[10px] text-gray-500">{{ p.description }}</div>
          </div>
          <div class="flex gap-1">
            <button class="text-[10px] bg-red-900/30 hover:bg-red-800 text-red-400 rounded px-2 py-0.5" @click="uninstallPlugin(p.plugin_id || p.id)">卸载</button>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { api, unwrap } from '../../api'

const plugins = ref<any[]>([])
const candidates = ref<any[]>([])
const bookPlugins = ref<any[]>([])
const capabilities = ref<any[]>([])

const fmt = (v: any) => (v == null ? '—' : `${Math.round(v * 100)}%`)
const statusClass = (s: string) =>
  s === 'ACTIVE' ? 'text-emerald-400' : s === 'GRAY' ? 'text-amber-400' : s === 'CANDIDATE' ? 'text-gray-400' : 'text-gray-600'
const candStatusClass = (s: string) => s === 'FULL' ? 'text-emerald-400' : s === 'MANUAL_APPROVED' ? 'text-amber-400' : 'text-gray-500'

async function loadPlugins() {
  try {
    const res = await api.governance.plugins()
    plugins.value = unwrap<any>(res)?.plugins || []
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
    const raw = unwrap<any>(res)?.candidates ?? []
    candidates.value = raw || []
  } catch { candidates.value = [] }
}

async function promote(id: string) {
  try {
    await api.reflection.promoteSkillCandidate(id)
    await loadCandidates()
  } catch (e: any) { alert(e?.response?.data?.detail ?? '晋升失败') }
}

async function loadCapabilities() {
  try {
    const res = await api.plugins.capabilities()
    capabilities.value = res.data?.data || []
  } catch { capabilities.value = [] }
}

async function loadBookPlugins() {
  try {
    const res = await api.plugins.list()
    const all = res.data?.data || []
    bookPlugins.value = all.filter((p: any) => p.kind === 'library' || p.card_category === 'extend')
  } catch { bookPlugins.value = [] }
}

async function uninstallPlugin(id: string) {
  if (!confirm('确定卸载此插件？')) return
  try {
    await api.plugins.uninstall(id)
    await loadBookPlugins()
  } catch (e: any) { alert(e?.response?.data?.detail ?? '卸载失败') }
}

onMounted(() => { loadPlugins(); loadCandidates(); loadBookPlugins(); loadCapabilities() })
</script>