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

# 軽食系: 金額に関わらず常に「会議費」(社内ランチ・打ち合わせ用)
LIGHT_FOOD_KEYWORDS = [
    # カフェ・喫茶店
    "カフェ", "Cafe", "CAFE", "Coffee", "COFFEE", "コーヒー", "珈琲",
    "スターバックス", "STARBUCKS", "ドトール", "タリーズ", "TULLY", "上島珈琲",
    "ベローチェ", "ルノアール", "コメダ",
    # 弁当・軽食・テイクアウト
    "弁当", "べんとう", "BENTO", "ベントー",
    "ほっかほっか", "ほっかほか", "ほっともっと", "オリジン", "玉子焼", "おにぎり",
    "ランチ", "LUNCH", "lunch", "定食", "丼", "牛丼",
    "吉野家", "すき家", "松屋", "なか卯", "天屋",
    # 麺類
    "蕎麦", "そば", "うどん", "ラーメン", "らーめん", "つけ麺",
    "丸亀製麺", "はなまるうどん",
    # ファストフード
    "マクドナルド", "McDonald", "MCDONALD", "モスバーガー", "MOS", "ロッテリア",
    "ケンタッキー", "KFC", "サブウェイ", "Subway", "バーガーキング",
    # コンビニ・スーパー(食事用途)
    "セブン-イレブン", "セブンイレブン", "セブン", "ローソン", "ファミリーマート",
    "ミニストップ", "デイリーヤマザキ",
    # ベーカリー
    "パン", "ベーカリー", "BAKERY", "ドンク", "ヴィ・ド・フランス",
]

# 会食系: 金額で会議費⇄接待交際費を切替
DINING_KEYWORDS = [
    "居酒屋", "レストラン", "RESTAURANT", "Restaurant",
    "料亭", "料理", "割烹", "懐石",
    "バー", "BAR", "Bar", "ラウンジ", "クラブ", "スナック",
    "宴会", "懇親", "会食", "ディナー", "DINNER",
    "焼肉", "鮨", "寿司", "すし", "鮮魚",
    "天ぷら", "和食", "中華", "イタリアン", "フレンチ", "韓国料理",
    "ステーキ", "ホテル", "HOTEL",
]

# 1人当たり10,000円超は接待交際費(令和6年4月以降の税制改正準拠)
ENTERTAINMENT_AMOUNT_THRESHOLD = 10000

# 宿泊系キーワード(摘要に含まれていたら旅費交通費に強制振り分け)
ACCOMMODATION_DESCRIPTION_KEYWORDS = [
    "宿泊", "宿泊費", "宿泊代", "ステイ", "STAY", "Stay",
    "ROOM", "Room", "客室", "ルーム", "スイート", "ツイン", "シングル", "ダブル",
    "1泊", "2泊", "3泊", "○泊", "泊数",
]

# ホテル・旅館系キーワード(支払先名)
ACCOMMODATION_VENDOR_KEYWORDS = [
    "ホテル", "HOTEL", "Hotel",
    "旅館", "民宿", "ペンション", "ロッジ", "ヴィラ", "リゾート",
    "ニューオータニ", "帝国ホテル", "リッツカールトン", "オークラ", "プリンス",
    "ヒルトン", "Hilton", "シェラトン", "Sheraton", "マリオット", "Marriott",
    "ハイアット", "Hyatt", "インターコンチネンタル",
    "東横イン", "アパホテル", "APA", "ルートイン", "ドーミーイン",
    "コンフォート", "ビジネスホテル", "カプセルホテル",
]

# 飲食示唆キーワード(ホテルでも、摘要にこれがあれば飲食扱い)
DINING_DESCRIPTION_KEYWORDS = [
    "飲食", "ディナー", "DINNER", "Dinner",
    "ランチ", "LUNCH", "Lunch", "朝食", "BREAKFAST", "Breakfast",
    "コース", "コース料理", "懐石", "弁当", "オードブル",
    "食事", "料理", "サービス料",
    "ドリンク", "ワイン", "シャンパン", "ビール",
]

