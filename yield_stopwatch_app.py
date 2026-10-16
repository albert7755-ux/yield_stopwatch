# -*- coding: utf-8 -*-
"""
殖利率碼表（Streamlit 版）
先答出該檔債券的殖利率 → 那個數字就是碼表的目標秒數 → 把碼表停在那個數字上。

Secrets（Streamlit Cloud → Settings → Secrets）：
    LOBSTER_TOKEN       龍蝦報價 API 的 BOND_UPLOAD_TOKEN，沒有它抓不到題目
    SUPABASE_URL        排行榜用
    SUPABASE_ANON_KEY   排行榜用，只能用 anon public key
    LOBSTER_API         選填，預設就是下面那個網址

排行榜要先在 Supabase 的 SQL Editor 跑一次 supabase_setup.sql。
網址帶 ?admin=1 會多出答案表和「下載獨立 HTML」，貼給別人的連結不要帶。
"""
import json
import random
from pathlib import Path

import requests
import streamlit as st
import streamlit.components.v1 as components

st.set_page_config(page_title="殖利率碼表", page_icon="⏱️", layout="centered")

# 把 Streamlit 預設的邊框、選單藏起來，讓遊戲佔滿畫面
st.markdown(
    """
    <style>
      #MainMenu, header, footer {visibility: hidden;}
      .block-container {padding: 0 !important; max-width: 560px;}
      iframe {border: 0;}
    </style>
    """,
    unsafe_allow_html=True,
)

# ---------- 出題規則，要調就改這裡 ----------
API        = st.secrets.get("LOBSTER_API", "https://eln-bot.onrender.com/api/bonds")
CCY        = "USD"            # 只收美元計價
N_ROUNDS   = 3                # 一局幾檔
YTM_RANGE  = (4.0, 8.5)       # 碼表停在 4~8 秒最好操作
YEARS      = (1.0, 30.0)      # 剩餘年期
MIN_GAP    = 0.30             # |殖利率 − 票面| 門檻，太接近會變成送分題
MIN_SEP    = 0.55             # 同一局各檔的殖利率至少彼此相差這麼多，不然三題一模一樣
PI_MARKS   = ("專投", "高資產")  # tag 有這些標記的＝不是一般投資人可申購，排除

FOOTNOTE = ("本遊戲僅供內部教育訓練使用。題目中的報價與殖利率為特定時點的參考資料，可能已經變動，"
            "實際條件與價格以總行報價為準，不構成投資建議或要約。"
            "債券投資須留意利率風險、信用風險與流動性風險。請勿外流。")


@st.cache_data(ttl=600, show_spinner=False)
def load_bonds(api, token):
    r = requests.get(api, params={"token": token}, timeout=30)
    r.raise_for_status()
    d = r.json()
    return d.get("bonds", []), d.get("updated_at", "")


def num(v):
    try:
        f = float(v)
        return f if f == f else None          # NaN 會自己不等於自己
    except (TypeError, ValueError):
        return None


def pool_of(bonds):
    """挑出可以出題的券：一般投資人、美元、殖利率與年期在範圍內、票面與殖利率差得夠開。"""
    out, seen = [], set()
    for b in bonds:
        cpn, ytm, px = num(b.get("coupon")), num(b.get("ytm")), num(b.get("offer"))
        yrs = num(b.get("years"))
        if None in (cpn, ytm, px, yrs):
            continue
        tag = str(b.get("tag") or "")
        if any(m in tag for m in PI_MARKS):                 # 限專投／高資產的不出
            continue
        if CCY and str(b.get("ccy") or "").upper() != CCY:
            continue
        if not (YTM_RANGE[0] <= ytm <= YTM_RANGE[1]):
            continue
        if not (YEARS[0] <= yrs <= YEARS[1]):
            continue
        if abs(ytm - cpn) < MIN_GAP:
            continue
        key = b.get("isin") or b.get("code") or b.get("name")
        if key in seen:
            continue
        seen.add(key)
        rating = str(b.get("ratings") or "").split("/")[0].strip()
        out.append({"name": str(b.get("name") or "").strip(), "isin": str(b.get("isin") or ""),
                    "cpn": cpn, "ytm": ytm, "px": px, "yrs": yrs, "rating": rating,
                    "freq": str(b.get("freq") or "每半年"), "mat": str(b.get("maturity") or "")[:10]})
    return out


def pick(pool, n, min_sep):
    """隨機抽 n 檔，要求殖利率彼此至少差 min_sep，並盡量帶一檔溢價券（價格 > 100）。"""
    rng = random.Random()
    n = max(1, min(n, len(pool)))
    best, best_key = None, None
    for _ in range(600):
        s = rng.sample(pool, n)
        ys = sorted(x["ytm"] for x in s)
        sep = min(ys[i + 1] - ys[i] for i in range(len(ys) - 1)) if n > 1 else 99.0
        prem = any(x["px"] > 100 for x in s)
        if sep >= min_sep and prem:
            best = s
            break
        key = (prem, sep)
        if best_key is None or key > best_key:
            best, best_key = s, key
    return sorted(best, key=lambda x: x["ytm"])             # 由低到高，順帶排出信用階梯


