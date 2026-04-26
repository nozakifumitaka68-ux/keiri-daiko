"""
領収書 ↔ カード利用明細 突合エンジン

シンプル設計(MVP):
- 日付完全一致(±0日、設定で許容範囲拡張可)
- 金額完全一致(±0円、設定で許容範囲拡張可)
- 支払先 文字列類似度 70% 以上(設定で変更可)

突合成功時:
- 仕訳の credit を「現金」→「未払金」に変更
- match_status を "card_matched" に変更
- カード明細の match_status も "matched" に
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

import yaml

from .storage import (
    find_card_statements_by_client,
    find_pending_receipts,
    find_unmatched_bank_payments,
    find_unsettled_card_statements,
    save_entry,
    update_bank_statement,
    update_card_statement,
    update_journal_match,
)

CONFIG_PATH = Path(__file__).parent.parent / "config.yaml"

# デフォルト設定(config.yaml が無い時のフォールバック)
DEFAULT_CONFIG = {
    "date_tolerance_days": 0,
    "amount_tolerance_yen": 0,
    "vendor_similarity_threshold": 0.7,
    "min_score_gap": 0.1,
}


# ===================================
# 公開関数
# ===================================

def run_matching(client_id: str = "client_a", dry_run: bool = False) -> dict[str, Any]:
    """
    指定クライアントの領収書とカード明細を突合する。

    Args:
        client_id: クライアントID
        dry_run: True なら DB 更新せず結果だけ返す
    Returns:
        {
          "matched": [{journal_id, statement_id, score, ...}],
          "unmatched_receipts": [...],
          "unmatched_statements": [...],
        }
    """
    config = _load_matching_config()

    receipts = find_pending_receipts(client_id)
    all_statements = find_card_statements_by_client(client_id)
    unmatched_statements = [s for s in all_statements if s.get("match_status") == "unmatched"]

    matched = []
    used_statement_ids: set[str] = set()

    for receipt in receipts:
        candidates = _find_candidates(receipt, unmatched_statements, used_statement_ids, config)
        best = _pick_best(candidates, config)
        if best:
            matched.append({
                "journal_id": receipt["id"],
                "journal_vendor": receipt.get("vendor"),
                "journal_amount": receipt.get("amount"),
                "journal_date": receipt.get("transaction_date"),
                "statement_id": best["statement"]["id"],
                "statement_vendor": best["statement"].get("vendor_raw"),
                "statement_amount": best["statement"].get("amount"),
                "statement_date": best["statement"].get("usage_date"),
                "score": best["score"],
                "vendor_similarity": best["vendor_similarity"],
            })
            used_statement_ids.add(best["statement"]["id"])

            if not dry_run:
                update_journal_match(
                    journal_id=receipt["id"],
                    card_statement_id=best["statement"]["id"],
                    new_credit="未払金",
                )
                update_card_statement(
                    best["statement"]["id"],
                    {"match_status": "matched", "matched_journal_id": receipt["id"]},
                )

    unmatched_receipts = [
        {
            "id": r["id"],
            "vendor": r.get("vendor"),
            "amount": r.get("amount"),
            "date": r.get("transaction_date"),
        }
        for r in receipts
        if not any(m["journal_id"] == r["id"] for m in matched)
    ]
    unmatched_remaining = [
        {
            "id": s["id"],
            "vendor_raw": s.get("vendor_raw"),
            "amount": s.get("amount"),
            "usage_date": s.get("usage_date"),
        }
        for s in unmatched_statements
        if s["id"] not in used_statement_ids
    ]

    return {
        "matched": matched,
        "matched_count": len(matched),
        "unmatched_receipts": unmatched_receipts,
        "unmatched_statements": unmatched_remaining,
        "dry_run": dry_run,
        "config": config,
    }


# ===================================
# 内部関数
# ===================================

def _load_matching_config() -> dict[str, Any]:
    """config.yamlから突合設定を読込"""
    if not CONFIG_PATH.exists():
        return DEFAULT_CONFIG.copy()
    with open(CONFIG_PATH, encoding="utf-8") as f:
        config = yaml.safe_load(f) or {}
    matching = config.get("matching", {})
    return {**DEFAULT_CONFIG, **matching}


def _find_candidates(
    receipt: dict[str, Any],
    statements: list[dict[str, Any]],
    excluded_ids: set[str],
    config: dict[str, Any],
) -> list[dict[str, Any]]:
    """1領収書に対する候補明細を抽出"""
    candidates = []
    receipt_date = receipt.get("transaction_date")
    receipt_amount = receipt.get("amount")
    receipt_vendor = receipt.get("vendor") or ""

    for statement in statements:
        if statement["id"] in excluded_ids:
            continue

        # 日付チェック
        if not _date_matches(
            receipt_date,
            statement.get("usage_date"),
            statement.get("posting_date"),
            config["date_tolerance_days"],
        ):
            continue

        # 金額チェック
        if not _amount_matches(
            receipt_amount,
            statement.get("amount"),
            config["amount_tolerance_yen"],
        ):
            continue

        # 支払先類似度
        sim = _vendor_similarity(receipt_vendor, statement.get("vendor_raw", ""))
        if sim < config["vendor_similarity_threshold"]:
            continue

        candidates.append({
            "statement": statement,
            "vendor_similarity": sim,
            "score": sim,  # 現状は類似度をそのままスコアに
        })

    return candidates


def _pick_best(
    candidates: list[dict[str, Any]],
    config: dict[str, Any],
) -> dict[str, Any] | None:
    """
    候補の中から最高スコアを選ぶ。
    複数候補がある場合は、2位とのスコア差が min_score_gap 以上あれば採用。
    曖昧な場合は誤マッチ防止のため None を返す。
    """
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]

    sorted_c = sorted(candidates, key=lambda x: x["score"], reverse=True)
    best, second = sorted_c[0], sorted_c[1]
    if best["score"] - second["score"] >= config["min_score_gap"]:
        return best
    # 僅差なら誤マッチ防止のため見送り
    return None


def _date_matches(
    receipt_date: str | None,
    usage_date: str | None,
    posting_date: str | None,
    tolerance_days: int,
) -> bool:
    """日付がしきい値以内に一致するか(利用日優先、計上日も補助確認)"""
    if not receipt_date:
        return False
    if usage_date and _date_diff(receipt_date, usage_date) <= tolerance_days:
        return True
    # 利用日でマッチしなければ計上日でも試す(将来拡張用)
    if posting_date and _date_diff(receipt_date, posting_date) <= tolerance_days:
        return True
    return False


def _date_diff(d1: str, d2: str) -> int:
    """日付文字列(YYYY-MM-DD)の差分日数(絶対値)"""
    from datetime import date

    try:
        a = date.fromisoformat(d1)
        b = date.fromisoformat(d2)
        return abs((a - b).days)
    except (ValueError, TypeError):
        return 999  # パース失敗時は不一致扱い


def _amount_matches(amount1: int | None, amount2: int | None, tolerance: int) -> bool:
    """金額がしきい値以内に一致するか"""
    if amount1 is None or amount2 is None:
        return False
    return abs(amount1 - amount2) <= tolerance


def _vendor_similarity(name1: str, name2: str) -> float:
    """
    支払先の文字列類似度。
    前処理: 大文字化・記号除去・カナ統一など、表記揺れに強くする。
    """
    n1 = _normalize_vendor(name1)
    n2 = _normalize_vendor(name2)
    if not n1 or not n2:
        return 0.0
    # SequenceMatcherで比較。部分一致も加味する
    base = SequenceMatcher(None, n1, n2).ratio()
    # 一方が他方を含む場合はボーナス
    if n1 in n2 or n2 in n1:
        return max(base, 0.85)
    return base


def _normalize_vendor(name: str) -> str:
    """支払先名の前処理(類似度比較しやすくする)"""
    if not name:
        return ""
    # 大文字化
    s = name.upper()
    # 全角英数→半角(よくある表記揺れ)
    s = s.translate(str.maketrans(
        "０１２３４５６７８９ＡＢＣＤＥＦＧＨＩＪＫＬＭＮＯＰＱＲＳＴＵＶＷＸＹＺ",
        "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ",
    ))
    # 記号・空白除去
    s = re.sub(r"[\s\-_/.,()（）「」『』【】、。・*]+", "", s)
    # よくある接尾辞除去
    for suffix in ("株式会社", "(株)", "(株)", "有限会社", "(有)", "CO.,LTD", "INC", "LTD", "CORP"):
        s = s.replace(suffix, "")
    return s


# ===================================
# 銀行明細(カード引落) ↔ カード明細群の突合
# ===================================

def run_bank_matching(client_id: str = "client_a", dry_run: bool = False) -> dict[str, Any]:
    """
    銀行明細のカード会社引落と、未決済のカード明細群を突合する。

    マッチ条件:
    - 銀行の出金(amount<0)1件 = 同一カード会社の未決済カード明細合計と一致
    - 銀行摘要にカード会社キーワードが含まれる(任意)

    マッチ成功時:
    - カード明細群に settlement_status='settled' を付与
    - 取り崩し仕訳(借方:未払金 / 貸方:普通預金)を1本生成
    - 銀行明細に match_status='matched_card_payment' を付与

    Returns:
        {
          "matched": [{bank_id, card_company, statement_ids, total_amount, settlement_journal_id}],
          "unmatched_bank_payments": [...],
          "unsettled_card_statements_by_card": {...},
        }
    """
    config = _load_bank_matching_config()

    bank_payments = find_unmatched_bank_payments(client_id)
    unsettled_cards = find_unsettled_card_statements(client_id)

    # カード名(card_name)でグルーピング
    cards_by_name: dict[str, list[dict[str, Any]]] = {}
    for c in unsettled_cards:
        key = c.get("card_name", "未指定")
        cards_by_name.setdefault(key, []).append(c)

    matched_results = []
    used_card_keys: set[str] = set()

    for bank_payment in bank_payments:
        withdrawal = abs(bank_payment.get("amount") or 0)
        description = bank_payment.get("description", "")

        # カード会社キーワードを抽出
        identified_company = _identify_card_company(description, config["card_company_keywords"])

        # マッチ候補を探す: 各カード名の未決済合計と一致するか
        match_card_name = None
        match_statements = []

        for card_name, statements in cards_by_name.items():
            if card_name in used_card_keys:
                continue

            # カード会社特定済の場合、card_nameと部分一致するもののみ対象
            if identified_company and not _name_relates(card_name, identified_company):
                continue

            total = sum(s.get("amount") or 0 for s in statements)
            if abs(total - withdrawal) <= config["amount_tolerance_yen"]:
                match_card_name = card_name
                match_statements = statements
                break

        if not match_statements:
            continue

        # マッチ確定
        statement_ids = [s["id"] for s in match_statements]
        total_amount = sum(s.get("amount") or 0 for s in match_statements)

        settlement_journal_id = None
        if not dry_run:
            # 1. カード明細群を settled に
            for s in match_statements:
                update_card_statement(s["id"], {"settlement_status": "settled"})

            # 2. 取り崩し仕訳を生成
            from .journal import create_settlement_entry  # 循環import回避

            settlement_journal = create_settlement_entry(
                client_id=client_id,
                amount=total_amount,
                transaction_date=bank_payment.get("transaction_date"),
                card_name=match_card_name,
                bank_account=bank_payment.get("account_name"),
                bank_statement_id=bank_payment["id"],
                covered_card_statement_ids=statement_ids,
            )
            saved_journal = save_entry(settlement_journal)
            settlement_journal_id = saved_journal["id"]

            # 3. 銀行明細を matched に
            update_bank_statement(bank_payment["id"], {
                "match_status": "matched_card_payment",
                "matched_card_statement_ids": statement_ids,
                "settlement_journal_id": settlement_journal_id,
            })

        matched_results.append({
            "bank_id": bank_payment["id"],
            "bank_date": bank_payment.get("transaction_date"),
            "bank_description": description,
            "bank_amount": bank_payment.get("amount"),
            "card_company": identified_company,
            "card_name": match_card_name,
            "card_statement_ids": statement_ids,
            "card_statement_count": len(statement_ids),
            "total_amount": total_amount,
            "settlement_journal_id": settlement_journal_id,
        })
        used_card_keys.add(match_card_name)

    # 残った未マッチ
    matched_bank_ids = {m["bank_id"] for m in matched_results}
    unmatched_bank = [
        {
            "id": b["id"],
            "date": b.get("transaction_date"),
            "description": b.get("description"),
            "amount": b.get("amount"),
        }
        for b in bank_payments
        if b["id"] not in matched_bank_ids
    ]

    cards_summary = {
        name: {
            "count": len(statements),
            "total": sum(s.get("amount") or 0 for s in statements),
            "settled": name in used_card_keys,
        }
        for name, statements in cards_by_name.items()
    }

    return {
        "matched": matched_results,
        "matched_count": len(matched_results),
        "unmatched_bank_payments": unmatched_bank,
        "cards_summary_by_name": cards_summary,
        "dry_run": dry_run,
        "config": config,
    }


def _load_bank_matching_config() -> dict[str, Any]:
    """config.yamlから引落突合設定を読込"""
    if not CONFIG_PATH.exists():
        return {
            "amount_tolerance_yen": 0,
            "card_company_keywords": [],
        }
    with open(CONFIG_PATH, encoding="utf-8") as f:
        config = yaml.safe_load(f) or {}
    bank = config.get("bank_matching", {})
    return {
        "amount_tolerance_yen": bank.get("amount_tolerance_yen", 0),
        "card_company_keywords": bank.get("card_company_keywords", []),
    }


def _identify_card_company(description: str, keywords: list[str]) -> str | None:
    """銀行摘要からカード会社を特定"""
    if not description:
        return None
    upper = description.upper()
    for kw in keywords:
        if kw.upper() in upper:
            return kw
    return None


def _name_relates(card_name: str, company_keyword: str) -> bool:
    """card_name と特定したカード会社キーワードが関連しているか緩く判定"""
    if not card_name or not company_keyword:
        return True  # どちらかが空なら制限なし
    n = card_name.upper()
    k = company_keyword.upper()
    if k in n or n in k:
        return True
    # ASCII部分の共通
    if any(part in n for part in re.findall(r"[A-Z]+", k)):
        return True
    # 日本語含めた類似度判定(SequenceMatcher で40%以上)
    if SequenceMatcher(None, n, k).ratio() >= 0.4:
        return True
    # 連続2文字以上の共通サブ文字列
    for i in range(len(k) - 1):
        for j in range(i + 2, len(k) + 1):
            if k[i:j] in n:
                return True
    return False


# CLI
if __name__ == "__main__":
    import json
    import sys

    client = sys.argv[1] if len(sys.argv) > 1 else "client_a"
    dry = "--dry-run" in sys.argv
    bank = "--bank" in sys.argv

    if bank:
        result = run_bank_matching(client_id=client, dry_run=dry)
    else:
        result = run_matching(client_id=client, dry_run=dry)
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
