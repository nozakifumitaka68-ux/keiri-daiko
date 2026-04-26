"""
マネーフォワード API クライアント

Mock/Real の2実装を持ち、config.yaml の mf_mode で切替可能。
- Mock: ローカルJSONに保存して成功レスポンスを返す
- Real: マネーフォワード Cloud Accounting API への OAuth2 + 仕訳POST
"""

from __future__ import annotations

import json
import logging
import os
from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from .jst import now_iso, now_yyyymmdd
from .storage import save_entry

logger = logging.getLogger(__name__)

CONFIG_PATH = Path(__file__).parent.parent / "config.yaml"
LOG_DIR = Path(__file__).parent.parent / "data" / "logs"


# ===================================
# 抽象クラス
# ===================================
class MFClient(ABC):
    """マネーフォワードAPIクライアントの抽象インターフェース"""

    @abstractmethod
    def post_journal(self, journal: dict[str, Any]) -> dict[str, Any]:
        """
        仕訳を登録する。
        Returns:
            {"id": マネフォ仕訳ID, "status": "registered"|"draft"|"error", ...}
        """
        ...

    @abstractmethod
    def get_accounts(self) -> list[dict[str, Any]]:
        """勘定科目マスタ取得"""
        ...

    @abstractmethod
    def health_check(self) -> dict[str, Any]:
        """接続確認"""
        ...


# ===================================
# Mock 実装(API無くても動く)
# ===================================
class MockMFClient(MFClient):
    """
    マネフォAPIのモック実装。
    API情報未着の段階でも開発・検証を進めるためのスタブ。
    実際にはローカルJSONに保存して、登録成功したフリをする。
    """

    def post_journal(self, journal: dict[str, Any]) -> dict[str, Any]:
        # storage.save_entry に流して履歴保存
        saved = save_entry({
            **journal,
            "mf_mode": "mock",
            "registered_at": now_iso(),
        })

        # ログにも残す
        self._log_event("post_journal", {
            "input": journal,
            "saved_id": saved["id"],
        })

        return {
            "id": f"mock-{saved['id'][:8]}",
            "status": "registered",
            "internal_id": saved["id"],
            "mf_mode": "mock",
            "message": "モックモード: ローカルhistory.jsonに保存されました(マネフォには未登録)",
        }

    def get_accounts(self) -> list[dict[str, Any]]:
        """ダミーの勘定科目マスタを返す"""
        return [
            {"code": "601", "name": "消耗品費", "category": "経費"},
            {"code": "602", "name": "事務用品費", "category": "経費"},
            {"code": "603", "name": "旅費交通費", "category": "経費"},
            {"code": "604", "name": "接待交際費", "category": "経費"},
            {"code": "605", "name": "会議費", "category": "経費"},
            {"code": "606", "name": "通信費", "category": "経費"},
            {"code": "607", "name": "水道光熱費", "category": "経費"},
            {"code": "101", "name": "現金", "category": "資産"},
            {"code": "111", "name": "普通預金", "category": "資産"},
            {"code": "201", "name": "未払金", "category": "負債"},
            {"code": "211", "name": "役員借入金", "category": "負債"},
        ]

    def health_check(self) -> dict[str, Any]:
        return {
            "status": "ok",
            "mode": "mock",
            "message": "Mockクライアント稼働中。.env のMF_MODEを変更すれば実APIに切替可能",
        }

    def _log_event(self, event: str, data: dict[str, Any]) -> None:
        """イベントログ出力"""
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        log_file = LOG_DIR / f"mf_mock_{now_yyyymmdd()}.log"
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(json.dumps({
                "timestamp": now_iso(),
                "event": event,
                "data": data,
            }, ensure_ascii=False) + "\n")


