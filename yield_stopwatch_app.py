# -*- coding: utf-8 -*-
"""
殖利率碼表（Streamlit 版）
先答出該檔債券的殖利率（YTC）→ 那個數字就是碼表的目標秒數 → 把碼表停在那個數字上。

計分：每題滿分 100 ＝ 答對 50 ＋ 手感最多 50。三題滿分 300，越高越好。
      手感分看「停表時間 vs 你瞄準的數字」，再除以盲數時間正規化，所以不同券公平。

Secrets（Streamlit Cloud → Settings → Secrets）：
    SUPABASE_URL        排行榜用
    SUPABASE_ANON_KEY   排行榜用，只能用 anon public key
排行榜要先在 Supabase 的 SQL Editor 跑一次 supabase_setup.sql。

換券單：改下面 BONDS 那一塊就好。ytc 填報價檔「YTM/YTC」欄的後面那個數字。
網址帶 ?admin=1 會多出答案表和「下載獨立 HTML」，貼給別人的連結不要帶。
"""
import json
import random
from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components

st.set_page_config(page_title="殖利率碼表", page_icon="⏱️", layout="centered")

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

# =====================================================================
#  題庫：要換券就改這一塊
#  ytc 用報價檔「YTM/YTC (%)」欄位後面那個數字（可賣回債以 YTC 為準）
# =====================================================================
ASOF = "2026-09-18"
BONDS = [
    {"code": "BB26100005", "name": "Alphabet公司債15", "rating": "AA+",
     "cpn": 6.25,  "px": 101.73, "ytc": 6.10, "mat": "2046/8/15", "yrs": 19.82, "freq": "每半年"},
    {"code": "BB26090020", "name": "亞馬遜公司債11", "rating": "AA",
     "cpn": 6.00,  "px": 98.29,  "ytc": 6.15, "mat": "2046/7/9",  "yrs": 19.72, "freq": "每半年"},
    {"code": "BB26100006", "name": "Alphabet公司債16", "rating": "AA+",
     "cpn": 5.45,  "px": 99.29,  "ytc": 5.55, "mat": "2036/8/15", "yrs": 9.82,  "freq": "每半年"},
    {"code": "BB26070003", "name": "輝達公司債11", "rating": "AA",
     "cpn": 4.95,  "px": 95.47,  "ytc": 5.58, "mat": "2036/6/15", "yrs": 9.65,  "freq": "每半年"},
    {"code": "BB26090015", "name": "萬事達卡公司債6", "rating": "A+",
     "cpn": 5.00,  "px": 96.88,  "ytc": 5.43, "mat": "2036/6/8",  "yrs": 9.63,  "freq": "每半年"},
    {"code": "BB26040015", "name": "美國運通公司債6", "rating": "A−",
     "cpn": 5.667, "px": 101.44, "ytc": 5.45, "mat": "2036/4/25", "yrs": 9.51,  "freq": "每半年"},
    {"code": "BB23030001", "name": "高盛金融國際公司債29", "rating": "A",
     "cpn": 0.0,   "px": 72.09,  "ytc": 5.29, "mat": "2033/2/28", "yrs": 6.35,  "freq": "無（零息）"},
    {"code": "BB26090006", "name": "德意志銀行公司債7", "rating": "A",
     "cpn": 0.0,   "px": 79.00,  "ytc": 6.29, "mat": "2031/7/20", "yrs": 4.75,  "freq": "無（零息）"},
]

N_ROUNDS   = 3      # 一局幾檔
MIN_SEP    = 0.10   # 同一局裡兩檔的目標秒數至少差這麼多，不然感覺像同一題
MAX_ZERO   = 1      # 一局最多幾檔零息債

FOOTNOTE = (f"本遊戲僅供內部教育訓練使用。題目中的報價與殖利率為 {ASOF} 的參考資料，"
            "可能已經變動，實際條件與價格以總行報價為準，不構成投資建議或要約。"
            "可賣回債以 YTC 計算。債券投資須留意利率風險、信用風險與流動性風險。請勿外流。")


def valid(trio):
    """一局三檔的組合規則。"""
    ys = sorted(b["ytc"] for b in trio)
    if any(ys[i + 1] - ys[i] < MIN_SEP for i in range(len(ys) - 1)):
        return False
    if sum(1 for b in trio if b["cpn"] == 0) > MAX_ZERO:
        return False
    return True


