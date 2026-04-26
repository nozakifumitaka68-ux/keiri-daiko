"""
Supabase ストレージバックエンド(requests ベース・SDK非依存)

Streamlit Cloud の揮発ストレージ問題を解決するため、
仕訳・カード明細・銀行明細・領収書画像を Supabase に永続化する。

設計:
- supabase-py SDK を使わず、PostgREST と Storage REST API を直接呼ぶ
- → Python 3.14 でも問題なく動作(依存はrequestsだけ)

使用方法:
- 環境変数 SUPABASE_URL / SUPABASE_KEY を設定すると自動的にこのバックエンドが使用される
- 設定がなければ core/storage.py が JSON フォールバックする
"""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import quote

import requests

from .jst import now_iso

# テーブル名・バケット名
JOURNALS_TABLE = "journals"
CARD_TABLE = "card_statements"
BANK_TABLE = "bank_statements"
RECEIPTS_BUCKET = "receipts"

# Supabase API path
REST_PATH = "/rest/v1"
STORAGE_PATH = "/storage/v1"


# ===================================
# 初期化
# ===================================

def is_configured() -> bool:
    """Supabase が設定されているか判定"""
    return bool(os.getenv("SUPABASE_URL") and os.getenv("SUPABASE_KEY"))


def _base_url() -> str:
    return (os.getenv("SUPABASE_URL") or "").rstrip("/")


def _api_key() -> str:
    return os.getenv("SUPABASE_KEY") or ""


def _headers(prefer: str | None = None, content_type: str = "application/json") -> dict[str, str]:
    """REST API共通ヘッダ"""
    h = {
        "apikey": _api_key(),
        "Authorization": f"Bearer {_api_key()}",
        "Content-Type": content_type,
    }
    if prefer:
        h["Prefer"] = prefer
    return h


def health_check() -> dict[str, Any]:
    """接続確認"""
    if not is_configured():
        return {"status": "not_configured", "message": "SUPABASE_URL / SUPABASE_KEY 未設定"}
    try:
        url = f"{_base_url()}{REST_PATH}/{JOURNALS_TABLE}?select=id&limit=1"
        r = requests.get(url, headers=_headers(), timeout=10)
        if r.status_code == 200:
            return {"status": "ok", "message": "接続成功"}
        return {
            "status": "error",
            "code": r.status_code,
            "message": r.text[:200],
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}


# ===================================
# REST 共通操作
# ===================================

def _get(table: str, params: dict[str, str]) -> list[dict[str, Any]]:
    url = f"{_base_url()}{REST_PATH}/{table}"
    r = requests.get(url, headers=_headers(), params=params, timeout=30)
    r.raise_for_status()
    return r.json() if r.text else []


def _insert(table: str, payload: dict[str, Any] | list[dict[str, Any]]) -> list[dict[str, Any]]:
    url = f"{_base_url()}{REST_PATH}/{table}"
    r = requests.post(
        url,
        headers=_headers(prefer="return=representation"),
        data=json.dumps(payload, ensure_ascii=False),
        timeout=60,
    )
    r.raise_for_status()
    if not r.text:
        return []
    data = r.json()
    return data if isinstance(data, list) else [data]


def _update(table: str, eq_field: str, eq_value: str, payload: dict[str, Any]) -> list[dict[str, Any]]:
    url = f"{_base_url()}{REST_PATH}/{table}"
    r = requests.patch(
        url,
        headers=_headers(prefer="return=representation"),
        params={eq_field: f"eq.{eq_value}"},
        data=json.dumps(payload, ensure_ascii=False),
        timeout=30,
    )
    r.raise_for_status()
    if not r.text:
        return []
    data = r.json()
    return data if isinstance(data, list) else [data]


# ===================================
# 仕訳(journals)CRUD
# ===================================

def save_journal(entry: dict[str, Any]) -> dict[str, Any]:
    payload = _prepare_journal(entry)
    payload.setdefault("id", str(uuid.uuid4()))
    payload.setdefault("created_at", now_iso())
    rows = _insert(JOURNALS_TABLE, payload)
    return rows[0] if rows else payload


def update_journal(entry_id: str, updates: dict[str, Any]) -> dict[str, Any] | None:
    payload = _prepare_journal(updates)
    payload["updated_at"] = now_iso()
    rows = _update(JOURNALS_TABLE, "id", entry_id, payload)
    return rows[0] if rows else None


def delete_journal_soft(entry_id: str, reason: str = "") -> dict[str, Any] | None:
    return update_journal(entry_id, {
        "is_deleted": True,
        "deleted_at": now_iso(),
        "delete_reason": reason,
    })


