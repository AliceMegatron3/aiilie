<template>
  <div class="space-y-4 p-4 relative">
    <!-- 总督计划弹窗 -->
    <DagPlanModal
      :isOpen="isDagModalOpen"
      :dag="currentDag"
      @close="isDagModalOpen = false"
      @confirm="executeQuantize"
    />
    <div class="flex justify-between items-center mb-4">
      <h2 class="text-xs font-semibold text-gray-500 uppercase tracking-wider">书库列表</h2>
      <button @click="triggerFileInput" class="text-xs bg-indigo-600 hover:bg-indigo-500 text-white px-3 py-1.5 rounded-md transition-colors shadow flex items-center gap-1">
        <span class="text-lg leading-none">+</span> 本地导入
      </button>
    </div>

    <!-- 隐藏的文件选择器 -->
    <input
      type="file"
      ref="fileInput"
      @change="handleFileSelect"
      accept=".txt,.md,.json"
      class="hidden"
    />

    <!-- 操作反馈提示 -->
    <div v-if="feedback" class="text-xs px-2 py-1.5 rounded border" :class="feedbackType === 'success' ? 'bg-green-900/20 border-green-800 text-green-400' : 'bg-red-900/20 border-red-800 text-red-400'">
      {{ feedback }}
    </div>

    <!-- 加载状态 -->
    <div v-if="isLoading" class="text-xs text-gray-500 text-center py-4">
      <span class="animate-pulse">加载书库...</span>
    </div>

    <!-- 空状态 -->
    <div v-else-if="books.length === 0" class="text-xs text-gray-600 text-center py-8">
      书库为空，点击「导入书籍」添加
    </div>

    <!-- 书籍列表卡片化 (Grid 布局) -->
    <div v-else class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
      <div
        v-for="book in books"
        :key="book.book_id || book.id"
        class="relative flex flex-col p-4 bg-[#18181b] rounded-xl cursor-pointer hover:border-indigo-500/50 border border-[#2a2a30] transition-all hover:shadow-[0_4px_20px_-4px_rgba(99,102,241,0.2)] group"
        :class="{ 'border-indigo-500 bg-[#23232b] shadow-[0_4px_20px_-4px_rgba(99,102,241,0.3)]': selectedBookId === (book.book_id || book.id) }"
        @click="selectBook(book.book_id || book.id)"
      >
        <!-- 装饰性背景光晕 -->
        <div class="absolute inset-0 bg-gradient-to-br from-indigo-500/5 to-transparent opacity-0 group-hover:opacity-100 transition-opacity rounded-xl pointer-events-none"></div>
        
        <div class="flex items-start justify-between mb-2 z-10">
          <h3 class="font-bold text-sm text-gray-200 line-clamp-2 leading-snug pr-2" :title="book.title || book.name">
            📚 {{ book.title || book.name }}
          </h3>
          <span v-if="book.size" class="shrink-0 text-[10px] font-mono px-2 py-0.5 rounded-full bg-[#2a2a30] text-gray-400">
            {{ formatSize(book.size) }}
          </span>
        </div>
        
        <p class="text-[11px] text-gray-500 font-mono mt-auto mb-3 z-10">
          ID: {{ (book.book_id || book.id).substring(0, 8) }}
        </p>

        <!-- 选中后的操作按钮 -->
        <div class="mt-auto flex gap-2 z-10" v-if="selectedBookId === (book.book_id || book.id)">
          <button @click.stop="quantize(book.book_id || book.id, 'info')" class="flex-1 text-xs font-semibold bg-[#2a2a30] hover:bg-indigo-600 text-gray-300 hover:text-white py-1.5 rounded-lg transition-colors">
            ✦ 量化资料
          </button>
          <button @click.stop="quantize(book.book_id || book.id, 'data')" class="flex-1 text-xs font-semibold bg-[#2a2a30] hover:bg-indigo-600 text-gray-300 hover:text-white py-1.5 rounded-lg transition-colors">
            ✦ 量化数据
          </button>
        </div>
        <div class="mt-auto flex gap-2 z-10 opacity-0 group-hover:opacity-100 transition-opacity" v-else>
           <div class="w-full text-center text-[10px] text-indigo-400/50 py-1.5">点击选中以量化</div>
        </div>
      </div>
    </div>

    <!-- 知识卡片检索区 (四象限过滤透镜) -->
    <div class="pt-4 border-t border-[#2a2a30]">
      <div class="flex items-center justify-between mb-2">
        <h3 class="text-xs font-semibold text-gray-500 uppercase tracking-wider">四象限知识库检索</h3>
      </div>
      
      <div class="flex flex-col gap-3 mb-3 bg-[#1e1e24] border border-[#2a2a30] p-3 rounded-lg">
        <div class="flex items-center gap-3">
          <span class="text-xs text-gray-400 w-16">最低有用度</span>
          <input type="range" v-model="minUtility" min="0" max="1" step="0.1" class="flex-1 accent-indigo-500">
          <span class="text-xs text-indigo-400 font-mono w-6 text-right">{{ minUtility }}</span>
        </div>
        <div class="flex items-center gap-3">
          <span class="text-xs text-gray-400 w-16">最低冷门度</span>
          <input type="range" v-model="minEntropy" min="0" max="1" step="0.1" class="flex-1 accent-pink-500">
          <span class="text-xs text-pink-400 font-mono w-6 text-right">{{ minEntropy }}</span>
        </div>
      </div>

      <div class="flex gap-2 mb-2">
        <input
          v-model="searchKeyword"
          type="text"
          placeholder="输入关键词搜索 (支持过滤)..."
          class="flex-1 bg-[#121212] border border-gray-700 rounded px-2 py-1.5 text-xs text-white focus:outline-none focus:border-indigo-500"
          @keydown.enter="searchCards"
        >
        <button @click="searchCards" class="text-xs font-semibold bg-indigo-600 hover:bg-indigo-500 text-white px-3 py-1.5 rounded transition-colors shadow">
          搜索
        </button>
      </div>
      
      <!-- 搜索结果 -->
      <div v-if="searchResults.length > 0" class="grid grid-cols-1 gap-2 max-h-64 overflow-y-auto pr-1">
        <div
          v-for="card in searchResults"
          :key="card.card_id"
          class="p-3 bg-[#18181b] rounded-lg cursor-pointer border transition-all"
          :class="(card.utility_score >= 0.8 && card.entropy_score >= 0.8) ? 'border-yellow-500/50 shadow-[0_0_10px_rgba(234,179,8,0.2)]' : 'border-[#2a2a30] hover:border-indigo-500/30'"
          @click="viewCardDetail(card.card_id)"
        >
          <div class="flex justify-between items-start mb-1">
            <span class="text-xs font-bold" :class="(card.utility_score >= 0.8 && card.entropy_score >= 0.8) ? 'text-yellow-400' : 'text-gray-200'">
              {{ card.subtype || '通用' }}
              <span v-if="card.utility_score >= 0.8 && card.entropy_score >= 0.8" class="ml-1 text-[10px] bg-yellow-900/30 px-1 py-0.5 rounded text-yellow-300">神级设定</span>
            </span>
            <div class="flex gap-2 text-[10px] font-mono">
              <span class="text-indigo-400">U:{{ (card.utility_score || 0).toFixed(1) }}</span>
              <span class="text-pink-400">E:{{ (card.entropy_score || 0).toFixed(1) }}</span>
            </div>
          </div>
          <div class="text-[11px] text-gray-400 line-clamp-2 leading-relaxed">
            {{ card.preview || card.content?.substring(0, 100) || '...' }}
          </div>
        </div>
      </div>
      <div v-else-if="hasSearched" class="text-xs text-gray-500 text-center py-4 border border-dashed border-[#2a2a30] rounded-lg">
        未找到符合当前有用度/冷门度标准的卡片
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted } from 'vue'
import { api } from '../../api'
import { useAgentStore } from '../../stores/agentStore'
import DagPlanModal from '../DagPlanModal.vue'

