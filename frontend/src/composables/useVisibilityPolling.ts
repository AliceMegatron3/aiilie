/**
 * composables/useVisibilityPolling.js — 页面可见性感知轮询
 * ==========================================================
 * 优化点：状态轮询改为「页面可见时才触发」，标签页隐藏时自动暂停，
 * 恢复可见时立即刷新一次。替代无脑 setInterval，减少后台空耗。
 */
import { onMounted, onUnmounted, ref } from 'vue'

export function useVisibilityPolling(fetcher: () => void | Promise<void>, intervalMs = 5000) {
  const isVisible = ref(typeof document === 'undefined' ? true : !document.hidden)
  let timer: ReturnType<typeof setInterval> | null = null
  let running = false

  const stop = () => {
    if (timer) {
      clearInterval(timer)
      timer = null
    }
    running = false
  }

  const start = () => {
    if (running) return
    running = true
    fetcher()
    timer = setInterval(() => {
      if (isVisible.value) fetcher()
    }, intervalMs)
  }

  const onVisibilityChange = () => {
    isVisible.value = !document.hidden
    if (isVisible.value) {
      fetcher() // 恢复可见立即刷新
    }
  }

  onMounted(() => {
    document.addEventListener('visibilitychange', onVisibilityChange)
    start()
  })

  onUnmounted(() => {
    document.removeEventListener('visibilitychange', onVisibilityChange)
    stop()
  })

  return { start, stop, isVisible }
}
