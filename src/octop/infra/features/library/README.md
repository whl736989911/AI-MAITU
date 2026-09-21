# 企业功能目录

一个功能 = 一个目录，目录里放一份声明式的 `feature.json`（可选再加一份 `PROMPT.md` 作为系统提示词）。
功能定义随仓库走：给某个企业客户定制功能时，加一个目录即可，不需要改数据库、不需要写迁移。

```
library/
  meeting-notes/          # 目录名必须等于 feature.json 里的 id
    feature.json          # 功能定义（必填）
    PROMPT.md             # 系统提示词（prompt.system_file 指向它）
  quote-draft/
    feature.json
    PROMPT.md
```

本目录随应用发布，**只读**：里面的定义由版本控制，升级时整体替换。
管理员在设置界面里新建/修改的功能写到用户目录 `~/.octop/features/`，扫描时**排在
本目录之后**——同名 id 以用户目录为准。所以预置功能被某个部署改过之后，改动会一直生效，
不会被下一次升级悄悄改回原样；而包内的 `feature.json` 始终是仓库里的那一份，界面无法
改写它（对预置功能点保存会被拒绝，不会在用户目录里偷偷生成副本）。

新增功能：

1. `mkdir library/<功能 id>`，目录名用小写短横线命名。
2. 写 `feature.json`（字段见下）。
3. 写 `PROMPT.md`（可选，但在 `prompt.system_file` 里声明了就必须存在）。
4. 验证：用 Python 直接跑一次校验，确认没有错误信息：

   ```python
   import json
   from pathlib import Path
   from octop.infra.features import validate_manifest

   d = Path("src/octop/infra/features/library/my-feature")
   print(validate_manifest(json.loads((d / "feature.json").read_text(encoding="utf-8")), d.name))
   # 输出 [] 表示合法
   ```

5. 运行中的服务调一次 `FeatureCatalog.reload()` 即可重新扫盘（定义在启动时懒加载并缓存）。

## feature.json 字段

| 字段 | 必填 | 类型 | 说明 |
|---|---|---|---|
| `id` | 是 | string | 功能 id，**必须等于目录名** |
| `version` | 是 | integer | 定义格式版本，目前只支持 `1` |
| `label` | 是 | `{"zh": …, "en": …}` | 双语名称，两个键都必须非空 |
| `description` | 是 | `{"zh": …, "en": …}` | 双语说明，两个键都必须非空 |
| `icon_name` | 是 | string | [lucide](https://lucide.dev/icons/) 图标名，例如 `file-text` |
| `color` | 否 | string | 强调色；缺省用品牌色 |
| `unit` | 是 | string | 组织单元键，功能列表按它分组 |
| `input_schema` | 是 | object | 运行表单的字段定义，只允许下面的子集 |
| `ui_schema` | 否 | object | `order`：字段顺序；`widgets`：按字段覆盖控件（如 `textarea`） |
| `prompt` | 是 | object | `user_template`（必填）、`system_file`（可选，相对本目录） |
| `output` | 是 | object | `kind`：`markdown` / `json` / `text` |
| `permissions` | 否 | object | `allow_units` / `allow_roles` 字符串数组；M1 只存不判 |

## input_schema 允许的子集

前端表单完全由 `input_schema` 渲染，所以只接受下列写法，**出现其它关键字或类型会被判为非法定义**：

- 根节点：`{"type": "object", "properties": {…}, "required": […]}`，`properties` 不能为空。
- 字段类型 `type`：`string` / `number` / `integer` / `boolean` / `array`（`object` 只能做根）。
- `title` / `description`：双语对象，不是字符串 —— `{"zh": "客户名称", "en": "Customer"}`。
- `format`：`textarea` / `date` / `email`。
- `enum`：字符串数组，例如 `["CNY", "USD", "EUR"]`。
- `items`：只在 `type` 为 `array` 时使用，元素同样只能是上面这些类型（数组可以嵌套，用来做字符串网格/子表，但元素不能是对象）。
- `required` 里的字段名必须真实存在于 `properties`。

示例（数组子表 + 枚举 + 日期）：

```jsonc
{
  "type": "object",
  "required": ["currency", "valid_until", "line_items"],
  "properties": {
    "currency": { "type": "string", "enum": ["CNY", "USD"], "title": { "zh": "币种", "en": "Currency" } },
    "valid_until": { "type": "string", "format": "date", "title": { "zh": "有效期至", "en": "Valid until" } },
    "line_items": {
      "type": "array",
      "title": { "zh": "报价明细", "en": "Line items" },
      "items": { "type": "array", "items": { "type": "string" } }   // 一行一项：名称 | 数量 | 单价
    }
  }
}
```

## prompt.user_template 占位符

| 占位符 | 替换成 |
|---|---|
| `{{inputs}}` | 人类可读的输入清单（按 `ui_schema.order` 排序，用双语 `title` 当标签：值里有中文用中文标签，否则用英文标签） |
| `{{inputs_json}}` | 输入的原始 JSON |

其余 `{{…}}` 原样保留，不会报错。空值和未填写的字段会被省略。

## 排错

单个 `feature.json` 非法（JSON 语法错误、缺字段、用了不允许的 schema 关键字、`system_file` 指向的文件不存在……）只会**跳过该功能并记一条 warning**，不会影响其它功能和服务启动：

```python
from octop.infra.features import FeatureCatalog

catalog = FeatureCatalog()
catalog.list()      # 只有合法功能
catalog.warnings()  # ["my-feature/feature.json: input_schema.type must be 'object'", …]
```

`feature_json_schema()` 返回定义格式自身的 JSON Schema（draft 2020-12），可以喂给编辑器和离线校验工具。
