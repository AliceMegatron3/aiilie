/**
 * useFeatureFlags 单元测试 — 功能开关 fail-closed 门控（阶段A UI 门控）
 * 覆盖：默认关闭兜底、显式真/假、加载一次缓存、加载失败按关闭处理、reload 刷新。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'

const getFeatures = vi.fn()

vi.mock('../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api')>()
  return {
    ...actual,
    api: { ...actual.api, settings: { getFeatures } },
  }
})

type UseFeatureFlags = () => {
  flags: { value: Record<string, boolean> }
  enabled: (key: string, defaultOn?: boolean) => boolean
  reload: () => Promise<void>
}
let useFeatureFlags: UseFeatureFlags

beforeEach(async () => {
  getFeatures.mockReset()
  vi.resetModules()
  const mod = await import('../composables/useFeatureFlags')
  useFeatureFlags = mod.useFeatureFlags
})

describe('useFeatureFlags', () => {
  it('后端未返回键时按关闭处理（fail-closed），显式 defaultOn 生效', async () => {
    getFeatures.mockResolvedValue({ data: { known_toggle: true } })
    const ff = useFeatureFlags()
    await new Promise((r) => setTimeout(r, 0))
    expect(ff.enabled('known_toggle')).toBe(true)
    expect(ff.enabled('missing_key')).toBe(false) // fail-closed
    expect(ff.enabled('missing_key', true)).toBe(true) // 显式兜底
  })

  it('显式 false 不被 defaultOn 覆盖', async () => {
    getFeatures.mockResolvedValue({ data: { gate: false } })
    const ff = useFeatureFlags()
    await new Promise((r) => setTimeout(r, 0))
    expect(ff.enabled('gate', true)).toBe(false)
  })

  it('同一开关只拉取一次（load-once 去重）', async () => {
    getFeatures.mockResolvedValue({ data: {} })
    const ff = useFeatureFlags()
    await new Promise((r) => setTimeout(r, 0))
    expect(ff.enabled('x')).toBe(false)
    // 再次调用 useFeatureFlags（同一单例）不应触发第二次拉取
    const ff2 = useFeatureFlags()
    await new Promise((r) => setTimeout(r, 0))
    expect(ff2.enabled('y')).toBe(false)
    expect(getFeatures).toHaveBeenCalledTimes(1)
  })

  it('拉取失败时保持空开关，按 fail-closed 返回 false', async () => {
    getFeatures.mockRejectedValue(new Error('backend down'))
    const ff = useFeatureFlags()
    await new Promise((r) => setTimeout(r, 0))
    expect(ff.enabled('anything')).toBe(false)
    expect(ff.flags.value).toEqual({})
  })

  it('reload 可重新拉取并刷新为最新值', async () => {
    getFeatures.mockResolvedValue({ data: { v: true } })
    const ff = useFeatureFlags()
    await new Promise((r) => setTimeout(r, 0))
    expect(ff.enabled('v')).toBe(true)

    getFeatures.mockResolvedValue({ data: { v: false } })
    await ff.reload()
    await new Promise((r) => setTimeout(r, 0))
    expect(ff.enabled('v')).toBe(false)
    expect(getFeatures).toHaveBeenCalledTimes(2)
  })
})