def restore_journal(entry_id: str) -> dict[str, Any] | None:
    return update_journal(entry_id, {
        "is_deleted": False,
        "deleted_at": None,
        "delete_reason": None,
        "restored_at": now_iso(),
    })


def list_journals(client_id: str | None = None, include_deleted: bool = False) -> list[dict[str, Any]]:
    params: dict[str, str] = {"select": "*", "order": "created_at.asc"}
    if client_id:
        params["client_id"] = f"eq.{client_id}"
    if not include_deleted:
        params["is_deleted"] = "eq.false"
    return _get(JOURNALS_TABLE, params)


def list_deleted_journals(client_id: str | None = None) -> list[dict[str, Any]]:
    params: dict[str, str] = {
        "select": "*",
        "order": "deleted_at.desc",
        "is_deleted": "eq.true",
    }
    if client_id:
        params["client_id"] = f"eq.{client_id}"
    return _get(JOURNALS_TABLE, params)


# ===================================
# カード明細
# ===================================

def save_card(entry: dict[str, Any]) -> dict[str, Any]:
    payload = _prepare_card(entry)
    payload.setdefault("id", str(uuid.uuid4()))
    payload.setdefault("imported_at", now_iso())
    rows = _insert(CARD_TABLE, payload)
    return rows[0] if rows else payload


