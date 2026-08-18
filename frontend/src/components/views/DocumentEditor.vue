<template>
  <div class="h-full w-full flex flex-col bg-[#1e1e24] text-gray-200 relative">
    <!-- 顶栏：标题（可编辑重命名） + 操作区 -->
    <div class="px-8 pt-4 pb-4 border-b border-[#2a2a30] shrink-0">
      <div class="flex items-center gap-2 mb-2">
        <button
          class="text-xs text-gray-400 hover:text-gray-200 transition-colors flex items-center gap-1"
          @click="appStore.setActiveModule('projects')"
        >← 返回文档列表</button>
        <span class="text-[10px] text-gray-600 font-mono">doc: {{ docId }}</span>
      </div>
      <div class="flex items-end justify-between gap-4">
        <input
          v-model="docTitle"
          type="text"
          class="flex-1 bg-transparent text-2xl font-bold text-gray-100 placeholder-gray-600 focus:outline-none border-b border-transparent focus:border-indigo-500/60 pb-1"
          placeholder="无标题文档"
          @blur="handleRename"
          @keydown.enter="$event.target.blur()"
        />
        <div class="flex items-center gap-2 shrink-0">
          <!-- 保存状态指示器 -->
          <span class="text-xs font-mono" :class="saveStatusClass">{{ saveStatusText }}</span>
          <button
            class="text-xs bg-[#2a2a30] hover:bg-gray-600 text-gray-300 px-3 py-1.5 rounded border border-gray-700 transition-colors"
            :disabled="!dirty"
            :class="{ 'opacity-40 cursor-not-allowed': !dirty }"
            @click="saveNow"
          >保存</button>
          <button
            class="text-xs bg-emerald-900/60 hover:bg-emerald-800 text-emerald-300 px-3 py-1.5 rounded border border-emerald-800/60 transition-colors"
            :disabled="dirty || !generationBaseline || confirming"
            :class="{ 'opacity-40 cursor-not-allowed': dirty || !generationBaseline || confirming }"
            :title="dirty ? '请先保存当前修改' : generationBaseline ? '确认后将记录作者保留率信号' : '当前没有待确认生成稿'"
            @click="confirmAuthorFinal"
          >{{ confirming ? '确认中...' : '确认定稿' }}</button>
          <!-- 版本历史下拉 -->
          <div class="relative">
            <button
              class="text-xs bg-[#2a2a30] hover:bg-gray-600 text-gray-300 px-3 py-1.5 rounded border border-gray-700 transition-colors"
              @click="toggleVersions"
            >版本 ({{ versions.length }})</button>
            <div
              v-if="showVersions"
              class="absolute right-0 top-9 w-64 bg-[#1c1c20] border border-[#3f3f46] rounded-lg shadow-2xl z-20 overflow-hidden"
            >
              <div class="max-h-64 overflow-y-auto">
                <div v-if="versions.length === 0" class="px-4 py-3 text-xs text-gray-500">暂无历史版本</div>
                <div
                  v-for="v in versions"
                  :key="v.version"
                  class="px-4 py-2.5 border-b border-[#2a2a30] last:border-0 hover:bg-[#2a2a30] flex items-center justify-between cursor-pointer"
                  @click="restoreVersion(v)"
                >
                  <div>
                    <div class="text-xs text-gray-300">v{{ v.version }}</div>
                    <div class="text-[10px] text-gray-500">{{ formatTime(v.timestamp) }} · {{ formatBytes(v.size) }}</div>
                  </div>
                  <span class="text-[10px] text-indigo-400">恢复</span>
                </div>
              </div>
              <div class="px-4 py-2 border-t border-[#2a2a30] text-center text-[10px] text-gray-600" @click="showVersions = false">
                点击外部关闭
              </div>
            </div>
          </div>
          <button
            v-if="featureEnabled('tts_enable')"
            class="text-xs bg-[#2a2a30] hover:bg-gray-600 text-gray-300 px-3 py-1.5 rounded border border-gray-700 transition-colors"
            @click="showAudio = !showAudio"
          >🔊 朗读</button>
          <button
            v-if="featureEnabled('branch_version_enable')"
            class="text-xs bg-[#2a2a30] hover:bg-gray-600 text-gray-300 px-3 py-1.5 rounded border border-gray-700 transition-colors"
            @click="showDiff = !showDiff"
          >🔀 分支对比</button>
          <button
            v-if="featureEnabled('storyboard_enable')"
            class="text-xs bg-[#2a2a30] hover:bg-gray-600 text-gray-300 px-3 py-1.5 rounded border border-gray-700 transition-colors"
            @click="showGallery = true"
          >🎬 分镜</button>
          <button
            class="text-xs bg-red-950/60 hover:bg-red-900 text-red-400 px-3 py-1.5 rounded border border-red-900/60 transition-colors"
            @click="handleDelete"
          >删除</button>
        </div>
      </div>
    </div>

    <!-- Markdown 编辑区（真实内容加载 + 自动保存） -->
    <div class="flex-1 p-8 overflow-y-auto">
      <div v-if="loading" class="text-gray-500 text-sm">文档加载中...</div>
      <div v-else-if="loadError" class="text-red-400 text-sm">{{ loadError }}</div>
      <textarea
        v-else
        v-model="content"
        class="w-full h-full bg-transparent text-gray-300 resize-none focus:outline-none leading-relaxed text-lg"
        placeholder="在此处开始编辑 Markdown 内容..."
        @input="onInput"
      ></textarea>
    </div>

    <!-- 浮动词数统计 -->
    <div class="absolute bottom-4 right-6 text-[11px] text-gray-600 font-mono pointer-events-none">
      {{ content.length }} 字
    </div>

    <!-- 补丁2：沉浸式朗读抽屉 -->
    <div v-if="showAudio" class="absolute bottom-4 left-4 right-4 z-30">
      <AudioPlayer
        :text="content"
        :project-id="projectId"
        :doc-id="docId"
        :voice-profiles="voiceProfiles"
        :title="docTitle || '文档朗读'"
      />
    </div>

    <!-- 补丁3：分支差异对比抽屉 -->
    <div v-if="showDiff" class="absolute top-28 right-6 left-1/3 z-30">
      <DiffViewer :doc-id="docId" />
    </div>

    <!-- 补丁4：分镜画廊弹层 -->
    <GalleryModal
      v-if="showGallery"
      :project-id="projectId"
      :doc-id="docId"
      :doc-text="content"
      :style-tags="styleTags"
      @close="showGallery = false"
      @locate="locateTextRange"
    />

    <!-- 悬浮的幽灵工具栏 -->
    <div class="absolute bottom-8 left-1/2 transform -translate-x-1/2 bg-[#27272a]/80 backdrop-blur-md border border-gray-700 rounded-full px-6 py-2 flex space-x-6 opacity-0 hover:opacity-100 transition-opacity duration-300 cursor-pointer">
      <span class="text-gray-400 hover:text-white transition-colors">B</span>
      <span class="text-gray-400 hover:text-white transition-colors italic">I</span>
      <span class="text-gray-400 hover:text-white transition-colors underline">U</span>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, watch, onMounted, onUnmounted, onBeforeUnmount } from 'vue'
