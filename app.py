import streamlit as st
import pandas as pd
import plotly.express as px
import sqlite3
from pathlib import Path

st.set_page_config(page_title="داشبورد ارزش بازار دلاری", layout="wide")

st.markdown("""
<style>
html, body, [class*="css"], .stApp {font-family:Tahoma,Arial,sans-serif!important}
.stApp {direction:rtl}
[data-testid="stSidebar"] {direction:rtl}
[data-testid="stSidebar"] * {text-align:right}
.block-container {max-width:1450px;padding-top:2rem;padding-bottom:3rem}
h1 {font-size:2.25rem!important;font-weight:800!important}
[data-testid="stMetric"] {background:white;border:1px solid #e5e7eb;border-radius:15px;padding:16px 18px;box-shadow:0 2px 8px rgba(0,0,0,.04)}
[data-testid="stMetricLabel"] {font-size:.92rem!important;font-weight:600!important}
[data-testid="stMetricValue"] {font-size:1.55rem!important;font-weight:800!important;direction:ltr;text-align:right}
/* Keep Streamlit's own navigation/header controls LTR so mobile icons don't collapse. */
[data-testid="stHeader"], [data-testid="stToolbar"], [data-testid="stDecoration"], [data-testid="stStatusWidget"] {direction:ltr!important}
@media (max-width: 768px) {
  .block-container {padding:1rem .9rem 2rem!important}
  h1 {font-size:1.75rem!important;line-height:1.5!important}
  [data-testid="stMetric"] {padding:12px 14px!important}
  [data-testid="stMetricValue"] {font-size:1.35rem!important}
  [data-testid="stPlotlyChart"] {direction:ltr!important}
}
</style>
""",unsafe_allow_html=True)

DB_PATH=Path(__file__).with_name("market_data_fundamental.db")

def db_connect_readonly():
    return sqlite3.connect(f"file:{DB_PATH.as_posix()}?mode=ro", uri=True)

def norm(x):
    if pd.isna(x): return ""
    return str(x).strip().replace("ي","ی").replace("ك","ک").replace("\u200c"," ")

def header_row(raw,terms):
    for i in range(min(40,len(raw))):
        text=" | ".join(norm(x) for x in raw.iloc[i].tolist())
        if all(t in text for t in terms): return i
    return None

def make_df(raw,h):
    cols=[norm(x) if norm(x) else f"col_{j}" for j,x in enumerate(raw.iloc[h])]
    d=raw.iloc[h+1:].copy(); d.columns=cols
    return d.reset_index(drop=True)

def find_col(cols,*terms):
    for c in cols:
        s=norm(c)
        if all(t in s for t in terms): return c
    return None

def date_key(s):
    return pd.to_datetime(s,errors="coerce").dt.strftime("%Y-%m-%d")

def parse_fx(uploaded):
    uploaded.seek(0); raw=pd.read_excel(uploaded,header=None)
    h=header_row(raw,["تاریخ میلادی","دلار آزاد"])
    if h is None: raise ValueError("سطر عنوان فایل دلار پیدا نشد.")
    d=make_df(raw,h)
    g=find_col(d.columns,"تاریخ میلادی"); sh=find_col(d.columns,"تاریخ شمسی")
    fr=find_col(d.columns,"دلار آزاد"); ni=find_col(d.columns,"دلار نیمایی") or find_col(d.columns,"نیما")
    if g is None or fr is None or ni is None: raise ValueError("ستون‌های لازم فایل دلار پیدا نشدند.")
    o=pd.DataFrame({"gregorian_date":date_key(d[g]),"shamsi_date":d[sh].map(norm) if sh else "",
                    "usd_free":pd.to_numeric(d[fr],errors="coerce"),
                    "usd_nima":pd.to_numeric(d[ni],errors="coerce")})
    return o.dropna(subset=["gregorian_date","usd_free","usd_nima"]).drop_duplicates("gregorian_date",keep="last")

