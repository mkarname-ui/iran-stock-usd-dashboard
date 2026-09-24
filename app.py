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
html, body, [class*="css"], .stApp {font-family:Tahoma,Arial,sans-serif!important}
.block-container {max-width:1450px;padding-top:1.4rem;padding-bottom:3rem}
h1,h2,h3,p,[data-testid="stMarkdownContainer"] {text-align:right}
[data-testid="stWidgetLabel"] {text-align:right}
[data-testid="stSidebar"] {direction:rtl}
[data-testid="stSidebar"] * {text-align:right}
[data-testid="stMetric"] {background:var(--secondary-background-color);color:var(--text-color);border:1px solid rgba(128,128,128,.28);border-radius:15px;padding:16px 18px;box-shadow:0 2px 8px rgba(0,0,0,.08);direction:rtl}
[data-testid="stMetric"] * {color:var(--text-color)!important}
[data-testid="stMetricLabel"] {font-size:.92rem!important;font-weight:600!important}
[data-testid="stMetricValue"] {font-size:1.55rem!important;font-weight:800!important;direction:ltr;text-align:right}
/* نمودارها و جدول‌ها عمداً LTR می‌مانند؛ RTL سراسری در موبایل باعث نوارهای سبز/متن عمودی می‌شد. */
[data-testid="stPlotlyChart"],[data-testid="stDataFrame"] {direction:ltr!important;max-width:100%!important}
[data-testid="stHeader"],[data-testid="stToolbar"],[data-testid="stDecoration"],[data-testid="stStatusWidget"] {direction:ltr!important}
@media (max-width:768px) {
  .block-container {padding:.8rem .75rem 2rem!important;overflow-x:hidden!important}
  h1 {font-size:1.65rem!important;line-height:1.55!important}
  [data-testid="stMetric"] {padding:12px 14px!important}
  [data-testid="stMetricValue"] {font-size:1.35rem!important}
  [data-testid="stPlotlyChart"] {overflow:hidden!important;width:100%!important}
}
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
          SELECT *,
                 ROW_NUMBER() OVER(PARTITION BY symbol ORDER BY gregorian_date DESC) rn_last,
                 ROW_NUMBER() OVER(PARTITION BY symbol ORDER BY gregorian_date ASC) rn_first
          FROM market_cap_usd
        ),
        post96 AS (
          SELECT * FROM market_cap_usd
          WHERE CAST(substr(replace(shamsi_date,'-','/'),1,4) AS INTEGER) >= 1396
            AND CAST(substr(replace(shamsi_date,'-','/'),1,4) AS INTEGER) <= 1404
            AND market_cap_usd_free > 0
        ),
        floors AS (
          SELECT p.symbol, p.market_cap_usd_free AS floor96, p.shamsi_date AS floor96_date,
                 ROW_NUMBER() OVER(PARTITION BY p.symbol ORDER BY p.market_cap_usd_free ASC, p.gregorian_date ASC) rn
          FROM post96 p
        ),
        agg AS (
          SELECT symbol, company_name, MAX(market_cap_usd_free) peak, COUNT(*) n
          FROM market_cap_usd GROUP BY symbol,company_name
        )
        SELECT a.symbol AS "نماد", a.company_name AS "نام شرکت",
               f.shamsi_date AS "شروع داده", l.shamsi_date AS "آخرین تاریخ",
               l.market_cap_usd_free/1000000.0 AS "ارزش فعلی (M$)",
               a.peak/1000000.0 AS "سقف تاریخی (M$)",
               fl.floor96/1000000.0 AS "کف ۱۳۹۶–۱۴۰۴ (M$)",
               fl.floor96_date AS "تاریخ کف ۱۳۹۶–۱۴۰۴",
               CASE WHEN fl.floor96>0 THEN l.market_cap_usd_free/fl.floor96 END AS "نسبت فعلی به کف ۱۳۹۶–۱۴۰۴",
               (l.market_cap_usd_free/a.peak-1)*100.0 AS "فاصله از سقف (%)",
               a.n AS "تعداد روز داده"
        FROM agg a
        JOIN x f ON f.symbol=a.symbol AND f.rn_first=1
        JOIN x l ON l.symbol=a.symbol AND l.rn_last=1
        LEFT JOIN floors fl ON fl.symbol=a.symbol AND fl.rn=1
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
    pages=["تحلیل سهم","جدول نسبت به کف ۱۳۹۶–۱۴۰۴","نمای کلی بازار","مقایسه سهم‌ها"]
    if FUND_DB_PATH.exists():
        pages.extend(["جدول سری زمانی بازار و بنیادی","تحلیل بنیادی"])
    page=st.radio("بخش",pages)

