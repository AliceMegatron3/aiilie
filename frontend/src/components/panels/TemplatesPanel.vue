<template>
  <div class="space-y-4 p-4">
    <div class="flex justify-between items-center mb-4">
      <h2 class="text-xs font-semibold text-gray-500 uppercase tracking-wider">模板库</h2>
      <button @click="loadTemplates" class="text-xs text-gray-400 hover:text-gray-200 transition-colors">刷新</button>
    </div>

    <!-- 加载状态 -->
    <div v-if="isLoading" class="text-xs text-gray-500 text-center py-4">
      <span class="animate-pulse">加载模板...</span>
    </div>

    <!-- 空状态 -->
    <div v-else-if="templates.length === 0" class="text-xs text-gray-600 text-center py-8">
      暂无可用模板
    </div>

    <!-- 模板列表 -->
    <div v-else class="space-y-2">
      <!-- 分类筛选按钮 -->
      <div class="flex flex-wrap gap-1 mb-3">
        <button v-for="cat in categories" :key="cat"
          class="text-[10px] px-2 py-0.5 rounded transition-colors"
          :class="selectedCategory === cat ? 'bg-indigo-600 text-white' : 'bg-[#2a2a30] text-gray-400 hover:text-gray-200'"
          @click="selectedCategory = selectedCategory === cat ? '' : cat"
        >{{ cat }}</button>
      </div>

      <div
        v-for="tpl in filteredTemplates"
        :key="tpl.template_id"
        class="p-2.5 bg-[#18181b] rounded-lg cursor-pointer hover:border-gray-500 border border-[#2a2a30] transition-colors"
        :class="{ 'border-indigo-500 bg-[#23232b]': appStore.selectedTemplateId === tpl.template_id }"
        @click="selectTemplate(tpl)"
      >
        <div class="flex items-center justify-between mb-1">
          <h3 class="font-medium text-sm text-gray-300 truncate">{{ tpl.name || tpl.template_id }}</h3>
          <span v-if="tpl.category" class="text-[10px] px-1.5 py-0.5 rounded bg-indigo-900/40 text-indigo-400">{{ tpl.category }}</span>
        </div>
        <p class="text-[11px] text-gray-500 line-clamp-2">{{ tpl.description || tpl.template_text?.substring(0, 80) || '...' }}</p>
      </div>
    </div>

    <!-- 模板编辑区（选中后展示完整编辑器：语法高亮变量/渲染预览/版本回滚） -->
    <div v-if="selectedTemplate" class="pt-4 border-t border-[#2a2a30]">
      <PromptTemplateEditor :template="selectedTemplate" @saved="loadTemplates" />
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { useAppStore } from '../../stores/useAppStore'
import { api } from '../../api'
import PromptTemplateEditor from './PromptTemplateEditor.vue'

const appStore = useAppStore()
const templates = ref([])
const isLoading = ref(false)
const selectedCategory = ref('')

const categories = computed(() => {
  const cats = new Set(templates.value.map((t) => t.category).filter(Boolean))
  return [...cats].sort()
})

const filteredTemplates = computed(() => {
  if (!selectedCategory.value) return templates.value
  return templates.value.filter((t) => t.category === selectedCategory.value)
})

const selectedTemplate = computed(() => {
  if (!appStore.selectedTemplateId) return null
  return templates.value.find((t) => t.template_id === appStore.selectedTemplateId) || null
})

const loadTemplates = async () => {
  isLoading.value = true
  try {
    const res = await api.prompts.list()
    if (res.data.status === 'success') {
      templates.value = res.data.data || []
    }
  } catch (e) {
    console.error('加载模板列表失败', e)
    templates.value = []
  } finally {
    isLoading.value = false
  }
}

const selectTemplate = (tpl) => {
  appStore.selectTemplate(tpl.template_id)
}

onMounted(() => {
  loadTemplates()
})
</script>
