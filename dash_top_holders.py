"""
Polymarket 15min Top Holders Live Dashboard (最终稳定版 - Telegram推送无颜色错误)
- 去除所有 <span style> 标签，避免 400 Bad Request
- 用 emoji + 粗体区分方向与加减仓（视觉足够明显）
- 支持多个 chat_id（.env 用逗号分隔）
- 其他功能完整
"""

import logging
import re
import os
from datetime import datetime
import httpx
from dotenv import load_dotenv
import dash
from dash import dcc, html
from dash.dependencies import Input, Output
import plotly.graph_objects as go
import pandas as pd
import requests

load_dotenv()

INTERVAL_SEC = int(os.getenv("QUERY_INTERVAL_SECONDS", 45))
TOP_N = min(int(os.getenv("TOP_LIMIT", 12)), 20)
MIN_BALANCE = int(os.getenv("MIN_BALANCE", 50))
USERNAME_MAX_LEN = int(os.getenv("USERNAME_MAX_LEN", 15))

LARGE_POSITION_THRESHOLD = int(os.getenv("LARGE_POSITION_THRESHOLD", 10000))
CONCENTRATION_THRESHOLD = int(os.getenv("CONCENTRATION_THRESHOLD", 30000))
DELTA_THRESHOLD = int(os.getenv("DELTA_THRESHOLD", 1000))

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")  # 支持多个，用逗号分隔

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


def find_current_slug(coin: str):
    url = PAGE_URLS.get(coin, PAGE_URLS["ETH"])
    try:
        r = httpx.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=20)
        matches = re.findall(rf"{PREFIXES[coin]}(\d+)", r.text)
        if not matches:
            return None
        now = int(datetime.now().timestamp())
        active = max(
            (int(ts) for ts in matches if now < int(ts) + 900),
            default=max(map(int, matches)),
        )
        return f"{PREFIXES[coin]}{active}"
    except Exception as e:
        logger.error(f"找 {coin} 当前市场失败: {e}")
        return None


def get_condition_id(slug: str):
    try:
        r = httpx.get(
            f"https://gamma-api.polymarket.com/markets?slug={slug}", timeout=10
        )
        data = r.json()
        return data[0]["conditionId"] if data else None
    except:
        return None


def update_data():
    global current_data, prev_data
    for coin in COINS:
        slug = find_current_slug(coin)
        if not slug:
            continue
        cond_id = get_condition_id(slug)
        if not cond_id:
            continue

        try:
            holders_data = fetch_holders(cond_id)
            now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

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
                        action = "加仓" if delta_val > 0 else "减仓"
                        delta_warnings.append(
                            f"{direction} {action} {row['full_user']} ({sign}{abs(delta_val):,.0f} shares)"
                        )

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

            # Telegram 推送（稳定版：去除 span style，用 emoji + 粗体）
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
                                emoji = "📈 <b>UP 加仓</b>"
                            else:
                                emoji = "📉 <b>UP 减仓</b>"
                        else:  # DOWN
                            if "加仓" in w:
                                emoji = "📉 <b>DOWN 加仓</b>"
                            else:
                                emoji = "📈 <b>DOWN 减仓</b>"

                        line = f"{emoji} {w.replace(row['full_user'], row['user'])}"  # 用短名，避免太长
                        messages.append(line)

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
                            logger.error(
                                f"推送到 {chat_id} 失败: {e} | 响应: {response.text if 'response' in locals() else '无'}"
                            )

            logger.info(
                f"{coin} 更新完成: {now_str} | 净持仓 {net_position:+,.0f} ({net_pct:+.1f}%) | 异动: {len(delta_warnings)} 条 | 集中度警告: {has_concentration}"
            )
        except Exception as e:
            logger.error(f"{coin} 更新失败: {e}")


app = dash.Dash(
    __name__,
    external_stylesheets=[
        "https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css"
    ],
)

app.layout = html.Div(
    [
        html.H1(
            "Polymarket 15min Top Holders Live Dashboard", className="text-center mb-4"
        ),
        html.Hr(),
        dcc.Interval(id="interval", interval=INTERVAL_SEC * 1000, n_intervals=0),
        html.Div(id="dashboard-content", className="container"),
    ]
)


@app.callback(Output("dashboard-content", "children"), Input("interval", "n_intervals"))
def render_dashboard(n):
    update_data()

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
    app.run(debug=True, port=8050)
