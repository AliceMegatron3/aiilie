<template>
  <div class="fixed inset-0 bg-black/60 backdrop-blur-sm flex items-center justify-center z-50" @click.self="$emit('close')">
    <div class="bg-[#1c1c20] border border-gray-700 rounded-xl shadow-2xl w-[72vw] max-w-5xl h-[80vh] flex flex-col overflow-hidden">
      <!-- 头部 -->
      <div class="px-5 py-3.5 border-b border-[#2a2a30] flex items-center justify-between shrink-0">
        <div class="flex items-center gap-3">
          <h3 class="text-sm font-bold text-gray-100">🎬 场景分镜画廊</h3>
          <button
            class="text-xs bg-indigo-600 hover:bg-indigo-500 text-white px-3 py-1.5 rounded transition-colors"
            @click="extractAndGenerate"
            :disabled="generating"
          >{{ generating ? '提取中...' : '从当前文档提取场景并生成' }}</button>
        </div>
        <button class="text-gray-500 hover:text-white text-lg leading-none" @click="$emit('close')">✕</button>
      </div>

      <!-- 主体 -->
      <div class="flex-1 flex overflow-hidden">
        <!-- 左侧：文本段落列表 -->
        <div class="w-64 shrink-0 border-r border-[#2a2a30] overflow-y-auto">
          <div class="px-3 py-2 text-[10px] text-gray-500 sticky top-0 bg-[#1c1c20] border-b border-[#2a2a30]">文本段落</div>
          <div
            v-for="p in scenePassages"
            :key="p.start"
            class="px-3 py-2.5 border-b border-[#1f1f24] cursor-pointer transition-colors"
            :class="activePassage === p ? 'bg-indigo-950/50 border-l-2 border-l-indigo-500' : 'hover:bg-[#242428] border-l-2 border-l-transparent'"
            @click="selectPassage(p)"
          >
            <p class="text-[11px] text-gray-300 leading-snug line-clamp-3">{{ p.snippet }}</p>
            <span class="text-[9px] text-gray-600 font-mono">[{{ p.start }}-{{ p.end }}] 场景度 {{ p.score }}</span>
          </div>
          <div v-if="scenePassages.length === 0" class="px-3 py-4 text-xs text-gray-600">暂无提取结果</div>
        </div>

        <!-- 右侧：图片画廊（锚点双向绑定） -->
        <div class="flex-1 overflow-y-auto p-4">
          <div class="grid grid-cols-2 xl:grid-cols-3 gap-4">
            <div
              v-for="a in anchors"
              :key="a.anchor_id"
              class="bg-[#1f1f24] border rounded-lg overflow-hidden transition-all"
              :class="a.anchor_id === activeAnchor?.anchor_id ? 'border-indigo-500 shadow-lg shadow-indigo-900/30' : 'border-gray-700 hover:border-gray-500'"
              @click="selectAnchor(a)"
            >
              <div class="aspect-video bg-[#121214] flex items-center justify-center overflow-hidden">
                <img
                  v-if="a.image_file"
                  :src="api.storyboard.imageURL(projectId, a.image_file)"
                  class="w-full h-full object-cover"
                  :alt="a.prompt"
                />
                <span v-else class="text-[10px] text-gray-600">{{ a.status === 'PENDING' ? '生成中...' : '待生成' }}</span>
              </div>
              <div class="p-2.5">
                <p class="text-[10px] text-gray-400 leading-snug line-clamp-2 mb-1.5" :title="a.prompt">{{ a.prompt }}</p>
                <div class="flex items-center justify-between">
                  <button
                    v-if="a.text_range"
                    class="text-[9px] text-indigo-400 hover:text-indigo-300 border border-indigo-900/60 rounded px-1.5 py-0.5 hover:bg-indigo-950/50 transition-colors"
                    @click.stop="jumpToParagraph(a)"
                  >定位段落 [{{ a.text_range[0] }}-{{ a.text_range[1] }}]</button>
                  <span v-else class="text-[9px] text-gray-600">无锚点</span>
                  <button class="text-[9px] text-red-400/80 hover:text-red-300" @click.stop="removeAnchor(a)">删除</button>
                </div>
              </div>
            </div>
          </div>
          <div v-if="anchors.length === 0" class="h-full flex flex-col items-center justify-center text-gray-600 text-sm gap-2">
            <span class="text-3xl">🎞️</span>
            <span>暂无分镜图片，点击上方按钮从文档提取场景</span>
          </div>
        </div>
      </div>

      <!-- 选中锚点详情（图片→段落回查） -->
      <div v-if="activeAnchor" class="shrink-0 border-t border-[#2a2a30] px-5 py-3 flex items-start gap-3">
        <div class="flex-1">
          <div class="text-[10px] text-gray-500 mb-1">图片 ↔ 段落双向绑定</div>
          <p class="text-xs text-gray-300 leading-relaxed">{{ activeAnchor.scene_text }}</p>
        </div>
        <button
          v-if="activeAnchor.doc_id"
          class="text-[10px] bg-[#2a2a30] hover:bg-gray-600 text-gray-300 px-2.5 py-1.5 rounded border border-gray-700 transition-colors shrink-0"
          @click="openAnchorDoc"
        >在文档中查看</button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { toast } from '../utils/toast'