def parse_stock(uploaded):
    uploaded.seek(0); raw=pd.read_excel(uploaded,header=None)
    h=header_row(raw,["تاریخ میلادی","ارزش بازار"])
    if h is None: raise ValueError(f"{uploaded.name}: جدول قیمت پیدا نشد.")
    identity=""
    for i in range(max(0,h-6),h):
        vals=[norm(x) for x in raw.iloc[i].tolist() if norm(x)]
        for v in vals:
            if v not in ["Pouya Finance","تاریخچه قیمت"] and "Copyright" not in v:
                identity=v
    if "-" in identity: symbol,company=identity.split("-",1)
    else: symbol,company=identity,identity
    symbol,company=norm(symbol),norm(company)
    d=make_df(raw,h)
    g=find_col(d.columns,"تاریخ میلادی"); sh=find_col(d.columns,"تاریخ شمسی"); mc=find_col(d.columns,"ارزش بازار")
    if g is None or sh is None or mc is None: raise ValueError(f"{uploaded.name}: تاریخ/ارزش بازار پیدا نشد.")
    o=pd.DataFrame({"symbol":symbol or Path(uploaded.name).stem,"company_name":company,
                    "gregorian_date":date_key(d[g]),"shamsi_date":d[sh].map(norm),
                    "market_cap_rial":pd.to_numeric(d[mc],errors="coerce")})
    return o.dropna(subset=["gregorian_date","market_cap_rial"])

def ensure_db():
    if not DB_PATH.exists():
        st.error("فایل market_data.db کنار app.py پیدا نشد.")
        st.stop()

@st.cache_data(show_spinner=False)
def load_summary(db_mtime):
    with db_connect_readonly() as con:
        return pd.read_sql_query("""
        WITH x AS (
          SELECT *, ROW_NUMBER() OVER(PARTITION BY symbol ORDER BY gregorian_date DESC) rn_last,
                    ROW_NUMBER() OVER(PARTITION BY symbol ORDER BY gregorian_date ASC) rn_first
          FROM market_cap_usd
        ),
        agg AS (
          SELECT symbol, company_name,
                 MAX(market_cap_usd_free) peak,
                 MIN(market_cap_usd_free) trough,
                 COUNT(*) n
          FROM market_cap_usd GROUP BY symbol,company_name
        )
        SELECT a.symbol AS "نماد", a.company_name AS "نام شرکت",
               f.shamsi_date AS "شروع داده", l.shamsi_date AS "آخرین تاریخ",
               l.market_cap_usd_free/1000000.0 AS "ارزش فعلی (M$)",
               a.peak/1000000.0 AS "سقف تاریخی (M$)",
               a.trough/1000000.0 AS "کف تاریخی (M$)",
               (l.market_cap_usd_free/a.peak-1)*100.0 AS "فاصله از سقف (%)",
               a.n AS "تعداد روز داده"
        FROM agg a
        JOIN x f ON f.symbol=a.symbol AND f.rn_first=1
        JOIN x l ON l.symbol=a.symbol AND l.rn_last=1
        ORDER BY "ارزش فعلی (M$)" DESC
        """,con)

@st.cache_data(show_spinner=False)
def load_symbol(symbol, db_mtime):
    with db_connect_readonly() as con:
        return pd.read_sql_query("""
        SELECT symbol AS Symbol, company_name AS Company, gregorian_date AS Date_Gregorian,
               shamsi_date AS Date_Shamsi, market_cap_rial AS MarketCap_Rial,
               usd_free AS USD_Free, usd_nima AS USD_NIMA,
               market_cap_usd_free AS MarketCap_USD_Free,
               market_cap_usd_nima AS MarketCap_USD_NIMA
        FROM market_cap_usd WHERE symbol=? ORDER BY gregorian_date
        """,con,params=(symbol,))


