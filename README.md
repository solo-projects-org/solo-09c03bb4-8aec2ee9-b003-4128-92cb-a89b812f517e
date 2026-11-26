# 公交排班结构准点率差核算服务

用 FastAPI 实现的精确核算接口：给定线路类别（行）、时段（列）的合计、少数已知格班次数，以及基期/本期各格的实测班次和准点班次，枚举/优化所有符合排班记录的非负整数排班表，求：

\[
\Delta_{\min}=\min_X \sum_{i,j}\frac{x_{ij}}{S}
\left(\frac{O^{(1)}_{ij}}{N^{(1)}_{ij}}-\frac{O^{(0)}_{ij}}{N^{(0)}_{ij}}\right)
\]

\[
\Delta_{\max}=\max_X \sum_{i,j}\frac{x_{ij}}{S}
\left(\frac{O^{(1)}_{ij}}{N^{(1)}_{ij}}-\frac{O^{(0)}_{ij}}{N^{(0)}_{ij}}\right)
\]

其中 \(S=\sum_i R_i=\sum_j C_j\) 为标准排班总数，范围 \(1\sim100000\)；行、列数均为 \(1\sim6\)。

所有分数均使用 Python `Fraction` 精确计算，不经过浮点优化。极值由精确分数费用流求出；运输网络具有整数性，因此返回的见证表一定是非负整数表。

## 月末批量试算的单次求解优化

旧流程为找出“该补报实测的格子”，会对每个未知格各跑一次“强制至少 1 班”的排班可行性最大流（最多 `m×n` 次/请求），月末批量试算时反复逐格试排、成本很高。现在每次请求 **最多只做一次排班可行性求解**，流程为：

1. 先做纯算术检查（行/列合计、申报总数、已知格是否超额）；算术已失败时直接按冲突退回，**0 次**可行性求解。
2. 对残量运输网络构造并求解 **一次** Dinic 最大流：最大流达不到残量总和即无可行表，照旧返回 HTTP `409` 冲突。
3. 同一张残量图上，用残量可达性一次性读出“能在至少一张可行表中排到班次”的全部格：格边已有正流量，或其列节点可沿残量边到达其行节点（对应一次整数增广圈调整），二者等价于该格在某张可行表中为正——不再逐格发起可行性求解。
4. 这些格中只要有任一格在基期或本期缺实测（实测为 `null`/`0`），就 **一次性列齐全部缺报格并以 HTTP `422` 退回，不启动率差取界**（本次仅 1 次可行性求解、0 次费用流）。
5. 实测足够出数时，把已确定排不到班（在所有可行表中恒为 0）的未知格从上下界费用流网络中剔除再取界；由于这些格对目标函数贡献恒为 0，最小/最大值与全部达界见证表均保持精确，响应格式与状态码不变。

实现中保留 `app.core.SOLVER_STATS`（含 `feasibility_solves`、`min_cost_solves` 计数）以及 `get_solver_stats()` / `reset_solver_stats()`，用于自测核对求解次数。各路径的实测计数见文末“实现说明”。

## 启动

### Docker Compose

```bash
docker compose up --build
```

服务地址：<http://127.0.0.1:8000>

- 交互式文档：<http://127.0.0.1:8000/docs>
- 健康检查：<http://127.0.0.1:8000/health>
- 主接口：`POST http://127.0.0.1:8000/api/v1/assessments`

兼容别名：`/api/v1/evaluate`、`/api/evaluate`、`/evaluate`、`/assess`、`/calculate`。

也可以直接运行包含正常取界、100000 班边界、缺实测退回和冲突退回的脚本：

```bash
./examples/curl_examples.sh
# 或指定其他地址：BASE_URL=http://127.0.0.1:18080 ./examples/curl_examples.sh
```

### 本地 Uvicorn

```bash
python -m pip install -r requirements.txt
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

## 请求格式

完整示例见 [`examples/request.json`](examples/request.json)：

```bash
curl -sS -X POST http://127.0.0.1:8000/api/v1/assessments \
  -H 'Content-Type: application/json' \
  --data-binary @examples/request.json
