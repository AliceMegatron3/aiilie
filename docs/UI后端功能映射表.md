# 前端 UI ↔ 后端功能 对应关系表

> 生成日期: 2026-08-16 | 依据: 前端按后端功能域重构(批次A-C)后的实际接线
> 说明: 每条 = 「前端哪个视图/按钮」 → 点击后调用「哪个后端接口」 → 触发「什么后端功能」

## 一、创作域

### 项目库 `/projects` → ProjectsView
| 前端操作 | 后端接口 | 触发功能 |
|---|---|---|
| 切换算力模式 | `POST /projects/{id}/mode` | rapid/think 模式切换 |
| 绑定/解绑书库(project面板) | `POST /projects/{id}/bind-book` / `unbind-book` | 项目绑定知识库 |
| 创建/列出项目 | `POST/GET /projects` | 项目 CRUD |
| 新增文档 | `POST /projects/{id}/docs` | 录入文档归档 |
| 打开文档 | `GET /docs/{id}` | 读取全文 |

### 文档编辑器 `/projects/:pid/docs/:did` → DocumentEditor
| 前端操作 | 后端接口 | 触发功能 |
|---|---|---|
| 保存 | `PUT /docs/{did}` | 自动保存+版本快照 |
| 确认定稿 | `POST /docs/{did}/author-confirm` | 生成保留率信号(P1) |
| 登记生成基线 | `POST /docs/{did}/generation-baseline` | 标记待确认生成稿 |
| 版本历史/回滚 | `GET/POST /docs/{did}/versions` | 文档版本管理 |
| 分支管理 | `/docs/{did}/branches*` | 平行宇宙分支(需开关) |

### 叙事结构 `/narrative` → NarrativeView(批次2新增)
| 前端操作 | 后端接口 | 触发功能 |
|---|---|---|
| 新建卷 | `POST /narrative/volumes` | 卷纲要 |
| 章列表 | `GET /narrative/projects/{pid}/chapters` | 章纲读取 |
| 解析纲要 | `POST /narrative/chapters/parse` | 自由文本→拍/预算/伏笔 |
| 确认拍纲 | `POST /narrative/chapters/{cid}/confirm-beats` | 拍纲进入生成约束 |
| 伏笔巡检 | `GET /narrative/projects/{pid}/threads/audit` | 超期硬约束告警 |
| 弧线套用/预览 | `POST /narrative/arcs/{id}/preview`, `apply-arc` | 预算序列派生 |
| 弧线偏差 | `GET /narrative/projects/{pid}/arc-variance` | 配方vs实际报告 |

### 世界观锁定 `/lockfield` → LockfieldView(批次2新增)
| 前端操作 | 后端接口 | 触发功能 |
|---|---|---|
| 保存锁定场 | `POST /lockfield/projects/{pid}` | 史实基线配置 |
| 查看must集 | `GET /lockfield/projects/{pid}/must-set` | 物化must集(稳定前缀) |
| 版本/回滚 | `GET/POST /lockfield/projects/{pid}/versions`, `rollback` | 编年史 |
| 偏离登记/确认 | `POST /lockfield/divergences`, `{id}/actions` | 架空vs史实记录 |

### 群像时间线 `/ensemble-history` → EnsembleHistoryView(批次2新增)
| 前端操作 | 后端接口 | 触发功能 |
|---|---|---|
| 角色轨道清单 | `GET /ensemble/projects/{pid}/tracks` | 轨道读取 |
| 角色历史 | `GET /ensemble/projects/{pid}/tracks/{cid}/history` | 按章号快照链 |
| 指定章号状态 | `GET .../tracks/{cid}/at-chapter?chapter=N` | 该章角色状态 |

## 二、知识域

### 书库 `/library` → LibraryView/LibraryPanel
| 前端操作 | 后端接口 | 触发功能 |
|---|---|---|
| 上传书籍 | `POST /library/books/upload_file` | 书籍入库(白名单+大小上限) |
| 量化书籍 | `POST /library/books/{bid}/quantize` | 卡片量化管线 |
| 检索卡片 | `GET /library/cards/search` | 多维卡检索 |
| 卡详情 | `GET /library/cards/{cid}/detail` | 卡读取 |

### 知识缺口 `/knowledge-gaps` → KnowledgeGapsView(批次2新增)
| 前端操作 | 后端接口 | 触发功能 |
|---|---|---|
| 缺口聚账 | `GET /knowledge/gaps` | 检索未命中聚合 |
| 补全建议 | `GET/POST /knowledge/proposals`, `propose` | 补全PENDING |
| 执行补全 | `POST /knowledge/complete` | LLM→draft卡待审 |