import { api, unwrap } from '../../api'
import { toast } from '../../utils/toast'
import { useAppStore } from '../../stores/useAppStore'
import { useFeatureFlags } from '../../composables/useFeatureFlags'
import AudioPlayer from '../../components/AudioPlayer.vue'
import DiffViewer from '../../components/DiffViewer.vue'
import GalleryModal from '../../components/GalleryModal.vue'

const props = defineProps({
  docId: { type: String, required: true },
})

const appStore = useAppStore()
const { enabled: featureEnabled } = useFeatureFlags()
const loading = ref(true)
const loadError = ref('')
const docTitle = ref('')
const content = ref('')
const dirty = ref(false)
const saving = ref(false)
const saveStatus = ref('saved') // saved | saving | pending | error
const versions = ref([])
const showVersions = ref(false)
// 补丁2/3/4 挂载状态
const showAudio = ref(false)
const showDiff = ref(false)
const showGallery = ref(false)
const projectId = ref('')
const voiceProfiles = ref({})
const styleTags = ref([])

const generationBaseline = ref(null)
const confirming = ref(false)

let autosaveTimer = null
let lastSavedContent = ''

const saveStatusText = computed(() => ({
  saved: '已保存',
  saving: '保存中...',
  pending: '未保存',
  error: '保存失败',
}[saveStatus.value]))

const saveStatusClass = computed(() => ({
  saved: 'text-emerald-500',
  saving: 'text-yellow-500',
  pending: 'text-amber-500',
  error: 'text-red-500',
}[saveStatus.value]))

const loadDoc = async () => {
  loading.value = true
  loadError.value = ''
  try {
    const res = await api.projects.getDoc(props.docId)
    const doc = res.data
    docTitle.value = doc.doc_name || ''
    content.value = doc.raw_content || ''
    projectId.value = doc.project_id || ''
    lastSavedContent = content.value
    dirty.value = false
    saveStatus.value = 'saved'
    await loadVersions()
    await loadGenerationBaseline()
    // 补丁2：加载项目角色音色映射与风格标签（失败静默降级）
    if (projectId.value) {
      try {
        const vpRes = await api.tts.voiceProfiles(projectId.value)
        voiceProfiles.value = vpRes.data?.data || {}
      } catch (_) { voiceProfiles.value = {} }
      try {
        const pRes = await api.projects.list()
        const proj = (pRes.data || []).find(p => p.project_id === projectId.value)
        styleTags.value = proj?.style_tags || []
      } catch (_) { styleTags.value = [] }
    }
  } catch (e) {
    loadError.value = '文档加载失败：' + (e.response?.data?.detail || e.message)
  } finally {
    loading.value = false
  }
}

/** 补丁4：分镜锚点 → 文本段落定位（图片→段落方向）。 */
const locateTextRange = ({ start, end }) => {
  const ta = document.querySelector('.flex-1.p-8 textarea')
  if (ta && typeof start === 'number') {
    ta.focus()
    ta.setSelectionRange(start, end ?? start)
    ta.scrollTop = Math.max(0, start - 200)
    toast.info(`已定位到文本位置 [${start}-${end}]`)
  }
  showGallery.value = false
}

