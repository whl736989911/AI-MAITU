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
| `agent` | 否 | 功能自有 agent 的能力层：`model`/温度/token 上限/`tools_disabled`/`skills`/`subagents`/`mcp_servers`/`knowledge_base_ids`/`max_parallel`。缺省（或 `null`）继承调用者的 agent，空数组表示"一个都不要"；知识库与连接器再按调用者可见范围收窄。`max_parallel` 是本功能各步骤的**默认并发上限**（7.7）：步骤自己声明了 `max_parallel` 就用步骤的，否则用它，都没有时用平台默认值（当前 4）。注意 `subagents: []`（显式"一个子 agent 都不要"）与必须派发的步骤不能共存，保存时会被拒 |
| `steps` | 否 | array | 任务步骤，见下文。缺省或空数组 = 单次运行（只用 `prompt.user_template` 跑一轮） |

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

## 任务步骤 `steps`

一个功能要么单次运行，要么声明 `steps`：**按顺序执行的步骤**，每一步产出**一个带类型的产品**（artifact），
后一步直接读前一步的**结构化数据**，而不是读它写的那段话。步骤之间可以插**人工门**和**校验门**。

```jsonc
"steps": [
  {
    "id": "extract_l1",                       // 小写标识符，功能内唯一
    "name": "提取 L1 项",
    "mode": "agent",                          // 只支持 agent
    "inputs": [],                             // 前序步骤产出的 artifact 名
    "tools": ["read_file"],                   // 本步工具白名单；缺省=继承，[]=一个工具都不给
    "max_parallel": 4,                        // 7.7 的并发上限：同一时刻最多几个子 agent 在跑
    "output": { "name": "bom_rows", "schema": "table:4cols" },
    "prompt": "读 BOM PDF，识别层级列，过滤 L1。",  // 同样支持 {{inputs}} / {{inputs_json}}
    "gate": "auto",                           // auto | confirm | validate
    "on_failure": "abort"                     // abort | escalate | retry
  },
  {
    "id": "report",
    "name": "摘要报告",
    "mode": "agent",
    "inputs": ["bom_rows"],
    "output": { "name": "summary", "schema": "object" },
    "prompt": "按材质推导 Coating，输出待确认清单。",
    "gate": "confirm",
    "allow_edit": true,
    "on_failure": "escalate"
  }
]
```

| 字段 | 必填 | 说明 |
|---|---|---|
| `id` | 是 | 步骤 id：小写标识符（`^[a-z][a-z0-9_]{0,63}$`），功能内唯一 |
| `name` | 是 | 步骤名，展示用 |
| `mode` | 是 | `agent` = 这一步由一次 agent 回合完成（一步一个 agent）；`orchestrate` = 拆解由模型自己决定（7.6）：要不要拆、拆几块、先跑哪块都归模型，每一块用 `task` 工具派给一个子 agent。平台只做两件事——用 `max_parallel` 限制**同时**在跑的子 agent 数，并把派出去了什么记进审计（7.8 分解留痕） |
| `inputs` | 否 | 要读的 artifact 名。必须是**更早的步骤**产出的名字（写错会在保存时被拒），它们以 JSON 形式进入本步提示词 |
| `tools` | 否 | 本步工具白名单。缺省 = 继承运行的工具面；`[]` = 本步一个工具都不用。名字必须是真实的内置工具名。声明了 `orchestrate` / `agent_role` 的步骤必须把派发工具 `task` 留在白名单里，否则运行会被明确拒绝（`FEATURE_STEP_UNSUPPORTED`） |
| `max_parallel` | 否 | 正整数，7.7 的并发上限：**同一时刻**最多几个子 agent 在跑。超过上限的 `task` 调用会等一个空位，总共派出的数量不受这个数限制。缺省继承 `agent.max_parallel`，两者都没有时用平台默认值（当前 4） |
| `output` | 是 | `{"name": …, "schema": …}`，见下 |
| `prompt` | 是 | 本步提示词；`{{inputs}}` / `{{inputs_json}}` 渲染运行表单的值（与 `prompt.user_template` 同一套占位符） |
| `gate` | 是 | `auto`（跑完继续）/ `confirm`（停下来等人批准）/ `validate`（读产物里的布尔 `passed`，不为真就不许交付） |
| `allow_edit` | 否 | 人工能否改这一步的产物（默认否）。在 gate 上批准时可以改；回退重跑时也可以把它当输入改掉——**两种改法都进审计** |
| `on_failure` | 是 | `abort`（这次运行算失败）/ `escalate`（**停给人工决定，平台不擅自决定**）/ `retry`（再试一次，仍失败则按 abort） |
| `agent_role` | 否 | 这一步以哪个子 agent 的身份运行：本步回合必须用 `task` 工具把这个角色派出去（`subagent_type` = 这个名字），**一回合下来没派出这个角色就算这一步失败**。拆解式步骤里它只是其中一块，其它块照常派 |

### `output.schema` 允许的产物类型

| 写法 | 产物 |
|---|---|
| `text` | 纯文本（原样） |
| `list` | JSON 数组 |
| `object` | JSON 对象 |
| `table` | JSON 二维数组（每行等长；也可以是等键的对象数组） |
| `table:<N>cols` | 同上，且每行恰好 N 个单元格（例如 `table:12cols`） |

引擎会**从模型回复里取出 JSON 并校验形状**：形状不对（比如声明 4 列却给了 3 列）就算这一步失败，
按 `on_failure` 处理——**不会把一段文字当成表格传给下一步**。

### 运行与重跑

- `POST /api/features/{id}/run`：有 `steps` 的定义从这里开始跑，停在门或跑完为止，返回运行状态。
- `GET /api/features/{id}/runs/{task_id}`：这次运行停在哪一步、每步状态与产物、在等哪个门。
- `POST .../runs/{task_id}/approve`：**批准后继续同一个运行**（不是新起一次），可带 `edits` 改产物。
- `POST .../runs/{task_id}/rewind`：`{"to_step": "<步骤 id>", "edits": {...}}`。不带 `edits` 是**回退重跑**
  （作废该步及之后的全部产物，从那里重跑）；带 `edits` 是**带修正重跑**（人工改过的中间产物被注入，
  改前改后都记进审计）。
- `GET .../runs/{task_id}/audit`：每步的输入/产物，以及**人工改过什么**（含改前改后、谁改的、什么时候）。

运行状态是**持久化**的：门停下来之后，运行不会因为请求结束而消失，批准时按数据库里的状态继续
（带着前面各步已经产出的产物），不会重跑已经跑过的步骤。

## 排错

单个 `feature.json` 非法（JSON 语法错误、缺字段、用了不允许的 schema 关键字、`system_file` 指向的文件不存在……）只会**跳过该功能并记一条 warning**，不会影响其它功能和服务启动：

```python
from octop.infra.features import FeatureCatalog

catalog = FeatureCatalog()
catalog.list()      # 只有合法功能
catalog.warnings()  # ["my-feature/feature.json: input_schema.type must be 'object'", …]
```

`feature_json_schema()` 返回定义格式自身的 JSON Schema（draft 2020-12），可以喂给编辑器和离线校验工具。
