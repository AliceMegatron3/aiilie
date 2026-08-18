<template>
  <div class="h-full flex flex-col bg-[#121212] text-gray-200">
    <div class="px-6 pt-4 pb-3 border-b border-[#2a2a30]">
      <h2 class="text-sm font-semibold">知识 Ledger 与法则库</h2>
      <p class="text-[10px] text-gray-500 mt-1">来源证据、量化指标和作者法则草稿；此页不会生成正文。</p>
    </div>

    <div class="px-6 py-3 flex gap-2 border-b border-[#1f1f25]">
      <button v-for="tab in tabs" :key="tab.key" @click="activeTab = tab.key; load()"
        class="text-xs px-3 py-1 rounded"
        :class="activeTab === tab.key ? 'bg-indigo-600 text-white' : 'bg-[#1e1e24] text-gray-400'">
        {{ tab.label }}
      </button>
      <button class="ml-auto text-xs text-gray-500 hover:text-gray-300" @click="load">刷新</button>
    </div>

    <div class="flex-1 overflow-y-auto px-6 py-4">
      <div v-if="loading" class="text-xs text-gray-500 text-center py-8">加载中...</div>
      <div v-else-if="activeTab !== 'laws' && items.length === 0" class="text-xs text-gray-600 text-center py-8">暂无记录</div>
      <div v-else-if="activeTab === 'laws'" class="mb-4 border border-amber-900/40 rounded-lg p-3 bg-[#18181b]">
        <div class="flex items-center justify-between mb-2">
          <div class="text-xs text-amber-300">作者上帝层：法则分支</div>
          <button class="text-[10px] text-gray-500 hover:text-gray-300" @click="loadBranches">刷新分支</button>
        </div>
        <div class="grid grid-cols-1 md:grid-cols-3 gap-2">
          <input v-model="branchForm.branch_id" placeholder="分支 ID，如 world-main" class="bg-[#121212] border border-[#3f3f46] rounded px-2 py-1 text-xs text-white">
          <input v-model="branchForm.name" placeholder="分支名称" class="bg-[#121212] border border-[#3f3f46] rounded px-2 py-1 text-xs text-white">
          <button class="text-xs bg-amber-700/70 hover:bg-amber-600 text-white rounded px-2 py-1" @click="createBranch">创建分支</button>
        </div>
        <div v-if="branches.length" class="mt-3 flex flex-wrap gap-2">
          <button v-for="branch in branches" :key="branch.branch_id" @click="selectBranch(branch)"
            class="text-left text-xs rounded border px-2 py-1"
            :class="selectedBranch?.branch_id === branch.branch_id ? 'border-amber-500 bg-amber-900/30 text-amber-200' : 'border-[#3f3f46] text-gray-400'">
            {{ branch.name }} <span class="text-[10px] text-gray-600">r{{ branch.current_revision }}</span>
          </button>
        </div>
        <div v-if="selectedBranch" class="mt-3 border-t border-[#2a2a30] pt-3">
          <div class="text-[10px] text-gray-500 mb-2">当前分支：{{ selectedBranch.name }} · 历史 revision（只能新增，不能覆盖）</div>
          <div class="space-y-1 mb-3">
            <div v-for="revision in revisions" :key="`${revision.branch_id}:${revision.revision}`" class="flex items-center justify-between text-[10px] text-gray-400 bg-[#121212] rounded px-2 py-1">
              <span>r{{ revision.revision }} · {{ revision.operation }} · {{ revision.note || '无备注' }}</span>
              <button v-if="revision.revision > 0" class="text-amber-400 hover:text-amber-300" @click="rollbackTo(revision)">生成回滚</button>
            </div>
          </div>
          <textarea v-model="overrideJson" class="w-full min-h-[70px] bg-[#121212] border border-[#3f3f46] rounded p-2 text-[11px] text-gray-300 font-mono" placeholder='{"qi_density": 3}'></textarea>
          <div class="flex gap-2 mt-2">
            <input v-model="overrideNote" placeholder="覆盖说明（必填建议）" class="flex-1 bg-[#121212] border border-[#3f3f46] rounded px-2 py-1 text-xs text-white">
            <button class="text-xs bg-indigo-600 hover:bg-indigo-500 text-white rounded px-3 py-1" @click="appendOverride">提交新覆盖</button>
          </div>
        </div>
      </div>

      <div v-else-if="items.length === 0" class="text-xs text-gray-600 text-center py-8">暂无记录</div>
      <div v-else class="space-y-2">
        <div v-for="item in items" :key="item.document_id || item.evidence_id || item.metric_id || `${item.law_id}:${item.version}`"
          class="border border-[#2a2a30] rounded-lg p-3 bg-[#18181b]">
          <template v-if="activeTab === 'documents'">
            <div class="flex justify-between"><span class="text-xs text-indigo-300">{{ item.document_id }}</span><span class="text-[10px] text-gray-600">{{ item.status }}</span></div>
            <div class="text-[11px] text-gray-400 mt-1">{{ item.title || '未命名文档' }}</div>
            <div class="text-[10px] text-gray-600 mt-1">{{ item.content_hash }}</div>
          </template>
          <template v-else-if="activeTab === 'evidence'">
            <div class="flex justify-between"><span class="text-xs text-emerald-300">{{ item.document_id }}</span><span class="text-[10px] text-gray-500">{{ item.evidence_level }}</span></div>
            <div class="text-[11px] text-gray-400 mt-1">{{ item.quote || '无引用片段' }}</div>
            <div class="text-[10px] text-gray-600 mt-1">{{ item.passage_id }} · confidence {{ Number(item.confidence || 0).toFixed(2) }}</div>
          </template>
          <template v-else-if="activeTab === 'metrics'">
            <div class="flex justify-between"><span class="text-xs text-pink-300">{{ item.name }}</span><span class="text-[10px] text-gray-500">{{ item.semantic_level }}</span></div>
            <pre class="text-[11px] text-gray-400 mt-1 whitespace-pre-wrap">{{ JSON.stringify(item.value, null, 2) }}</pre>
            <div class="text-[10px] text-gray-600 mt-1">{{ item.method }} · n={{ item.sample_size }} {{ item.unit }}</div>
          </template>
          <template v-else>
            <div class="flex justify-between"><span class="text-xs text-amber-300">{{ item.name }}</span><span class="text-[10px] text-gray-500">{{ item.layer }} · {{ item.status }}</span></div>
            <div class="text-[11px] text-gray-400 mt-1">{{ item.expression || '变量派生/约束法则' }}</div>
            <div class="text-[10px] text-gray-600 mt-1">strength={{ item.strength }} · priority={{ item.priority }} · v{{ item.version }}</div>
          </template>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { api } from '../../api'