summary=load_summary(db_mtime())

if page=="تحلیل سهم":
    options={f'{r["نماد"]} — {r["نام شرکت"]}':r["نماد"] for _,r in summary.iterrows()}
    q=st.text_input("جستجوی نماد یا نام شرکت",placeholder="مثلاً فاسمین یا کالسیمین")
    labels=[k for k in options if norm(q) in norm(k)] if q else list(options)
    if not labels: st.warning("سهمی با این عبارت پیدا نشد."); st.stop()
    label=st.selectbox("انتخاب سهم",labels)
    sym=options[label]; d=load_symbol(sym,db_mtime())
    last=d.iloc[-1]
    peak=d["MarketCap_USD_Free"].max()
    d96=d[d["Date_Shamsi"].astype(str).str.replace("-","/",regex=False).str[:4].apply(lambda x: x.isdigit() and 1396<=int(x)<=1404)].copy()
    d96=d96[d96["MarketCap_USD_Free"]>0]
    trough96=d96["MarketCap_USD_Free"].min() if not d96.empty else float("nan")
    current_to_trough=(last["MarketCap_USD_Free"]/trough96) if pd.notna(trough96) and trough96 > 0 else float("nan")
    c1,c2,c3,c4,c5=st.columns(5)
    c1.metric("آخرین تاریخ",last["Date_Shamsi"])
    c2.metric("ارزش بازار │ دلار آزاد",f'{last["MarketCap_USD_Free"]/1e6:,.1f} M$')
    c3.metric("ارزش بازار │ دلار نیمایی",f'{last["MarketCap_USD_NIMA"]/1e6:,.1f} M$')
    c4.metric("نسبت ارزش فعلی به کف ۱۳۹۶–۱۴۰۴",f'{current_to_trough:,.2f} برابر' if pd.notna(current_to_trough) else "—")
    c5.metric("فاصله از سقف",f'{(last["MarketCap_USD_Free"]/peak-1)*100:,.1f}%')
    p=d[["Date_Gregorian","Date_Shamsi","MarketCap_USD_Free","MarketCap_USD_NIMA"]].copy()
    p["Date_Gregorian"]=pd.to_datetime(p["Date_Gregorian"],errors="coerce")
    p["دلار آزاد"]=p["MarketCap_USD_Free"]/1e6; p["دلار نیمایی"]=p["MarketCap_USD_NIMA"]/1e6
    long=p.melt(id_vars=["Date_Gregorian","Date_Shamsi"],value_vars=["دلار آزاد","دلار نیمایی"],var_name="مبنای ارز",value_name="ارزش بازار (میلیون دلار)")
    long=long.sort_values("Date_Gregorian")
    fig=px.line(long,x="Date_Gregorian",y="ارزش بازار (میلیون دلار)",color="مبنای ارز",hover_data={"Date_Shamsi":True,"Date_Gregorian":False},title=f'{sym} — {last["Company"]}')
    fig.update_layout(height=540,hovermode="x unified",legend_title_text="",xaxis_title="تاریخ",yaxis_title="میلیون دلار")
    fig.update_xaxes(nticks=14); st.plotly_chart(fig,use_container_width=True,config={"displayModeBar":False,"responsive":True})

    # نمودار دوم: همان ارزش بازار دلاری، با حذف داده‌های زمستان ۱۳۹۸ تا پایان پاییز ۱۴۰۰.
    # بازه حذف‌شده: ۱۳۹۸/۱۰/۰۱ تا ۱۴۰۰/۰۹/۳۰ (شامل ابتدا و انتها).
    def shamsi_key(v):
        t=str(v).strip().replace("-","/")
        parts=t.split("/")
        try:
            y=int(parts[0]); m=int(parts[1]); day=int(parts[2])
            return y*10000+m*100+day
        except Exception:
            return None

    p_gap=p.copy()
    p_gap["_sh_key"]=p_gap["Date_Shamsi"].apply(shamsi_key)
    p_gap=p_gap[~p_gap["_sh_key"].between(13981001,14000930,inclusive="both")].drop(columns=["_sh_key"])
    long_gap=p_gap.melt(id_vars=["Date_Gregorian","Date_Shamsi"],value_vars=["دلار آزاد","دلار نیمایی"],var_name="مبنای ارز",value_name="ارزش بازار (میلیون دلار)")
    long_gap=long_gap.sort_values("Date_Gregorian")
    fig_gap=px.line(long_gap,x="Date_Gregorian",y="ارزش بازار (میلیون دلار)",color="مبنای ارز",hover_data={"Date_Shamsi":True,"Date_Gregorian":False},title=f'{sym} — ارزش بازار دلاری با حذف زمستان ۱۳۹۸ تا پایان پاییز ۱۴۰۰')
    fig_gap.update_layout(height=540,hovermode="x unified",legend_title_text="",xaxis_title="تاریخ",yaxis_title="میلیون دلار")
    fig_gap.update_xaxes(nticks=14)
    st.plotly_chart(fig_gap,use_container_width=True,config={"displayModeBar":False,"responsive":True})

    free=d["MarketCap_USD_Free"]/1e6
    imax=d["MarketCap_USD_Free"].idxmax()
    imin96=d96["MarketCap_USD_Free"].idxmin() if not d96.empty else None
    a,b,c,e=st.columns(4)
    a.metric("کف ۱۳۹۶–۱۴۰۴",f"{d96.loc[imin96,'MarketCap_USD_Free']/1e6:,.1f} M$" if imin96 is not None else "—")
    b.metric("تاریخ کف ۱۳۹۶–۱۴۰۴",d96.loc[imin96,"Date_Shamsi"] if imin96 is not None else "—")
    c.metric("سقف تاریخی",f"{free.max():,.1f} M$")
    e.metric("تاریخ سقف",d.loc[imax,"Date_Shamsi"])

