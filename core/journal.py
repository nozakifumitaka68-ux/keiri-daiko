"""
仕訳生成モジュール

OCR結果(領収書の構造化データ)から、勘定科目・税区分を推定して
マネーフォワード形式の仕訳候補を生成する。

ロジック:
1. ルールベース判定(キーワード・金額・支払方法)
2. 不明な場合は Claude API で補助判定(オプション)
"""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

# 設定ファイル読込
CONFIG_PATH = Path(__file__).parent.parent / "config.yaml"


# ===================================
# 勘定科目推定のキーワードルール
# ===================================
ACCOUNT_KEYWORDS = {
    "旅費交通費": ["タクシー", "TAXI", "JR", "新幹線", "電車", "バス", "高速", "駐車", "ETC", "Uber"],
    "接待交際費": ["居酒屋", "レストラン", "料亭", "バー", "BAR", "宴会", "懇親"],
    "会議費": ["カフェ", "Cafe", "Coffee", "コーヒー", "スターバックス", "STARBUCKS", "ドトール"],
    "事務用品費": ["文具", "ノート", "ペン", "ファイル", "コクヨ", "アスクル", "オフィス用品"],
    "消耗品費": ["Amazon", "アマゾン", "ヨドバシ", "ビックカメラ"],
    "通信費": ["NTT", "ドコモ", "Docomo", "Softbank", "ソフトバンク", "au", "光回線", "インターネット",
              "AWS", "Microsoft", "Google", "GitHub", "OpenAI", "Anthropic"],
    "水道光熱費": ["電気", "ガス", "水道", "東京電力", "東京ガス"],
    "新聞図書費": ["書店", "BOOK", "紀伊国屋", "丸善", "Amazon Kindle", "新聞"],
    "支払手数料": ["振込手数料", "事務手数料", "決済手数料"],
}

# 高額判定の閾値(固定資産候補)
HIGH_VALUE_THRESHOLD = 100000


# ===================================
# 公開関数
# ===================================

def generate_journal(ocr_result: dict[str, Any], client_id: str = "client_a") -> dict[str, Any]:
    """
    OCR結果から仕訳候補を生成する。

    Args:
        ocr_result: core.ocr.extract_receipt の戻り値
        client_id: クライアントID(設定から勘定科目マスタ取得)
    Returns:
        仕訳候補(マネフォ形式に近い構造)
    """
    if ocr_result.get("error"):
        return _empty_journal(ocr_result, error=ocr_result["error"])

    config = _load_config()
    client_config = config.get("clients", {}).get(client_id, {})

    # 1. 勘定科目を推定
    account = _estimate_account(ocr_result, client_config)

    # 2. 税区分を判定
    tax_rate = ocr_result.get("tax_rate") or 10
    tax_category = _estimate_tax_category(tax_rate, ocr_result)

    # 3. 支払方法から借方/貸方を決定
    payment_method = ocr_result.get("payment_method", "unknown")
    debit_credit = _estimate_debit_credit(payment_method, account, ocr_result.get("total_amount", 0))

    # 4. 確認必須フラグ
    needs_review = _needs_review(ocr_result, account)

    # 5. 仕訳エントリを構築
    return {
        "client_id": client_id,
        "transaction_date": ocr_result.get("date") or datetime.now().strftime("%Y-%m-%d"),
        "vendor": ocr_result.get("vendor"),
        "vendor_registration_number": ocr_result.get("vendor_registration_number"),
        "debit": debit_credit["debit"],
        "credit": debit_credit["credit"],
        "amount": ocr_result.get("total_amount", 0),
        "tax_amount": ocr_result.get("tax_amount"),
        "tax_rate": tax_rate,
        "tax_category": tax_category,
        "description": _build_description(ocr_result),
        "payment_method_hint": payment_method,  # OCRが推定した参考値(仕訳には反映しない)
        # 突合ステータス: cash_pending(初期) / card_matched(明細と紐付け済) / cash_confirmed(現金確定)
        "match_status": "cash_pending",
        "matched_card_statement_id": None,
        "needs_review": needs_review,
        "review_reasons": _review_reasons(ocr_result, account),
        "confidence": ocr_result.get("confidence", 0.0),
        "source_file": ocr_result.get("_source_file"),
        "ocr_raw": ocr_result,  # トレース用に元データ保持
    }


# ===================================
# 内部関数
# ===================================

def _load_config() -> dict[str, Any]:
    """config.yamlを読込"""
    if not CONFIG_PATH.exists():
        return {}
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _estimate_account(ocr_result: dict[str, Any], client_config: dict[str, Any]) -> str:
    """勘定科目をキーワードベースで推定"""
    text_blob = " ".join([
        str(ocr_result.get("vendor") or ""),
        " ".join(item.get("description", "") for item in ocr_result.get("items", [])),
        str(ocr_result.get("notes") or ""),
    ]).lower()

    for account, keywords in ACCOUNT_KEYWORDS.items():
        for kw in keywords:
            if kw.lower() in text_blob:
                return account

    # マッチしなければデフォルト
    return client_config.get("accounts", {}).get("default_expense", "消耗品費")


