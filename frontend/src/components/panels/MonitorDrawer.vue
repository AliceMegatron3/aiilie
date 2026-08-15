<template>
  <!-- 附属模块：监控面板 (置于 AI 对话下方，默认折叠) -->
  <div class="shrink-0 flex flex-col bg-[#0f0f11] border-t border-gray-900">
    <!-- 附属模块控制头 -->
    <div
      @click="expanded = !expanded"
      class="flex items-center justify-between p-2 cursor-pointer hover:bg-[#18181b] transition-colors select-none text-[10px] text-gray-500 uppercase tracking-widest"
    >
      <span class="flex items-center space-x-2">
        <span>系统资源与会话监控</span>
      </span>
      <span class="transform transition-transform" :class="expanded ? 'rotate-180' : ''">▼</span>
    </div>

    <!-- 监控面板容器 -->
    <div v-show="expanded" class="p-2 space-y-2 bg-[#121212] border-t border-gray-800 max-h-64 overflow-y-auto">
      <SessionManagerPanel />

      <!-- 系统队列监控 (Batch 5) -->
      <div v-if="queueInfo" class="flex justify-between items-center mt-2 p-2 bg-[#18181b] rounded border border-gray-800">
        <div class="flex items-center gap-3 text-[10px] text-gray-500 font-mono">
          <span>队列: {{ queueInfo.pending_tasks_count || 0 }}</span>
          <span v-if="queueInfo.batch1_engine?.mounted">
            Batch1引擎: {{ queueInfo.batch1_engine.queue_size || 0 }}/{{ queueInfo.batch1_engine.tracked_tasks || 0 }}
          </span>
        </div>
        <button @click="$emit('interrupt')" class="text-[10px] text-red-500 hover:text-red-400 border border-red-900 bg-red-900/20 px-2 py-0.5 rounded transition-colors">
          紧急熔断
        </button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import SessionManagerPanel from '../SessionManagerPanel.vue'

interface QueueInfo {
  pending_tasks_count?: number
  batch1_engine?: { mounted?: boolean; queue_size?: number; tracked_tasks?: number }
}

defineProps<{ queueInfo: QueueInfo | null }>()

defineEmits(['interrupt'])

const expanded = ref(false)
</script>