```

主要字段：

| 字段 | 说明 |
| --- | --- |
| `row_totals` | 各行（线路类别）合计，非负整数，长度 ≤ 6 |
| `column_totals` | 各列（时段）合计，非负整数，长度 ≤ 6 |
| `row_labels` / `column_labels` | 可选标签，仅用于回显和问题定位 |
| `total_shifts` | 可选；若提供，必须等于行/列合计之和，且在 1～100000 |
| `fixed_cells` | 少数已知格：`{"row": 0, "column": 1, "shifts": 2}` |
| `baseline` / `current` | 基期/本期数据 |
| `*.measured_shifts` | 各格实测班次矩阵；`0` 表示该期该格无实测班次 |
| `*.on_time_shifts` | 各格准点班次矩阵，必须满足 `0 <= on_time <= measured` |

坐标约定：JSON 中的 `row`、`column`、`row_index`、`column_index` 默认是 **0 基下标**；`row_number`、`column_number` 为 **1 基编号**。如果调用方希望把普通 `row`/`column` 按 1 基解析，可在请求顶层加 `"index_base": 1` 或 `"one_based": true`；显式 `row_index` 仍保持 0 基。也支持 `row_label` / `route_category` / `time_period` 传标签。

如果排班记录是“矩阵中少数格有数字、其余格为未知”，可改用 `schedule_matrix`（或 `fixed_matrix`），未知格填 `null`。矩阵中的 `0` 表示该格被明确固定为 0。

服务还兼容 `row_sums`、`col_totals`、`known_cells`、`actual_shifts`、`punctual_shifts`、`base`、`previous`、`report` 等常见别名字段。

## 试算取界示例

[`examples/request.json`](examples/request.json) 的行合计 `[2,2,2]`、列合计 `[3,3]`，已知第 1 行第 2 列为 2 班。可行表只有两张：

```text
最小值见证表       最大值见证表
[[0,2],            [[0,2],
 [1,1],             [2,0],
 [2,0]]             [1,1]]
```

实际响应中的关键字段为：

```json
{
  "status": "ok",
  "total_scheduled_shifts": 6,
  "minimum": {
    "fraction": "1/12",
    "difference": {"numerator": 1, "denominator": 12, "fraction": "1/12"},
    "schedule": [[0, 2], [1, 1], [2, 0]]
  },
  "maximum": {
    "fraction": "1/4",
    "difference": {"numerator": 1, "denominator": 4, "fraction": "1/4"},
    "schedule": [[0, 2], [2, 0], [1, 1]]
  }
}
```

每个极值对象还附带该见证表对应的两期加权准点率：

- 最小值表：基期 `1/2`，本期 `7/12`，差 `1/12`
- 最大值表：基期 `1/2`，本期 `3/4`，差 `1/4`

### 100000 班边界

```bash
curl -sS -X POST http://127.0.0.1:8000/evaluate \
  -H 'Content-Type: application/json' \
  --data-binary @examples/request_boundary.json
```

该例只有一个格且固定为 `100000` 班，结果唯一：

```text
minimum = maximum = (6/7 - 1/3) = 11/21
schedule = [[100000]]
```

## 退回场景

### 可能排班为正但某期无实测班次

接口先对每个未知格做“强制至少 1 班”的可行性检查，因此列出的是 **所有可能在某张可行表中大于 0 的问题格**，而不只是某一张见证表里的正格。

```bash
curl -i -X POST http://127.0.0.1:8000/api/v1/assessments \
  -H 'Content-Type: application/json' \
  --data-binary @examples/request_blocked.json
```

返回 HTTP `422`：

```json
{
  "status": "missing_measurements",
  "message": "存在可能排有班次但某一期没有实测班次的格；为避免无依据加权，本次不出数。",
  "blocked_cells": [
    {
      "row": 0,
      "column": 0,
      "row_number": 1,
      "column_number": 1,
      "missing_in": ["current"],
      "baseline_measured_shifts": 4,
      "current_measured_shifts": 0
    },
    {
      "row": 0,
      "column": 1,
      "row_number": 1,
      "column_number": 2,
      "missing_in": ["baseline"],
      "baseline_measured_shifts": 0,
      "current_measured_shifts": 5
    }
  ],
  "result_available": false
}
```

被排班记录明确固定为 0、或由行列合计强制为 0 的格，即使实测缺失也不会拦截核算。

### 月末批量：缺报退回与补报出数（配套请求文件）

为模拟考核组月末试排，仓库额外提供一对前后衔接的请求：

```bash
# 第一步：本期 (0,0) 格实测缺失 -> HTTP 422，列齐缺报格，不启动取界
curl -i -X POST http://127.0.0.1:8000/api/v1/assessments \
  -H 'Content-Type: application/json' \
  --data-binary @examples/request_missing_measurement.json

# 第二步：补报该格实测/准点班次后重试 -> HTTP 200，返回精确分数与见证表
curl -sS -X POST http://127.0.0.1:8000/api/v1/assessments \
  -H 'Content-Type: application/json' \
  --data-binary @examples/request_resupplied.json
