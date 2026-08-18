/**
 * toast 单元测试 — 统一 Toast 通知服务 + Promise 化 confirm/prompt
 * 覆盖：四种类型入队、duration 到期自动移除、手动 dismiss、
 *       手动/取消 confirm、prompt 结果传递。
 * 说明：state 为模块级响应式单例，用 vi.resetModules 隔离各用例。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'

let toast: typeof import('../utils/toast').toast
let useToastState: typeof import('../utils/toast').useToastState

beforeEach(async () => {
  vi.resetModules()
  const mod = await import('../utils/toast')
  toast = mod.toast
  useToastState = mod.useToastState
})

afterEach(() => {
  vi.useRealTimers()
})

describe('toast 基础入队/移除', () => {
  it('success/info/warning/prompt type 入队并可读', () => {
    const st = useToastState()
    toast.success('a')
    toast.info('b')
    toast.warning('c')
    toast.error('d')
    expect(st.toasts.map((t) => [t.type, t.message])).toEqual([
      ['success', 'a'],
      ['info', 'b'],
      ['warning', 'c'],
      ['error', 'd'],
    ])
  })

  it('手动 dismiss 移除指定 toast', () => {
    const st = useToastState()
    const id = toast.warning('keep')
    toast.info('drop')
    toast.dismiss(id)
    const messages = st.toasts.map((t) => t.message)
    expect(messages).toEqual(['drop'])
  })

  it('duration>0 时到期自动移除；duration<=0 永不自动移除', () => {
    vi.useFakeTimers()
    const st = useToastState()
    toast.info('temp') // 默认 3000
    toast.error('sticky', 0) // 不自动移除
    expect(st.toasts).toHaveLength(2)
    vi.advanceTimersByTime(3000)
    expect(st.toasts).toHaveLength(1)
    expect(st.toasts[0].message).toBe('sticky')
    vi.advanceTimersByTime(10000)
    expect(st.toasts).toHaveLength(1)
  })
})

describe('toast.confirm / toast.prompt（Promise 化）', () => {
  it('confirm 未决前不 resolve，_resolveConfirm(true/false) 后按值完成', async () => {
    const st = useToastState()
    let resolved: boolean | 'PENDING' = 'PENDING'
    toast.confirm('确认？').then((v) => { resolved = v })
    expect(st.confirmState?.message).toBe('确认？')
    expect(resolved).toBe('PENDING')
    toast._resolveConfirm(true)
    await Promise.resolve()
    expect(resolved).toBe(true)
    expect(st.confirmState).toBeNull()
  })

  it('prompt 返回输入值；取消返回 null', async () => {
    const st = useToastState()
    const p1 = toast.prompt('标题', '默认值')
    expect(st.promptState?.title).toBe('标题')
    toast._resolvePrompt('输入内容')
    await expect(p1).resolves.toBe('输入内容')
    expect(st.promptState).toBeNull()

    const p2 = toast.prompt('标题')
    toast._resolvePrompt(null)
    await expect(p2).resolves.toBeNull()
  })
})