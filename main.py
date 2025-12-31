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
st.set_page_config(page_title="价值选股器", layout="wide")

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
            # 检查是否有必要的列，如果没有则认为缓存失效
            required_columns = ['中文名称', '中文行业', '52周最高', '52周最低']
            if not all(col in df.columns for col in required_columns):
                return None, None
                
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
    # 初始化 session state (移到最前面，以便UI逻辑使用)
    if 'data' not in st.session_state:
        # 尝试加载缓存
        cached_df, last_updated = load_cache()
        if cached_df is not None:
            st.session_state.data = cached_df
            st.session_state.last_updated = last_updated
        else:
            st.session_state.data = None
            st.session_state.last_updated = None

    # 注入 CSS 优化顶部空间和手机显示
    st.markdown("""
        <style>
        /* 隐藏 Streamlit 默认的 Header 和 Footer */
        header {visibility: hidden;}
        .stApp > header {display: none;}
        
        /* 调整顶部内边距，避免被遮挡 */
        .block-container {
            padding-top: 2rem !important;
            padding-bottom: 1rem !important;
        }
        h3 {
            margin-top: 0 !important;
            padding-top: 0 !important;
        }
        /* 调整按钮在手机上的显示 */
        @media (max-width: 640px) {
            .stButton > button {
                width: 100%;
            }
        }
        </style>
    """, unsafe_allow_html=True)

    # 顶部布局：标题 + 按钮 + 状态信息
    # 使用单行布局，将标题和按钮放在一起
    col_header, col_btn = st.columns([3, 1], gap="small")
    
    with col_header:
        st.markdown("### 📈 价值投资选股器")
        
    with col_btn:
        btn_label = "重新选股" if st.session_state.data is not None else "开始选股"
        start_btn = st.button(btn_label, type="primary", use_container_width=True)

    # 紧凑的状态栏
    if 'last_updated' in st.session_state and st.session_state.last_updated:
        count_str = ""
        if st.session_state.data is not None:
            count_str = f" | 共 {len(st.session_state.data)} 只股票"
        
        # 将状态信息和筛选标准放在一行 (利用 columns)
        c1, c2 = st.columns([2, 1])
        with c1:
             st.caption(f"📅 上次统计: {st.session_state.last_updated}{count_str}")
        with c2:
             with st.expander("查看筛选标准", expanded=False):
                st.markdown("""
                **筛选标准：**
                1. **高ROE**：净资产收益率 > 15%
                2. **低负债**：债务权益比 < 150%
                3. **高毛利**：毛利率 > 40%
                4. **合理估值**：市盈率(PE) < 35
                """)
    else:
        st.caption("尚未获取数据")
    
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
        
        # 按照当前价最接近52周最低价排序
        # 计算逻辑：(当前价格 - 52周最低) / 52周最低，值越小越靠前
        try:
            # 确保列是数值类型
            df['当前价格'] = pd.to_numeric(df['当前价格'], errors='coerce')
            df['52周最低'] = pd.to_numeric(df['52周最低'], errors='coerce')
            
            # 计算偏离度
            df['low_diff'] = (df['当前价格'] - df['52周最低']) / df['52周最低']
            
            # 排序
            df = df.sort_values(by='low_diff', ascending=True)
        except Exception as e:
            st.error(f"排序计算出错: {e}")

        if df.empty:
            st.warning("没有找到符合所有条件的股票。")
        else:
            # 显示表格
            # 提示用户操作
            st.caption("💡 单击表格中的行查看详细信息（已按接近52周最低价排序）")
            
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
                width='stretch',
                height=700,
                on_select="rerun",
                selection_mode="single-row"
            )
            
            # 股票详情查看
            if len(event.selection.rows) > 0:
                selected_index = event.selection.rows[0]
                # 注意：排序后索引变了，需要用 iloc 获取正确的数据
                selected_ticker = df.iloc[selected_index]['代码']
                show_stock_details_dialog(selected_ticker)

@st.dialog("股票详情")
def show_stock_details_dialog(ticker):
    show_stock_details(ticker)


