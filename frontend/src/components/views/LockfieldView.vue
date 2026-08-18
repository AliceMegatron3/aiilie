<template>
  <div class="h-full flex flex-col">
    <div class="px-6 pt-4 pb-3 border-b border-[#2a2a30] shrink-0 flex items-center gap-3">
      <h2 class="text-sm font-semibold text-gray-200">世界观锁定场 · 史实基线</h2>
      <select v-model="projectId" class="text-xs bg-[#121212] border border-[#3f3f46] rounded px-2 py-1 text-gray-200" @change="loadConfig">
        <option value="" disabled>选择项目</option>
        <option v-for="p in projects" :key="p.project_id" :value="p.project_id">{{ p.project_name }}</option>
      </select>
      <button class="text-xs bg-indigo-600/60 hover:bg-indigo-500 text-white rounded px-3 py-1" @click="saveConfig" v-if="projectId">保存配置</button>
      <button class="text-xs bg-[#2a2a30] hover:bg-gray-600 text-gray-300 rounded px-3 py-1" @click="loadMustSet" v-if="projectId">查看 must 集</button>
    </div>

    <div class="flex-1 overflow-y-auto px-6 py-4" v-if="projectId">
      <!-- 配置表单 -->
      <div class="mb-6 space-y-2">
        <div class="text-xs text-gray-400">锁定场名称</div>
        <input v-model="config.name" class="w-full bg-[#121212] border border-[#3f3f46] rounded px-2 py-1 text-sm text-gray-200" placeholder="如:乱世双璧·正史基底" />

        <div class="text-xs text-gray-400 mt-2">锁定库ID(逗号分隔,如:historical_materials)</div>
        <input v-model="lockedLibsText" class="w-full bg-[#121212] border border-[#3f3f46] rounded px-2 py-1 text-sm text-gray-200" />

        <div class="text-xs text-gray-400 mt-2">最低证据等级(A最强→D最弱,仅 B 及以上进 must)</div>
        <select v-model="config.type_rules.min_evidence" class="bg-[#121212] border border-[#3f3f46] rounded px-2 py-1 text-sm text-gray-200">
          <option>A</option><option>B</option><option>C</option><option>D</option>
        </select>
      </div>

      <!-- must集展示 -->
      <div v-if="mustSet" class="mb-6">
        <div class="text-xs text-gray-300 mb-1">物化 must 集</div>
        <div class="text-[11px] text-gray-500">共 {{ mustSet.total }} 张卡 · 指纹 {{ mustSet.digest }}</div>
        <div v-for="cid in (mustSet.card_ids||[]).slice(0,5)" :key="cid" class="text-[10px] text-gray-400 pl-2 truncate">{{ cid }}</div>
        <div v-if="(mustSet.card_ids||[]).length > 5" class="text-[10px] text-gray-600 pl-2">…余 {{ mustSet.card_ids.length - 5 }} 条</div>
      </div>

      <!-- 版本与回滚 -->
      <div class="mb-6">
        <div class="flex items-center gap-2 mb-1">
          <div class="text-xs text-gray-300">编年史(版本)</div>
          <button class="text-[10px] bg-[#2a2a30] hover:bg-gray-600 text-gray-300 rounded px-2 py-0.5" @click="loadVersions" v-if="projectId">刷新</button>
        </div>
        <div v-for="v in versions" :key="v.version" class="flex items-center gap-3 text-[11px] text-gray-400 py-0.5 border-b border-[#1f1f25]">
          <span class="text-gray-300">v{{ v.version }}</span>
          <span>{{ v.change_type }}</span>
          <span class="text-gray-600">{{ v.created_at?.slice(0,16) }}</span>
          <button class="text-[10px] text-amber-500/80 hover:text-amber-400" @click="rollback(v.version)">回滚</button>
        </div>
      </div>

      <!-- 偏离登记 -->
      <div>
        <div class="flex items-center gap-2 mb-1">
          <div class="text-xs text-gray-300">偏离登记(架空 vs 锁定史实)</div>
          <button class="text-[10px] bg-[#2a2a30] hover:bg-gray-600 text-gray-300 rounded px-2 py-0.5" @click="showNewDiv = !showNewDiv">登记</button>
        </div>
        <div v-if="showNewDiv" class="mb-2 space-y-1 text-xs">
          <input v-model="newDiv.description" placeholder="如:本书设定刘备早逝" class="w-full bg-[#121212] border border-[#3f3f46] rounded px-2 py-1 text-gray-200" />
          <button class="bg-amber-700/70 text-white rounded px-3 py-1" @click="proposeDiv">登记建议</button>
        </div>
        <div v-for="d in divergences" :key="d.divergence_id" class="flex items-center gap-3 text-[11px] text-gray-400 py-1 border-b border-[#1f1f25]">
          <span :class="d.status === 'CONFIRMED' ? 'text-emerald-500' : 'text-amber-500/80'">[{{ d.status }}]</span>
          <span>{{ d.description }}</span>
          <span v-if="d.chapter_number" class="text-gray-600">第{{ d.chapter_number }}章</span>
          <button v-if="d.status === 'PENDING'" class="text-[10px] text-emerald-500/80" @click="confirmDiv(d.divergence_id)">确认</button>
          <button v-if="d.status === 'PENDING'" class="text-[10px] text-red-500/60" @click="retractDiv(d.divergence_id)">驳回</button>
        </div>
      </div>
    </div>
    <div v-else class="flex-1 flex items-center justify-center text-gray-600 text-sm">请选择项目</div>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, watch } from 'vue'