elif page=="جدول نسبت به کف ۱۳۹۶–۱۴۰۴":
    st.subheader("نسبت ارزش بازار دلاری فعلی به کمترین ارزش در بازه ۱۳۹۶ تا پایان ۱۴۰۴")
    st.caption("مبنای محاسبه دلار آزاد است. کف هر نماد فقط در بازه ابتدای ۱۳۹۶ تا پایان ۱۴۰۴ محاسبه می‌شود و جدول از کوچک‌ترین نسبت به بزرگ‌ترین مرتب شده است.")
    # برای خوانایی موبایل نام شرکت حذف شده و نسبت بلافاصله بعد از نماد قرار می‌گیرد.
    floor_table=summary[["نماد","نسبت فعلی به کف ۱۳۹۶–۱۴۰۴","ارزش فعلی (M$)","کف ۱۳۹۶–۱۴۰۴ (M$)","تاریخ کف ۱۳۹۶–۱۴۰۴","آخرین تاریخ"]].copy()
    floor_table=floor_table.dropna(subset=["نسبت فعلی به کف ۱۳۹۶–۱۴۰۴"]).sort_values("نسبت فعلی به کف ۱۳۹۶–۱۴۰۴",ascending=True)
    floor_table["ارزش فعلی (M$)"]=pd.to_numeric(floor_table["ارزش فعلی (M$)"],errors="coerce").round(2)
    floor_table["کف ۱۳۹۶–۱۴۰۴ (M$)"]=pd.to_numeric(floor_table["کف ۱۳۹۶–۱۴۰۴ (M$)"],errors="coerce").round(2)
    floor_table["نسبت فعلی به کف ۱۳۹۶–۱۴۰۴"]=pd.to_numeric(floor_table["نسبت فعلی به کف ۱۳۹۶–۱۴۰۴"],errors="coerce").round(3)
    st.dataframe(floor_table,use_container_width=True,hide_index=True,height=700,column_config={
        "ارزش فعلی (M$)":st.column_config.NumberColumn(format="%.2f"),
        "کف ۱۳۹۶–۱۴۰۴ (M$)":st.column_config.NumberColumn(format="%.2f"),
        "نسبت فعلی به کف ۱۳۹۶–۱۴۰۴":st.column_config.NumberColumn(format="%.3f ×"),
    })
    st.download_button("دانلود جدول (CSV)",floor_table.to_csv(index=False).encode("utf-8-sig"),"current_to_floor_1396_1404.csv","text/csv")

elif page=="نمای کلی بازار":
    st.subheader("نمای کلی سهام موجود در پایگاه داده")
    st.caption("«فعلی» برای هر سهم یعنی آخرین روز معاملاتی موجود همان سهم.")
    show=summary.copy()
    for c in ["ارزش فعلی (M$)","سقف تاریخی (M$)","کف ۱۳۹۶–۱۴۰۴ (M$)","نسبت فعلی به کف ۱۳۹۶–۱۴۰۴","فاصله از سقف (%)"]: show[c]=show[c].round(1)
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
        fig.update_xaxes(nticks=14); st.plotly_chart(fig,use_container_width=True,config={"displayModeBar":False,"responsive":True})
        st.caption("هر خط فقط در روزهایی رسم شده که همان سهم داده معاملاتی دارد.")