# その他の科目別キーワード(飲食以外)
ACCOUNT_KEYWORDS = {
    "旅費交通費": [
        "タクシー", "TAXI", "Taxi", "JR", "新幹線", "電車", "バス", "高速", "駐車",
        "ETC", "Uber", "UBER", "GO", "Lyft", "DiDi", "ANA", "JAL", "航空", "空港",
        "鉄道", "京王", "京急", "東京メトロ", "都営",
    ],
    "事務用品費": [
        "文具", "ノート", "ペン", "ファイル", "コクヨ", "アスクル", "ASKUL",
        "オフィス用品", "封筒", "印鑑", "プリンタ", "コピー用紙", "トナー",
    ],
    "消耗品費": [
        "Amazon", "アマゾン", "ヨドバシ", "ビックカメラ", "BIC", "ニトリ", "ダイソー",
        "セリア", "キャンドゥ", "ホームセンター",
    ],
    "通信費": [
        "NTT", "ドコモ", "DOCOMO", "Docomo", "Softbank", "ソフトバンク", "au",
        "楽天モバイル", "光回線", "インターネット",
        "AWS", "Amazon Web", "Microsoft", "Google", "GitHub", "OpenAI", "Anthropic",
        "Slack", "Zoom", "Notion", "Stripe", "クラウド",
    ],
    "水道光熱費": [
        "電気", "ガス", "水道", "東京電力", "東京ガス", "東京水道",
        "関西電力", "中部電力", "九州電力", "TEPCO", "TGES",
    ],
    "新聞図書費": [
        "書店", "BOOK", "紀伊国屋", "丸善", "ジュンク堂", "Amazon Kindle",
        "新聞", "日経", "朝日", "読売", "毎日", "産経", "雑誌",
    ],
    "支払手数料": [
        "振込手数料", "事務手数料", "決済手数料", "送金手数料", "両替手数料",
    ],
    "広告宣伝費": [
        "広告", "宣伝", "Google広告", "Yahoo広告", "Facebook広告", "Meta広告",
        "Instagram広告", "X広告", "LINE広告",
    ],
    "保険料": [
        "保険", "損保", "生命保険", "東京海上", "三井住友海上", "あいおい",
    ],
    "租税公課": [
        "印紙", "収入印紙", "登録免許税", "固定資産税", "事業税", "消費税納付",
    ],
    "地代家賃": [
        "家賃", "賃料", "テナント", "オフィス賃料", "駐車場代",
    ],
}

