# 功能业务数据（Functional Data）MVP

## 目标

每个功能可以定义自己的业务对象/台账，记录支持字段、草稿权限、变更历史、附件版本、受控文档最小语义和站内到期提醒。供应商与受控文件只是两个配置样例，不在代码中写死。

可直接提交给 `PUT /schema` 的样例：[供应商样例](examples/feature-data-supplier.json)、[受控文件样例](examples/feature-data-controlled-document.json)。界面默认使用表单设计器，不需要用户编辑 JSON。

## 存储

- 对象定义：功能工作区 `.maitu/feature-data/schema.json`（随工作区备份；不进入专家模板导出）。
- 记录/变更/附件元数据/提醒/通知：主数据库表；功能责任人字段由 migration 046 增补（045 已部署的数据库必须执行 046）。
- 附件内容：功能工作区 `.maitu/feature-data/files/<record_id>/<file_id>`，通过 `BackendWorkspace` 读写。
- 删除记录保留文件供审计，不物理删除。

## Schema

```json
{
  "version": 1,
  "objects": [
    {
      "key": "supplier",
      "name": {"zh": "供应商", "en": "Supplier"},
      "kind": "ledger",
      "title_field": "name",
      "controlled": false,
      "fields": [
        {"key": "name", "label": {"zh": "名称"}, "type": "text", "required": true},
        {"key": "expires_at", "label": {"zh": "到期日"}, "type": "date", "reminder_days": [90, 60, 30]},
        {"key": "license", "label": {"zh": "资质文件"}, "type": "file"}
      ]
    },
    {
      "key": "controlled_doc",
      "name": {"zh": "受控文件"},
      "kind": "document",
      "title_field": "title",
      "controlled": true,
      "fields": [
        {"key": "doc_no", "type": "text", "required": true},
        {"key": "title", "type": "text", "required": true},
        {"key": "version", "type": "text", "required": true},
        {"key": "effective_at", "type": "date"},
        {"key": "status", "type": "enum", "options": ["draft", "approved", "effective", "obsolete"]},
        {"key": "revision_reason", "type": "textarea"},
        {"key": "file", "type": "file"}
      ]
    }
  ]
}
```

支持字段类型：`text`、`textarea`、`number`、`integer`、`boolean`、`date`、`datetime`、`enum`、`file`、`user`、`org_unit`、`record_ref`、`knowledge_ref`。`file` 值不放在 `values`，由附件接口管理。`date`/`datetime` 可带 `reminder_days`。`user` 保存正整数用户 ID；`org_unit` 保存组织单元 key。`record_ref` 保存目标记录 ID，可指定 `target_object` 和可选 `target_agent_id`（默认当前功能）。`knowledge_ref` 保存 `{kb_id, document_id, version?, page?, layout?}`；省略版本时固定已登记的原件 SHA-256。带版本的原件下载验证实际字节并提供请求期临时快照；外部文件变更但尚未同步时返回 409，不把新文件当成旧版本下载，也不提供历史原件存储。
限制：每个功能最多 20 个对象，每个对象最多 40 个字段；对象 key `^[a-z][a-z0-9_]{0,63}$`；字段 key 同规则。列表过滤每次最多 20 个条件。
`controlled=true` 仅允许 `kind=document`，且必须含一个 `file` 字段。

日期字段可声明固定计算：`computed: {op:"add_days"|"add_months"|"add_years", source_field:"issued_on", amount:12}`，或用 `amount_field:"cycle_months"` 读取整数字段。仅允许安全的固定运算；校验依赖和循环，按月/年末日夹取到目标月最后一天。缺少输入时不产生派生值，并取消对应待发送提醒、保留提醒历史；客户端不可伪造提交计算字段，更新时服务端重新计算。

`reminder_recipients` 可配置 `creator`、`owner`、`responsible`、`user_fields`、`unit_fields`、`role_keys`。角色键仅限已有 `admin`、`enterprise_admin`、`unit_admin`、`user`。未配置时保留创建人和功能所有者的原有默认；交接后的创建人回退使用当前责任人。单位收件人沿用工作调度中的本单位及子单位解析。发送时仅纳入仍有效、且当前能读取该功能与记录的用户；提醒日期按服务器时区计算。

## 权限

- 读 schema/记录/附件：使用统一功能访问策略；所属企业管理员可管理本企业已发布功能，其他用户仍须满足功能 ACL。
- 写 schema：功能管理者（`feature_can_manage`，通过 `AgentCapability.CONFIGURATION`）。
- 建记录：任何可访问功能者；`created_by=caller`。
- 改/删/附件上传：私有草稿仅创建人；共享记录由当前责任人（未交接时为创建人）或功能管理者维护。交接保留原始创建人及审计，但原创建人不再仅凭创建身份获得写权限。
- 记录可见性：
  - `private`：仅创建人（管理者也不越过；审计例外后续再加）。
  - `feature`：跟随功能 ACL；私有草稿不得通过交接转为共享或被接管。