@st.cache_data(show_spinner=False)
def load_fundamental_ratios(symbol, db_mtime):
    # Build the balance-sheet ratios from standardized fundamental observations.
    # This avoids depending on convenience-view column names and keeps the app
    # compatible with the normalized fundamental schema.
    with db_connect_readonly() as con:
        raw=pd.read_sql_query("""
        SELECT o.period_end_month AS Period, m.standard_code AS Code, o.value AS Value
        FROM fundamental_observations o
        JOIN fundamental_metrics m ON m.metric_id=o.metric_id
        WHERE o.symbol=? AND o.period_type='quarterly' AND m.standard_code IN
        ('inventory','trade_and_other_receivables','cash','short_term_investments',
         'current_assets','current_liabilities','current_financial_debt',
         'long_term_financial_debt','equity','total_assets','total_liabilities')
        ORDER BY o.period_end_month
        """,con,params=(symbol,))
        market=pd.read_sql_query("""
        SELECT period_end_month AS Period, market_date_shamsi AS MarketDate,
               market_cap_rial/1000000.0 AS MarketCap_MRial
        FROM v_key_fundamental_ratios
        WHERE symbol=? ORDER BY period_end_month
        """,con,params=(symbol,))
    if raw.empty or market.empty:
        return pd.DataFrame()
    d=raw.pivot_table(index='Period',columns='Code',values='Value',aggfunc='last').reset_index()
    d=market.merge(d,on='Period',how='left').sort_values('Period')
    needed=['inventory','trade_and_other_receivables','cash','short_term_investments',
            'current_assets','current_liabilities','current_financial_debt',
            'long_term_financial_debt','equity','total_assets','total_liabilities']
    for c in needed:
        if c not in d.columns: d[c]=pd.NA
    d=d.rename(columns={'trade_and_other_receivables':'receivables'})
    mc=pd.to_numeric(d['MarketCap_MRial'],errors='coerce').replace(0,pd.NA)
    d['inventory_to_mcap']=pd.to_numeric(d['inventory'],errors='coerce')/mc
    d['receivables_to_mcap']=pd.to_numeric(d['receivables'],errors='coerce')/mc
    d['cash_to_mcap']=pd.to_numeric(d['cash'],errors='coerce')/mc
    d['current_assets_to_mcap']=pd.to_numeric(d['current_assets'],errors='coerce')/mc
    d['working_capital_to_mcap']=(pd.to_numeric(d['current_assets'],errors='coerce')-pd.to_numeric(d['current_liabilities'],errors='coerce'))/mc
    d['equity_to_mcap']=pd.to_numeric(d['equity'],errors='coerce')/mc
    return d

@st.cache_data(show_spinner=False)
def load_fundamental_ttm(symbol, db_mtime):
    with db_connect_readonly() as con:
        raw=pd.read_sql_query("""
        SELECT o.period_end_month AS Period, m.standard_code AS Code, o.value AS Value
        FROM fundamental_observations o JOIN fundamental_metrics m ON m.metric_id=o.metric_id
        WHERE o.symbol=? AND o.period_type='quarterly' AND m.standard_code IN
        ('revenue','gross_profit','operating_profit','net_profit','operating_cash_flow')
        ORDER BY o.period_end_month
        """,con,params=(symbol,))
    if raw.empty: return pd.DataFrame()
    d=raw.pivot_table(index='Period',columns='Code',values='Value',aggfunc='last').reset_index().sort_values('Period')
    for c in ['revenue','gross_profit','operating_profit','net_profit','operating_cash_flow']:
        if c not in d: d[c]=pd.NA
        d[c+'_ttm']=d[c].rolling(4,min_periods=4).sum()
    d['gross_margin_ttm']=d['gross_profit_ttm']/d['revenue_ttm']
    d['operating_margin_ttm']=d['operating_profit_ttm']/d['revenue_ttm']
    d['net_margin_ttm']=d['net_profit_ttm']/d['revenue_ttm']
    d['cfo_to_net_profit_ttm']=d['operating_cash_flow_ttm']/d['net_profit_ttm']
    return d

def db_mtime():
    return DB_PATH.stat().st_mtime_ns

ensure_db()
st.title("📊 داشبورد ارزش بازار دلاری سهام")
st.markdown("نسخه آنلاین فقط‌خواندنی — اطلاعات سهام مستقیماً از پایگاه داده **SQLite** خوانده می‌شود.")

with st.sidebar:
    st.header("پایگاه داده")
    with db_connect_readonly() as con:
        nstocks=con.execute("SELECT COUNT(*) FROM stocks").fetchone()[0]
        nrows=con.execute("SELECT COUNT(*) FROM stock_history").fetchone()[0]
    st.success(f"{nstocks} نماد | {nrows:,} رکورد")
    page=st.radio("بخش",["تحلیل سهم","تحلیل بنیادی تاریخی","نمای کلی بازار","مقایسه سهم‌ها"])

summary=load_summary(db_mtime())

