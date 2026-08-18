/**
 * useThemeStore 单元测试 — 主题皮肤系统（批次C）
 * 覆盖：默认主题、setTheme 应用+持久化、init 从 localStorage 恢复/兜底 dark、
 *       registerTheme 扩展 available、apply 写 CSS 变量。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { useThemeStore } from '../stores/useThemeStore'

beforeEach(() => {
  setActivePinia(createPinia())
  localStorage.clear()
  document.documentElement.removeAttribute('data-theme')
  document.documentElement.removeAttribute('style')
})

describe('useThemeStore', () => {
  it('初始主题为 dark，available 含三套内置皮肤', () => {
    const s = useThemeStore()
    expect(s.current).toBe('dark')
    expect(s.available.map((t) => t.name)).toEqual(['dark', 'sepia', 'twilight'])
  })

  it('setTheme 应用 data-theme 并持久化', () => {
    const s = useThemeStore()
    s.setTheme('sepia')
    expect(s.current).toBe('sepia')
    expect(document.documentElement.getAttribute('data-theme')).toBe('sepia')
    expect(localStorage.getItem('no0ai_theme')).toBe('sepia')
  })

  it('init：无存档时兜底 dark', () => {
    const s = useThemeStore()
    s.init()
    expect(s.current).toBe('dark')
    expect(document.documentElement.getAttribute('data-theme')).toBe('dark')
  })

  it('init：有合法存档时恢复', () => {
    localStorage.setItem('no0ai_theme', 'twilight')
    const s = useThemeStore()
    s.init()
    expect(s.current).toBe('twilight')
  })

  it('registerTheme 扩展 available（不重复同名内置）', () => {
    const s = useThemeStore()
    s.registerTheme({ name: 'ocean', label: '海洋', variables: { '--bg': '#0af' } })
    s.registerTheme({ name: 'dark', label: '重复', variables: {} })
    const names = s.available.map((t) => t.name)
    expect(names).toContain('ocean')
    expect(names.filter((n) => n === 'dark')).toHaveLength(1)
  })

  it('apply 为带变量的插件皮肤写入 CSS 变量', () => {
    const s = useThemeStore()
    s.registerTheme({ name: 'ocean', label: '海洋', variables: { '--bg': '#0af' } })
    s.setTheme('ocean')
    expect(document.documentElement.style.getPropertyValue('--bg')).toBe('#0af')
  })
})