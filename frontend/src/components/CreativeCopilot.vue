<template>
  <div class="creative-copilot-overlay" v-if="isVisible">
    <div class="copilot-container">
      <header class="copilot-header">
        <h2>🔮 创作预估与反思沙盘 (Creative Copilot Hub)</h2>
        <button class="close-btn" @click="closeHub">×</button>
      </header>

      <!-- 方向报告 B 类降级：无真实后端 provider，明确标注 DISABLED，
           不再用前端硬编码假数据伪装“已完成”的分析/预估。 -->
      <div class="copilot-body">
        <div class="disabled-banner">
          <div class="disabled-badge">DISABLED</div>
          <div class="disabled-row"><span class="field">feature</span><code>{{ featureId }}</code></div>
          <div class="disabled-row"><span class="field">reason</span><code>{{ reason }}</code></div>
          <p class="disabled-note">
            该能力暂未接入真实后端 provider，当前不提供走势预估、反思建议或量化结论，
            避免以未验证的假数据误导创作决策。待接入可用的拆书/量化/反思服务后再开放。
          </p>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
const props = defineProps({
  isVisible: {
    type: Boolean,
    default: false
  },
  bookId: {
    type: String,
    default: 'book_001_遮天'
  }
})

const emit = defineEmits(['close'])

// 与后端 feature_status.feature_disabled 对齐的结构化字段
const featureId = 'creative_copilot.analysis'
const reason = 'provider_not_configured'

const closeHub = () => {
  emit('close')
}
</script>

<style scoped>
.creative-copilot-overlay {
  position: fixed;
  top: 0;
  left: 0;
  width: 100vw;
  height: 100vh;
  background: rgba(0, 0, 0, 0.6);
  backdrop-filter: blur(4px);
  z-index: 9999;
  display: flex;
  justify-content: center;
  align-items: center;
}

.copilot-container {
  width: 90%;
  max-width: 1200px;
  height: 85vh;
  background: #1e1e2e;
  border-radius: 12px;
  border: 1px solid #3b3b54;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  box-shadow: 0 20px 50px rgba(0,0,0,0.5);
  color: #cdd6f4;
}

.copilot-header {
  padding: 20px;
  background: #181825;
  border-bottom: 1px solid #3b3b54;
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.copilot-header h2 {
  margin: 0;
  font-size: 1.2rem;
  color: #f5e0dc;
}

.close-btn {
  background: transparent;
  border: none;
  color: #f38ba8;
  font-size: 1.5rem;
  cursor: pointer;
}

.copilot-body {
  flex: 1;
  padding: 20px;
  overflow-y: auto;
}

.disabled-banner {
  background: #181825;
  border: 1px solid #313244;
  border-radius: 8px;
  padding: 20px;
}

.disabled-badge {
  display: inline-block;
  background: #fab387;
  color: #11111b;
  font-weight: bold;
  font-size: 0.8rem;
  padding: 3px 10px;
  border-radius: 4px;
  margin-bottom: 14px;
}

.disabled-row {
  display: flex;
  align-items: baseline;
  gap: 10px;
  margin-bottom: 8px;
  font-size: 0.9rem;
}

.disabled-row .field {
  color: #89b4fa;
  width: 80px;
  font-family: monospace;
}

.disabled-row code {
  background: #11111b;
  color: #f5e0dc;
  padding: 2px 8px;
  border-radius: 4px;
  font-family: monospace;
}

.disabled-note {
  margin-top: 14px;
  color: #cdd6f4;
  font-size: 0.9rem;
  line-height: 1.6;
  border-left: 4px solid #fab387;
  padding-left: 10px;
}
</style>