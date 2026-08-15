<template>
  <div class="flex flex-col h-screen bg-[#1e1e24] text-gray-200 font-sans overflow-hidden">
    <!-- Top Bar -->
    <TopBar />

    <div class="flex flex-1 overflow-hidden">
      <!-- 左侧导航 (固定宽度，轻微磨砂质感) -->
      <LeftPanel class="w-[260px] border-r border-[#2a2a30] bg-[#121212]/85 backdrop-blur-sm flex flex-col shrink-0 z-10 shadow-lg" />
      
      <!-- 中间主工作区 -->
      <main class="flex-1 flex flex-col relative z-0">
        <CenterPanel />
      </main>
      
      <!-- 分割线，用于拖拽改变宽度 -->
      <div 
        class="w-1 bg-[#121212] border-l border-r border-[#2a2a30] cursor-col-resize hover:bg-purple-500/50 active:bg-purple-500 transition-colors z-10 shrink-0"
        @mousedown="startResize"
      ></div>

      <!-- 右侧：AI 交互区（轻微磨砂质感） -->
      <RightPanel :style="{ width: rightPanelWidth + 'px' }" class="bg-[#18181b]/85 backdrop-blur-sm flex flex-col shrink-0 z-10 shadow-xl" />
    </div>

    <!-- 🔮 创作预估沙盘 (全局弹窗) -->
    <CreativeCopilot 
      :isVisible="appStore.copilotVisible" 
      @close="appStore.toggleCopilot()" 
    />

    <!-- 统一 Toast 容器 -->
    <ToastContainer />
    
    <!-- 潜意识流展示悬浮窗 -->
    <SubconsciousStream />
  </div>
</template>

<script setup>
import { ref, onMounted, onUnmounted } from 'vue'
import TopBar from './components/TopBar.vue'
import LeftPanel from './components/LeftPanel.vue'
import CenterPanel from './components/CenterPanel.vue'
import RightPanel from './components/RightPanel.vue'
import CreativeCopilot from './components/CreativeCopilot.vue'
import ToastContainer from './components/ui/ToastContainer.vue'
import SubconsciousStream from './components/SubconsciousStream.vue'
import { useAppStore } from './stores/useAppStore'

const appStore = useAppStore()
const rightPanelWidth = ref(380) // 默认宽度
const isResizing = ref(false)

const startResize = (e) => {
  isResizing.value = true
  document.body.style.cursor = 'col-resize'
  document.addEventListener('mousemove', onResize)
  document.addEventListener('mouseup', stopResize)
}

const onResize = (e) => {
  if (!isResizing.value) return
  // 计算新宽度：整个窗口宽度减去当前鼠标 X 坐标
  const newWidth = document.body.clientWidth - e.clientX
  // 限制拖拽最小和最大宽度
  if (newWidth > 300 && newWidth < 800) {
    rightPanelWidth.value = newWidth
  }
}

const stopResize = () => {
  isResizing.value = false
  document.body.style.cursor = ''
  document.removeEventListener('mousemove', onResize)
  document.removeEventListener('mouseup', stopResize)
}

onUnmounted(() => {
  document.removeEventListener('mousemove', onResize)
  document.removeEventListener('mouseup', stopResize)
})
</script>

<style>
/* 隐藏原生滚动条并保持毛玻璃质感底色 */
::-webkit-scrollbar {
  width: 6px;
  height: 6px;
}
::-webkit-scrollbar-thumb {
  background-color: #3f3f46;
  border-radius: 4px;
}
::-webkit-scrollbar-track {
  background: transparent;
}
</style>
