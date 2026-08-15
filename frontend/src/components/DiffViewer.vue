<template>
  <div class="w-full bg-[#1c1c20] border border-gray-700 rounded-lg flex flex-col overflow-hidden">
    <!-- 头部：分支选择 + 操作 -->
    <div class="flex items-center justify-between px-4 py-2.5 border-b border-[#2a2a30] shrink-0 flex-wrap gap-2">
      <div class="flex items-center gap-2 text-xs">
        <span class="text-gray-400">分支差异对比</span>
        <select v-model="branchA" class="bg-[#2a2a30] border border-gray-700 rounded px-2 py-1 text-gray-300 focus:outline-none">
          <option v-for="b in branches" :key="b.branch_id" :value="b.branch_id">{{ b.name }}{{ b.is_archived ? ' (已归档)' : '' }}</option>
        </select>
        <span class="text-gray-600">vs</span>
        <select v-model="branchB" class="bg-[#2a2a30] border border-gray-700 rounded px-2 py-1 text-gray-300 focus:outline-none">
          <option v-for="b in branches" :key="b.branch_id" :value="b.branch_id">{{ b.name }}{{ b.is_archived ? ' (已归档)' : '' }}</option>
        </select>
        <button class="bg-indigo-600 hover:bg-indigo-500 text-white px-2.5 py-1 rounded transition-colors" @click="loadDiff">对比</button>
      </div>
      <div class="flex items-center gap-1.5 text-[10px] text-gray-500">
        <span class="px-1.5 rounded bg-emerald-900/50 text-emerald-400">+ {{ statInserts }}</span>
        <span class="px-1.5 rounded bg-red-900/50 text-red-400">- {{ statDeletes }}</span>
        <span class="px-1.5 rounded bg-slate-800 text-slate-400">= {{ statEquals }}</span>
      </div>
    </div>

    <!-- 差异主体 -->
    <div class="flex-1 overflow-auto max-h-96 font-mono text-xs leading-relaxed">
      <div v-if="loading" class="p-4 text-gray-500">加载差异中...</div>
      <div v-else-if="lineDiffs.length === 0" class="p-4 text-gray-500">两分支内容完全一致</div>
      <div v-else>
        <div
          v-for="(d, i) in lineDiffs"
          :key="i"
          class="flex border-b border-[#1f1f24]/60"
          :class="lineClass(d[0])"
        >
          <span class="w-8 shrink-0 text-right pr-2 select-none text-gray-600">{{ i + 1 }}</span>
          <span class="w-6 shrink-0 text-center select-none">{{ opMark(d[0]) }}</span>
          <span class="whitespace-pre-wrap break-all flex-1">{{ d[1] }}</span>
        </div>
      </div>
    </div>

    <!-- 分支管理操作 -->
    <div class="px-4 py-2.5 border-t border-[#2a2a30] flex items-center gap-2 shrink-0 flex-wrap">
      <button class="text-[10px] bg-[#2a2a30] hover:bg-gray-600 text-gray-300 px-2.5 py-1.5 rounded border border-gray-700 transition-colors" @click="openCreateBranch">+ 新分支</button>
      <button class="text-[10px] bg-[#2a2a30] hover:bg-gray-600 text-gray-300 px-2.5 py-1.5 rounded border border-gray-700 transition-colors" @click="switchBranch">切换当前时间线</button>
      <template v-if="canArchive">
        <button class="text-[10px] bg-amber-950/60 hover:bg-amber-900 text-amber-400 px-2.5 py-1.5 rounded border border-amber-900/60 transition-colors" @click="archiveBranch">归档</button>
      </template>
      <button v-if="archivedBranches.length" class="text-[10px] bg-[#2a2a30] hover:bg-gray-600 text-gray-300 px-2.5 py-1.5 rounded border border-gray-700 transition-colors" @click="showArchived = !showArchived">归档列表 ({{ archivedBranches.length }})</button>
      <div v-if="showArchived" class="w-full mt-1 flex flex-wrap gap-1.5">
        <span v-for="b in archivedBranches" :key="b.branch_id" class="text-[10px] px-2 py-1 rounded bg-slate-800 text-gray-400 flex items-center gap-1.5">
          {{ b.name }}
          <button class="text-emerald-400 hover:text-emerald-300" @click="unarchiveBranch(b)">恢复</button>
        </span>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { toast } from '../utils/toast'
import { api } from '../api'

const props = defineProps({
  docId: { type: String, required: true },
})

const branches = ref([])
const archivedBranches = ref([])
const branchA = ref('')
const branchB = ref('')
const lineDiffs = ref([])
const loading = ref(false)
const showArchived = ref(false)
const currentBranchId = ref('main')

const statInserts = computed(() => lineDiffs.value.filter(d => d[0] === 1).length)
const statDeletes = computed(() => lineDiffs.value.filter(d => d[0] === -1).length)
const statEquals = computed(() => lineDiffs.value.filter(d => d[0] === 0).length)
const canArchive = computed(() => branchA.value && branchA.value !== 'main' && !branches.value.find(b => b.branch_id === branchA.value)?.is_archived)

const lineClass = (op) => op === 1 ? 'bg-emerald-950/40 text-emerald-300' : op === -1 ? 'bg-red-950/40 text-red-300' : 'text-gray-400'
const opMark = (op) => op === 1 ? '+' : op === -1 ? '-' : ''

async function loadBranches() {
  try {
    const res = await api.branch.list(props.docId)
    branches.value = res.data?.data || []
    currentBranchId.value = res.data?.current_branch_id || 'main'
    if (branches.value.length >= 2) {
      branchA.value = branches.value[0].branch_id
      branchB.value = branches.value[1].branch_id
    } else if (branches.value.length === 1) {
      branchA.value = branches.value[0].branch_id
      branchB.value = branches.value[0].branch_id
    }
    const archRes = await api.branch.archived(props.docId)
    archivedBranches.value = archRes.data?.data || []
    await loadDiff()
  } catch (e) {
    // 分支系统未启用（feature 关闭）时静默降级
    branches.value = [{ branch_id: 'main', name: 'main', is_archived: false }]
  }
}

async function loadDiff() {
  if (!branchA.value || !branchB.value) return
  loading.value = true
  try {
    const res = await api.branch.diff(props.docId, branchA.value, branchB.value)
    lineDiffs.value = res.data?.data?.line_diff || []
  } catch (e) {
    toast.error('差异对比失败：' + (e.response?.data?.detail || e.message))
  } finally {
    loading.value = false
  }
}

async function openCreateBranch() {
  const name = await toast.prompt('新分支名称')
  if (!name) return
  try {
    await api.branch.create(props.docId, name, currentBranchId.value)
    toast.success('平行宇宙分支已创建')
    await loadBranches()
  } catch (e) {
    toast.error('创建失败：' + (e.response?.data?.detail || e.message))
  }
}

async function switchBranch() {
  if (!branchA.value) return
  await api.branch.switch(props.docId, branchA.value)
  toast.success(`已切换至分支 ${branchA.value}`)
  await loadBranches()
}

async function archiveBranch() {
  const ok = await toast.confirm('确认归档该分支？（分支将只读保留，可随时恢复）')
  if (!ok) return
  await api.branch.archive(props.docId, branchA.value)
  toast.success('分支已归档')
  await loadBranches()
}

async function unarchiveBranch(b) {
  await api.branch.unarchive(props.docId, b.branch_id)
  toast.success('分支已恢复')
  await loadBranches()
}

onMounted(loadBranches)
</script>