### 情感引擎 `/emotion` → EmotionView/EmotionFramesPanel
| 前端操作 | 后端接口 | 触发功能 |
|---|---|---|
| 情感量化 | `POST /emotion/quantify` | 单段情感帧 |
| 批量量化 | `POST /emotion/book/{bid}/quantize-task` | 整书情感 |
| 帧列表/详情 | `GET /emotion/frames*` | 情感帧读取 |

### 叙事时间轴 `/timeline` → TimelineView
| 前端操作 | 后端接口 | 触发功能 |
|---|---|---|
| 时间线CRUD | `GET/POST/PUT/DELETE /projects/{pid}/timelines*` | 非线性叙事 |
| 冲突检查 | `GET /projects/{pid}/timelines/{tid}/conflicts` | 时间线冲突 |

## 三、智能域

### 智能体 `/agents` → AgentsView/AgentDashboard
| 前端操作 | 后端接口 | 触发功能 |
|---|---|---|
| 智能体面版 | (store-only,未调后端) | 多智能体(默认关) |

### 深度思考 `/deep-think` → DeepThinkView
| 前端操作 | 后端接口 | 触发功能 |
|---|---|---|
| 提交长思考 | `POST /deep-think/tasks` | 五阶段深思考 |
| 报告/检查点 | `GET /deep-think/reports/{id}`, `checkpoints/{id}` | 分析报告 |

### 会话池 `/sessions` → SessionsView/SessionsPanel
| 前端操作 | 后端接口 | 触发功能 |
|---|---|---|
| 会话列表/统计 | `GET /sessions/list`, `stats` | 会话池 |
| 冻结/销毁 | `POST/DELETE /sessions/{sid}` | 会话管理 |

### 反思记录 `/reflections` → ReflectionsView/ReflectionsPanel
| 前端操作 | 后端接口 | 触发功能 |
|---|---|---|
| 触发反思 | `POST /reflection/task/trigger` | 反思采集 |
| 规则/技能 | `GET /reflection/rule/active`, `skill/list` | 学习产物 |

## 四、系统域

### 插件 `/plugins` → PluginsView(批次A新增)
| 前端操作 | 后端接口 | 触发功能 |
|---|---|---|
| 行为插件清单 | `GET /plugins/behavior` | 插件规格+统计 |
| 治理流转 | `POST /plugins/behavior/{id}/status` | active/gray/retired |
| 技能候选 | `GET /reflection/skills/candidates` | 量化/反思候选 |
| 候选晋升 | `POST /reflection/skills/candidates/{cid}/promote` | 版本化 |

### 工作区 `/workspace` → WorkspaceView(批次A新增)
| 前端操作 | 后端接口 | 触发功能 |
|---|---|---|
| 工作区信息 | `GET /workspace/info` | 根路径 |
| 目录浏览 | `POST /workspace/browse` | 只读浏览(限工作区) |
| 导入子项目 | `POST /workspace/import` | 文件夹导入 |

### 提示词模板 `/templates` → TemplatesView/TemplatesPanel
| 前端操作 | 后端接口 | 触发功能 |
|---|---|---|
| 模板列表 | `GET /prompts/list` | 模板库 |
| 版本/回滚 | `GET/POST /prompts/{tid}/versions` | 模板版本 |

### 系统设置 `/settings` → SettingsView
| 前端操作 | 后端接口 | 触发功能 |
|---|---|---|
| LLM配置 | `GET/POST /settings/llm` | 云端/本地模型 |
| 连通性检测 | `GET /llm/deepseek/ping` | 模型连通(批次B) |
| 主题切换 | `POST(本地store)` | 皮肤接口(批次C) |

## 全局
| 组件 | 后端接口 | 触发功能 |
|---|---|---|
| RightPanel AI交互 | `POST /system/command` | 统一指令路由 |
| 治理面板(浮动) | `/plugins/behavior*`, `/narrative/arcs*`, `/ensemble*` | 插件/弧线/群像治理 |

## 后端有、前端暂未展示(后续)
- `/novel-agent/*`(多智能体默认关)
- `/library/*/quantize/converge`, `/scratchpad`(agent内部工具)
- `/experience/*`(经验域)
- `/plugins/install`(501未实现,故意不展示)
- `/system/subconscious`(经WS消费)