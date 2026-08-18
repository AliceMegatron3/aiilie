<template>
  <div class="h-full flex flex-col p-4 bg-[#121212] text-gray-200">
    <div class="flex justify-between items-center mb-4">
      <h2 class="text-xs font-semibold text-gray-500 uppercase tracking-wider">系统设置</h2>
      <button 
        @click="saveSettings" 
        class="text-xs bg-indigo-600 hover:bg-indigo-500 text-white px-2 py-1 rounded transition-colors shadow"
        :disabled="isSaving"
      >
        {{ isSaving ? '...' : '保存' }}
      </button>
    </div>

    <div v-if="isLoading" class="text-xs text-gray-400">加载配置中...</div>

    <div v-else class="space-y-4">
      
      <!-- 云端大模型设置 (DeepSeek/OpenAI) -->
      <section class="bg-[#18181b] p-3 rounded-lg border border-[#2a2a30]">
        <div class="flex items-center justify-between mb-3">
          <h3 class="font-medium text-xs text-white">云端大模型 (Cloud)</h3>
          <label class="relative inline-flex items-center cursor-pointer">
            <input type="checkbox" v-model="form.deepseek.enable_switch" class="sr-only peer">
            <div class="w-8 h-4 bg-gray-600 peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-3 after:w-3 after:transition-all peer-checked:bg-indigo-500"></div>
          </label>
        </div>

        <div class="space-y-3" :class="{'opacity-50 pointer-events-none': !form.deepseek.enable_switch}">
          <div>
            <label class="block text-[11px] font-medium text-gray-400 mb-1">API Base URL</label>
            <input 
              v-model="form.deepseek.api_base" 
              type="text" 
              class="w-full bg-[#121212] border border-[#3f3f46] rounded px-2 py-1.5 text-xs text-white focus:outline-none focus:border-indigo-500"
              placeholder="https://api.deepseek.com/v1"
            >
          </div>
          <div>
            <label class="block text-[11px] font-medium text-gray-400 mb-1">API Key</label>
            <input 
              v-model="form.deepseek.api_key" 
              type="password" 
              class="w-full bg-[#121212] border border-[#3f3f46] rounded px-2 py-1.5 text-xs text-white focus:outline-none focus:border-indigo-500"
              placeholder="sk-..."
            >
          </div>
          <div>
            <label class="block text-[11px] font-medium text-gray-400 mb-1">模型名称</label>
            <input 
              v-model="form.deepseek.model_name" 
              type="text" 
              class="w-full bg-[#121212] border border-[#3f3f46] rounded px-2 py-1.5 text-xs text-white focus:outline-none focus:border-indigo-500"
              placeholder="deepseek-reasoner"
            >
          </div>
        </div>
      </section>

      <!-- 本地大模型设置 (Ollama) -->
      <section class="bg-[#18181b] p-3 rounded-lg border border-[#2a2a30]">
        <div class="flex items-center justify-between mb-3">
          <h3 class="font-medium text-xs text-white">本地大模型 (Ollama)</h3>
          <label class="relative inline-flex items-center cursor-pointer">
            <input type="checkbox" v-model="form.ollama.enable_switch" class="sr-only peer">
            <div class="w-8 h-4 bg-gray-600 peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-3 after:w-3 after:transition-all peer-checked:bg-indigo-500"></div>
          </label>
        </div>

        <div class="space-y-3" :class="{'opacity-50 pointer-events-none': !form.ollama.enable_switch}">
          <div>
            <label class="block text-[11px] font-medium text-gray-400 mb-1">Ollama API URL</label>
            <input 
              v-model="form.ollama.api_base" 
              type="text" 
              class="w-full bg-[#121212] border border-[#3f3f46] rounded px-2 py-1.5 text-xs text-white focus:outline-none focus:border-indigo-500"
              placeholder="http://127.0.0.1:11434/api/generate"
            >
          </div>
          <div>
            <label class="block text-[11px] font-medium text-gray-400 mb-1">本地模型名称</label>
            <div class="flex gap-1">
              <select
                v-model="form.ollama.model_name"
                class="flex-1 bg-[#121212] border border-[#3f3f46] rounded px-2 py-1.5 text-xs text-white focus:outline-none focus:border-indigo-500"
              >
                <option value="" disabled>选择模型...</option>
                <option v-for="m in ollamaModels" :key="m.id" :value="m.name">{{ m.name }}</option>
              </select>
              <button @click="fetchOllamaModels" class="text-[10px] bg-[#2a2a30] hover:bg-gray-600 text-gray-400 rounded px-1.5 py-0.5 shrink-0">刷新</button>
            </div>
          </div>
        </div>
      </section>

      <!-- 系统功能开关 -->
      <section class="bg-[#18181b] p-3 rounded-lg border border-[#2a2a30]">
        <div class="flex items-center justify-between">
          <div>
            <h3 class="font-medium text-xs text-white">九阶情感量化引擎</h3>
            <p class="text-[10px] text-gray-500 mt-1">启用后将记录每一帧画面的情感波澜，影响 Agent 生成表现</p>
          </div>
          <label class="relative inline-flex items-center cursor-pointer">
            <input type="checkbox" v-model="form.features.emotion_quantify_enable" class="sr-only peer">
            <div class="w-8 h-4 bg-gray-600 peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-3 after:w-3 after:transition-all peer-checked:bg-purple-500"></div>
          </label>
        </div>
      </section>

    </div>
  </div>
</template>

<script setup>
import { ref, onMounted, reactive } from 'vue'
import { api, unwrap } from '../../api'
import { toast } from '../../utils/toast'
import { useAgentStore } from '../../stores/agentStore'

const agentStore = useAgentStore()
const isLoading = ref(true)
const isSaving = ref(false)
const ollamaModels = ref([])

const fetchOllamaModels = async () => {
  try {
    const res = await api.models.ollama()
    ollamaModels.value = res.data?.data || []
  } catch (e) {
    ollamaModels.value = []
  }
}

const form = reactive({
  deepseek: {
    api_base: '',
    api_key: '',
    model_name: '',
    enable_switch: false,
    request_timeout: 60,
    max_retry_times: 2
  },
  ollama: {
    api_base: '',
    model_name: '',
    enable_switch: true
  },
  features: {
    emotion_quantify_enable: false
  }
})

const loadSettings = async () => {
  try {
    const res = await api.settings.getLLM()
    const s = unwrap<any>(res)
    if (s) {
      Object.assign(form.deepseek, s.deepseek || {})
      Object.assign(form.ollama, s.ollama || {})
    }
    
    const featureRes = await api.settings.getFeatures()
    const f = unwrap<any>(featureRes)
    if (f) {
      Object.assign(form.features, f || {})
    }
  } catch (e) {
    console.error('加载设置失败', e)
  } finally {
    isLoading.value = false
  }
}

const saveSettings = async () => {
  isSaving.value = true
  try {
    await api.settings.saveLLM(form)
    await api.settings.saveFeatures(form.features)
    // 同步回写 agentStore
    if (form.ollama.model_name) {
      agentStore.setModels(form.deepseek.model_name || agentStore.primaryModel, form.ollama.model_name)
    }
    toast.success('配置保存成功！')
  } catch (e) {
    console.error('保存设置失败', e)
    toast.error('保存失败，请检查网络或后台服务。')
  } finally {
    isSaving.value = false
  }
}

onMounted(() => {
  loadSettings()
  fetchOllamaModels()
})
</script>
