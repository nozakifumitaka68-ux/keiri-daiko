"""
経理代行システム - Streamlit UI

ブラウザで「アップロード→確認・編集→突合→登録」を完結させる画面。
起動: streamlit run app.py
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st
import yaml
from dotenv import load_dotenv

from core.auth import render_logout_button, require_login
from core.bank_statement import import_csv as import_bank_csv
from core.card_statement import import_csv as import_card_csv
from core.jst import to_jst_display
from core.matcher import run_bank_matching, run_matching
from core.mf_client import get_mf_client
from core.pipeline import process_receipt, retry_ocr
from core.storage import (
    delete_bank_statement,
    delete_card_statement,
    delete_entry,
    find_bank_statements_by_client,
    find_card_statements_by_client,
    find_pending_receipts,
    find_unmatched_bank_payments,
    find_unsettled_card_statements,
    get_receipt_image_bytes,
    get_receipt_image_path,
    load_bank_statements,
    load_card_statements,
    load_deleted_bank_statements,
    load_deleted_card_statements,
    load_deleted_history,
    load_history,
    restore_bank_statement,
    restore_card_statement,
    restore_entry,
    update_bank_statement,
    update_card_statement,
    update_entry,
)

load_dotenv()


# ===================================
# Streamlit Cloud Secrets を環境変数に注入
# ===================================
# Cloudでは .env 不在のため、st.secrets から os.environ にコピーして
# core モジュールが os.getenv() で読めるようにする
def _hydrate_env_from_secrets() -> None:
    try:
        if hasattr(st, "secrets"):
            for key in (
                "APP_PASSWORD",
                "OCR_ENGINE",
                "OCR_STUB_MODE",
                "GEMINI_API_KEY",
                "GEMINI_MODEL",
                "ANTHROPIC_API_KEY",
                "MF_MODE",
                "MF_CLIENT_ID",
                "MF_CLIENT_SECRET",
                "MF_REDIRECT_URI",
                "SUPABASE_URL",
                "SUPABASE_KEY",
            ):
                value = st.secrets.get(key) if key in st.secrets else None
                if value is not None and not os.getenv(key):
                    os.environ[key] = str(value)
    except (FileNotFoundError, KeyError, AttributeError):
        pass


_hydrate_env_from_secrets()


# ===================================
# ページ設定
# ===================================
st.set_page_config(
    page_title="経理代行システム",
    page_icon="📒",
    layout="wide",
    initial_sidebar_state="expanded",
)


# ===================================
# カラーパレット(PPT資料と統一)
# ===================================
NAVY = "#1E2761"
NAVY_DARK = "#141B47"
ICE = "#E8EEF9"
ICE_LIGHT = "#F5F7FB"
AMBER = "#F59E0B"
AMBER_DARK = "#B45309"
CORAL = "#EF4444"
GREEN = "#10B981"
TEAL = "#14B8A6"
GRAY = "#64748B"
GRAY_LIGHT = "#94A3B8"


# ===================================
# カスタムCSS(Streamlit のデフォルトを上書き)
# ===================================
def inject_custom_css() -> None:
    st.markdown(f"""
    <style>
    /* ベース */
    .main .block-container {{
        padding-top: 1.5rem;
        padding-bottom: 3rem;
        max-width: 1400px;
    }}

    /* ヘッダー */
    h1 {{
        color: {NAVY};
        font-weight: 700;
        letter-spacing: -0.02em;
    }}
    h2 {{
        color: {NAVY};
        font-weight: 600;
        margin-top: 1.5rem;
    }}
    h3 {{
        color: {NAVY};
        font-weight: 600;
    }}

    /* タブのデザイン */
    .stTabs [data-baseweb="tab-list"] {{
        gap: 4px;
        border-bottom: 2px solid {ICE};
    }}
    .stTabs [data-baseweb="tab"] {{
        padding: 0.6rem 1.2rem;
        border-radius: 8px 8px 0 0;
        font-weight: 500;
        font-size: 0.95rem;
        color: {GRAY};
    }}
    .stTabs [aria-selected="true"] {{
        background-color: {NAVY} !important;
        color: white !important;
    }}

    /* メトリックカード */
    [data-testid="stMetric"] {{
        background-color: white;
        border: 1px solid {ICE};
        padding: 1rem 1.2rem;
        border-radius: 12px;
        box-shadow: 0 1px 3px rgba(30, 39, 97, 0.05);
    }}
    [data-testid="stMetricLabel"] {{
        color: {GRAY};
        font-size: 0.85rem;
        font-weight: 500;
    }}
    [data-testid="stMetricValue"] {{
        color: {NAVY};
        font-weight: 700;
    }}

    /* プライマリボタン(アンバー) */
    .stButton > button[kind="primary"] {{
        background-color: {AMBER};
        color: white;
        border: none;
        font-weight: 600;
        padding: 0.5rem 1.2rem;
        border-radius: 8px;
        box-shadow: 0 1px 3px rgba(245, 158, 11, 0.3);
    }}
    .stButton > button[kind="primary"]:hover {{
        background-color: {AMBER_DARK};
        box-shadow: 0 2px 6px rgba(245, 158, 11, 0.4);
    }}

    /* セカンダリボタン */
    .stButton > button {{
        border-radius: 8px;
        font-weight: 500;
    }}

    /* サイドバー */
    [data-testid="stSidebar"] {{
        background-color: {NAVY};
    }}
    [data-testid="stSidebar"] * {{
        color: white !important;
    }}
    [data-testid="stSidebar"] h1,
    [data-testid="stSidebar"] h2,
    [data-testid="stSidebar"] h3 {{
        color: {AMBER} !important;
    }}
    [data-testid="stSidebar"] [data-testid="stMetric"] {{
        background-color: {NAVY_DARK};
        border: 1px solid {NAVY_DARK};
    }}
    [data-testid="stSidebar"] [data-testid="stMetricLabel"] {{
        color: {ICE} !important;
    }}
    [data-testid="stSidebar"] [data-testid="stMetricValue"] {{
        color: {AMBER} !important;
    }}
    [data-testid="stSidebar"] .stSelectbox label,
    [data-testid="stSidebar"] .stTextInput label {{
        color: {ICE} !important;
    }}

    /* ステータスバッジ用クラス */
    .badge {{
        display: inline-block;
        padding: 3px 10px;
        border-radius: 12px;
        font-size: 0.78rem;
        font-weight: 600;
        letter-spacing: 0.02em;
    }}
    .badge-cash {{
        background-color: #FEF3C7;
        color: {AMBER_DARK};
    }}
    .badge-card {{
        background-color: #DCFCE7;
        color: #166534;
    }}
    .badge-settlement {{
        background-color: #DBEAFE;
        color: #1E40AF;
    }}
    .badge-warning {{
        background-color: #FEE2E2;
        color: #991B1B;
    }}

    /* カード(汎用ラッパ) */
    .info-card {{
        background-color: white;
        border: 1px solid {ICE};
        border-radius: 12px;
        padding: 1.2rem 1.5rem;
        margin-bottom: 1rem;
        box-shadow: 0 1px 3px rgba(30, 39, 97, 0.05);
    }}
    .info-card-accent {{
        border-left: 4px solid {AMBER};
    }}

    /* ハイライトボックス */
    .highlight-box {{
        background: linear-gradient(135deg, {NAVY} 0%, {NAVY_DARK} 100%);
        color: white;
        padding: 1.5rem 2rem;
        border-radius: 16px;
        margin: 1rem 0;
    }}
    .highlight-box h3 {{
        color: {AMBER} !important;
        margin-top: 0;
    }}

    /* 表(dataframe) */
    [data-testid="stDataFrame"] {{
        border: 1px solid {ICE};
        border-radius: 8px;
        overflow: hidden;
    }}

    /* expander */
    [data-testid="stExpander"] {{
        border: 1px solid {ICE};
        border-radius: 12px;
        background-color: white;
    }}

    /* file uploader */
    [data-testid="stFileUploader"] section {{
        background-color: white;
        border: 2px dashed {ICE};
        border-radius: 12px;
        padding: 1.5rem;
    }}
    [data-testid="stFileUploader"] section:hover {{
        border-color: {AMBER};
        background-color: #FFFBF0;
    }}

    /* alert系の角丸を統一 */
    .stAlert {{
        border-radius: 12px;
    }}

    /* divider 弱める */
    hr {{
        border-color: {ICE} !important;
        margin: 1.5rem 0 !important;
    }}
    </style>
    """, unsafe_allow_html=True)


# ===================================
# 設定読込
# ===================================
@st.cache_data
def load_config() -> dict[str, Any]:
    config_path = Path(__file__).parent / "config.yaml"
    if config_path.exists():
        with open(config_path, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    return {}


config = load_config()


# ===================================
# ヘルパ
# ===================================
def _status_label(status: str | None) -> str:
    """仕訳の支払区分ラベル(共通)"""
    return {
        "cash_pending": "💴 現金(突合待)",
        "card_matched": "💳 カード払",
        "cash_confirmed": "💴 現金確定",
        "settlement": "🏦 取り崩し",
        "ocr_failed": "❌ OCR失敗",
    }.get(status or "", status or "")


def _detect_ocr_engine() -> str:
    """現在のOCRエンジンを判定(サイドバー表示用)"""
    explicit = (os.getenv("OCR_ENGINE") or "").lower().strip()
    if explicit in ("stub", "gemini", "claude"):
        return explicit
    if os.getenv("OCR_STUB_MODE") == "1":
        return "stub"
    if os.getenv("GEMINI_API_KEY"):
        return "gemini"
    if os.getenv("ANTHROPIC_API_KEY"):
        return "claude"
    return "stub"


def status_badge(status: str | None, kind: str = "journal") -> str:
    """ステータスをHTMLバッジに変換"""
    if kind == "journal":
        mapping = {
            "cash_pending": ("💴 現金(突合待)", "badge-cash"),
            "card_matched": ("💳 カード払", "badge-card"),
            "cash_confirmed": ("💴 現金確定", "badge-cash"),
            "settlement": ("🏦 取り崩し", "badge-settlement"),
            "ocr_failed": ("❌ OCR失敗", "badge-warning"),
        }
    elif kind == "card":
        mapping = {
            "unmatched": ("⏳ 未突合", "badge-warning"),
            "matched": ("✅ 突合済", "badge-card"),
        }
    elif kind == "card_settlement":
        mapping = {
            "settled": ("🏦 引落済", "badge-settlement"),
            None: ("⏳ 未決済", "badge-warning"),
        }
    elif kind == "bank":
        mapping = {
            "unmatched": ("⏳ 未突合", "badge-warning"),
            "matched_card_payment": ("💳 カード引落", "badge-settlement"),
            "matched_other": ("✅ 突合済", "badge-card"),
        }
    else:
        mapping = {}

    label, css = mapping.get(status, (status or "—", "badge"))
    return f'<span class="badge {css}">{label}</span>'


def metric_with_delta(
    label: str,
    value: str | int,
    delta: str | None = None,
    help_text: str | None = None,
) -> None:
    """カラム内で使うシンプルなメトリック"""
    st.metric(label, value, delta=delta, help=help_text)


# ===================================
# サイドバー
# ===================================
def render_sidebar() -> dict[str, Any]:
    st.sidebar.markdown(f"""
    <div style="padding: 0.5rem 0 1rem 0; border-bottom: 1px solid rgba(255,255,255,0.1); margin-bottom: 1rem;">
        <div style="font-size: 1.5rem; font-weight: 700; color: {AMBER};">📒 KEIRI-DAIKO</div>
        <div style="font-size: 0.75rem; color: {ICE}; opacity: 0.7;">経理代行システム MVP</div>
    </div>
    """, unsafe_allow_html=True)

    # クライアント選択
    clients = config.get("clients", {})
    client_options = {cid: c.get("name", cid) for cid, c in clients.items()}
    if not client_options:
        client_options = {"client_a": "クライアントA"}

    client_id = st.sidebar.selectbox(
        "🏢 クライアント",
        options=list(client_options.keys()),
        format_func=lambda x: client_options[x],
    )

    # クライアント別のクイックステータス
    history = load_history()
    pending = find_pending_receipts(client_id)
    cards = find_card_statements_by_client(client_id)
    bank = find_bank_statements_by_client(client_id)
    unsettled = find_unsettled_card_statements(client_id)
    unmatched_bank = find_unmatched_bank_payments(client_id)

    st.sidebar.markdown("##### 📊 現在の状況")
    col_a, col_b = st.sidebar.columns(2)
    with col_a:
        st.metric("仕訳", len(history))
        st.metric("カード明細", len(cards))
    with col_b:
        st.metric("未突合領収書", len(pending))
        st.metric("未決済", len(unsettled))

    st.sidebar.divider()

    # モード表示
    st.sidebar.markdown("##### ⚙ 動作モード")

    # OCRエンジンを動的に判定(core/ocr.py の _select_engine と同じロジック)
    ocr_engine = _detect_ocr_engine()
    ocr_meta = {
        "gemini": ("Gemini Vision", "実APIで読取(無料枠)", GREEN),
        "claude": ("Claude Vision", "実APIで読取", GREEN),
        "stub": ("スタブモード", "ダミーデータを返す", AMBER),
    }
    label, desc, color = ocr_meta.get(ocr_engine, ("Unknown", "不明", AMBER))
    st.sidebar.markdown(f"""
    <div style="background-color: {NAVY_DARK}; padding: 0.6rem 0.8rem; border-radius: 8px; margin-bottom: 0.4rem; border-left: 3px solid {color};">
        <div style="font-size: 0.75rem; color: {ICE}; opacity: 0.7;">🤖 OCR</div>
        <div style="font-weight: 600; color: {color};">{label}</div>
        <div style="font-size: 0.7rem; color: {ICE}; opacity: 0.6;">{desc}</div>
    </div>
    """, unsafe_allow_html=True)
    ocr_stub = ocr_engine == "stub"

    mf_mode = os.getenv("MF_MODE", config.get("mf_mode", "mock"))
    if mf_mode == "mock":
        st.sidebar.markdown(f"""
        <div style="background-color: {NAVY_DARK}; padding: 0.6rem 0.8rem; border-radius: 8px; margin-bottom: 0.4rem; border-left: 3px solid {AMBER};">
            <div style="font-size: 0.75rem; color: {ICE}; opacity: 0.7;">💼 マネフォ</div>
            <div style="font-weight: 600; color: {AMBER};">モックモード</div>
            <div style="font-size: 0.7rem; color: {ICE}; opacity: 0.6;">ローカルJSONに保存</div>
        </div>
        """, unsafe_allow_html=True)
    else:
        st.sidebar.markdown(f"""
        <div style="background-color: {NAVY_DARK}; padding: 0.6rem 0.8rem; border-radius: 8px; margin-bottom: 0.4rem; border-left: 3px solid {GREEN};">
            <div style="font-size: 0.75rem; color: {ICE}; opacity: 0.7;">💼 マネフォ</div>
            <div style="font-weight: 600; color: {GREEN};">実API連携</div>
        </div>
        """, unsafe_allow_html=True)

    # ストレージモード(Supabase / ローカルJSON)
    has_supabase = bool(os.getenv("SUPABASE_URL") and os.getenv("SUPABASE_KEY"))
    if has_supabase:
        st.sidebar.markdown(f"""
        <div style="background-color: {NAVY_DARK}; padding: 0.6rem 0.8rem; border-radius: 8px; margin-bottom: 0.4rem; border-left: 3px solid {GREEN};">
            <div style="font-size: 0.75rem; color: {ICE}; opacity: 0.7;">💾 ストレージ</div>
            <div style="font-weight: 600; color: {GREEN};">Supabase 永続化</div>
            <div style="font-size: 0.7rem; color: {ICE}; opacity: 0.6;">DB+Storage(暗号化済)</div>
        </div>
        """, unsafe_allow_html=True)
    else:
        st.sidebar.markdown(f"""
        <div style="background-color: {NAVY_DARK}; padding: 0.6rem 0.8rem; border-radius: 8px; margin-bottom: 0.4rem; border-left: 3px solid {CORAL};">
            <div style="font-size: 0.75rem; color: {ICE}; opacity: 0.7;">💾 ストレージ</div>
            <div style="font-weight: 600; color: {CORAL};">ローカルJSON</div>
            <div style="font-size: 0.7rem; color: {ICE}; opacity: 0.6;">⚠ 再デプロイで消失します</div>
        </div>
        """, unsafe_allow_html=True)

    st.sidebar.divider()
    st.sidebar.markdown("##### 💡 ヒント")
    st.sidebar.caption(
        "1. 領収書をアップロード\n\n"
        "2. カード明細CSVを取込\n\n"
        "3. 領収書突合で未払金確定\n\n"
        "4. 銀行明細CSVを取込\n\n"
        "5. 引落突合で取り崩し仕訳生成"
    )

    return {
        "client_id": client_id,
        "client_name": client_options[client_id],
        "mf_mode": mf_mode,
        "ocr_stub": ocr_stub,
        "stats": {
            "history_count": len(history),
            "pending_count": len(pending),
            "cards_count": len(cards),
            "bank_count": len(bank),
            "unsettled_count": len(unsettled),
            "unmatched_bank_count": len(unmatched_bank),
        },
    }


# ===================================
# タブ0: ダッシュボード
# ===================================
def render_dashboard(state: dict[str, Any]) -> None:
    stats = state["stats"]

    # ヒーローセクション
    st.markdown(f"""
    <div class="highlight-box">
        <div style="display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 1rem;">
            <div>
                <h3 style="color: {AMBER}; margin: 0;">📒 経理代行システム</h3>
                <p style="margin: 0.5rem 0 0 0; opacity: 0.85; font-size: 1.05rem;">
                    {state['client_name']} の処理状況
                </p>
            </div>
            <div style="text-align: right;">
                <div style="font-size: 0.85rem; opacity: 0.7;">{datetime.now().strftime('%Y年%m月%d日 %H:%M')}</div>
                <div style="font-size: 0.75rem; opacity: 0.5;">最終更新</div>
            </div>
        </div>
    </div>
    """, unsafe_allow_html=True)

    # KPI カード
    st.subheader("📊 ステータスサマリ")
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        metric_with_delta("📥 全仕訳", stats["history_count"], help_text="作成済の仕訳エントリ総数")
    with c2:
        metric_with_delta("⏳ 突合待ち領収書", stats["pending_count"], help_text="まだカード明細と紐付いていない領収書")
    with c3:
        metric_with_delta("💳 カード明細", stats["cards_count"], help_text="取込済のカード利用明細")
    with c4:
        metric_with_delta("🏦 銀行明細", stats["bank_count"], help_text="取込済の銀行入出金明細")

    st.divider()

    # 仕訳フローの可視化
    st.subheader("🔄 仕訳生成フロー")
    history = load_history()
    client_history = [h for h in history if h.get("client_id") == state["client_id"]]
    cash_pending = sum(1 for h in client_history if h.get("match_status") == "cash_pending")
    card_matched = sum(1 for h in client_history if h.get("match_status") == "card_matched")
    settlement = sum(1 for h in client_history if h.get("match_status") == "settlement")

    flow_html = f"""
    <div style="display: flex; gap: 0.5rem; align-items: stretch; flex-wrap: nowrap; overflow-x: auto;">
      {_flow_card("Step 1", "領収書投入", "借方:勘定科目<br>貸方:現金", cash_pending, NAVY, "cash_pending")}
      <div style="display: flex; align-items: center; padding: 0 0.3rem; color: {AMBER}; font-size: 1.5rem; font-weight: bold;">→</div>
      {_flow_card("Step 2", "カード明細突合", "借方:勘定科目<br>貸方:未払金", card_matched, AMBER, "card_matched")}
      <div style="display: flex; align-items: center; padding: 0 0.3rem; color: {AMBER}; font-size: 1.5rem; font-weight: bold;">→</div>
      {_flow_card("Step 3", "銀行引落突合", "借方:未払金<br>貸方:普通預金", settlement, GREEN, "settlement")}
    </div>
    """
    st.markdown(flow_html, unsafe_allow_html=True)

    st.divider()

    # 2カラム: 直近アクティビティ + 注意事項
    col_l, col_r = st.columns([3, 2])

    with col_l:
        st.subheader("🕒 直近の活動")
        recent = sorted(client_history, key=lambda h: h.get("created_at", ""), reverse=True)[:8]
        if recent:
            for h in recent:
                _render_activity_item(h)
        else:
            st.info("まだ活動はありません。📤 アップロードタブから領収書を投入してください。")

    with col_r:
        st.subheader("⚠ 要対応")
        # 確認必須 / 未決済 / 未突合銀行
        needs_review = [h for h in client_history if h.get("needs_review")]
        if needs_review:
            st.warning(f"確認必須の仕訳: {len(needs_review)}件")
        if stats["unsettled_count"] > 0:
            st.info(f"未決済のカード明細: {stats['unsettled_count']}件 → 銀行明細で引落突合を")
        if stats["unmatched_bank_count"] > 0:
            st.info(f"未突合の銀行出金: {stats['unmatched_bank_count']}件")
        if not needs_review and stats["unsettled_count"] == 0 and stats["unmatched_bank_count"] == 0:
            st.success("✨ すべての処理が完了しています")

        st.divider()
        st.markdown("##### 📌 クイックアクション")
        st.caption("各タブで以下が実行できます:")
        st.markdown(
            f"""
            - **📤 アップロード** — 領収書投入
            - **💳 カード明細** — CSV取込
            - **🔗 領収書突合** — 未払金化
            - **🏦 銀行明細** — CSV取込
            - **💸 引落突合** — 取り崩し仕訳
            """
        )


def _flow_card(step: str, title: str, journal: str, count: int, color: str, status_key: str) -> str:
    return f"""
    <div style="flex: 1; min-width: 200px; background: white; border: 2px solid {color}; border-radius: 14px; padding: 1.2rem; box-shadow: 0 2px 6px rgba(30, 39, 97, 0.06);">
        <div style="font-size: 0.7rem; color: {color}; font-weight: 700; letter-spacing: 0.1em;">{step}</div>
        <div style="font-size: 1.1rem; font-weight: 700; color: {NAVY}; margin: 0.3rem 0;">{title}</div>
        <div style="background: {ICE_LIGHT}; padding: 0.5rem 0.7rem; border-radius: 6px; font-family: monospace; font-size: 0.78rem; color: {GRAY}; margin-bottom: 0.6rem;">
            {journal}
        </div>
        <div style="display: flex; align-items: baseline; gap: 0.3rem;">
            <div style="font-size: 1.8rem; font-weight: 700; color: {color};">{count}</div>
            <div style="font-size: 0.75rem; color: {GRAY};">件</div>
        </div>
    </div>
    """


def _render_activity_item(h: dict[str, Any]) -> None:
    created = to_jst_display(h.get("created_at"))
    vendor = h.get("vendor") or "—"
    amount = h.get("amount") or 0
    debit = h.get("debit", "")
    credit = h.get("credit", "")
    badge = status_badge(h.get("match_status"), kind="journal")

    st.markdown(f"""
    <div style="background: white; border: 1px solid {ICE}; border-radius: 10px; padding: 0.8rem 1rem; margin-bottom: 0.5rem; display: flex; align-items: center; justify-content: space-between; gap: 1rem;">
        <div style="flex: 1; min-width: 0;">
            <div style="display: flex; align-items: center; gap: 0.6rem; flex-wrap: wrap;">
                <strong style="color: {NAVY}; font-size: 0.95rem; overflow: hidden; text-overflow: ellipsis;">{vendor[:40]}</strong>
                {badge}
            </div>
            <div style="font-size: 0.78rem; color: {GRAY}; margin-top: 0.2rem;">
                {created}  •  借方:{debit}  /  貸方:{credit}
            </div>
        </div>
        <div style="font-weight: 700; color: {NAVY}; font-size: 1.05rem; white-space: nowrap;">
            ¥{amount:,}
        </div>
    </div>
    """, unsafe_allow_html=True)


# ===================================
# タブ1: アップロード
# ===================================
def render_upload_tab(state: dict[str, Any]) -> None:
    st.subheader("📤 領収書アップロード")
    st.caption(
        "複数の領収書(画像/PDF)をアップロードして、AI読取・仕訳生成を行います。"
        "処理した仕訳は自動で保存され、「📚 仕訳台帳」タブで確認・編集できます。"
    )

    uploaded_files = st.file_uploader(
        "領収書ファイルをドラッグ&ドロップ または ファイル選択",
        type=["jpg", "jpeg", "png", "pdf", "webp"],
        accept_multiple_files=True,
        key="upload_files",
    )

    col_btn, col_opt = st.columns([1, 3])
    with col_btn:
        process_btn = st.button(
            "🚀 処理開始",
            type="primary",
            disabled=not uploaded_files,
            use_container_width=True,
        )
    with col_opt:
        force_register = st.checkbox(
            "🔄 重複検出されても強制登録(通常はOFF推奨)",
            value=False,
            help="同じ領収書を意図的にもう一度登録したい時だけ ON にする",
        )

    if process_btn and uploaded_files:
        results = []
        progress = st.progress(0.0, text="処理開始...")

        for i, uf in enumerate(uploaded_files):
            progress.progress(
                (i + 1) / len(uploaded_files),
                text=f"処理中 {i + 1}/{len(uploaded_files)}: {uf.name}",
            )

            with tempfile.NamedTemporaryFile(
                delete=False,
                suffix=Path(uf.name).suffix,
            ) as tmp:
                tmp.write(uf.read())
                tmp_path = tmp.name

            try:
                # 仕訳は常に自動保存(編集・削除は仕訳台帳タブで実施)
                result = process_receipt(
                    tmp_path,
                    client_id=state["client_id"],
                    auto_register=True,
                    archive=False,
                    skip_duplicates=True,
                    force_register=force_register,
                    save_image=True,
                    original_filename=uf.name,
                )
                result["original_name"] = uf.name
                results.append(result)
            finally:
                os.unlink(tmp_path)

        progress.empty()
        st.session_state["last_results"] = results

        ok_count = sum(1 for r in results if r.get("status") == "ok")
        dup_count = sum(1 for r in results if r.get("status") == "duplicate_skipped")
        review_count = sum(
            1 for r in results
            if r.get("status") == "ok" and (r.get("journal") or {}).get("needs_review")
        )

        msg_parts = []
        if ok_count > 0:
            msg_parts.append(f"✅ {ok_count}件 新規登録")
        if dup_count > 0:
            msg_parts.append(f"⏭ {dup_count}件 重複スキップ")

        if msg_parts:
            st.success(" / ".join(msg_parts) + "(仕訳台帳に保存済み)")

        if dup_count > 0:
            with st.expander(f"⏭ 重複スキップされた領収書({dup_count}件)", expanded=True):
                for r in results:
                    if r.get("status") != "duplicate_skipped":
                        continue
                    name = r.get("original_name", "—")
                    j = r.get("journal", {}) or {}
                    info = r.get("duplicate_info", {}) or {}
                    st.markdown(f"**📄 {name}**  ¥{j.get('amount') or 0:,}  ·  {j.get('vendor', '—')}")
                    if info.get("exact_hash_match"):
                        st.caption(
                            "🔁 ファイル内容が完全一致(SHA-256ハッシュ): "
                            + ", ".join(
                                f"{m['id_short']}({m['vendor']} ¥{m['amount']:,})"
                                for m in info["exact_hash_match"]
                            )
                        )
                    if info.get("data_match"):
                        st.caption(
                            "📋 取引データが類似一致(日付・金額・支払先): "
                            + ", ".join(
                                f"{m['id_short']}({m['vendor']} ¥{m['amount']:,})"
                                for m in info["data_match"]
                            )
                        )
                st.info("💡 もし意図的に重複登録したい場合は、上の「強制登録」をONにしてもう一度処理してください")

        if review_count > 0:
            st.warning(
                f"⚠ {review_count}件は確認が必要です。"
                f"「📚 仕訳台帳」タブで「⚠ 要確認のみ」フィルタを使うと素早くチェックできます。"
            )

    # 直近の処理結果(セッション中のみ)
    if "last_results" in st.session_state and st.session_state["last_results"]:
        st.divider()
        st.markdown("##### 📋 今のセッションでの処理結果")
        st.caption("（リロードすると消えます。永続データは「📚 仕訳台帳」タブで確認してください）")
        results = st.session_state["last_results"]

        c1, c2, c3, c4 = st.columns(4)
        with c1:
            metric_with_delta("処理件数", len(results))
        with c2:
            ok = sum(1 for r in results if r.get("status") == "ok")
            metric_with_delta("成功", ok)
        with c3:
            review = sum(
                1 for r in results
                if (r.get("journal") or {}).get("needs_review")
            )
            metric_with_delta("⚠ 要確認", review)
        with c4:
            total = sum((r.get("journal", {}) or {}).get("amount") or 0 for r in results)
            metric_with_delta("合計金額", f"¥{total:,}")

        summary_df = _to_summary_df(results)
        st.dataframe(summary_df, use_container_width=True, hide_index=True)

        st.info(
            "💡 修正・削除したい項目があれば、上部の「📚 仕訳台帳」タブを開き、"
            "「✏ 編集・削除」モードに切り替えてください。"
        )


# ===================================
# タブ2: カード明細
# ===================================
def render_card_tab(state: dict[str, Any]) -> None:
    st.subheader("💳 カード利用明細")
    st.caption(
        "カード会社からダウンロードした明細CSVを取り込み、領収書との突合に使います。"
    )

    all_statements = find_card_statements_by_client(state["client_id"])
    unmatched = [s for s in all_statements if s.get("match_status") == "unmatched"]
    matched = [s for s in all_statements if s.get("match_status") == "matched"]

    # KPI カード
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        metric_with_delta("全明細", len(all_statements))
    with c2:
        metric_with_delta("⏳ 未突合", len(unmatched))
    with c3:
        metric_with_delta("✅ 突合済", len(matched))
    with c4:
        total = sum(s.get("amount") or 0 for s in all_statements)
        metric_with_delta("合計金額", f"¥{total:,}")

    st.divider()

    col_l, col_r = st.columns([3, 2])
    with col_l:
        st.markdown("##### 📥 CSVアップロード")
        uploaded_csv = st.file_uploader(
            "カード明細CSV(三井住友・楽天・マネフォ書出し等)",
            type=["csv"],
            accept_multiple_files=False,
            key="card_csv_uploader",
        )
    with col_r:
        st.markdown("##### ⚙ 取込オプション")
        card_name = st.text_input(
            "カード名(任意)",
            value="",
            key="card_name_input",
            placeholder="例: 三井住友VISA",
            help="未入力ならCSV内の値か '未指定' になります",
        )

    if st.button(
        "📥 CSVを取り込む",
        type="primary",
        disabled=not uploaded_csv,
        use_container_width=False,
    ):
        csv_bytes = uploaded_csv.read()
        result = import_card_csv(
            csv_bytes,
            client_id=state["client_id"],
            card_name=card_name or None,
            skip_duplicates=True,
        )
        msg_parts = []
        if result["saved_count"] > 0:
            msg_parts.append(f"✅ {result['saved_count']}件 新規取込")
        if result["skipped_count"] > 0:
            msg_parts.append(f"⏭ {result['skipped_count']}件 重複スキップ")
        if msg_parts:
            st.success(" / ".join(msg_parts))
        else:
            st.warning("取り込めるデータがありませんでした")
        st.rerun()

    st.divider()
    st.markdown("##### 📋 取り込み済み明細")

    if not all_statements:
        st.info("まだ明細がありません。上のフォームからCSVをアップロードしてください。")
        return

    view_mode = st.radio(
        "表示形式",
        options=["📋 一覧表(高速)", "✏ 編集・削除(個別操作)"],
        horizontal=True,
        label_visibility="collapsed",
        key="card_view_mode",
    )

    if view_mode == "📋 一覧表(高速)":
        df = pd.DataFrame([
            {
                "ID": s.get("id", "")[:8],
                "状態": "✅ 突合済" if s.get("match_status") == "matched" else "⏳ 未突合",
                "決済": "🏦 引落済" if s.get("settlement_status") == "settled" else "—",
                "利用日": s.get("usage_date"),
                "計上日": s.get("posting_date"),
                "支払先": s.get("vendor_raw"),
                "金額": s.get("amount"),
                "カード": s.get("card_name"),
            }
            for s in all_statements[::-1]
        ])
        st.dataframe(df, use_container_width=True, hide_index=True)
    else:
        st.caption("各明細を展開して編集・削除できます")
        for s in all_statements[::-1][:50]:
            _render_card_row(s)
        if len(all_statements) > 50:
            st.info(f"50件まで表示中(全{len(all_statements)}件)")


def _render_card_row(s: dict[str, Any]) -> None:
    """個別カード明細の編集・削除UI"""
    sid = s.get("id", "")
    short = sid[:8]
    title = (
        f"💳 {s.get('vendor_raw', '—')[:30]}  ¥{s.get('amount') or 0:,}  "
        f"·  {s.get('usage_date')}  ·  {short}"
    )
    with st.expander(title, expanded=False):
        col_l, col_r = st.columns([3, 2])
        with col_l:
            with st.form(key=f"edit_card_{sid}"):
                st.markdown("**✏ 編集**")
                ec1, ec2 = st.columns(2)
                with ec1:
                    new_usage = st.text_input("利用日", value=s.get("usage_date", "") or "", key=f"c_usage_{sid}")
                    new_posting = st.text_input("計上日", value=s.get("posting_date", "") or "", key=f"c_posting_{sid}")
                    new_vendor = st.text_input("支払先", value=s.get("vendor_raw", "") or "", key=f"c_vendor_{sid}")
                with ec2:
                    new_amount = st.number_input("金額", value=int(s.get("amount") or 0), step=1, key=f"c_amount_{sid}")
                    new_card = st.text_input("カード名", value=s.get("card_name", "") or "", key=f"c_card_{sid}")
                    new_memo = st.text_input("備考", value=s.get("memo", "") or "", key=f"c_memo_{sid}")

                if st.form_submit_button("💾 保存", type="primary", use_container_width=True):
                    update_card_statement(sid, {
                        "usage_date": new_usage,
                        "posting_date": new_posting,
                        "vendor_raw": new_vendor,
                        "amount": new_amount,
                        "card_name": new_card,
                        "memo": new_memo,
                    })
                    st.success("✅ 保存しました")
                    st.rerun()

        with col_r:
            st.markdown("**🗑 削除**")
            confirm_key = f"c_confirm_del_{sid}"
            if st.session_state.get(confirm_key):
                st.warning("本当に削除しますか?")
                d1, d2 = st.columns(2)
                with d1:
                    if st.button("✅ 削除する", key=f"c_do_del_{sid}", type="primary", use_container_width=True):
                        delete_card_statement(sid, reason="ユーザー操作")
                        st.session_state[confirm_key] = False
                        st.success("ゴミ箱に移動しました")
                        st.rerun()
                with d2:
                    if st.button("キャンセル", key=f"c_cancel_del_{sid}", use_container_width=True):
                        st.session_state[confirm_key] = False
                        st.rerun()
            else:
                if st.button("🗑 削除", key=f"c_del_{sid}", use_container_width=True):
                    st.session_state[confirm_key] = True
                    st.rerun()

            if s.get("match_status") == "matched":
                st.info(f"📎 仕訳ID: {(s.get('matched_journal_id') or '')[:8]} と紐付き済")
            if s.get("settlement_status") == "settled":
                st.info("🏦 銀行引落で決済済")


# ===================================
# タブ4: 領収書突合
# ===================================
def render_match_tab(state: dict[str, Any]) -> None:
    st.subheader("🔗 領収書 ↔ カード明細 突合")
    st.caption(
        "現金払いとして仮登録された仕訳を、取り込み済のカード明細と照合します。"
        "マッチしたものは「貸方:現金 → 貸方:未払金」に書き換わります。"
    )

    pending_receipts = find_pending_receipts(state["client_id"])
    all_statements = find_card_statements_by_client(state["client_id"])
    unmatched_statements = [s for s in all_statements if s.get("match_status") == "unmatched"]

    c1, c2, c3 = st.columns(3)
    with c1:
        metric_with_delta("⏳ 突合待ち仕訳", len(pending_receipts))
    with c2:
        metric_with_delta("⏳ 未突合カード明細", len(unmatched_statements))
    with c3:
        metric_with_delta("✅ 突合済明細", len(all_statements) - len(unmatched_statements))

    st.divider()

    col_l, col_r = st.columns(2)
    with col_l:
        if st.button(
            "🔍 ドライラン(プレビュー)",
            use_container_width=True,
            help="DBは更新せず、マッチング結果だけ表示",
        ):
            result = run_matching(client_id=state["client_id"], dry_run=True)
            st.session_state["match_preview"] = result
    with col_r:
        if st.button(
            "✅ 突合実行(仕訳を更新)",
            type="primary",
            use_container_width=True,
            disabled=not pending_receipts or not unmatched_statements,
        ):
            result = run_matching(client_id=state["client_id"], dry_run=False)
            st.success(f"✅ {result['matched_count']}件の仕訳をカード払いに更新しました")
            st.session_state["match_preview"] = result
            st.rerun()

    preview = st.session_state.get("match_preview")
    if not preview:
        if pending_receipts and unmatched_statements:
            st.info("🔍 ドライラン または ✅ 突合実行 でマッチング結果が表示されます。")
        elif not pending_receipts:
            st.success("✨ すべての仕訳がカード明細と突合済 or 現金確定済です")
        else:
            st.warning("カード明細がまだ取り込まれていません。「💳 カード明細」タブから取り込んでください。")
        return

    st.divider()

    if preview.get("dry_run"):
        st.warning("⚠ ドライラン結果(まだ DB は更新されていません)")
    else:
        st.success(f"✅ 突合完了({preview['matched_count']}件マッチ)")

    if preview["matched"]:
        st.markdown(f"##### ✅ マッチした仕訳({len(preview['matched'])}件)")
        df_m = pd.DataFrame([
            {
                "日付": m["journal_date"],
                "領収書(支払先)": m["journal_vendor"],
                "明細(支払先)": m["statement_vendor"],
                "金額": m["journal_amount"],
                "類似度": f"{m['vendor_similarity']:.0%}",
            }
            for m in preview["matched"]
        ])
        st.dataframe(df_m, use_container_width=True, hide_index=True)

    col_um1, col_um2 = st.columns(2)
    with col_um1:
        st.markdown(f"##### ⏳ 未マッチの仕訳({len(preview['unmatched_receipts'])}件)")
        if preview["unmatched_receipts"]:
            df_ur = pd.DataFrame(preview["unmatched_receipts"])
            st.dataframe(df_ur, use_container_width=True, hide_index=True)
        else:
            st.success("全件マッチ済")

    with col_um2:
        st.markdown(f"##### ⏳ 未マッチの明細({len(preview['unmatched_statements'])}件)")
        if preview["unmatched_statements"]:
            df_us = pd.DataFrame(preview["unmatched_statements"])
            st.dataframe(df_us, use_container_width=True, hide_index=True)
        else:
            st.success("全件マッチ済")

    with st.expander("⚙ 現在の突合しきい値"):
        st.json(preview.get("config", {}))


# ===================================
# タブ5: 銀行明細
# ===================================
def render_bank_tab(state: dict[str, Any]) -> None:
    st.subheader("🏦 銀行明細")
    st.caption(
        "普通預金の入出金CSVを取り込みます。カード会社への引落と未払金を突合し、"
        "取り崩し仕訳(借方:未払金 / 貸方:普通預金)を自動生成します。"
    )

    all_bank = find_bank_statements_by_client(state["client_id"])
    unmatched_payments = find_unmatched_bank_payments(state["client_id"])
    matched_payments = [b for b in all_bank if b.get("match_status") != "unmatched"]

    c1, c2, c3, c4 = st.columns(4)
    with c1:
        metric_with_delta("全明細", len(all_bank))
    with c2:
        metric_with_delta("⏳ 未突合の出金", len(unmatched_payments))
    with c3:
        metric_with_delta("✅ 突合済", len(matched_payments))
    with c4:
        total_out = abs(sum(p.get("amount") or 0 for p in unmatched_payments))
        metric_with_delta("未突合出金合計", f"¥{total_out:,}")

    st.divider()

    col_l, col_r = st.columns([3, 2])
    with col_l:
        st.markdown("##### 📥 CSVアップロード")
        uploaded_csv = st.file_uploader(
            "銀行明細CSV(三井住友銀行・みずほ・楽天等)",
            type=["csv"],
            accept_multiple_files=False,
            key="bank_csv_uploader",
        )
    with col_r:
        st.markdown("##### ⚙ 取込オプション")
        account_name = st.text_input(
            "口座名(任意)",
            value="",
            key="bank_account_input",
            placeholder="例: 三井住友銀行 普通",
        )

    if st.button(
        "📥 銀行CSVを取り込む",
        type="primary",
        disabled=not uploaded_csv,
    ):
        csv_bytes = uploaded_csv.read()
        result = import_bank_csv(
            csv_bytes,
            client_id=state["client_id"],
            account_name=account_name or None,
            skip_duplicates=True,
        )
        msg_parts = []
        if result["saved_count"] > 0:
            msg_parts.append(f"✅ {result['saved_count']}件 新規取込")
        if result["skipped_count"] > 0:
            msg_parts.append(f"⏭ {result['skipped_count']}件 重複スキップ")
        if msg_parts:
            st.success(" / ".join(msg_parts))
        else:
            st.warning("取り込めるデータがありませんでした")
        st.rerun()

    st.divider()
    st.markdown("##### 📋 取り込み済み銀行明細")

    if not all_bank:
        st.info("まだ銀行明細がありません。上のフォームからCSVをアップロードしてください。")
        return

    view_mode = st.radio(
        "表示形式",
        options=["📋 一覧表(高速)", "✏ 編集・削除(個別操作)"],
        horizontal=True,
        label_visibility="collapsed",
        key="bank_view_mode",
    )

    if view_mode == "📋 一覧表(高速)":
        df = pd.DataFrame([
            {
                "ID": b.get("id", "")[:8],
                "状態": _bank_status_label(b.get("match_status")),
                "取引日": b.get("transaction_date"),
                "摘要": b.get("description"),
                "金額": b.get("amount"),
                "残高": b.get("balance"),
                "口座": b.get("account_name"),
            }
            for b in all_bank[::-1]
        ])
        st.dataframe(df, use_container_width=True, hide_index=True)
    else:
        st.caption("各明細を展開して編集・削除できます")
        for b in all_bank[::-1][:50]:
            _render_bank_row(b)
        if len(all_bank) > 50:
            st.info(f"50件まで表示中(全{len(all_bank)}件)")


def _render_bank_row(b: dict[str, Any]) -> None:
    """個別銀行明細の編集・削除UI"""
    bid = b.get("id", "")
    short = bid[:8]
    sign = "+" if (b.get("amount") or 0) > 0 else ""
    title = (
        f"🏦 {b.get('description', '—')[:30]}  {sign}¥{b.get('amount') or 0:,}  "
        f"·  {b.get('transaction_date')}  ·  {short}"
    )
    with st.expander(title, expanded=False):
        col_l, col_r = st.columns([3, 2])
        with col_l:
            with st.form(key=f"edit_bank_{bid}"):
                st.markdown("**✏ 編集**")
                ec1, ec2 = st.columns(2)
                with ec1:
                    new_date = st.text_input("取引日", value=b.get("transaction_date", "") or "", key=f"b_date_{bid}")
                    new_desc = st.text_input("摘要", value=b.get("description", "") or "", key=f"b_desc_{bid}")
                    new_amount = st.number_input(
                        "金額(出金=負)", value=int(b.get("amount") or 0), step=1, key=f"b_amount_{bid}",
                    )
                with ec2:
                    new_account = st.text_input("口座名", value=b.get("account_name", "") or "", key=f"b_account_{bid}")
                    cur_balance = b.get("balance")
                    new_balance = st.number_input(
                        "残高", value=int(cur_balance) if cur_balance is not None else 0, step=1, key=f"b_balance_{bid}",
                    )

                if st.form_submit_button("💾 保存", type="primary", use_container_width=True):
                    update_bank_statement(bid, {
                        "transaction_date": new_date,
                        "description": new_desc,
                        "amount": new_amount,
                        "account_name": new_account,
                        "balance": new_balance,
                    })
                    st.success("✅ 保存しました")
                    st.rerun()

        with col_r:
            st.markdown("**🗑 削除**")
            confirm_key = f"b_confirm_del_{bid}"
            if st.session_state.get(confirm_key):
                st.warning("本当に削除しますか?")
                d1, d2 = st.columns(2)
                with d1:
                    if st.button("✅ 削除する", key=f"b_do_del_{bid}", type="primary", use_container_width=True):
                        delete_bank_statement(bid, reason="ユーザー操作")
                        st.session_state[confirm_key] = False
                        st.success("ゴミ箱に移動しました")
                        st.rerun()
                with d2:
                    if st.button("キャンセル", key=f"b_cancel_del_{bid}", use_container_width=True):
                        st.session_state[confirm_key] = False
                        st.rerun()
            else:
                if st.button("🗑 削除", key=f"b_del_{bid}", use_container_width=True):
                    st.session_state[confirm_key] = True
                    st.rerun()

            if b.get("match_status") == "matched_card_payment":
                st.info("💳 カード引落として突合済")


def _bank_status_label(status: str | None) -> str:
    return {
        "unmatched": "⏳ 未突合",
        "matched_card_payment": "💳 カード引落突合済",
        "matched_other": "✅ その他突合済",
    }.get(status or "", status or "")


# ===================================
# タブ6: 引落突合
# ===================================
def render_bank_match_tab(state: dict[str, Any]) -> None:
    st.subheader("💸 銀行引落 ↔ 未払金 突合")
    st.caption(
        "銀行口座からのカード会社引落と、未決済のカード明細群を照合し、"
        "未払金の取り崩し仕訳を自動生成します。"
    )

    bank_payments = find_unmatched_bank_payments(state["client_id"])
    unsettled = find_unsettled_card_statements(state["client_id"])

    c1, c2, c3 = st.columns(3)
    with c1:
        metric_with_delta("⏳ 未突合出金(銀行)", len(bank_payments))
    with c2:
        metric_with_delta("⏳ 未決済カード明細", len(unsettled))
    with c3:
        metric_with_delta(
            "未決済合計",
            f"¥{sum(s.get('amount') or 0 for s in unsettled):,}",
        )

    st.divider()

    col_l, col_r = st.columns(2)
    with col_l:
        if st.button(
            "🔍 ドライラン(プレビュー)",
            use_container_width=True,
            key="bank_match_dry",
        ):
            result = run_bank_matching(client_id=state["client_id"], dry_run=True)
            st.session_state["bank_match_preview"] = result
    with col_r:
        if st.button(
            "✅ 引落突合実行(取り崩し仕訳生成)",
            type="primary",
            use_container_width=True,
            disabled=not bank_payments or not unsettled,
            key="bank_match_run",
        ):
            result = run_bank_matching(client_id=state["client_id"], dry_run=False)
            st.success(f"✅ {result['matched_count']}件の引落を取り崩し仕訳に変換しました")
            st.session_state["bank_match_preview"] = result
            st.rerun()

    preview = st.session_state.get("bank_match_preview")
    if not preview:
        if bank_payments and unsettled:
            st.info("🔍 ドライラン または ✅ 引落突合実行 で結果が表示されます。")
        elif not bank_payments:
            st.success("✨ 銀行明細でカード引落として処理すべきものがありません")
        else:
            st.warning("未決済のカード明細がありません。先に「🔗 領収書突合」を実行してください。")
        return

    st.divider()

    if preview.get("dry_run"):
        st.warning("⚠ ドライラン結果(まだ DB は更新されていません)")
    else:
        st.success(f"✅ 引落突合完了({preview['matched_count']}件マッチ)")

    if preview["matched"]:
        st.markdown(f"##### ✅ マッチした引落({len(preview['matched'])}件)")
        df_m = pd.DataFrame([
            {
                "引落日": m["bank_date"],
                "銀行摘要": m["bank_description"],
                "識別カード会社": m["card_company"] or "(摘要から特定できず)",
                "対象カード名": m["card_name"],
                "対象明細件数": m["card_statement_count"],
                "合計金額": m["total_amount"],
            }
            for m in preview["matched"]
        ])
        st.dataframe(df_m, use_container_width=True, hide_index=True)

    col_um1, col_um2 = st.columns(2)
    with col_um1:
        st.markdown(f"##### ⏳ 未マッチの出金({len(preview['unmatched_bank_payments'])}件)")
        if preview["unmatched_bank_payments"]:
            df_u = pd.DataFrame(preview["unmatched_bank_payments"])
            st.dataframe(df_u, use_container_width=True, hide_index=True)
        else:
            st.success("全件マッチ済")

    with col_um2:
        st.markdown("##### カード別 未決済サマリ")
        cards_summary = preview.get("cards_summary_by_name", {})
        if cards_summary:
            df_c = pd.DataFrame([
                {
                    "カード名": k,
                    "件数": v["count"],
                    "合計": v["total"],
                    "決済済": "✅" if v["settled"] else "⏳",
                }
                for k, v in cards_summary.items()
            ])
            st.dataframe(df_c, use_container_width=True, hide_index=True)
        else:
            st.info("未決済カード明細がありません")

    with st.expander("⚙ 現在の引落突合設定"):
        st.json(preview.get("config", {}))


# ===================================
# タブ7: 仕訳台帳
# ===================================
def render_history_tab(state: dict[str, Any]) -> None:
    st.subheader("📚 仕訳台帳")
    st.caption("全仕訳エントリの一覧。状態(現金/カード払/取り崩し)別にフィルタもできます。")

    history = load_history()
    filtered = [e for e in history if e.get("client_id") == state["client_id"]]

    # KPI
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        metric_with_delta("全履歴件数", len(history))
    with c2:
        metric_with_delta("このクライアント", len(filtered))
    with c3:
        total = sum(h.get("amount") or 0 for h in filtered)
        metric_with_delta("合計金額", f"¥{total:,}")
    with c4:
        review = sum(1 for h in filtered if h.get("needs_review"))
        metric_with_delta("⚠ 要確認", review)

    if not filtered:
        st.info("このクライアントの履歴はまだありません。")
        return

    st.divider()

    # クイックフィルタ
    col_qf1, col_qf2, col_qf3 = st.columns([1, 1, 3])
    with col_qf1:
        only_review = st.toggle(
            "⚠ 要確認のみ",
            value=False,
            help="needs_review=True の仕訳だけ表示する",
        )
    with col_qf2:
        only_failed = st.toggle(
            "❌ OCR失敗のみ",
            value=False,
            help="再OCR or 手動入力が必要な失敗エントリだけ表示",
        )
    with col_qf3:
        status_filter = st.multiselect(
            "状態フィルタ",
            options=["cash_pending", "card_matched", "settlement", "cash_confirmed", "ocr_failed"],
            default=[],
            format_func=lambda s: {
                "cash_pending": "💴 現金(突合待)",
                "card_matched": "💳 カード払",
                "settlement": "🏦 取り崩し",
                "cash_confirmed": "💴 現金確定",
                "ocr_failed": "❌ OCR失敗",
            }.get(s, s),
            label_visibility="collapsed",
            placeholder="状態で絞り込み(複数選択可)",
        )

    if only_review:
        filtered = [h for h in filtered if h.get("needs_review")]
    if only_failed:
        filtered = [h for h in filtered if h.get("match_status") == "ocr_failed"]
    if status_filter:
        filtered = [h for h in filtered if h.get("match_status") in status_filter]

    # 表示モード切替
    view_mode = st.radio(
        "表示形式",
        options=["📋 一覧表(高速)", "✏ 編集・削除(個別操作)"],
        horizontal=True,
        label_visibility="collapsed",
    )

    if view_mode == "📋 一覧表(高速)":
        df = pd.DataFrame([
            {
                "ID": h.get("id", "")[:8],
                "登録日": to_jst_display(h.get("created_at")),
                "取引日": h.get("transaction_date"),
                "支払先": h.get("vendor"),
                "借方": h.get("debit"),
                "貸方": h.get("credit"),
                "金額": h.get("amount"),
                "税率": f"{h.get('tax_rate') or '—'}%" if h.get("tax_rate") else "—",
                "支払区分": _status_label(h.get("match_status")),
                "要確認": "⚠" if h.get("needs_review") else "✓",
            }
            for h in filtered[::-1]
        ])
        st.dataframe(df, use_container_width=True, hide_index=True)

        with st.expander("🔍 履歴の生データを見る(JSON)"):
            st.json(filtered[::-1])
    else:
        # 個別編集・削除モード
        st.caption("各仕訳を展開して編集・削除できます")
        for h in filtered[::-1][:50]:  # 表示は最新50件まで(パフォーマンス対策)
            _render_journal_row(h)
        if len(filtered) > 50:
            st.info(f"50件まで表示中(全{len(filtered)}件)。フィルタで絞り込んでください")


def _render_journal_row(h: dict[str, Any]) -> None:
    """個別仕訳の編集・削除UI"""
    entry_id = h.get("id", "")
    short_id = entry_id[:8]
    vendor = h.get("vendor") or "—"
    amount = h.get("amount") or 0
    badge = status_badge(h.get("match_status"), kind="journal")
    is_failed = h.get("match_status") == "ocr_failed"

    # 失敗時はタイトルにアイコン付け
    icon = "❌" if is_failed else "📄"
    title = f"{icon} {vendor[:30]}  ¥{amount:,}  ·  {h.get('transaction_date') or '日付不明'}  ·  {short_id}"

    with st.expander(title, expanded=is_failed):  # 失敗エントリは展開状態で表示
        st.markdown(badge, unsafe_allow_html=True)

        # OCR失敗時の専用UI: 再OCRボタン + エラー表示
        if is_failed:
            st.error("⚠ OCR読取に失敗しています。「🔄 OCR再実行」または下のフォームで「✏ 手動入力」してください")
            ocr_raw = h.get("ocr_raw") or {}
            err = ocr_raw.get("_error") or "(詳細不明)"
            st.caption(f"📝 エラー詳細: {err}")
            if ocr_raw.get("_retried_at"):
                st.caption(f"🔄 最終リトライ: {ocr_raw.get('_retried_at')}")

            # 再OCRボタン
            retry_col1, retry_col2 = st.columns([1, 3])
            with retry_col1:
                if st.button(
                    "🔄 OCR再実行",
                    key=f"retry_ocr_{entry_id}",
                    type="primary",
                    use_container_width=True,
                ):
                    with st.spinner("Gemini で再OCR実行中..."):
                        result = retry_ocr(entry_id, client_id=h.get("client_id", "client_a"))
                    if result["status"] == "ok":
                        st.success("✅ OCR成功!仕訳を更新しました")
                        st.rerun()
                    elif result["status"] == "still_failed":
                        st.error(f"❌ まだ失敗: {result.get('error')}")
                    else:
                        st.error(f"❌ {result.get('error', '不明なエラー')}")
            with retry_col2:
                st.caption("👈 押すと保存済画像で再度OCRを試みます。それでもダメなら下で手動入力してください。")

        col_l, col_r = st.columns([3, 2])
        with col_l:
            with st.form(key=f"edit_journal_{entry_id}"):
                st.markdown("**✏ 編集**")
                ec1, ec2 = st.columns(2)
                with ec1:
                    new_date = st.text_input("日付", value=h.get("transaction_date", ""), key=f"j_date_{entry_id}")
                    new_vendor = st.text_input("支払先", value=h.get("vendor", "") or "", key=f"j_vendor_{entry_id}")
                    new_amount = st.number_input("金額", value=int(h.get("amount") or 0), step=1, key=f"j_amount_{entry_id}")
                    new_debit = st.text_input("借方", value=h.get("debit", "") or "", key=f"j_debit_{entry_id}")
                with ec2:
                    new_credit = st.text_input("貸方", value=h.get("credit", "") or "", key=f"j_credit_{entry_id}")
                    tax_options = [10, 8, 0]
                    cur_tax = h.get("tax_rate") or 10
                    new_tax = st.selectbox(
                        "税率(%)",
                        options=tax_options,
                        index=tax_options.index(cur_tax) if cur_tax in tax_options else 0,
                        key=f"j_tax_{entry_id}",
                    )
                    new_desc = st.text_input("摘要", value=h.get("description", "") or "", key=f"j_desc_{entry_id}")
                    status_options = ["cash_pending", "card_matched", "cash_confirmed", "settlement"]
                    cur_status = h.get("match_status") or "cash_pending"
                    new_status = st.selectbox(
                        "支払区分",
                        options=status_options,
                        index=status_options.index(cur_status) if cur_status in status_options else 0,
                        format_func=_status_label,
                        key=f"j_status_{entry_id}",
                    )

                save = st.form_submit_button("💾 保存", type="primary", use_container_width=True)
                if save:
                    update_entry(entry_id, {
                        "transaction_date": new_date,
                        "vendor": new_vendor,
                        "amount": new_amount,
                        "debit": new_debit,
                        "credit": new_credit,
                        "tax_rate": new_tax,
                        "description": new_desc,
                        "match_status": new_status,
                    })
                    st.success("✅ 保存しました")
                    st.rerun()

        with col_r:
            st.markdown("**🗑 削除**")
            confirm_key = f"j_confirm_del_{entry_id}"
            if st.session_state.get(confirm_key):
                st.warning("本当に削除しますか?(ゴミ箱から復元可能)")
                d1, d2 = st.columns(2)
                with d1:
                    if st.button("✅ 削除する", key=f"j_do_del_{entry_id}", type="primary", use_container_width=True):
                        delete_entry(entry_id, reason="ユーザー操作")
                        st.session_state[confirm_key] = False
                        st.success("ゴミ箱に移動しました")
                        st.rerun()
                with d2:
                    if st.button("キャンセル", key=f"j_cancel_del_{entry_id}", use_container_width=True):
                        st.session_state[confirm_key] = False
                        st.rerun()
            else:
                if st.button("🗑 削除", key=f"j_del_{entry_id}", use_container_width=True):
                    st.session_state[confirm_key] = True
                    st.rerun()

            if h.get("needs_review"):
                st.info("⚠ 要確認: " + " / ".join(h.get("review_reasons", [])))

        # 領収書原本プレビュー(Supabase / ローカル両対応)
        receipt_path = h.get("receipt_path")
        receipt_filename = h.get("receipt_filename") or "領収書"
        if receipt_path:
            data = get_receipt_image_bytes(receipt_path)
            if data:
                ext = Path(receipt_filename).suffix.lower() or Path(receipt_path).suffix.lower()
                with st.expander(f"📎 領収書原本({receipt_filename})", expanded=False):
                    img_col, btn_col = st.columns([3, 1])
                    with img_col:
                        if ext in (".jpg", ".jpeg", ".png", ".webp", ".gif"):
                            from io import BytesIO
                            st.image(BytesIO(data), use_container_width=True)
                        elif ext == ".pdf":
                            try:
                                import tempfile as tf
                                from pdf2image import convert_from_bytes
                                images = convert_from_bytes(
                                    data, first_page=1, last_page=1, dpi=150,
                                )
                                if images:
                                    st.image(images[0], use_container_width=True)
                                    st.caption("(PDF 1ページ目をプレビュー表示)")
                            except Exception as e:
                                st.warning(f"PDFプレビュー失敗: {e}")
                        else:
                            st.info(f"プレビュー非対応の形式: {ext}")
                    with btn_col:
                        st.download_button(
                            "📥 ダウンロード",
                            data=data,
                            file_name=receipt_filename,
                            use_container_width=True,
                            key=f"dl_{entry_id}",
                        )
                        st.caption(f"📦 {len(data) / 1024:.1f} KB")
            else:
                st.caption("📎 領収書ファイルが見つかりません(削除/未保存)")

        # OCR raw 参照(エキスパンドで折りたたみ)
        ocr_raw = h.get("ocr_raw")
        if ocr_raw:
            with st.expander("🔍 AI読取結果(参照)", expanded=False):
                clean = {k: v for k, v in ocr_raw.items() if not k.startswith("_")}
                st.json(clean)


# ===================================
# タブ8: ゴミ箱(削除済データの復元)
# ===================================
def render_trash_tab(state: dict[str, Any]) -> None:
    st.subheader("🗑 ゴミ箱")
    st.caption(
        "削除した仕訳・カード明細・銀行明細はここに保管されます。"
        "復元ボタンで元に戻せます。"
    )

    deleted_journals = [
        h for h in load_deleted_history() if h.get("client_id") == state["client_id"]
    ]
    deleted_cards = [
        s for s in load_deleted_card_statements() if s.get("client_id") == state["client_id"]
    ]
    deleted_bank = [
        b for b in load_deleted_bank_statements() if b.get("client_id") == state["client_id"]
    ]

    c1, c2, c3 = st.columns(3)
    with c1:
        metric_with_delta("削除済 仕訳", len(deleted_journals))
    with c2:
        metric_with_delta("削除済 カード明細", len(deleted_cards))
    with c3:
        metric_with_delta("削除済 銀行明細", len(deleted_bank))

    if not (deleted_journals or deleted_cards or deleted_bank):
        st.info("ゴミ箱は空です")
        return

    st.divider()
    sub_tabs = st.tabs([
        f"📄 仕訳({len(deleted_journals)})",
        f"💳 カード明細({len(deleted_cards)})",
        f"🏦 銀行明細({len(deleted_bank)})",
    ])

    with sub_tabs[0]:
        if not deleted_journals:
            st.success("削除済の仕訳はありません")
        for h in deleted_journals[::-1]:
            _render_trash_row_journal(h)

    with sub_tabs[1]:
        if not deleted_cards:
            st.success("削除済のカード明細はありません")
        for s in deleted_cards[::-1]:
            _render_trash_row_card(s)

    with sub_tabs[2]:
        if not deleted_bank:
            st.success("削除済の銀行明細はありません")
        for b in deleted_bank[::-1]:
            _render_trash_row_bank(b)


def _render_trash_row_journal(h: dict[str, Any]) -> None:
    eid = h.get("id", "")
    deleted_at = to_jst_display(h.get("deleted_at"))
    title = (
        f"📄 {h.get('vendor', '—')[:30]}  ¥{h.get('amount') or 0:,}  ·  "
        f"削除: {deleted_at}"
    )
    with st.expander(title, expanded=False):
        col_l, col_r = st.columns([3, 1])
        with col_l:
            st.write(f"**取引日**: {h.get('transaction_date')}")
            st.write(f"**借方**: {h.get('debit')} / **貸方**: {h.get('credit')}")
            st.write(f"**摘要**: {h.get('description', '')}")
            if h.get("delete_reason"):
                st.caption(f"削除理由: {h['delete_reason']}")
        with col_r:
            if st.button("♻ 復元", key=f"restore_j_{eid}", type="primary", use_container_width=True):
                restore_entry(eid)
                st.success("✅ 復元しました")
                st.rerun()


def _render_trash_row_card(s: dict[str, Any]) -> None:
    sid = s.get("id", "")
    deleted_at = to_jst_display(s.get("deleted_at"))
    title = (
        f"💳 {s.get('vendor_raw', '—')[:30]}  ¥{s.get('amount') or 0:,}  ·  "
        f"削除: {deleted_at}"
    )
    with st.expander(title, expanded=False):
        col_l, col_r = st.columns([3, 1])
        with col_l:
            st.write(f"**利用日**: {s.get('usage_date')}")
            st.write(f"**カード**: {s.get('card_name', '—')}")
            if s.get("delete_reason"):
                st.caption(f"削除理由: {s['delete_reason']}")
        with col_r:
            if st.button("♻ 復元", key=f"restore_c_{sid}", type="primary", use_container_width=True):
                restore_card_statement(sid)
                st.success("✅ 復元しました")
                st.rerun()


def _render_trash_row_bank(b: dict[str, Any]) -> None:
    bid = b.get("id", "")
    deleted_at = to_jst_display(b.get("deleted_at"))
    title = (
        f"🏦 {b.get('description', '—')[:30]}  ¥{b.get('amount') or 0:,}  ·  "
        f"削除: {deleted_at}"
    )
    with st.expander(title, expanded=False):
        col_l, col_r = st.columns([3, 1])
        with col_l:
            st.write(f"**取引日**: {b.get('transaction_date')}")
            st.write(f"**口座**: {b.get('account_name', '—')}")
            if b.get("delete_reason"):
                st.caption(f"削除理由: {b['delete_reason']}")
        with col_r:
            if st.button("♻ 復元", key=f"restore_b_{bid}", type="primary", use_container_width=True):
                restore_bank_statement(bid)
                st.success("✅ 復元しました")
                st.rerun()


# ===================================
# ヘルパ
# ===================================
def _to_summary_df(results: list[dict[str, Any]]) -> pd.DataFrame:
    status_map = {
        "ok": "✅ 新規登録",
        "duplicate_skipped": "⏭ 重複スキップ",
        "ocr_failed": "❌ OCR失敗",
        "journal_failed": "❌ 仕訳生成失敗",
        "exception": "❌ 例外",
    }
    rows = []
    for r in results:
        j = r.get("journal", {}) or {}
        reg = r.get("registration") or {}
        status_label = status_map.get(r.get("status"), r.get("status"))
        rows.append({
            "ファイル": r.get("original_name", ""),
            "ステータス": status_label,
            "支払先": j.get("vendor"),
            "借方": j.get("debit"),
            "貸方": j.get("credit"),
            "金額": j.get("amount"),
            "要確認": "⚠" if j.get("needs_review") else "✓",
            "登録": reg.get("status", "未登録"),
        })
    return pd.DataFrame(rows)


# ===================================
# メイン
# ===================================
def main() -> None:
    inject_custom_css()

    # 認証ガード(パスワード未通過なら以降を表示しない)
    if not require_login():
        return

    state = render_sidebar()
    render_logout_button()

    tabs = st.tabs([
        "🏠 ダッシュボード",
        "📤 アップロード",
        "💳 カード明細",
        "🔗 領収書突合",
        "🏦 銀行明細",
        "💸 引落突合",
        "📚 仕訳台帳",
        "🗑 ゴミ箱",
    ])
    with tabs[0]:
        render_dashboard(state)
    with tabs[1]:
        render_upload_tab(state)
    with tabs[2]:
        render_card_tab(state)
    with tabs[3]:
        render_match_tab(state)
    with tabs[4]:
        render_bank_tab(state)
    with tabs[5]:
        render_bank_match_tab(state)
    with tabs[6]:
        render_history_tab(state)
    with tabs[7]:
        render_trash_tab(state)


if __name__ == "__main__":
    main()
