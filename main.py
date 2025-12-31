import streamlit as st
import yfinance as yf
import pandas as pd
import requests
from bs4 import BeautifulSoup
import os
import datetime
from io import StringIO
import json
import time
from deep_translator import GoogleTranslator

# 设置页面配置
st.set_page_config(page_title="巴菲特价值选股器", layout="wide")

CACHE_FILE = "stock_cache.csv"
META_FILE = "cache_metadata.json"

def translate_text(text):
    if not text:
        return text
    try:
        # 使用 Google Translate
        return GoogleTranslator(source='auto', target='zh-CN').translate(text)
    except Exception:
        return text

def save_cache(df):
    try:
        df.to_csv(CACHE_FILE, index=False)
        with open(META_FILE, 'w') as f:
            json.dump({"last_updated": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")}, f)
        return True
    except Exception as e:
        st.error(f"缓存保存失败: {e}")
        return False

def load_cache():
    if os.path.exists(CACHE_FILE) and os.path.exists(META_FILE):
        try:
            df = pd.read_csv(CACHE_FILE)
            with open(META_FILE, 'r') as f:
                meta = json.load(f)
            return df, meta.get("last_updated", "未知时间")
        except Exception:
            return None, None
    return None, None

# 获取S&P 500成分股列表
@st.cache_data
def get_sp500_tickers():
    url = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
    }
    try:
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        
        # 尝试使用 pandas read_html 解析表格
        try:
            dfs = pd.read_html(StringIO(response.text))
            for df in dfs:
                if 'Symbol' in df.columns:
                    tickers = df['Symbol'].tolist()
                    # 替换 . 为 - (例如 BRK.B -> BRK-B)
                    return [str(t).replace('.', '-') for t in tickers]
        except Exception as e:
            print(f"Pandas read_html failed: {e}")

        # 如果 pandas 失败，回退到 BeautifulSoup
        soup = BeautifulSoup(response.text, 'html.parser')
        table = soup.find('table', {'id': 'constituents'})
        
        if not table:
            st.error("无法在页面上找到股票表格，Wikipedia 页面结构可能已更改。")
            return []
            
        tickers = []
        for row in table.findAll('tr')[1:]:
            cols = row.findAll('td')
            if cols:
                ticker = cols[0].text.strip()
                tickers.append(ticker.replace('.', '-'))
        return tickers
    except Exception as e:
        st.error(f"获取股票列表失败: {e}")
        return []

import concurrent.futures

# 获取股票数据并筛选
@st.cache_data(ttl=3600*24) # 缓存24小时
def analyze_stocks(tickers):
    selected_stocks = []
    
    # 进度条
    progress_bar = st.progress(0)
    status_text = st.empty()
    
    total = len(tickers)
    processed_count = 0
    
    def process_ticker(ticker):
        try:
            stock = yf.Ticker(ticker)
            # 访问 info 属性会触发网络请求
            info = stock.info
            
            if not info:
                return None
                
            # 巴菲特选股策略 (简化版)
            # 1. 净资产收益率 (ROE) > 15%
            roe = info.get('returnOnEquity', 0)
            if roe is None or roe < 0.15:
                return None
                
            # 2. 债务权益比 (Debt to Equity) < 1.5 (稍微放宽到1.5)
            # debtToEquity 是百分比，例如 50 表示 0.5
            de_ratio = info.get('debtToEquity', 1000)
            if de_ratio is None or de_ratio > 150: 
                return None
                
            # 3. 毛利率 (Gross Margins) > 40% (可选，巴菲特喜欢高毛利)
            gross_margins = info.get('grossMargins', 0)
            if gross_margins is None or gross_margins < 0.4:
                return None
                
            # 4. 市盈率 (PE Ratio) > 0 且不过高
            pe = info.get('trailingPE', 0)
            if pe is None or pe <= 0 or pe > 35: # 放宽到35
                return None
            
            return {
                '代码': ticker,
                '名称': info.get('shortName', ticker),
                '中文名称': info.get('shortName', ticker), # 稍后批量翻译
                '当前价格': info.get('currentPrice', 0),
                '52周最高': info.get('fiftyTwoWeekHigh', 0),
                '52周最低': info.get('fiftyTwoWeekLow', 0),
                '市盈率(PE)': round(pe, 2),
                'ROE(%)': round(roe * 100, 2),
                '债务权益比(%)': de_ratio,
                '毛利率(%)': round(gross_margins * 100, 2),
                '市值(亿)': round(info.get('marketCap', 0) / 100000000, 2),
                '行业': info.get('industry', '未知'),
                '中文行业': info.get('industry', '未知') # 稍后批量翻译
            }
        except Exception:
            return None

    # 使用线程池并发处理，提高速度
    # 注意：并发过高可能会被封IP，建议适度
    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
        futures = {executor.submit(process_ticker, ticker): ticker for ticker in tickers}
        
        for i, future in enumerate(concurrent.futures.as_completed(futures)):
            result = future.result()
            if result:
                selected_stocks.append(result)
            
            processed_count += 1
            if processed_count % 10 == 0: # 每10个更新一次进度条，减少刷新频率
                progress = processed_count / total
                progress_bar.progress(progress)
                status_text.text(f"正在分析... ({processed_count}/{total})")

    # 批量翻译 (为了不影响筛选速度，筛选完再翻译)
    status_text.text("正在翻译名称和行业信息...")
    if selected_stocks:
        # 去重行业并翻译，建立缓存
        industries = list(set([s['行业'] for s in selected_stocks if s['行业'] != '未知']))
        industry_map = {}
        for ind in industries:
            industry_map[ind] = translate_text(ind)
            
        # 应用翻译
        for stock in selected_stocks:
            stock['中文行业'] = industry_map.get(stock['行业'], stock['行业'])
            # 公司名称逐个翻译，稍微慢点
            stock['中文名称'] = translate_text(stock['名称'])

    status_text.text("分析完成！")
    progress_bar.empty()
    
    return pd.DataFrame(selected_stocks)


def main():
    st.title("📈 巴菲特价值投资选股器")
    
    with st.expander("查看筛选标准 (巴菲特价值投资理念)", expanded=False):
        st.markdown("""
        **筛选标准：**
        1. **高ROE**：净资产收益率 > 15%
        2. **低负债**：债务权益比 < 150%
        3. **高毛利**：毛利率 > 40%
        4. **合理估值**：市盈率(PE) < 35
        """)
    
    # 初始化 session state
    if 'data' not in st.session_state:
        # 尝试加载缓存
        cached_df, last_updated = load_cache()
        if cached_df is not None:
            st.session_state.data = cached_df
            st.session_state.last_updated = last_updated
            st.info(f"📅 已加载本地缓存数据，上次统计时间：{last_updated}")
        else:
            st.session_state.data = None
            st.session_state.last_updated = None

    col1, col2 = st.columns([1, 4])
    with col1:
        btn_label = "重新选股" if st.session_state.data is not None else "开始选股"
        start_btn = st.button(btn_label, type="primary")
    
    if start_btn:
        with st.spinner('正在获取S&P 500列表并分析数据，请耐心等待（这可能需要几分钟）...'):
            tickers = get_sp500_tickers()
            if tickers:
                # 我们可以先只取前50个做演示，因为500个太慢了
                # 或者全量跑，因为有缓存
                # 为了保证“市值前500名”，S&P 500就是最好的代表
                df = analyze_stocks(tickers) 
                st.session_state.data = df
                st.session_state.last_updated = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                save_cache(df) # 保存缓存
                st.rerun() # 重新加载以显示结果和更新时间
            else:
                st.error("无法获取股票列表")

    if st.session_state.data is not None:
        df = st.session_state.data
        if 'last_updated' in st.session_state and st.session_state.last_updated:
             st.caption(f"数据统计时间: {st.session_state.last_updated}")

        if df.empty:
            st.warning("没有找到符合所有条件的股票。")
        else:
            st.success(f"筛选出 {len(df)} 只符合条件的股票：")
            st.info("💡 点击表格中的行可以查看股票详情")
            
            # 显示表格
            event = st.dataframe(
                df,
                column_config={
                    "代码": "股票代码",
                    "中文名称": "公司名称",
                    "当前价格": st.column_config.NumberColumn("价格($)", format="$%.2f"),
                    "52周最高": st.column_config.NumberColumn("52周最高", format="$%.2f"),
                    "52周最低": st.column_config.NumberColumn("52周最低", format="$%.2f"),
                    "市盈率(PE)": st.column_config.NumberColumn("PE", format="%.2f"),
                    "ROE(%)": st.column_config.NumberColumn("ROE", format="%.2f%%"),
                    "债务权益比(%)": st.column_config.NumberColumn("负债率", format="%.2f%%"),
                    "毛利率(%)": st.column_config.NumberColumn("毛利率", format="%.2f%%"),
                    "市值(亿)": st.column_config.NumberColumn("市值($亿)", format="$%.2f"),
                    "中文行业": "行业",
                },
                column_order=["代码", "中文名称", "中文行业", "当前价格", "52周最高", "52周最低", "市盈率(PE)", "ROE(%)", "债务权益比(%)", "毛利率(%)", "市值(亿)"],
                hide_index=True,
                use_container_width=True,
                on_select="rerun",
                selection_mode="single-row"
            )
            
            # 股票详情查看
            if len(event.selection.rows) > 0:
                selected_index = event.selection.rows[0]
                selected_ticker = df.iloc[selected_index]['代码']
                show_stock_details(selected_ticker)

def show_stock_details(ticker):
    try:
        stock = yf.Ticker(ticker)
        info = stock.info
        
        st.markdown(f"### {info.get('shortName')} ({ticker})")
        
        col1, col2, col3 = st.columns(3)
        with col1:
            st.metric("当前价格", f"${info.get('currentPrice', 0)}")
        with col2:
            st.metric("52周最高", f"${info.get('fiftyTwoWeekHigh', 0)}")
        with col3:
            st.metric("52周最低", f"${info.get('fiftyTwoWeekLow', 0)}")
            
        st.markdown("#### 公司简介")
        # 尝试翻译简介或者直接显示英文
        st.write(info.get('longBusinessSummary', '暂无简介'))
        
        st.markdown("#### 核心财务数据")
        fin_data = {
            "指标": ["市值", "企业价值", "Trailing PE", "Forward PE", "PEG Ratio", "Price/Book"],
            "数值": [
                f"${info.get('marketCap', 0):,}",
                f"${info.get('enterpriseValue', 0):,}",
                str(info.get('trailingPE', 'N/A')),
                str(info.get('forwardPE', 'N/A')),
                str(info.get('pegRatio', 'N/A')),
                str(info.get('priceToBook', 'N/A'))
            ]
        }
        st.table(pd.DataFrame(fin_data))
        
    except Exception as e:
        st.error(f"无法获取详情: {e}")

if __name__ == "__main__":
    main()
