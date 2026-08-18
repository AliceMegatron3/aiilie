<template>
  <div class="flex flex-col h-full">
    <!-- 三驾马车顶部切换栏 -->
    <div class="flex border-b border-[#2a2a30] shrink-0">
      <button
        v-for="tab in tabs"
        :key="tab.key"
        class="flex-1 px-2 py-2.5 text-[11px] font-medium transition-colors text-center"
        :class="activeTab === tab.key
          ? 'text-indigo-300 border-b-2 border-indigo-500 bg-[#1a1a22]'
          : 'text-gray-500 hover:text-gray-300 hover:bg-[#1a1a22]'"
        @click="switchTab(tab.key)"
      >{{ tab.icon }} {{ tab.label }}</button>
    </div>

    <!-- 内容区：滚动 -->
    <div class="flex-1 overflow-y-auto">

      <!-- ====== 项目 Tab ====== -->
      <div v-if="activeTab === 'projects'">
        <!-- 无项目选中：项目列表 -->
        <div v-if="!projectStore.currentProjectId" class="p-3">
          <div class="flex items-center justify-between mb-2">
            <span class="text-[10px] text-gray-500 uppercase">项目列表</span>
            <button @click="showNewProject = !showNewProject" class="text-[10px] text-indigo-400 hover:text-indigo-300">+ 新建</button>
          </div>
          <div v-if="showNewProject" class="bg-[#18181b] p-2 rounded border border-indigo-700/40 mb-2">
            <input v-model="newProjectName" placeholder="项目名称" class="w-full bg-[#121212] border border-[#3f3f46] rounded px-2 py-1 text-xs text-white focus:outline-none focus:border-indigo-500 mb-1" @keydown.enter="createProject">
            <div class="flex gap-1">
              <button @click="createProject" :disabled="!newProjectName.trim()" class="flex-1 text-[10px] bg-indigo-600 text-white rounded py-0.5 disabled:opacity-40">创建</button>
              <button @click="showNewProject = false" class="text-[10px] text-gray-400 px-2">取消</button>
            </div>
          </div>
          <div v-for="proj in projectStore.projects" :key="proj.project_id || proj.id"
            class="px-2.5 py-2 rounded cursor-pointer text-xs mb-0.5 transition-colors flex items-center justify-between group"
            :class="(proj.project_id || proj.id) === projectStore.currentProjectId ? 'bg-indigo-900/30 text-indigo-200' : 'text-gray-400 hover:bg-[#1a1a22] hover:text-gray-200'"
            @click="projectStore.selectProject(proj.project_id || proj.id)"
          >
            <span class="truncate">{{ proj.project_name || proj.name }}</span>
            <span class="text-[9px] text-gray-600">{{ (proj.docs_count || (proj.documents ? proj.documents.length : 0)) }}篇</span>
          </div>
        </div>

        <!-- 项目选中：文件树 -->
        <div v-else class="flex flex-col h-full">
          <!-- 项目标题栏 -->
          <div class="px-3 py-2 border-b border-[#1f1f25] flex items-center justify-between shrink-0">
            <div class="flex items-center gap-1 min-w-0">
              <button @click="projectStore.currentProjectId = null" class="text-[10px] text-gray-500 hover:text-gray-300 shrink-0">←</button>
              <span class="text-xs font-medium text-gray-300 truncate">{{ projectStore.currentProject?.project_name || projectStore.currentProject?.name }}</span>
            </div>
            <div class="flex gap-1 shrink-0">
              <button @click="addFile" class="text-[10px] text-gray-500 hover:text-indigo-400 px-1" title="新建文件">📄+</button>
              <button @click="addFolder" class="text-[10px] text-gray-500 hover:text-indigo-400 px-1" title="新建文件夹">📁+</button>
              <label class="text-[10px] text-gray-500 hover:text-indigo-400 px-1 cursor-pointer" title="导入文件">
                📥
                <input type="file" multiple class="hidden" @change="importFiles" accept=".txt,.md,.json">
              </label>
            </div>
          </div>
          <!-- 文件树 -->
          <div class="flex-1 overflow-y-auto p-2" @dragover.prevent @drop.prevent="handleDrop">
            <div v-if="treeItems.length === 0" class="text-[10px] text-gray-600 text-center py-6">拖拽文件到此处导入</div>
            <template v-for="item in treeItems" :key="item.id">
              <div
                class="flex items-center gap-1.5 px-2 py-1 rounded text-xs cursor-pointer transition-colors group"
                :class="item.id === appStore.activeDocId ? 'bg-indigo-900/30 text-indigo-200' : 'text-gray-400 hover:bg-[#1a1a22] hover:text-gray-200'"
                :style="{ paddingLeft: (item.depth * 12 + 8) + 'px' }"
                @click="item.is_folder ? toggleFolder(item) : openDoc(item)"
              >
                <span class="text-[11px] shrink-0">{{ item.is_folder ? (openFolders.has(item.id) ? '📂' : '📁') : '📄' }}</span>
                <span class="truncate flex-1">{{ item.name }}</span>
                <span v-if="item.is_folder" class="text-[9px] text-gray-600">{{ item.doc_count ?? '' }}</span>
                <!-- hover 操作 -->
                <div class="hidden group-hover:flex gap-0.5 shrink-0">
                  <button v-if="item.is_folder" @click.stop="addFileIn(item)" class="text-[9px] text-gray-600 hover:text-indigo-400" title="新建文件">+</button>
                  <button @click.stop="renameItem(item)" class="text-[9px] text-gray-600 hover:text-indigo-400">✏</button>
                  <button @click.stop="deleteItem(item)" class="text-[9px] text-gray-600 hover:text-red-400">✕</button>
                </div>
              </div>
            </template>
          </div>
        </div>
      </div>

      <!-- ====== 书库 Tab ====== -->
      <div v-if="activeTab === 'library'" class="p-3">
        <div class="flex items-center justify-between mb-2">
          <span class="text-[10px] text-gray-500 uppercase">书库</span>
          <div class="flex gap-1">
            <label class="text-[10px] text-indigo-400 hover:text-indigo-300 cursor-pointer">导入
              <input type="file" class="hidden" @change="importBook" accept=".txt,.md,.json">
            </label>
          </div>
        </div>
        <div v-if="books.length === 0" class="text-[10px] text-gray-600 text-center py-6">暂无书籍</div>
        <div v-for="book in books" :key="book.book_id || book.id"
          class="px-2.5 py-2 rounded text-xs mb-0.5 transition-colors group flex items-center justify-between"
          :class="selectedBookId === (book.book_id || book.id) ? 'bg-indigo-900/30 text-indigo-200' : 'text-gray-400 hover:bg-[#1a1a22] hover:text-gray-200'"
          @click="selectBook(book)"
        >
          <div class="truncate flex-1 min-w-0">
            <div class="text-xs truncate">{{ book.title || book.name }}</div>
            <div class="text-[9px] text-gray-600">{{ formatSize(book.size) }}</div>
          </div>
          <div class="hidden group-hover:flex gap-1 shrink-0 items-center">
            <button @click.stop="openQuant(book, 'info')" class="text-[9px] bg-indigo-900/40 text-indigo-300 hover:bg-indigo-800/60 rounded px-1.5 py-0.5" title="资料量化">📚</button>
            <button @click.stop="openQuant(book, 'data')" class="text-[9px] bg-pink-900/40 text-pink-300 hover:bg-pink-800/60 rounded px-1.5 py-0.5" title="数据量化">📊</button>
            <button @click.stop="renameBook(book)" class="text-[9px] text-gray-600 hover:text-indigo-400">✏</button>
            <button @click.stop="deleteBook(book)" class="text-[9px] text-gray-600 hover:text-red-400">✕</button>
          </div>
        </div>
        <!-- Ledger 与知识缺口入口 -->
        <div class="mt-3 pt-2 border-t border-[#2a2a30] space-y-0.5">
          <button @click="appStore.setActiveModule('ledger')" class="text-[10px] text-gray-500 hover:text-gray-300 w-full text-left px-2.5 py-1.5 rounded hover:bg-[#1a1a22]">📚 Ledger 与法则库</button>
          <button @click="appStore.setActiveModule('code')" class="text-[10px] text-gray-500 hover:text-gray-300 w-full text-left px-2.5 py-1.5 rounded hover:bg-[#1a1a22]">💻 编程副驾驶</button>
          <button @click="appStore.setActiveModule('knowledge-gaps')" class="text-[10px] text-gray-500 hover:text-gray-300 w-full text-left px-2.5 py-1.5 rounded hover:bg-[#1a1a22]">🧩 知识缺口（补全质量）</button>
        </div>
      </div>

      <!-- ====== 卡片 Tab ====== -->
      <div v-if="activeTab === 'cards'" class="p-3">
        <!-- 数据类型 -->
        <div class="mb-3">
          <div class="text-[10px] text-pink-400/80 uppercase font-semibold mb-1 px-1">📊 数据类型</div>
          <div v-for="cat in dataTypeCategories" :key="cat.key"
            class="px-2.5 py-1.5 rounded text-xs cursor-pointer transition-colors flex items-center justify-between group"
            :class="selectedCardCat === cat.key ? 'bg-pink-900/20 text-pink-200' : 'text-gray-400 hover:bg-[#1a1a22] hover:text-gray-200'"
            @click="openCardCat(cat)"
          >
            <div class="flex items-center gap-2 min-w-0">
              <span class="shrink-0">{{ cat.icon }}</span>
              <span class="truncate">{{ cat.label }}</span>
            </div>
            <div class="flex items-center gap-1 shrink-0">
              <button @click.stop="learnFromCategory(cat)" class="hidden group-hover:inline text-[8px] bg-pink-900/40 text-pink-300 rounded px-1 py-0.5 hover:bg-pink-800/60" title="让智能体学习此类卡片">学</button>
              <span class="text-[9px] text-gray-600 font-mono">{{ cat.count }}</span>
            </div>
          </div>
        </div>
        <!-- 资料类型 -->
        <div>
          <div class="text-[10px] text-indigo-400/80 uppercase font-semibold mb-1 px-1">📚 资料类型</div>
          <div v-for="cat in infoTypeCategories" :key="cat.key"
            class="px-2.5 py-1.5 rounded text-xs cursor-pointer transition-colors flex items-center justify-between group"
            :class="selectedCardCat === cat.key ? 'bg-indigo-900/20 text-indigo-200' : 'text-gray-400 hover:bg-[#1a1a22] hover:text-gray-200'"
            @click="openCardCat(cat)"
          >
            <div class="flex items-center gap-2 min-w-0">
              <span class="shrink-0">{{ cat.icon }}</span>
              <span class="truncate">{{ cat.label }}</span>
            </div>
            <div class="flex items-center gap-1 shrink-0">
              <button @click.stop="learnFromCategory(cat)" class="hidden group-hover:inline text-[8px] bg-indigo-900/40 text-indigo-300 rounded px-1 py-0.5 hover:bg-indigo-800/60" title="让智能体学习此类卡片">学</button>
              <span class="text-[9px] text-gray-600 font-mono">{{ cat.count }}</span>
            </div>
          </div>
        </div>
        <!-- 扩展卡 -->
        <div class="mt-3 pt-2 border-t border-[#2a2a30]">
          <div class="px-2.5 py-1.5 rounded text-xs cursor-pointer transition-colors flex items-center justify-between"
            :class="selectedCardCat === 'extend' ? 'bg-emerald-900/20 text-emerald-200' : 'text-gray-400 hover:bg-[#1a1a22] hover:text-gray-200'"
            @click="openCardCat({ key: 'extend', type: 'extend' })"
          >
            <div class="flex items-center gap-2"><span>🧩</span><span>扩展卡</span></div>
            <span class="text-[9px] text-gray-600 font-mono">{{ extendCount }}</span>
          </div>
        </div>
      </div>
    </div>

    <!-- 量化弹窗 -->
    <QuantizeModal
      :is-open="isQuantOpen"
      :book-id="quantBook?.book_id || quantBook?.id || null"
      :book-title="quantBook?.title || quantBook?.name || ''"
      :all-models="allModels"
      :default-model="agentStore.primaryModel || agentStore.workerModel || ''"
      :default-mode="quantMode"
      @close="isQuantOpen = false"
      @refresh-models="refreshModels"
      @confirm="doQuantize"
    />
  </div>
