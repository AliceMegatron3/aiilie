<template>
  <div class="creative-copilot-overlay" v-if="isVisible">
    <div class="copilot-container">
      <header class="copilot-header">
        <h2>🔮 创作预估与反思沙盘 (Creative Copilot Hub)</h2>
        <button class="close-btn" @click="closeHub">×</button>
      </header>
      
      <div class="copilot-body">
        <!-- 面板1: 雷达预估区 -->
        <section class="panel radar-panel">
          <h3>📊 走势预估对比 (初心 vs 现状)</h3>
          <div class="radar-placeholder">
            <!-- 实际开发中这里会接入 ECharts 雷达图 -->
            <div class="chart-mockup">
              <p>🔴 红色区域：当前小说走势 (战力膨胀度: 85, 情感浓度: 40)</p>
              <p>🔵 蓝色区域：原定大纲初心 (战力膨胀度: 30, 情感浓度: 80)</p>
            </div>
          </div>
          <div class="warnings">
            <div v-for="(warn, index) in trendData.warnings" :key="index" class="warn-item">
              {{ warn }}
            </div>
          </div>
          <div class="prediction-box">
            <strong>🔮 未来走势预估：</strong>
            <p>{{ trendData.future_prediction }}</p>
          </div>
        </section>

        <!-- 面板2: 反思建议区 -->
        <section class="panel suggestion-panel">
          <h3>💡 书库量化经验总和建议</h3>
          <div class="suggestion-list">
            <div v-for="(sug, index) in suggestions" :key="index" class="sug-card">
              <div class="sug-header">
                <span :class="['sug-tag', sug.type]">{{ sug.type === 'plot' ? '剧情' : '设定' }}</span>
                <h4>{{ sug.title }}</h4>
              </div>
              <p>{{ sug.content }}</p>
            </div>
          </div>
        </section>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'

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

const trendData = ref({ warnings: [], future_prediction: '' })
const suggestions = ref([])

const closeHub = () => {
  emit('close')
}

// 模拟调用后端接口
const fetchCopilotData = async () => {
  // 实际接入: await fetch(`/api/creative_copilot/${props.bookId}/trend`)
  trendData.value = {
    warnings: [
      "⚠️ 战力通胀预警：近期章节主角跨越2个大境界秒杀敌人，已偏离大纲设定的'苟道'初心。",
      "⚠️ 情感线边缘化：近10万字女主出场率低于5%，建议增加羁绊剧情。"
    ],
    future_prediction: "基于当前走势，主角将在30章内达到世界战力天花板，可能导致后期剧情缺乏压迫感。建议引入上层界域或开启大势力内斗。"
  }
  
  suggestions.value = [
    {
      type: "plot",
      title: "剧情节奏建议",
      content: "根据事件专员提取的大纲，连续3个段落都在描写打斗，缺乏文戏张弛。建议下一章转入战后搜刮或反派视角的惊恐反应，拉升爽点体验。"
    },
    {
      type: "lore",
      title: "设定一致性检查",
      content: "检测到‘天帝圣剑’在第50章设定为残缺，但在最新量化章节中发挥了完美威力。建议打个补丁说明其修复过程，或解释为临时爆发。"
    }
  ]
}

onMounted(() => {
  fetchCopilotData()
})
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
  display: flex;
  flex: 1;
  padding: 20px;
  gap: 20px;
  overflow-y: auto;
}

.panel {
  flex: 1;
  background: #181825;
  border-radius: 8px;
  padding: 20px;
  border: 1px solid #313244;
}

.panel h3 {
  margin-top: 0;
  border-bottom: 1px solid #313244;
  padding-bottom: 10px;
  color: #89b4fa;
}

.chart-mockup {
  background: #11111b;
  padding: 20px;
  border-radius: 8px;
  margin-bottom: 20px;
  font-family: monospace;
}

.warn-item {
  background: rgba(250, 179, 135, 0.1);
  border-left: 4px solid #fab387;
  padding: 10px;
  margin-bottom: 10px;
  border-radius: 4px;
}

.prediction-box {
  margin-top: 20px;
  padding: 15px;
  background: rgba(203, 166, 247, 0.1);
  border: 1px solid #cba6f7;
  border-radius: 8px;
  color: #cba6f7;
}

.sug-card {
  background: #11111b;
  border: 1px solid #313244;
  padding: 15px;
  border-radius: 8px;
  margin-bottom: 15px;
}

.sug-header {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 10px;
}

.sug-header h4 {
  margin: 0;
  color: #f5e0dc;
}

.sug-tag {
  padding: 3px 8px;
  border-radius: 4px;
  font-size: 0.8rem;
  font-weight: bold;
}

.sug-tag.plot {
  background: #a6e3a1;
  color: #11111b;
}

.sug-tag.lore {
  background: #89b4fa;
  color: #11111b;
}
</style>
