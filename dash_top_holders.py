"""
Polymarket 15min Top Holders Live Dashboard (最终稳定版 - Telegram推送用户名+shares修复)
- APScheduler 后台定时执行 update_data()（不依赖浏览器）
- 前端 Interval 每 INTERVAL_SEC 秒刷新页面内容
- 时间显示 UTC+8 (Asia/Hong_Kong)
- Telegram 推送修复：用户名 + shares，不重复
- 支持多个 chat_id
"""

import logging
import re
import os
from datetime import datetime
from zoneinfo import ZoneInfo
import httpx
from dotenv import load_dotenv
import dash
from dash import dcc, html
from dash.dependencies import Input, Output
import plotly.graph_objects as go
import pandas as pd
import requests
from apscheduler.schedulers.background import BackgroundScheduler

load_dotenv()

INTERVAL_SEC = int(os.getenv("QUERY_INTERVAL_SECONDS", 45))
TOP_N = min(int(os.getenv("TOP_LIMIT", 12)), 20)
MIN_BALANCE = int(os.getenv("MIN_BALANCE", 50))
USERNAME_MAX_LEN = int(os.getenv("USERNAME_MAX_LEN", 15))

LARGE_POSITION_THRESHOLD = int(os.getenv("LARGE_POSITION_THRESHOLD", 10000))
CONCENTRATION_THRESHOLD = int(os.getenv("CONCENTRATION_THRESHOLD", 30000))
DELTA_THRESHOLD = int(os.getenv("DELTA_THRESHOLD", 1000))

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

COINS = ["BTC", "ETH", "XRP", "SOL"]
PREFIXES = {c: f"{c.lower()}-updown-15m-" for c in COINS}
PAGE_URLS = {
    "BTC": "https://polymarket.com/crypto/15M?coin=btc",
    "ETH": "https://polymarket.com/crypto/15M",
    "XRP": "https://polymarket.com/crypto/15M?coin=xrp",
    "SOL": "https://polymarket.com/crypto/15M?coin=sol",
}

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s | %(levelname)-5s | %(message)s"
)
logger = logging.getLogger(__name__)

current_data = {}
prev_data = {}

HK_TZ = ZoneInfo("Asia/Hong_Kong")


def fetch_holders(condition_id: str):
    params = {"market": condition_id, "limit": TOP_N, "minBalance": MIN_BALANCE}
    try:
        with httpx.Client(timeout=15.0) as client:
            r = client.get("https://data-api.polymarket.com/holders", params=params)
            r.raise_for_status()
            return r.json()
    except Exception as e:
        logger.error(f"holders API 失败 {condition_id}: {e}")
        return []


def get_current_market(coin: str):
    """
    用 Gamma API 获取最新活跃 15min 市场 slug 和 conditionId
    - 搜索 active=true + closed=false + slug_contains=prefix
    - 选 endTimeStamp > now 的最大值（最新市场）
    """
    prefix = PREFIXES[coin]
    params = {"active": "true", "closed": "false", "limit": 10, "slug_contains": prefix}
    try:
        r = httpx.get(
            "https://gamma-api.polymarket.com/markets", params=params, timeout=10
        )
        r.raise_for_status()
        data = r.json()
        if not data:
            logger.warning(f"{coin} 无活跃 15min 市场")
            return None, None

        now = int(datetime.now(HK_TZ).timestamp())
        active_markets = [m for m in data if int(m.get("endTimeStamp", 0)) > now]

        if not active_markets:
            logger.warning(f"{coin} 无未结束的活跃市场")
            return None, None

        # 选 endTimeStamp 最大的（最新）
        latest_market = max(active_markets, key=lambda m: int(m.get("endTimeStamp", 0)))
        slug = latest_market["slug"]
        cond_id = latest_market["conditionId"]
        logger.info(f"{coin} 最新市场: slug={slug}, condition_id={cond_id}")
        return slug, cond_id
    except Exception as e:
        logger.error(f"获取 {coin} 市场失败: {e}")
        return None, None


