<template>
  <!-- 输入指令区 -->
  <div class="shrink-0 bg-[#18181b] border-t border-[#2a2a30] p-3 flex flex-col space-y-3 z-10">
    <!-- 多行命令输入框 -->
    <textarea
      v-model="commandInput"
      @keydown.enter.prevent="emitSend"
      class="w-full bg-[#27272a] border border-gray-700 rounded-lg p-3 text-sm text-gray-200 focus:outline-none focus:border-purple-500 focus:ring-1 focus:ring-purple-500 transition-all resize-none shadow-inner"
      rows="3"
      placeholder="输入您的指令 (Enter发送)..."
    ></textarea>

    <!-- 模式选择与发送按钮区域 -->
    <div class="flex items-center justify-between">
      <div class="flex space-x-2 bg-[#121212] p-1 rounded-lg border border-gray-800">
        <label class="flex items-center space-x-1 cursor-pointer px-3 py-1.5 rounded-md transition-colors hover:bg-[#27272a]"
               :class="currentMode === 'rapid' ? 'bg-[#27272a] border border-gray-700' : 'border border-transparent'">
          <input type="radio" value="rapid" v-model="currentMode" @change="$emit('mode-change', currentMode)" class="hidden">
          <span class="text-xs font-medium" :class="currentMode === 'rapid' ? 'text-blue-400' : 'text-gray-500'">⚡ 快速(Rapid)</span>
        </label>

        <label class="flex items-center space-x-1 cursor-pointer px-3 py-1.5 rounded-md transition-colors hover:bg-[#27272a]"
               :class="currentMode === 'think' ? 'bg-[#27272a] border border-gray-700' : 'border border-transparent'">
          <input type="radio" value="think" v-model="currentMode" @change="$emit('mode-change', currentMode)" class="hidden">
          <span class="text-xs font-medium" :class="currentMode === 'think' ? 'text-purple-400' : 'text-gray-500'">🧠 思考(Think)</span>
        </label>

        <label class="flex items-center space-x-1 cursor-pointer px-3 py-1.5 rounded-md transition-colors hover:bg-[#27272a]"
               :class="currentMode === 'complex' ? 'bg-[#27272a] border border-red-900/50' : 'border border-transparent'">
          <input type="radio" value="complex" v-model="currentMode" @change="$emit('mode-change', currentMode)" class="hidden">
          <span class="text-xs font-bold" :class="currentMode === 'complex' ? 'text-red-500' : 'text-gray-500'">🔥 繁杂(Complex)</span>
        </label>
      </div>

      <!-- 发送按钮 -->
      <button
        @click="emitSend"
        :disabled="isProcessing || !commandInput.trim()"
        class="px-5 py-2 bg-purple-600/90 hover:bg-purple-500 text-white text-xs font-bold rounded-lg disabled:opacity-50 transition-colors shadow-[0_0_10px_rgba(147,51,234,0.3)] hover:shadow-[0_0_15px_rgba(147,51,234,0.5)]"
      >
        发送
      </button>
    </div>
  </div>
</template>

<script setup>
import { ref, watch } from 'vue'

const props = defineProps({
  isProcessing: { type: Boolean, default: false },
  modelValue: { type: String, default: '' },
  mode: { type: String, default: 'rapid' },
})

const emit = defineEmits(['send', 'mode-change', 'update:modelValue', 'update:mode'])

const commandInput = ref(props.modelValue)
const currentMode = ref(props.mode)

watch(() => props.modelValue, (v) => { commandInput.value = v })
watch(() => props.mode, (v) => { currentMode.value = v })

const emitSend = () => {
  const text = commandInput.value.trim()
  if (!text) return
  emit('send', text)
  commandInput.value = ''
}

defineExpose({ clear: () => { commandInput.value = '' } })
</script>