</template>

<script setup>
import { ref, computed, onMounted, watch } from 'vue'
import { useAppStore } from '../stores/useAppStore'
import { useProjectStore } from '../stores/useProjectStore'
import { useAgentStore } from '../stores/agentStore'
import { api } from '../api'
import { toast } from '../utils/toast'
import QuantizeModal from './QuantizeModal.vue'

const appStore = useAppStore()
const projectStore = useProjectStore()
const agentStore = useAgentStore()

// ---- Tab 控制 ----
const tabs = [
  { key: 'projects', icon: '📁', label: '项目' },
  { key: 'library', icon: '📚', label: '书库' },
  { key: 'cards', icon: '🗃️', label: '卡片' },
]
const activeTab = ref('projects')

const switchTab = (key) => {
  activeTab.value = key
}

// ---- 量化弹窗相关 ----
const isQuantOpen = ref(false)
const quantBook = ref(null)
const quantMode = ref('both')
const allModels = ref([])

const openQuant = async (book, mode) => {
  quantBook.value = book
  quantMode.value = mode || 'both'
  try {
    const res = await api.models.ollama()
    const local = (res.data?.data || []).map((m) => ({ ...m, type: 'local' }))
    const cloud = agentStore.primaryModel ? [{ id: agentStore.primaryModel, name: agentStore.primaryModel, type: 'cloud' }] : []
    allModels.value = [...local, ...cloud]
  } catch {
    allModels.value = []
  }
  isQuantOpen.value = true
}

