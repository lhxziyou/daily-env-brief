# 每日环保简报

双线运行的每日环保资讯简报系统：
- **云线**：GitHub Actions + PushPlus（24h 在线，关电脑也推）
- **本地线**：WorkBuddy 自动化 + WxPusher/AI 增强（由 AI 补全云线抓不到的源）

两条线共用同一套 `render_brief.py` 渲染/推送引擎，任意修正自动同步。

## 目录结构

```
.
├── brief_crawl.py              # 云线纯脚本抓取（生态环境部/福建省厅/福建司法厅）
├── render_brief.py             # 渲染 + 双通道推送引擎（云/本地共用）
├── effective_calendar.json     # 「今日正式实施」权威日历（两线共用）
├── refresh_calendar.py         # 从法规详情页自动抽取施行日期补全日历
├── quotes.json                 # 每日金句库
├── wxpusher_config.json        # 本地 WxPusher 配置（勿入库）
├── pushplus_config.json        # 本地 PushPlus 配置（勿入库）
├── brief_data.json             # 本地线数据（AI/WebSearch 生成）
├── brief_data_cloud.json       # 云线数据（brief_crawl.py 生成）
├── .github/workflows/daily.yml # GitHub Actions 工作流
├── 云端部署实施计划.md         # 详细部署与运维说明
└── 汇总.csv / history/          # 去重与汇总记录
```

## 快速开始（本地测试）

```bash
# 1. 安装依赖
pip install -r requirements.txt
playwright install chromium

# 2. 配置 WxPusher（本地线）
# 编辑 wxpusher_config.json：
# {
#   "app_token": "AT_xxx",
#   "topic_id": "46054",
#   "uid": ""
# }

# 3. 生成简报并推送
python render_brief.py

# 4. 仅生成海报不推送
SKIP_PNG=0 PUSH_CHANNELS="" python render_brief.py
```

## GitHub Actions 云线部署

1. Fork/创建 GitHub 仓库
2. 在 Settings → Secrets and variables → Actions 中添加：
   - `PUSHPLUS_TOKEN`：PushPlus Token
   - `WX_PUSHER_APP_TOKEN`（可选）：WxPusher AppToken
   - `WX_PUSHER_TOPIC_ID`（可选）：WxPusher TopicId
3. 工作流 `.github/workflows/daily.yml` 已配置：
   - 每日 UTC 00:30（北京时间 08:30）运行
   - 安装依赖 + Playwright Chromium
   - 抓取 → 渲染 → PushPlus 推送

## 今日实施日历维护

`effective_calendar.json` 为权威来源。人工确知法规/标准实施日期的，添加 `curated: true` 条目；`refresh_calendar.py` 会自动从详情页抽取施行日期补充 `auto` 条目，不会覆盖 `curated`。

## 注意事项

- 云端不生成 PNG 海报（`SKIP_PNG=1`），只推送可点击 HTML；海报在本地按需生成。
- 两条线数据文件分离：`DATA_PATH=brief_data_cloud.json`（云线） vs 默认 `brief_data.json`（本地线），避免互相覆盖。
- 暂停≠删除：任一生产线暂停时只禁用 workflow/自动化，不删文件，随时可恢复。
