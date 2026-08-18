/**
 * 前端功能开关读取器（阶段A UI 门控）
 * -----------------------------------
 * 一次性从后端 /settings/features 拉取开关并缓存；供默认关闭/降级的功能做 UI 门控，
 * 避免前端展示“死入口”。避免为每次渲染重复请求。
 */
import { ref } from 'vue'
import { api, unwrap } from '../api'

type FeatureFlags = Record<string, boolean>

const flags = ref<FeatureFlags>({})
let loaded = false
let loading: Promise<void> | null = null

async function load(): Promise<void> {
  if (loaded) return
  if (loading) return loading
  loading = (async () => {
    try {
      const res = await api.settings.getFeatures()
      flags.value = unwrap<any>(res) || {}
      loaded = true
    } catch {
      // 后端不可达时保持空开关；调用方按“关闭”处理（fail-closed UI）。
      loaded = true
    } finally {
      loading = null
    }
  })()
  return loading
}

/**
 * 读取功能是否开启。默认关闭（fail-closed），显式拉取失败按关闭处理。
 * @param key 功能键，如 'branch_version_enable'
 * @param defaultOn 后端未返回该键时的兜底值（通常 false）
 */
function enabled(key: string, defaultOn = false): boolean {
  const val = flags.value[key]
  return typeof val === 'boolean' ? val : defaultOn
}

export function useFeatureFlags() {
  if (!loaded && !loading) void load()
  return { flags, enabled, reload: load }
}