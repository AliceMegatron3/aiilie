<template>
  <div v-if="isOpen" class="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm" @click.self="close">
    <div class="bg-[#18181b] border border-[#2a2a30] shadow-2xl rounded-xl w-full max-w-md flex flex-col overflow-hidden">
      <!-- Header -->
      <div class="px-6 py-4 border-b border-[#2a2a30] flex justify-between items-center bg-[#1e1e24]">
        <h3 class="text-lg font-bold text-gray-200 flex items-center gap-2">
          <span class="text-indigo-400">⚡</span> 量化设置
        </h3>
        <button @click="close" class="text-gray-400 hover:text-white transition-colors">✕</button>
      </div>

      <!-- Content -->
      <div class="p-6 space-y-5">
        <div class="text-xs text-gray-500">对书籍「{{ bookTitle || bookId }}」执行量化</div>

        <!-- 量化模式 -->
        <div>
          <label class="text-[11px] font-medium text-gray-400 mb-1 block">量化类型</label>
          <div class="flex gap-2">
            <button
              v-for="m in modeOptions"
              :key="m.key"
              @click="mode = m.key"
              class="flex-1 px-3 py-2 rounded text-xs transition-colors border"
              :class="mode === m.key ? 'bg-indigo-600/20 border-indigo-500 text-indigo-300' : 'bg-[#121212] border-[#2a2a30] text-gray-400 hover:border-gray-600'"
            >{{ m.label }}</button>
          </div>
        </div>

        <!-- 模型选择 -->
        <div>
          <label class="text-[11px] font-medium text-gray-400 mb-1 block">量化模型</label>
          <select v-model="model" class="w-full bg-[#121212] border border-[#3f3f46] rounded px-2 py-1.5 text-xs text-white focus:outline-none focus:border-indigo-500">
            <option value="" disabled>选择模型...</option>
            <option v-for="m in allModels" :key="m.id" :value="m.name">{{ m.type === 'cloud' ? '☁️' : '🏠' }} {{ m.name }}</option>
          </select>
          <button @click="$emit('refresh-models')" class="mt-1 text-[10px] text-indigo-400 hover:text-indigo-300">刷新模型列表</button>
        </div>

        <!-- 提取方式 -->
        <div>
          <label class="text-[11px] font-medium text-gray-400 mb-1 block">提取方式</label>
          <select v-model="extraction" class="w-full bg-[#121212] border border-[#3f3f46] rounded px-2 py-1.5 text-xs text-white focus:outline-none focus:border-indigo-500">
            <option value="auto">auto（LLM 优先，无模型自动回退规则）</option>
            <option value="llm">llm（仅大模型提取）</option>
            <option value="rule">rule（仅规则提取，离线快速）</option>
          </select>
        </div>

        <!-- 渐进量化轮次 -->
        <div>
          <label class="text-[11px] font-medium text-gray-400 mb-1 block">量化轮次</label>
          <div class="flex gap-2">
            <button v-for="r in rounds" :key="r.key" @click="quantizeRound = r.key"
              class="flex-1 px-2 py-1.5 rounded text-[11px] border transition-colors text-center"
              :class="quantizeRound === r.key ? 'bg-indigo-600/20 border-indigo-500 text-indigo-300' : 'bg-[#121212] border-[#2a2a30] text-gray-400 hover:border-gray-600'"
            >{{ r.label }}</button>
          </div>
          <div class="text-[9px] text-gray-600 mt-1">{{ roundHint }}</div>
        </div>

        <p class="text-[10px] text-gray-600 leading-relaxed">
          提示：本地 Ollama 模型消耗显存，云端模型按 token 计费。规则提取不调模型、速度最快。
        </p>
      </div>

      <!-- Footer -->
      <div class="px-6 py-4 border-t border-[#2a2a30] flex justify-end gap-2 bg-[#1e1e24]">
        <button @click="close" class="text-xs text-gray-400 hover:text-white px-3 py-1.5">取消</button>
        <button @click="confirm" :disabled="!model" class="text-xs bg-indigo-600 hover:bg-indigo-500 text-white px-4 py-1.5 rounded disabled:opacity-50 disabled:cursor-not-allowed">
          开始量化
        </button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, watch } from 'vue'

const props = defineProps<{
  isOpen: boolean
  bookId: string | null
  bookTitle: string | null
  allModels: { id: string; name: string; type: string }[]
  defaultModel: string
  defaultMode?: string
}>()

const emit = defineEmits<{
  (e: 'close'): void
  (e: 'refresh-models'): void
  (e: 'confirm', payload: { model: string; extraction: string; mode: string; quantizeRound: number }): void
}>()

const modeOptions = [
  { key: 'info', label: '资料卡' },
  { key: 'data', label: '数据卡' },
  { key: 'both', label: '资料+数据' },
]
const rounds = [
  { key: 1, label: '①粗扫', hint: '快速扫描，提取显性信息' },
  { key: 2, label: '②深挖', hint: '深度分析，提取隐性信息' },
  { key: 3, label: '③精炼', hint: '合并重复，提升质量' },
]
const roundHints: Record<number, string> = {
  1: '粗扫：快速提取世界观/人名/地名等显性设定',
  2: '深挖：用 LLM 深度分析关系/情感/伏笔等隐性信息',
  3: '精炼：基于已有卡片合并重复、提升质量、补全缺失',
}

const mode = ref('both')
const model = ref('')
const extraction = ref('auto')
const quantizeRound = ref(1)
const roundHint = ref(roundHints[1])

watch(() => quantizeRound.value, (r) => { roundHint.value = roundHints[r] || '' })

watch(() => props.isOpen, (open) => {
  if (open) {
    mode.value = props.defaultMode || 'both'
    model.value = props.defaultModel
    extraction.value = 'auto'
    quantizeRound.value = 1
    roundHint.value = roundHints[1]
  }
})

const close = () => emit('close')
const confirm = () => {
  if (!model.value) return
  emit('confirm', { model: model.value, extraction: extraction.value, mode: mode.value, quantizeRound: quantizeRound.value })
}
</script>
