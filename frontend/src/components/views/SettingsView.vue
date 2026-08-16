<template>
  <div class="h-full w-full flex flex-col p-8 bg-[#1e1e24] text-gray-200 overflow-y-auto">
    <div class="flex justify-between items-center mb-6">
      <h2 class="text-2xl font-bold">系统设置</h2>
      <div class="flex items-center gap-2">
        <button
          @click="testConnectivity"
          class="bg-[#2a2a30] hover:bg-gray-600 text-gray-300 px-4 py-2 rounded shadow transition-colors"
          :disabled="isPinging"
        >
          {{ isPinging ? '检测中...' : '连通性检测' }}
        </button>
        <button
          @click="saveSettings"
          class="bg-indigo-600 hover:bg-indigo-500 text-white px-4 py-2 rounded shadow transition-colors flex items-center gap-2"
          :disabled="isSaving"
        >
          <span v-if="isSaving">保存中...</span>
          <span v-else>保存配置</span>
        </button>
      </div>
    </div>
    <div v-if="pingResult" class="mb-3 text-xs px-3 py-2 rounded bg-[#121212] border border-[#2a2a30] text-gray-300">{{ pingResult }}</div>

    <div v-if="isLoading" class="text-gray-400">加载配置中...</div>

    <div v-else class="space-y-8 max-w-3xl">
      
      <!-- 云端大模型设置 (DeepSeek/OpenAI) -->
      <section class="bg-[#2a2a30] p-6 rounded-lg shadow border border-[#3f3f46]">
        <div class="flex items-center justify-between mb-4">
          <div>
            <h3 class="font-semibold text-lg text-white">云端大模型 (Cloud AI)</h3>
            <p class="text-sm text-gray-400 mt-1">配置 DeepSeek 或兼容 OpenAI 格式的云端模型接口。</p>
          </div>
          <label class="relative inline-flex items-center cursor-pointer">
            <input type="checkbox" v-model="form.deepseek.enable_switch" class="sr-only peer">
            <div class="w-11 h-6 bg-gray-600 peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-5 after:w-5 after:transition-all peer-checked:bg-indigo-500"></div>
          </label>
        </div>

        <div class="space-y-4" :class="{'opacity-50 pointer-events-none': !form.deepseek.enable_switch}">
          <div>
            <label class="block text-sm font-medium text-gray-300 mb-1">API Base URL</label>
            <input 
              v-model="form.deepseek.api_base" 
              type="text" 
              class="w-full bg-[#18181b] border border-[#3f3f46] rounded px-3 py-2 text-white focus:outline-none focus:border-indigo-500"
              :class="{ 'border-red-700': errors.deepseek_api_base }"
              placeholder="https://api.deepseek.com/v1"
            >
            <p v-if="errors.deepseek_api_base" class="text-xs text-red-400 mt-1">{{ errors.deepseek_api_base }}</p>
          </div>
          <div>
            <label class="block text-sm font-medium text-gray-300 mb-1">API Key</label>
            <input 
              v-model="form.deepseek.api_key" 
              type="password" 
              class="w-full bg-[#18181b] border border-[#3f3f46] rounded px-3 py-2 text-white focus:outline-none focus:border-indigo-500"
              :class="{ 'border-red-700': errors.deepseek_api_key }"
              placeholder="sk-..."
            >
            <p v-if="errors.deepseek_api_key" class="text-xs text-red-400 mt-1">{{ errors.deepseek_api_key }}</p>
          </div>
          <div>
            <label class="block text-sm font-medium text-gray-300 mb-1">模型名称 (Model Name)</label>
            <input 
              v-model="form.deepseek.model_name" 
              type="text" 
              class="w-full bg-[#18181b] border border-[#3f3f46] rounded px-3 py-2 text-white focus:outline-none focus:border-indigo-500"
              :class="{ 'border-red-700': errors.deepseek_model_name }"
              placeholder="deepseek-reasoner"
            >
            <p v-if="errors.deepseek_model_name" class="text-xs text-red-400 mt-1">{{ errors.deepseek_model_name }}</p>
          </div>
          <div class="grid grid-cols-2 gap-4">
            <div>
              <label class="block text-sm font-medium text-gray-300 mb-1">请求超时 (秒)</label>
              <input 
                v-model.number="form.deepseek.request_timeout" 
                type="number" 
                class="w-full bg-[#18181b] border border-[#3f3f46] rounded px-3 py-2 text-white focus:outline-none focus:border-indigo-500"
                :class="{ 'border-red-700': errors.deepseek_timeout }"
              >
              <p v-if="errors.deepseek_timeout" class="text-xs text-red-400 mt-1">{{ errors.deepseek_timeout }}</p>
            </div>
            <div>
              <label class="block text-sm font-medium text-gray-300 mb-1">最大重试次数</label>
              <input 
                v-model.number="form.deepseek.max_retry_times" 
                type="number" 
                class="w-full bg-[#18181b] border border-[#3f3f46] rounded px-3 py-2 text-white focus:outline-none focus:border-indigo-500"
                :class="{ 'border-red-700': errors.deepseek_retry }"
              >
              <p v-if="errors.deepseek_retry" class="text-xs text-red-400 mt-1">{{ errors.deepseek_retry }}</p>
            </div>
          </div>
        </div>
      </section>

      <!-- 本地大模型设置 (Ollama) -->
      <section class="bg-[#2a2a30] p-6 rounded-lg shadow border border-[#3f3f46]">
        <div class="flex items-center justify-between mb-4">
          <div>
            <h3 class="font-semibold text-lg text-white">本地大模型 (Ollama)</h3>
            <p class="text-sm text-gray-400 mt-1">配置本地断网环境下的 Ollama 服务与模型。</p>
          </div>
          <label class="relative inline-flex items-center cursor-pointer">
            <input type="checkbox" v-model="form.ollama.enable_switch" class="sr-only peer">
            <div class="w-11 h-6 bg-gray-600 peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-5 after:w-5 after:transition-all peer-checked:bg-indigo-500"></div>
          </label>
        </div>

        <div class="space-y-4" :class="{'opacity-50 pointer-events-none': !form.ollama.enable_switch}">
          <div>
            <label class="block text-sm font-medium text-gray-300 mb-1">Ollama API Base URL</label>
            <input 
              v-model="form.ollama.api_base" 
              type="text" 
              class="w-full bg-[#18181b] border border-[#3f3f46] rounded px-3 py-2 text-white focus:outline-none focus:border-indigo-500"
              :class="{ 'border-red-700': errors.ollama_api_base }"
              placeholder="http://127.0.0.1:11434/api/generate"
            >
            <p v-if="errors.ollama_api_base" class="text-xs text-red-400 mt-1">{{ errors.ollama_api_base }}</p>
          </div>
          <div>
            <label class="block text-sm font-medium text-gray-300 mb-1">本地模型名称 (Local Model)</label>
            <input 
              v-model="form.ollama.model_name" 
              type="text" 
              class="w-full bg-[#18181b] border border-[#3f3f46] rounded px-3 py-2 text-white focus:outline-none focus:border-indigo-500"
              :class="{ 'border-red-700': errors.ollama_model_name }"
              placeholder="qwen2.5:7b"
            >
            <p v-if="errors.ollama_model_name" class="text-xs text-red-400 mt-1">{{ errors.ollama_model_name }}</p>
          </div>
        </div>
      </section>

      <!-- 外观/主题(批次C:皮肤插件接口) -->
      <section class="bg-[#2a2a30] p-6 rounded-lg shadow border border-[#3f3f46]">
        <h3 class="font-semibold text-lg text-white mb-2">外观 / 主题</h3>
        <p class="text-sm text-gray-400 mb-3">切换界面皮肤。插件可注册自定义主题。</p>
        <div class="flex gap-2 flex-wrap">
          <button
            v-for="t in themeList"
            :key="t.name"
            @click="switchTheme(t.name)"
            class="px-4 py-2 rounded text-sm transition-colors"
            :class="currentTheme === t.name ? 'bg-indigo-600 text-white' : 'bg-[#18181b] text-gray-300 hover:bg-gray-700'"
          >{{ t.label }}</button>
        </div>
        <div v-if="themeNote" class="mt-2 text-xs text-gray-500">{{ themeNote }}</div>
      </section>

    </div>
  </div>