const refreshModels = async () => {
  try {
    const res = await api.models.ollama()
    const local = (res.data?.data || []).map((m) => ({ ...m, type: 'local' }))
    const cloud = agentStore.primaryModel ? [{ id: agentStore.primaryModel, name: agentStore.primaryModel, type: 'cloud' }] : []
    allModels.value = [...local, ...cloud]
  } catch {
    allModels.value = []
  }
}

const doQuantize = async (payload) => {
  isQuantOpen.value = false
  const bookId = quantBook.value?.book_id || quantBook.value?.id
  if (!bookId) return
  try {
    await api.library.quantize(bookId, payload.mode, payload.model, payload.extraction, payload.quantizeRound)
    toast.success(`已提交${payload.mode === 'data' ? '数据' : payload.mode === 'info' ? '资料' : '资料+数据'}量化任务`)
  } catch (e) {
    console.error('量化提交失败', e)
    toast.error('量化提交失败：' + (e.response?.data?.detail || e.message || '后端未响应'))
  }
}

// ---- 项目相关 ----
const showNewProject = ref(false)
const newProjectName = ref('')
const treeItems = ref([])
const openFolders = ref(new Set())

const createProject = async () => {
  const name = await toast.prompt('新项目名称：')
  if (!name || !name.trim()) return
  try {
    await projectStore.createProject({
      project_id: `proj_${Date.now()}`,
      project_name: name.trim(),
      genre: 'unknown',
      default_compute_mode: 'rapid',
      bind_book_ids: [],
    })
    showNewProject.value = false
  } catch (e) {
    console.error('创建项目失败', e)
  }
}

