import { defineStore } from 'pinia'

type SkillRole = 'lore_expert' | 'combat_expert' | 'emotion_expert'
type SkillOverride = boolean | null
interface AgentState {
  primaryModel: string
  workerModel: string
  skillOverrides: Record<SkillRole, SkillOverride>
  projectStyleLens: string
  lastExecutionDag: unknown
}

export const useAgentStore = defineStore('agent', {
  state: (): AgentState => ({
    // 全局模型选择
    primaryModel: 'deepseek-r1:7b',
    workerModel: 'qwen2.5:7b',
    
    // 专职子智能体（专员）的强制开关状态
    // 如果为 null，代表让总督自己决定；如果为 true/false，代表人类强制介入
    skillOverrides: {
      lore_expert: null,
      combat_expert: null,
      emotion_expert: null,
    },
    
    // 当前项目的风格透镜（可被提示词适配器读取）
    projectStyleLens: '玄幻修仙',
    
    // 最近一次总督下发的 DAG 执行计划图
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
    }
  }
})
