/**
 * agentStore 单元测试 — 模型配置与技能覆写
 * 覆盖：skillOverride 设置/重置、模型设置、风格镜头、执行 DAG、
 *       setModels 回填 + worker/primary 自动回退。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

const { ollama, getLLM } = vi.hoisted(() => ({ ollama: vi.fn(), getLLM: vi.fn() }))

vi.mock('../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api')>()
  return {
    ...actual,
    api: {
      ...actual.api,
      models: { ollama },
      settings: { ...actual.api.settings, getLLM },
    },
  }
})

import { useAgentStore } from '../stores/agentStore'

beforeEach(() => {
  setActivePinia(createPinia())
  ollama.mockReset()
  getLLM.mockReset()
})

describe('useAgentStore', () => {
  it('setSkillOverride 与 resetSkillOverrides', () => {
    const s = useAgentStore()
    s.setSkillOverride('combat_expert', false)
    expect(s.skillOverrides.combat_expert).toBe(false)
    s.resetSkillOverrides()
    expect(s.skillOverrides).toEqual({ lore_expert: null, combat_expert: null, emotion_expert: null })
  })

  it('setModels / setStyleLens / setExecutionDag 直接赋值', () => {
    const s = useAgentStore()
    s.setModels('m1', 'm2')
    s.setStyleLens('都市')
    s.setExecutionDag({ nodes: [] })
    expect(s.primaryModel).toBe('m1')
    expect(s.workerModel).toBe('m2')
    expect(s.projectStyleLens).toBe('都市')
    expect(s.lastExecutionDag).toEqual({ nodes: [] })
  })

  it('fetchFromConfig 回填模型并自动选择首个本地模型', async () => {
    ollama.mockResolvedValue({ data: { data: [{ id: 'qwen', name: 'qwen2.5', type: 'local' }] } })
    getLLM.mockResolvedValue({ data: { ollama: { model_name: 'qwen2.5' }, deepseek: { model_name: 'deepseek-chat' } } })
    const s = useAgentStore()
    await s.fetchFromConfig()
    expect(s.availableModels).toEqual([{ id: 'qwen', name: 'qwen2.5', type: 'local' }])
    expect(s.workerModel).toBe('qwen2.5')
    expect(s.primaryModel).toBe('deepseek-chat')
  })

  it('模型缺失时回退到首个本地模型', async () => {
    ollama.mockResolvedValue({ data: { data: [{ id: 'qwen', name: 'qwen2.5', type: 'local' }] } })
    getLLM.mockResolvedValue({ data: {} })
    const s = useAgentStore()
    await s.fetchFromConfig()
    expect(s.workerModel).toBe('qwen2.5')
    expect(s.primaryModel).toBe('qwen2.5')
  })

  it('拉取全部失败时保持现有状态，不抛错', async () => {
    ollama.mockRejectedValue(new Error('down'))
    getLLM.mockRejectedValue(new Error('down'))
    const s = useAgentStore()
    s.setModels('keep', 'keep')
    await expect(s.fetchFromConfig()).resolves.toBeUndefined()
    expect(s.primaryModel).toBe('keep')
  })
})