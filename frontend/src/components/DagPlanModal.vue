<template>
  <div v-if="isOpen" class="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm">
    <div class="bg-[#18181b] border border-[#2a2a30] shadow-2xl rounded-xl w-full max-w-2xl flex flex-col overflow-hidden">
      <!-- Header -->
      <div class="px-6 py-4 border-b border-[#2a2a30] flex justify-between items-center bg-[#1e1e24]">
        <h3 class="text-lg font-bold text-gray-200 flex items-center gap-2">
          <span class="text-indigo-400">⚡</span> 总督调度计划确认
        </h3>
        <button @click="closeModal" class="text-gray-400 hover:text-white transition-colors">
          ✕
        </button>
      </div>

      <!-- Content -->
      <div class="p-6 space-y-6">
        <!-- 嗅探结果 -->
        <div class="bg-indigo-900/20 border border-indigo-800/50 rounded-lg p-4">
          <h4 class="text-xs font-semibold text-indigo-400 uppercase tracking-wider mb-1">总督嗅探报告</h4>
          <p class="text-sm text-gray-300">{{ dag?.chapter_summary_sniffed || '未获取到嗅探结果' }}</p>
        </div>

        <!-- 执行节点 DAG 图展示 -->
        <div>
          <h4 class="text-xs font-semibold text-gray-500 uppercase tracking-wider mb-3">算力调度分配 (专员状态)</h4>
          <div class="space-y-3">
            <div 
              v-for="(node, index) in dag?.nodes" 
              :key="index"
              class="flex items-center justify-between p-3 rounded-lg border transition-all"
              :class="node.is_active ? 'bg-[#1e1e24] border-green-800/30' : 'bg-[#121212] border-gray-800 opacity-60'"
            >
              <div class="flex items-center gap-3">
                <!-- Status Icon -->
                <div class="w-2 h-2 rounded-full" :class="node.is_active ? 'bg-green-500 shadow-[0_0_8px_rgba(34,197,94,0.6)]' : 'bg-gray-500'"></div>
                
                <div>
                  <div class="text-sm font-bold text-gray-200">
                    {{ formatRoleName(node.role) }}
                    <span v-if="!node.is_active" class="ml-2 text-[10px] bg-gray-700 text-gray-300 px-1.5 py-0.5 rounded">已挂起休眠</span>
                  </div>
                  <div class="text-xs text-gray-400 mt-0.5 line-clamp-1">{{ node.reason }}</div>
                </div>
              </div>
              
              <div class="text-right shrink-0">
                <div class="text-[10px] text-gray-500">分配模型</div>
                <div class="text-xs font-mono text-indigo-400">{{ node.model_assigned || 'N/A' }}</div>
              </div>
            </div>
          </div>
        </div>
      </div>

      <!-- Footer -->
      <div class="px-6 py-4 border-t border-[#2a2a30] flex justify-end gap-3 bg-[#121212]">
        <button @click="closeModal" class="px-4 py-2 text-sm text-gray-400 hover:text-white transition-colors">
          取消
        </button>
        <button @click="confirmPlan" class="px-6 py-2 text-sm font-semibold bg-indigo-600 hover:bg-indigo-500 text-white rounded-lg shadow-lg shadow-indigo-500/20 transition-all">
          授权总督开始量化
        </button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, defineProps, defineEmits } from 'vue'

const props = defineProps({
  isOpen: Boolean,
  dag: Object
})

const emit = defineEmits(['close', 'confirm'])

const closeModal = () => {
  emit('close')
}

const confirmPlan = () => {
  emit('confirm')
}

const formatRoleName = (role) => {
  const map = {
    'lore_expert': '🏛️ 设定纪检委',
    'combat_expert': '⚔️ 战力评估师',
    'emotion_expert': '🎭 情感导师',
  }
  return map[role] || role
}
</script>
