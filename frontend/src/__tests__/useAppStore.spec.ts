/**
 * useAppStore 单元测试 — 应用级 UI 状态与路由联动
 * 覆盖：setActiveModule 路由联动、selectTemplate、toggleCopilot、
 *       openDocument/closeDocument 的活动文档状态。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

const push = vi.hoisted(() => vi.fn().mockResolvedValue(undefined))
const route = vi.hoisted(() => ({ path: '/' }))
vi.mock('../router', () => ({
  default: {
    currentRoute: { value: route },
    push,
  },
}))

import { useAppStore } from '../stores/useAppStore'

beforeEach(() => {
  setActivePinia(createPinia())
  push.mockClear()
})

describe('useAppStore', () => {
  it('setActiveModule 更新状态并同步路由；路径相同不重复 push', () => {
    const s = useAppStore()
    s.setActiveModule('library')
    expect(s.activeModule).toBe('library')
    expect(push).toHaveBeenCalledWith('/library')
    // 模拟路由已切换到 /library：再次设置同一模块不应重复 push
    route.path = '/library'
    push.mockClear()
    s.setActiveModule('library')
    expect(push).not.toHaveBeenCalled()
  })

  it('selectTemplate / toggleCopilot', () => {
    const s = useAppStore()
    s.selectTemplate('t1')
    s.toggleCopilot()
    expect(s.selectedTemplateId).toBe('t1')
    expect(s.copilotVisible).toBe(true)
    s.toggleCopilot()
    expect(s.copilotVisible).toBe(false)
  })

  it('openDocument 记录活动文档并导航到嵌套路由', () => {
    const s = useAppStore()
    s.openDocument('doc1', 'proj1')
    expect(s.activeDocId).toBe('doc1')
    expect(s.activeDocProjectId).toBe('proj1')
    expect(push).toHaveBeenCalledWith('/projects/proj1/docs/doc1')
  })

  it('closeDocument 清空活动文档并回项目列表', () => {
    const s = useAppStore()
    s.openDocument('doc1', 'proj1')
    s.closeDocument()
    expect(s.activeDocId).toBeNull()
    expect(s.activeDocProjectId).toBeNull()
    expect(push).toHaveBeenCalledWith('/projects')
  })
})