if page=="تحلیل سهم":
    options={f'{r["نماد"]} — {r["نام شرکت"]}':r["نماد"] for _,r in summary.iterrows()}
    q=st.text_input("جستجوی نماد یا نام شرکت",placeholder="مثلاً فاسمین یا کالسیمین")
    labels=[k for k in options if norm(q) in norm(k)] if q else list(options)
    if not labels: st.warning("سهمی با این عبارت پیدا نشد."); st.stop()
    label=st.selectbox("انتخاب سهم",labels)
    sym=options[label]; d=load_symbol(sym,db_mtime())
    last=d.iloc[-1]; peak=d["MarketCap_USD_Free"].max()
    c1,c2,c3,c4=st.columns(4)
    c1.metric("آخرین تاریخ",last["Date_Shamsi"])
    c2.metric("ارزش بازار │ دلار آزاد",f'{last["MarketCap_USD_Free"]/1e6:,.1f} M$')
    c3.metric("ارزش بازار │ دلار نیمایی",f'{last["MarketCap_USD_NIMA"]/1e6:,.1f} M$')
    c4.metric("فاصله از سقف",f'{(last["MarketCap_USD_Free"]/peak-1)*100:,.1f}%')
    p=d[["Date_Shamsi","MarketCap_USD_Free","MarketCap_USD_NIMA"]].copy()
    p["دلار آزاد"]=p["MarketCap_USD_Free"]/1e6; p["دلار نیمایی"]=p["MarketCap_USD_NIMA"]/1e6
    long=p.melt(id_vars="Date_Shamsi",value_vars=["دلار آزاد","دلار نیمایی"],var_name="مبنای ارز",value_name="ارزش بازار (میلیون دلار)")
    fig=px.line(long,x="Date_Shamsi",y="ارزش بازار (میلیون دلار)",color="مبنای ارز",title=f'{sym} — {last["Company"]}')
    fig.update_layout(height=540,hovermode="x unified",legend_title_text="",xaxis_title="تاریخ شمسی",yaxis_title="میلیون دلار")
    fig.update_xaxes(nticks=14); st.plotly_chart(fig,use_container_width=True)
    free=d["MarketCap_USD_Free"]/1e6
    imax=d["MarketCap_USD_Free"].idxmax(); imin=d["MarketCap_USD_Free"].idxmin()
    a,b,c,e=st.columns(4)
    a.metric("کف تاریخی",f"{free.min():,.1f} M$")
    b.metric("تاریخ کف",d.loc[imin,"Date_Shamsi"])
    c.metric("سقف تاریخی",f"{free.max():,.1f} M$")
    e.metric("تاریخ سقف",d.loc[imax,"Date_Shamsi"])


