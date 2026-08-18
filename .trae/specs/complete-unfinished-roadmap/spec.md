# 完成未收尾工程路线图（Ledger 权威主线 + 后续闭环）Spec

change-id: `complete-unfinished-roadmap`

## Why

系统已完成核心架构（六 Batch + Feature 门控），但**严格顺序主线 Ledger authoritative 尚未真正落地**：真实活动库 `C:\Users\11482\AppData\Roaming\No0_AI_V4\data\library_index.db` 中 `cards=18118`，而 `ledger_claims/documents/outbox/passages` 全部为 `0`，仅有一次 `dry_run=PREVIEW` 的迁移记录（`migrated=18118` 但未 apply）。此外 SkillGovernance、受限代码执行、多文件修复、独立构建服务、诗词插件、Docling、Cosign/Sigstore 均为"部分完成但生产未闭环"状态。本 spec 以"结论先行 → 严格顺序"方式收尾这些剩余工程，所有改动按依赖顺序分阶段落地。

## What Changes

- **阶段 1 · Ledger 迁移前置（数据来源与路径）**
  - 统一运行时索引路径：消除 `get_ai_index_dir()`（workspace）与真实活动库（AppData）双库分歧，收敛到单一权威路径。
  - 补齐 `book_b10eaeb7` 来源：找回/重新登记原始文档 → 登记 `ledger_documents` 与段落（passage）；对无法溯源到原始文档的旧卡，显式标记为 `derived draft` 而非 `authoritative claim`。
  - 修复 `ledger_migration` 干跑计数语义：`dry_run` 下 `migrated` 不再计入"已迁移"。
- **阶段 2 · Ledger 真实迁移与验证**
  - 正式 `migrate_legacy_cards(dry_run=False)` apply；outbox 事件 drain 清空；逐条 reconciliation；dual-read soak；backup/restore drill；`LedgerReadiness` 全绿。
- **阶段 3 · 灰度切换（分两步）**
  - 第一步：`read_mode=ledger` + `authoritative=true`，保留 `allow_legacy_card_projection=true`。
  - 第二步（稳定后）：关闭旧 direct card write（业务 writer 统一改走 LedgerRepository/LedgerOutbox）。
- **阶段 4 · SkillGovernance 观察期闭环**
  - 退休/恢复状态补全、按 `candidate_id@version` 效果归因、默认多智能体接线、旧投影（universal_skills/InfoCard/行为插件 override）对账、QUANTIFIED/LEARNED 行为插件重启从 Governance 完整重建。
- **阶段 5 · 其余独立计划收尾**
  - 多文件受控自动修复、独立构建服务（资源隔离 + manifest/provenance + Windows CI 验证）、Poetry 研究闭环、Docling 可选生产接入、真实 Cosign/Sigstore 发布链路。

## Impact

- Affected specs（能力域）：
  - Ledger 权威账本（repository / outbox / read facade / dual-read / migration / readiness / backup / reconciliation）
  - 统一调度与创作（GlobalRouter、批次1 引擎、ModelDispatcher）
  - 受限代码执行与 Windows 隔离（code_execution / windows_job / resource_profile）
  - SkillGovernance 与多智能体闭环（skill_governance / novel_agent_* / behavior_plugins）
  - 诗词分析（poetry_analyzer）、文档解析（parser / Docling）、插件安全（plugin / cosign）、构建发布（build / CI）
- Affected code（关键文件/系统）：
  - `core/path_resolver.py`、`core/config_manager.py`、`core/database.py`
  - `services/ledger_*.py`、`services/indexer.py`、`services/global_router.py`
  - `services/skill_governance.py`、`services/behavior_plugins.py`、`services/novel_agent_*.py`
  - `services/code_execution.py`、`services/windows_job.py`、`services/resource_profile.py`
  - `models/poetry.py`、`services/poetry_analyzer.py`、`services/parser.py`
  - `core/security.py`、`api/plugins.py`、`plugins.trust-policy.example.json`、`build*.ps1/bat`、`.github/workflows/ci.yml`
  - `config/config.yaml`、`config/llm_provider.yaml`
  - 前端 `frontend/src/`（新增诗词研究界面 / typecheck 基线清理）

## ADDED Requirements

### Requirement: Ledger 来源契约（真实数据前置门禁）
系统在生成任何 `authoritative` claim 之前，SHALL 满足来源契约：claim 必须关联到已登记的 `ledger_document` 及其 passage；无法溯源到原始文档的旧卡，SHALL 显式标记为 `derived/draft` 状态，不得伪装为权威 claim。

#### Scenario: 无锚点旧卡迁移
- **WHEN** 对 `source_anchor='{}'` 且无 `source_document_id` 的 18,117 张旧卡执行迁移
- **THEN** 这些卡要么被补齐 passage/source anchor 后生成 claims，要么被标记为 derived draft；绝不产生悬挂的 authoritative claim

#### Scenario: 迁移干跑语义
- **WHEN** 执行 `migrate_legacy_cards(dry_run=True)`
- **THEN** 结果中 `migrated` 字段如实反映"仅预览、未 apply"，与 `ledger_claims` 实际计数一致