- 功能 draft 时新建记录默认 `private`；active 时默认 `feature`。
- draft 记录发布到 feature 需要功能 active，且仅创建人/管理者。
- 记录发布后不自动回退；功能转回 draft 不改变既有 `feature` 记录可见性（由功能 ACL 决定）。

## 记录状态与变更历史

- `feature_data_records.visibility` 为 `private|feature`。
- 每次创建/更新/发布/附件上传写 `feature_data_record_changes`，`items_json` 保存字段级 before/after 或动作摘要。
- `revision` 乐观锁：更新请求带 `expected_revision`，不匹配返回 409。
- 文档受控模式下 `values.status` 的状态变更由记录更新接口承担，至少记录 before/after；第一版不引入独立审批流。

## API

全部挂在 `/api/agents/{agent_id}/feature-data`：

- `GET /schema` → `{schema, error}`
- `PUT /schema` body `{schema}`（管理者）
- `GET /{object_key}/records?q=&limit=&offset=&filters=&sort_by=&sort_order=` → 可见记录及过滤后总数。`filters` 是 JSON 数组，AND 条件，支持 `eq`、`ne`、`contains`、`gt`、`gte`、`lt`、`lte`、`in`、`is_empty`；每次最多 20 项并按字段类型校验。排序字段为 schema 字段或 `created_at`/`updated_at`/`title`，方向 `asc|desc`，稳定分页。
- Agent 工具 `feature_data_list` 通过 `filters_json`、`sort_by`、`sort_order` 接受相同列表条件，并沿用 HTTP 的字段校验、过滤、排序和 ACL；旧查询及分页参数仍可用。
- `GET /references/records?target_agent_id=&object_key=&q=` → 仅当前用户可读的目标记录选项；知识库引用选择沿用已有授权知识库/文档接口。
- `POST /{object_key}/records` body `{values}` → 创建（`title` 自动取 `title_field`）；拒绝客户端传入计算字段
- `GET /records/{record_id}` → 记录详情、附件及当前可用的引用解析信息
- `PATCH /records/{record_id}` body `{values?, expected_revision}` → 更新字段，计算字段重算
- `POST /records/{record_id}/publish` → private → feature
- `POST /feature-data/handover` body `{from_user_id,to_user_id,object_key?}` → 管理者转交共享记录，返回 `{transferred_count,record_ids}`；目标须为有效且已授权用户，私有草稿保持创建人专属
- `DELETE /records/{record_id}` → 软删除
- `GET /records/{record_id}/history?limit=` → 变更历史；无权读取引用目标时历史值会脱敏
- `POST /records/{record_id}/files/{field_key}` multipart `file`, `note?` → 新版本
- `GET /records/{record_id}/files` → 附件及版本
- `GET /records/{record_id}/files/{file_id}/download` → 文件
- `GET /notifications?unread_only=` → 当前用户站内通知
- `POST /notifications/{id}/read` → 标记已读
- `POST /reminders/{id}/complete` → 完成提醒

记录引用必须指向存在、未删除、对象类型匹配且当前调用者可读的记录，同时满足目标功能 ACL 与记录可见性。知识引用按当前知识库/文档 ACL 和定位信息校验。每次读取都重新解析权限；撤权后响应和历史记录中的引用值脱敏，不能泄露标题、路径或正文。`stale` 表示当前原件与固定版本不同，不代表历史字节仍可取回。

## 提醒

- 记录创建/更新时，从 schema 的日期字段计算提醒行；`due_at` 为 ISO 日期。
- 每个 `(record_id, field_key, due_at)` 唯一；状态 `open|done|dismissed`。
- 站内通知：`feature_data_notifications`，每个提醒每个用户每个阈值一条，去重；`channel` 字段当前固定 `in_app`，后续邮件/IM 适配器复用同一提醒源。
- 默认收件人为记录创建人 + 功能所有者；可用 `reminder_recipients` 声明责任人、用户字段、组织单元字段及已存在角色。组织单元含子单位；动态角色与组织单元在发送时解析，且收件人必须为有效用户并当前可读该功能和记录。
- 定时扫描：每 1 分钟；到期前阈值 90/60/30 天；业务日期按服务端时区计算。触发后写通知并推进 `next_notify_at`。
- 邮件/IM 渠道后续使用同一 reminder 源，不改变表结构。

## UI

功能详情“工作流”抽屉增加“业务数据”页签：

- 对象选择/记录列表/搜索
- 记录详情、字段编辑、保存
- 附件上传/版本列表/下载
- 变更历史
- 受控文档状态/版本显示
- 提醒/通知列表与完成
- `record_ref` 与 `knowledge_ref` 使用当前授权记录/文档选择器；当前版本失配显示为过期引用，不展示历史字节。
- 计算字段只读，由服务端重算；记录列表支持结构化筛选和排序。
- 管理者通过表单式“数据结构”设计器维护业务对象和字段，不需要手写 JSON；服务端仍负责字段名校验和受控文件规则

## Agent 工具

`feature_data_list`、`feature_data_get`、`feature_data_create`、`feature_data_update`、`feature_data_history`，均以当前 agent 的功能身份和调用者权限执行；不暴露其他功能数据。
