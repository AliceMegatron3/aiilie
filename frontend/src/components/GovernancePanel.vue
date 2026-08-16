<template>
  <div v-if="open" class="fixed inset-0 z-50 flex items-center justify-center bg-black/60" @click.self="$emit('close')">
    <div class="w-[720px] max-h-[80vh] overflow-y-auto rounded-lg border border-[#2a2a30] bg-[#18181b] shadow-2xl">
      <div class="sticky top-0 flex items-center justify-between border-b border-[#2a2a30] bg-[#18181b] px-5 py-3">
        <h2 class="text-sm font-semibold text-zinc-200">治理面板 · 插件 / 弧线 / 群像</h2>
        <button class="rounded px-2 py-1 text-xs text-zinc-400 hover:bg-[#2a2a30] hover:text-zinc-200" @click="$emit('close')">关闭</button>
      </div>
      <div class="flex gap-1 border-b border-[#2a2a30] px-4 pt-3">
        <button v-for="t in tabs" :key="t.key" class="rounded-t px-3 py-1.5 text-xs"
          :class="tab === t.key ? 'bg-[#2a2a30] text-zinc-100' : 'text-zinc-500 hover:text-zinc-300'"
          @click="tab = t.key">{{ t.label }}</button>
      </div>

      <!-- 插件治理 -->
      <div v-if="tab === 'plugins'" class="space-y-2 p-4">
        <div class="flex items-center gap-2">
          <button class="rounded bg-[#2a2a30] px-3 py-1 text-xs text-zinc-200 hover:bg-[#3a3a40]" @click="loadPlugins">刷新</button>
          <span class="text-[11px] text-zinc-500">验收率=机器自评;作者保留率=真信号(确认/改稿回流)</span>
        </div>
        <div v-for="p in plugins" :key="p.plugin_id" class="rounded border border-[#2a2a30] bg-[#121212] px-3 py-2">
          <div class="flex items-center justify-between">
            <div class="text-xs text-zinc-200">
              {{ p.name }}
              <span class="ml-2 rounded px-1.5 py-0.5 text-[10px]" :class="statusClass(p.status)">{{ p.status }}</span>
              <span class="ml-1 text-[10px] text-zinc-500">{{ p.source }}</span>
            </div>
            <div class="flex gap-1">
              <button class="rounded bg-[#2a2a30] px-2 py-0.5 text-[10px] text-zinc-300 hover:bg-[#3a3a40]" @click="setStatus(p, 'GRAY', 30)">灰度30</button>
              <button class="rounded bg-[#2a2a30] px-2 py-0.5 text-[10px] text-zinc-300 hover:bg-[#3a3a40]" @click="setStatus(p, 'ACTIVE')">转正</button>
              <button class="rounded bg-[#2a2a30] px-2 py-0.5 text-[10px] text-zinc-400 hover:bg-[#3a3a40]" @click="setStatus(p, 'RETIRED')">退役</button>
            </div>
          </div>
          <div class="mt-1 text-[11px] text-zinc-500">
            验收率 {{ fmt(p.stats?.acceptance_rate) }} · 命中 {{ p.stats?.triggered ?? 0 }} 次
            · 均耗 {{ p.stats?.avg_duration_ms ?? 0 }}ms
            <span v-if="p.stats?.author_retention != null" class="text-emerald-400/80"> · 作者保留率 {{ fmt(p.stats.author_retention) }}</span>
            <span v-if="p.stats?.signal_tasks != null" class="text-zinc-600"> ({{ p.stats.signal_tasks }} 个确认样本)</span>
            <span v-if="p.stats?.insufficient_sample" class="ml-1 text-amber-400/80">样本不足,勿作结论</span>
          </div>
        </div>
      </div>

      <!-- 弧线套用 -->
      <div v-if="tab === 'arcs'" class="space-y-3 p-4">
        <div class="grid grid-cols-3 gap-2 text-xs">
          <input v-model="arcForm.project_id" placeholder="项目ID" class="rounded border border-[#2a2a30] bg-[#121212] px-2 py-1 text-zinc-200" />
          <input v-model="arcForm.volume_id" placeholder="卷ID" class="rounded border border-[#2a2a30] bg-[#121212] px-2 py-1 text-zinc-200" />
          <input v-model.number="arcForm.n_chapters" type="number" min="1" placeholder="章数" class="rounded border border-[#2a2a30] bg-[#121212] px-2 py-1 text-zinc-200" />
        </div>
        <select v-model="arcForm.pattern_id" class="w-full rounded border border-[#2a2a30] bg-[#121212] px-2 py-1 text-xs text-zinc-200">
          <option value="" disabled>选择弧线模式</option>
          <option v-for="a in arcs" :key="a.pattern_id" :value="a.pattern_id">{{ a.name }}({{ a.stages.map(s => s.name).join('→') }})</option>
        </select>
        <div class="flex gap-2">
          <button class="rounded bg-[#2a2a30] px-3 py-1 text-xs text-zinc-200 hover:bg-[#3a3a40]" @click="previewArc">预览预算序列</button>
          <button class="rounded bg-emerald-700/80 px-3 py-1 text-xs text-white hover:bg-emerald-600" :disabled="!arcPreview" :class="{ 'opacity-40 cursor-not-allowed': !arcPreview }" @click="applyArc">确认套用到卷</button>
        </div>
        <div v-if="arcPreview" class="rounded border border-[#2a2a30] bg-[#121212] p-2 text-[11px] text-zinc-400">
          预览「{{ arcPreview.name }}」共 {{ arcPreview.n_chapters }} 章(套用会覆盖这些章的预算与弧线绑定,不动已确认拍纲):
          <div class="mt-1 text-zinc-300">{{ arcPreview.sequence.map(s => `${s.index}:${s.stage}/${s.conflict_intensity}`).join('  ') }}</div>
        </div>
        <div v-if="arcResult" class="rounded border border-emerald-800/40 bg-emerald-950/20 p-2 text-[11px] text-emerald-200/90">
          已派生 {{ arcResult.applied }} 章 · 阶段:
          <span>{{ arcResult.chapters.map(c => `${c.chapter_number}章:${c.arc_stage}`).join(', ') }}</span>
        </div>
        <div class="border-t border-[#2a2a30] pt-3">
          <button class="rounded bg-[#2a2a30] px-3 py-1 text-xs text-zinc-200 hover:bg-[#3a3a40]" @click="loadVariance">查弧线偏差(配方 vs 实际)</button>
          <div v-if="variance" class="mt-2 space-y-1 text-[11px] text-zinc-400">
            <div>
              绑定 {{ variance.bound_chapters }} 章 · 已测 {{ variance.measured_chapters }} 章 ·
              达成率 {{ variance.achievement_rate == null ? '—' : fmt(variance.achievement_rate) }}
            </div>
            <div v-for="s in variance.stages" :key="s.stage" class="text-zinc-500">
              {{ s.stage }}:目标均值 {{ s.avg_target }} / 实际均值 {{ s.avg_actual ?? '—' }} · 覆盖 {{ fmt(s.coverage) }}
            </div>
            <div v-if="offTargetItems.length" class="text-amber-400/80">
              偏离章:{{ offTargetItems.map(i => `${i.chapter_number}章(${i.delta > 0 ? '+' : ''}${i.delta})`).join(', ') }}
            </div>
            <div class="text-zinc-600">{{ variance.note }}</div>
          </div>
        </div>
      </div>

      <!-- 群像回摆 -->
      <div v-if="tab === 'ensemble'" class="space-y-3 p-4">
        <div class="flex gap-2 text-xs">
          <input v-model="ensForm.project_id" placeholder="项目ID" class="flex-1 rounded border border-[#2a2a30] bg-[#121212] px-2 py-1 text-zinc-200" />
          <button class="rounded bg-[#2a2a30] px-3 py-1 text-zinc-200 hover:bg-[#3a3a40]" @click="loadRelations">查关系账本</button>
        </div>
        <div v-if="tracks.length" class="space-y-1">
          <div class="text-[11px] text-zinc-500">生活轨道(在场识别与矛盾巡检的匹配来源)</div>
          <div v-for="t in tracks" :key="t.character_id" class="rounded border border-[#2a2a30] bg-[#121212] px-3 py-1.5 text-[11px] text-zinc-400">
            <span class="text-zinc-300">{{ t.name || t.character_id }}</span>
            <span class="ml-2">第{{ t.updated_chapter }}章态</span>
            <span v-if="t.position" class="ml-2">位置:{{ t.position }}</span>
            <span v-if="t.needs?.length" class="ml-2">需求:{{ t.needs.join('/') }}</span>
          </div>
        </div>
        <div v-for="r in relations" :key="r.pair" class="rounded border border-[#2a2a30] bg-[#121212] px-3 py-1.5 text-[11px]">
          <span class="text-zinc-300">{{ r.pair.replace('|', ' × ') }}</span>
          <span class="ml-2" :class="r.current >= 20 ? 'text-emerald-400/80' : r.current <= -20 ? 'text-red-400/80' : 'text-zinc-500'">
            {{ r.label }} ({{ r.current > 0 ? '+' : '' }}{{ r.current }})
          </span>
          <span class="ml-2 text-zinc-600">基线{{ r.baseline }} 挂账{{ r.active_event_deltas > 0 ? '+' : '' }}{{ r.active_event_deltas }}</span>
        </div>
        <div class="flex gap-2 border-t border-[#2a2a30] pt-3 text-xs">
          <input v-model="ensForm.event_id" placeholder="事件ID(清算用)" class="flex-1 rounded border border-[#2a2a30] bg-[#121212] px-2 py-1 text-zinc-200" />
          <button class="rounded bg-amber-700/80 px-3 py-1 text-white hover:bg-amber-600" @click="closeEvent">事件清算(按深浅回摆)</button>
        </div>
        <div v-if="reminders.length" class="space-y-1">
          <div v-for="(rm, i) in reminders" :key="i" class="rounded border border-amber-800/40 bg-amber-950/30 px-3 py-1.5 text-[11px] text-amber-200/90">{{ rm }}</div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { api } from '../api'

