# 功能业务数据配置样例

这两个对象使用同一套通用引擎，不包含供应商或受控文件的专用代码。

## 供应商台账

```json
{
  "version": 1,
  "objects": [
    {
      "key": "supplier",
      "name": {"zh": "供应商", "en": "Supplier"},
      "kind": "ledger",
      "title_field": "name",
      "fields": [
        {"key": "name", "label": {"zh": "名称", "en": "Name"}, "type": "text", "required": true},
        {"key": "scope", "label": {"zh": "批准范围", "en": "Approved scope"}, "type": "textarea"},
        {"key": "contact", "label": {"zh": "联系人", "en": "Contact"}, "type": "text"},
        {"key": "phone", "label": {"zh": "联系方式", "en": "Phone"}, "type": "text"},
        {"key": "status", "label": {"zh": "状态", "en": "Status"}, "type": "enum", "options": ["approved", "conditional", "suspended", "expired"]},
        {"key": "expires_at", "label": {"zh": "资质到期日", "en": "Qualification expires"}, "type": "date", "reminder_days": [90, 60, 30]},
        {"key": "license", "label": {"zh": "营业执照", "en": "Business license"}, "type": "file"},
        {"key": "certificate", "label": {"zh": "体系证书", "en": "System certificate"}, "type": "file"},
        {"key": "annual_review", "label": {"zh": "年审复审资料", "en": "Annual review"}, "type": "file"}
      ]
    }
  ]
}
```

每个文件字段独立维护版本历史；到期提醒在记录创建/更新时生成，站内通知由后台扫描产生。

## 通用计算日期与引用字段

以下字段声明仍由服务端按字段类型与当前权限校验；示例中的字段名不是内置业务对象。计算日期由服务端从来源日期及整数周期生成，客户端不能在创建或更新时直接填写 `renewal_due`：

```json
{
  "key": "renewal_due",
  "type": "date",
  "computed": {
    "op": "add_months",
    "source_field": "issued_on",
    "amount_field": "cycle_months"
  }
}
```

也可将 `amount_field` 换成整数 `amount`，运算仅允许 `add_days`、`add_months`、`add_years`。按月或按年计算遇到目标月份没有对应日期时，日期夹取到该月最后一天；缺少来源或周期值时不生成派生日期。

```json
[
  {
    "key": "related_record",
    "type": "record_ref",
    "target_object": "supplier",
    "target_agent_id": 42
  },
  {
    "key": "source_document",
    "type": "knowledge_ref"
  }
]
```

`target_agent_id` 可省略以引用当前功能；记录引用必须是当前调用者可读的目标记录。知识引用值包含 `kb_id`、`document_id`，可选 `version`（原件 SHA-256）、`page` 和 `layout`。省略版本时创建记录固定当时的原件版本；之后只解析当前原件及当前 ACL，不承诺保留历史原始字节。撤销目标权限后，引用详情和历史值会脱敏。

对象还可用 `reminder_recipients` 指定动态收件来源，例如：

```json
{
  "reminder_recipients": {
    "creator": true,
    "responsible": true,
    "user_fields": ["reviewer"],
    "unit_fields": ["review_unit"],
    "role_keys": ["unit_admin"]
  }
}
```

角色只接受既有 `admin`、`enterprise_admin`、`unit_admin`、`user` 键。单位包括其子单位；发送时仅选择有效且当前可读该功能及记录的用户。未配置时保留默认创建人和功能所有者收件人。

## 受控文件

```json
{
  "version": 1,
  "objects": [
    {
      "key": "controlled_document",
      "name": {"zh": "受控文件", "en": "Controlled document"},
      "kind": "document",
      "title_field": "title",
      "controlled": true,
      "fields": [
        {"key": "doc_no", "label": {"zh": "文件编号", "en": "Document no."}, "type": "text", "required": true},
        {"key": "title", "label": {"zh": "文件名称", "en": "Title"}, "type": "text", "required": true},
        {"key": "version", "label": {"zh": "版本", "en": "Version"}, "type": "text", "required": true},
        {"key": "status", "label": {"zh": "状态", "en": "Status"}, "type": "enum", "options": ["draft", "approved", "effective", "obsolete"]},
        {"key": "effective_at", "label": {"zh": "生效日期", "en": "Effective date"}, "type": "date"},
        {"key": "revision_reason", "label": {"zh": "修订原因", "en": "Revision reason"}, "type": "textarea"},
        {"key": "approved_by", "label": {"zh": "审批人", "en": "Approved by"}, "type": "text"},
        {"key": "file", "label": {"zh": "文件正文", "en": "Document file"}, "type": "file"}
      ]
    }
  ]
}
```

`controlled=true` 要求 `kind=document` 且至少有一个文件字段。当前 MVP 记录状态和修订信息，文件每次上传形成新版本；完整审批流、分发签收和电子签名属于下一阶段。
