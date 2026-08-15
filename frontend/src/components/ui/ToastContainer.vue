<template>
  <!-- 统一 Toast 通知容器（右上角堆叠） -->
  <teleport to="body">
    <!-- Toast 列表 -->
    <div class="fixed top-14 right-4 z-[9999] flex flex-col gap-2 items-end pointer-events-none">
      <transition-group name="toast">
        <div
          v-for="t in state.toasts"
          :key="t.id"
          class="toast-item pointer-events-auto px-4 py-2.5 rounded-lg border shadow-xl backdrop-blur-md text-sm max-w-sm transition-all duration-200"
          :class="toastClass(t.type)"
          @click="toast.dismiss(t.id)"
        >
          <span class="mr-2">{{ toastIcon(t.type) }}</span>{{ t.message }}
        </div>
      </transition-group>
    </div>

    <!-- 确认对话框（toast.confirm 的渲染载体） -->
    <div
      v-if="state.confirmState"
      class="fixed inset-0 z-[10000] bg-black/50 backdrop-blur-sm flex items-center justify-center"
      @click.self="resolveConfirm(false)"
    >
      <div class="bg-[#1e1e24] border border-[#3f3f46] rounded-xl shadow-2xl p-6 w-96 max-w-[90vw]">
        <h3 class="text-base font-semibold text-gray-100 mb-3">请确认操作</h3>
        <p class="text-sm text-gray-300 whitespace-pre-line leading-relaxed mb-6">{{ state.confirmState.message }}</p>
        <div class="flex justify-end gap-3">
          <button
            class="px-4 py-2 text-sm rounded-lg bg-[#2a2a30] hover:bg-gray-600 text-gray-300 transition-colors border border-gray-700"
            @click="resolveConfirm(false)"
          >取消</button>
          <button
            class="px-4 py-2 text-sm rounded-lg bg-red-600/90 hover:bg-red-500 text-white transition-colors"
            @click="resolveConfirm(true)"
          >确认</button>
        </div>
      </div>
    </div>

    <!-- 文本输入对话框（toast.prompt 的渲染载体） -->
    <div
      v-if="state.promptState"
      class="fixed inset-0 z-[10000] bg-black/50 backdrop-blur-sm flex items-center justify-center"
      @click.self="resolvePrompt(null)"
    >
      <div class="bg-[#1e1e24] border border-[#3f3f46] rounded-xl shadow-2xl p-6 w-96 max-w-[90vw]">
        <h3 class="text-base font-semibold text-gray-100 mb-3">{{ state.promptState.title }}</h3>
        <input
          ref="promptInput"
          v-model="promptValue"
          type="text"
          class="w-full bg-[#121212] border border-gray-700 rounded-lg px-3 py-2 text-sm text-gray-100 focus:outline-none focus:border-indigo-500 mb-6"
          @keydown.enter="resolvePrompt(promptValue)"
        />
        <div class="flex justify-end gap-3">
          <button
            class="px-4 py-2 text-sm rounded-lg bg-[#2a2a30] hover:bg-gray-600 text-gray-300 transition-colors border border-gray-700"
            @click="resolvePrompt(null)"
          >取消</button>
          <button
            class="px-4 py-2 text-sm rounded-lg bg-indigo-600/90 hover:bg-indigo-500 text-white transition-colors"
            @click="resolvePrompt(promptValue)"
          >确定</button>
        </div>
      </div>
    </div>
  </teleport>
</template>

<script setup>
import { ref, watch, nextTick } from 'vue'
import { useToastState, toast } from '../../utils/toast'

const state = useToastState()

const promptInput = ref(null)
const promptValue = ref('')

// 弹层打开时回填默认值并聚焦
watch(() => state.promptState, async (ps) => {
  if (ps) {
    promptValue.value = ps.defaultValue || ''
    await nextTick()
    promptInput.value?.focus()
  }
})

const toastClass = (type) => ({
  success: 'bg-emerald-950/80 border-emerald-700/50 text-emerald-200',
  error: 'bg-red-950/80 border-red-700/50 text-red-200',
  info: 'bg-indigo-950/80 border-indigo-700/50 text-indigo-200',
  warning: 'bg-amber-950/80 border-amber-700/50 text-amber-200',
}[type] || 'bg-gray-900/80 border-gray-700 text-gray-200')

const toastIcon = (type) => ({
  success: '✓',
  error: '✕',
  info: 'ℹ',
  warning: '⚠',
}[type] || 'ℹ')

const resolveConfirm = (ok) => {
  toast._resolveConfirm(ok)
}

const resolvePrompt = (value) => {
  toast._resolvePrompt(value)
}
</script>

<style scoped>
.toast-enter-active,
.toast-leave-active {
  transition: all 0.25s ease;
}
.toast-enter-from {
  opacity: 0;
  transform: translateX(24px);
}
.toast-leave-to {
  opacity: 0;
  transform: translateY(-8px);
}
</style>
