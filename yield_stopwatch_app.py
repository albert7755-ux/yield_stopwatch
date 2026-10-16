# -*- coding: utf-8 -*-
"""
殖利率碼表（Streamlit 版）
==========================
從龍蝦報價 API 抓架上債券，分層抽題，嵌入碼表遊戲。

玩法：先答出該檔的殖利率 → 那個數字就是碼表的目標秒數 → 把碼表停在那個數字上。
答錯的人會瞄著錯的數字停，誤差自然大（誤差一律對照「正確殖利率」計算）。

執行：
    streamlit run yield_stopwatch_app.py

同一個資料夾要有：
    yield_stopwatch_game.html      ← 遊戲本體（外觀都在這裡改）

環境變數 / Secrets（也可以在側邊欄直接輸入）：
    LOBSTER_API    例 https://eln-bot.onrender.com/api/bonds
    LOBSTER_TOKEN  即 BOND_UPLOAD_TOKEN
"""
import os
import json
import math
import random
from pathlib import Path

import pandas as pd
import requests
import streamlit as st
import streamlit.components.v1 as components

st.set_page_config(page_title="殖利率碼表", page_icon="⏱️", layout="centered",
                   initial_sidebar_state="collapsed")

GAME_FILE = Path(__file__).with_name("yield_stopwatch_game.html")

# ===================== 出題規則（要調就改這裡） =====================
CCY_ONLY      = "USD"                      # 只收美元計價
N_ROUNDS      = 3                          # 一局幾檔

# 資格：龍蝦 API 的 tag 來自 pi_tag()，只會標「🔒專投」或「💎高資產」，
# 一般投資人可申購的券是「沒有標記」。所以「非專投」要用排除法，不是找關鍵字。
PI_MARKS      = ("專投", "高資產")
ELIGIBILITY   = {
    "一般投資人（非專投）": "general",
    "限專業投資人": "pi",
    "限高資產客戶": "hnw",
    "不限": "any",
}
MIN_GAP       = 0.30                       # |殖利率 − 票面| 門檻，低於此不出題（否則送分）
MIN_SEP       = 0.55                       # 同一局三檔的殖利率至少彼此相差這麼多
YEARS_RANGE   = (1.0, 30.0)                # 剩餘年期可接受範圍
NEED_PREMIUM  = True                       # 每局至少帶一檔溢價（價格 > 100）
FREQ_N = {"月配": 12, "季配": 4, "半年配": 2, "年配": 1,
          "每月": 12, "每季": 4, "每半年": 2, "每年": 1}

FOOTNOTE = ("本遊戲僅供內部教育訓練使用。題目中的報價與殖利率為特定時點的參考資料，可能已經變動，"
            "實際條件與價格以總行報價為準，不構成投資建議或要約。"
            "債券投資須留意利率風險、信用風險與流動性風險。請勿外流。")

# ===================== 樣式（富邦藍金，與其他工具一致） =====================
st.markdown("""<style>
@import url('https://fonts.googleapis.com/css2?family=Noto+Sans+TC:wght@400;500;700&display=swap');
html, body, [class*="css"] { font-family:'Noto Sans TC','Microsoft JhengHei',sans-serif; }
.stApp { background:#eef2f7; }
.block-container { padding-top:1.2rem; max-width:860px; }
#MainMenu, footer, header { visibility:hidden; }
.hero { background:linear-gradient(115deg,#0B2A4A 0%,#1F8AC0 100%); color:#fff;
        padding:20px 24px; border-radius:14px; margin-bottom:16px;
        box-shadow:0 4px 18px rgba(11,42,74,.18); }
.hero h1 { margin:0; font-size:24px; font-weight:700; letter-spacing:1px; }
.hero p  { margin:6px 0 0; font-size:13px; opacity:.9; }
.stButton button { background:#1F8AC0; color:#fff; border:0; border-radius:8px;
    padding:.45rem 1.3rem; font-weight:600; }
.stButton button:hover { background:#0B2A4A; }
.stDownloadButton button { background:#C9A227; color:#fff; border:0; border-radius:8px;
    font-weight:600; padding:.5rem 1.4rem; }
section[data-testid="stSidebar"] { background:#0B2A4A; }
section[data-testid="stSidebar"] * { color:#dbe6f0 !important; }
section[data-testid="stSidebar"] input { background:#163a5f !important; border-color:#2b5680 !important; }
</style>""", unsafe_allow_html=True)


