/**
 * electron 运行环境检测与 URL 构建单测
 * 覆盖：desktopAPI / process.versions.electron 两条检测路径、
 *       Electron 与浏览器两态的 API base & WS base URL。
 */
import { describe, it, expect, beforeEach } from 'vitest'
import { isElectron, getApiBaseURL, getWsBaseURL } from '../utils/electron'

function clearEnv() {
  // 移除桌面/preload 注入，避免污染
  ;(window as any).desktopAPI = undefined
  try {
    delete (window as any).process
  } catch { /* 只读属性忽略 */ }
}

function setLocation(protocol: string, host: string) {
  Object.defineProperty(window, 'location', {
    configurable: true,
    value: { protocol, host },
  })
}

beforeEach(() => {
  clearEnv()
  setLocation('http:', 'localhost:5174')
})

describe('isElectron 检测', () => {
  it('桌面环境（desktopAPI.isDesktop）→ true', () => {
    ;(window as any).desktopAPI = { isDesktop: true }
    expect(isElectron()).toBe(true)
  })

  it('非桌面（无 desktopAPI、无 process.versions.electron）→ false', () => {
    expect(isElectron()).toBe(false)
  })
})

describe('URL 构建随环境切换', () => {
  it('Electron 态直连本地后端', () => {
    ;(window as any).desktopAPI = { isDesktop: true }
    expect(getApiBaseURL()).toBe('http://127.0.0.1:8000/api/v1')
    expect(getWsBaseURL()).toBe('ws://127.0.0.1:8000')
  })

  it('浏览器态：API 同源相对路径，WS 按页面协议', () => {
    expect(getApiBaseURL()).toBe('/api/v1')
    setLocation('https:', 'app.example.com')
    expect(getWsBaseURL()).toBe('wss://app.example.com')
    setLocation('http:', 'app.example.com')
    expect(getWsBaseURL()).toBe('ws://app.example.com')
  })
})