const loadTree = async () => {
  const pid = projectStore.currentProjectId
  if (!pid) { treeItems.value = []; return }
  try {
    const res = await api.projects.listDocs(pid)
    const docs = res.data || []
    // 构建文件树：平铺 → 按 folder 分层
    const folders = new Map()
    const roots = []
    for (const d of docs) {
      const item = { ...d, id: d.doc_id, name: d.doc_name, is_folder: d.doc_type === 'folder', depth: 0, children: [], doc_count: 0 }
      if (item.is_folder) {
        folders.set(item.id, item)
      }
    }
    for (const d of docs) {
      const item = { ...d, id: d.doc_id, name: d.doc_name, is_folder: d.doc_type === 'folder', depth: 0, children: [], doc_count: 0 }
      if (d.parent_folder_id && folders.has(d.parent_folder_id)) {
        folders.get(d.parent_folder_id).children.push(item)
      } else {
        roots.push(item)
      }
    }
    // 递归展开树，计算 depth
    const flatten = (items, depth = 0) => {
      const result = []
      for (const it of items) {
        it.depth = depth
        it.doc_count = it.children ? it.children.length : 0
        result.push(it)
        if (it.children && it.children.length > 0 && openFolders.value.has(it.id)) {
          result.push(...flatten(it.children, depth + 1))
        }
      }
      return result
    }
    treeItems.value = flatten(roots)
  } catch (e) {
    console.error('加载文件树失败', e)
  }
}