const agentStore = useAgentStore()
const isDagModalOpen = ref(false)
const currentDag = ref(null)
const pendingQuantize = ref({ bookId: null, type: null })

const books = ref([])
const selectedBookId = ref(null)
const isLoading = ref(false)
const feedback = ref('')
const feedbackType = ref('success')
const fileInput = ref(null)

const triggerFileInput = () => {
  if (fileInput.value) {
    fileInput.value.click()
  }
}

const searchKeyword = ref('')
const searchResults = ref([])
const minUtility = ref(0.0)
const minEntropy = ref(0.0)
const hasSearched = ref(false)

const showFeedback = (msg, type = 'success') => {
  feedback.value = msg
  feedbackType.value = type
  setTimeout(() => { feedback.value = '' }, 4000)
}

const formatSize = (bytes) => {
  if (typeof bytes === 'string') return bytes
  if (bytes < 1024) return bytes + ' B'
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB'
  return (bytes / 1024 / 1024).toFixed(1) + ' MB'
}

const loadBooks = async () => {
  isLoading.value = true
  try {
    const res = await api.library.listBooks()
    if (res.data.success) {
      books.value = res.data.data || []
    }
  } catch (e) {
    console.error('Failed to load books', e)
    showFeedback('加载书库失败', 'error')
  } finally {
    isLoading.value = false
  }
}