</template>

<script setup>
import { ref, onMounted, reactive, computed } from 'vue'
import { api } from '../../api'
import { toast } from '../../utils/toast'
import { useThemeStore } from '../../stores/useThemeStore'

const themeStore = useThemeStore()
const currentTheme = computed(() => themeStore.current)
const themeList = computed(() => themeStore.available)
const themeNote = ref('')
const switchTheme = (name) => {
  themeStore.setTheme(name)
  themeNote.value = `已切换到「${name}」皮肤`
}

const isLoading = ref(true)
const isSaving = ref(false)
const isPinging = ref(false)
const pingResult = ref('')
const errors = reactive({})

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
  }
})

// 必填校验：启用状态下强制校验关键字段
const validate = () => {
  Object.keys(errors).forEach((k) => delete errors[k])

  if (form.deepseek.enable_switch) {
    if (!form.deepseek.api_base.trim()) errors.deepseek_api_base = 'API Base URL 不能为空'
    if (!form.deepseek.api_key.trim()) errors.deepseek_api_key = 'API Key 不能为空'
    if (!form.deepseek.model_name.trim()) errors.deepseek_model_name = '模型名称不能为空'
    if (!Number.isFinite(form.deepseek.request_timeout) || form.deepseek.request_timeout < 1) {
      errors.deepseek_timeout = '超时时间必须为大于 0 的数字'
    }
    if (!Number.isInteger(form.deepseek.max_retry_times) || form.deepseek.max_retry_times < 0) {
      errors.deepseek_retry = '重试次数必须为非负整数'
    }
  }

  if (form.ollama.enable_switch) {
    if (!form.ollama.api_base.trim()) errors.ollama_api_base = 'Ollama API Base URL 不能为空'
    if (!form.ollama.model_name.trim()) errors.ollama_model_name = '本地模型名称不能为空'
  }

  return Object.keys(errors).length === 0
}