# ===================== 工具函式 =====================
def _num(v):
    """轉 float，失敗回 None"""
    try:
        f = float(v)
        return None if (math.isnan(f) or math.isinf(f)) else f
    except (TypeError, ValueError):
        return None


def clean_price(cpn_pct, ytm_pct, years, freq):
    """由殖利率回推債券淨價（每 100 面額）。與金開心配置試算同一套算法，數字才會對得起來。"""
    m = FREQ_N.get(str(freq).strip(), 2)
    c = float(cpn_pct) / m
    yq = float(ytm_pct) / 100.0 / m
    T = float(years)
    if T <= 0 or yq <= -0.99:
        return None
    dirty, j, t_first = 0.0, 0, T
    while True:
        t = T - j / m
        if t <= 1e-9:
            break
        cf = c + (100.0 if j == 0 else 0.0)
        dirty += cf / ((1 + yq) ** (t * m))
        t_first = t
        j += 1
    accrued = c * max(0.0, 1.0 - t_first * m)
    return dirty - accrued


def pick_price(b, cpn, ytm, years, freq):
    """報價檔有價格就用（優先申購端 Offer），沒有就由殖利率回推。"""
    for k in ("offer_price", "offer", "ask", "price", "px", "clean_price",
              "Offer Price", "offerPrice"):
        v = _num(b.get(k))
        if v and 20 < v < 250:
            return v, "報價"
    v = clean_price(cpn, ytm, years, freq)
    return (round(v, 2), "回推") if v else (None, "-")


@st.cache_data(ttl=600, show_spinner=False)
def load_bonds(api, token):
    r = requests.get(api, params={"token": token}, timeout=30)
    r.raise_for_status()
    d = r.json()
    return d.get("bonds", []), d.get("updated_at", "")


def rating_text(b):
    """評等欄位在不同來源的欄名不一，挑第一個有值的，只取一家（S&P 優先）。"""
    for k in ("ratings", "rating", "sp", "s&p", "sp_rating", "moody", "fitch"):
        v = b.get(k)
        if v and str(v).strip() not in ("", "-", "nan", "None"):
            return str(v).strip().split("/")[0].strip()
    return ""


def to_frame(bonds):
    """把 API 回傳的 bonds 攤平成 DataFrame，並補上價格與折溢價。"""
    rows = []
    for b in bonds:
        cpn = _num(b.get("coupon"))
        ytm = _num(b.get("ytm"))
        yrs = _num(b.get("years"))
        if cpn is None or ytm is None or yrs is None:
            continue
        freq = b.get("freq") or "半年配"
        px, px_src = pick_price(b, cpn, ytm, yrs, freq)
        if px is None:
            continue
        rows.append({
            "code": str(b.get("code") or "").strip(),
            "isin": str(b.get("isin") or "").strip(),
            "name": str(b.get("name") or "").strip(),
            "ccy": str(b.get("ccy") or "").strip().upper(),
            "tag": str(b.get("tag") or "").strip(),
            "sector": str(b.get("sector") or "").strip(),
            "rating": rating_text(b),
            "maturity": str(b.get("maturity") or "-").strip(),
            "freq": str(freq).strip(),
            "coupon": cpn, "ytm": ytm, "years": yrs,
            "price": px, "price_src": px_src,
            "gap": abs(ytm - cpn),
            "premium": px > 100.0,
        })
    df = pd.DataFrame(rows)
    if not df.empty:
        # 同一檔券可能出現在多個區塊，用 ISIN 去重（沒有 ISIN 時退回代碼＋名稱）
        df["_k"] = df["isin"].where(df["isin"].ne(""), df["code"] + "|" + df["name"])
        df = df.drop_duplicates(subset=["_k"], keep="first").drop(columns=["_k"])
    return df


