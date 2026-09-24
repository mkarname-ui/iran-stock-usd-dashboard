import streamlit as st
import pandas as pd
import plotly.express as px
import sqlite3
from pathlib import Path
import tempfile
import os

st.set_page_config(page_title="داشبورد ارزش بازار دلاری", layout="wide")

st.markdown("""
<style>
html,body,[class*="css"],.stApp{font-family:Tahoma,Arial,sans-serif!important}
.stApp{direction:rtl}[data-testid="stSidebar"]{direction:rtl}
[data-testid="stSidebar"] *{text-align:right}
.block-container{max-width:1450px;padding-top:2rem;padding-bottom:3rem}
h1{font-size:2.25rem!important;font-weight:800!important}
[data-testid="stMetric"]{background:white;border:1px solid #e5e7eb;border-radius:15px;padding:16px 18px;box-shadow:0 2px 8px rgba(0,0,0,.04)}
[data-testid="stMetricLabel"]{font-size:.92rem!important;font-weight:600!important}
[data-testid="stMetricValue"]{font-size:1.55rem!important;font-weight:800!important;direction:ltr;text-align:right}
</style>
""",unsafe_allow_html=True)

DB_PATH=Path(__file__).with_name("market_data.db")
FUND_DB_PATH=Path(__file__).with_name("market_data_fundamental.db")

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
    with sqlite3.connect(DB_PATH) as con:
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
    with sqlite3.connect(DB_PATH) as con:
        return pd.read_sql_query("""
        SELECT symbol AS Symbol, company_name AS Company, gregorian_date AS Date_Gregorian,
               shamsi_date AS Date_Shamsi, market_cap_rial AS MarketCap_Rial,
               usd_free AS USD_Free, usd_nima AS USD_NIMA,
               market_cap_usd_free AS MarketCap_USD_Free,
               market_cap_usd_nima AS MarketCap_USD_NIMA
        FROM market_cap_usd WHERE symbol=? ORDER BY gregorian_date
        """,con,params=(symbol,))

def db_mtime():
    return DB_PATH.stat().st_mtime_ns

ensure_db()
st.title("📊 داشبورد ارزش بازار دلاری سهام")
st.markdown("نسخه آنلاین فقط‌خواندنی — اطلاعات بازار و داده‌های بنیادی مستقیماً از پایگاه داده **SQLite** خوانده می‌شوند.")

with st.sidebar:
    st.header("پایگاه داده")
    with sqlite3.connect(DB_PATH) as con:
        nstocks=con.execute("SELECT COUNT(*) FROM stocks").fetchone()[0]
        nrows=con.execute("SELECT COUNT(*) FROM stock_history").fetchone()[0]
    st.success(f"{nstocks} نماد | {nrows:,} رکورد")
    pages=["تحلیل سهم","نمای کلی بازار","مقایسه سهم‌ها"]
    if FUND_DB_PATH.exists(): pages.append("تحلیل بنیادی")
    page=st.radio("بخش",pages)

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
    p=d[["Date_Gregorian","Date_Shamsi","MarketCap_USD_Free","MarketCap_USD_NIMA"]].copy()
    p["Date_Gregorian"]=pd.to_datetime(p["Date_Gregorian"],errors="coerce")
    p["دلار آزاد"]=p["MarketCap_USD_Free"]/1e6; p["دلار نیمایی"]=p["MarketCap_USD_NIMA"]/1e6
    long=p.melt(id_vars=["Date_Gregorian","Date_Shamsi"],value_vars=["دلار آزاد","دلار نیمایی"],var_name="مبنای ارز",value_name="ارزش بازار (میلیون دلار)")
    long=long.sort_values("Date_Gregorian")
    fig=px.line(long,x="Date_Gregorian",y="ارزش بازار (میلیون دلار)",color="مبنای ارز",hover_data={"Date_Shamsi":True,"Date_Gregorian":False},title=f'{sym} — {last["Company"]}')
    fig.update_layout(height=540,hovermode="x unified",legend_title_text="",xaxis_title="تاریخ",yaxis_title="میلیون دلار")
    fig.update_xaxes(nticks=14); st.plotly_chart(fig,use_container_width=True)
    free=d["MarketCap_USD_Free"]/1e6
    imax=d["MarketCap_USD_Free"].idxmax(); imin=d["MarketCap_USD_Free"].idxmin()
    a,b,c,e=st.columns(4)
    a.metric("کف تاریخی",f"{free.min():,.1f} M$")
    b.metric("تاریخ کف",d.loc[imin,"Date_Shamsi"])
    c.metric("سقف تاریخی",f"{free.max():,.1f} M$")
    e.metric("تاریخ سقف",d.loc[imax,"Date_Shamsi"])

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
        cmp["Date_Gregorian"]=pd.to_datetime(cmp["Date_Gregorian"],errors="coerce")
        cmp=cmp.sort_values(["Symbol","Date_Gregorian"])
        fig=px.line(cmp,x="Date_Gregorian",y="ارزش بازار (میلیون دلار)",color="Symbol",hover_data={"Date_Shamsi":True,"Date_Gregorian":False},title="مقایسه ارزش بازار دلاری — دلار آزاد")
        fig.update_layout(height=560,hovermode="x unified",legend_title_text="نماد",xaxis_title="تاریخ شمسی")
        fig.update_xaxes(nticks=14); st.plotly_chart(fig,use_container_width=True)
        st.caption("هر خط فقط در روزهایی رسم شده که همان سهم داده معاملاتی دارد.")

