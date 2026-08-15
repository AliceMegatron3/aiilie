<template>
  <div class="subconscious-stream-widget" :class="{ 'expanded': isExpanded }">
    <div class="widget-header" @click="toggleExpand">
      <div class="pulse-indicator"></div>
      <span class="widget-title">潜意识发散流</span>
      <span class="toggle-icon">{{ isExpanded ? '▼' : '▲' }}</span>
    </div>
    
    <div class="widget-content" v-show="isExpanded">
      <div class="stream-text">{{ streamContent }}</div>
      <div class="widget-footer" v-if="fileName">
        当前碎片: {{ fileName }}
      </div>
    </div>
  </div>
</template>

<script lang="ts" setup>
import { ref } from 'vue'
import { useWebSocket } from '../composables/useWebSocket'

const isExpanded = ref(true)
const streamContent = ref('等待大脑活跃...')
const fileName = ref('')

const toggleExpand = () => {
  isExpanded.value = !isExpanded.value
}

useWebSocket({
  onMessage: (data: any) => {
    if (data.type === 'subconscious_stream') {
      streamContent.value = data.content
      fileName.value = data.file || ''
    }
  }
})
</script>

<style scoped>
.subconscious-stream-widget {
  position: fixed;
  bottom: 20px;
  right: 20px;
  width: 320px;
  background: rgba(18, 18, 18, 0.85);
  backdrop-filter: blur(10px);
  border: 1px solid rgba(255, 255, 255, 0.1);
  border-radius: 8px;
  color: #fff;
  z-index: 9999;
  box-shadow: 0 4px 20px rgba(0, 0, 0, 0.5);
  transition: all 0.3s ease;
  overflow: hidden;
  display: flex;
  flex-direction: column;
}

.subconscious-stream-widget:hover {
  border-color: rgba(0, 212, 255, 0.3);
}

.widget-header {
  padding: 12px 16px;
  display: flex;
  align-items: center;
  cursor: pointer;
  background: rgba(255, 255, 255, 0.05);
  user-select: none;
}

.pulse-indicator {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #00d4ff;
  margin-right: 12px;
  animation: pulse 2s infinite;
}

@keyframes pulse {
  0% { transform: scale(0.95); box-shadow: 0 0 0 0 rgba(0, 212, 255, 0.7); }
  70% { transform: scale(1); box-shadow: 0 0 0 6px rgba(0, 212, 255, 0); }
  100% { transform: scale(0.95); box-shadow: 0 0 0 0 rgba(0, 212, 255, 0); }
}

.widget-title {
  flex: 1;
  font-size: 14px;
  font-weight: 600;
  letter-spacing: 1px;
}

.toggle-icon {
  font-size: 10px;
  opacity: 0.5;
}

.widget-content {
  padding: 16px;
  max-height: 400px;
  overflow-y: auto;
  border-top: 1px solid rgba(255, 255, 255, 0.05);
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.stream-text {
  font-family: 'Consolas', 'Courier New', Courier, monospace;
  font-size: 12px;
  line-height: 1.6;
  white-space: pre-wrap;
  color: rgba(255, 255, 255, 0.7);
}

.widget-footer {
  font-size: 10px;
  color: rgba(255, 255, 255, 0.3);
  text-align: right;
}
</style>
