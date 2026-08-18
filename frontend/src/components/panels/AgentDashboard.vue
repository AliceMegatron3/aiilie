<template>
  <div class="space-y-6 p-4">
    <!-- 方向报告 B 类：专员开关/风格透镜仅改前端 Pinia，未接到后端任务执行链路，
         明确标注 DISABLED（待插件内核/受控执行能力成型后再接入），避免误以为已生效。 -->
    <div class="bg-[#18181b] border border-[#2a2a30] rounded-xl p-3 text-[10px] text-gray-400 leading-relaxed">
      <span class="font-bold text-amber-400">DISABLED</span>
      <span class="ml-1">· feature: agent.specialist_control · reason: provider_not_configured</span>
      <div class="mt-1">当前专员开关与风格透镜仅保存在本端（前端状态），尚未接入后端任务执行与插件内核，
        改动不会影响实际的拆书/量化/反思任务。待 V0.3–V0.5 受控执行与插件 SDK 成型后再接入。</div>
    </div>

    <!-- Header -->
    <div>
      <h2 class="text-xs font-semibold text-gray-500 uppercase tracking-wider mb-1">六部专员控制台</h2>
      <p class="text-[11px] text-gray-400">管理子智能体的职责与模型分配</p>
    </div>

    <!-- 专员开关 -->
    <div class="space-y-3">
      <!-- 设定纪检委 -->
      <div class="bg-[#18181b] border border-[#2a2a30] rounded-xl p-4 flex items-center justify-between">
        <div class="flex items-center gap-3">
          <div class="w-8 h-8 rounded-lg bg-indigo-900/30 text-indigo-400 flex items-center justify-center text-lg">🏛️</div>
          <div>
            <div class="text-sm font-bold text-gray-200">设定纪检委 (Lore)</div>
            <div class="text-[10px] text-gray-400">提取静态世界观与背景设定</div>
          </div>
        </div>
        <div class="flex items-center gap-2">
          <span class="text-[10px] text-gray-500">始终激活</span>
          <div class="w-10 h-5 rounded-full bg-indigo-600 relative cursor-not-allowed opacity-70">
            <div class="absolute right-1 top-1 bottom-1 w-3 bg-white rounded-full"></div>
          </div>
        </div>
      </div>

      <!-- 战力评估师 -->
      <div class="bg-[#18181b] border border-[#2a2a30] rounded-xl p-4 flex items-center justify-between">
        <div class="flex items-center gap-3">
          <div class="w-8 h-8 rounded-lg bg-red-900/30 text-red-400 flex items-center justify-center text-lg">⚔️</div>
          <div>
            <div class="text-sm font-bold text-gray-200">战力评估师 (Combat)</div>
            <div class="text-[10px] text-gray-400">分析功法、境界、战斗胜负</div>
          </div>
        </div>
        <div class="flex flex-col items-end gap-1">
          <select 
            v-model="skillOverrides.combat_expert" 
            class="bg-[#121212] border border-gray-700 rounded px-2 py-1 text-xs text-gray-300 focus:outline-none focus:border-indigo-500"
          >
            <option :value="null">🧠 总督动态嗅探 (推荐)</option>
            <option :value="true">🟢 强制开启</option>
            <option :value="false">🔴 强制关闭</option>
          </select>
        </div>
      </div>

      <!-- 情感导师 -->
      <div class="bg-[#18181b] border border-[#2a2a30] rounded-xl p-4 flex items-center justify-between">
        <div class="flex items-center gap-3">
          <div class="w-8 h-8 rounded-lg bg-pink-900/30 text-pink-400 flex items-center justify-center text-lg">🎭</div>
          <div>
            <div class="text-sm font-bold text-gray-200">情感导师 (Emotion)</div>
            <div class="text-[10px] text-gray-400">提取人物交互、对话、好感度</div>
          </div>
        </div>
        <div class="flex flex-col items-end gap-1">
          <select 
            v-model="skillOverrides.emotion_expert" 
            class="bg-[#121212] border border-gray-700 rounded px-2 py-1 text-xs text-gray-300 focus:outline-none focus:border-indigo-500"
          >
            <option :value="null">🧠 总督动态嗅探 (推荐)</option>
            <option :value="true">🟢 强制开启</option>
            <option :value="false">🔴 强制关闭</option>
          </select>
        </div>
      </div>
    </div>

    <!-- 风格透镜 -->
    <div class="pt-4 border-t border-[#2a2a30]">
      <h3 class="text-xs font-semibold text-gray-500 uppercase tracking-wider mb-2">项目风格透镜 (Style Lens)</h3>
      <p class="text-[10px] text-gray-400 mb-3">为智能体注入风格化认知偏好，提高提取的精准度。</p>
      
      <div class="grid grid-cols-2 gap-2">
        <button 
          v-for="style in ['玄幻修仙', '科幻废土', '都市权谋', '克苏鲁悬疑']" 
          :key="style"
          @click="setStyle(style)"
          class="px-3 py-2 rounded-lg text-xs font-medium border transition-all"
          :class="agentStore.projectStyleLens === style ? 'bg-indigo-600/20 border-indigo-500 text-indigo-400' : 'bg-[#18181b] border-[#2a2a30] text-gray-400 hover:border-gray-600'"
        >
          {{ style }}
        </button>
      </div>
      <div class="mt-2 flex">
        <input 
          v-model="customStyle"
          type="text" 
          placeholder="自定义风格..."
          class="flex-1 bg-[#121212] border border-gray-700 rounded-l-lg px-3 py-1.5 text-xs text-white focus:outline-none focus:border-indigo-500"
        >
        <button @click="setStyle(customStyle)" class="bg-[#2a2a30] hover:bg-gray-600 px-3 py-1.5 rounded-r-lg border border-l-0 border-gray-700 text-xs text-gray-300">
          应用
        </button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive, watch } from 'vue'
import { useAgentStore } from '../../stores/agentStore'

const agentStore = useAgentStore()
const customStyle = ref('')

const skillOverrides = reactive({
  combat_expert: agentStore.skillOverrides.combat_expert,
  emotion_expert: agentStore.skillOverrides.emotion_expert
})

watch(() => skillOverrides.combat_expert, (val) => agentStore.setSkillOverride('combat_expert', val))
watch(() => skillOverrides.emotion_expert, (val) => agentStore.setSkillOverride('emotion_expert', val))

const setStyle = (style) => {
  if (style && style.trim() !== '') {
    agentStore.setStyleLens(style)
  }
}
</script>
