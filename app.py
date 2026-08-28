"""
A股集合竞价选股工具 - Streamlit 网页版
功能：
1. 手动选股：获取实时（或最近）竞价数据，按条件筛选
2. 个股历史竞价分析：查看某只股票近两年的竞价成交额、量能柱、连板情况
数据源：akshare（免费）
部署：Streamlit Cloud（免费托管）
"""

import streamlit as st
import pandas as pd
import akshare as ak
from datetime import datetime, timedelta
import traceback
import time

# 设置页面标题
st.set_page_config(page_title="A股集合竞价选股", page_icon="📈", layout="wide")

# ------------------------------
# 工具函数
# ------------------------------

def load_trade_dates():
    """获取交易日历，失败则用工作日"""
    try:
        df = ak.tool_trade_date_hist_sina()
        col = 'trade_date' if 'trade_date' in df.columns else df.columns[0]
        return [str(d) for d in df[col]]
    except:
        # 降级：生成最近500个工作日
        today = datetime.now().strftime("%Y-%m-%d")
        dt = datetime.strptime(today, "%Y-%m-%d") - timedelta(days=1)
        dates = []
        while len(dates) < 500:
            if dt.weekday() < 5:
                dates.append(dt.strftime("%Y-%m-%d"))
            dt -= timedelta(days=1)
        dates.reverse()
        return dates

def get_prev_trade_date(date_str, trade_dates):
    """获取上一个交易日"""
    if date_str in trade_dates:
        idx = trade_dates.index(date_str)
        if idx > 0:
            return trade_dates[idx-1]
    # 否则往前找
    past = [d for d in trade_dates if d < date_str]
    return past[-1] if past else None

def get_spot_df():
    """获取全市场实时行情快照（带重试）"""
    for attempt in range(3):
        try:
            df = ak.stock_zh_a_spot_em()
            df.columns = [str(c) for c in df.columns]
            return df
        except Exception as e:
            st.warning(f"获取行情失败，重试 {attempt+1}/3...")
            time.sleep(3)
    st.error("多次获取行情失败，请稍后再试")
    return None

def get_stock_auction_history(code, trade_dates, days=480):
    """获取个股历史竞价数据（基于1分钟K线提取09:25）"""
    recent_dates = trade_dates[-days:] if len(trade_dates) > days else trade_dates
    records = []
    prev_amount = None
    prev_close = None

    progress = st.progress(0)
    total = len(recent_dates)
    for i, date_str in enumerate(recent_dates):
        progress.progress((i+1)/total)
        try:
            # 获取当日09:25的1分钟K线
            start = f"{date_str} 09:25:00"
            end = f"{date_str} 09:26:00"
            df_min = ak.stock_zh_a_hist_min_em(symbol=code, start_date=start, end_date=end, period="1", adjust="")
            if df_min is None or df_min.empty:
                # 尝试09:30
                start = f"{date_str} 09:30:00"
                end = f"{date_str} 09:31:00"
                df_min = ak.stock_zh_a_hist_min_em(symbol=code, start_date=start, end_date=end, period="1", adjust="")
            if df_min is None or df_min.empty:
                continue

            row = df_min.iloc[0]
            open_price = float(row['开盘'])
            volume = float(row['成交量'])  # 手
            auction_amount = open_price * volume * 100  # 元

            # 判断涨停
            is_limit_up = False
            if prev_close and prev_close > 0:
                change_pct = (open_price - prev_close) / prev_close * 100
                if code.startswith(('30','68')):
                    limit_pct = 20.0
                else:
                    limit_pct = 10.0
                if abs(change_pct - limit_pct) < 0.5:
                    is_limit_up = True

            # 判断一字板
            high = float(row['最高'])
            low = float(row['最低'])
            close = float(row['收盘'])
            is_yizi = (open_price == high == low == close) and is_limit_up

            records.append({
                '日期': date_str,
                '竞价成交量(手)': volume,
                '开盘价': open_price,
                '竞价成交额(元)': auction_amount,
                '前一日竞价额': prev_amount,
                '变化额': auction_amount - prev_amount if prev_amount is not None else None,
                '是否涨停': is_limit_up,
                '是否一字涨停': is_yizi,
            })

            # 更新prev_close（用日线收盘价更准）
            prev_amount = auction_amount
            try:
                df_daily = ak.stock_zh_a_hist(symbol=code, period="daily", start_date=date_str, end_date=date_str, adjust="")
                if df_daily is not None and not df_daily.empty:
                    prev_close = float(df_daily.iloc[0]['收盘'])
            except:
                prev_close = close if close > 0 else open_price

        except Exception as e:
            # 静默跳过
            pass

    progress.empty()

    if not records:
        return None

    df = pd.DataFrame(records)
    df = df.sort_values('日期', ascending=False).reset_index(drop=True)

    # 计算连板
    consecutive = 0
    for _, r in df.iterrows():
        if r['是否涨停']:
            consecutive += 1
        else:
            break
    df['连板天数'] = consecutive

    # 连板累计成交额：从最早涨停日到最新涨停日的全天成交额总和
    total_turnover = 0
    if consecutive > 0:
        first_date = df[df['是否涨停']].iloc[-1]['日期']
        last_date = df[df['是否涨停']].iloc[0]['日期']
        try:
            df_range = ak.stock_zh_a_hist(symbol=code, period="daily", start_date=first_date, end_date=last_date, adjust="")
            if df_range is not None and not df_range.empty:
                total_turnover = float(df_range['成交额'].sum())
        except:
            pass
    df['连板累计成交额'] = total_turnover

    return df

