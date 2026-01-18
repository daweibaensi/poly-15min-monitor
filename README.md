# Polymarket 15min Top Holders Live Dashboard

实时监控 Polymarket BTC/ETH/XRP/SOL 当前 15 分钟 up-down 市场的 Top Holders 持仓情况、鲸鱼大额异动及集中度风险预警。

专为高赔率 15min 量化策略设计，结合 Telegram 推送实现手机秒级警报。

## 功能亮点

- **实时柱状图**：UP（绿色）/ DOWN（红色）持仓 Top N 名
- **大额持仓高亮**：> `LARGE_POSITION_THRESHOLD` shares 深色突出
- **集中度警告**：单个地址 > `CONCENTRATION_THRESHOLD` shares → 橙色警报
- **大额异动警报**：单地址 Δ > `DELTA_THRESHOLD` shares → 红/绿文字 + Telegram 推送
  - UP 加仓：📈 深绿
  - UP 减仓：📉 浅绿
  - DOWN 加仓：📉 深红
  - DOWN 减仓：📈 浅红
- **净持仓指标**：UP - DOWN 总量及百分比（绿正红负）
- **用户名显示**：优先 name > pseudonym > 地址后8位，超长截断 + hover 完整
- **所有阈值热配置**：通过 `.env` 动态调整，无需改代码
- **多端推送**：支持多个 Telegram 私聊/群组（逗号分隔）

## 快速启动（本地）

```bash
# 进入项目目录
cd poly-15min-monitor

# 激活虚拟环境（如果有）
venv\Scripts\activate

# 安装依赖（第一次运行）
pip install -r requirements.txt

# 启动
python dash_top_holders.py
浏览器打开 http://127.0.0.1:8050/
配置（全部在 .env 文件）
env# 刷新间隔（秒）
QUERY_INTERVAL_SECONDS=45

# 显示前多少名（API 最大20）
TOP_LIMIT=12

# 最小持仓过滤
MIN_BALANCE=50

# 用户名显示最大长度（超长截断）
USERNAME_MAX_LEN=15

# 大额持仓高亮阈值（柱子变深色）
LARGE_POSITION_THRESHOLD=10000

# 集中度警告阈值（单个地址 > 此值显示橙色警告）
CONCENTRATION_THRESHOLD=30000

# 大额异动阈值（单地址变化 > 此值显示红/绿警报）
DELTA_THRESHOLD=1000

# Telegram 推送（必填，支持多个 chat_id，用逗号分隔）
TELEGRAM_TOKEN=你的机器人token
TELEGRAM_CHAT_ID=私聊ID1,-100群ID2,-100群ID3
注意：.env 包含敏感信息，永不上 GitHub（已通过 .gitignore 保护）
如何获取 Telegram Token 和 Chat ID
1. 获取 Telegram Bot Token（机器人令牌）

在 Telegram 搜索并打开 @BotFather（官方机器人）
发送 /newbot 命令
按提示输入 Bot 名称（比如 POLYmarket15minBot）
输入 Bot 用户名（必须以 _bot 结尾，比如 poly_15min_monitor_bot）
BotFather 会立即回复你一条消息，里面有：textUse this token to access the HTTP API:~~~~~~~~~复制这个长字符串，就是你的 TELEGRAM_TOKEN

注意：Token 非常敏感，任何人拿到都能控制你的 Bot，不要泄露！
2. 获取 Chat ID（接收消息的 ID）
方法 A：私聊（发给自己）

打开你刚创建的 Bot（t.me/你的bot名）
给它发任意消息（比如 /start 或 “hello”）
在浏览器打开下面链接（把 token 替换进去）：texthttps://api.telegram.org/bot<你的TOKEN>/getUpdates
页面返回 JSON，找到类似下面部分：JSON"message": {
  "message_id": xxx,
  "from": {
    "id": 123456789,   ← 这就是你的私聊 Chat ID
    ...
  },
  "chat": {
    "id": 123456789,   ← 重复确认
    ...
  }
}→ 复制这个数字（通常 9-10 位正数），填到 .env 的 TELEGRAM_CHAT_ID

方法 B：群组（发到群里）

创建一个 Telegram 群组（或用现有群）
把你的 Bot 加进群（搜索 bot 名加为成员，最好设为管理员）
在群里发任意消息
再次打开 getUpdates 链接
找到群的 chat id（格式为 -100 开头，比如 -1001234567890）→ 把群 ID 加到 TELEGRAM_CHAT_ID，用逗号分隔：textTELEGRAM_CHAT_ID=123456789,-1001234567890,-1009876543210

小技巧：getUpdates 链接可以多打开几次（发消息后刷新），确保抓到最新记录。

免责声明
本项目仅用于个人监控与学习，不构成任何投资建议。Polymarket 数据可能有延迟，Telegram 推送依赖网络稳定性，使用风险自负。
作者：cwddjb .NYAN 💤 🦖 (@Davidchanghai)
创建时间：2026 年 1 月
欢迎 PR 或 issue 交流优化！