const selectBook = (id) => {
  selectedBookId.value = selectedBookId.value === id ? null : id
}

const handleFileSelect = async (event) => {
  const file = event.target.files[0]
  if (!file) return

  isLoading.value = true
  try {
    const res = await api.library.uploadBookFile(file)
    if (res.data.success || res.data.book_id) {
      // 保存到本地列表
      const bookEntry = {
        book_id: res.data.book_id,
        title: file.name,
        size: file.size,
      }
      books.value.unshift(bookEntry)
      localStorage.setItem('no0ai_books', JSON.stringify(books.value))

      showFeedback(`书籍 ${file.name} 导入成功！`)
    }
  } catch (e) {
    showFeedback(e.response?.data?.detail || '导入失败，请检查文件格式或大小', 'error')
  } finally {
    isLoading.value = false
    // 重置 input 以便重复选择同一个文件
    if (fileInput.value) {
      fileInput.value.value = ''
    }
  }
}

const quantize = async (bookId, type) => {
  // 不再直接量化，而是先请求总督生成调度计划图
  try {
    // 模拟本章内容嗅探（实际中应从后端读取书籍第一章内容）
    const dummyContent = "主角拔剑怒吼：‘受死吧！’，随即爆发出强大的气势..."
    const res = await api.orchestrator.plan(
      bookId, 
      dummyContent, 
      agentStore.primaryModel, 
      agentStore.workerModel
    )
    
    if (res.data.success) {
      currentDag.value = res.data.data
      agentStore.setExecutionDag(res.data.data)
      pendingQuantize.value = { bookId, type }
      isDagModalOpen.value = true
    }
  } catch (e) {
    showFeedback('无法获取总督调度计划，请重试', 'error')
  }
}

const executeQuantize = async () => {
  isDagModalOpen.value = false
  const { bookId, type } = pendingQuantize.value
  if (!bookId || !type) return

  try {
    const res = await api.library.quantize(bookId, type)
    // 后端返回信封 { success, data: { task_id } }，需从 data 内层取 task_id
    const payload = res.data?.data || res.data || {}
    showFeedback(`量化任务已授权提交！任务ID: ${(payload.task_id || '').substring(0, 12)}...`)
  } catch (e) {
    const detail = e.response?.data?.detail || '量化任务提交失败'
    showFeedback(detail, 'error')
  }
}

const searchCards = async () => {
  hasSearched.value = true
  try {
    const res = await api.library.searchCards({ 
      keyword: searchKeyword.value, 
      limit: 50,
      min_utility: parseFloat(minUtility.value),
      min_entropy: parseFloat(minEntropy.value)
    })
    searchResults.value = res.data.data || []
  } catch (e) {
    showFeedback('卡片搜索失败', 'error')
  }
}

const viewCardDetail = async (cardId) => {
  try {
    const res = await api.library.cardDetail(cardId)
    showFeedback(`卡片详情已加载，类型: ${res.data.data?.subtype || '通用'}`)
  } catch (e) {
    showFeedback('获取卡片详情失败', 'error')
  }
}

onMounted(() => {
  loadBooks()
})
</script>