def tag_mask(df, mode):
    """資格篩選。general＝tag 沒有專投／高資產標記的（一般投資人可申購）。"""
    has_pi = df["tag"].str.contains(PI_MARKS[0], na=False)
    has_hnw = df["tag"].str.contains(PI_MARKS[1], na=False)
    if mode == "general":
        return ~(has_pi | has_hnw)
    if mode == "pi":
        return has_pi
    if mode == "hnw":
        return has_hnw
    return pd.Series(True, index=df.index)


def eligible(df, ccy, mode, gap_min, yrs_lo, yrs_hi, ytm_lo, ytm_hi):
    if df.empty:
        return df
    m = pd.Series(True, index=df.index)
    if ccy:
        m &= df["ccy"].eq(ccy)
    m &= tag_mask(df, mode)
    m &= df["gap"] >= gap_min
    m &= df["years"].between(yrs_lo, yrs_hi)
    m &= df["ytm"].between(ytm_lo, ytm_hi)
    return df[m]


def make_options(ytm, coupon, rng):
    """
    三個選項：正解、票面利率（最常見的誤會）、一個誘答。
    誘答放在正解的另一側，與正解、票面都至少差 0.25，避免變成猜硬幣。
    """
    opts = [round(ytm, 2), round(coupon, 2)]
    side = -1 if coupon > ytm else 1          # 誘答擺在票面的反方向
    for _ in range(40):
        decoy = round(ytm + side * rng.uniform(0.35, 0.95), 2)
        if decoy <= 0.2:
            continue
        if all(abs(decoy - o) >= 0.25 for o in opts):
            opts.append(decoy)
            break
    else:
        opts.append(round(ytm + side * 0.6, 2))
    rng.shuffle(opts)
    return opts


def explain(r):
    """折溢價的白話解說，結果頁會顯示。"""
    px, cpn, ytm, yrs = r["price"], r["coupon"], r["ytm"], r["years"]
    if px < 100:
        disc = 100 - px
        per_year = disc / yrs if yrs > 0 else 0
        return (f"價格 {px:.2f} 是<b>折價</b>，到期還你 100，中間那 {disc:.2f} 元價差"
                f"攤在 {yrs:.1f} 年裡（一年約 {per_year:.2f} 元）也算報酬，"
                f"所以殖利率 {ytm:.2f}% 高於票面 {cpn:.2f}%。")
    if px > 100:
        prem = px - 100
        return (f"價格 {px:.2f} 是<b>溢價</b>，你買得比面額貴 {prem:.2f} 元，"
                f"到期只還你 100，那段差額要從票息裡扣回去，"
                f"所以殖利率 {ytm:.2f}% 低於票面 {cpn:.2f}%。")
    return f"價格剛好在 100 附近（平價），殖利率 {ytm:.2f}% 會貼近票面 {cpn:.2f}%。"


def build_questions(pool, seed=None, n=None, min_sep=None):
    """
    抽題：隨機抽 n 檔，但要求它們的殖利率彼此至少相差 min_sep，
    否則三題的目標秒數會擠在一起（例如 4.90 / 4.98 / 5.07），玩起來三題一模一樣。
    同時盡量帶一檔溢價券，免得玩的人只學到「折價→殖利率比較高」那半邊。
    抽不到完美組合時，取試過的組合裡最接近的。
    """
    n = N_ROUNDS if n is None else int(n)
    min_sep = MIN_SEP if min_sep is None else float(min_sep)
    rng = random.Random(seed)
    idx = list(pool.index)
    n = max(1, min(n, len(idx)))

    best, best_key = None, None
    for _ in range(600):
        sub = pool.loc[rng.sample(idx, n)]
        ys = sorted(sub["ytm"].tolist())
        sep = min(ys[i + 1] - ys[i] for i in range(len(ys) - 1)) if len(ys) > 1 else 99.0
        prem = bool(sub["premium"].any())
        if sep >= min_sep and (prem or not NEED_PREMIUM):
            best = sub
            break
        key = (prem, sep)
        if best_key is None or key > best_key:
            best, best_key = sub, key

    picks = [best.loc[i] for i in best.sort_values("ytm").index]   # 由低到高，順帶排出信用階梯

    qs = []
    for r in picks:
        bits = [r["code"]] if r["code"] else []
        if r["maturity"] and r["maturity"] != "-":
            bits.append(str(r["maturity"])[:10])
        if r["rating"]:
            bits.append(r["rating"])
        if r["sector"] and r["sector"] not in ("未分類", "—"):
            bits.append(r["sector"])
        bits.append(r["freq"])
        qs.append({
            "iss": r["name"],
            "sub": "　·　".join(bits),
            "cpn": round(r["coupon"], 3),
            "yrs": round(r["years"], 1),
            "px": round(r["price"], 2),
            "ans": round(r["ytm"], 2),
            "opts": make_options(r["ytm"], r["coupon"], rng),
            "why": explain(r),
        })
    return qs, picks