elif page=="جدول سری زمانی بازار و بنیادی":
    st.subheader("جدول سری زمانی بازار و بنیادی")
    st.caption("فقط نمادها و تاریخ‌هایی نمایش داده می‌شوند که داده موجودی کالا / ارزش بازار برای آنها قابل محاسبه باشد. ارزش بازار دلاری و نسبت خالص دارایی جاری نیز برای همان تاریخ‌ها نمایش داده می‌شوند.")

    if not FUND_DB_PATH.exists():
        st.warning("فایل market_data_fundamental.db کنار app.py پیدا نشد.")
        st.stop()

    def _sh_key(x):
        try:
            z=str(x).replace("-","/").split("/")[:3]
            y,m,d=[int(v) for v in z]
            return y*10000+m*100+d
        except:
            return None

    @st.cache_data(show_spinner=False)
    def load_market_fundamental_timeseries(market_mtime, fund_mtime):
        # کل تاریخچه بازار: همه نمادها
        with sqlite3.connect(DB_PATH) as con:
            market=pd.read_sql_query("""
                SELECT symbol,company_name,gregorian_date,shamsi_date,
                       market_cap_rial,market_cap_usd_free,market_cap_usd_nima
                FROM market_cap_usd
                ORDER BY symbol,gregorian_date
            """,con)

        market["period_key"]=market["shamsi_date"].map(_sh_key)
        market["gregorian_date"]=pd.to_datetime(market["gregorian_date"],errors="coerce")
        market=market.dropna(subset=["period_key","gregorian_date"]).copy()

        # فقط اقلام ترازنامه لازم برای دو نسبت درخواستی
        with sqlite3.connect(FUND_DB_PATH) as con:
            bs=pd.read_sql_query("""
                SELECT symbol,period_end_shamsi,source_label,value_numeric
                FROM v_fundamentals
                WHERE source_type='balance_sheet'
                  AND value_numeric IS NOT NULL
                  AND source_label IN (
                    'موجودی مواد و کالا',
                    'جمع دارایی‌های جاری',
                    'جمع بدهی‌های جاری و غیر جاری'
                  )
                ORDER BY symbol,period_end_shamsi
            """,con)

        ratio_rows=[]
        if not bs.empty:
            bs["period_key"]=bs["period_end_shamsi"].map(_sh_key)
            bs=bs.dropna(subset=["period_key"]).copy()
            piv=bs.pivot_table(
                index=["symbol","period_key","period_end_shamsi"],
                columns="source_label",
                values="value_numeric",
                aggfunc="last"
            ).reset_index()

            for sym,p in piv.groupby("symbol",sort=False):
                mh=market[market["symbol"]==sym][
                    ["symbol","period_key","gregorian_date","shamsi_date","market_cap_rial",
                     "market_cap_usd_free","market_cap_usd_nima"]
                ].sort_values("period_key")
                if mh.empty:
                    continue

                p=p.sort_values("period_key").copy()
                matched=pd.merge_asof(
                    p,
                    mh.drop(columns=["symbol"]),
                    on="period_key",
                    direction="backward"
                )
                matched["symbol"]=sym

                # فایل‌های بنیادی فعلی با واحد میلیون ریال ذخیره شده‌اند؛
                # ارزش بازار ریالی به میلیون ریال تبدیل می‌شود تا نسبت هم‌واحد باشد.
                mcap_mr=pd.to_numeric(matched["market_cap_rial"],errors="coerce")/1_000_000
                inventory=pd.to_numeric(matched.get("موجودی مواد و کالا"),errors="coerce")
                ca=pd.to_numeric(matched.get("جمع دارایی‌های جاری"),errors="coerce")
                tl=pd.to_numeric(matched.get("جمع بدهی‌های جاری و غیر جاری"),errors="coerce")

                matched["inventory_ratio"]=inventory/mcap_mr*100
                matched["ncav_ratio"]=(ca-tl)/mcap_mr*100
                ratio_rows.append(matched[[
                    "symbol","gregorian_date","period_end_shamsi",
                    "inventory_ratio","ncav_ratio"
                ]])

        ratios=pd.concat(ratio_rows,ignore_index=True) if ratio_rows else pd.DataFrame(
            columns=["symbol","gregorian_date","period_end_shamsi","inventory_ratio","ncav_ratio"]
        )

        # نسبت‌ها فقط روی روز معاملاتی متناظر با همان پایان دوره قرار می‌گیرند.
        out=market.merge(
            ratios,
            on=["symbol","gregorian_date"],
            how="left"
        )

        out=out.rename(columns={
            "shamsi_date":"تاریخ بازار",
            "period_end_shamsi":"پایان دوره بنیادی",
            "symbol":"نماد",
            "company_name":"نام شرکت",
            "market_cap_usd_free":"ارزش بازار دلاری آزاد",
            "market_cap_usd_nima":"ارزش بازار دلاری نیما",
            "inventory_ratio":"موجودی کالا / ارزش بازار (%)",
            "ncav_ratio":"(دارایی جاری - کل بدهی‌ها) / ارزش بازار (%)"
        })
        return out

    ts=load_market_fundamental_timeseries(
        DB_PATH.stat().st_mtime_ns,
        FUND_DB_PATH.stat().st_mtime_ns
    )

    # در این جدول فقط نمادهایی نمایش داده می‌شوند که نسبت موجودی کالا/ارزش بازار
    # برای آنها حداقل در یک دوره قابل محاسبه بوده است.
    eligible_symbols=sorted(
        ts.loc[ts["موجودی کالا / ارزش بازار (%)"].notna(), "نماد"]
          .dropna().unique().tolist()
    )
    selected=st.multiselect(
        "نمادها",
        eligible_symbols,
        default=eligible_symbols,
        key="ts_symbols"
    )

    # مبنای جدول موجودی کالاست: فقط تاریخ‌هایی که نسبت موجودی/ارزش بازار واقعاً موجود است.
    # بنابراین هیچ روز روزانه یا ردیف None صرفاً به خاطر نسبت دیگر وارد جدول نمی‌شود.
    view=ts[ts["نماد"].isin(selected)].copy()
    view=view[view["موجودی کالا / ارزش بازار (%)"].notna()].copy()

    view=view.sort_values(["gregorian_date","نماد"],ascending=[False,True])
    show=view[[
        "تاریخ بازار","پایان دوره بنیادی","نماد","نام شرکت",
        "ارزش بازار دلاری آزاد","ارزش بازار دلاری نیما",
        "موجودی کالا / ارزش بازار (%)",
        "(دارایی جاری - کل بدهی‌ها) / ارزش بازار (%)"
    ]].copy()

    show["ارزش بازار دلاری آزاد"]=show["ارزش بازار دلاری آزاد"]/1_000_000
    show["ارزش بازار دلاری نیما"]=show["ارزش بازار دلاری نیما"]/1_000_000
    show=show.rename(columns={
        "ارزش بازار دلاری آزاد":"ارزش بازار دلار آزاد (M$)",
        "ارزش بازار دلاری نیما":"ارزش بازار دلار نیما (M$)"
    })

    for c in [
        "ارزش بازار دلار آزاد (M$)",
        "ارزش بازار دلار نیما (M$)",
        "موجودی کالا / ارزش بازار (%)",
        "(دارایی جاری - کل بدهی‌ها) / ارزش بازار (%)"
    ]:
        show[c]=pd.to_numeric(show[c],errors="coerce").round(2)

    st.dataframe(
        show,
        use_container_width=True,
        hide_index=True,
        height=650,
        column_config={
            "ارزش بازار دلار آزاد (M$)":st.column_config.NumberColumn(format="%.2f"),
            "ارزش بازار دلار نیما (M$)":st.column_config.NumberColumn(format="%.2f"),
            "موجودی کالا / ارزش بازار (%)":st.column_config.NumberColumn(format="%.2f%%"),
            "(دارایی جاری - کل بدهی‌ها) / ارزش بازار (%)":st.column_config.NumberColumn(format="%.2f%%"),
        }
    )

    st.download_button(
        "دانلود جدول سری زمانی (CSV)",
        show.to_csv(index=False).encode("utf-8-sig"),
        "market_fundamental_timeseries.csv",
        "text/csv"
    )
    st.caption("برای پایان دوره‌ای که روز معاملاتی نباشد، آخرین روز معاملاتی قبل از پایان دوره استفاده می‌شود. بنابراین در محاسبه نسبت از روز آینده استفاده نشده است.")