# ------------------------------
# Streamlit 界面
# ------------------------------

st.title("A股集合竞价选股工具")
st.caption("数据来源：akshare | 仅供研究，不构成投资建议")

# 侧边栏：模式选择
mode = st.sidebar.radio("选择功能", ["选股", "个股历史竞价分析"])

if mode == "选股":
    st.header("集合竞价选股")
    st.markdown("手动点击按钮开始选股，结果可导出CSV。注意：非交易时间获取的是最新行情数据。")

    # 参数设置放在侧边栏
    with st.sidebar.expander("选股条件", expanded=True):
        exclude_st = st.checkbox("剔除ST", value=True)
        exclude_star = st.checkbox("剔除科创板", value=True)
        exclude_bse = st.checkbox("剔除北交所", value=True)
        exclude_gem = st.checkbox("剔除创业板", value=False)
        min_auction_amount = st.number_input("竞价成交额最小（元）", min_value=0, value=5000000, step=100000)
        max_auction_amount = st.number_input("竞价成交额最大（元，0=不限）", min_value=0, value=0, step=100000)
        min_auction_change = st.number_input("竞价涨幅最小（%）", value=0.0, step=0.1)
        max_auction_change = st.number_input("竞价涨幅最大（%）", value=5.0, step=0.1)
        # 更多条件可自行添加

    if st.button("开始选股", type="primary"):
        with st.spinner("正在获取行情并筛选..."):
            spot = get_spot_df()
            if spot is not None:
                # 数据清洗
                spot = spot.dropna(subset=['代码', '名称', '今开', '昨收', '成交额'])
                spot['股票代码'] = spot['代码'].astype(str).str.zfill(6)
                spot['股票名称'] = spot['名称'].astype(str)
                if exclude_st:
                    spot = spot[~spot['股票名称'].str.contains("ST", case=False, na=False)]
                if exclude_star:
                    spot = spot[~spot['股票代码'].str.startswith("688")]
                if exclude_bse:
                    spot = spot[~spot['股票代码'].str.startswith(("8","4"))]
                if exclude_gem:
                    spot = spot[~spot['股票代码'].str.startswith("300")]

                spot['竞价涨幅'] = (spot['今开'] - spot['昨收']) / spot['昨收'] * 100
                spot['竞价成交额'] = spot['成交额']
                spot['竞价成交量'] = spot['成交量']

                # 筛选
                if min_auction_amount > 0:
                    spot = spot[spot['竞价成交额'] >= min_auction_amount]
                if max_auction_amount > 0:
                    spot = spot[spot['竞价成交额'] <= max_auction_amount]
                spot = spot[spot['竞价涨幅'] >= min_auction_change]
                spot = spot[spot['竞价涨幅'] <= max_auction_change]

                # 输出列
                cols = ['股票代码','股票名称','最新价','竞价涨幅','竞价成交额','竞价成交量','流通市值','总市值','换手率','量比','最新涨跌幅']
                available = [c for c in cols if c in spot.columns]
                result = spot[available].sort_values('竞价成交额', ascending=False).reset_index(drop=True)

                st.success(f"选股完成，共 {len(result)} 只股票")
                st.dataframe(result)

                # CSV导出
                csv = result.to_csv(index=False).encode('utf-8-sig')
                st.download_button("下载CSV", csv, "选股结果.csv", "text/csv")
            else:
                st.error("获取行情失败，请检查网络或稍后再试")

elif mode == "个股历史竞价分析":
    st.header("个股历史竞价分析")
    code_input = st.text_input("输入股票代码（6位数字）", value="000001")
    days_input = st.slider("分析天数（交易日）", min_value=30, max_value=480, value=240)

    if st.button("分析"):
        with st.spinner("正在获取历史数据，可能需要1-2分钟..."):
            trade_dates = load_trade_dates()
            df = get_stock_auction_history(code_input, trade_dates, days=days_input)
            if df is not None and not df.empty:
                st.success(f"获取到 {len(df)} 条竞价数据")
                # 摘要
                latest = df.iloc[0]
                st.metric("最新竞价成交额", f"{latest['竞价成交额(元)']:,.0f} 元")
                st.metric("连板天数", latest['连板天数'])
                st.metric("连板累计成交额", f"{latest['连板累计成交额']:,.0f} 元")

                # 柱状图
                st.subheader("近30日竞价成交额柱状图")
                recent = df.head(30).sort_values('日期')
                # 用颜色区分增减
                colors = []
                for _, r in recent.iterrows():
                    if r['变化额'] is not None and r['变化额'] > 0:
                        colors.append('#e74c3c')  # 红涨
                    else:
                        colors.append('#2ecc71')  # 绿跌
                st.bar_chart(recent.set_index('日期')['竞价成交额(元)'], color=colors)

                # 完整表格
                st.subheader("详细数据")
                st.dataframe(df[['日期','竞价成交额(元)','前一日竞价额','变化额','竞价成交量(手)','是否一字涨停','连板天数','连板累计成交额']])

                # 导出
                csv = df.to_csv(index=False).encode('utf-8-sig')
                st.download_button("下载历史数据CSV", csv, f"{code_input}_竞价历史.csv", "text/csv")
            else:
                st.error("未获取到数据，可能该股票无数据或网络问题")
