"""
A股集合竞价选股工具 - Streamlit 网页版（完整版）
功能：
1. 手动选股：获取实时（或最近）竞价数据，按多条件筛选
2. 个股历史竞价分析：查看某只股票近两年的竞价成交额、量能柱、连板情况
数据源：akshare（免费）
部署：Streamlit Cloud（免费托管）
"""

import streamlit as st
import pandas as pd
import akshare as ak
from datetime import datetime, timedelta
import time
import traceback

# 页面基础设置
st.set_page_config(page_title="A股集合竞价选股", page_icon="📈", layout="wide")

# ---------- 自定义样式：小号浅灰色标题 ----------
st.markdown("""
<style>
    .small-grey-title {
        font-size: 16px;
        color: #888888;
        font-weight: normal;
        margin-bottom: 0.5rem;
    }
    .small-grey-subtitle {
        font-size: 14px;
        color: #aaaaaa;
        font-weight: normal;
        margin-bottom: 0.5rem;
    }
</style>
""", unsafe_allow_html=True)

st.markdown('<p class="small-grey-title">A股集合竞价选股工具</p>', unsafe_allow_html=True)
st.markdown('<p class="small-grey-subtitle">数据来源：akshare | 仅供研究，不构成投资建议</p>', unsafe_allow_html=True)

# ---------- 交易日历工具 ----------
@st.cache_data(ttl=3600)
def load_trade_dates():
    """获取交易日历（缓存1小时），失败则用工作日近似"""
    try:
        df = ak.tool_trade_date_hist_sina()
        col = 'trade_date' if 'trade_date' in df.columns else df.columns[0]
        return [str(d) for d in df[col]]
    except:
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
    past = [d for d in trade_dates if d < date_str]
    return past[-1] if past else None

def is_trade_day(date_str, trade_dates):
    """判断是否交易日"""
    return date_str in trade_dates

# ---------- 行情获取（带重试和多数据源） ----------
def get_spot_df():
    """获取全市场实时行情快照，依次尝试东财、新浪、腾讯"""
    for attempt in range(3):
        # 尝试东方财富
        try:
            df = ak.stock_zh_a_spot_em()
            df.columns = [str(c) for c in df.columns]
            return df
        except Exception as e:
            st.warning(f"东财接口失败（尝试 {attempt+1}/3），尝试新浪...")
            time.sleep(2)
        # 尝试新浪
        try:
            df = ak.stock_zh_a_spot()
            df.columns = [str(c) for c in df.columns]
            return df
        except Exception as e:
            st.warning(f"新浪接口失败，尝试腾讯...")
            time.sleep(2)
        # 尝试腾讯
        try:
            df = ak.stock_zh_a_spot_tx()
            df.columns = [str(c) for c in df.columns]
            return df
        except Exception as e:
            st.warning(f"腾讯接口也失败，等待5秒重试...")
            time.sleep(5)
    st.error("所有数据源均连接失败，请稍后再试")
    return None

# ---------- 历史竞价数据获取（个股） ----------
def get_stock_auction_history(code, trade_dates, days=480):
    """获取个股历史竞价数据，基于1分钟K线提取09:25数据"""
    recent_dates = trade_dates[-days:] if len(trade_dates) > days else trade_dates
    records = []
    prev_amount = None
    prev_close = None

    progress = st.progress(0)
    total = len(recent_dates)
    for i, date_str in enumerate(recent_dates):
        progress.progress((i+1)/total)
        try:
            # 尝试获取09:25的1分钟K线
            start = f"{date_str} 09:25:00"
            end = f"{date_str} 09:26:00"
            df_min = ak.stock_zh_a_hist_min_em(symbol=code, start_date=start, end_date=end, period="1", adjust="")
            if df_min is None or df_min.empty:
                # 有些日期可能从09:30开始
                start = f"{date_str} 09:30:00"
                end = f"{date_str} 09:31:00"
                df_min = ak.stock_zh_a_hist_min_em(symbol=code, start_date=start, end_date=end, period="1", adjust="")
            if df_min is None or df_min.empty:
                continue

            row = df_min.iloc[0]
            open_price = float(row['开盘'])
            volume = float(row['成交量'])  # 单位：手
            auction_amount = open_price * volume * 100  # 手转股，再乘价格

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

            # 更新前一数据
            prev_amount = auction_amount
            try:
                df_daily = ak.stock_zh_a_hist(symbol=code, period="daily", start_date=date_str, end_date=date_str, adjust="")
                if df_daily is not None and not df_daily.empty:
                    prev_close = float(df_daily.iloc[0]['收盘'])
            except:
                prev_close = close if close > 0 else open_price

        except Exception as e:
            # 静默跳过单个日期失败
            pass

    progress.empty()
    if not records:
        return None

    df = pd.DataFrame(records)
    df = df.sort_values('日期', ascending=False).reset_index(drop=True)

    # 计算连板天数
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

# ---------- 侧边栏：模式选择 ----------
mode = st.sidebar.radio("选择功能", ["选股", "个股历史竞价分析"])

