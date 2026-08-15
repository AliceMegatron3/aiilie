/**
 * utils/electron.js — 运行环境检测与 URL 构建
 * ==============================================
 * Electron 检测统一使用 window.process?.versions?.electron
 * （替代原先 window.location.protocol === 'file:' 的脆弱判断）。
 */

/**
 * 是否运行在 Electron 桌面容器中。
 * 注意：Electron 渲染进程注入 process.versions，浏览器环境无此对象。
 */
export function isElectron() {
  return typeof window !== 'undefined' &&
    typeof window.process === 'object' &&
    window.process !== null &&
    typeof window.process.versions === 'object' &&
    Boolean(window.process.versions.electron)
}

/**
 * API base URL：
 * - Electron：直连本地后端 http://127.0.0.1:8000/api/v1
 * - 浏览器：同源相对路径 /api/v1（生产态 FastAPI 同源挂载 / 开发态 Vite 代理）
 */
export function getApiBaseURL() {
  return isElectron() ? 'http://127.0.0.1:8000/api/v1' : '/api/v1'
}

/**
 * WebSocket base URL（含协议 ws/wss 选择）：
 * - Electron：ws://127.0.0.1:8000
 * - 浏览器：按页面协议选择 ws/wss，host 取当前页面
 */
export function getWsBaseURL() {
  if (isElectron()) {
    return 'ws://127.0.0.1:8000'
  }
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  return `${protocol}//${window.location.host}`
}

export default { isElectron, getApiBaseURL, getWsBaseURL }