# 巴菲特持仓数据 (截至 2025年 Q3)
# 数据来源: 13F Filing via Dataroma/CNBC
BUFFETT_HOLDINGS = {
    "AAPL": {"shares": 238212764, "cost": "约 $35 (2016-2018建仓)"},
    "AXP": {"shares": 151610700, "cost": "约 $8.49 (长期持有)"},
    "BAC": {"shares": 568070012, "cost": "约 $14 (含2017行权)"},
    "KO": {"shares": 400000000, "cost": "约 $3.25 (1988年建仓)"},
    "CVX": {"shares": 122064792, "cost": "约 $128 (2020年起建仓)"},
    "OXY": {"shares": 264941431, "cost": "约 $52 (2019年起建仓)"},
    "MCO": {"shares": 24669778, "cost": "约 $10 (2000年分拆)"},
    "CB": {"shares": 31332895, "cost": "约 $230 - $291 (2023-2025增持)"},
    "KHC": {"shares": 325634818, "cost": "约 $30 (账面价值)"},
    "GOOGL": {"shares": 17846142, "cost": "约 $174 - $257 (2025 Q3建仓)"},
    "DVA": {"shares": 32160579, "cost": "约 $45 (2011-2014建仓)"},
    "KR": {"shares": 50000000, "cost": "约 $42 (2019-2021建仓)"},
    "SIRI": {"shares": 124807117, "cost": "约 $25 (Liberty合并重组)"},
    "V": {"shares": 8297460, "cost": "约 $22 (2011年建仓)"},
    "VRSN": {"shares": 8989880, "cost": "约 $85 (2012-2013建仓)"},
    "MA": {"shares": 3986648, "cost": "约 $25 (2011年建仓)"},
    "AMZN": {"shares": 10000000, "cost": "约 $90 (2019年建仓)"},
    "STZ": {"shares": 13400000, "cost": "未公开 (可能为历史遗留)"},
    "UNH": {"shares": 5039564, "cost": "未公开"},
    "COF": {"shares": 7150000, "cost": "约 $150 (2023-2024建仓)"},
    "AON": {"shares": 4100000, "cost": "约 $300 (2021-2024建仓)"},
    "DPZ": {"shares": 2981945, "cost": "约 $402 - $504 (2024-2025建仓)"},
    "ALLY": {"shares": 29000000, "cost": "约 $35 (2022年建仓)"},
    "LLYVK": {"shares": 10917661, "cost": "未公开"},
    "POOL": {"shares": 3458885, "cost": "约 $310 - $350 (2024-2025建仓)"},
    "LEN": {"shares": 7050950, "cost": "约 $115 (2023年建仓)"},
    "NUE": {"shares": 6407749, "cost": "约 $150 (2023-2024建仓)"},
    "LPX": {"shares": 5664793, "cost": "约 $60 (2022-2023建仓)"},
    "LLYVA": {"shares": 4986588, "cost": "未公开"},
    "FWONK": {"shares": 3018555, "cost": "未公开"},
    "HEI-A": {"shares": 1294612, "cost": "约 $160 - $200 (2024建仓)"},
    "CHTR": {"shares": 1060882, "cost": "约 $160 (2014年建仓)"},
    "LAMR": {"shares": 1202110, "cost": "约 $100 - $123 (2025建仓)"},
    "ALLE": {"shares": 780133, "cost": "未公开"},
    "NVR": {"shares": 11112, "cost": "约 $7000 (2023年建仓)"},
    "DEO": {"shares": 227750, "cost": "约 $160 (2023年建仓)"},
    "JEF": {"shares": 433558, "cost": "约 $30 (2022年建仓)"},
    "LEN-B": {"shares": 180980, "cost": "约 $100"},
    "LILA": {"shares": 2630792, "cost": "未公开"},
    "BATRK": {"shares": 223645, "cost": "未公开"},
    "LILAK": {"shares": 1284020, "cost": "未公开"}
}

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
        summary = info.get('longBusinessSummary', '暂无简介')
        if summary and summary != '暂无简介':
            # 如果简介太长，Google Translate API可能会报错，可以考虑截断或者分段，这里先直接尝试
            # 为了更好的体验，可以在这里显示“正在翻译...”
            summary = translate_text(summary)
        st.write(summary)
        
        # 巴菲特持仓情况 (新增)
        st.markdown("#### 🏦 巴菲特持仓情况")
        
        # 标准化 ticker (将 . 替换为 - 以匹配字典键)
        lookup_ticker = ticker.replace('.', '-')
        
        if lookup_ticker in BUFFETT_HOLDINGS:
            holding = BUFFETT_HOLDINGS[lookup_ticker]
            shares = holding['shares']
            cost = holding['cost']
            
            # 计算持仓市值 (如果能获取到当前价格)
            current_price = info.get('currentPrice', 0)
            market_value_str = "N/A"
            if current_price and shares:
                 market_value = current_price * shares
                 market_value_str = f"${market_value:,.2f}"
            
            st.success(f"✅ 巴菲特 (Berkshire Hathaway) 持有此股")
            
            b_col1, b_col2, b_col3 = st.columns(3)
            with b_col1:
                st.metric("持仓数量", f"{shares:,} 股")
            with b_col2:
                st.metric("当前持仓市值", market_value_str)
            with b_col3:
                st.metric("估计成本", cost)
                
            st.caption(f"数据来源: Berkshire Hathaway 13F Filing (Q3 2025). 成本数据仅为估计或未公开。")
        else:
            st.info("ℹ️ 巴菲特 (Berkshire Hathaway) 当前未持有此股 (基于 Q3 2025 数据)")

        st.markdown("#### 核心财务数据")
        
        # 格式化股息率
        div_yield = info.get('dividendYield')
        if div_yield is not None:
            # yfinance 返回的 dividendYield 通常已经是百分比数值 (例如 0.38 代表 0.38%, 7.34 代表 7.34%)
            # 不需要乘以 100
            div_yield_str = f"{div_yield:.2f}%"
        else:
            div_yield_str = "N/A"

        fin_data = {
            "指标": ["总市值", "企业价值", "静态市盈率 (TTM)", "预测市盈率 (Forward)", "PEG 比率", "市净率 (P/B)", "股息率"],
            "数值": [
                f"${info.get('marketCap', 0):,}",
                f"${info.get('enterpriseValue', 0):,}",
                str(info.get('trailingPE', 'N/A')),
                str(info.get('forwardPE', 'N/A')),
                str(info.get('pegRatio', 'N/A')),
                str(info.get('priceToBook', 'N/A')),
                div_yield_str
            ]
        }
        st.table(pd.DataFrame(fin_data))
        
    except Exception as e:
        st.error(f"无法获取详情: {e}")

if __name__ == "__main__":
    main()