import { api, unwrap } from '../../api'

const projects = ref<any[]>([])
const projectId = ref('')
const config = ref<any>({ name: '', locked_library_ids: [], type_rules: { min_evidence: 'B', require_rule_strength: 'hard', require_status: 'approved' } })
const lockedLibsText = ref('')
const mustSet = ref<any>(null)
const versions = ref<any[]>([])
const divergences = ref<any[]>([])
const showNewDiv = ref(false)
const newDiv = ref({ description: '' })

async function loadProjects() {
  try {
    const res = await api.projects.list()
    projects.value = res.data || []
  } catch { projects.value = [] }
}

async function loadConfig() {
  if (!projectId.value) return
  try {
    const res = await api.lockfield.get(projectId.value)
    const lf = unwrap<any>(res)?.lockfield
    if (lf) {
      config.value = lf
      lockedLibsText.value = (lf.locked_library_ids || []).join(',')
    } else {
      config.value = { name: '', locked_library_ids: [], type_rules: { min_evidence: 'B', require_rule_strength: 'hard', require_status: 'approved' } }
      lockedLibsText.value = ''
    }
    mustSet.value = null
    versions.value = []
    divergences.value = []
  } catch {}
}

async function saveConfig() {
  try {
    const cfg = { ...config.value, project_id: projectId.value }
    cfg.locked_library_ids = lockedLibsText.value.split(',').map(s => s.trim()).filter(Boolean)
    if (!cfg.name) cfg.name = '未命名锁定场'
    await api.lockfield.save(projectId.value, cfg)
    alert('锁定场配置已保存')
  } catch (e: any) { alert(e?.response?.data?.detail ?? '保存失败') }
}

async function loadMustSet() {
  try {
    const res = await api.lockfield.mustSet(projectId.value)
    mustSet.value = unwrap<any>(res)?.must_set
  } catch { mustSet.value = null }
}

async function loadVersions() {
  try {
    const res = await api.lockfield.versions(projectId.value)
    versions.value = unwrap<any>(res)?.versions || []
  } catch { versions.value = [] }
}

async function rollback(target_version: number) {
  if (!confirm(`确认回滚到版本 v${target_version}?`)) return
  try {
    await api.lockfield.rollback(projectId.value, target_version)
    await loadConfig()
    await loadVersions()
  } catch (e: any) { alert(e?.response?.data?.detail ?? '回滚失败') }
}

async function loadDivergences() {
  try {
    const res = await api.lockfield.listDivergences(projectId.value)
    divergences.value = unwrap<any>(res)?.divergences || []
  } catch { divergences.value = [] }
}

async function proposeDiv() {
  if (!newDiv.value.description) return
  try {
    await api.lockfield.proposeDivergence({ project_id: projectId.value, description: newDiv.value.description })
    newDiv.value.description = ''
    showNewDiv.value = false
    await loadDivergences()
  } catch (e: any) { alert(e?.response?.data?.detail ?? '登记失败') }
}

async function confirmDiv(id: string) {
  try { await api.lockfield.divergenceAction(id, 'confirm'); await loadDivergences() } catch {}
}
async function retractDiv(id: string) {
  try { await api.lockfield.divergenceAction(id, 'retract'); await loadDivergences() } catch {}
}

onMounted(loadProjects)
</script>