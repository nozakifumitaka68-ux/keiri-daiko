"""
カード利用明細モジュール

CSV からカード利用明細を取り込み、内部データモデルに変換して保存する。

サポートCSVフォーマット(柔軟):
- マネーフォワード書出し形式
- 三井住友カード明細CSV
- 楽天カード明細CSV
- 汎用フォーマット(列名ベースで自動マッピング)

最低限必要な列: 利用日 / 利用先 / 金額
任意の列: 計上日 / カード名義 / 備考
"""

from __future__ import annotations

import csv
import io
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from .storage import save_card_statements_bulk

# ===================================
# 列名マッピング(代表的な命名揺れに対応)
# ===================================
COLUMN_ALIASES = {
    "usage_date": [
        "利用日", "ご利用日", "使用日", "取引日", "Date", "Usage Date",
        "Transaction Date", "transaction_date",
    ],
    "posting_date": [
        "計上日", "ご請求日", "請求日", "Posting Date", "posting_date",
    ],
    "vendor": [
        "利用先", "ご利用店舗", "ご利用先", "店舗名", "加盟店名", "摘要",
        "Description", "Merchant", "vendor",
    ],
    "amount": [
        "金額", "利用金額", "ご利用金額", "請求金額", "Amount", "amount",
    ],
    "card_name": [
        "カード名", "カード名称", "ご利用カード", "Card", "card_name",
    ],
    "memo": [
        "備考", "メモ", "Memo", "Notes", "notes",
    ],
}


# ===================================
# 公開関数
# ===================================

def import_csv(
    csv_content: str | bytes,
    client_id: str = "client_a",
    card_name: str | None = None,
) -> list[dict[str, Any]]:
    """
    CSV内容からカード明細を取り込み、保存する。

    Args:
        csv_content: CSVのテキスト内容(または bytes)
        client_id: クライアントID
        card_name: カード名(指定がない場合はCSVの列から取得 or "未指定")
    Returns:
        保存された明細リスト
    """
    if isinstance(csv_content, bytes):
        csv_content = _decode_csv_bytes(csv_content)

    rows = _parse_csv(csv_content)
    if not rows:
        return []

    statements = []
    for row in rows:
        statement = _row_to_statement(row, client_id, card_name)
        if statement:
            statements.append(statement)

    return save_card_statements_bulk(statements)


def import_csv_file(
    file_path: str | Path,
    client_id: str = "client_a",
    card_name: str | None = None,
) -> list[dict[str, Any]]:
    """ファイルパスから取り込み"""
    path = Path(file_path)
    with open(path, "rb") as f:
        return import_csv(f.read(), client_id=client_id, card_name=card_name)


# ===================================
# 内部関数
# ===================================

def _decode_csv_bytes(data: bytes) -> str:
    """日本のカード明細CSVは Shift_JIS が多いので段階的にデコード"""
    for encoding in ("utf-8-sig", "utf-8", "cp932", "shift_jis"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    # 最終フォールバック(エラー文字は置換)
    return data.decode("utf-8", errors="replace")


def _parse_csv(content: str) -> list[dict[str, str]]:
    """CSVを辞書リストに変換"""
    reader = csv.DictReader(io.StringIO(content))
    return [dict(row) for row in reader]


def _row_to_statement(
    row: dict[str, str],
    client_id: str,
    card_name: str | None,
) -> dict[str, Any] | None:
    """1行を内部明細フォーマットに変換"""
    usage_date = _normalize_date(_lookup_column(row, "usage_date"))
    if not usage_date:
        return None

    vendor = _lookup_column(row, "vendor")
    if not vendor:
        return None

    amount = _normalize_amount(_lookup_column(row, "amount"))
    if amount is None or amount == 0:
        return None

    posting_date = _normalize_date(_lookup_column(row, "posting_date")) or usage_date

    return {
        "client_id": client_id,
        "card_name": card_name or _lookup_column(row, "card_name") or "未指定",
        "usage_date": usage_date,
        "posting_date": posting_date,
        "vendor_raw": vendor.strip(),
        "amount": amount,
        "memo": _lookup_column(row, "memo") or "",
        "raw_row": row,  # トレース用
    }


def _lookup_column(row: dict[str, str], key: str) -> str:
    """列名揺れを吸収して値を取得"""
    aliases = COLUMN_ALIASES.get(key, [key])
    for alias in aliases:
        if alias in row and row[alias]:
            return row[alias]
        # 大文字小文字無視マッチ
        for k, v in row.items():
            if k.lower() == alias.lower() and v:
                return v
    return ""


def _normalize_date(value: str) -> str:
    """日付文字列を YYYY-MM-DD に正規化"""
    if not value:
        return ""
    value = value.strip()

    # 数字だけ抽出して構造を判定
    digits = re.findall(r"\d+", value)
    if len(digits) >= 3:
        try:
            year = int(digits[0])
            month = int(digits[1])
            day = int(digits[2])
            # 年が2桁なら西暦補正
            if year < 100:
                year += 2000
            # 妥当性チェック
            if 1900 <= year <= 2100 and 1 <= month <= 12 and 1 <= day <= 31:
                return f"{year:04d}-{month:02d}-{day:02d}"
        except ValueError:
            pass

    # ISO 形式直接パース
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d", "%Y年%m月%d日"):
        try:
            dt = datetime.strptime(value, fmt)
            return dt.strftime("%Y-%m-%d")
        except ValueError:
            continue
    return ""


def _normalize_amount(value: str) -> int | None:
    """金額文字列を整数(円)に正規化"""
    if not value:
        return None
    # カンマ・円記号・スペース除去
    cleaned = re.sub(r"[,¥円\s]", "", value.strip())
    # 負号は除去(返金・取消は今回扱わない、絶対値のみ)
    cleaned = cleaned.lstrip("-")
    if not cleaned:
        return None
    try:
        return int(float(cleaned))
    except ValueError:
        return None


# CLI
if __name__ == "__main__":
    import json
    import sys

    if len(sys.argv) < 2:
        print("使い方: python -m core.card_statement <CSVファイル> [クライアントID]")
        sys.exit(1)

    target = sys.argv[1]
    client = sys.argv[2] if len(sys.argv) > 2 else "client_a"
    saved = import_csv_file(target, client_id=client)
    print(f"取り込み完了: {len(saved)}件")
    print(json.dumps(saved[:3], ensure_ascii=False, indent=2))
