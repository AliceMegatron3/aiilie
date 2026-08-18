<template>
  <div class="h-full flex flex-col">
    <div class="px-6 pt-4 pb-3 border-b border-[#2a2a30] shrink-0">
      <h2 class="text-sm font-semibold text-gray-200">卡片库</h2>
      <p class="text-[10px] text-gray-600 mt-0.5">资料卡 / 数据卡 / 扩展卡 · 详情编辑删除</p>
    </div>

    <!-- 顶部筛选 -->
    <div class="px-6 py-3 flex flex-wrap gap-2 items-center border-b border-[#1f1f25] shrink-0">
      <div class="flex rounded-md overflow-hidden border border-[#3f3f46]">
        <button
          v-for="t in tabs"
          :key="t.key"
          class="px-3 py-1 text-xs transition-colors"
          :class="activeTab === t.key ? 'bg-indigo-600 text-white' : 'bg-[#18181b] text-gray-400 hover:bg-[#2a2a30]'"
          @click="switchTab(t.key)"
        >{{ t.label }}</button>
      </div>
      <input
        v-model="keyword"
        type="text"
        placeholder="关键词搜索..."
        class="bg-[#121212] border border-[#3f3f46] rounded px-2 py-1 text-xs text-white focus:outline-none focus:border-indigo-500"
        @keydown.enter="load"
      >
      <button @click="load" class="text-xs bg-indigo-600 hover:bg-indigo-500 text-white px-2 py-1 rounded">搜索</button>
    </div>

    <div v-if="loading" class="text-xs text-gray-500 text-center py-8">加载中...</div>
    <div v-else-if="cards.length === 0" class="text-xs text-gray-600 text-center py-8">暂无卡片</div>

    <div v-else class="flex-1 overflow-y-auto px-6 py-4 grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3">
      <div
        v-for="c in cards"
        :key="c.card_id"
        class="p-3 rounded-lg border transition-all cursor-pointer"
        :class="typeBorder(c.card_type)"
        @click="openDetail(c.card_id)"
      >
        <div class="flex justify-between items-start mb-1">
          <span class="text-xs font-bold text-gray-200">{{ c.subtype || '通用' }}</span>
          <span class="text-[9px] font-mono px-1.5 py-0.5 rounded" :class="typeBadge(c.card_type)">{{ c.card_type }}</span>
        </div>
        <div class="text-[11px] text-gray-400 line-clamp-3 leading-relaxed">{{ cardPreview(c) }}</div>
        <div class="flex gap-2 mt-2 text-[10px] font-mono">
          <span class="text-indigo-400">U:{{ (c.utility_score || 0).toFixed(1) }}</span>
          <span class="text-pink-400">E:{{ (c.entropy_score || 0).toFixed(1) }}</span>
          <span class="text-gray-600 ml-auto">{{ (c.tags || []).slice(0, 3).join(' · ') }}</span>
        </div>
      </div>
    </div>

    <!-- 详情弹窗 -->
    <div v-if="detail" class="fixed inset-0 bg-black/60 z-50 flex items-center justify-center p-6" @click.self="detail = null">
      <div class="bg-[#1e1e24] border border-[#3f3f46] rounded-xl w-full max-w-2xl max-h-[85vh] overflow-y-auto p-6">
        <div class="flex items-center justify-between mb-4">
          <h3 class="text-sm font-semibold text-gray-200">{{ detail.subtype || '卡片详情' }}</h3>
          <button class="text-gray-400 hover:text-white text-lg" @click="detail = null">✕</button>
        </div>

        <div class="space-y-3 text-xs">
          <div class="grid grid-cols-2 gap-3 text-gray-400">
            <div><span class="text-gray-600">card_id:</span> <span class="font-mono text-gray-300">{{ detail.card_id }}</span></div>
            <div><span class="text-gray-600">type:</span> <span class="text-gray-300">{{ detail.card_type }}</span></div>
            <div><span class="text-gray-600">source_book:</span> <span class="text-gray-300">{{ detail.source_book_id }}</span></div>
            <div><span class="text-gray-600">subtype:</span> <span class="text-gray-300">{{ detail.subtype }}</span></div>
            <div><span class="text-gray-600">U/E:</span> <span class="text-gray-300">{{ (detail.utility_score || 0).toFixed(2) }} / {{ (detail.entropy_score || 0).toFixed(2) }}</span></div>
            <div><span class="text-gray-600">tags:</span> <span class="text-gray-300">{{ (detail.tags || []).join(', ') || '—' }}</span></div>
          </div>

          <div>
            <div class="text-gray-500 mb-1">内容</div>
            <textarea
              v-model="editContent"
              class="w-full bg-[#121212] border border-[#3f3f46] rounded p-2 text-gray-200 text-xs focus:outline-none focus:border-indigo-500 min-h-[100px]"
            ></textarea>
          </div>

          <div v-if="detail.payload && Object.keys(detail.payload).length" class="border-t border-[#2a2a30] pt-3">
            <div class="text-gray-500 mb-1">payload</div>
            <pre class="bg-[#121212] rounded p-2 text-[10px] text-gray-400 overflow-x-auto whitespace-pre-wrap">{{ JSON.stringify(detail.payload, null, 2) }}</pre>
          </div>
          <div v-if="detail.original_fragment" class="border-t border-[#2a2a30] pt-3">
            <div class="text-gray-500 mb-1">原文片段</div>
            <div class="text-[10px] text-gray-500 italic">{{ detail.original_fragment }}</div>
          </div>
        </div>

        <div class="flex justify-end gap-2 mt-5">
          <button class="text-xs bg-red-900/30 hover:bg-red-800/40 text-red-400 rounded px-3 py-1.5" @click="remove(detail.card_id)">删除</button>
          <button class="text-xs bg-indigo-600 hover:bg-indigo-500 text-white rounded px-3 py-1.5" @click="saveEdit">保存</button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { api, unwrapData } from '../../api'

