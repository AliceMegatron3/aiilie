/**
 * useVisibilityPolling 单元测试 — 页面可见性感知轮询
 * 覆盖：挂载即触发一次、按 interval 轮询、隐藏时暂停不重复请求、
 *       恢复可见立即刷新一次、卸载清理定时器。用假定时器保证确定性。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { defineComponent } from 'vue'
import { mount } from '@vue/test-utils'
import { useVisibilityPolling } from '../composables/useVisibilityPolling'

function mountPolling(fetcher: () => void, intervalMs: number) {
  const Comp = defineComponent({
    setup() {
      useVisibilityPolling(fetcher, intervalMs)
    },
    template: '<div />',
  })
  return mount(Comp)
}

function setHidden(hidden: boolean) {
  Object.defineProperty(document, 'hidden', { configurable: true, value: hidden })
}

function fireVisibility() {
  document.dispatchEvent(new Event('visibilitychange'))
}

let fetcher: ReturnType<typeof vi.fn>

beforeEach(() => {
  vi.useFakeTimers()
  fetcher = vi.fn()
  setHidden(false)
})

afterEach(() => {
  vi.useRealTimers()
  vi.restoreAllMocks()
})

describe('useVisibilityPolling', () => {
  it('挂载即触发一次，并按 interval 持续轮询', async () => {
    const wrapper = mountPolling(fetcher, 5000)
    expect(fetcher).toHaveBeenCalledTimes(1)
    await vi.advanceTimersByTimeAsync(5000)
    expect(fetcher).toHaveBeenCalledTimes(2)
    await vi.advanceTimersByTimeAsync(5000)
    expect(fetcher).toHaveBeenCalledTimes(3)
    wrapper.unmount()
  })

  it('页面隐藏时不触发轮询；恢复可见立即刷新一次', async () => {
    const wrapper = mountPolling(fetcher, 5000)
    expect(fetcher).toHaveBeenCalledTimes(1)

    setHidden(true)
    fireVisibility()
    await vi.advanceTimersByTimeAsync(5000)
    expect(fetcher).toHaveBeenCalledTimes(1) // 隐藏期间不轮询

    setHidden(false)
    fireVisibility() // 恢复可见立即刷新
    expect(fetcher).toHaveBeenCalledTimes(2)
    wrapper.unmount()
  })

  it('卸载时清理 interval，不再轮询', async () => {
    const wrapper = mountPolling(fetcher, 5000)
    expect(fetcher).toHaveBeenCalledTimes(1)
    wrapper.unmount()
    const after = fetcher.mock.calls.length
    await vi.advanceTimersByTimeAsync(10000)
    expect(fetcher.mock.calls.length).toBe(after)
  })
})