# ===================================
# Real 実装(Day 3 で完成、現状は骨組み)
# ===================================
class RealMFClient(MFClient):
    """
    マネーフォワードクラウド会計の実APIクライアント。

    OAuth2 認可フロー: https://biz.moneyforward.com/support/account/guide/others/ot09.html
    APIエンドポイント: https://api.biz.moneyforward.com/

    現状は骨組み実装。client_id/client_secret 入手後に各メソッドを完成させる。
    """

    BASE_URL = "https://api.biz.moneyforward.com"
    TOKEN_URL = "https://api.biz.moneyforward.com/authorize/o/oauth/token"

    def __init__(self) -> None:
        self.client_id = os.getenv("MF_CLIENT_ID", "")
        self.client_secret = os.getenv("MF_CLIENT_SECRET", "")
        self.redirect_uri = os.getenv("MF_REDIRECT_URI", "http://localhost:8501/callback")
        self.access_token: str | None = self._load_token()

        if not self.client_id or not self.client_secret:
            logger.warning(
                "MF_CLIENT_ID/MF_CLIENT_SECRET が未設定です。"
                "実API呼出はエラーになります。"
            )

    def post_journal(self, journal: dict[str, Any]) -> dict[str, Any]:
        if not self.access_token:
            return {
                "id": None,
                "status": "error",
                "error": "認証トークンがありません。scripts/mf_auth.py で認可を完了してください",
            }

        # TODO: 実API仕様確定後にエンドポイント・ペイロード実装
        # import requests
        # response = requests.post(
        #     f"{self.BASE_URL}/api/v3/journals",
        #     headers={"Authorization": f"Bearer {self.access_token}"},
        #     json=self._to_mf_payload(journal),
        # )
        return {
            "id": None,
            "status": "not_implemented",
            "message": "RealMFClient.post_journal は Day 3 で実装予定。現状は骨組みのみ",
        }

    def get_accounts(self) -> list[dict[str, Any]]:
        # TODO: GET /api/v3/items または /api/v3/accounts
        return []

    def health_check(self) -> dict[str, Any]:
        return {
            "status": "skeleton" if not self.access_token else "ready",
            "mode": "real",
            "client_id_set": bool(self.client_id),
            "client_secret_set": bool(self.client_secret),
            "token_loaded": bool(self.access_token),
            "message": "実API実装は Day 3 で完成予定",
        }

    def _load_token(self) -> str | None:
        """保存済みアクセストークンを読込"""
        token_path = Path(__file__).parent.parent / "data" / "mf_token.json"
        if not token_path.exists():
            return None
        try:
            with open(token_path, encoding="utf-8") as f:
                data = json.load(f)
            return data.get("access_token")
        except Exception:
            return None

    def _to_mf_payload(self, journal: dict[str, Any]) -> dict[str, Any]:
        """
        内部仕訳形式 → マネフォAPIペイロード変換。
        Day 3 で実API仕様に合わせて完成させる。
        """
        # TODO: 実API仕様に合わせる
        return {
            "transaction_date": journal.get("transaction_date"),
            "amount": journal.get("amount"),
            "debit_account": journal.get("debit"),
            "credit_account": journal.get("credit"),
            "memo": journal.get("description"),
        }


# ===================================
# ファクトリ関数
# ===================================
def get_mf_client() -> MFClient:
    """
    config.yaml の mf_mode に従って適切なクライアントを返す。
    .env の MF_MODE があればそれが優先される。
    """
    mode = os.getenv("MF_MODE")
    if not mode:
        if CONFIG_PATH.exists():
            with open(CONFIG_PATH, encoding="utf-8") as f:
                config = yaml.safe_load(f) or {}
            mode = config.get("mf_mode", "mock")
        else:
            mode = "mock"

    mode = mode.lower()
    if mode == "real":
        return RealMFClient()
    return MockMFClient()


# CLI実行
if __name__ == "__main__":
    import sys

    client = get_mf_client()
    print(f"Client: {type(client).__name__}")
    print(f"Health: {json.dumps(client.health_check(), ensure_ascii=False, indent=2)}")

    if "--accounts" in sys.argv:
        accounts = client.get_accounts()
        print(f"Accounts: {json.dumps(accounts, ensure_ascii=False, indent=2)}")