elif page=="تحلیل بنیادی":
    st.subheader("تحلیل بنیادی تاریخی")
    if not FUND_DB_PATH.exists():
        st.warning("فایل market_data_fundamental.db کنار app.py پیدا نشد.")
        st.stop()
    with sqlite3.connect(FUND_DB_PATH) as fcon:
        fsymbols=pd.read_sql_query("SELECT symbol,company_name FROM companies ORDER BY symbol",fcon)
        labels={f'{r.symbol} — {r.company_name or ""}':r.symbol for _,r in fsymbols.iterrows()}
        flabel=st.selectbox("انتخاب شرکت",list(labels),key="fund_symbol")
        fsym=labels[flabel]
        raw=pd.read_sql_query("""
          SELECT period_label,period_end_shamsi,publication_date_raw,source_type,
                 source_label,canonical_code,canonical_name_fa,value_numeric
          FROM v_fundamentals WHERE symbol=? AND value_numeric IS NOT NULL
          ORDER BY period_end_shamsi
        """,fcon,params=(fsym,))
    if raw.empty:
        st.info("برای این شرکت هنوز داده بنیادی ثبت نشده است.")
        st.stop()

    # انتخاب متغیرهای استانداردشده؛ اقلام خام همچنان در دیتابیس حفظ شده‌اند.
    canon=raw[raw["canonical_code"].notna()].copy()
    if canon.empty:
        st.info("هنوز متغیر استانداردشده‌ای برای این شرکت تعریف نشده است.")
        st.stop()
    metric_map=(canon[["canonical_code","canonical_name_fa"]].drop_duplicates()
                .set_index("canonical_code")["canonical_name_fa"].to_dict())
    selected=st.multiselect("متغیرهای بنیادی",list(metric_map),
        default=[x for x in ["inventory","sales"] if x in metric_map],
        format_func=lambda x: metric_map.get(x,x))
    if not selected:
        st.info("حداقل یک متغیر بنیادی انتخاب کنید.")
        st.stop()
    f=canon[canon["canonical_code"].isin(selected)].copy()
    # تاریخ شمسی فقط برای برچسب است؛ محور نمودار با ترتیب واقعی دوره‌ها ساخته می‌شود.
    def sh_key(x):
        try:
            y,m,d=[int(z) for z in str(x).replace('-','/').split('/')[:3]]
            return y*10000+m*100+d
        except: return None
    f["period_key"]=f["period_end_shamsi"].map(sh_key)
    f=f.dropna(subset=["period_key"]).sort_values(["canonical_code","period_key"])
    f["متغیر"]=f["canonical_code"].map(metric_map)
    fig=px.line(f,x="period_key",y="value_numeric",color="متغیر",markers=True,
                hover_data={"period_end_shamsi":True,"period_label":True,"publication_date_raw":True,"period_key":False},
                title=f"{fsym} — روند متغیرهای بنیادی")
    ticks=f[["period_key","period_end_shamsi"]].drop_duplicates().sort_values("period_key")
    step=max(1,len(ticks)//12)
    ticks=ticks.iloc[::step]
    fig.update_xaxes(tickmode="array",tickvals=ticks["period_key"],ticktext=ticks["period_end_shamsi"],title="دوره مالی")
    fig.update_layout(height=540,hovermode="x unified",legend_title_text="",yaxis_title="مقدار گزارش‌شده")
    st.plotly_chart(fig,use_container_width=True)

    latest=(f.sort_values("period_key").groupby("canonical_code",as_index=False).tail(1))
    cols=st.columns(min(4,len(latest)))
    for i,(_,r) in enumerate(latest.iterrows()):
        cols[i%len(cols)].metric(metric_map.get(r["canonical_code"],r["canonical_code"]),f'{r["value_numeric"]:,.0f}',r["period_end_shamsi"])

    st.caption("اعداد این بخش همان مقادیر گزارش‌شده در فایل‌های بنیادی هستند؛ واحد یا مقیاس منبع به‌صورت خودکار تغییر داده نشده است.")
    with st.expander("مشاهده داده‌های استفاده‌شده"):
        show=f[["period_end_shamsi","period_label","متغیر","source_label","value_numeric","publication_date_raw"]].copy()
        show.columns=["پایان دوره","عنوان دوره","متغیر","عنوان اصلی منبع","مقدار","تاریخ انتشار منبع"]
        st.dataframe(show,use_container_width=True,hide_index=True)

st.caption("داده‌های دلاری هنگام نمایش از اتصال ارزش بازار ریالی با نرخ ارز همان تاریخ محاسبه می‌شوند.")