# ================= 选股模式 =================
if mode == "选股":
    st.markdown('<p class="small-grey-title">集合竞价选股</p>', unsafe_allow_html=True)
    st.caption("手动点击按钮开始选股，结果可导出CSV。注意：非交易时间获取的是最新行情数据。")

    # 时间判断提示
    now = datetime.now()
    today_str = now.strftime("%Y-%m-%d")
    trade_dates = load_trade_dates()
    if is_trade_day(today_str, trade_dates):
        if now.hour == 9 and 15 <= now.minute < 25:
            st.warning("现在是9:15-9:25集合竞价可撤单阶段，数据可能虚假，请等到9:25后再操作。")
        elif now.hour == 9 and now.minute >= 25:
            st.success("当前已过9:25，可获取最终竞价数据。")
        else:
            st.info("当前非竞价时间段，选股结果基于最新行情。")
    else:
        st.info("今天非交易日，选股结果基于最新行情。")

    # 选股条件设置
    with st.sidebar.expander("选股条件", expanded=True):
        exclude_st = st.checkbox("剔除ST", value=True)
        exclude_star = st.checkbox("剔除科创板", value=True)
        exclude_bse = st.checkbox("剔除北交所", value=True)
        exclude_gem = st.checkbox("剔除创业板", value=False)

        st.markdown("**竞价成交额条件**")
        min_auction_amount = st.number_input("最小竞价成交额（元）", min_value=0, value=5000000, step=100000)
        max_auction_amount = st.number_input("最大竞价成交额（元，0=不限）", min_value=0, value=0, step=100000)

        st.markdown("**竞价涨幅条件**")
        min_auction_change = st.number_input("最小竞价涨幅（%）", value=0.0, step=0.1)
        max_auction_change = st.number_input("最大竞价涨幅（%）", value=5.0, step=0.1)

        st.markdown("**量比条件**")
        enable_volume_ratio = st.checkbox("启用量比条件", value=False)
        min_volume_ratio = st.number_input("最小量比", value=1.0, step=0.1, disabled=not enable_volume_ratio)

        st.markdown("**竞价金额占比昨日全天成交额**")
        enable_amount_ratio = st.checkbox("启用金额占比条件", value=False)
        min_amount_ratio = st.number_input("最小占比（%）", value=1.0, step=0.1, disabled=not enable_amount_ratio)
        max_amount_ratio = st.number_input("最大占比（%，0=不限）", value=0.0, step=0.1, disabled=not enable_amount_ratio)

        st.markdown("**最新涨跌幅条件**")
        min_change = st.number_input("最小涨跌幅（%）", value=None, step=0.1)
        max_change = st.number_input("最大涨跌幅（%）", value=None, step=0.1)

        st.markdown("**流通市值条件**")
        min_float_mcap = st.number_input("最小流通市值（元）", value=None, step=1000000)
        max_float_mcap = st.number_input("最大流通市值（元）", value=None, step=1000000)

    if st.button("开始选股", type="primary"):
        # 时间拦截：9:15-9:25禁止
        if is_trade_day(today_str, trade_dates) and now.hour == 9 and 15 <= now.minute < 25:
            st.error("当前为9:15-9:25集合竞价可撤单阶段，数据虚假，禁止选股。请9:25后再试。")
        else:
            with st.spinner("正在获取行情并筛选..."):
                spot = get_spot_df()
                if spot is not None:
                    # 数据清洗
                    spot = spot.dropna(subset=['代码', '名称', '今开', '昨收', '成交额'])
                    spot['股票代码'] = spot['代码'].astype(str).str.zfill(6)
                    spot['股票名称'] = spot['名称'].astype(str)

                    # 剔除条件
                    if exclude_st:
                        spot = spot[~spot['股票名称'].str.contains("ST", case=False, na=False)]
                    if exclude_star:
                        spot = spot[~spot['股票代码'].str.startswith("688")]
                    if exclude_bse:
                        spot = spot[~spot['股票代码'].str.startswith(("8","4"))]
                    if exclude_gem:
                        spot = spot[~spot['股票代码'].str.startswith("300")]

                    # 计算基础指标
                    spot['竞价涨幅'] = (spot['今开'] - spot['昨收']) / spot['昨收'] * 100
                    spot['竞价成交额'] = spot['成交额']
                    spot['竞价成交量'] = spot['成交量']
                    spot['最新涨跌幅'] = spot['涨跌幅']

                    # 应用基础过滤
                    if min_auction_amount > 0:
                        spot = spot[spot['竞价成交额'] >= min_auction_amount]
                    if max_auction_amount > 0:
                        spot = spot[spot['竞价成交额'] <= max_auction_amount]
                    if min_auction_change is not None:
                        spot = spot[spot['竞价涨幅'] >= min_auction_change]
                    if max_auction_change is not None:
                        spot = spot[spot['竞价涨幅'] <= max_auction_change]
                    if min_change is not None:
                        spot = spot[spot['最新涨跌幅'] >= min_change]
                    if max_change is not None:
                        spot = spot[spot['最新涨跌幅'] <= max_change]
                    if min_float_mcap is not None:
                        spot = spot[spot['流通市值'] >= min_float_mcap]
                    if max_float_mcap is not None:
                        spot = spot[spot['流通市值'] <= max_float_mcap]

                    # 量比条件（实时快照中已有量比字段）
                    if enable_volume_ratio and '量比' in spot.columns:
                        spot = spot[spot['量比'] >= min_volume_ratio]
                    elif enable_volume_ratio:
                        st.warning("当前数据源未提供量比字段，量比条件忽略。")

                    # 金额占比条件（需要昨日全天成交额，仅对初步筛选后的股票计算）
                    if enable_amount_ratio and len(spot) > 0:
                        st.info(f"正在计算 {len(spot)} 只股票的竞价金额占比...")
                        prev_date = get_prev_trade_date(today_str, trade_dates)
                        ratios = []
                        progress_bar = st.progress(0)
                        for i, (_, row) in enumerate(spot.iterrows()):
                            progress_bar.progress((i+1)/len(spot))
                            code = row['股票代码']
                            try:
                                df_hist = ak.stock_zh_a_hist(symbol=code, period="daily", start_date=prev_date, end_date=prev_date, adjust="")
                                if df_hist is not None and not df_hist.empty:
                                    yesterday_amount = float(df_hist.iloc[0]['成交额'])
                                    ratio = row['竞价成交额'] / yesterday_amount * 100 if yesterday_amount > 0 else None
                                else:
                                    ratio = None
                            except:
                                ratio = None
                            ratios.append(ratio)
                        progress_bar.empty()
                        spot['竞价金额占比'] = ratios
                        # 应用占比过滤
                        spot = spot.dropna(subset=['竞价金额占比'])
                        if min_amount_ratio > 0:
                            spot = spot[spot['竞价金额占比'] >= min_amount_ratio]
                        if max_amount_ratio > 0:
                            spot = spot[spot['竞价金额占比'] <= max_amount_ratio]
                    else:
                        spot['竞价金额占比'] = None

                    # 选择输出列
                    display_cols = ['股票代码','股票名称','最新价','竞价涨幅','竞价成交额','竞价成交量','量比','竞价金额占比','流通市值','总市值','换手率','最新涨跌幅']
                    available_cols = [c for c in display_cols if c in spot.columns]
                    result = spot[available_cols].sort_values('竞价成交额', ascending=False).reset_index(drop=True)

                    st.success(f"选股完成，共 {len(result)} 只股票")
                    st.dataframe(result)

                    # CSV导出
                    csv = result.to_csv(index=False).encode('utf-8-sig')
                    st.download_button("下载CSV", csv, "选股结果.csv", "text/csv")
                else:
                    st.error("获取行情失败，请检查网络或稍后再试")

