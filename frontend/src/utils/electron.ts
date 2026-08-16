/**
 * utils/electron.js — 运行环境检测与 URL 构建
 * ==============================================
 * Electron 检测统一使用 window.process?.versions?.electron
 * （替代原先 window.location.protocol === 'file:' 的脆弱判断）。
 */

/**
 * 是否运行在 Electron 桌面容器中。
 * 优先用 preload 注入的 desktopAPI(批次3,可靠);否则回退 process.versions 检测。
 */
export function isElectron() {
  if (typeof window !== 'undefined' && (window as any).desktopAPI?.isDesktop === true) {
    return true
  }
  return typeof window !== 'undefined' &&
    typeof (window as any).process === 'object' &&
    (window as any).process !== null &&
    typeof (window as any).process.versions === 'object' &&
    Boolean((window as any).process.versions.electron)
}

/**
 * 打开系统文件夹选择器(Electron 环境);返回所选目录绝对路径,取消返回 null。
 * Web/浏览器环境无此能力,返回 null——调用方应退化为手动输入路径。
 */
export async function selectFolder(): Promise<string | null> {
  const api = (window as any).desktopAPI
  if (api && typeof api.selectFolder === 'function') {
    return await api.selectFolder()
  }
  return null
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