def _estimate_tax_category(tax_rate: int, ocr_result: dict[str, Any]) -> str:
    """税区分(マネフォ準拠の文字列)を判定"""
    if tax_rate == 10:
        return "課税仕入10%"
    elif tax_rate == 8:
        # 軽減税率(食品等)か旧8%か
        return "課税仕入8%(軽減)"
    elif tax_rate == 0:
        return "非課税仕入"
    else:
        return "対象外"


def _estimate_debit_credit(payment_method: str, account: str, amount: int) -> dict[str, str]:
    """
    借方/貸方を決定する。

    新仕様(MVP):
    - 領収書投入時は **デフォルトで現金払い** とする。
      借方:勘定科目 / 貸方:現金
    - 後でカード明細との突合に成功したら、別途 update_journal_match()
      経由で 貸方:現金 → 貸方:未払金 に書き換わる。

    payment_method はOCRで抽出された参考情報として保持するが、
    仕訳には反映させない(誤判定リスクを排除するため)。
    """
    return {"debit": account, "credit": "現金"}


def _needs_review(ocr_result: dict[str, Any], account: str) -> bool:
    """確認必須フラグを判定"""
    # 高額(固定資産候補)
    if (ocr_result.get("total_amount") or 0) >= HIGH_VALUE_THRESHOLD:
        return True
    # 確信度が低い
    if (ocr_result.get("confidence") or 1.0) < 0.7:
        return True
    # 支払方法不明
    if ocr_result.get("payment_method") == "unknown":
        return True
    # スタブモード
    if ocr_result.get("_stub"):
        return True
    return False


def _review_reasons(ocr_result: dict[str, Any], account: str) -> list[str]:
    """確認必須の理由を文字列リストで返す"""
    reasons = []
    if (ocr_result.get("total_amount") or 0) >= HIGH_VALUE_THRESHOLD:
        reasons.append(f"高額(¥{HIGH_VALUE_THRESHOLD:,}以上)─固定資産候補")
    if (ocr_result.get("confidence") or 1.0) < 0.7:
        reasons.append("AI読取確信度が低い")
    if ocr_result.get("payment_method") == "unknown":
        reasons.append("支払方法が判定できない")
    if ocr_result.get("_stub"):
        reasons.append("スタブモードで生成された(実OCR未実行)")
    if not ocr_result.get("vendor_registration_number"):
        reasons.append("適格請求書発行事業者番号なし(免税事業者の可能性)")
    return reasons


def _build_description(ocr_result: dict[str, Any]) -> str:
    """摘要文字列を生成"""
    items = ocr_result.get("items", [])
    if items:
        first_item = items[0].get("description", "")
        if len(items) > 1:
            return f"{first_item} 他{len(items) - 1}件"
        return first_item
    return ocr_result.get("vendor") or "領収書"


def _empty_journal(ocr_result: dict[str, Any], error: str) -> dict[str, Any]:
    """エラー時の空仕訳"""
    return {
        "error": error,
        "ocr_raw": ocr_result,
        "needs_review": True,
        "review_reasons": [f"OCR失敗: {error}"],
    }


# ===================================
# 取り崩し仕訳(銀行引落でカード払い決済)
# ===================================

def create_settlement_entry(
    client_id: str,
    amount: int,
    transaction_date: str | None,
    card_name: str | None,
    bank_account: str | None,
    bank_statement_id: str,
    covered_card_statement_ids: list[str],
) -> dict[str, Any]:
    """
    銀行口座からのカード引落に対応する取り崩し仕訳を生成する。

    仕訳: 借方:未払金 / 貸方:普通預金

    既存の card_matched 状態の仕訳には触れない。新規仕訳として追加する。
    """
    return {
        "client_id": client_id,
        "transaction_date": transaction_date or datetime.now().strftime("%Y-%m-%d"),
        "vendor": f"カード会社引落: {card_name or '未指定'}",
        "vendor_registration_number": None,
        "debit": "未払金",
        "credit": "普通預金",
        "amount": amount,
        "tax_amount": None,
        "tax_rate": None,
        "tax_category": "対象外",
        "description": (
            f"{card_name or 'カード'}引落({len(covered_card_statement_ids)}件分): "
            f"¥{amount:,}"
        ),
        "payment_method_hint": "bank",
        "match_status": "settlement",  # 通常の領収書とは別カテゴリ
        "matched_card_statement_id": None,
        "settlement_info": {
            "bank_statement_id": bank_statement_id,
            "covered_card_statement_ids": covered_card_statement_ids,
            "card_name": card_name,
            "bank_account": bank_account,
        },
        "needs_review": False,
        "review_reasons": [],
        "confidence": 1.0,
        "source_file": None,
        "ocr_raw": None,
    }


# CLI実行
if __name__ == "__main__":
    import json
    import sys
    from .ocr import extract_receipt

    if len(sys.argv) < 2:
        print("使い方: python -m core.journal <領収書ファイルのパス>")
        sys.exit(1)

    ocr = extract_receipt(sys.argv[1])
    journal = generate_journal(ocr)
    print(json.dumps(journal, ensure_ascii=False, indent=2, default=str))
