"""
仕訳CSV エクスポートモジュール

マネーフォワードクラウド会計の仕訳取込形式 CSV を生成する。
APIが繋がってない時の手動取込ルートとして使う。

参考:
- マネフォ公式 仕訳CSV取込フォーマット
- https://biz.moneyforward.com/support/account/howto/howto09/m05.html
"""

from __future__ import annotations

import csv
import io
from typing import Any

from .jst import to_jst_display


# マネフォクラウド会計の仕訳取込CSV ヘッダ
# 「フリー仕訳取込」形式に準拠
MF_CSV_HEADERS = [
    "取引No",
    "取引日",
    "借方勘定科目",
    "借方補助科目",
    "借方部門",
    "借方取引先",
    "借方税区分",
    "借方インボイス",
    "借方金額(円)",
    "借方税額",
    "貸方勘定科目",
    "貸方補助科目",
    "貸方部門",
    "貸方取引先",
    "貸方税区分",
    "貸方インボイス",
    "貸方金額(円)",
    "貸方税額",
    "摘要",
    "仕訳メモ",
    "タグ",
    "MF仕訳タイプ",
    "決算整理仕訳",
    "作成日時",
    "作成者",
    "最終更新日時",
    "最終更新者",
]


def journals_to_mf_csv(journals: list[dict[str, Any]]) -> str:
    """
    仕訳リストを マネフォ取込形式 CSV 文字列に変換する。

    Args:
        journals: 仕訳エントリのリスト
    Returns:
        UTF-8 BOM 付き CSV 文字列(Excel互換)
    """
    output = io.StringIO()
    output.write("﻿")  # BOM(Excel/マネフォで日本語崩れ防止)
    writer = csv.writer(output, quoting=csv.QUOTE_ALL)
    writer.writerow(MF_CSV_HEADERS)

    for i, j in enumerate(journals, start=1):
        if j.get("is_deleted"):
            continue
        # OCR失敗エントリは中身が空なのでスキップ
        if j.get("match_status") == "ocr_failed":
            continue

        amount = j.get("amount") or 0
        tax_amount = j.get("tax_amount") or 0
        tax_category = j.get("tax_category") or "対象外"
        debit_tax = "課税仕入" in (tax_category or "") and tax_amount or 0
        credit_tax = 0  # 通常は借方側に税額載せる

        writer.writerow([
            str(i),                                  # 取引No
            j.get("transaction_date") or "",         # 取引日(YYYY-MM-DD)
            j.get("debit") or "",                    # 借方勘定科目
            "",                                      # 借方補助科目
            "",                                      # 借方部門
            j.get("vendor") or "",                   # 借方取引先
            tax_category,                            # 借方税区分
            "対象外" if not j.get("vendor_registration_number") else "適格",  # 借方インボイス
            str(amount),                             # 借方金額
            str(debit_tax),                          # 借方税額
            j.get("credit") or "",                   # 貸方勘定科目
            "",                                      # 貸方補助科目
            "",                                      # 貸方部門
            "",                                      # 貸方取引先
            "対象外",                                # 貸方税区分
            "対象外",                                # 貸方インボイス
            str(amount),                             # 貸方金額
            str(credit_tax),                         # 貸方税額
            j.get("description") or "",              # 摘要
            f"keiri-daiko ID:{(j.get('id') or '')[:8]}",  # 仕訳メモ(トレース用)
            "",                                      # タグ
            "通常仕訳",                              # MF仕訳タイプ
            "0",                                     # 決算整理仕訳(0=通常)
            to_jst_display(j.get("created_at"), "%Y/%m/%d %H:%M:%S"),  # 作成日時
            "keiri-daiko-system",                   # 作成者
            to_jst_display(j.get("updated_at") or j.get("created_at"), "%Y/%m/%d %H:%M:%S"),  # 最終更新
            "keiri-daiko-system",                   # 最終更新者
        ])

    return output.getvalue()


def journals_to_simple_csv(journals: list[dict[str, Any]]) -> str:
    """
    シンプルなCSV(社内確認・他システム連携用)を生成。
    マネフォ取込ではなく、汎用フォーマット。
    """
    output = io.StringIO()
    output.write("﻿")
    writer = csv.writer(output, quoting=csv.QUOTE_ALL)
    writer.writerow([
        "ID", "登録日時(JST)", "取引日", "支払先", "登録番号",
        "借方", "貸方", "金額", "税率(%)", "税区分",
        "摘要", "支払区分", "要確認", "領収書ファイル",
    ])

    status_label_map = {
        "cash_pending": "現金(突合待)",
        "card_matched": "カード払",
        "cash_confirmed": "現金確定",
        "settlement": "取り崩し",
        "ocr_failed": "OCR失敗",
    }

    for j in journals:
        if j.get("is_deleted"):
            continue
        writer.writerow([
            (j.get("id") or "")[:8],
            to_jst_display(j.get("created_at")),
            j.get("transaction_date") or "",
            j.get("vendor") or "",
            j.get("vendor_registration_number") or "",
            j.get("debit") or "",
            j.get("credit") or "",
            j.get("amount") or 0,
            j.get("tax_rate") or "",
            j.get("tax_category") or "",
            j.get("description") or "",
            status_label_map.get(j.get("match_status"), j.get("match_status") or ""),
            "要確認" if j.get("needs_review") else "確認済",
            j.get("receipt_filename") or "",
        ])

    return output.getvalue()


def filter_for_export(
    journals: list[dict[str, Any]],
    date_from: str | None = None,
    date_to: str | None = None,
    statuses: list[str] | None = None,
    exclude_failed: bool = True,
) -> list[dict[str, Any]]:
    """
    エクスポート対象の仕訳をフィルタする。

    Args:
        journals: 仕訳リスト
        date_from: 取引日の開始(YYYY-MM-DD、含む)
        date_to: 取引日の終了(YYYY-MM-DD、含む)
        statuses: 含める match_status のリスト(None=全部)
        exclude_failed: True なら ocr_failed を除外
    """
    result = []
    for j in journals:
        if j.get("is_deleted"):
            continue
        if exclude_failed and j.get("match_status") == "ocr_failed":
            continue
        if statuses and j.get("match_status") not in statuses:
            continue
        d = j.get("transaction_date") or ""
        if date_from and d and d < date_from:
            continue
        if date_to and d and d > date_to:
            continue
        result.append(j)
    return result


# CLI
if __name__ == "__main__":
    import sys
    from .storage import load_history

    history = load_history()
    if "--simple" in sys.argv:
        print(journals_to_simple_csv(history))
    else:
        print(journals_to_mf_csv(history))