def update_data():
    global current_data, prev_data
    for coin in COINS:
        cond_id = get_current_market(coin)
        if not cond_id:
            continue

        try:
            holders_data = fetch_holders(cond_id)
            now_str = datetime.now(HK_TZ).strftime("%Y-%m-%d %H:%M:%S")

            up_holders = []
            down_holders = []
            for item in holders_data:
                holders = item.get("holders", [])
                if not holders:
                    continue
                outcome_idx = holders[0].get("outcomeIndex")
                if outcome_idx == 0:
                    up_holders = holders
                elif outcome_idx == 1:
                    down_holders = holders

            def make_df(holders_list):
                rows = []
                for h in holders_list:
                    full_name = (
                        h.get("name") or h.get("pseudonym") or h["proxyWallet"][-8:]
                    )
                    display_name = (
                        (full_name[:USERNAME_MAX_LEN] + "...")
                        if len(full_name) > USERNAME_MAX_LEN
                        else full_name
                    )

                    rows.append(
                        {
                            "user": display_name,
                            "full_user": full_name,
                            "address": h["proxyWallet"],
                            "shares": h["amount"],
                            "name": h.get("name", ""),
                            "pseudonym": h.get("pseudonym", ""),
                            "is_large": h["amount"] > LARGE_POSITION_THRESHOLD,
                        }
                    )
                return pd.DataFrame(rows).sort_values("shares", ascending=False)

            up_df = make_df(up_holders)
            down_df = make_df(down_holders)

            up_total = up_df["shares"].sum()
            down_total = down_df["shares"].sum()
            total_position = up_total + down_total
            net_position = up_total - down_total
            net_pct = (
                (net_position / total_position * 100) if total_position > 0 else 0.0
            )

            delta_warnings = []
            if coin in prev_data:
                for direction, df in [("UP", up_df), ("DOWN", down_df)]:
                    prev_df = prev_data[coin][direction.lower()]
                    merged = (
                        df.set_index("address")
                        .join(
                            prev_df.set_index("address"), rsuffix="_prev", how="outer"
                        )
                        .fillna(0)
                    )
                    merged["delta"] = merged["shares"] - merged["shares_prev"]
                    large_delta = merged[abs(merged["delta"]) > DELTA_THRESHOLD]
                    for addr, row in large_delta.iterrows():
                        delta_val = row["delta"]
                        sign = "+" if delta_val > 0 else "-"
                        username = row["full_user"]
                        delta_str = f"{direction} { '加仓' if delta_val > 0 else '减仓' } {username} ({sign}{abs(delta_val):,.0f} shares)"
                        delta_warnings.append(delta_str)

            has_concentration = any(
                df["shares"].max() > CONCENTRATION_THRESHOLD for df in [up_df, down_df]
            )

            current_data[coin] = {
                "up": up_df,
                "down": down_df,
                "timestamp": now_str,
                "slug": slug,
                "net_position": net_position,
                "net_pct": net_pct,
                "delta_warnings": delta_warnings,
                "has_concentration": has_concentration,
            }

            prev_data[coin] = {"up": up_df.copy(), "down": down_df.copy()}

            # Telegram 推送（修复用户名 + shares 显示，不重复）
            if TELEGRAM_TOKEN and TELEGRAM_CHAT_ID:
                chat_ids = [
                    cid.strip() for cid in TELEGRAM_CHAT_ID.split(",") if cid.strip()
                ]
                messages = []
                if has_concentration:
                    messages.append(
                        f"<b>⚠️ 集中度警告</b> {coin} 有地址持仓 > {CONCENTRATION_THRESHOLD} shares！"
                    )

                if delta_warnings:
                    messages.append(f"<b>🚨 大额异动 {coin} ({now_str})</b>：")
                    for w in delta_warnings:
                        if "UP" in w:
                            if "加仓" in w:
                                emoji = "📈"
                            else:
                                emoji = "📉"
                        else:
                            if "加仓" in w:
                                emoji = "📉"
                            else:
                                emoji = "📈"
                        messages.append(f"{emoji} {w}")

                if messages:
                    msg = "\n".join(messages)
                    for chat_id in chat_ids:
                        try:
                            response = requests.post(
                                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                                data={
                                    "chat_id": chat_id,
                                    "text": msg,
                                    "parse_mode": "HTML",
                                    "disable_web_page_preview": True,
                                },
                                timeout=10,
                            )
                            response.raise_for_status()
                            logger.info(f"Telegram 已推送 {coin} 警报 到 {chat_id}")
                        except Exception as e:
                            logger.error(f"推送到 {chat_id} 失败: {e}")

            logger.info(
                f"{coin} 更新完成: {now_str} | 净持仓 {net_position:+,.0f} ({net_pct:+.1f}%) | 异动: {len(delta_warnings)} 条 | 集中度警告: {has_concentration}"
            )
        except Exception as e:
            logger.error(f"{coin} 更新失败: {e}")


# 启动后台定时器
scheduler = BackgroundScheduler(timezone=ZoneInfo("Asia/Hong_Kong"))
scheduler.add_job(update_data, "interval", seconds=INTERVAL_SEC)
scheduler.start()

app = dash.Dash(
    __name__,
    external_stylesheets=[
        "https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css"
    ],
)

app.layout = html.Div(
    [
        # 右上角联系方式（浮动定位）
        html.Div(
            [
                html.H1(
                    "Polymarket 15min Top Holders Live Dashboard",
                    className="text-center mb-4",
                ),
                # 右上角联系框
                html.Div(
                    [
                        html.A(
                            "更多赚钱攻略： @poly_make_money",  # 显示的文字（children）
                            href="https://x.com/poly_make_money",  # 跳转链接
                            target="_blank",  # 在新标签页打开（推荐）
                            style={
                                "color": "#1DA1F2",
                                "fontSize": "20px",  # 改字体大小
                                "fontWeight": "bold",
                            },  # 自定义样式
                        ),
                    ],
                    style={
                        "position": "absolute",
                        "top": "30px",
                        "right": "30px",
                        "zIndex": 999,
                        "background": "rgba(255, 255, 255, 0.95)",
                        "padding": "8px 16px",
                        "borderRadius": "8px",
                        "boxShadow": "0 4px 12px rgba(0,0,0,0.15)",
                        "fontSize": "14px",
                        "color": "#444",
                        "whiteSpace": "nowrap",
                    },
                ),
            ],
            style={"position": "relative", "marginBottom": "20px"},
        ),  # 父容器相对定位
        html.Hr(),
        dcc.Interval(
            id="refresh-interval", interval=INTERVAL_SEC * 1000, n_intervals=0
        ),
        html.Div(id="dashboard-content", className="container"),
    ]
)


