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
