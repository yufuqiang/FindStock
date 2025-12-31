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
import random
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
            required_columns = ['中文名称', '中文行业', '52周最高', '52周最低', '当前价格']
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
    
    # 定义周期性行业列表 (根据 GICS 标准简化)
    CYCLICAL_SECTORS = [
        "Energy", "Materials", "Industrials", "Consumer Discretionary", "Financials", "Real Estate",
        "Basic Materials", "Financial Services", "Consumer Cyclical" # yfinance 可能返回的行业名称
    ]

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
                
            # 3. 毛利率 (Gross Margins) > 20% (巴菲特喜欢高毛利，但40%过于严格，可能漏掉零售巨头如Costco，调整为20%)
            gross_margins = info.get('grossMargins', 0)
            if gross_margins is None or gross_margins < 0.2:
                return None
                
            # 4. 市盈率 (PE Ratio) > 0 且不过高
            pe = info.get('trailingPE', 0)
            if pe is None or pe <= 0 or pe > 35: # 放宽到35
                return None

            # 5. 自由现金流 (Free Cash Flow) > 0 (真金白银)
            # 注意：yfinance 的 key 是 freeCashflow (全小写 flow)，不是 freeCashFlow
            fcf = info.get('freeCashflow')
            if fcf is None:
                # 尝试手动计算: 经营现金流 - 资本开支
                ocf = info.get('operatingCashflow')
                capex = info.get('capitalExpenditures') # 通常是负数
                if ocf is not None and capex is not None:
                    fcf = ocf + capex # capex 是负数，所以相加
                else:
                    fcf = 0 # 无法获取，默认为0，避免报错，但可能漏掉好公司
            
            if fcf < 0:
                return None

            # 6. 净利率 (Profit Margins) > 10% (最终赚钱能力)
            net_margin = info.get('profitMargins', 0)
            if net_margin is None or net_margin < 0.1:
                return None

            # 7. 营收增长率 (Revenue Growth) > 0 (确保未衰退)
            rev_growth = info.get('revenueGrowth', 0)
            # 考虑到短期波动，暂时不作为硬性剔除标准，仅作为展示，或者放宽到 -5% 以防误杀
            # 这里暂时不做硬性过滤，只获取数据

            # 判断是否为周期股
            sector = info.get('sector', 'Unknown')
            is_cyclical = sector in CYCLICAL_SECTORS
            
            # 判断估值状态
            valuation_status = "未知"
            peg = info.get('pegRatio')
            
            # 如果没有 PEG，尝试根据 PE 和 增长率估算 (PEG = PE / (GrowthRate * 100))
            if peg is None:
                pe_val = info.get('trailingPE')
                growth_val = info.get('earningsGrowth') # 预估增长率
                if pe_val is not None and growth_val is not None and growth_val > 0:
                    peg = pe_val / (growth_val * 100)
            
            # revenueGrowth 是小数，例如 0.05 表示 5%
            # 优先判断衰退，再判断估值
            if rev_growth is not None and rev_growth < 0:
                valuation_status = "📉 衰退" # 营收负增长
            elif peg is not None:
                if peg < 1.0 and rev_growth > 0:
                    valuation_status = "💰 低估" # PEG < 1 且有增长
                elif 1.0 <= peg <= 2.0 and rev_growth > 0:
                    valuation_status = "⚖️ 合理" # 1 <= PEG <= 2
                elif peg > 2.0 and rev_growth > 0:
                    valuation_status = "🏔️ 高估" # PEG > 2
            
            # 构建合并显示列
            range_52 = f"${info.get('fiftyTwoWeekLow', 0)} - ${info.get('fiftyTwoWeekHigh', 0)}"
            
            pe_display = f"{round(pe, 2)}"
            roe_display = f"{round(roe * 100, 2)}%"
            pe_roe_merged = f"PE:{pe_display} | ROE:{roe_display}"
            
            debt_display = f"{de_ratio}%"
            margin_display = f"{round(gross_margins * 100, 2)}%"
            debt_margin_merged = f"负债:{debt_display} | 毛利:{margin_display}"

            return {
                '代码': ticker,
                '名称': info.get('shortName', ticker),
                '中文名称': info.get('shortName', ticker), # 稍后批量翻译
                '估值状态': valuation_status,
                '当前价格': info.get('currentPrice', 0),
                '52周最高': info.get('fiftyTwoWeekHigh', 0),
                '52周最低': info.get('fiftyTwoWeekLow', 0),
                '52周范围': range_52,
                'PE/ROE': pe_roe_merged,
                '负债/毛利': debt_margin_merged,
                '市盈率(PE)': round(pe, 2),
                'PEG': round(peg, 2) if peg is not None else 0,
                'ROE(%)': round(roe * 100, 2),
                '债务权益比(%)': de_ratio,
                '毛利率(%)': round(gross_margins * 100, 2),
                '净利率(%)': round(net_margin * 100, 2),
                '自由现金流(亿)': round(fcf / 100000000, 2) if fcf is not None else 0,
                '市值(亿)': round(info.get('marketCap', 0) / 100000000, 2),
                '行业': info.get('industry', '未知'),
                '板块': sector, # 新增板块字段用于判断
                '中文行业': info.get('industry', '未知'), # 稍后批量翻译
                '周期股': '⚠️是' if is_cyclical else '否',
                # 隐藏字段 (用于详情页备份)
                'longBusinessSummary': info.get('longBusinessSummary', '暂无简介'),
                'enterpriseValue': info.get('enterpriseValue', 0),
                'forwardPE': info.get('forwardPE', 0),
                'pegRatio': peg if peg is not None else 0,
                'priceToBook': info.get('priceToBook', 0),
                'dividendYield': info.get('dividendYield', 0),
                'marketCap': info.get('marketCap', 0),
                'trailingPE': info.get('trailingPE', 0),
                'returnOnEquity': info.get('returnOnEquity', 0),
                'debtToEquity': info.get('debtToEquity', 0),
                'grossMargins': info.get('grossMargins', 0),
                'profitMargins': info.get('profitMargins', 0),
                'freeCashFlow': fcf if fcf is not None else 0,
                'revenueGrowth': info.get('revenueGrowth', 0)
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
            cn_industry = industry_map.get(stock['行业'], stock['行业'])
            stock['中文行业'] = cn_industry
            # 公司名称逐个翻译，稍微慢点
            cn_name = translate_text(stock['名称'])
            stock['中文名称'] = cn_name
            
            # 合并 公司名称 和 行业
            stock['公司/行业'] = f"{cn_name} | {cn_industry}"

    status_text.text("分析完成！")
    progress_bar.empty()
    
    return pd.DataFrame(selected_stocks)


def main():
    # 初始化 session state (移到最前面，以便UI逻辑使用)
    if 'data' not in st.session_state:
        # 尝试加载缓存
        cached_df, last_updated = load_cache()
        
        # 检查缓存是否包含新添加的列，如果不包含则失效
        if cached_df is not None:
            required_cols = ['PEG', '净利率(%)', '自由现金流(亿)', '估值状态', '52周范围', 'PE/ROE', '负债/毛利', '公司/行业', '当前价格']
            if not all(col in cached_df.columns for col in required_cols):
                cached_df = None
                last_updated = None
                
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
             with st.expander("查看筛选标准与指标解读", expanded=False):
                st.markdown("""
                **筛选标准：**
                1. **高ROE**：净资产收益率 > 15%
                2. **低负债**：债务权益比 < 150%
                3. **高毛利**：毛利率 > 20% (原40%，适度放宽以包容零售/高周转行业)
                4. **合理估值**：市盈率(PE) < 35
                5. **真金白银**：自由现金流 > 0 (新增)
                6. **最终赚钱**：净利率 > 10% (新增)
                
                ---
                **🎓 指标小课堂**
                *   **PEG (市盈率/增长比)**：< 1 为低估，< 2 为合理。弥补了单纯看PE的缺陷，考虑了成长性。
                *   **FCF (自由现金流)**：公司真正能自由支配的现金。巴菲特最看重的“所有者盈余”。
                *   **净利率 (Net Margin)**：扣除所有成本（含税、利息）后剩下的钱。比毛利率更能反映最终盈利能力。
                *   **ROE (净资产收益率)**：>15% 说明公司用股东的钱赚钱能力很强。
                
                **🔄 关于周期股**
                *   表格中标记为“⚠️是”的属于周期性行业（如能源、原材料、金融）。
                *   **特点**：在经济繁荣时业绩极好（低PE、高ROE），经济衰退时业绩极差。
                *   **注意**：对于周期股，低市盈率往往是**卖出**信号（行业见顶），高市盈率往往是**买入**信号（行业见底）。请谨慎投资！
                """)
    else:
        st.caption("尚未获取数据")
    
    if start_btn:
        # 清除缓存，强制重新获取
        analyze_stocks.clear()
        
        with st.spinner('正在强制刷新数据并分析（这可能需要几分钟，请耐心等待）...'):
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
            
            # 给数值列加上颜色样式
            # 定义颜色映射
            # 蓝色: 价格, 市值 (基本面规模)
            # 紫色: PE (估值)
            # 绿色: ROE, 毛利率 (盈利能力)
            # 红色: 负债率 (风险)
            
            styled_df = df.style.applymap(lambda x: 'color: #2962FF; font-weight: 500;', subset=['当前价格', '52周最高', '52周最低', '市值(亿)', '自由现金流(亿)']) \
                                .applymap(lambda x: 'color: #6200EA; font-weight: 500;', subset=['市盈率(PE)', 'PEG']) \
                                .applymap(lambda x: 'color: #00C853; font-weight: 500;', subset=['ROE(%)', '毛利率(%)', '净利率(%)']) \
                                .applymap(lambda x: 'color: #D50000; font-weight: 500;', subset=['债务权益比(%)'])
            
            event = st.dataframe(
                styled_df,
                column_config={
                    "代码": "股票代码",
                    "公司/行业": st.column_config.TextColumn("公司 | 行业", width="medium"),
                    "估值状态": st.column_config.TextColumn("估值状态", help="基于PEG和营收增长判断：\n💰 低估：PEG < 1\n⚖️ 合理：1 < PEG < 2\n🏔️ 高估：PEG > 2\n📉 衰退：营收负增长"),
                    "当前价格": st.column_config.NumberColumn("价格($)", format="$%.2f"),
                    "52周范围": st.column_config.TextColumn("52周范围 (低 - 高)", width="medium"),
                    "PE/ROE": st.column_config.TextColumn("PE | ROE", width="medium"),
                    "PEG": st.column_config.NumberColumn("PEG", format="%.2f", help="市盈率相对盈利增长比率，<1通常为低估"),
                    "负债/毛利": st.column_config.TextColumn("负债 | 毛利", width="medium"),
                    "净利率(%)": st.column_config.NumberColumn("净利率", format="%.2f%%", help="净利润占营收的比例"),
                    "自由现金流(亿)": st.column_config.NumberColumn("FCF(亿)", format="$%.2f", help="自由现金流：巴菲特最看重的真金白银"),
                    "市值(亿)": st.column_config.NumberColumn("市值($亿)", format="$%.2f"),
                    "周期股": st.column_config.TextColumn("周期性?", help="周期性行业通常随经济周期波动较大"),
                },
                column_order=["代码", "公司/行业", "估值状态", "周期股", "当前价格", "52周范围", "PE/ROE", "PEG", "负债/毛利", "净利率(%)", "自由现金流(亿)", "市值(亿)"],
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

@st.dialog("股票详情", width="large")
def show_stock_details_dialog(ticker):
    # 自定义 CSS 来调整弹窗宽度
    # width="large" 通常很宽，这里通过 max-width 限制在 900px 左右 (比默认 large 窄一些，比 small 宽很多)
    st.markdown("""
        <style>
        div[role="dialog"][aria-modal="true"] {
            width: 80vw !important;
            max-width: 900px !important;
        }
        </style>
    """, unsafe_allow_html=True)
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

@st.cache_data(ttl=604800, show_spinner=False) # 缓存7天
def get_stock_details_cached(ticker):
    # 增加随机延迟
    time.sleep(random.uniform(0.1, 0.5))
    
    max_retries = 3
    for i in range(max_retries):
        try:
            stock = yf.Ticker(ticker)
            info = stock.info
            # 简单的有效性检查
            if info and 'currentPrice' in info:
                return info
        except Exception as e:
            if i < max_retries - 1:
                time.sleep(random.uniform(1, 3) * (i + 1))
            else:
                print(f"Failed to fetch details for {ticker}: {e}")
                
    # 尝试备用接口 (简单的页面请求测试)
    try:
        url = f"https://finance.yahoo.com/quote/{ticker}"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
        }
        resp = requests.get(url, headers=headers, timeout=5)
        if resp.status_code == 200:
            return {'__backup_mode__': True}
    except Exception:
        pass
        
    return None

def get_industry_averages(industry):
    if 'data' in st.session_state and st.session_state.data is not None:
        df = st.session_state.data
        # 筛选同行业
        industry_df = df[df['行业'] == industry]
        count = len(industry_df)
        if not industry_df.empty:
            avg_pe = industry_df['市盈率(PE)'].mean()
            avg_roe = industry_df['ROE(%)'].mean()
            avg_de = industry_df['债务权益比(%)'].mean()
            avg_margin = industry_df['毛利率(%)'].mean()
            return {
                'count': count,
                'avg_pe': f"{avg_pe:.2f}",
                'avg_roe': f"{avg_roe:.2f}%",
                'avg_de': f"{avg_de:.2f}%",
                'avg_margin': f"{avg_margin:.2f}%"
            }
    return {'count': 0}

def format_value(val, fmt="{:.2f}"):
    if val is None or val == 'N/A' or val == '':
        return "N/A"
    try:
        return fmt.format(float(val))
    except:
        return str(val)

def show_stock_details(ticker):
    try:
        # 1. 尝试获取详细信息 (带缓存)
        info = get_stock_details_cached(ticker)
        
        is_backup_mode = False
        
        # 2. 如果获取失败或处于备用模式，构造降级数据
        if not info or info.get('__backup_mode__'):
            is_backup_mode = True
            # 从 session_state 中恢复数据
            if 'data' in st.session_state and st.session_state.data is not None:
                df = st.session_state.data
                row = df[df['代码'] == ticker]
                if not row.empty:
                    row = row.iloc[0]
                    # 构造基础 info 对象
                    info = {
                        'shortName': row.get('名称', ticker),
                        'currentPrice': row.get('当前价格'),
                        'fiftyTwoWeekHigh': row.get('52周最高'),
                        'fiftyTwoWeekLow': row.get('52周最低'),
                        'marketCap': row.get('marketCap', row.get('市值(亿)', 0) * 100000000),
                        'trailingPE': row.get('trailingPE', row.get('市盈率(PE)')),
                        'forwardPE': row.get('forwardPE'),
                        'pegRatio': row.get('pegRatio'),
                        'priceToBook': row.get('priceToBook'),
                        'enterpriseValue': row.get('enterpriseValue'),
                        'returnOnEquity': row.get('returnOnEquity', row.get('ROE(%)', 0) / 100),
                        'debtToEquity': row.get('debtToEquity', row.get('债务权益比(%)')),
                        'grossMargins': row.get('grossMargins', row.get('毛利率(%)', 0) / 100),
                        'industry': row.get('行业'),
                        'longBusinessSummary': row.get('longBusinessSummary', '⚠️ 网络繁忙或API受限，当前显示为缓存的基础数据。详细简介暂时无法获取。'),
                        'dividendYield': row.get('dividendYield')
                    }
                else:
                    st.error("无法获取详情，且找不到缓存的基础数据。")
                    return
            else:
                st.error("无法获取详情 (API Rate Limit)。")
                return

        st.markdown(f"### {info.get('shortName')} ({ticker})")
        if is_backup_mode:
             st.warning("当前处于备用数据模式 (API限流保护)，已加载本地缓存的完整数据。")
        
        # 定义自定义指标组件 (带颜色)
        def custom_metric(label, value, color="#2962FF"):
            st.markdown(f"<div style='font-size: 14px; color: rgba(49, 51, 63, 0.6); margin-bottom: -10px;'>{label}</div>", unsafe_allow_html=True)
            st.markdown(f"<div style='font-size: 24px; font-weight: 600; color: {color}; overflow-wrap: break-word; line-height: 1.2; margin-bottom: 1rem;'>{value}</div>", unsafe_allow_html=True)

        col1, col2, col3 = st.columns(3)
        with col1:
            custom_metric("当前价格", f"${info.get('currentPrice', 0)}")
        with col2:
            custom_metric("52周最高", f"${info.get('fiftyTwoWeekHigh', 0)}")
        with col3:
            custom_metric("52周最低", f"${info.get('fiftyTwoWeekLow', 0)}")
            
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
                custom_metric("持仓数量", f"{shares:,} 股", color="#2962FF")
            with b_col2:
                custom_metric("当前持仓市值", market_value_str, color="#2962FF")
            with b_col3:
                custom_metric("估计成本", cost, color="#FF6D00") # 橙色显示成本
                
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

        # 计算行业均值
        industry = info.get('industry')
        avgs = get_industry_averages(industry) if industry else {'count': 0}
        
        count = avgs.get('count', 0)
        
        if count > 1:
            avg_col_name = f"同榜行业均值 (共{count}家)"
            avg_pe = avgs.get('avg_pe', '-')
            avg_roe = avgs.get('avg_roe', '-')
            avg_de = avgs.get('avg_de', '-')
            avg_margin = avgs.get('avg_margin', '-')
        else:
            avg_col_name = "同榜行业均值"
            avg_pe = "仅此一家入选"
            avg_roe = "仅此一家入选"
            avg_de = "仅此一家入选"
            avg_margin = "仅此一家入选"
        
        # 准备数据
        roe = info.get('returnOnEquity')
        roe_str = f"{roe * 100:.2f}%" if roe is not None else "N/A"
        
        de_ratio = info.get('debtToEquity')
        de_str = f"{de_ratio:.2f}%" if de_ratio is not None else "N/A"
        
        gross_margins = info.get('grossMargins')
        gm_str = f"{gross_margins * 100:.2f}%" if gross_margins is not None else "N/A"

        # 安全获取并格式化数值，防止 NoneType 错误
        market_cap_val = info.get('marketCap')
        market_cap_str = f"${market_cap_val:,}" if market_cap_val is not None else "N/A"

        ev_val = info.get('enterpriseValue')
        ev_str = f"${ev_val:,}" if ev_val is not None else "N/A"

        fin_data = {
            "指标": [
                "总市值", "企业价值", "静态市盈率 (TTM)", "预测市盈率 (Forward)", "PEG 比率", 
                "市净率 (P/B)", "股息率", "ROE (净资产收益率)", "负债权益比 (负债率)", "毛利率"
            ],
            "数值": [
                market_cap_str,
                ev_str,
                format_value(info.get('trailingPE')),
                format_value(info.get('forwardPE')),
                format_value(info.get('pegRatio')),
                format_value(info.get('priceToBook')),
                div_yield_str,
                roe_str,
                de_str,
                gm_str
            ],
            avg_col_name: [
                "", "", avg_pe, "", "", 
                "", "", avg_roe, avg_de, avg_margin
            ]
        }
        
        fin_df = pd.DataFrame(fin_data)
        
        # 定义每一行的颜色样式
        # 0: 总市值 (蓝)
        # 1: 企业价值 (蓝)
        # 2-5: 估值指标 PE, PEG, PB (紫)
        # 6: 股息率 (绿)
        # 7: ROE (绿)
        # 8: 负债率 (红)
        # 9: 毛利率 (绿)
        
        def highlight_metrics(row):
            styles = [''] * len(row) # 初始化样式列表
            idx = row.name # 获取行索引
            
            color = 'black'
            if idx in [0, 1]:
                color = '#2962FF' # 蓝
            elif idx in [2, 3, 4, 5]:
                color = '#6200EA' # 紫
            elif idx in [6, 7, 9]:
                color = '#00C853' # 绿
            elif idx == 8:
                color = '#D50000' # 红
            
            # 应用颜色到数值列 (第1列和第2列，索引为1和2)
            # pandas series index: 0=指标, 1=数值, 2=avg_col_name
            styles[1] = f'color: {color}; font-weight: 500;'
            styles[2] = f'color: {color}; font-weight: 500;'
            
            return styles

        # 使用 apply 对每一行应用样式
        styled_fin_df = fin_df.style.apply(highlight_metrics, axis=1)
        st.table(styled_fin_df)
        
    except Exception as e:
        st.error(f"无法获取详情: {e}")

if __name__ == "__main__":
    main()