const tabs = [
  { key: 'info', label: '资料卡' },
  { key: 'data', label: '数据卡' },
  { key: 'extend', label: '扩展卡' },
]
const activeTab = ref('info')
const keyword = ref('')
const cards = ref<any[]>([])
const loading = ref(false)
const detail = ref<any>(null)
const editContent = ref('')

const switchTab = (key: string) => {
  activeTab.value = key
  load()
}

const typeBorder = (t: string) =>
  t === 'info' ? 'border-indigo-800/40 hover:border-indigo-500/60' :
  t === 'data' ? 'border-pink-800/40 hover:border-pink-500/60' :
  'border-emerald-800/40 hover:border-emerald-500/60'

const typeBadge = (t: string) =>
  t === 'info' ? 'bg-indigo-900/40 text-indigo-300' :
  t === 'data' ? 'bg-pink-900/40 text-pink-300' :
  'bg-emerald-900/40 text-emerald-300'

const cardPreview = (c: any) => {
  try {
    const parsed = JSON.parse(c.content || '{}')
    if (typeof parsed === 'object' && parsed !== null) return JSON.stringify(parsed).substring(0, 160)
  } catch { /* 非 JSON 直接显示 */ }
  return (c.content || '...').substring(0, 160)
}

const load = async () => {
  loading.value = true
  try {
    const res = await api.library.searchCards({
      card_type: activeTab.value,
      keyword: keyword.value || undefined,
      limit: 100,
    })
    const data = unwrapData(res) as { results?: any[] }
    cards.value = data?.results ?? []
  } catch (e) {
    console.error('加载卡片失败', e)
  } finally {
    loading.value = false
  }
}

const openDetail = async (cardId: string) => {
  try {
    const res = await api.library.cardDetail(cardId)
    detail.value = unwrapData(res) || {}
    editContent.value = detail.value.content || ''
  } catch (e: any) {
    alert(e?.response?.data?.detail || '获取卡片详情失败')
  }
}

const saveEdit = async () => {
  if (!detail.value) return
  try {
    await api.library.updateCard(detail.value.card_id, { content: editContent.value })
    detail.value = null
    await load()
  } catch (e: any) {
    alert(e?.response?.data?.detail || '保存失败')
  }
}

const remove = async (cardId: string) => {
  if (!confirm('确定删除此卡片？')) return
  try {
    await api.library.deleteCard(cardId)
    detail.value = null
    await load()
  } catch (e: any) {
    alert(e?.response?.data?.detail || '删除失败')
  }
}

onMounted(load)
</script>