const toggleFolder = (item) => {
  if (openFolders.value.has(item.id)) {
    openFolders.value.delete(item.id)
  } else {
    openFolders.value.add(item.id)
  }
  loadTree()
}

const addFile = async () => {
  const name = await toast.prompt('新文件名：')
  if (!name) return
  await projectStore.addDocument(projectStore.currentProjectId, { doc_name: name, content: '', doc_type: 'file' })
  loadTree()
}

const addFolder = async () => {
  const name = await toast.prompt('新文件夹名：')
  if (!name) return
  await projectStore.addDocument(projectStore.currentProjectId, { doc_name: name, content: '', doc_type: 'folder' })
  loadTree()
}

const addFileIn = async (folder) => {
  const name = await toast.prompt(`在「${folder.name}」中新建文件名：`)
  if (!name) return
  await projectStore.addDocument(projectStore.currentProjectId, { doc_name: name, content: '', doc_type: 'file', parent_folder_id: folder.id })
  loadTree()
}

const openDoc = (item) => {
  if (item.is_folder) return
  appStore.openDocument(item.id, projectStore.currentProjectId)
}

const renameItem = async (item) => {
  const newName = await toast.prompt('重命名：', item.name)
  if (!newName || newName.trim() === item.name) return
  try {
    await api.projects.renameDoc(item.id, newName.trim())
    loadTree()
  } catch (e) {
    console.error('重命名失败', e)
  }
}

const deleteItem = async (item) => {
  const ok = await toast.confirm(`确定删除「${item.name}」？`)
  if (!ok) return
  try {
    await api.projects.deleteDoc(item.id)
    loadTree()
  } catch (e) {
    console.error('删除失败', e)
  }
}

const importFiles = async (event) => {
  const files = event.target.files
  if (!files || files.length === 0) return
  for (const file of files) {
    const text = await file.text()
    await projectStore.addDocument(projectStore.currentProjectId, { doc_name: file.name, content: text, doc_type: 'file' })
  }
  loadTree()
  event.target.value = ''
}

const handleDrop = async (event) => {
  const files = event.dataTransfer.files
  if (!files || files.length === 0) return
  for (const file of files) {
    const text = await file.text()
    await projectStore.addDocument(projectStore.currentProjectId, { doc_name: file.name, content: text, doc_type: 'file' })
  }
  loadTree()
}

// ---- 书库相关 ----
const books = ref([])
const selectedBookId = ref(null)

const loadBooks = async () => {
  try {
    const res = await api.library.listBooks()
    books.value = res.data?.data || res.data || []
  } catch (e) {
    books.value = []
  }
}

const selectBook = (book) => {
  selectedBookId.value = book.book_id || book.id
}