import { api } from '../api'

const props = defineProps({
  projectId: { type: String, default: null },
  docId: { type: String, default: null },
  docText: { type: String, default: '' },
  styleTags: { type: Array, default: () => [] },
})

const emit = defineEmits(['close', 'locate'])

const scenePassages = ref([])
const anchors = ref([])
const activePassage = ref(null)
const activeAnchor = ref(null)
const generating = ref(false)

async function loadAnchors() {
  if (!props.projectId) return
  const res = await api.storyboard.anchors(props.projectId, props.docId)
  anchors.value = res.data?.data || []
}

async function extractAndGenerate() {
  if (!props.projectId) return toast.warning('缺少项目上下文')
  if (!props.docText) return toast.warning('文档内容为空')
  generating.value = true
  try {
    const res = await api.storyboard.extractScenes(props.docText, props.styleTags)
    scenePassages.value = res.data?.data || []
    if (scenePassages.value.length === 0) {
      toast.info('未提取到场景描写段落（需要含环境描写特征词）')
      return
    }
    // 批量提交生图任务（前 4 个高场景度段落）
    const batch = scenePassages.value.slice(0, 4)
    for (const p of batch) {
      await api.storyboard.generate(props.projectId, p.snippet, props.styleTags, props.docId, p.start, p.end)
    }
    toast.success(`已提交 ${batch.length} 个分镜生图任务`)
    await refreshAnchors()
  } catch (e) {
    toast.error('提取失败：' + (e.response?.data?.detail || e.message))
  } finally {
    generating.value = false
  }
}

async function refreshAnchors() {
  // 后端生图任务异步执行，轮询最多 15 次
  for (let i = 0; i < 15; i++) {
    await new Promise(r => setTimeout(r, 800))
    const res = await api.storyboard.anchors(props.projectId, props.docId)
    anchors.value = res.data?.data || []
    if (anchors.value.length && anchors.value.every(a => a.status === 'DONE')) break
  }
}

function selectPassage(p) {
  activePassage.value = p
  // 段落 → 图片：按文本范围查锚点
  api.storyboard.anchorsByRange(props.projectId, props.docId, p.start, p.end).then(res => {
    const matched = res.data?.data || []
    activeAnchor.value = matched[0] || null
  }).catch(() => {})
}

function selectAnchor(a) {
  activeAnchor.value = a
}

function jumpToParagraph(a) {
  // 图片 → 段落：向父组件请求定位文本范围
  if (a.text_range && a.text_range.length === 2) {
    emit('locate', { start: a.text_range[0], end: a.text_range[1] })
  }
}

function openAnchorDoc() {
  if (activeAnchor.value?.doc_id && activeAnchor.value.doc_id !== props.docId) {
    toast.info(`该分镜锚定在文档 ${activeAnchor.value.doc_id}`)
  }
  jumpToParagraph(activeAnchor.value)
}

async function removeAnchor(a) {
  const ok = await toast.confirm('删除该分镜（含图片文件）？')
  if (!ok) return
  await api.storyboard.removeAnchor(props.projectId, a.anchor_id)
  anchors.value = anchors.value.filter(x => x.anchor_id !== a.anchor_id)
  if (activeAnchor.value?.anchor_id === a.anchor_id) activeAnchor.value = null
  toast.success('已删除')
}

onMounted(loadAnchors)
</script>

<style scoped>
.line-clamp-2 {
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.line-clamp-3 {
  display: -webkit-box;
  -webkit-line-clamp: 3;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
</style>