/** 读取待确认生成基线；仅用于展示与确认按钮门控，不产生作者信号。 */
const loadGenerationBaseline = async () => {
  try {
    const res = await api.projects.generationBaseline(props.docId)
    generationBaseline.value = unwrap<any>(res)?.baseline || null
  } catch (e) {
    generationBaseline.value = null
  }
}

/** 作者明确确认定稿：唯一产生作者保留率信号的入口。 */
const confirmAuthorFinal = async () => {
  if (dirty.value) {
    toast.warning('请先保存当前修改，再确认定稿')
    return
  }
  if (!generationBaseline.value) {
    toast.info('当前文档没有待确认的生成稿')
    return
  }
  confirming.value = true
  try {
    const res = await api.projects.authorConfirmDoc(props.docId, content.value)
    const retention = unwrap<any>(res)?.confirmation?.signal?.retention
    generationBaseline.value = null
    toast.success(
      retention == null
        ? '已确认定稿'
        : `已确认定稿，本次保留率 ${Math.round(retention * 100)}%`
    )
  } catch (e) {
    toast.error('确认定稿失败：' + (e.response?.data?.detail || e.message))
  } finally {
    confirming.value = false
  }
}

const loadVersions = async () => {
  try {
    const res = await api.projects.docVersions(props.docId)
    versions.value = res.data?.data || []
  } catch (e) {
    versions.value = []
  }
}

const onInput = () => {
  dirty.value = content.value !== lastSavedContent
  saveStatus.value = dirty.value ? 'pending' : 'saved'
  // 自动保存：停止输入 2 秒后触发
  if (autosaveTimer) clearTimeout(autosaveTimer)
  autosaveTimer = setTimeout(saveNow, 2000)
}

const saveNow = async () => {
  if (autosaveTimer) clearTimeout(autosaveTimer)
  if (!dirty.value || saving.value) return
  saving.value = true
  saveStatus.value = 'saving'
  try {
    await api.projects.saveDoc(props.docId, content.value)
    lastSavedContent = content.value
    dirty.value = false
    saveStatus.value = 'saved'
  } catch (e) {
    saveStatus.value = 'error'
    toast.error('文档保存失败：' + (e.response?.data?.detail || e.message))
  } finally {
    saving.value = false
  }
}

const handleRename = async () => {
  const newName = docTitle.value.trim()
  if (!newName) {
    toast.warning('文档名称不能为空')
    return
  }
  try {
    await api.projects.renameDoc(props.docId, newName)
    toast.success('文档已重命名')
  } catch (e) {
    toast.error('重命名失败：' + (e.response?.data?.detail || e.message))
  }
}

const toggleVersions = async () => {
  showVersions.value = !showVersions.value
  if (showVersions.value) await loadVersions()
}

const restoreVersion = async (v) => {
  const ok = await toast.confirm(`确认回滚到版本 v${v.version}？\n当前内容将被覆盖（回滚前会自动保存当前版本）。`)
  if (!ok) return
  try {
    // 回滚前先保存当前状态（后端回滚本身也会产生新版本）
    await saveNow()
    await api.projects.restoreDocVersion(props.docId, v.version)
    toast.success(`已回滚至版本 v${v.version}`)
    showVersions.value = false
    await loadDoc()
  } catch (e) {
    toast.error('回滚失败：' + (e.response?.data?.detail || e.message))
  }
}

const handleDelete = async () => {
  const ok = await toast.confirm('确认删除该文档？\n文档内容与全部版本历史将被移除，此操作不可撤销。')
  if (!ok) return
  try {
    await api.projects.deleteDoc(props.docId)
    toast.success('文档已删除')
    appStore.closeDocument()
  } catch (e) {
    toast.error('删除失败：' + (e.response?.data?.detail || e.message))
  }
}

const formatTime = (iso) => {
  try {
    return new Date(iso).toLocaleString('zh-CN', { hour12: false })
  } catch (e) {
    return iso
  }
}

const formatBytes = (bytes) => {
  if (!bytes) return '0 B'
  const units = ['B', 'KB', 'MB']
  let i = 0
  while (bytes >= 1024 && i < units.length - 1) {
    bytes /= 1024
    i++
  }
  return bytes.toFixed(1) + ' ' + units[i]
}

// 点击外部关闭版本下拉
const onDocumentClick = (e) => {
  if (showVersions.value && !e.target.closest('.relative')) {
    showVersions.value = false
  }
}

watch(() => props.docId, () => {
  if (autosaveTimer) clearTimeout(autosaveTimer)
  loadDoc()
})

onMounted(() => {
  loadDoc()
  document.addEventListener('click', onDocumentClick)
})

onUnmounted(() => {
  document.removeEventListener('click', onDocumentClick)
})

onBeforeUnmount(() => {
  // 组件卸载时若有未保存内容，尝试同步保存（fire-and-forget）
  if (autosaveTimer) clearTimeout(autosaveTimer)
  if (dirty.value) {
    api.projects.saveDoc(props.docId, content.value).catch(() => {})
  }
})
</script>
