import axios from 'axios'
import axiosRetry from 'axios-retry'
import { getApiBaseURL } from '../utils/electron'

type JsonObject = Record<string, unknown>
type NullableString = string | null

/**
 * No.0 AI V4.0 统一 API 客户端
 * ================================
 * 覆盖批次1-5全部后端 API 端点。
 * - 开发态：Vite 代理 /api → http://127.0.0.1:8000
 * - 生产态：FastAPI 同源挂载，baseURL 直接生效
 * - Electron 检测：统一走 window.process?.versions?.electron（utils/electron.js）
 */
const baseURL = getApiBaseURL()

const apiClient = axios.create({
  baseURL: baseURL,
  headers: {
    'Content-Type': 'application/json'
  },
  timeout: 30000
})

// 健康探针与请求排队 (5.1)
let isBackendHealthy = true
// 后端健康探针挂在根路径 /health，而非 /api/v1 前缀下；据此推导探针 URL。
const healthURL = baseURL.replace(/\/api\/v1\/?$/, '') + '/health'
let healthTimer: ReturnType<typeof setInterval> | null = null
let healthProbe: Promise<void> | null = null

export const checkHealth = async (): Promise<void> => {
  if (healthProbe) return healthProbe

  healthProbe = axios.get(healthURL, { timeout: 1000 }).then(
    () => {
      isBackendHealthy = true
    },
    () => {
      isBackendHealthy = false
    },
  ).finally(() => {
    healthProbe = null
  })

  return healthProbe
}

/** 启动全局单例健康探针；重复启动不会创建额外 timer。 */
export const startHealthPolling = (): void => {
  if (healthTimer) return
  void checkHealth()
  healthTimer = setInterval(() => {
    void checkHealth()
  }, 3000)
}

/** 停止健康探针并释放 timer；应用卸载或测试环境清理时调用。 */
export const stopHealthPolling = (): void => {
  if (healthTimer) {
    clearInterval(healthTimer)
    healthTimer = null
  }
}

apiClient.interceptors.request.use(async (config) => {
  if (config.url === '/health') return config
  
  if (!isBackendHealthy) {
    let waited = 0;
    while (!isBackendHealthy && waited < 5000) {
      await new Promise(r => setTimeout(r, 500));
      waited += 500;
    }
    if (!isBackendHealthy) {
      return Promise.reject(new Error("服务暂不可用"));
    }
  }
  return config;
});

// 增加 3 次指数退避重试，带 jitter
axiosRetry(apiClient, {
  retries: 3,
  retryDelay: (retryCount) => {
    return retryCount * 1000 + Math.random() * 200; // 1s, 2s, 3s + 0~200ms jitter
  },
  retryCondition: (error) => {
    return (
      error.code === 'ECONNREFUSED' || 
      error.code === 'ETIMEDOUT' || 
      Boolean(error.response && [502, 503, 504].includes(error.response.status))
    );
  }
})

// 统一响应拦截：提取 data 层，规范化错误
apiClient.interceptors.response.use(
  (response: any) => response,
  (error: any) => {
    let msg = error.response?.data?.detail || error.response?.data?.message || error.message
    
    // 差异化报错，禁用笼统的 network error
    if (!error.response || error.response.status >= 500) {
      if (error.code === 'ECONNABORTED' || error.message.includes('timeout') || error.code === 'ETIMEDOUT') {
        msg = '推理超时';
      } else {
        msg = '服务离线';
      }
    } else {
      msg = `业务异常: ${msg}`;
    }

    // 更新 error 对象的 message 以便上层捕获时能显示友好提示
    error.message = msg;
    console.error('[API Error]', error.config?.method?.toUpperCase(), error.config?.url, msg)
    return Promise.reject(error)
  }
)

// --- Types & Interfaces ---
export interface TaskSegment {
  id: string;
  status: string;
  progress: number;
}
export interface TaskDetail {
  task_id: string;
  status: string;
  segments: TaskSegment[];
}
export interface AuthorProject {
  id: string;
  name: string;
  default_compute_mode: 'rapid' | 'think';
  bind_book_ids: string[];
}
export interface ProjectDoc {
  id: string;
  project_id: string;
  title: string;
  ai_parse_path?: string;
}
export interface APIResponse<T> {
  data: T;
  message?: string;
  error_code?: number;
}