def render_html(questions, asof):
    tpl = GAME_FILE.read_text(encoding="utf-8")
    inject = (
        "<script>\n"
        f"window.__QUESTIONS__ = {json.dumps(questions, ensure_ascii=False)};\n"
        f"window.__FOOTNOTE__ = {json.dumps(FOOTNOTE, ensure_ascii=False)};\n"
        f"window.__ASOF__ = {json.dumps(asof or '', ensure_ascii=False)};\n"
        "</script>\n"
    )
    return tpl.replace("</head>", inject + "</head>", 1)


# ===================== 側邊欄 =====================
st.sidebar.header("資料來源")
api = st.sidebar.text_input("龍蝦報價 API",
                            os.getenv("LOBSTER_API", "https://eln-bot.onrender.com/api/bonds"))
token = st.sidebar.text_input("存取密碼", os.getenv("LOBSTER_TOKEN", ""), type="password")

st.sidebar.markdown("---")
st.sidebar.header("出題條件")
ccy = st.sidebar.text_input("幣別", CCY_ONLY, help="留空＝不限幣別")
elig_label = st.sidebar.selectbox("資格", list(ELIGIBILITY.keys()), index=0,
                                  help="報價檔只會標「專投」或「高資產」，"
                                       "一般投資人可申購的券是沒有標記的，所以用排除法篩")
elig = ELIGIBILITY[elig_label]
n_rounds = st.sidebar.slider("一局幾檔", 2, 6, N_ROUNDS, 1)
ytm_lo, ytm_hi = st.sidebar.slider("殖利率範圍 (%)", 0.0, 12.0, (4.0, 8.5), 0.5,
                                   help="碼表停在 4~8 秒最好操作，所以預設篩這個區間")
gap_min = st.sidebar.slider("|殖利率 − 票面| 最小值", 0.0, 1.5, MIN_GAP, 0.05,
                            help="太接近的話，「票面」那個錯誤選項跟正解幾乎一樣，會變成送分題")
min_sep = st.sidebar.slider("同一局各檔殖利率最小間距", 0.0, 1.5, MIN_SEP, 0.05,
                            help="太小的話三題的目標秒數會擠在一起，玩起來像同一題。"
                                 "太大則可能抽不到符合的組合")
yrs_lo, yrs_hi = st.sidebar.slider("剩餘年期", 0.0, 40.0, YEARS_RANGE, 0.5)

# ===================== 主畫面 =====================
bonds, updated = [], ""
err = ""
if token:
    try:
        bonds, updated = load_bonds(api, token)
    except Exception as e:
        err = str(e)[:160]

st.markdown(f"""<div class="hero">
<h1>⏱️ 殖利率碼表</h1>
<p>先答出殖利率，再把碼表停在那個數字上。題目來自架上即時報價
{f"　·　報價檔：{updated}" if updated else ""}</p></div>""", unsafe_allow_html=True)

if not token:
    st.info("請在左側側邊欄輸入**存取密碼**以載入架上報價。（展開方式：點左上角的 **»**）")
    st.stop()
if err:
    st.error(f"報價載入失敗：{err}")
    st.stop()

df = to_frame(bonds)
if df.empty:
    st.error("報價檔裡沒有可用的債券（需要同時有票面利率、殖利率、剩餘年期）。")
    st.stop()

pool = eligible(df, ccy.strip().upper(), elig, gap_min, yrs_lo, yrs_hi, ytm_lo, ytm_hi)

