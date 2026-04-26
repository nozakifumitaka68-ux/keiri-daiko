"""
重複検出モジュール

領収書・カード明細・銀行明細の重複を検出する。

検出方式:
- 領収書: SHA-256ファイルハッシュ(完全一致) + 取引データ(類似一致)
- カード明細: (利用日, 金額, 支払先, カード名) の組み合わせ
- 銀行明細: (取引日, 金額, 摘要, 口座) の組み合わせ
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from .storage import (
    find_bank_statements_by_client,
    find_by_client,
    find_card_statements_by_client,
)


# ===================================
# ファイルハッシュ
# ===================================

def calculate_file_hash(data: bytes) -> str:
    """ファイル内容の SHA-256 ハッシュを返す"""
    return hashlib.sha256(data).hexdigest()


def calculate_file_hash_from_path(file_path: str | Path) -> str:
    """ファイルパスから SHA-256 ハッシュを計算"""
    with open(file_path, "rb") as f:
        return calculate_file_hash(f.read())


# ===================================
# 領収書(仕訳)の重複検出
# ===================================

def find_duplicate_receipts(
    client_id: str,
    file_hash: str | None = None,
    transaction_date: str | None = None,
    amount: int | None = None,
    vendor: str | None = None,
) -> dict[str, Any]:
    """
    領収書の重複を検出する。

    判定:
    - exact_hash_match: ファイルハッシュ完全一致(同じ画像)
    - data_match: 取引データ一致(日付+金額+支払先部分一致)

    Returns:
        {
          "has_duplicate": bool,
          "exact_hash_match": [...],   # ハッシュ一致した既存仕訳
          "data_match": [...],          # データ一致した既存仕訳
        }
    """
    existing = find_by_client(client_id)

    exact_hash_match = []
    data_match = []

    for entry in existing:
        # ファイルハッシュ一致(削除済も対象から除外: find_by_client が既に対応)
        if file_hash and entry.get("file_hash") == file_hash:
            exact_hash_match.append(_summarize_entry(entry))
            continue  # ハッシュ一致したらデータ一致は重複してチェックしない

        # 取引データ一致(日付・金額・支払先)
        if (
            transaction_date
            and amount
            and entry.get("transaction_date") == transaction_date
            and entry.get("amount") == amount
            and _vendor_similar(vendor or "", entry.get("vendor") or "")
        ):
            data_match.append(_summarize_entry(entry))

    return {
        "has_duplicate": bool(exact_hash_match or data_match),
        "exact_hash_match": exact_hash_match,
        "data_match": data_match,
    }


def _vendor_similar(a: str, b: str) -> bool:
    """支払先名の類似判定(同一/部分一致)"""
    if not a or not b:
        return False
    a_norm = a.lower().strip()
    b_norm = b.lower().strip()
    if a_norm == b_norm:
        return True
    # 一方が他方を含む(支店名違いなど)
    if len(a_norm) >= 3 and a_norm in b_norm:
        return True
    if len(b_norm) >= 3 and b_norm in a_norm:
        return True
    return False


def _summarize_entry(entry: dict[str, Any]) -> dict[str, Any]:
    """重複表示用に要点だけ抽出"""
    return {
        "id": entry.get("id"),
        "id_short": (entry.get("id") or "")[:8],
        "transaction_date": entry.get("transaction_date"),
        "vendor": entry.get("vendor"),
        "amount": entry.get("amount"),
        "match_status": entry.get("match_status"),
        "created_at": entry.get("created_at"),
    }


# ===================================
# カード明細の重複検出
# ===================================

def find_duplicate_card_statement(
    client_id: str,
    statement: dict[str, Any],
) -> dict[str, Any] | None:
    """
    カード明細の重複を検出。

    Args:
        statement: 取込予定の明細(usage_date, amount, vendor_raw, card_name 必須)
    Returns:
        重複した既存明細(あれば) or None
    """
    existing = find_card_statements_by_client(client_id)
    for s in existing:
        if (
            s.get("usage_date") == statement.get("usage_date")
            and s.get("amount") == statement.get("amount")
            and (s.get("vendor_raw") or "").strip().lower()
            == (statement.get("vendor_raw") or "").strip().lower()
            and (s.get("card_name") or "") == (statement.get("card_name") or "")
        ):
            return s
    return None


def filter_new_card_statements(
    client_id: str,
    statements: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """
    新規取込分と重複分を分離する。

    Returns:
        (new_statements, duplicate_statements)
    """
    new_list = []
    dup_list = []
    for s in statements:
        if find_duplicate_card_statement(client_id, s):
            dup_list.append(s)
        else:
            new_list.append(s)
    return new_list, dup_list


# ===================================
# 銀行明細の重複検出
# ===================================

def find_duplicate_bank_statement(
    client_id: str,
    statement: dict[str, Any],
) -> dict[str, Any] | None:
    """銀行明細の重複を検出"""
    existing = find_bank_statements_by_client(client_id)
    for s in existing:
        if (
            s.get("transaction_date") == statement.get("transaction_date")
            and s.get("amount") == statement.get("amount")
            and (s.get("description") or "").strip().lower()
            == (statement.get("description") or "").strip().lower()
            and (s.get("account_name") or "") == (statement.get("account_name") or "")
        ):
            return s
    return None


def filter_new_bank_statements(
    client_id: str,
    statements: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """銀行明細の新規取込分と重複分を分離"""
    new_list = []
    dup_list = []
    for s in statements:
        if find_duplicate_bank_statement(client_id, s):
            dup_list.append(s)
        else:
            new_list.append(s)
    return new_list, dup_list


# CLI
if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1:
        path = sys.argv[1]
        h = calculate_file_hash_from_path(path)
        print(f"SHA-256: {h}")
