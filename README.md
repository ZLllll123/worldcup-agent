# FIFA 数据采集、转换与校验

这套脚本分成三个阶段：

1. `collect_fifa.py`：采集 FIFA 官方页面的原始快照。
2. `transform_fifa.py`：把快照转换为 `rankings.csv` 和 `matches.csv`。
3. `validate_fifa.py`：检查数据是否完整、字段是否合理、淘汰赛编号是否连续。

脚本不会进行预测，也不会向外部服务提交数据。

## 在 PyCharm 中直接运行

将这些文件放在同一个目录，例如：

```text
D:\python\pythonprogram\agent\
├── collect_fifa.py
├── transform_fifa.py
├── validate_fifa.py
└── requirements.txt
```

在 PyCharm 的 Python Interpreter 中选择已经安装 Playwright 的环境，例如：

```text
D:\python\pythonenvironment\envs\keyan\python.exe
```

然后依次右键运行，不需要填写任何 Program arguments：

1. 运行 `collect_fifa.py`；
2. 运行 `transform_fifa.py`，它会自动选择最新原始快照；
3. 运行 `validate_fifa.py`，它会自动选择最新转换结果。

所有路径以脚本所在目录为基准，不依赖 PyCharm 的 Working Directory。数据将写入：

```text
D:\python\pythonprogram\agent\data\raw\fifa
D:\python\pythonprogram\agent\data\processed\fifa
```

如果 PyCharm 报浏览器不存在，请在其 Terminal 中运行：

```powershell
python -m playwright install chromium
```

## 1. 安装环境

建议使用 Python 3.11 或 3.12：

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m playwright install chromium
```

如果使用 Conda，请先激活实际运行脚本的环境，再执行安装命令：

```powershell
conda activate keyan
python -m pip install -r requirements.txt
python -m playwright install chromium
```

## 2. 重新采集

```powershell
python collect_fifa.py
```

默认输出到：

```text
data/raw/fifa/<UTC时间>/
├── manifest.json
├── world_cup_schedule_results/
│   ├── page.html
│   ├── page.txt
│   ├── dom_records.json
│   └── metadata.json
└── mens_world_ranking/
    ├── page.html
    ├── page.txt
    ├── dom_records.json
    └── metadata.json
```

新版采集器会点击排名页的 `Show full rankings`，并把以下诊断信息写入排名页的 `metadata.json`：

```json
{
  "interaction": {
    "show_full_rankings_clicked": true,
    "ranking_table_rows_before_expand": 11,
    "ranking_table_rows_after_expand": 200
  }
}
```

具体行数可能变化，但 `after_expand` 应明显大于 11。如果点击失败，用可见浏览器排查：

```powershell
python collect_fifa.py --headed --settle-seconds 5
```

## 3. 转换为 CSV

将下面的时间戳替换成新快照目录名：

```powershell
python transform_fifa.py data/raw/fifa/20260708T045204Z
```

默认输出：

```text
data/processed/fifa/20260708T045204Z/
├── rankings.csv
├── matches.csv
└── processed_manifest.json
```

CSV 使用 UTF-8 with BOM，Windows Excel 可以直接打开。原始行、来源 URL、采集时间和官方排名更新时间会保留在 CSV 中，便于追溯。

也可以指定输出位置：

```powershell
python transform_fifa.py data/raw/fifa/20260708T045204Z `
  --output-dir data/processed/fifa/latest
```

## 4. 校验

```powershell
python validate_fifa.py data/processed/fifa/20260708T045204Z
```

成功时应显示：

```text
VALIDATION PASSED
- ranking rows: <至少48>
- match rows: 104
- knockout match numbers: 73-104 complete
```

校验器会检查：

- 排名至少包含 48 支球队，且排名和球队不重复；
- 世界杯共 104 场：72 场小组赛和 32 场淘汰赛；
- 淘汰赛编号完整覆盖 73–104；
- 已完赛比赛具有比分，未开赛比赛没有伪造比分；
- 点球大战比分完整；
- 半决赛、季军赛和决赛引用的上游比赛编号合理。

如果只得到 10 条排名，校验会失败并提示重新展开完整排名。不要跳过这个错误进入预测建模。

## 5. 建议的数据目录规则

- `data/raw` 永远保存原始快照，不手工修改。
- `data/processed` 只保存脚本生成的结构化数据。
- 每次采集使用新的 UTC 时间戳目录。
- 建模时明确选择一个快照，避免不同时间的数据混在一起。

运行前请确认数据源的服务条款和使用范围。采集器只访问两个公开页面、串行运行，并在来源之间等待一秒，不应改成高频抓取。