c1, c2, c3 = st.columns(3)
c1.metric("報價檔總筆數", f"{len(df):,}")
c2.metric("符合出題條件", f"{len(pool):,}")
c3.metric("其中溢價券", f"{int(pool['premium'].sum()) if not pool.empty else 0:,}")

if pool.empty:
    st.warning("這組條件下沒有可出題的債券。請放寬左側的殖利率範圍或票面價差門檻。")
    with st.expander("看看卡在哪一條"):
        st.write({
            "報價檔總筆數（去重後）": len(df),
            "幣別符合": int(df["ccy"].eq(ccy.strip().upper()).sum()) if ccy.strip() else len(df),
            f"資格符合（{elig_label}）": int(tag_mask(df, elig).sum()),
            "殖利率在範圍內": int(df["ytm"].between(ytm_lo, ytm_hi).sum()),
            f"票面價差 ≥ {gap_min}": int((df["gap"] >= gap_min).sum()),
            "年期在範圍內": int(df["years"].between(yrs_lo, yrs_hi).sum()),
        })
        st.caption("報價檔裡出現過的「資格」標記：" + "、".join(
            [(v if v.strip() else "（空白＝一般投資人）") for v in
             df["tag"].fillna("").unique().tolist()][:12]))
    st.stop()

if len(pool) < n_rounds:
    st.warning(f"符合條件的券只有 {len(pool)} 檔，少於一局要出的 {n_rounds} 檔。請放寬條件。")

# ---- 出題（放進 session_state，避免每次互動就換題）----
sig = (ccy, elig, n_rounds, ytm_lo, ytm_hi, gap_min, min_sep, yrs_lo, yrs_hi)
left, right = st.columns([1, 3])
if left.button("🎲 換一組題目") or st.session_state.get("sig") != sig:
    st.session_state["qs"], st.session_state["picks"] = build_questions(
        pool, n=n_rounds, min_sep=min_sep)
    st.session_state["sig"] = sig
right.caption(f"從 {len(pool)} 檔可出題的券裡抽 {n_rounds} 檔，"
              f"要求彼此殖利率至少相差 {min_sep:.2f}%，並盡量帶一檔溢價券"
              f"（池子裡有 {int(pool['premium'].sum())} 檔溢價）。")

qs = st.session_state["qs"]
if not qs:
    st.error("抽不出題目，請放寬條件。")
    st.stop()

html = render_html(qs, updated)
components.html(html, height=840, scrolling=True)

st.download_button("⬇️ 下載這組題目的獨立 HTML（可放靜態空間，不怕 Streamlit 休眠）",
                   data=html.encode("utf-8"),
                   file_name="yield_stopwatch.html", mime="text/html")

with st.expander("🔍 這一局的答案（出題者自己看，別給玩的人看）"):
    show = pd.DataFrame(st.session_state["picks"])[
        ["name", "isin", "rating", "coupon", "ytm", "years", "price", "price_src", "gap", "premium", "tag"]
    ].rename(columns={"name": "債券", "isin": "ISIN", "rating": "評等", "coupon": "票面%",
                      "ytm": "殖利率%", "years": "剩餘年期", "price": "參考價格",
                      "price_src": "價格來源", "gap": "票面價差", "premium": "是否溢價",
                      "tag": "資格"})
    st.dataframe(show.style.format({
        "票面%": "{:.3f}", "殖利率%": "{:.2f}", "剩餘年期": "{:.1f}",
        "參考價格": "{:.2f}", "票面價差": "{:.2f}"}), use_container_width=True, hide_index=True)
    st.caption("「價格來源」若顯示『回推』，表示報價檔沒有給淨價，是用殖利率、票面、年期與配息頻率"
               "回推出來的（與金開心配置試算同一套算法）。")

st.markdown(f"""<div style="background:#FDECEC;border:1.5px solid #B23A2E;border-radius:9px;
padding:11px 15px;text-align:center;color:#B23A2E;font-weight:700;font-size:14px;margin-top:12px">
本頁僅供內部教育訓練使用　請勿外流</div>""", unsafe_allow_html=True)
st.caption(FOOTNOTE)
