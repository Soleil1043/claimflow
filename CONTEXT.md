# CONTEXT.md — 领域术语表

> 核赔平台（多险种）统一语言。架构评审/新会话先读此文件再读代码。
> 架构决策记录见 `.agent/decisions.md`（D037-D040 为当前形态的依据）。

## 核心名词

- **案件（Case）**：一次理赔申请的完整生命周期，`case_id` 唯一标识，自然键
  `(user_id, policy_no, claimed_amount, incident_date)` 幂等。
- **险种（InsuranceLine）**：medical / auto / property / accident。worker 按"险种 pack"
  分批上线，首批仅 medical；未上线险种受理期直接转人工。
- **阶段（Stage）**：核赔流水线的六个 worker 步骤——材料审核（material_review）、
  保单核验（policy_verify）、风控筛查（fraud_check）、责任认定（liability_judge）、
  金额理算（amount_calc）、决定书生成（decision_generate）。
- **派发目标（DispatchTarget）**：orchestrator 可调度对象 = 六个阶段 + `human`（转人工）。
  定义于 `schemas/stages.py` 的 StrEnum。
- **StageSpec**：阶段的唯一权威定义（name / channel / output_model / requires /
  snapshot_keys / in_must_complete / description），注册表在 `schemas/stages.py`。
  阶段知识只写这一处，其余全部派生（D040）。
- **结论 channel**：ClaimCaseState 中各阶段唯一写者的字段（material/policy/risk/
  liability/calc/decision/compliance），值为对应产出模型的 dump。
  字段所有权表见 docs/claimflow-新架构设计-v2.md 5.3。
- **守卫（Guard）**：前置条件强制（代码层，D039 安全设计 2）——前置缺失改投、
  重复派发去重、decision_generate 必须单派、材料残缺时查询类 worker 丢弃、
  补件恢复后允许重跑材料审核。算法是代码，前置数据在 StageSpec.requires。
- **静态合规门**：decision_generate → compliance_gate 是图上焊死的静态边，
  不在调度空间（D039 铁律 F10）。compliance 不是 DispatchTarget，不进 StageSpec。
- **兜底编排（default_route）**：orchestrator LLM 失败/超预算时按险种标准管线
  确定性推进（D039 安全设计 3）。
- **skill 规程**：`skills/<stage>/<line>.md` 作业规程包，人工调优面——准确率迭代
  改文本不改代码（D039）；漂移由金样本路由一致率 ≥95% 软门兜住。
- **决定书（Decision Document）**：低风险小额案件的最终产出，版本化落库；
  正文金额必须等于理算结果（机器断言，不依赖 LLM）。
- **工单（Ticket）**：人工介入三类——SUPPLEMENT（补件）/ REVIEW（核赔复核）/
  ESCAPE（升级），interrupt + Command(resume) 实现，坐席回写必过合规复审。
- **分级自动**：低风险 + 责任明确 + 金额阈值内自动签发；否则转人工。阈值配置化
  （pydantic-settings），阈值契约归一见 D040 后续任务。