defineProps<{ open: boolean }>()
defineEmits(['close'])

const tabs = [
  { key: 'plugins', label: '插件治理' },
  { key: 'arcs', label: '弧线套用' },
  { key: 'ensemble', label: '群像回摆' },
]
const tab = ref('plugins')

const plugins = ref<any[]>([])
const arcs = ref<any[]>([])
const arcPreview = ref<any>(null)
const variance = ref<any>(null)
const offTargetItems = ref<any[]>([])
const relations = ref<any[]>([])
const tracks = ref<any[]>([])
const reminders = ref<string[]>([])
const arcResult = ref<any>(null)
const arcForm = ref({ project_id: '', volume_id: '', pattern_id: '', n_chapters: 12 })
const ensForm = ref({ project_id: '', event_id: '' })

const fmt = (v: any) => (v == null ? '—' : `${Math.round(v * 100)}%`)
const statusClass = (s: string) =>
  s === 'ACTIVE' ? 'bg-emerald-900/60 text-emerald-300'
  : s === 'GRAY' ? 'bg-amber-900/60 text-amber-300'
  : s === 'CANDIDATE' ? 'bg-zinc-700/60 text-zinc-300'
  : 'bg-zinc-800/60 text-zinc-500'

async function loadPlugins() {
  try {
    const res = await api.governance.plugins()
    plugins.value = res.data?.plugins ?? []
  } catch { /* 面板静默 */ }
}
async function setStatus(p: any, status: string, gray = 0) {
  try {
    await api.governance.setPluginStatus(p.plugin_id, status, gray)
    await loadPlugins()
  } catch (e: any) {
    alert(e?.response?.data?.detail ?? '治理操作失败')
  }
}
async function loadArcs() {
  try {
    const res = await api.governance.arcs()
    arcs.value = res.data?.arcs ?? []
  } catch { /* 面板静默 */ }
}
async function previewArc() {
  const { pattern_id, n_chapters } = arcForm.value
  if (!pattern_id) return alert('请选择弧线模式')
  try {
    const res = await api.governance.previewArc(pattern_id, n_chapters)
    arcPreview.value = res.data
    arcResult.value = null
  } catch (e: any) {
    alert(e?.response?.data?.detail ?? '预览失败')
  }
}
async function applyArc() {
  const { project_id, volume_id, pattern_id, n_chapters } = arcForm.value
  if (!project_id || !volume_id || !pattern_id) return alert('请填写项目/卷/弧线')
  if (!arcPreview.value) return alert('请先预览预算序列再确认套用')
  try {
    const res = await api.governance.applyArc(project_id, volume_id, { pattern_id, n_chapters })
    arcResult.value = res.data
    arcPreview.value = null
  } catch (e: any) {
    alert(e?.response?.data?.detail ?? '套用失败')
  }
}
async function loadVariance() {
  const { project_id, volume_id } = arcForm.value
  if (!project_id) return alert('请填写项目ID')
  try {
    const res = await api.governance.arcVariance(project_id, volume_id || undefined)
    variance.value = res.data
    offTargetItems.value = (res.data?.items ?? []).filter((i: any) => i.flag !== 'on_target')
  } catch (e: any) {
    variance.value = null
    offTargetItems.value = []
  }
}
async function loadRelations() {
  if (!ensForm.value.project_id) return
  try {
    const res = await api.governance.ensembleRelations(ensForm.value.project_id)
    relations.value = res.data?.relationships ?? []
  } catch { relations.value = [] }
  try {
    const res = await api.governance.ensembleTracks(ensForm.value.project_id)
    tracks.value = res.data?.tracks ?? []
  } catch { tracks.value = [] }
}
async function closeEvent() {
  const { project_id, event_id } = ensForm.value
  if (!project_id || !event_id) return alert('请填写项目与事件ID')
  try {
    const res = await api.governance.ensembleCloseEvent(project_id, event_id)
    reminders.value = res.data?.reminders ?? []
    await loadRelations()
  } catch { reminders.value = [] }
}

onMounted(() => { loadPlugins(); loadArcs() })
</script>
