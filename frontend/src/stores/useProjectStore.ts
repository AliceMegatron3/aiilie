import { defineStore } from 'pinia'
import { api } from '../api'

interface ProjectInput {
  id?: string
  project_id?: string
  name?: string
  project_name?: string
  genre?: string
  style_tags?: string[]
  bind_book_ids?: string[]
  default_compute_mode?: 'rapid' | 'think'
  created_at?: string
  updated_at?: string
}

interface ProjectState {
  projects: Array<AuthorProject & { project_id?: string }>
  currentProjectId: string | null
  documents: unknown[]
  isLoading: boolean
  error: string | null
  loadedAt: number
}

interface AuthorProject {
  id: string
  name: string
  default_compute_mode: 'rapid' | 'think'
  bind_book_ids: string[]
}

export const useProjectStore = defineStore('project', {
  state: (): ProjectState => ({
    projects: [],
    currentProjectId: null,
    documents: [],
    isLoading: false,
    error: null,
    // 最近一次成功拉取时间戳（毫秒），用于短窗口去重
    loadedAt: 0,
  }),

  getters: {
    currentProject: (state) => {
      // 兼容后端字段 project_id / 前端字段 id
      return state.projects.find(p => (p.project_id || p.id) === state.currentProjectId) || null
    },
  },

  actions: {
    selectProject(id: string) {
      this.currentProjectId = id
    },

    async fetchProjects(force = false) {
      // 去重：非强制且 30s 内已加载过，直接跳过（避免 CenterPanel 与 ProjectsView 双重请求）
      if (!force && this.loadedAt && Date.now() - this.loadedAt < 30000) {
        return
      }
      this.isLoading = true
      this.error = null
      try {
        const res = await api.projects.list()
        // 后端返回 list[AuthorProject]，字段为 project_id/project_name
        this.projects = res.data || []
        this.loadedAt = Date.now()
        // 自动选中第一个项目（如果当前未选中）
        if (!this.currentProjectId && this.projects.length > 0) {
          this.currentProjectId = this.projects[0].project_id || this.projects[0].id
        }
      } catch (e) {
        const error = e as { response?: { data?: { detail?: string } } }
        this.error = error.response?.data?.detail || '获取项目列表失败'
        console.error('[ProjectStore] fetchProjects error:', this.error)
        this.projects = []
      } finally {
        this.isLoading = false
      }
    },

    async createProject(projectData: ProjectInput) {
      try {
        // 将前端字段映射为后端 AuthorProject 字段
        const payload = {
          project_id: projectData.project_id || projectData.id,
          project_name: projectData.project_name || projectData.name,
          genre: projectData.genre || 'unknown',
          style_tags: projectData.style_tags || [],
          bind_book_ids: projectData.bind_book_ids || [],
          default_compute_mode: projectData.default_compute_mode || 'rapid',
          created_at: projectData.created_at,
          updated_at: projectData.updated_at,
        }
        const res = await api.projects.create(payload)
        this.projects.push(res.data)
        return res.data
      } catch (e) {
        const error = e as { response?: { data?: { detail?: string } } }
        this.error = error.response?.data?.detail || '创建项目失败'
        throw e
      }
    },

    async switchMode(project_id: string, mode: 'rapid' | 'think') {
      try {
        await api.projects.switchMode(project_id, mode)
        // 同步本地状态
        const proj = this.projects.find(p => (p.project_id || p.id) === project_id)
        if (proj) {
          proj.default_compute_mode = mode
        }
      } catch (e) {
        console.error('[ProjectStore] switchMode error:', e)
        throw e
      }
    },
  },
})