const importBook = async (event) => {
  const file = event.target.files[0]
  if (!file) return
  try {
    await api.library.uploadBookFile(file)
    await loadBooks()
    toast.success(`书籍「${file.name}」导入成功`)
  } catch (e) {
    console.error('导入书籍失败', e)
    toast.error('导入失败：' + (e.response?.data?.detail || e.message || '后端未响应'))
  }
  event.target.value = ''
}

const renameBook = async (book) => {
  const newName = await toast.prompt('重命名书籍：', book.title || book.name)
  if (!newName || !newName.trim()) return
  try {
    await api.library.renameBook(book.book_id || book.id, newName.trim())
    await loadBooks()
    toast.success('书籍已重命名')
  } catch (e) {
    console.error('重命名失败', e)
    toast.error('重命名失败：' + (e.response?.data?.detail || e.message || '后端未响应'))
  }
}

const deleteBook = async (book) => {
  const ok = await toast.confirm(`确定删除书籍「${book.title || book.name}」？`)
  if (!ok) return
  try {
    await api.library.deleteBook(book.book_id || book.id)
    loadBooks()
  } catch (e) {
    console.error('删除失败', e)
  }
}

const formatSize = (bytes) => {
  if (typeof bytes === 'string') return bytes
  if (!bytes) return ''
  if (bytes < 1024) return bytes + ' B'
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB'
  return (bytes / 1024 / 1024).toFixed(1) + ' MB'
}

// ---- 卡片相关 ----
const selectedCardCat = ref(null)
const extendCount = ref(0)

// 数据类型卡片（可量化为数值/状态）
const dataTypeCategories = ref([
  { key: 'data_emotion', icon: '💗', label: '情感变化', type: 'data', subtype: 'emotion', count: 0 },
  { key: 'data_combat', icon: '⚔️', label: '等级数据', type: 'data', subtype: 'combat', count: 0 },
  { key: 'data_character', icon: '📈', label: '角色成长', type: 'data', subtype: 'character_growth', count: 0 },
  { key: 'data_relation', icon: '🔗', label: '关系变化', type: 'data', subtype: 'relation', count: 0 },
])

// 资料类型卡片（文本描述/设定）
const infoTypeCategories = ref([
  { key: 'info_worldview', icon: '🌍', label: '世界设定', type: 'info', category: 'worldview', count: 0 },
  { key: 'info_plot', icon: '📖', label: '剧情', type: 'info', category: 'plot', count: 0 },
  { key: 'info_character', icon: '👤', label: '角色人设', type: 'info', category: 'character', count: 0 },
  { key: 'info_combat', icon: '🗡️', label: '战斗模块', type: 'info', subtype: 'combat_module', count: 0 },
  { key: 'info_style', icon: '🎨', label: '风格', type: 'info', category: 'style', count: 0 },
])

const loadCardCounts = async () => {
  try {
    for (const cat of [...dataTypeCategories.value, ...infoTypeCategories.value]) {
      const params = { card_type: cat.type, limit: 1 }
      if (cat.category) params.category = cat.category
      if (cat.subtype) params.subtype = cat.subtype
      const res = await api.library.searchCards(params)
      cat.count = res.data?.data?.total ?? 0
    }
    // 扩展卡计数
    const extRes = await api.library.searchCards({ card_type: 'extend', limit: 1 })
    extendCount.value = extRes.data?.data?.total ?? 0
  } catch (e) {
    // 静默
  }
}

const openCardCat = (cat) => {
  selectedCardCat.value = cat.key
  appStore.setActiveModule('cards')
}

const learnFromCategory = async (cat) => {
  try {
    const content = `智能体学习了卡片分类「${cat.label}」(${cat.type})，优化索引权重`
    await api.experience.submit('quantize', content)
  } catch (e) {
    console.error('学习失败', e)
  }
}

// ---- 生命周期 ----
onMounted(() => {
  projectStore.fetchProjects()
  loadBooks()
  loadCardCounts()
})

watch(() => projectStore.currentProjectId, (id) => {
  if (id) loadTree()
})
</script>