const loadSettings = async () => {
  try {
    const res = await api.settings.getLLM()
    if (res.data) {
      Object.assign(form.deepseek, res.data.deepseek || {})
      Object.assign(form.ollama, res.data.ollama || {})
    }
  } catch (e) {
    console.error('加载设置失败', e)
  } finally {
    isLoading.value = false
  }
}

const saveSettings = async () => {
  if (!validate()) {
    toast.error('请先修正表单中的必填项错误')
    return
  }
  isSaving.value = true
  try {
    await api.settings.saveLLM(form)
    toast.success('配置保存成功！')
  } catch (e) {
    console.error('保存设置失败', e)
    toast.error('保存失败，请检查网络或后台服务。')
  } finally {
    isSaving.value = false
  }
}

// 批次B:云端模型连通性检测
const testConnectivity = async () => {
  isPinging.value = true
  pingResult.value = ''
  try {
    const res = await api.settings.pingDeepseek()
    const data = res.data || {}
    if (data.status === 'ok') {
      const keyStatus = data.has_api_key ? '已配置密钥' : '未配置密钥'
      const enStatus = data.is_enabled ? '已启用' : '未启用'
      pingResult.value = `连通正常 · ${data.model_name || '模型'} @ ${data.api_base || ''} · ${keyStatus} · ${enStatus}`
    } else {
      pingResult.value = `配置异常: ${data.message || '未知'}`
    }
  } catch (e) {
    pingResult.value = `无法连接后端: ${e?.message || '失败'}`
  } finally {
    isPinging.value = false
  }
}

onMounted(() => {
  loadSettings()
})
</script>