elif page=="تحلیل بنیادی تاریخی":
    st.subheader("تحلیل بنیادی تاریخی")
    with db_connect_readonly() as con:
        fsyms=[r[0] for r in con.execute("SELECT DISTINCT symbol FROM fundamental_observations ORDER BY symbol").fetchall()]
    if not fsyms:
        st.info("هنوز داده بنیادی برای هیچ نمادی ثبت نشده است.")
        st.stop()
    sym=st.selectbox("انتخاب سهم",fsyms)
    ratios=load_fundamental_ratios(sym,db_mtime())
    ttm=load_fundamental_ttm(sym,db_mtime())
    if ratios.empty:
        st.warning("برای این سهم نسبت بنیادی قابل محاسبه وجود ندارد.")
        st.stop()
    metric_map={
      "موجودی / ارزش بازار":"inventory_to_mcap",
      "دریافتنی / ارزش بازار":"receivables_to_mcap",
      "وجه نقد / ارزش بازار":"cash_to_mcap",
      "دارایی جاری / ارزش بازار":"current_assets_to_mcap",
      "سرمایه در گردش / ارزش بازار":"working_capital_to_mcap",
      "حقوق صاحبان سهام / ارزش بازار":"equity_to_mcap"}
    selected=st.multiselect("متغیرهای ترازنامه‌ای",list(metric_map),default=["موجودی / ارزش بازار","دریافتنی / ارزش بازار","سرمایه در گردش / ارزش بازار"])
    if selected:
        pp=ratios[["Period"]+[metric_map[x] for x in selected]].rename(columns={v:k for k,v in metric_map.items()})
        long=pp.melt(id_vars="Period",var_name="متغیر",value_name="نسبت")
        fig=px.line(long,x="Period",y="نسبت",color="متغیر",markers=True,title=f"{sym} — نسبت‌های بنیادی به ارزش بازار")
        fig.update_layout(height=520,hovermode="x unified",legend_title_text="",yaxis_tickformat=".0%",xaxis_title="دوره مالی",yaxis_title="نسبت")
        fig.update_xaxes(nticks=14); st.plotly_chart(fig,use_container_width=True)
    last=ratios.iloc[-1]
    c1,c2,c3,c4=st.columns(4)
    c1.metric("آخرین دوره",last["Period"])
    c2.metric("موجودی / ارزش بازار",f'{last["inventory_to_mcap"]:.1%}' if pd.notna(last["inventory_to_mcap"]) else "—")
    c3.metric("دریافتنی / ارزش بازار",f'{last["receivables_to_mcap"]:.1%}' if pd.notna(last["receivables_to_mcap"]) else "—")
    c4.metric("سرمایه در گردش / ارزش بازار",f'{last["working_capital_to_mcap"]:.1%}' if pd.notna(last["working_capital_to_mcap"]) else "—")
    if not ttm.empty and ttm['revenue_ttm'].notna().any():
        st.markdown("### کیفیت سودآوری — چهار فصل متحرک")
        mm={"حاشیه ناخالص TTM":"gross_margin_ttm","حاشیه عملیاتی TTM":"operating_margin_ttm","حاشیه خالص TTM":"net_margin_ttm"}
        z=ttm[["Period"]+list(mm.values())].rename(columns={v:k for k,v in mm.items()}).melt(id_vars="Period",var_name="متغیر",value_name="نسبت").dropna()
        fig2=px.line(z,x="Period",y="نسبت",color="متغیر",markers=True)
        fig2.update_layout(height=450,hovermode="x unified",legend_title_text="",yaxis_tickformat=".0%",xaxis_title="دوره مالی",yaxis_title="حاشیه سود")
        fig2.update_xaxes(nticks=14); st.plotly_chart(fig2,use_container_width=True)
        z2=ttm.dropna(subset=['cfo_to_net_profit_ttm'])
        if not z2.empty:
            fig3=px.line(z2,x="Period",y="cfo_to_net_profit_ttm",markers=True,title="جریان نقد عملیاتی / سود خالص — TTM")
            fig3.update_layout(height=400,hovermode="x unified",xaxis_title="دوره مالی",yaxis_title="نسبت")
            fig3.update_xaxes(nticks=14); st.plotly_chart(fig3,use_container_width=True)
    st.caption("اقلام ترازنامه‌ای با ارزش بازار آخرین روز معاملاتی موجود تا پایان همان دوره مالی مقایسه شده‌اند. نسبت‌های سودآوری بر مبنای چهار فصل متحرک محاسبه می‌شوند.")

elif page=="نمای کلی بازار":
    st.subheader("نمای کلی سهام موجود در پایگاه داده")
    st.caption("«فعلی» برای هر سهم یعنی آخرین روز معاملاتی موجود همان سهم.")
    show=summary.copy()
    for c in ["ارزش فعلی (M$)","سقف تاریخی (M$)","کف تاریخی (M$)","فاصله از سقف (%)"]: show[c]=show[c].round(1)
    st.dataframe(show,use_container_width=True,hide_index=True,height=600)
    st.download_button("دانلود جدول نمای کلی (CSV)",show.to_csv(index=False).encode("utf-8-sig"),"market_overview.csv","text/csv")

elif page=="مقایسه سهم‌ها":
    choices={f'{r["نماد"]} — {r["نام شرکت"]}':r["نماد"] for _,r in summary.iterrows()}
    picked=st.multiselect("سهم‌ها را برای مقایسه انتخاب کنید",list(choices),max_selections=8)
    if picked:
        syms=[choices[x] for x in picked]
        parts=[load_symbol(s,db_mtime()) for s in syms]
        cmp=pd.concat(parts,ignore_index=True)
        cmp["ارزش بازار (میلیون دلار)"]=cmp["MarketCap_USD_Free"]/1e6
        fig=px.line(cmp,x="Date_Shamsi",y="ارزش بازار (میلیون دلار)",color="Symbol",title="مقایسه ارزش بازار دلاری — دلار آزاد")
        fig.update_layout(height=560,hovermode="x unified",legend_title_text="نماد",xaxis_title="تاریخ شمسی")
        fig.update_xaxes(nticks=14); st.plotly_chart(fig,use_container_width=True)
        st.caption("هر خط فقط در روزهایی رسم شده که همان سهم داده معاملاتی دارد.")


st.caption("نسخه عمومی فقط‌خواندنی است. داده‌های دلاری هنگام نمایش از اتصال ارزش بازار ریالی با نرخ ارز همان تاریخ محاسبه می‌شوند.")
