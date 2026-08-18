/**
 * behaviorTracker 单元测试 — 行为采集（批次7）
 * 覆盖：路由切换联动 page_stay（停留时长）+ action_path 上报、跨路由时长归因正确。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'

const report = vi.fn()

vi.mock('../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api')>()
  return {
    ...actual,
    api: { ...actual.api, behavior: { report } },
  }
})

type TrackRouteAction = (toPath: string, fromPath: string) => void
let trackRouteAction: TrackRouteAction

beforeEach(async () => {
  vi.useFakeTimers()
  report.mockReset()
  report.mockResolvedValue(undefined)
  vi.resetModules()
  const mod = await import('../utils/behaviorTracker')
  trackRouteAction = mod.trackRouteAction
})

afterEach(() => {
  vi.useRealTimers()
})

describe('behaviorTracker.trackRouteAction', () => {
  it('首次进入只上报 action_path，不产生 page_stay（无前置路由）', () => {
    vi.setSystemTime(1000)
    trackRouteAction('/b', '/a')
    expect(report).toHaveBeenCalledTimes(1)
    expect(report).toHaveBeenCalledWith('action_path', { path: '/b', from: '/a' })
  })

  it('跨路由上报上一页 page_stay（含正确停留时长）后，再记录当前 action_path', () => {
    vi.setSystemTime(1000)
    trackRouteAction('/a', '/start')
    report.mockClear()

    vi.setSystemTime(1500) // 停留 500ms
    trackRouteAction('/b', '/a')
    expect(report).toHaveBeenCalledTimes(2)
    expect(report.mock.calls[0]).toEqual(['page_stay', { page: '/a', route: '/a', duration_ms: 500 }])
    expect(report.mock.calls[1]).toEqual(['action_path', { path: '/b', from: '/a' }])
  })

  it('同毫秒内跳转（duration=0）不产生 page_stay', () => {
    vi.setSystemTime(2000)
    trackRouteAction('/a', '/z')
    report.mockClear()
    // 时间未前进，duration=0 → 应跳过 page_stay，只上报 action_path
    trackRouteAction('/c', '/a')
    expect(report).toHaveBeenCalledTimes(1)
    expect(report).toHaveBeenCalledWith('action_path', { path: '/c', from: '/a' })
  })
})