elif page=="تحلیل بنیادی":
    st.subheader("تحلیل بنیادی تاریخی")
    if not FUND_DB_PATH.exists():
        st.warning("فایل market_data_fundamental.db کنار app.py پیدا نشد.")
        st.stop()

    def sh_key_one(x):
        try:
            z=str(x).replace("-","/").split("/")[:3]
            y,m,d=[int(v) for v in z]
            return y*10000+m*100+d
        except: return None

    source_names={
        "balance_sheet":"ترازنامه",
        "income_statement":"صورت سود و زیان",
        "cash_flow":"جریان وجوه نقد",
        "sga":"هزینه‌های عمومی، اداری و فروش",
        "inventory_turnover":"گردش موجودی",
        "production":"تولید و فروش",
        "production_sales":"تولید و فروش",
        "other_revenue":"سایر درآمدها و هزینه‌ها",
        "fx":"وضعیت ارزی",
        "cost_of_goods_sold":"بهای تمام‌شده",
        "cogs":"بهای تمام‌شده"
    }

    with sqlite3.connect(FUND_DB_PATH) as fcon:
        fsymbols=pd.read_sql_query("SELECT symbol,company_name FROM companies ORDER BY symbol",fcon)
        labels={f'{r.symbol} — {r.company_name or ""}':r.symbol for _,r in fsymbols.iterrows()}
        flabel=st.selectbox("انتخاب شرکت",list(labels),key="fund_symbol")
        fsym=labels[flabel]
        raw=pd.read_sql_query("""
          SELECT period_label,period_end_shamsi,publication_date_raw,source_type,
                 source_label,canonical_code,canonical_name_fa,value_numeric
          FROM v_fundamentals
          WHERE symbol=? AND value_numeric IS NOT NULL
          ORDER BY period_end_shamsi,source_type,source_label
        """,fcon,params=(fsym,))

    if raw.empty:
        st.info("برای این شرکت هنوز داده بنیادی ثبت نشده است.")
        st.stop()

    raw["period_key"]=raw["period_end_shamsi"].map(sh_key_one)
    raw=raw.dropna(subset=["period_key"]).copy()
    raw["بخش"]=raw["source_type"].map(source_names).fillna(raw["source_type"])

    # ارزش بازار هر دوره: همان روز، و اگر روز معاملاتی نباشد آخرین روز معاملاتی قبل از پایان دوره.
    with sqlite3.connect(DB_PATH) as mcon:
        mh=pd.read_sql_query("""
          SELECT shamsi_date,market_cap_rial,gregorian_date
          FROM stock_history WHERE symbol=? ORDER BY gregorian_date
        """,mcon,params=(fsym,))
    mh["period_key"]=mh["shamsi_date"].map(sh_key_one)
    mh=mh.dropna(subset=["period_key","market_cap_rial"]).sort_values("period_key")
    periods=raw[["period_key","period_end_shamsi"]].drop_duplicates().sort_values("period_key")
    if not mh.empty:
        periods=pd.merge_asof(periods,mh[["period_key","shamsi_date","market_cap_rial","gregorian_date"]],on="period_key",direction="backward")
    else:
        periods["market_cap_rial"]=pd.NA
        periods["shamsi_date"]=pd.NA
        periods["gregorian_date"]=pd.NA

    # ---------------- نسبت‌های ترازنامه به ارزش بازار ----------------
    bs=raw[raw["source_type"]=="balance_sheet"].copy()
    bp=bs.pivot_table(index=["period_key","period_end_shamsi"],columns="source_label",values="value_numeric",aggfunc="last").reset_index()
    bp=bp.merge(periods,on=["period_key","period_end_shamsi"],how="left")

    def col(name):
        return pd.to_numeric(bp[name],errors="coerce") if name in bp.columns else pd.Series(float("nan"),index=bp.index)
    # ارقام صورت‌های مالی پویا فایننس در این فایل‌ها میلیون ریال‌اند؛ ارزش بازار دیتابیس ریال است.
    mcap_mr=pd.to_numeric(bp.get("market_cap_rial"),errors="coerce")/1_000_000
    derived=pd.DataFrame({"period_key":bp["period_key"],"period_end_shamsi":bp["period_end_shamsi"],"market_date":bp.get("shamsi_date")})
    current_assets=col("جمع دارایی‌های جاری")
    current_liab=col("جمع بدهی‌های جاری")
    cash=col("موجودی نقد")
    short_inv=col("سرمایه گذاری کوتاه مدت")
    inventory=col("موجودی مواد و کالا")
    receivables=col("دریافتنی‌های تجاری و سایر دریافتنی‌ها")
    if receivables.isna().all(): receivables=col("دریافتنی‌های تجاری و سایر دریافتنیها")
    equity=col("جمع حقوق صاحبان سهام")
    total_assets=col("جمع دارایی‌ها")
    total_liab=col("جمع بدهی‌های جاری و غیر جاری")
    debt_current=col("حصه جاری تسهیلات مالی دریافتی")
    debt_long=col("تسهیلات مالی دریافتی بلند مدت")
    financial_debt=debt_current.fillna(0)+debt_long.fillna(0)
    working_capital=current_assets-current_liab
    net_current_assets_after_total_liab=current_assets-total_liab
    net_debt=financial_debt-cash.fillna(0)-short_inv.fillna(0)

    ratio_series={
        "موجودی مواد و کالا / ارزش بازار":inventory/mcap_mr*100,
        "وجه نقد / ارزش بازار":cash/mcap_mr*100,
        "سرمایه‌گذاری کوتاه‌مدت / ارزش بازار":short_inv/mcap_mr*100,
        "دریافتنی‌های تجاری / ارزش بازار":receivables/mcap_mr*100,
        "دارایی‌های جاری / ارزش بازار":current_assets/mcap_mr*100,
        "بدهی‌های جاری / ارزش بازار":current_liab/mcap_mr*100,
        "سرمایه در گردش / ارزش بازار":working_capital/mcap_mr*100,
        "خالص دارایی جاری پس از کل بدهی‌ها / ارزش بازار":net_current_assets_after_total_liab/mcap_mr*100,
        "بدهی مالی / ارزش بازار":financial_debt/mcap_mr*100,
        "بدهی خالص / ارزش بازار":net_debt/mcap_mr*100,
        "حقوق صاحبان سهام / ارزش بازار":equity/mcap_mr*100,
        "کل دارایی‌ها / ارزش بازار":total_assets/mcap_mr*100,
        "کل بدهی‌ها / ارزش بازار":total_liab/mcap_mr*100,
    }
    ratios=[]
    for name,ser in ratio_series.items():
        z=derived.copy(); z["متغیر"]=name; z["مقدار"]=ser
        ratios.append(z)
    ratios=pd.concat(ratios,ignore_index=True).dropna(subset=["مقدار"])

    tab1,tab2,tab3,tab4=st.tabs(["نسبت‌ها به ارزش بازار","صورت‌های مالی","هزینه و عملیات","جدول داده"])

    with tab1:
        st.markdown("#### نسبت‌های بنیادی به ارزش بازار")
        ratio_opts=list(ratio_series.keys())
        default_ratios=[x for x in ["موجودی مواد و کالا / ارزش بازار","خالص دارایی جاری پس از کل بدهی‌ها / ارزش بازار","سرمایه در گردش / ارزش بازار","وجه نقد / ارزش بازار","بدهی مالی / ارزش بازار"] if x in ratio_opts]
        rsel=st.multiselect("انتخاب نسبت",ratio_opts,default=default_ratios,key="ratio_select")
        rp=ratios[ratios["متغیر"].isin(rsel)].sort_values(["متغیر","period_key"])
        if not rp.empty:
            fig=px.line(rp,x="period_key",y="مقدار",color="متغیر",markers=True,
                        hover_data={"period_end_shamsi":True,"market_date":True,"period_key":False},
                        title=f"{fsym} — نسبت‌های بنیادی به ارزش بازار")
            ticks=rp[["period_key","period_end_shamsi"]].drop_duplicates().sort_values("period_key")
            step=max(1,len(ticks)//12); ticks=ticks.iloc[::step]
            fig.update_xaxes(tickmode="array",tickvals=ticks["period_key"],ticktext=ticks["period_end_shamsi"],title="پایان دوره مالی")
            fig.update_yaxes(ticksuffix="%",title="درصد ارزش بازار")
            fig.update_layout(height=560,hovermode="x unified",legend_title_text="")
            st.plotly_chart(fig,use_container_width=True,config={"displayModeBar":False,"responsive":True})
            latest=rp.sort_values("period_key").groupby("متغیر",as_index=False).tail(1)
            cc=st.columns(min(4,max(1,len(latest))))
            for i,(_,r) in enumerate(latest.iterrows()):
                cc[i%len(cc)].metric(r["متغیر"],f'{r["مقدار"]:,.1f}%',r["period_end_shamsi"])
        else: st.info("برای نسبت انتخاب‌شده داده قابل محاسبه وجود ندارد.")
        st.caption("ارزش بازار هر دوره از آخرین روز معاملاتی همان تاریخ یا قبل از آن گرفته می‌شود؛ بنابراین از داده آینده استفاده نمی‌شود.")

    with tab2:
        st.markdown("#### اقلام ترازنامه، سود و زیان و جریان وجوه نقد")
        stmt_types=[x for x in ["balance_sheet","income_statement","cash_flow"] if x in raw["source_type"].unique()]
        stmt=st.selectbox("صورت مالی",stmt_types,format_func=lambda x:source_names.get(x,x),key="stmt_type")
        rr=raw[raw["source_type"]==stmt].copy()
        items=rr["source_label"].drop_duplicates().tolist()
        defaults={
            "balance_sheet":["موجودی نقد","دریافتنی‌های تجاری و سایر دریافتنی‌ها","موجودی مواد و کالا","جمع دارایی‌های جاری","جمع بدهی‌های جاری","جمع حقوق صاحبان سهام"],
            "income_statement":["فروش","سود (زیان) ناخالص","سود (زیان) عملیاتی","سود (زیان) خالص"],
            "cash_flow":["نقد حاصل از عملیات","جریان خالص ورود (خروج) وجه نقد ناشی از فعالیتهای عملیاتی IFRS","جریان خالص ورود (خروج) نقد حاصل از فعالیت‌های سرمایه‌گذاری IFRS","جریان خالص ورود (خروج) نقد حاصل از فعالیت‌های تامین مالی IFRS","خالص افزایش (کاهش) در وجه نقد","وجه نقد در پایان دوره"]
        }
        dflt=[x for x in defaults.get(stmt,[]) if x in items][:6]
        isel=st.multiselect("اقلام",items,default=dflt,key="stmt_items")
        fp=rr[rr["source_label"].isin(isel)].sort_values(["source_label","period_key"])
        if not fp.empty:
            fig=px.line(fp,x="period_key",y="value_numeric",color="source_label",markers=True,
                        hover_data={"period_end_shamsi":True,"period_label":True,"publication_date_raw":True,"period_key":False},
                        title=f"{fsym} — {source_names.get(stmt,stmt)}")
            ticks=fp[["period_key","period_end_shamsi"]].drop_duplicates().sort_values("period_key")
            step=max(1,len(ticks)//12); ticks=ticks.iloc[::step]
            fig.update_xaxes(tickmode="array",tickvals=ticks["period_key"],ticktext=ticks["period_end_shamsi"],title="پایان دوره مالی")
            fig.update_layout(height=560,hovermode="x unified",legend_title_text="",yaxis_title="مقدار گزارش‌شده")
            st.plotly_chart(fig,use_container_width=True,config={"displayModeBar":False,"responsive":True})
        st.caption("اقلام جریان وجوه نقد و سود و زیان دقیقاً مطابق مقادیر گزارش‌شده منبع نمایش داده می‌شوند؛ دوره‌های جریانی به‌صورت خودکار TTM یا سالانه‌سازی نشده‌اند.")

    with tab3:
        st.markdown("#### هزینه‌ها، گردش موجودی و داده‌های عملیاتی")
        ops=[x for x in raw["source_type"].drop_duplicates().tolist() if x not in ["balance_sheet","income_statement","cash_flow"]]
        if ops:
            op=st.selectbox("گروه داده",ops,format_func=lambda x:source_names.get(x,x),key="op_type")
            oo=raw[raw["source_type"]==op].copy()
            oitems=oo["source_label"].drop_duplicates().tolist()
            odflt=[x for x in ["حق العمل و کمیسیون فروش","هزینه انرژی (آب، برق، گاز و سوخت)","هزینه حمل و نقل و انتقال","جمع"] if x in oitems][:4]
            osel=st.multiselect("اقلام عملیاتی",oitems,default=odflt,key="op_items")
            opf=oo[oo["source_label"].isin(osel)].sort_values(["source_label","period_key"])
            if not opf.empty:
                fig=px.line(opf,x="period_key",y="value_numeric",color="source_label",markers=True,
                            hover_data={"period_end_shamsi":True,"period_label":True,"period_key":False},
                            title=f"{fsym} — {source_names.get(op,op)}")
                ticks=opf[["period_key","period_end_shamsi"]].drop_duplicates().sort_values("period_key")
                step=max(1,len(ticks)//12); ticks=ticks.iloc[::step]
                fig.update_xaxes(tickmode="array",tickvals=ticks["period_key"],ticktext=ticks["period_end_shamsi"],title="دوره")
                fig.update_layout(height=560,hovermode="x unified",legend_title_text="",yaxis_title="مقدار گزارش‌شده")
                st.plotly_chart(fig,use_container_width=True,config={"displayModeBar":False,"responsive":True})
        else: st.info("داده عملیاتی دیگری برای این شرکت ثبت نشده است.")

    with tab4:
        st.markdown("#### داده خام استانداردشده")
        cats=raw["بخش"].drop_duplicates().tolist()
        csel=st.multiselect("بخش‌های جدول",cats,default=cats[:3],key="table_cats")
        show=raw[raw["بخش"].isin(csel)][["period_end_shamsi","period_label","بخش","source_label","value_numeric","publication_date_raw"]].copy()
        show.columns=["پایان دوره","عنوان دوره","بخش","عنوان اصلی منبع","مقدار","تاریخ انتشار"]
        st.dataframe(show,use_container_width=True,hide_index=True,height=600)
        st.download_button("دانلود داده بنیادی انتخاب‌شده (CSV)",show.to_csv(index=False).encode("utf-8-sig"),f"fundamentals_{fsym}.csv","text/csv")

st.caption("داده‌های دلاری هنگام نمایش از اتصال ارزش بازار ریالی با نرخ ارز همان تاریخ محاسبه می‌شوند.")
