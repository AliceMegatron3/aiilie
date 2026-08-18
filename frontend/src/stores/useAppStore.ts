import { defineStore } from 'pinia'
import router from '../router'

// 全部前端功能域，与后端 api domain、router 路径一一对应
type AppModule =
  // 创作
  | 'projects'
  // 知识
  | 'library' | 'cards' | 'ledger' | 'code' | 'knowledge-gaps' | 'emotion'
  // 智能
  | 'agents' | 'deep-think' | 'sessions' | 'reflections' | 'experience'
  // 系统
  | 'plugins' | 'workspace' | 'settings' | 'templates'

// 功能域 → 对应后端 api domain(文档用途,便于导航与接线核对)
export const MODULE_TO_API_DOMAIN: Record<string, string> = {
  projects: 'projects / docs',
  narrative: 'narrative',
  lockfield: 'lockfield',
  'ensemble-history': 'ensemble',
  library: 'library',
  cards: 'library',
  ledger: 'ledger',
  code: 'code-execution',
  'knowledge-gaps': 'knowledge',
  emotion: 'emotion',
  timeline: 'timeline',
  agents: 'novel-agent',
  'deep-think': 'deepThink',
  sessions: 'sessions',
  reflections: 'reflection',
  plugins: 'plugins / reflection-governance',
  workspace: 'workspace',
  settings: 'settings / models',
  templates: 'prompts',
  experience: 'experience',
}

interface AppState {
  activeModule: AppModule
  selectedTemplateId: string | null
  copilotVisible: boolean
  activeDocId: string | null
  activeDocProjectId: string | null
}

/**
 * 应用级 UI 状态（含路由联动）
 * - 模块切换：setActiveModule 同步 router.push，URL 与界面状态一致
 * - 活动文档：activeDocId 驱动 CenterPanel 打开 DocumentEditor
 */
export const useAppStore = defineStore('app', {
  state: (): AppState => ({
    // 'projects', 'library', 'templates', 'sessions', 'reflections', 'settings'
    activeModule: 'projects',
    // when a template is selected, we store its id here
    selectedTemplateId: null,
    // show Creative Copilot modal
    copilotVisible: false,
    // 当前在编辑器打开的文档（null = 未打开编辑器）
    activeDocId: null,
    activeDocProjectId: null,
  }),
  actions: {
    setActiveModule(moduleName: AppModule) {
      this.activeModule = moduleName
      // 模块切换同步路由（URL 与界面状态对齐）
      const routePath = `/${moduleName}`
      if (router.currentRoute.value.path !== routePath) {
        router.push(routePath).catch(() => { /* 重复导航忽略 */ })
      }
    },
    selectTemplate(templateId: string | null) {
      this.selectedTemplateId = templateId
    },
    toggleCopilot() {
      this.copilotVisible = !this.copilotVisible
    },
    openDocument(docId: string, projectId: string | null = null) {
      this.activeDocId = docId
      if (projectId) this.activeDocProjectId = projectId
      // 文档编辑走嵌套路由 /projects/:projectId/docs/:docId
      const projId = this.activeDocProjectId || 'default'
      router.push(`/projects/${projId}/docs/${docId}`).catch(() => {})
    },
    closeDocument() {
      this.activeDocId = null
      this.activeDocProjectId = null
      // 离开文档编辑路由，避免删除后仍停留在已删除文档页面
      router.push('/projects').catch(() => {})
    },
  },
})
