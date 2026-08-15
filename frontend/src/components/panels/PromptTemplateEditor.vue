<template>
  <div class="mt-4 border-t border-[#2a2a30] pt-4 space-y-3">
    <!-- 头部：名称 + 内置/用户标记 -->
    <div class="flex items-center justify-between">
      <div class="flex items-center space-x-2">
        <h3 class="text-sm font-bold text-gray-200">{{ template.name || template.template_id }}</h3>
        <span
          v-if="template.is_builtin"
          class="text-[10px] px-1.5 py-0.5 rounded bg-amber-900/40 text-amber-400"
        >内置 · 只读</span>
        <span v-else class="text-[10px] px-1.5 py-0.5 rounded bg-emerald-900/40 text-emerald-400">用户模板</span>
      </div>
      <span class="text-[10px] font-mono text-gray-500">{{ template.template_id }}</span>
    </div>

    <div v-if="template.is_builtin" class="text-[11px] text-amber-500/90 bg-amber-900/10 border border-amber-900/40 rounded p-2">
      🔒 内置模板不可直接修改；点击保存将自动另存为用户副本（原内置模板保持不变）。
    </div>

    <!-- 模板正文编辑区 -->
    <div>
      <div class="text-[11px] text-gray-500 mb-1">模板正文（Jinja2 语法，支持 {{ variable }} 占位符）</div>
      <textarea
        v-model="draftText"
        :readonly="readonlyMode"
        rows="10"
        spellcheck="false"
        class="w-full bg-[#101014] border border-gray-700 rounded-lg p-3 text-xs font-mono text-gray-300 leading-relaxed focus:border-indigo-500 outline-none resize-y"
        :class="{ 'opacity-60 cursor-not-allowed': readonlyMode }"
        placeholder="模板内容..."
      ></textarea>
    </div>

    <!-- 变量提示（点击插入光标处） -->
    <div v-if="detectedVars.length" class="flex items-center flex-wrap gap-1.5">
      <span class="text-[11px] text-gray-500">检测到变量：</span>
      <button
        v-for="v in detectedVars"
        :key="v"
        @click="insertVariable(v)"
        class="text-[11px] px-2 py-0.5 rounded bg-indigo-900/40 text-indigo-300 border border-indigo-900 hover:bg-indigo-800/50 transition-colors"
        :title="'点击插入 ' + v + ' 占位符'"
      >{{ v }}</button>
    </div>
    <div v-else class="text-[11px] text-gray-600">未检测到变量占位符</div>

    <!-- 渲染预览 -->
    <div>
      <div class="flex items-center justify-between mb-1">
        <span class="text-[11px] text-gray-500">渲染预览</span>
        <div class="flex space-x-1.5">
          <input
            v-model="previewVarsJson"
            class="bg-[#101014] border border-gray-700 rounded px-2 py-1 text-[10px] font-mono text-gray-300 w-48 focus:border-indigo-500 outline-none"
            placeholder='变量 JSON，如 {"x": "值"}'
          />
          <button
            @click="renderPreview"
            class="text-[11px] px-2 py-1 rounded bg-indigo-600 hover:bg-indigo-500 text-white transition-colors"
          >预览</button>
        </div>
      </div>
      <pre
        class="bg-[#0c0c10] border border-gray-800 rounded-lg p-3 text-[11px] text-gray-400 whitespace-pre-wrap max-h-40 overflow-y-auto min-h-[3rem]"
      >{{ previewText || '（点击预览查看渲染结果）' }}</pre>
      <div v-if="renderError" class="text-[11px] text-red-400 mt-1">{{ renderError }}</div>
    </div>

    <!-- 版本历史与回滚 -->
    <div v-if="versions.length" class="space-y-1.5">
      <div class="text-[11px] text-gray-500">版本历史（点击回滚）</div>
      <div
        v-for="v in versions"
        :key="v.version"
        class="flex items-center justify-between bg-[#1f1f22] border border-gray-800 rounded p-1.5 px-2"
      >
        <div class="text-[11px] text-gray-400">
          <span class="text-gray-300 font-mono">v{{ v.version }}</span>
          <span class="ml-2 text-gray-600">{{ v.name }}</span>
          <span class="ml-2 text-gray-600">{{ (v.timestamp || '').slice(0, 16).replace('T', ' ') }}</span>
        </div>
        <button
          @click="rollback(v.version)"
          class="text-[11px] px-2 py-0.5 rounded bg-gray-700 hover:bg-gray-600 text-gray-300 transition-colors"
        >回滚</button>
      </div>
    </div>

    <!-- 操作按钮 -->
    <div class="flex justify-end space-x-2">
      <button
        @click="refresh"
        class="text-xs px-3 py-1.5 rounded bg-gray-700 hover:bg-gray-600 text-gray-300 transition-colors"
      >放弃修改</button>
      <button
        :disabled="readonlyMode || saving"
        @click="save"
        class="text-xs px-3 py-1.5 rounded bg-indigo-600 hover:bg-indigo-500 text-white transition-colors disabled:opacity-50"
      >{{ saving ? '保存中...' : '保存模板' }}</button>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, watch } from 'vue'
import { api } from '../../api'

const props = defineProps({
  template: { type: Object, required: true },
})
const emit = defineEmits(['saved'])

const draftText = ref('')
const previewVarsJson = ref('{}')
const previewText = ref('')
const renderError = ref('')
const versions = ref([])
const saving = ref(false)

const readonlyMode = computed(() => Boolean(props.template?.is_builtin))

// 变量检测：{{ xxx }}
const detectedVars = computed(() => {
  const re = /{{\s*([a-zA-Z_][\w.]*)\s*}}/g
  const found = new Set()
  let m
  while ((m = re.exec(draftText.value)) !== null) found.add(m[1])
  return [...found]
})

watch(
  () => props.template,
  (tpl) => {
    if (tpl) {
      draftText.value = tpl.template_text || ''
      previewText.value = ''
      renderError.value = ''
      loadVersions(tpl.template_id)
    }
  },
  { immediate: true }
)

const refresh = () => {
  draftText.value = props.template?.template_text || ''
}

const insertVariable = (v) => {
  draftText.value += `{{ ${v} }}`
}

const loadVersions = async (templateId) => {
  try {
    const res = await api.prompts.listVersions(templateId)
    if (res.data.status === 'success') versions.value = res.data.data || []
  } catch (e) {
    versions.value = []
  }
}

const renderPreview = async () => {
  renderError.value = ''
  let vars = {}
  try {
    vars = JSON.parse(previewVarsJson.value || '{}')
  } catch (e) {
    renderError.value = '变量 JSON 格式错误'
    return
  }
  try {
    const res = await api.prompts.render(props.template.template_id, vars)
    if (res.data.status === 'success') {
      previewText.value = res.data.rendered_text
    }
  } catch (e) {
    renderError.value = e.message || '渲染失败（可能模板语法错误）'
  }
}

const save = async () => {
  saving.value = true
  try {
    const payload = {
      ...props.template,
      template_text: draftText.value,
      // 内置模板修改 → 自动另存用户副本（后端强制转用户空间）
    }
    await api.prompts.save(payload)
    emit('saved')
  } catch (e) {
    console.error('保存模板失败', e)
  } finally {
    saving.value = false
  }
}

const rollback = async (version) => {
  try {
    await api.prompts.rollback(props.template.template_id, version)
    emit('saved') // 触发父组件重载列表
  } catch (e) {
    console.error('回滚失败', e)
  }
}
</script>
