import { defineStore } from 'pinia'
import router from '../router'

type AppModule = 'projects' | 'library' | 'templates' | 'sessions' | 'reflections' | 'settings'
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