def make_q(b):
    """一題：三個選項＝正解、票面利率（最常見的誤會）、一個誘答。"""
    rng = random.Random()
    opts = [round(b["ytm"], 2), round(b["cpn"], 2)]
    side = -1 if b["cpn"] > b["ytm"] else 1                 # 誘答擺在票面的反方向
    decoy = round(b["ytm"] + side * 0.6, 2)
    for _ in range(40):
        d = round(b["ytm"] + side * rng.uniform(0.35, 0.95), 2)
        if d > 0.2 and all(abs(d - o) >= 0.25 for o in opts):
            decoy = d
            break
    opts.append(decoy)
    rng.shuffle(opts)

    if b["px"] < 100:
        disc = 100 - b["px"]
        why = (f"價格 {b['px']:.2f} 是<b>折價</b>，到期還你 100，中間那 {disc:.2f} 元價差"
               f"攤在 {b['yrs']:.1f} 年裡（一年約 {disc / b['yrs']:.2f} 元）也算報酬，"
               f"所以殖利率 {b['ytm']:.2f}% 高於票面 {b['cpn']:.2f}%。")
    else:
        why = (f"價格 {b['px']:.2f} 是<b>溢價</b>，你買得比面額貴 {b['px'] - 100:.2f} 元，"
               f"到期只還你 100，那段差額要從票息裡扣回去，"
               f"所以殖利率 {b['ytm']:.2f}% 低於票面 {b['cpn']:.2f}%。")

    sub = "　·　".join(x for x in (b["isin"], b["mat"], b["rating"], b["freq"]) if x)
    return {"iss": b["name"], "sub": sub, "cpn": round(b["cpn"], 3), "yrs": round(b["yrs"], 1),
            "px": round(b["px"], 2), "ans": round(b["ytm"], 2), "opts": opts, "why": why}


# ---------- 抓資料、出題 ----------
token = st.secrets.get("LOBSTER_TOKEN", "")
admin = st.query_params.get("admin", "") == "1"

if not token:
    st.error("還沒設定 LOBSTER_TOKEN，抓不到報價。請在 Streamlit Cloud 的 Secrets 裡補上。")
    st.stop()

try:
    bonds, updated = load_bonds(API, token)
except Exception as e:
    st.error(f"報價載入失敗：{str(e)[:160]}")
    st.stop()

pool = pool_of(bonds)
if len(pool) < N_ROUNDS:
    st.error(f"符合出題條件的債券只有 {len(pool)} 檔，不夠出一局（需要 {N_ROUNDS} 檔）。"
             "請放寬程式上方的 YTM_RANGE 或 MIN_GAP。")
    st.stop()

if admin and st.button("🎲 換一組題目"):
    st.session_state.pop("picks", None)
if "picks" not in st.session_state:
    st.session_state["picks"] = pick(pool, N_ROUNDS, MIN_SEP)
picks = st.session_state["picks"]

# ---------- 注入並顯示 ----------
config = {
    "url": st.secrets.get("SUPABASE_URL", ""),
    "key": st.secrets.get("SUPABASE_ANON_KEY", ""),
    "table": st.secrets.get("SUPABASE_TABLE", "yield_stopwatch_scores"),
}
inject = ("<script>"
          f"window.__QUESTIONS__={json.dumps([make_q(b) for b in picks], ensure_ascii=False)};"
          f"window.__FOOTNOTE__={json.dumps(FOOTNOTE, ensure_ascii=False)};"
          f"window.__ASOF__={json.dumps(updated or '', ensure_ascii=False)};"
          f"window.__SUPABASE__={json.dumps(config) if config['url'] and config['key'] else 'null'};"
          "</script>")
html = Path(__file__).with_name("yield_stopwatch_game.html").read_text(encoding="utf-8")
html = html.replace("<head>", "<head>" + inject, 1)

components.html(html, height=900, scrolling=True)

# ---------- 以下只有 ?admin=1 看得到 ----------
if admin:
    st.caption(f"報價檔 {updated}　·　可出題 {len(pool)} 檔"
               f"（溢價 {sum(1 for b in pool if b['px'] > 100)} 檔）")
    st.download_button("⬇️ 下載這組題目的獨立 HTML", data=html.encode("utf-8"),
                       file_name="yield_stopwatch.html", mime="text/html")
    st.table([{"債券": b["name"][:14], "評等": b["rating"], "票面%": round(b["cpn"], 3),
               "殖利率%": round(b["ytm"], 2), "年期": round(b["yrs"], 1),
               "價格": round(b["px"], 2)} for b in picks])
