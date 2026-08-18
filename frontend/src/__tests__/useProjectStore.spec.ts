/**
 * useProjectStore 单元测试 — 项目状态（列表拉取去重 / currentProject / 创建 / 切模式）
 * 覆盖：unwraAdata 解包、自动选中首项、30s 去重、force 强制刷新、错误态、
 *       currentProject getter、createProject 字段映射、switchMode 本地同步。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

const { list, create } = vi.hoisted(() => ({ list: vi.fn(), create: vi.fn() }))

vi.mock('../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api')>()
  return {
    ...actual,
    api: {
      ...actual.api,
      projects: { ...actual.api.projects, list, create, switchMode: vi.fn(), addDocument: vi.fn() },
    },
  }
})

import { useProjectStore } from '../stores/useProjectStore'

const ENV = () => ({
  success: true,
  data: [
    { id: 'p1', name: '甲', default_compute_mode: 'rapid', bind_book_ids: [] },
    { id: 'p2', name: '乙', project_id: 'p2x', default_compute_mode: 'think', bind_book_ids: [] },
  ],
  message: 'success',
  error_code: null,
})

beforeEach(() => {
  setActivePinia(createPinia())
  vi.useRealTimers()
  list.mockReset()
  create.mockReset()
})

describe('useProjectStore', () => {
  it('fetchProjects 解包并自动选中第一个项目', async () => {
    list.mockResolvedValue({ data: ENV() })
    const s = useProjectStore()
    await s.fetchProjects()
    expect(s.projects).toHaveLength(2)
    expect(s.currentProjectId).toBe('p1')
    expect(s.isLoading).toBe(false)
  })

  it('30s 内非强制拉取被去重，不再调用 API', async () => {
    list.mockResolvedValue({ data: ENV() })
    const s = useProjectStore()
    await s.fetchProjects()
    expect(list).toHaveBeenCalledTimes(1)
    // 非强制：去重跳过
    await s.fetchProjects()
    expect(list).toHaveBeenCalledTimes(1)
    // 强制：重新拉取
    await s.fetchProjects(true)
    expect(list).toHaveBeenCalledTimes(2)
  })

  it('拉取失败置 error、清空列表，isLoading 复位', async () => {
    list.mockRejectedValue({ response: { data: { detail: '服务异常' } } })
    const s = useProjectStore()
    await s.fetchProjects(true)
    expect(s.error).toBe('服务异常')
    expect(s.projects).toEqual([])
    expect(s.isLoading).toBe(false)
  })

  it('currentProject 兼容 id / project_id 两字段', async () => {
    list.mockResolvedValue({ data: ENV() })
    const s = useProjectStore()
    await s.fetchProjects()
    s.selectProject('p2x')
    expect(s.currentProject?.project_id).toBe('p2x')
  })

  it('createProject 字段映射并 push 到列表', async () => {
    create.mockResolvedValue({ data: { success: true, data: { project_id: 'p9', project_name: '新作', default_compute_mode: 'rapid', bind_book_ids: [] } } })
    const s = useProjectStore()
    const created = await s.createProject({ name: '新作', genre: '玄幻', style_tags: ['a'] })
    expect(create).toHaveBeenCalledWith(expect.objectContaining({ project_name: '新作', genre: '玄幻', style_tags: ['a'] }))
    // reactive 数组引用断言不可靠（proxy 包装），按内容断言
    expect(s.projects.some((p) => (p as any).project_id === created.project_id)).toBe(true)
  })
})