def pick(n=N_ROUNDS):
    """隨機抽 n 檔，符合規則；盡量帶一檔溢價也帶一檔折價（教材才完整）。"""
    rng = random.Random()
    best = None
    for _ in range(400):
        trio = rng.sample(BONDS, min(n, len(BONDS)))
        if not valid(trio):
            continue
        mixed = any(b["px"] > 100 for b in trio) and any(b["px"] < 100 for b in trio)
        if mixed:
            return sorted(trio, key=lambda b: b["ytc"])
        if best is None:
            best = trio
    return sorted(best or rng.sample(BONDS, n), key=lambda b: b["ytc"])


def make_q(b):
    """
    三個選項：正解 ＋ 兩個誘答。
    一般券拿「票面利率」當其中一個誘答（把票面當殖利率是最常見的誤會）；
    零息債票面是 0，放進去沒有鑑別度，所以改生兩個合理的數字。
    """
    rng = random.Random()
    ans = round(b["ytc"], 2)
    opts = [ans]

    def add(target_side):
        for _ in range(60):
            d = round(ans + target_side * rng.uniform(0.40, 1.10), 2)
            if d > 0.3 and all(abs(d - o) >= 0.25 for o in opts):
                opts.append(d)
                return
        opts.append(round(ans + target_side * 0.7, 2))

    if b["cpn"] > 0:
        cpn = round(b["cpn"], 2)
        if abs(cpn - ans) >= 0.25:
            opts.append(cpn)                       # 票面陷阱
            add(-1 if cpn > ans else 1)            # 誘答擺在票面反方向
        else:
            add(1)
            add(-1)
    else:
        add(1)
        add(-1)
    rng.shuffle(opts)

    if b["cpn"] == 0:
        why = (f"這是<b>零息債</b>，整個期間一毛利息都不配。你用 {b['px']:.2f} 買進，"
               f"到期拿回 100，中間那 {100 - b['px']:.2f} 元的價差"
               f"攤在 {b['yrs']:.1f} 年裡，就是 {ans:.2f}% 的殖利率——全部來自價差，不是票息。")
    elif b["px"] < 100:
        disc = 100 - b["px"]
        why = (f"價格 {b['px']:.2f} 是<b>折價</b>，到期還你 100，中間那 {disc:.2f} 元價差"
               f"攤在 {b['yrs']:.1f} 年裡（一年約 {disc / b['yrs']:.2f} 元）也算報酬，"
               f"所以殖利率 {ans:.2f}% 高於票面 {b['cpn']:.2f}%。")
    else:
        why = (f"價格 {b['px']:.2f} 是<b>溢價</b>，你買得比面額貴 {b['px'] - 100:.2f} 元，"
               f"到期只還你 100，那段差額要從票息裡扣回去，"
               f"所以殖利率 {ans:.2f}% 低於票面 {b['cpn']:.2f}%。")

    sub = "　·　".join(x for x in (b["code"], b["mat"], b["rating"], b["freq"]) if x)
    return {"iss": b["name"], "sub": sub, "cpn": round(b["cpn"], 3), "yrs": round(b["yrs"], 1),
            "px": round(b["px"], 2), "ans": ans, "opts": opts, "why": why}


# ---------- 出題 ----------
admin = st.query_params.get("admin", "") == "1"
if admin and st.button("🎲 換一組題目"):
    st.session_state.pop("picks", None)
if "picks" not in st.session_state:
    st.session_state["picks"] = pick()
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
          f"window.__ASOF__={json.dumps(ASOF, ensure_ascii=False)};"
          f"window.__SUPABASE__={json.dumps(config) if config['url'] and config['key'] else 'null'};"
          "</script>")
html = Path(__file__).with_name("yield_stopwatch_game.html").read_text(encoding="utf-8")
html = html.replace("<head>", "<head>" + inject, 1)

components.html(html, height=940, scrolling=True)

# ---------- 以下只有 ?admin=1 看得到 ----------
if admin:
    st.caption(f"題庫 {len(BONDS)} 檔　·　報價 {ASOF}")
    st.download_button("⬇️ 下載這組題目的獨立 HTML", data=html.encode("utf-8"),
                       file_name="yield_stopwatch.html", mime="text/html")
    st.table([{"債券": b["name"], "評等": b["rating"], "票面%": b["cpn"],
               "價格": b["px"], "YTC%": b["ytc"], "年期": b["yrs"]} for b in picks])
