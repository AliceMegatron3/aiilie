<template>
  <!-- 对话滚动容器 -->
  <div v-show="expanded" class="flex-1 p-4 overflow-y-auto space-y-4 min-h-0" ref="chatContainer">
    <div v-for="(msg, index) in messages" :key="index"
         class="text-sm p-3 rounded-lg border border-gray-800 bg-[#27272a]/50 text-gray-300">

      <div v-if="msg.role === 'user'" class="text-blue-400 mb-1 text-xs font-bold">You:</div>
      <div v-else class="text-purple-400 mb-1 text-xs font-bold font-mono">No.0 AI:</div>

      <div class="whitespace-pre-wrap leading-relaxed">{{ msg.content }}</div>

      <!-- 量化进度条 -->
      <div v-if="msg.role === 'ai' && msg.quantize_progress" class="mt-3 p-3 bg-[#18181b] border border-emerald-900/50 rounded-lg text-xs text-gray-400">
        <div class="flex items-center justify-between mb-2">
          <span class="text-emerald-400 flex items-center gap-1">
            <span class="w-1.5 h-1.5 rounded-full bg-emerald-400" :class="msg.quantize_progress.percent < 100 ? 'animate-pulse' : ''"></span>
            量化进度 · {{ msg.quantize_progress.round_label || '' }}
          </span>
          <span class="text-emerald-300 font-mono">{{ msg.quantize_progress.percent }}%</span>
        </div>
        <div class="h-2 w-full bg-[#2a2a30] rounded-full overflow-hidden">
          <div
            class="h-full bg-emerald-400 transition-all duration-500 ease-out rounded-full"
            :style="{ width: `${msg.quantize_progress.percent}%` }"
          ></div>
        </div>
        <div class="text-[10px] text-gray-600 mt-1">分段 {{ msg.quantize_progress.completed }}/{{ msg.quantize_progress.total }} · 本段 {{ msg.quantize_progress.cards_in_seg }} 张卡片</div>
      </div>

      <!-- 深度思考进度 (WebSocket 推送) -->
      <div v-if="msg.role === 'ai' && msg.thinking_progress" class="mt-3 p-3 bg-[#18181b] border border-gray-700/50 rounded-lg text-xs text-gray-400 font-mono">
        <div class="text-purple-500 mb-2 flex items-center space-x-1">
          <span class="animate-pulse">🧠</span>
          <span>深度思考阶段 (Thinking Process)</span>
        </div>
        <div class="whitespace-pre-wrap border-l-2 border-purple-500/30 pl-2 ml-1">
          {{ msg.thinking_progress }}
        </div>
      </div>

    </div>

    <!-- 加载动画 -->
    <div v-if="isProcessing" class="text-xs text-gray-500 animate-pulse flex items-center space-x-2 p-2">
      <div class="w-1.5 h-1.5 bg-purple-500 rounded-full"></div>
      <span>AI 正在响应...</span>
    </div>
  </div>
</template>

<script setup>
import { ref, nextTick, watch } from 'vue'

const props = defineProps({
  messages: { type: Array, required: true },
  isProcessing: { type: Boolean, default: false },
  expanded: { type: Boolean, default: true },
})

const chatContainer = ref(null)

const scrollToBottom = async () => {
  await nextTick()
  if (chatContainer.value) {
    chatContainer.value.scrollTop = chatContainer.value.scrollHeight
  }
}

// 消息条数变化时自动滚动（内容级变化由父组件调用 scrollToBottom 处理）
watch(
  () => props.messages.length,
  () => scrollToBottom(),
)

defineExpose({ scrollToBottom })
</script>