export const api = {
  // ==========================================
  // 工作区(批次3:只读浏览,写权限限工作区内)
  // ==========================================
  workspace: {
    /** 工作区根与AI索引目录信息 */
    info: () => apiClient.get('/workspace/info'),
    /** 浏览工作区目录(空 path=根);返回子目录与文件清单 */
    browse: (path: string = '') => apiClient.post('/workspace/browse', { path }),
    /** 导入工作区内文件夹为子项目 */
    import: (folder_path: string) => apiClient.post('/workspace/import', { folder_path }),
  },

  // ==========================================
  // 治理面板(P1/P2/P6/P7):插件治理·弧线套用·群像回摆
  // ==========================================
  governance: {
    /** 行为插件清单(规格+聚合统计) */
    plugins: () => apiClient.get('/plugins/behavior'),
    /** 插件统计(含作者保留率真信号) */
    pluginStats: () => apiClient.get('/plugins/behavior/stats'),
    /** 治理流转(ACTIVE/GRAY/RETIRED/CANDIDATE) */
    setPluginStatus: (plugin_id: string, status: string, gray_percent = 0) =>
      apiClient.post(`/plugins/behavior/${plugin_id}/status`, { status, gray_percent }),
    /** 作者信号登记(确认/改稿后回流,生成终稿vs作者定稿) */
    authorSignal: (task_id: string, generated_text: string, final_text: string) =>
      apiClient.post('/plugins/behavior/author-signal', { task_id, generated_text, final_text }),
    /** 弧线模式清单 */
    arcs: () => apiClient.get('/narrative/arcs'),
    /** 套用弧线到卷(派生整卷章级预算) */
    applyArc: (project_id: string, volume_id: string, payload: { pattern_id: string; n_chapters: number; start_number?: number }) =>
      apiClient.post(`/narrative/projects/${project_id}/volumes/${volume_id}/apply-arc`, payload),
    /** 弧线套用前预览派生的章级预算序列 */
    previewArc: (pattern_id: string, n_chapters: number) =>
      apiClient.post(`/narrative/arcs/${pattern_id}/preview`, null, { params: { n_chapters } }),
    /** 弧线配方 vs 实际偏差报告(只报告,不改预算) */
    arcVariance: (project_id: string, volume_id?: string) =>
      apiClient.get(`/narrative/projects/${project_id}/arc-variance`, {
        params: volume_id ? { volume_id } : {},
      }),
    /** 群像生活轨道清单 */
    ensembleTracks: (project_id: string) =>
      apiClient.get(`/ensemble/projects/${project_id}/tracks`),
    /** 录入/更新角色生活轨道(含别名,供在场识别) */
    upsertTrack: (project_id: string, payload: JsonObject) =>
      apiClient.post(`/ensemble/projects/${project_id}/tracks`, payload),
    /** 设定关系基线(情感账户余额) */
    setRelationshipBaseline: (project_id: string, payload: JsonObject) =>
      apiClient.post(`/ensemble/projects/${project_id}/relationships/baseline`, payload),
    /** 登记事件挂账(清算时按深浅回摆) */
    recordEventDelta: (project_id: string, payload: JsonObject) =>
      apiClient.post(`/ensemble/projects/${project_id}/relationships/event-delta`, payload),
    /** 录入角色声纹 */
    upsertVoice: (project_id: string, payload: JsonObject) =>
      apiClient.post(`/ensemble/projects/${project_id}/voices`, payload),
    /** 群像关系账本 */
    ensembleRelations: (project_id: string) =>
      apiClient.get(`/ensemble/projects/${project_id}/relationships`),
    /** 事件清算(按余额深浅回摆) */
    ensembleCloseEvent: (project_id: string, event_id: string) =>
      apiClient.post(`/ensemble/projects/${project_id}/events/${event_id}/close`),
  },

  // ==========================================
  // 批次1：任务引擎 (Task Engine)
  // ==========================================
  tasks: {
    /** 获取任务详情（含分段进度） */
    detail: (task_id: string) => apiClient.get(`/tasks/${task_id}`),
    /** 获取任务分段列表 */
    segments: (task_id: string) => apiClient.get(`/tasks/${task_id}/segments`),
    /** 取消任务 */
    cancel: (task_id: string) => apiClient.post(`/tasks/${task_id}/cancel`),
    /** 获取任务审计报告 */
    auditReport: (task_id: string) => apiClient.get(`/tasks/${task_id}/audit-report`),
    /** 兼容旧接口：任务状态查询 */
    status: (task_id: string) => apiClient.get(`/tasks/${task_id}/status`),
  },

  // ==========================================
  // 批次2：知识库 (Library)
  // ==========================================
  library: {
    /** 导入新书（文本内容） */
    importBook: (title: string, content: string) => apiClient.post('/library/books/import', { title, content }),
    /** 上传书籍文件（Multipart） */
    uploadBookFile: (file: File) => {
      const formData = new FormData()
      formData.append('file', file)
      return apiClient.post('/library/books/upload_file', formData, {
        headers: { 'Content-Type': 'multipart/form-data' },
        timeout: 120000
      })
    },
    /** 获取书库列表 */
    listBooks: () => apiClient.get('/library/books'),
    /** 启动书籍量化任务 */
    quantize: (book_id: string, mode: string = 'both') => apiClient.post(`/library/books/${book_id}/quantize`, { mode }),
    /** 检索知识卡片 */
    searchCards: (params: any = {}) => apiClient.get('/library/cards/search', { params }),
    /** 获取卡片详情 */
    cardDetail: (card_id: string) => apiClient.get(`/library/cards/${card_id}/detail`),
    /** 获取书籍 Info Cards (带四象限过滤) */
      bookInfoCards: (book_id: string, minUtility: number, minEntropy: number) =>
      apiClient.get(`/library/books/${book_id}/info-cards`, { params: { min_utility: minUtility, min_entropy: minEntropy } }),
    /** 获取书籍 Data Cards (带四象限过滤) */
      bookDataCards: (book_id: string, minUtility: number, minEntropy: number) =>
      apiClient.get(`/library/books/${book_id}/data-cards`, { params: { min_utility: minUtility, min_entropy: minEntropy } }),
  },

  // ==========================================
  // 批次3：项目管理与创作学习 (Projects)
  // ==========================================
  projects: {
    /** 获取所有项目列表 */
    list: () => apiClient.get<AuthorProject[]>('/projects'),
    /** 创建新项目 */
    create: (project: JsonObject) => apiClient.post<AuthorProject>('/projects', project),
    /** 切换项目算力模式 (rapid/think) */
    switchMode: (project_id: string, mode: string) => apiClient.post(`/projects/${project_id}/mode`, { mode }),
    /** 绑定书库到项目 */
    bindBook: (project_id: string, book_id: string) => apiClient.post(`/projects/${project_id}/bind-book`, { book_id }),
    /** 解绑书库 */
    unbindBook: (project_id: string, book_id: string) => apiClient.post(`/projects/${project_id}/unbind-book`, { book_id }),
    /** 录入新文档 */
    addDocument: (project_id: string, doc: Omit<ProjectDoc, 'id'>) => apiClient.post<ProjectDoc>(`/projects/${project_id}/docs`, doc),
    /** 获取项目文档列表 */
    listDocs: (project_id: string) => apiClient.get<ProjectDoc[]>(`/projects/${project_id}/docs`),
    /** 获取单个文档内容 */
    getDoc: (doc_id: string) => apiClient.get<ProjectDoc>(`/docs/${doc_id}`),
    /** 保存单个文档内容 */
    saveDoc: (doc_id: string, raw_content: string) => apiClient.put(`/docs/${doc_id}`, { raw_content }),
    /** 登记某任务生成内容为待确认基线；不会产生作者信号 */
    setGenerationBaseline: (doc_id: string, task_id: string, generated_text: string) =>
      apiClient.post(`/docs/${doc_id}/generation-baseline`, { task_id, generated_text }),
    /** 获取文档待确认生成基线 */
    generationBaseline: (doc_id: string) => apiClient.get(`/docs/${doc_id}/generation-baseline`),
    /** 作者明确确认定稿，才生成保留率信号并消费基线 */
    authorConfirmDoc: (doc_id: string, final_text: string) =>
      apiClient.post(`/docs/${doc_id}/author-confirm`, { final_text }),
    /** 重命名文档（功能补全） */
    renameDoc: (doc_id: string, doc_name: string) => apiClient.put(`/docs/${doc_id}/rename`, { doc_name }),
    /** 删除文档（含版本历史）（功能补全） */
    deleteDoc: (doc_id: string) => apiClient.delete(`/docs/${doc_id}`),
    /** 获取文档版本历史（功能补全） */
    docVersions: (doc_id: string) => apiClient.get(`/docs/${doc_id}/versions`),
    /** 回滚文档到指定版本（功能补全） */
    restoreDocVersion: (doc_id: string, version: number) => apiClient.post(`/docs/${doc_id}/versions/${version}/restore`),
    /** 触发文档 AI 学习 */
    learnDocument: (doc_id: string) => apiClient.post(`/docs/${doc_id}/learn`),
    /** 读取文档自学习报告 */
    getParseResult: (doc_id: string) => apiClient.get(`/docs/${doc_id}/parse-result`),
    /** 触发项目云端同步 */
    sync: (project_id: string) => apiClient.post(`/projects/${project_id}/sync`),
    /** 获取同步状态 */
    syncStatus: (project_id: string) => apiClient.get(`/projects/${project_id}/sync-status`),
  },

  // ==========================================
  // 批次4：反思中枢 (Reflection)
  // ==========================================
  reflection: {
    /** 手动触发反思采集 */
    trigger: (priority = 50, scope_filter = 'ALL') => apiClient.post('/reflection/task/trigger', { priority, scope_filter }),
    /** 系统事件上报触发自动反思 */
    reportEvent: (event_type: string, details: JsonObject = {}) => apiClient.post('/reflection/task/event/report', { event_type, details }),
    /** 查询反思任务进度 */
    taskProgress: (task_id: string) => apiClient.get(`/reflection/task/${task_id}/progress`),
    /** 分页查询历史反思会话 */
    listSessions: (limit = 50, offset = 0) => apiClient.get('/reflection/report/list', { params: { limit, offset } }),
    /** 查询已激活的优化规则 */
    activeRules: () => apiClient.get('/reflection/rule/active'),
    /** 一键启用/禁用规则 */
    toggleRule: (rule_id: string, is_active: boolean) => apiClient.put(`/reflection/rule/${rule_id}/toggle`, { is_active }),
    /** 查看通用技能资产 */
    listSkills: () => apiClient.get('/reflection/skill/list'),
    /** 获取待审核规则（补丁B） */
    pendingRules: () => apiClient.get('/reflection/pending-rules'),
    /** 批准规则上线（补丁B） */
    approveRule: (rule_id: string) => apiClient.post(`/reflection/rules/${rule_id}/approve`),
  },

  // ==========================================
  // 批次5：全局调度中枢 (System)
  // ==========================================
  system: {
    /** 全局统一指令入口 */
    command: (command: string, options: JsonObject = {}, project_id: NullableString = null) =>
      apiClient.post('/system/command', { command, options, project_id }),
    /** 系统健康探针与状态 */
    status: () => apiClient.get('/system/status'),
    /** 队列大屏：排队情况与Batch1引擎负载 */
    queue: () => apiClient.get('/system/queue'),
    /** 紧急熔断中断 */
    interrupt: () => apiClient.post('/system/interrupt'),
    /** 获取会话聊天历史 */
    chatHistory: (session_id: string) => apiClient.get(`/system/sessions/${session_id}/chat-history`),
    /** 资源统计（补丁E） */
    resourceStats: () => apiClient.get('/system/resource-stats'),
    /** 触发垃圾回收（补丁E） */
    triggerGC: () => apiClient.post('/system/gc-run'),
    /** GC 任务报告（补丁E） */
    gcReport: (task_id: string) => apiClient.get(`/system/gc/${task_id}/report`),
  },

  // ==========================================
  // 设置管理 (Settings)
  // ==========================================
  settings: {
    /** 获取大模型配置 */
    getLLM: () => apiClient.get('/settings/llm'),
    /** 保存大模型配置 */
    saveLLM: (data: JsonObject) => apiClient.post('/settings/llm', data),
    /** 获取系统功能开关 */
    getFeatures: () => apiClient.get('/settings/features'),
    /** 保存系统功能开关 */
    saveFeatures: (data: JsonObject) => apiClient.post('/settings/features', data),
  },

  // ==========================================
  // 会话池 (Sessions)
  // ==========================================
  sessions: {
    /** 创建会话 */
    create: (model_key: string, opts: JsonObject = {}) => apiClient.post('/sessions/create', { model_key, ...opts }),
    /** 列出所有会话 */
    list: () => apiClient.get('/sessions/list'),
    /** 会话统计 */
    stats: () => apiClient.get('/sessions/stats'),
    /** 获取单个会话 */
    get: (session_id: string) => apiClient.get(`/sessions/${session_id}`),
    /** 冻结会话 */
    freeze: (session_id: string) => apiClient.post(`/sessions/${session_id}/freeze`),
    /** 销毁会话 */
    destroy: (session_id: string) => apiClient.delete(`/sessions/${session_id}`),
  },

  // ==========================================
  // 提示词模板 (Prompts)
  // ==========================================
  prompts: {
    /** 列出所有模板 */
    list: () => apiClient.get('/prompts/list'),
    /** 渲染模板 */
    render: (template_id: string, variables: JsonObject) => apiClient.post('/prompts/render', { template_id, variables }),
    /** 保存模板 */
    save: (template: JsonObject) => apiClient.post('/prompts/save', template),
    /** 模板版本历史 */
    listVersions: (template_id: string) => apiClient.get(`/prompts/${template_id}/versions`),
    /** 模板回滚到指定版本 */
    rollback: (template_id: string, version: number) =>
      apiClient.post(`/prompts/${template_id}/versions/${version}/rollback`),
  },

  // ==========================================
  // 情感量化引擎 (Emotion)
  // ==========================================
  emotion: {
    /** 单段文本实时情感量化 */
    quantify: (text: string, project_id: NullableString = null, book_id: NullableString = null) =>
      apiClient.post('/emotion/quantify', { text, project_id, book_id }),
    /** 根据情感帧生成小说片段 */
    generate: (frame_id: string) => apiClient.post('/emotion/generate', { frame_id }),
    /** 启动整本书情感批量量化任务 */
    batchQuantize: (book_id: string) => apiClient.post(`/emotion/book/${book_id}/quantize-task`),
    /** 帧列表 */
    frames: (project_id = null, limit = 50) =>
      apiClient.get('/emotion/frames', { params: { project_id, limit } }),
    /** 帧详情 */
    frameDetail: (frame_id: string) => apiClient.get(`/emotion/frames/${frame_id}`),
  },

  // ==========================================
  // 插件管理 (Plugins)
  // ==========================================
  plugins: {
    /** 列出已安装插件 */
    list: () => apiClient.get('/plugins'),
    /** 安装插件 */
    install: () => apiClient.post('/plugins/install'),
  },

  // ==========================================
  // 智能体反思与经验库 (Experience)
  // ==========================================
  experience: {
    /** 提交新经验 */
    submit: (exp_type: string, content: string) => apiClient.post('/experience/', { exp_type, content }),
    /** 获取经验列表 */
    list: (status = null) => apiClient.get('/experience/', { params: { status } }),
    /** 审批经验 */
    approve: (exp_id: string, is_approved: boolean) => apiClient.put(`/experience/${exp_id}/approval`, { is_approved }),
    /** 拉取量化提示词注入 */
    getPrompt: () => apiClient.get('/experience/quantize-prompt'),
  },

  // ==========================================
  // 智能体总督与专员调度 (Orchestrator)
  // ==========================================
  orchestrator: {
    /** 获取执行计划 DAG */
    plan: (book_id: string, chapter_content: string, primary_model: string, worker_model: string) =>
      apiClient.post('/orchestrator/plan', { book_id, chapter_content, primary_model, worker_model }),
  },

  // ==========================================
  // 补丁1: 非线性叙事时间轴 (Timeline)
  // ==========================================
  timeline: {
    /** 项目时间线列表 */
    list: (project_id: string) => apiClient.get(`/projects/${project_id}/timelines`),
    /** 创建时间线 */
    create: (project_id: string, timeline: JsonObject) => apiClient.post(`/projects/${project_id}/timelines`, timeline),
    /** 更新时间线 */
    update: (project_id: string, timeline_id: string, timeline: JsonObject) =>
      apiClient.put(`/projects/${project_id}/timelines/${timeline_id}`, timeline),
    /** 删除时间线 */
    remove: (project_id: string, timeline_id: string) =>
      apiClient.delete(`/projects/${project_id}/timelines/${timeline_id}`),
    /** 新增事件 */
    addEvent: (project_id: string, timeline_id: string, event: JsonObject) =>
      apiClient.post(`/projects/${project_id}/timelines/${timeline_id}/events`, event),
    /** 更新事件 */
    updateEvent: (project_id: string, timeline_id: string, event_id: string, event: JsonObject) =>
      apiClient.put(`/projects/${project_id}/timelines/${timeline_id}/events/${event_id}`, event),
    /** 删除事件 */
    removeEvent: (project_id: string, timeline_id: string, event_id: string) =>
      apiClient.delete(`/projects/${project_id}/timelines/${timeline_id}/events/${event_id}`),
    /** 冲突检测 */
    conflicts: (project_id: string, timeline_id: string) =>
      apiClient.get(`/projects/${project_id}/timelines/${timeline_id}/conflicts`),
  },

  // ==========================================
  // 补丁2: 角色语调 TTS (TTS)
  // ==========================================
  tts: {
    /** 分割旁白/对话并识别角色 */
    segment: (text: string, known_characters = []) =>
      apiClient.post('/tts/segment', { text, known_characters }),
    /** 合成语音 */
    synthesize: (text: string, voice = null, engine = null) =>
      apiClient.post('/tts/synthesize', { text, voice, engine }),
    /** 音频播放地址 */
    audioURL: (file_name: string) => `${baseURL}/tts/audio/${file_name}`,
    /** 提交 OOC 反馈 */
    oocFeedback: (project_id: string, doc_id: string, character: string, content: string) =>
      apiClient.post('/tts/ooc-feedback', { project_id, doc_id, character, content }),
    /** 项目音色映射 */
    voiceProfiles: (project_id: string) => apiClient.get(`/projects/${project_id}/voice-profiles`),
  },

  // ==========================================
  // 补丁3: 版本分支平行宇宙 (Branch)
  // ==========================================
  branch: {
    /** 分支列表 */
    list: (doc_id: string) => apiClient.get(`/docs/${doc_id}/branches`),
    /** 创建分支 */
    create: (doc_id: string, name: string, from_branch_id = null) =>
      apiClient.post(`/docs/${doc_id}/branches`, { name, from_branch_id }),
    /** 切换分支 */
    switch: (doc_id: string, branch_id: string) =>
      apiClient.post(`/docs/${doc_id}/branches/${branch_id}/switch`),
    /** 归档分支 */
    archive: (doc_id: string, branch_id: string) =>
      apiClient.post(`/docs/${doc_id}/branches/${branch_id}/archive`),
    /** 恢复归档分支 */
    unarchive: (doc_id: string, branch_id: string) =>
      apiClient.post(`/docs/${doc_id}/branches/${branch_id}/unarchive`),
    /** 归档分支列表 */
    archived: (doc_id: string) => apiClient.get(`/docs/${doc_id}/branches/archived`),
    /** 分支差异对比 */
    diff: (doc_id: string, branch_a: string, branch_b: string) =>
      apiClient.get(`/docs/${doc_id}/branches/${branch_a}/diff/${branch_b}`),
  },

  // ==========================================
  // 补丁4: 场景可视化分镜 (Storyboard)
  // ==========================================
  storyboard: {
    /** 提取场景段落 */
    extractScenes: (text: string, style_tags = []) =>
      apiClient.post('/storyboard/extract-scenes', { text, style_tags }),
    /** 提交生图任务 */
    generate: (project_id: string, scene_text: string, style_tags = [], doc_id = null, start = null, end = null) =>
      apiClient.post(`/projects/${project_id}/storyboard/generate`, { scene_text, style_tags, doc_id, start, end }),
    /** 锚点列表 */
    anchors: (project_id: string, doc_id = null) =>
      apiClient.get(`/projects/${project_id}/storyboard/anchors`, { params: { doc_id } }),
    /** 按段落查图 */
    anchorsByRange: (project_id: string, doc_id: string, start: number, end: number) =>
      apiClient.get(`/projects/${project_id}/storyboard/anchors-by-range`, { params: { doc_id, start, end } }),
    /** 删除锚点 */
    removeAnchor: (project_id: string, anchor_id: string) =>
      apiClient.delete(`/projects/${project_id}/storyboard/anchors/${anchor_id}`),
    /** 图片地址 */
    imageURL: (project_id: string, file_name: string) =>
      `${baseURL}/projects/${project_id}/storyboard/images/${file_name}`,
  },

  // ==========================================
  // 批次7: 深度思考与元认知 (DeepThink)
  // ==========================================
  deepThink: {
    /** 提交长思考任务 */
    submit: (prompt: string, project_id = null, doc_id = null) =>
      apiClient.post('/deep-think/tasks', { prompt, project_id, doc_id }),
    /** 获取分析报告 */
    report: (task_id: string) => apiClient.get(`/deep-think/reports/${task_id}`),
    /** 获取检查点 */
    checkpoint: (task_id: string) => apiClient.get(`/deep-think/checkpoints/${task_id}`),
    /** 软件进化建议书 */
    tickets: () => apiClient.get('/deep-think/tickets'),
    /** 系统架构镜像 */
    blueprint: () => apiClient.get('/system/blueprint'),
  },

  // ==========================================
  // 批次7: 用户行为日志采集 (Behavior)
  // ==========================================
  behavior: {
    /** 上报行为事件 */
    report: (event_type: string, payload = {}) =>
      apiClient.post('/behavior/events', { event_type, payload }),
  },
}

export default api
