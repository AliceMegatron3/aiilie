/**
 * utils/behaviorTracker.ts — 批次7：用户行为日志前端采集
 * ==========================================================
 * 自动采集三类行为并上报后端（失败静默，绝不阻塞业务）：
 * 1. 操作路径 action_path：路由跳转链路
 * 2. 页面停留 page_stay：路由切换时的停留时长
 * 3. 报错路径 error_path：window.onerror / unhandledrejection
 */
import { api } from '../api'

let lastRoute = ''
let lastRouteAt = 0
let initialized = false

function report(eventType: string, payload: Record<string, unknown> = {}) {
  api.behavior.report(eventType, payload).catch(() => { /* 静默 */ })
}

/** 路由级操作路径记录。 */
export function trackRouteAction(toPath: string, fromPath: string) {
  const now = Date.now()
  if (lastRoute && lastRouteAt) {
    const duration = now - lastRouteAt
    if (duration > 0) {
      report('page_stay', { page: lastRoute, route: lastRoute, duration_ms: duration })
    }
  }
  lastRoute = toPath
  lastRouteAt = now
  report('action_path', { path: toPath, from: fromPath || null })
}

/** 全局错误采集（window.onerror + unhandledrejection）。 */
function captureErrors() {
  window.addEventListener('error', (e) => {
    try {
      report('error_path', {
        route: window.location.hash || window.location.pathname,
        error: e.message || 'unknown error',
        stack: (e.error && e.error.stack) || '',
      })
    } catch (_) { /* 静默 */ }
  })
  window.addEventListener('unhandledrejection', (e) => {
    try {
      const reason = e.reason
      report('error_path', {
        route: window.location.hash || window.location.pathname,
        error: reason?.message || String(reason || 'unhandled rejection'),
        stack: (reason && reason.stack) || '',
      })
    } catch (_) { /* 静默 */ }
  })
}

/** 安装行为采集器（幂等，App 启动时调用一次）。 */
export function installBehaviorTracker(router: { afterEach: (hook: (to: unknown, from: unknown) => void) => void }) {
  if (initialized) return
  initialized = true
  router.afterEach((to: any, from: any) => {
    try {
      trackRouteAction((to && (to.fullPath || to.path)) || '', (from && (from.fullPath || from.path)) || '')
    } catch (_) { /* 静默 */ }
  })
  captureErrors()
}

export default { installBehaviorTracker, trackRouteAction }