# 高額判定の閾値(固定資産候補・要確認マーク用)
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

    # 1. 勘定科目を推定(config から飲食しきい値を取得)
    journal_rules = config.get("journal_rules", {})
    entertainment_threshold = journal_rules.get(
        "entertainment_amount_threshold", ENTERTAINMENT_AMOUNT_THRESHOLD
    )
    account = _estimate_account(ocr_result, client_config, entertainment_threshold)

    # 2. 税区分を判定
    tax_rate = ocr_result.get("tax_rate") or 10
    tax_category = _estimate_tax_category(tax_rate, ocr_result)

    # 3. 支払方法から借方/貸方を決定
    payment_method = ocr_result.get("payment_method", "unknown")
    debit_credit = _estimate_debit_credit(payment_method, account, ocr_result.get("total_amount", 0))

    # 4. 確認必須フラグ
    needs_review = _needs_review(ocr_result, account)

    # 5. 1人当たり金額(人数情報あれば計算)
    amount = ocr_result.get("total_amount", 0) or 0
    people_count = ocr_result.get("people_count")
    if people_count and isinstance(people_count, (int, float)) and people_count > 0:
        per_person_amount = int(amount / people_count)
    else:
        per_person_amount = None

    # 6. 仕訳エントリを構築
    return {
        "client_id": client_id,
        "transaction_date": ocr_result.get("date") or datetime.now().strftime("%Y-%m-%d"),
        "vendor": ocr_result.get("vendor"),
        "vendor_registration_number": ocr_result.get("vendor_registration_number"),
        "debit": debit_credit["debit"],
        "credit": debit_credit["credit"],
        "amount": amount,
        "tax_amount": ocr_result.get("tax_amount"),
        "tax_rate": tax_rate,
        "tax_category": tax_category,
        "description": _build_description(ocr_result),
        "payment_method_hint": payment_method,  # OCRが推定した参考値(仕訳には反映しない)
        # 人数考慮の判定情報
        "people_count": people_count,
        "per_person_amount": per_person_amount,
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


def _estimate_account(
    ocr_result: dict[str, Any],
    client_config: dict[str, Any],
    entertainment_threshold: int = ENTERTAINMENT_AMOUNT_THRESHOLD,
) -> str:
    """
    勘定科目をキーワード+金額(1人当たり考慮)ベースで推定する。

    判定優先順位:
    1. 宿泊系(摘要に「宿泊」「ROOM」等) → 旅費交通費
       (ホテル支払先 + 摘要に飲食キーワードがある場合は除外)
    2. 軽食系(弁当・カフェ・牛丼・コンビニ等) → 常に会議費
    3. 会食系(居酒屋・レストラン・料亭等) → 1人当たり金額で判定
       - 1人当たりしきい値以下 → 会議費
       - 1人当たりしきい値超 → 接待交際費(令和6年税制改正準拠)
       ※ people_count あれば total_amount / people_count、なければ total_amount で判定
    4. ホテル系支払先(摘要なし) → 旅費交通費(出張宿泊と推定)
    5. その他カテゴリ(交通費・通信費等) → キーワードマッチ
    6. マッチなし → デフォルト(消耗品費)
    """
    vendor = str(ocr_result.get("vendor") or "")
    description_text = " ".join(
        item.get("description", "") or "" for item in ocr_result.get("items", [])
    )
    notes_text = str(ocr_result.get("notes") or "")

    text_blob = " ".join([vendor, description_text, notes_text]).lower()
    description_lower = description_text.lower()
    vendor_lower = vendor.lower()

    amount = ocr_result.get("total_amount") or 0
    people_count = ocr_result.get("people_count")

    # 1人当たり金額(人数情報あれば計算、なければ総額をそのまま)
    if people_count and isinstance(people_count, (int, float)) and people_count > 0:
        per_person = amount / people_count
    else:
        per_person = amount

    has_dining_in_description = any(
        kw.lower() in description_lower for kw in DINING_DESCRIPTION_KEYWORDS
    )
    has_accommodation_in_description = any(
        kw.lower() in description_lower for kw in ACCOMMODATION_DESCRIPTION_KEYWORDS
    )
    has_accommodation_in_vendor = any(
        kw.lower() in vendor_lower for kw in ACCOMMODATION_VENDOR_KEYWORDS
    )

    # 1. 摘要に宿泊キーワード → 旅費交通費(ホテル+宿泊費 等)
    if has_accommodation_in_description and not has_dining_in_description:
        return "旅費交通費"

    # 2. 軽食系 → 常に会議費
    for kw in LIGHT_FOOD_KEYWORDS:
        if kw.lower() in text_blob:
            return "会議費"

    # 3. 会食系 → 1人当たり金額で判定
    for kw in DINING_KEYWORDS:
        if kw.lower() in text_blob:
            # ホテル系で飲食キーワードが摘要にない場合は宿泊扱い
            if has_accommodation_in_vendor and not has_dining_in_description:
                return "旅費交通費"
            if per_person > entertainment_threshold:
                return "接待交際費"
            return "会議費"

    # 4. ホテル系支払先で摘要が空・不明 → 旅費交通費
    if has_accommodation_in_vendor:
        return "旅費交通費"

    # 5. その他カテゴリのキーワードマッチ
    for account, keywords in ACCOUNT_KEYWORDS.items():
        for kw in keywords:
            if kw.lower() in text_blob:
                return account

    # 6. デフォルト
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
