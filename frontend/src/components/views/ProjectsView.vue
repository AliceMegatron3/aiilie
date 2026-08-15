<template>
  <div class="h-full w-full flex flex-col bg-[#1e1e24] text-gray-200">
    <!-- 项目文档列表 -->
    <div class="flex-1 overflow-y-auto p-6">
      <div v-if="!projectStore.currentProject" class="flex items-center justify-center h-full text-gray-500">
        请从左侧选择一个项目
      </div>
      <div v-else>
        <div class="flex items-center justify-between mb-4">
          <h2 class="text-lg font-semibold text-white">{{ projectStore.currentProject.project_name || projectStore.currentProject.name }}</h2>
          <button @click="loadDocs" class="text-xs bg-[#2a2a30] hover:bg-gray-600 text-gray-300 px-3 py-1.5 rounded transition-colors border border-gray-700">
            刷新文档
          </button>
        </div>

        <div v-if="docsLoading" class="text-xs text-gray-500 py-4">加载文档中...</div>
        <div v-else-if="documents.length === 0" class="text-xs text-gray-600 text-center py-8">
          该项目暂无文档
        </div>
        <div v-else class="space-y-2">
          <div
            v-for="doc in documents"
            :key="doc.doc_id"
            class="p-3 bg-[#18181b] rounded-lg border border-[#2a2a30] hover:border-gray-600 transition-colors group"
          >
            <!-- 主区域：点击打开编辑器 -->
            <div class="cursor-pointer" @click="openDoc(doc)">
              <div class="flex items-center justify-between">
                <h3 class="font-medium text-sm text-gray-300">{{ doc.doc_name }}</h3>
                <span class="text-[10px] text-gray-600 font-mono opacity-0 group-hover:opacity-100 transition-opacity">
                  点击打开编辑
                </span>
              </div>
              <p class="text-[11px] text-gray-500 mt-1">状态: {{ doc.status || 'DRAFT' }}</p>
            </div>
            <!-- 操作区：重命名 / 删除 -->
            <div class="flex gap-2 mt-2 pt-2 border-t border-[#2a2a30] opacity-0 group-hover:opacity-100 transition-opacity">
              <button
                class="text-[11px] text-indigo-400 hover:text-indigo-300 px-2 py-1 rounded border border-indigo-900/50 hover:bg-indigo-900/30 transition-colors"
                @click.stop="renameDoc(doc)"
              >重命名</button>
              <button
                class="text-[11px] text-red-400 hover:text-red-300 px-2 py-1 rounded border border-red-900/50 hover:bg-red-900/30 transition-colors"
                @click.stop="deleteDoc(doc)"
              >删除</button>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, watch, onMounted } from 'vue'
import { useProjectStore } from '../../stores/useProjectStore'
import { useAppStore } from '../../stores/useAppStore'
import { api } from '../../api'
import { toast } from '../../utils/toast'

const projectStore = useProjectStore()
const appStore = useAppStore()
const documents = ref([])
const docsLoading = ref(false)

const loadDocs = async () => {
  const pid = projectStore.currentProjectId
  if (!pid) return
  docsLoading.value = true
  try {
    const res = await api.projects.listDocs(pid)
    documents.value = res.data || []
  } catch (e) {
    console.error('加载文档失败', e)
    documents.value = []
  } finally {
    docsLoading.value = false
  }
}

// 点击文档 → 联动打开 DocumentEditor（store 驱动 + 路由同步）
const openDoc = (doc) => {
  appStore.openDocument(doc.doc_id, projectStore.currentProjectId)
}

const renameDoc = async (doc) => {
  const newName = await toast.prompt('请输入新文档名称：', doc.doc_name)
  if (!newName || !newName.trim() || newName.trim() === doc.doc_name) return
  try {
    await api.projects.renameDoc(doc.doc_id, newName.trim())
    toast.success('文档已重命名')
    loadDocs()
  } catch (e) {
    toast.error('重命名失败：' + (e.response?.data?.detail || e.message))
  }
}

const deleteDoc = async (doc) => {
  const ok = await toast.confirm(`确认删除文档「${doc.doc_name}」？\n内容与版本历史将一并移除。`)
  if (!ok) return
  try {
    await api.projects.deleteDoc(doc.doc_id)
    toast.success('文档已删除')
    loadDocs()
  } catch (e) {
    toast.error('删除失败：' + (e.response?.data?.detail || e.message))
  }
}

watch(() => projectStore.currentProjectId, () => {
  documents.value = []
  loadDocs()
})

onMounted(() => {
  projectStore.fetchProjects()
  loadDocs()
})
</script>