### Requirement: 运行时索引路径统一
系统 SHALL 使用单一权威的库索引路径（AppData `%APPDATA%/No0_AI_V4/data/`），禁止在运行时与 workspace `.ai_index/` 之间分裂出第二个活动索引库。

#### Scenario: 双库检测
- **WHEN** 系统启动或索引初始化
- **THEN** 若发现 workspace `.ai_index/library_index.db` 与权威路径库并存且状态不一致，SHALL 告警并统一到权威路径，不产生分裂写入

### Requirement: Ledger 灰度切换门禁
系统 SHALL 仅在 `LedgerReadiness` 全绿（迁移完成、outbox 清空、卡片/claim 数量一致、双读一致、备份可恢复）后，才允许切换 `read_mode=ledger` + `authoritative=true`；灰度期保留 `allow_legacy_card_projection=true`，稳定后再关闭旧 direct card write。

#### Scenario: 未达标即切换
- **WHEN** readiness 报告存在任一 blocker 却请求开启 authoritative
- **THEN** 启动被 fail-closed 拒绝（沿用 `core/bootstrap.py` 现有门禁）

### Requirement: SkillGovernance 效果归因与状态闭环
系统 SHALL 按 `candidate_id@version` 归因技能效果统计，并支持技能的退休/恢复完整状态机；行为插件在服务重启后 SHALL 从 Governance 源完整重建（QUANTIFIED/LEARNED）。

#### Scenario: 重启重建
- **WHEN** 服务重启后加载行为插件
- **THEN** 插件定义从 `skill_governance` 权威源重建，不依赖旧 `universal_skills`/InfoCard 兼容写路径

### Requirement: 受限代码执行隔离补全
系统 SHALL 为 CodeTask 提供：Windows 实机 CPU/内存/进程树限制验证、子进程/孙进程逃逸测试、磁盘硬配额、操作系统级网络断网策略、任务状态持久化恢复。

#### Scenario: 逃逸测试
- **WHEN** 在 Windows 实机运行 Job Object 限制下的任务，任务内部派生孙进程
- **THEN** 孙进程同样受 CPU/内存/进程数限制约束，不逃逸出配额

### Requirement: 独立构建服务
系统 SHALL 提供独立 builder 服务：隔离源码卷/输出卷，CPU/内存/磁盘/进程限制，lockfile/dependency digest，构建产物 manifest + provenance，签名/扫描/导入主应用前验证，构建失败可靠返回非零状态，并在 Windows CI 验证 EXE 构建。

### Requirement: 真实 Cosign/Sigstore 发布链路
系统 SHALL 将真实 `.sig`/bundle 绑定到插件/构建产物：证书 identity、issuer 校验、Rekor/Fulcio provenance、artifact digest 绑定验证、CI 发布签名、部署前强制验证。

### Requirement: Poetry 研究闭环
系统 SHALL 补齐诗词能力：作品/版本持久化、Ledger provenance、平仄规则、中古音/普通话/平水韵分离、韵部候选、五绝/七绝/律诗/词牌格律验证、典故候选与出处回链、版本校勘、评测集、前端研究界面。

### Requirement: Docling 可选生产接入
系统 SHALL 补齐 Docling：依赖锁定、模型工件预下载/离线模式、CPU 线程/页数限制、解析超时/并发限制、结构化 provenance、表格/页码/版面 golden tests、生产 readiness 检查、默认/增强镜像分离。

## MODIFIED Requirements

### Requirement: Ledger 文档生命周期（既有接入，需补全）
现有 import/rename/delete 已接入 Ledger；本 spec 要求**存量历史文档全量登记**进 `ledger_documents`，并让剩余仍直接写 `cards` 的业务 writer 迁移到 `LedgerRepository`/`LedgerOutbox`（在阶段 3 第二步关闭旧 direct card write 时完成）。

### Requirement: Feature 默认装配（既有门控，需调整）
`feature.novel_multi_agent_enable` 当前默认 `false`（`config.yaml` 与 `bootstrap` 默认值）。阶段 4 在 SkillGovernance 闭环验证通过后，SHALL 评估将多智能体接线设为默认装配，并在文档中明确开启条件。

### Requirement: CI 覆盖（既有，需扩展）
`.github/workflows/ci.yml` 当前仅 ubuntu-latest。阶段 5 SHALL 增加 Windows runner（Windows Job Object 测试 + EXE 打包验证），并在发布前增加前端 typecheck 门禁（`npm run type-check`）。

## REMOVED Requirements

### Requirement: 旧 direct card write 路径（灰度稳定后移除）
**Reason**: Ledger 成为权威数据源后，业务 writer 直接写 `cards` 会造成账本与卡片漂移，破坏 reconciliation 与权威一致性。
**Migration**: 阶段 3 第二步将所有 writer 迁移到 `LedgerRepository`/`LedgerOutbox`，保留 `allow_legacy_card_projection` 作为只读投影兼容层，待观察期后再下线。

### Requirement: 无来源锚点的隐藏式权威 claim（禁止）
**Reason**: `source_anchor='{}'` 且无 document 的旧卡若被直接投影为 authoritative claim，会伪造来源证据。
**Migration**: 一律先补 passage/锚点，或将卡标记为 `derived/draft`。