```

第一步的退回体（保持既有格式）：

```json
{
  "status": "missing_measurements",
  "message": "存在可能排有班次但某一期没有实测班次的格；为避免无依据加权，本次不出数。",
  "blocked_cells": [
    {
      "row": 0,
      "column": 0,
      "row_number": 1,
      "column_number": 1,
      "row_label": "线路一",
      "column_label": "高峰",
      "missing_in": ["current"],
      "baseline_measured_shifts": 4,
      "current_measured_shifts": 0
    }
  ],
  "result_available": false
}
```

第二步补报后（2×2、行/列合计均为 `[2,2]`）精确取界为 `minimum = -1/4`、`maximum = 2/5`，见证表分别为 `[[2,0],[0,2]]` 与 `[[0,2],[2,0]]`，各对象仍带两期加权准点率字段。

### 排班记录冲突

```bash
curl -i -X POST http://127.0.0.1:8000/api/v1/assessments \
  -H 'Content-Type: application/json' \
  --data-binary @examples/request_conflict.json
```

行/列合计不一致、已知格超过行列合计、同一格的重复记录数值不同，或扣除已知格后残量运输网络不可行，均返回 HTTP `409`：

```json
{
  "status": "schedule_conflict",
  "message": "排班记录不一致，无法构造任何符合条件的排班表。",
  "conflicts": [
    {"code": "residual_transportation_infeasible"}
  ],
  "feasible": false
}
```

字段类型、矩阵维度、负数、`on_time > measured` 等请求格式/数值语义错误返回 HTTP `422`，`status` 为 `invalid_request`。

如果调用方希望业务退回也使用 HTTP 200，可加查询参数 `?always_200=true`，响应体中仍保留 `status`、`conflicts` 或 `blocked_cells`。

## 正确性核对

核心测试包含：

- 120 组随机小尺寸实例，与穷举所有非负整数排班表得到的最小/最大值逐例比较；
- 比对“可能为正”的格集合与穷举结果；另含对 1～3 维、合计 0～3 的数百种行列合计/固定格组合做掩码穷举核对（单次最大流 + 残量可达性）；
- 校验返回的每张见证表满足行列合计、固定格和非负整数约束；
- 用见证表重新计算两期加权率和差值，确认与返回分数完全相等；
- 1×1、全固定表、6×6、100000 班性能边界；
- 缺实测退回、结构冲突、非法准点数和 API 别名；
- 每次请求的求解次数断言：成功/缺报/结构冲突路径可行性求解均不超过 1 次，算术冲突 0 次；缺报路径不发生费用流取界；并核对“剔除恒 0 未知格”前后极值完全一致；
- 缺报退回 → 补报出数的端到端流程（含别名路径返回体一致）。

运行：

```bash
python -m unittest discover -s tests -v
```

API 测试需要 `fastapi`、`uvicorn`、`httpx`；可使用：

```bash
python -m pip install -r requirements-dev.txt
python -m unittest discover -s tests -v
```

## 实现说明 / 待补工作

- 可行性最大流与“某格能否为正”合并为一次求解：可行性由 Dinic 最大流判定，可能为正的格由同一张残量图上“格边有流量或列可残量到达行”读出（增广圈定理）。
- 极值使用精确分数费用的最短增广路最小费用流；实测足够出数后，恒为 0 的未知格不进入费用流网络，行列最多各 6、总班次最多 100000 时，本地随机压测单例约在十几毫秒内。
- 求解次数自测（`app.core.SOLVER_STATS`，计数包含可行性最大流与取界费用流）实测：

  | 请求路径 | feasibility_solves | min_cost_solves |
  | --- | --- | --- |
  | 正常出数（含 6×6/100000 边界） | 1 | 2 |
  | 缺实测退回 | 1 | 0 |
  | 结构（残量运输）冲突 | 1 | 0 |
  | 算术冲突（行列合计不符等） | 0 | 0 |
  | 全固定表（残量为 0） | 0 | 2 |

- 当前接口无鉴权、无持久化，适合内部核算或批处理服务；若上线到真实内网，可补鉴权、审计日志、请求大小限制和速率控制。
- 本仓库交付了 `docker compose.yml` 并校验了 YAML/代码与本地 Uvicorn/TestClient 实调；当前开发沙箱没有 Docker/Podman 可执行程序和容器 socket，因此未能在该沙箱内实际执行 `docker compose up --build`。在装有 Docker Compose 的环境中可直接运行上文命令。
