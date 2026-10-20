# 功能业务数据（Functional Data）MVP

## 目标

每个功能可以定义自己的业务对象/台账，记录支持字段、草稿权限、变更历史、附件版本、受控文档最小语义和站内到期提醒。供应商与受控文件只是两个配置样例，不在代码中写死。

可直接提交给 `PUT /schema` 的样例：[供应商样例](examples/feature-data-supplier.json)、[受控文件样例](examples/feature-data-controlled-document.json)。界面默认使用表单设计器，不需要用户编辑 JSON。

## 存储

- 对象定义：功能工作区 `.maitu/feature-data/schema.json`（随工作区备份；不进入专家模板导出）。
- 记录/变更/附件元数据/提醒/通知：主数据库表（migration 042）。
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

支持字段类型：`text`、`textarea`、`number`、`integer`、`boolean`、`date`、`datetime`、`enum`、`file`。`file` 值不放在 `values`，由附件接口管理。`date`/`datetime` 可带 `reminder_days`。
限制：每个功能最多 20 个对象，每个对象最多 40 个字段；对象 key `^[a-z][a-z0-9_]{0,63}$`；字段 key 同规则。
`controlled=true` 仅允许 `kind=document`，且必须含一个 `file` 字段。

## 权限

- 读 schema/记录/附件：`require_agent_row`（功能 ACL）。
- 写 schema：功能管理者（`feature_can_manage`，通过 `AgentCapability.CONFIGURATION`）。
- 建记录：任何可访问功能者；`created_by=caller`。
- 改/删/附件上传/发布记录：记录创建人或功能管理者。
- 记录可见性：
  - `private`：仅创建人（管理者也不越过；审计例外后续再加）。
  - `feature`：跟随功能 ACL。
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
- `GET /{object_key}/records?q=&limit=&offset=` → 可见记录列表
- `POST /{object_key}/records` body `{values}` → 创建（`title` 自动取 `title_field`）
- `GET /records/{record_id}` → 记录详情 + 附件
- `PATCH /records/{record_id}` body `{values?, expected_revision}` → 更新字段
- `POST /records/{record_id}/publish` → private → feature
- `DELETE /records/{record_id}` → 软删除（创建人/管理者）
- `GET /records/{record_id}/history?limit=` → 变更历史
- `POST /records/{record_id}/files/{field_key}` multipart `file`, `note?` → 新版本
- `GET /records/{record_id}/files` → 附件及版本
- `GET /records/{record_id}/files/{file_id}/download` → 文件
- `GET /notifications?unread_only=` → 当前用户站内通知
- `POST /notifications/{id}/read` → 标记已读
- `POST /reminders/{id}/complete` → 完成提醒

## 提醒

- 记录创建/更新时，从 schema 的日期字段计算提醒行；`due_at` 为 ISO 日期。
- 每个 `(record_id, field_key, due_at)` 唯一；状态 `open|done|dismissed`。
- 站内通知：`feature_data_notifications`，每个提醒每个用户每个阈值一条，去重；`channel` 字段当前固定 `in_app`，后续邮件/IM 适配器复用同一提醒源。
- 第一阶段接收人：记录创建人 + 功能所有者（去重）；渠道先站内，预留 `channel` 字段。
- 定时扫描：每 1 分钟；到期前阈值 90/60/30 天；触发后写通知并推进 `next_notify_at`。
- 邮件/IM 渠道后续使用同一 reminder 源，不改变表结构。

## UI

功能详情“工作流”抽屉增加“业务数据”页签：

- 对象选择/记录列表/搜索
- 记录详情、字段编辑、保存
- 附件上传/版本列表/下载
- 变更历史
- 受控文档状态/版本显示
- 提醒/通知列表与完成
- 管理者通过表单式“数据结构”设计器维护业务对象和字段，不需要手写 JSON；服务端仍负责字段名校验和受控文件规则

## Agent 工具

`feature_data_list`、`feature_data_get`、`feature_data_create`、`feature_data_update`、`feature_data_history`，均以当前 agent 的功能身份和调用者权限执行；不暴露其他功能数据。
