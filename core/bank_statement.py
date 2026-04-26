"""
銀行明細インポートモジュール

銀行(普通預金口座)の入出金明細CSVを取り込む。
最低限必要な列: 取引日 / 摘要 / 出金額 or 入金額 (or 金額)

出金は内部表現で amount < 0 として保存。
"""

from __future__ import annotations

import csv
import io
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from .card_statement import _decode_csv_bytes, _normalize_amount, _normalize_date
from .storage import save_bank_statements_bulk

# ===================================
# 列名マッピング
# ===================================
COLUMN_ALIASES = {
    "transaction_date": [
        "取引日", "日付", "ご利用日", "計上日", "Date", "date",
    ],
    "description": [
        "摘要", "お取引内容", "内容", "明細", "Description", "Memo", "備考",
    ],
    "withdrawal": [
        "出金金額", "お支払金額", "出金", "支払金額", "Withdrawal", "Debit",
    ],
    "deposit": [
        "入金金額", "お預かり金額", "入金", "預入金額", "Deposit", "Credit",
    ],
    "amount": [
        "金額", "Amount", "amount", "取引金額",
    ],
    "balance": [
        "残高", "差引残高", "Balance",
    ],
    "account_name": [
        "口座名義", "口座", "Account", "支店", "本支店",
    ],
}


def import_csv(
    csv_content: str | bytes,
    client_id: str = "client_a",
    account_name: str | None = None,
    skip_duplicates: bool = True,
) -> dict[str, Any]:
    """銀行明細CSVを取り込み保存

    Returns:
        {"saved": [...], "skipped": [...], "saved_count": int, "skipped_count": int}
    """
    from .duplicate import filter_new_bank_statements

    if isinstance(csv_content, bytes):
        csv_content = _decode_csv_bytes(csv_content)

    rows = _parse_csv(csv_content)
    if not rows:
        return {"saved": [], "skipped": [], "saved_count": 0, "skipped_count": 0}

    statements = []
    for row in rows:
        statement = _row_to_statement(row, client_id, account_name)
        if statement:
            statements.append(statement)

    if skip_duplicates:
        new_stmts, dup_stmts = filter_new_bank_statements(client_id, statements)
    else:
        new_stmts, dup_stmts = statements, []

    saved = save_bank_statements_bulk(new_stmts) if new_stmts else []

    return {
        "saved": saved,
        "skipped": dup_stmts,
        "saved_count": len(saved),
        "skipped_count": len(dup_stmts),
    }


def import_csv_file(
    file_path: str | Path,
    client_id: str = "client_a",
    account_name: str | None = None,
    skip_duplicates: bool = True,
) -> dict[str, Any]:
    """ファイルから取込"""
    path = Path(file_path)
    with open(path, "rb") as f:
        return import_csv(
            f.read(),
            client_id=client_id,
            account_name=account_name,
            skip_duplicates=skip_duplicates,
        )


# ===================================
# 内部
# ===================================

def _parse_csv(content: str) -> list[dict[str, str]]:
    reader = csv.DictReader(io.StringIO(content))
    return [dict(row) for row in reader]


def _lookup_column(row: dict[str, str], key: str) -> str:
    aliases = COLUMN_ALIASES.get(key, [key])
    for alias in aliases:
        if alias in row and row[alias]:
            return row[alias]
        for k, v in row.items():
            if k.lower() == alias.lower() and v:
                return v
    return ""


def _row_to_statement(
    row: dict[str, str],
    client_id: str,
    account_name: str | None,
) -> dict[str, Any] | None:
    """1行を内部明細に変換。出金は amount<0 とする"""
    transaction_date = _normalize_date(_lookup_column(row, "transaction_date"))
    if not transaction_date:
        return None

    description = _lookup_column(row, "description")
    if not description:
        return None

    # 出金/入金のいずれかを使う(両列あるパターンが多い)
    withdrawal = _normalize_amount(_lookup_column(row, "withdrawal"))
    deposit = _normalize_amount(_lookup_column(row, "deposit"))
    amount_col = _normalize_amount(_lookup_column(row, "amount"))

    if withdrawal:
        amount = -abs(withdrawal)  # 出金は負
    elif deposit:
        amount = abs(deposit)  # 入金は正
    elif amount_col is not None:
        # 単一列なら符号そのまま使う
        amount = amount_col
    else:
        return None

    if amount == 0:
        return None

    balance_str = _lookup_column(row, "balance")
    balance = _normalize_amount(balance_str)

    return {
        "client_id": client_id,
        "account_name": account_name or _lookup_column(row, "account_name") or "未指定",
        "transaction_date": transaction_date,
        "description": description.strip(),
        "amount": amount,
        "balance": balance,
        "raw_row": row,
    }


# CLI
if __name__ == "__main__":
    import json
    import sys

    if len(sys.argv) < 2:
        print("使い方: python -m core.bank_statement <CSVファイル> [クライアントID]")
        sys.exit(1)

    target = sys.argv[1]
    client = sys.argv[2] if len(sys.argv) > 2 else "client_a"
    result = import_csv_file(target, client_id=client)
    print(f"取込完了: 新規 {result['saved_count']}件 / 重複スキップ {result['skipped_count']}件")
    print(json.dumps(result["saved"][:3], ensure_ascii=False, indent=2))