@app.callback(
    Output("dashboard-content", "children"), Input("refresh-interval", "n_intervals")
)
def render_dashboard(n):
    children = []
    for coin in COINS:
        if coin not in current_data:
            children.append(
                html.Div(f"{coin}: 无数据", className="alert alert-warning")
            )
            continue

        data = current_data[coin]
        ts = data["timestamp"]
        slug = data["slug"]
        net = data["net_position"]
        net_pct = data["net_pct"]
        delta_warnings = data["delta_warnings"]
        has_concentration = data["has_concentration"]

        net_color = "green" if net > 0 else "red"
        net_text = f"净持仓: {net:+,.0f} shares ({net_pct:+.1f}%)"

        concentration_warning = (
            html.Span(
                " 集中度高，注意操控风险",
                style={"color": "orange", "fontWeight": "bold"},
            )
            if has_concentration
            else ""
        )

        delta_alerts = []
        if delta_warnings:
            for w in delta_warnings:
                if "UP" in w:
                    color = "#006400" if "加仓" in w else "#90EE90"
                else:
                    color = "#8B0000" if "加仓" in w else "#FF4040"
                delta_alerts.append(
                    html.P(
                        w,
                        style={"color": color, "margin": "5px 0", "fontWeight": "bold"},
                    )
                )

        up_fig = go.Figure()
        if not data["up"].empty:
            colors = [
                "darkgreen" if is_large else "green"
                for is_large in data["up"]["is_large"]
            ]
            up_fig.add_trace(
                go.Bar(
                    x=data["up"]["shares"],
                    y=data["up"]["user"],
                    orientation="h",
                    marker_color=colors,
                    text=data["up"]["shares"].apply(lambda x: f"{x:,.0f}"),
                    textposition="auto",
                    hovertemplate=(
                        "<b>%{customdata[0]}</b><br>"
                        + "Shares: %{x:,.0f}<br>"
                        + "Address: %{customdata[1]}<br>"
                        + "Name: %{customdata[2]}<br>"
                        + "Pseudonym: %{customdata[3]}<extra></extra>"
                    ),
                    customdata=data["up"][
                        ["full_user", "address", "name", "pseudonym"]
                    ].values,
                )
            )
        up_fig.update_layout(
            title=f"{coin} UP (Yes) - {ts}", xaxis_title="Shares", height=450
        )

        down_fig = go.Figure()
        if not data["down"].empty:
            colors = [
                "darkred" if is_large else "red"
                for is_large in data["down"]["is_large"]
            ]
            down_fig.add_trace(
                go.Bar(
                    x=data["down"]["shares"],
                    y=data["down"]["user"],
                    orientation="h",
                    marker_color=colors,
                    text=data["down"]["shares"].apply(lambda x: f"{x:,.0f}"),
                    textposition="auto",
                    hovertemplate=(
                        "<b>%{customdata[0]}</b><br>"
                        + "Shares: %{x:,.0f}<br>"
                        + "Address: %{customdata[1]}<br>"
                        + "Name: %{customdata[2]}<br>"
                        + "Pseudonym: %{customdata[3]}<extra></extra>"
                    ),
                    customdata=data["down"][
                        ["full_user", "address", "name", "pseudonym"]
                    ].values,
                )
            )
        down_fig.update_layout(
            title=f"{coin} DOWN (No) - {ts}", xaxis_title="Shares", height=450
        )

        children.append(
            html.Div(
                [
                    html.H3(f"{coin} - {slug}", className="text-center"),
                    html.Div(
                        [
                            html.P(
                                net_text,
                                style={
                                    "color": net_color,
                                    "textAlign": "center",
                                    "fontSize": "1.1em",
                                    "marginBottom": "5px",
                                },
                            ),
                            html.P(
                                [
                                    f"最大持仓: {max(data['up']['shares'].max(), data['down']['shares'].max()):,.0f}",
                                    concentration_warning,
                                ],
                                style={
                                    "textAlign": "center",
                                    "fontSize": "1em",
                                    "marginBottom": "10px",
                                },
                            ),
                            (
                                html.Div(
                                    delta_alerts,
                                    style={
                                        "textAlign": "center",
                                        "marginBottom": "10px",
                                    },
                                )
                                if delta_alerts
                                else None
                            ),
                        ]
                    ),
                    html.Div(
                        [
                            html.Div(dcc.Graph(figure=up_fig), className="col-md-6"),
                            html.Div(dcc.Graph(figure=down_fig), className="col-md-6"),
                        ],
                        className="row",
                    ),
                ],
                className="mb-5",
            )
        )

    return children


if __name__ == "__main__":
    port = int(os.getenv("PORT", 8050))
    app.run(debug=False, host="0.0.0.0", port=port)
