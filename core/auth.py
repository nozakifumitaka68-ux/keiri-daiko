"""
パスワード認証モジュール

Streamlit Cloud 公開時に未認証アクセスを防ぐ簡易ログイン機能。
パスワードの取得元優先順位:
  1. Streamlit Cloud Secrets (st.secrets["APP_PASSWORD"])
  2. .env の APP_PASSWORD (os.getenv)
  3. デフォルト(設定がない場合は認証スキップ)
"""

from __future__ import annotations

import os
from typing import Any

import streamlit as st


def _get_password() -> str | None:
    """設定されたアプリパスワードを取得"""
    # 1) Streamlit Cloud Secrets を優先
    try:
        if hasattr(st, "secrets"):
            secret = st.secrets.get("APP_PASSWORD")
            if secret:
                return str(secret)
    except (FileNotFoundError, KeyError, AttributeError, Exception):
        pass

    # 2) 環境変数(.env) からの読込
    env_password = os.getenv("APP_PASSWORD")
    if env_password:
        return env_password

    return None


def require_login() -> bool:
    """
    認証ガード。
    パスワード未設定なら認証スキップ(ローカル開発用)。
    パスワード設定済なら、ログイン成功するまで画面を返さない。

    Returns:
        True: 認証通過(以降のアプリ表示OK)
        False: 認証画面表示中(以降の処理は中断すべき)
    """
    password = _get_password()
    if not password:
        # ローカル開発でパスワード未設定 → 認証スキップ
        return True

    # セッション初期化
    if "authenticated" not in st.session_state:
        st.session_state["authenticated"] = False

    if st.session_state["authenticated"]:
        return True

    # 認証画面を表示
    _render_login_form(password)
    return False


def _render_login_form(correct_password: str) -> None:
    """ログイン画面のレンダリング"""
    # ロゴ・タイトル
    st.markdown("""
    <div style="display: flex; justify-content: center; padding: 4rem 0 2rem 0;">
      <div style="background: linear-gradient(135deg, #1E2761 0%, #141B47 100%);
                  padding: 3rem 4rem; border-radius: 20px; max-width: 500px;
                  box-shadow: 0 8px 30px rgba(30, 39, 97, 0.2);">
        <div style="text-align: center;">
          <div style="font-size: 3rem; margin-bottom: 1rem;">📒</div>
          <h2 style="color: #F59E0B !important; margin: 0 0 0.5rem 0;">KEIRI-DAIKO</h2>
          <div style="color: #E8EEF9; opacity: 0.85; font-size: 0.95rem;">経理代行システム MVP</div>
          <div style="color: #E8EEF9; opacity: 0.6; font-size: 0.8rem; margin-top: 1.5rem;">
            このページは認証が必要です
          </div>
        </div>
      </div>
    </div>
    """, unsafe_allow_html=True)

    col_l, col_c, col_r = st.columns([1, 2, 1])
    with col_c:
        with st.form("login_form", clear_on_submit=False):
            password_input = st.text_input(
                "🔑 パスワード",
                type="password",
                placeholder="共有されたパスワードを入力",
            )
            submitted = st.form_submit_button(
                "ログイン",
                type="primary",
                use_container_width=True,
            )

            if submitted:
                if password_input == correct_password:
                    st.session_state["authenticated"] = True
                    st.rerun()
                else:
                    st.error("❌ パスワードが違います")

        st.caption(
            "※ このシステムはデモ用です。実際のクライアント情報は入力しないでください。"
        )


def render_logout_button() -> None:
    """サイドバーに置くログアウトボタン"""
    if st.session_state.get("authenticated"):
        if st.sidebar.button("🚪 ログアウト", use_container_width=True):
            st.session_state["authenticated"] = False
            st.rerun()
