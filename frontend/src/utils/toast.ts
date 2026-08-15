/**
 * utils/toast.js — 统一 Toast 通知服务
 * ====================================
 * 替代散落各处的 alert()/confirm() 弹窗：
 * - toast.success / toast.error / toast.info / toast.warning
 * - toast.confirm 提供 Promise 化的确认对话框（用于高危操作二次确认）
 */
import { reactive } from 'vue'

type ToastType = 'success' | 'error' | 'info' | 'warning'
interface ToastItem { id: number; type: ToastType; message: string; duration: number }
interface ConfirmState { message: string; resolve: (value: boolean) => void }
interface PromptState { title: string; defaultValue: string; resolve: (value: string | null) => void }

let seq = 0
const state = reactive<{
  toasts: ToastItem[]
  confirmState: ConfirmState | null
  promptState: PromptState | null
}>({
  toasts: [],
  confirmState: null,
  promptState: null,
})

function push(type: ToastType, message: string, duration = 3000) {
  const id = ++seq
  state.toasts.push({ id, type, message, duration })
  if (duration > 0) {
    setTimeout(() => remove(id), duration)
  }
  return id
}

function remove(id: number) {
  const idx = state.toasts.findIndex((t) => t.id === id)
  if (idx >= 0) state.toasts.splice(idx, 1)
}

export const toast = {
  success: (msg: string) => push('success', msg),
  error: (msg: string, duration = 4000) => push('error', msg, duration),
  info: (msg: string) => push('info', msg),
  warning: (msg: string) => push('warning', msg, 4500),
  dismiss: remove,
  /**
   * Promise 化确认框：resolve(true) 表示用户确认。
   * 比 window.confirm 更可控（可定制样式、不阻塞渲染）。
   */
  confirm(message: string): Promise<boolean> {
    return new Promise((resolve) => {
      state.confirmState = { message, resolve }
    })
  },
  _resolveConfirm(ok: boolean) {
    if (state.confirmState) {
      state.confirmState.resolve(ok)
      state.confirmState = null
    }
  },
  /**
   * Promise 化文本输入框：替代 window.prompt。
   * resolve(用户输入的字符串)，取消时 resolve(null)。
   */
  prompt(title: string, defaultValue = ''): Promise<string | null> {
    return new Promise((resolve) => {
      state.promptState = { title, defaultValue, resolve }
    })
  },
  _resolvePrompt(value: string | null) {
    if (state.promptState) {
      state.promptState.resolve(value)
      state.promptState = null
    }
  },
}

export function useToastState() {
  return state
}

export default toast
