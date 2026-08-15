<template>
  <div class="space-y-4 p-4">
    <div class="flex justify-between items-center mb-4">
      <h2 class="text-xs font-semibold text-gray-500 uppercase tracking-wider">我的项目</h2>
      <button @click="showCreateForm = !showCreateForm" class="text-xs bg-[#2a2a30] hover:bg-gray-600 text-gray-300 px-2 py-1 rounded transition-colors border border-gray-700">
        + 新建
      </button>
    </div>

    <!-- 新建项目表单 -->
    <div v-if="showCreateForm" class="bg-[#18181b] p-3 rounded-lg border border-indigo-700/50 space-y-2">
      <input
        v-model="newProject.name"
        type="text"
        placeholder="项目名称"
        class="w-full bg-[#121212] border border-gray-700 rounded px-2 py-1.5 text-xs text-white focus:outline-none focus:border-indigo-500"
      >
      <input
        v-model="newProject.description"
        type="text"
        placeholder="项目描述（可选）"
        class="w-full bg-[#121212] border border-gray-700 rounded px-2 py-1.5 text-xs text-white focus:outline-none focus:border-indigo-500"
      >
      <div class="flex gap-2">
        <button @click="handleCreate" :disabled="!newProject.name.trim()" class="flex-1 text-xs bg-indigo-600 hover:bg-indigo-500 text-white py-1.5 rounded transition-colors disabled:opacity-40">
          确认创建
        </button>
        <button @click="showCreateForm = false" class="text-xs bg-[#2a2a30] hover:bg-gray-600 text-gray-300 px-3 py-1.5 rounded transition-colors">
          取消
        </button>
      </div>
    </div>

    <!-- 加载状态 -->
    <div v-if="projectStore.isLoading" class="text-xs text-gray-500 text-center py-4">
      <span class="animate-pulse">加载项目中...</span>
    </div>

    <!-- 空状态 -->
    <div v-else-if="projectStore.projects.length === 0" class="text-xs text-gray-600 text-center py-8">
      暂无项目，点击「新建」创建第一个项目
    </div>

    <!-- 项目列表 -->
    <div v-else class="space-y-2">
      <div
        v-for="proj in projectStore.projects"
        :key="proj.project_id || proj.id"
        class="p-2.5 bg-[#18181b] rounded-lg cursor-pointer hover:border-gray-600 border border-[#2a2a30] transition-colors"
        :class="{ 'border-indigo-500 bg-[#23232b]': projectStore.currentProjectId === (proj.project_id || proj.id) }"
        @click="selectProject(proj.project_id || proj.id)"
      >
        <h3 class="font-medium text-sm text-gray-300 truncate">{{ proj.project_name || proj.name }}</h3>
        <div class="flex items-center justify-between mt-1">
          <p class="text-[11px] text-gray-500">
            文档: {{ proj.docs_count || (proj.documents ? proj.documents.length : 0) }}
          </p>
          <span v-if="proj.default_compute_mode" class="text-[10px] px-1.5 py-0.5 rounded"
            :class="proj.default_compute_mode === 'think' ? 'bg-purple-900/40 text-purple-400' : 'bg-blue-900/40 text-blue-400'">
            {{ proj.default_compute_mode === 'think' ? '深思' : '快速' }}
          </span>
        </div>
      </div>
    </div>

    <!-- 错误提示 -->
    <div v-if="projectStore.error" class="text-xs text-red-400 bg-red-900/20 border border-red-800 rounded p-2">
      {{ projectStore.error }}
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted, reactive } from 'vue'
import { useProjectStore } from '../../stores/useProjectStore'
import { api } from '../../api'

const projectStore = useProjectStore()
const showCreateForm = ref(false)
const newProject = reactive({
  name: '',
  description: '',
  default_compute_mode: 'rapid',
  bind_book_ids: [],
})

onMounted(() => {
  projectStore.fetchProjects()
})

const selectProject = (id) => {
  projectStore.selectProject(id)
}

const handleCreate = async () => {
  if (!newProject.name.trim()) return
  try {
    await projectStore.createProject({
      project_id: `proj_${Date.now()}`,
      project_name: newProject.name,
      genre: 'unknown',
      style_tags: [],
      default_compute_mode: newProject.default_compute_mode,
      bind_book_ids: newProject.bind_book_ids,
    })
    showCreateForm.value = false
    newProject.name = ''
    newProject.description = ''
  } catch (e) {
    console.error('创建项目失败', e)
  }
}
</script>
