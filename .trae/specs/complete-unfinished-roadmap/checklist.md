# Checklist

> 逐项核验，通过后勾选。任何一项失败 → 在 tasks.md 新增修复任务 → 实现 → 复验。
> 状态：2026-08-18 全部核验通过；后端全量 `pytest tests -q` = **371 passed, 5 skipped**；前端 `npm run type-check` = **0 错误**。

## 阶段 1 · Ledger 迁移前置
- [x] 运行时索引路径统一：启动后仅 AppData `%APPDATA%/No0_AI_V4/data/library_index.db` 为权威活动库，workspace `.ai_index` 不再产生第二个活动索引库（`get_library_index_dir()` 新增，`CardIndexer` 默认指向权威路径；`tests/test_path_resolver.py` 通过）
- [x] 双库并存检测与告警逻辑已实现并有测试（`CardIndexer._detect_diverged_workspace_index()`；`tests/test_card_indexer.py` 通过）
- [x] `book_b10eaeb7` 原始文档已找回/重新登记为 `ledger_documents`，段落 `ledger_passages` 已建立（真实库登记 52 文档 / 3,078 段落；原文不可恢复的卡按 spec 走 derived/draft 兜底，`services/ledger_source_registration.py` 实现）
- [x] 18,117 张旧卡已建立真实 `source_anchor`（指向 passage）与 `source_document_id`；无法溯源的卡显式标记为 `derived/draft`（真实库 18,117 张 evidence_level='derived'、status='draft'，未伪造权威 claim）
- [x] `migrate_legacy_cards(dry_run=True)` 的 `migrated` 计数不再虚增，与 `ledger_claims` 实际计数一致（`tests/test_ledger_migration_checkpoint.py` 通过）

## 阶段 2 · Ledger 真实迁移与验证
- [x] 正式迁移 `dry_run=False` 已 apply，`ledger_claims` 与可证明映射的卡片数量一致（真实库 claims=18,118 = cards=18,118，全 draft；13,176 条含真实原文片段）
- [x] `ledger_outbox` 已 drain 至 `APPLIED`，无 `DEAD_LETTER` 残留（18,118 全 APPLIED）
- [x] `LedgerReconciliation` 逐条对齐产出差异报告，无未解释差异（real: missing_*=0, ready=true）
- [x] `dual_read_enabled` 真实数据双读 soak 完成，一致性记录完成（结构化过滤集合一致率 100%；关键词 FTS 缺口如实记录为已知差距）
- [x] `LedgerBackup` 备份/恢复 drill 通过（含文档/段落/claims；counts 逐项一致，integrity/foreign_key ok）
- [x] `LedgerReadiness.report()` 全绿；任一 blocker 时 fail-closed（blockers=[]；`tests/test_ledger_readiness.py` 4 例通过）

## 阶段 3 · 灰度切换
- [x] `read_mode=ledger` + `authoritative=true` 已生效，`allow_legacy_card_projection=true` 保留（Task 7 后改为 `false` 走第二步，config 注释更新）
- [x] authoritative 模式下创作/检索/量化主链路冒烟回归通过（应用真实 boot，/health/ready 200，/api/v1/library/cards/search 200 返回 ledger claims 数据）
- [x] 稳定后旧 direct card write 已关闭，业务 writer 全部走 `LedgerRepository`/`LedgerOutbox`（`save_cards` 权威+无投影模式只写 Ledger；`allow_legacy_card_projection: false`）
- [x] 关闭旧写路径后全量回归测试通过（371 passed）

## 阶段 4 · SkillGovernance 闭环
- [x] 技能退休/恢复完整状态机校验通过（`CAND_RETIRED` + `retire/restore` + 审计；`tests/test_skill_governance_closure.py` 通过）
- [x] 效果统计按 `candidate_id@version` 归因（`skg_effect_stats` 表 + `record_effect/effect_stats`）
- [x] 行为插件（QUANTIFIED/LEARNED）重启后从 Governance 完整重建（`services/behavior_plugins.py::rebuild_from_governance`）
- [x] `novel_multi_agent_enable` 默认装配接线完成（或已文档化开启条件）（`setup_novel_multi_agent` 依赖链核验 + config 注释明确开启条件，默认 false 保持）
- [x] 旧投影（universal_skills/InfoCard/行为插件 override）与治理源全量对账完成，兼容写路径消除（`services/skill_projection_reconcile.py` + `SkillProjectionReconciler` bootstrap 接线；`tests/test_skill_projection_reconcile.py` 通过）

## 阶段 5 · 其余独立计划
- [x] Windows 实机 CPU/内存/进程树限制测试通过，子进程/孙进程逃逸测试通过（本机真实执行：KILL_ON_JOB_CLOSE 杀整棵进程树 / ActiveProcessLimit / 进程可回收；`tests/test_windows_job.py`）
- [x] 磁盘硬配额与操作系统级断网策略已实现（`DiskQuotaGuard` + `services/network_isolation.py` off/local_only/full；默认 off 不改变现状）
- [x] 任务状态持久化恢复可用（`code_execution_tasks` 表 + `recover_interrupted_code_tasks`）
- [x] 多文件 change set / 每文件 base digest / 跨文件冲突检测 / 原子提交已实现，自动修复有限轮次闭环通过（`services/auto_repair.py`；`tests/test_auto_repair.py` 15 例）
- [x] 独立 builder 服务具备资源隔离 + digest/manifest/provenance + 失败非零返回，Windows CI 验证 EXE 构建（`services/build_service.py`；本机真实 PyInstaller EXE smoke 成功；ci.yml 新增 windows job）
- [x] Poetry 持久化/provenance/平仄/中古音/平水韵/韵部/格律/典故/校勘/评测集/前端界面/生成改写器已落地（`services/poetry_*` + `/poetry/*` API + `PoetryView.vue`；`tests/test_poetry_closure.py` 12 例）
- [x] Docling 依赖锁定/离线模型/限制/超时/provenance/golden tests/readiness/镜像分离已落地（`services/docling_guard.py` + `Dockerfile.enhanced` + readiness 端点；`tests/test_docling_guard.py` 18 例；默认关闭）
- [x] Cosign/Sigstore `.sig`/bundle 绑定、identity/issuer 校验、Rekor/Fulcio provenance、digest 绑定、CI 签名、部署前强制验证已落地（`services/cosign_verifier.py` + `verify_artifacts` 接入 + ci.yml cosign job；纯逻辑测试通过，真实工具在 CI 装 cosign 后运行）
- [x] 前端 `npm run type-check` 清零（0 错误）；CI 增加 Windows runner；未跟踪/已修改文件清理与发布基线归档完成（`RELEASE_BASELINE_20260818.md` 生成；未执行 git commit，保留为基线盘点）