const tabs = [
  { key: 'documents', label: '文档' },
  { key: 'evidence', label: '证据' },
  { key: 'metrics', label: '指标' },
  { key: 'laws', label: '法则' },
]
const activeTab = ref('documents')
const items = ref<any[]>([])
const loading = ref(false)
const branches = ref<any[]>([])
const selectedBranch = ref<any>(null)
const revisions = ref<any[]>([])
const branchForm = ref({ branch_id: '', name: '' })
const overrideJson = ref('{}')
const overrideNote = ref('')

const loadBranches = async () => {
  try {
    const response = await api.ledger.branches()
    branches.value = response.data?.data || []
    if (selectedBranch.value) {
      const refreshed = branches.value.find((item: any) => item.branch_id === selectedBranch.value.branch_id)
      if (refreshed) await selectBranch(refreshed)
    }
  } catch (error) {
    console.error('加载法则分支失败', error)
    branches.value = []
  }
}

const createBranch = async () => {
  if (!branchForm.value.branch_id.trim() || !branchForm.value.name.trim()) return
  try {
    await api.ledger.createBranch({ ...branchForm.value, current_revision: 0 })
    branchForm.value = { branch_id: '', name: '' }
    await loadBranches()
  } catch (error: any) {
    alert(error?.response?.data?.detail || '创建分支失败')
  }
}

const selectBranch = async (branch: any) => {
  selectedBranch.value = branch
  try {
    const response = await api.ledger.revisions(branch.branch_id)
    revisions.value = response.data?.data || []
  } catch {
    revisions.value = []
  }
}

const appendOverride = async () => {
  if (!selectedBranch.value || !overrideNote.value.trim()) return
  try {
    const snapshot = JSON.parse(overrideJson.value || '{}')
    await api.ledger.appendRevision(selectedBranch.value.branch_id, {
      operation: 'override', snapshot, note: overrideNote.value.trim(),
    })
    overrideNote.value = ''
    await loadBranches()
  } catch (error: any) {
    alert(error?.response?.data?.detail || '覆盖数据必须是合法 JSON')
  }
}

const rollbackTo = async (revision: any) => {
  if (!selectedBranch.value || !confirm(`生成从 r${revision.revision} 回滚的新 revision？`)) return
  try {
    await api.ledger.appendRevision(selectedBranch.value.branch_id, {
      operation: 'rollback', snapshot: revision.snapshot || {}, note: `rollback from r${revision.revision}`,
    })
    await loadBranches()
  } catch (error: any) {
    alert(error?.response?.data?.detail || '回滚失败')
  }
}

const load = async () => {
  loading.value = true
  try {
    const fn = activeTab.value === 'documents' ? api.ledger.documents
      : activeTab.value === 'evidence' ? api.ledger.evidence
      : activeTab.value === 'metrics' ? api.ledger.metrics
      : api.ledger.laws
    const response = await fn()
    items.value = response.data?.data || []
    if (activeTab.value === 'laws') await loadBranches()
  } catch (error) {
    console.error('加载 Ledger 失败', error)
    items.value = []
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>