# ================= 个股历史竞价分析模式 =================
elif mode == "个股历史竞价分析":
    st.markdown('<p class="small-grey-title">个股历史竞价分析</p>', unsafe_allow_html=True)
    code_input = st.text_input("输入股票代码（6位数字）", value="000001")
    days_input = st.slider("分析天数（交易日）", min_value=30, max_value=480, value=240)

    if st.button("开始分析"):
        with st.spinner("正在获取历史数据，可能需要1-2分钟..."):
            trade_dates = load_trade_dates()
            df = get_stock_auction_history(code_input, trade_dates, days=days_input)
            if df is not None and not df.empty:
                st.success(f"获取到 {len(df)} 条竞价数据")
                # 摘要指标
                latest = df.iloc[0]
                col1, col2, col3 = st.columns(3)
                col1.metric("最新竞价成交额", f"{latest['竞价成交额(元)']:,.0f} 元")
                col2.metric("连板天数", latest['连板天数'])
                col3.metric("连板累计成交额", f"{latest['连板累计成交额']:,.0f} 元")

                # 柱状图（近30日）
                st.subheader("近30日竞价成交额柱状图")
                recent = df.head(30).sort_values('日期')
                # 颜色根据变化额正负
                colors = []
                for _, r in recent.iterrows():
                    if r['变化额'] is not None and r['变化额'] > 0:
                        colors.append('#e74c3c')  # 红色表示增加
                    else:
                        colors.append('#2ecc71')  # 绿色表示减少
                st.bar_chart(recent.set_index('日期')['竞价成交额(元)'], color=colors)

                # 详细数据表格
                st.subheader("详细数据")
                st.dataframe(df[['日期','竞价成交额(元)','前一日竞价额','变化额','竞价成交量(手)','是否一字涨停','连板天数','连板累计成交额']])

                # 下载
                csv = df.to_csv(index=False).encode('utf-8-sig')
                st.download_button("下载历史数据CSV", csv, f"{code_input}_竞价历史.csv", "text/csv")
            else:
                st.error("未获取到数据，可能该股票无数据或网络问题")
