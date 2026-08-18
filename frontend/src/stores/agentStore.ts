import { defineStore } from 'pinia'
import { api, unwrap } from '../api'

type SkillRole = 'lore_expert' | 'combat_expert' | 'emotion_expert'
type SkillOverride = boolean | null
interface AgentState {
  primaryModel: string
  workerModel: string
  availableModels: { id: string; name: string; type: string }[]
  skillOverrides: Record<SkillRole, SkillOverride>
  projectStyleLens: string
  lastExecutionDag: unknown
}

export const useAgentStore = defineStore('agent', {
  state: (): AgentState => ({
    primaryModel: '',
    workerModel: '',
    availableModels: [],
    skillOverrides: {
      lore_expert: null,
      combat_expert: null,
      emotion_expert: null,
    },
    projectStyleLens: '玄幻修仙',
    lastExecutionDag: null
  }),

  actions: {
    setSkillOverride(role: SkillRole, value: SkillOverride) {
      this.skillOverrides[role] = value
    },
    resetSkillOverrides() {
      this.skillOverrides = {
        lore_expert: null,
        combat_expert: null,
        emotion_expert: null,
      }
    },
    setModels(primary: string, worker: string) {
      this.primaryModel = primary
      this.workerModel = worker
    },
    setStyleLens(style: string) {
      this.projectStyleLens = style
    },
    setExecutionDag(dag: unknown) {
      this.lastExecutionDag = dag
    },
    async fetchFromConfig() {
      try {
        // 并行拉取本地模型列表与后端配置
        const [ollamaRes, settingsRes] = await Promise.allSettled([
          api.models.ollama(),
          api.settings.getLLM()
        ])
        // 合并本地已安装模型
        const ollamaModels: { id: string; name: string; type: string }[] =
          ollamaRes.status === 'fulfilled' ? (ollamaRes.value.data?.data || []) : []
        this.availableModels = ollamaModels
        // 从后端配置回填当前选中的模型
        if (settingsRes.status === 'fulfilled') {
          const s = unwrap<any>(settingsRes.value)
          if (s?.ollama?.model_name) this.workerModel = s.ollama.model_name
          if (s?.deepseek?.model_name) this.primaryModel = s.deepseek.model_name
        }
        // 如果 workerModel 为空且有可用本地模型，自动选择第一个
        if (!this.workerModel && ollamaModels.length > 0) {
          this.workerModel = ollamaModels[0].name
        }
        if (!this.primaryModel && ollamaModels.length > 0) {
          this.primaryModel = ollamaModels[0].name
        }
      } catch {
        // 静默失败，保持现有状态
      }
    }
  }
})
