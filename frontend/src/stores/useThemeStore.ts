import { defineStore } from 'pinia'

// 主题插件接口(批次C):CSS 变量皮肤系统。
// 运行时 registerTheme 注册新皮肤;setTheme 切换 <html data-theme> + 持久化。
export interface ThemeSpec {
  name: string
  label: string
  variables: Record<string, string>
}

const STORAGE_KEY = 'no0ai_theme'

// 内置三套皮肤(变量定义见 index.css)
const BUILTIN_THEMES: Record<string, ThemeSpec> = {
  dark: { name: 'dark', label: '深色(默认)', variables: {} },
  sepia: { name: 'sepia', label: '木纹/羊皮纸', variables: {} },
  twilight: { name: 'twilight', label: '冷蓝灰/暮色', variables: {} },
}

export const useThemeStore = defineStore('theme', {
  state: () => ({
    current: 'dark',
    // 前端插件注册的额外皮肤(css变量已在 index.css 定义则无需 variables)
    registered: {} as Record<string, ThemeSpec>,
  }),
  getters: {
    available(state): ThemeSpec[] {
      const list = Object.values(BUILTIN_THEMES)
      Object.values(state.registered).forEach((t) => {
        if (!list.find((x) => x.name === t.name)) list.push(t)
      })
      return list
    },
  },
  actions: {
    /** 启动时应用持久化主题 */
    init() {
      try {
        const saved = localStorage.getItem(STORAGE_KEY)
        if (saved && (BUILTIN_THEMES[saved] || this.registered[saved])) {
          this.apply(saved)
        } else {
          this.apply('dark')
        }
      } catch {
        this.apply('dark')
      }
    },
    /** 切换主题;写入 <html data-theme> + 持久化 */
    setTheme(name: string) {
      this.apply(name)
      try {
        localStorage.setItem(STORAGE_KEY, name)
      } catch { /* 忽略持久化失败 */ }
    },
    /** 运行时注册新皮肤(前端插件预留接口) */
    registerTheme(spec: ThemeSpec) {
      this.registered[spec.name] = spec
      // 若插件带 CSS 变量,写入对应 data-theme 下
      if (spec.variables && Object.keys(spec.variables).length) {
        // 由index.css管理内置;插件变量可在选择时应用(见 apply)
      }
    },
    apply(name: string) {
      this.current = name
      if (typeof document !== 'undefined') {
        document.documentElement.setAttribute('data-theme', name)
        // 应用插件变量(若有)
        const spec = this.registered[name]
        if (spec && spec.variables) {
          const root = document.documentElement
          Object.entries(spec.variables).forEach(([k, v]) => root.style.setProperty(k, v))
        }
      }
    },
  },
})