<template>
  <div class="h-full w-full overflow-y-auto bg-[#1e1e24] p-6">
    <div class="space-y-6">
      <!-- Header -->
      <div>
        <h2 class="text-xs font-semibold text-gray-500 uppercase tracking-wider mb-1">智能体控制台</h2>
        <p class="text-[11px] text-gray-400">运营专员状态 · 风格透镜 · 模型分配</p>
      </div>

      <AgentDashboard />

      <!-- 后端联动状态 -->
      <div class="grid grid-cols-1 gap-4 border-t border-[#2a2a30] pt-6">
        <div>
          <div class="flex items-center gap-2 mb-3">
            <h3 class="text-xs font-semibold text-gray-300">模型与系统状态</h3>
            <button @click="loadBackend" class="text-[10px] bg-[#2a2a30] hover:bg-gray-600 text-gray-300 rounded px-2 py-0.5">刷新</button>
          </div>
          <div class="bg-[#18181b] border border-[#2a2a30] rounded-xl p-4">
            <div class="grid grid-cols-1 sm:grid-cols-2 gap-3 text-xs">
              <div>
                <div class="text-[10px] text-gray-500">主模型 (Primary)</div>
                <div class="text-gray-200 mt-0.5">{{ agentStore.primaryModel || '未配置' }}</div>
              </div>
              <div>
                <div class="text-[10px] text-gray-500">工作模型 (Worker)</div>
                <div class="text-gray-200 mt-0.5">{{ agentStore.workerModel || '未配置' }}</div>
              </div>
              <div>
                <div class="text-[10px] text-gray-500">已检测本地模型</div>
                <div class="text-gray-200 mt-0.5 flex flex-wrap gap-1">
                  <span v-if="agentStore.availableModels.length === 0" class="text-gray-600">无 / 未启动 Ollama</span>
                  <span v-for="m in agentStore.availableModels" :key="m.id" class="bg-[#2a2a30] px-1.5 py-0.5 rounded text-[10px]">
                    {{ m.name }}
                  </span>
                </div>
              </div>
              <div>
                <div class="text-[10px] text-gray-500">后端状态码</div>
                <div class="text-gray-200 mt-0.5">{{ backendStatus ?? '未检测' }}</div>
              </div>
            </div>
          </div>
          <div v-if="backendError" class="mt-2 text-[10px] text-red-400">{{ backendError }}</div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import AgentDashboard from '../panels/AgentDashboard.vue'
import { useAgentStore } from '../../stores/agentStore'
import { api } from '../../api'

const agentStore = useAgentStore()
const backendStatus = ref(null)
const backendError = ref('')

const loadBackend = async () => {
  try {
    await agentStore.fetchFromConfig()
    const res = await api.novelAgent.systemStatus()
    backendStatus.value = res.data?.data?.status || res.data?.status || 'ok'
    backendError.value = ''
  } catch (e) {
    backendError.value = `后端状态读取失败: ${e?.message || '连接失败'}`
  }
}

onMounted(() => loadBackend())
</script>