def save_cards_bulk(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not entries:
        return []
    payloads = []
    for e in entries:
        p = _prepare_card(e)
        p.setdefault("id", str(uuid.uuid4()))
        p.setdefault("imported_at", now_iso())
        payloads.append(p)
    rows = _insert(CARD_TABLE, payloads)
    return rows if rows else payloads


def update_card(card_id: str, updates: dict[str, Any]) -> dict[str, Any] | None:
    payload = _prepare_card(updates)
    payload["updated_at"] = now_iso()
    rows = _update(CARD_TABLE, "id", card_id, payload)
    return rows[0] if rows else None


def delete_card_soft(card_id: str, reason: str = "") -> dict[str, Any] | None:
    return update_card(card_id, {
        "is_deleted": True,
        "deleted_at": now_iso(),
        "delete_reason": reason,
    })


def restore_card(card_id: str) -> dict[str, Any] | None:
    return update_card(card_id, {
        "is_deleted": False,
        "deleted_at": None,
        "delete_reason": None,
        "restored_at": now_iso(),
    })


def list_cards(client_id: str | None = None, include_deleted: bool = False) -> list[dict[str, Any]]:
    params: dict[str, str] = {"select": "*", "order": "imported_at.asc"}
    if client_id:
        params["client_id"] = f"eq.{client_id}"
    if not include_deleted:
        params["is_deleted"] = "eq.false"
    return _get(CARD_TABLE, params)


def list_deleted_cards(client_id: str | None = None) -> list[dict[str, Any]]:
    params: dict[str, str] = {
        "select": "*",
        "order": "deleted_at.desc",
        "is_deleted": "eq.true",
    }
    if client_id:
        params["client_id"] = f"eq.{client_id}"
    return _get(CARD_TABLE, params)


# ===================================
# 銀行明細
# ===================================

def save_bank(entry: dict[str, Any]) -> dict[str, Any]:
    payload = _prepare_bank(entry)
    payload.setdefault("id", str(uuid.uuid4()))
    payload.setdefault("imported_at", now_iso())
    rows = _insert(BANK_TABLE, payload)
    return rows[0] if rows else payload


def save_banks_bulk(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not entries:
        return []
    payloads = []
    for e in entries:
        p = _prepare_bank(e)
        p.setdefault("id", str(uuid.uuid4()))
        p.setdefault("imported_at", now_iso())
        payloads.append(p)
    rows = _insert(BANK_TABLE, payloads)
    return rows if rows else payloads


def update_bank(bank_id: str, updates: dict[str, Any]) -> dict[str, Any] | None:
    payload = _prepare_bank(updates)
    payload["updated_at"] = now_iso()
    rows = _update(BANK_TABLE, "id", bank_id, payload)
    return rows[0] if rows else None


def delete_bank_soft(bank_id: str, reason: str = "") -> dict[str, Any] | None:
    return update_bank(bank_id, {
        "is_deleted": True,
        "deleted_at": now_iso(),
        "delete_reason": reason,
    })


def restore_bank(bank_id: str) -> dict[str, Any] | None:
    return update_bank(bank_id, {
        "is_deleted": False,
        "deleted_at": None,
        "delete_reason": None,
        "restored_at": now_iso(),
    })


def list_banks(client_id: str | None = None, include_deleted: bool = False) -> list[dict[str, Any]]:
    params: dict[str, str] = {"select": "*", "order": "imported_at.asc"}
    if client_id:
        params["client_id"] = f"eq.{client_id}"
    if not include_deleted:
        params["is_deleted"] = "eq.false"
    return _get(BANK_TABLE, params)


def list_deleted_banks(client_id: str | None = None) -> list[dict[str, Any]]:
    params: dict[str, str] = {
        "select": "*",
        "order": "deleted_at.desc",
        "is_deleted": "eq.true",
    }
    if client_id:
        params["client_id"] = f"eq.{client_id}"
    return _get(BANK_TABLE, params)


# ===================================
# 領収書画像(Supabase Storage REST)
# ===================================

def upload_receipt(
    file_bytes: bytes,
    client_id: str,
    file_hash: str,
    original_filename: str,
) -> str:
    """Supabase Storage に画像アップロード(POST /storage/v1/object/<bucket>/<path>)"""
    ext = Path(original_filename).suffix.lower() or ".bin"
    object_path = f"{client_id}/{file_hash}{ext}"
    url = f"{_base_url()}{STORAGE_PATH}/object/{RECEIPTS_BUCKET}/{quote(object_path)}"

    headers = {
        "apikey": _api_key(),
        "Authorization": f"Bearer {_api_key()}",
        "Content-Type": _guess_mime(ext),
        "x-upsert": "true",  # 既存ファイルは上書き
    }
    r = requests.post(url, headers=headers, data=file_bytes, timeout=60)
    if r.status_code in (200, 201):
        return object_path
    # 既存と同一(409)も成功扱い
    if r.status_code == 409:
        return object_path
    r.raise_for_status()
    return object_path


def download_receipt(object_path: str) -> bytes | None:
    if not object_path:
        return None
    url = f"{_base_url()}{STORAGE_PATH}/object/{RECEIPTS_BUCKET}/{quote(object_path)}"
    headers = {
        "apikey": _api_key(),
        "Authorization": f"Bearer {_api_key()}",
    }
    r = requests.get(url, headers=headers, timeout=60)
    if r.status_code == 200:
        return r.content
    return None


def get_receipt_signed_url(object_path: str, expires_sec: int = 3600) -> str | None:
    """期限付き署名URL生成"""
    if not object_path:
        return None
    url = f"{_base_url()}{STORAGE_PATH}/object/sign/{RECEIPTS_BUCKET}/{quote(object_path)}"
    r = requests.post(
        url,
        headers=_headers(),
        data=json.dumps({"expiresIn": expires_sec}),
        timeout=10,
    )
    if r.status_code != 200:
        return None
    data = r.json()
    signed = data.get("signedURL") or data.get("signed_url")
    if signed and signed.startswith("/"):
        signed = f"{_base_url()}{STORAGE_PATH}{signed}" if "/storage/" not in signed else f"{_base_url()}{signed}"
    return signed


# ===================================
# 内部: フィールド整形(DBで許可されているカラム)
# ===================================

_JOURNAL_COLS = {
    "id", "client_id", "transaction_date", "vendor", "vendor_registration_number",
    "debit", "credit", "amount", "tax_amount", "tax_rate", "tax_category",
    "description", "payment_method_hint", "people_count", "per_person_amount",
    "match_status", "matched_card_statement_id", "needs_review", "review_reasons",
    "confidence", "source_file", "file_hash", "receipt_path", "receipt_filename",
    "ocr_raw", "settlement_info",
    "is_deleted", "deleted_at", "delete_reason", "restored_at",
    "mf_registration", "mf_mode",
    "created_at", "updated_at", "registered_at",
}

_CARD_COLS = {
    "id", "client_id", "card_name", "usage_date", "posting_date",
    "vendor_raw", "amount", "memo", "raw_row",
    "match_status", "matched_journal_id", "settlement_status",
    "is_deleted", "deleted_at", "delete_reason", "restored_at",
    "imported_at", "updated_at",
}

_BANK_COLS = {
    "id", "client_id", "account_name", "transaction_date", "description",
    "amount", "balance", "raw_row",
    "match_status", "matched_card_statement_ids", "settlement_journal_id",
    "is_deleted", "deleted_at", "delete_reason", "restored_at",
    "imported_at", "updated_at",
}


def _prepare_journal(entry: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in entry.items() if k in _JOURNAL_COLS}


def _prepare_card(entry: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in entry.items() if k in _CARD_COLS}


def _prepare_bank(entry: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in entry.items() if k in _BANK_COLS}


def _guess_mime(ext: str) -> str:
    return {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
        ".gif": "image/gif",
        ".pdf": "application/pdf",
    }.get(ext.lower(), "application/octet-stream")


# CLI
if __name__ == "__main__":
    print(json.dumps(health_check(), ensure_ascii=False, indent=2))
