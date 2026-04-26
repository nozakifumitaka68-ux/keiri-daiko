"""
領収書OCRモジュール

Claude Vision API を使って領収書画像から構造化データを抽出する。
APIキーがない・OCR_STUB_MODE=1 の場合はダミーJSONを返すスタブモードで動作する。
"""

from __future__ import annotations

import base64
import json
import os
import re
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

load_dotenv()

# ===================================
# 定数
# ===================================

# Claude に渡すプロンプト
EXTRACTION_PROMPT = """\
あなたは経理処理を支援するAIアシスタントです。
渡された領収書・レシート・請求書の画像から、以下のJSON形式で情報を抽出してください。

抽出フォーマット(必ずこの構造で返してください):
{
  "doc_type": "receipt" | "invoice" | "delivery_note",
  "date": "YYYY-MM-DD",
  "vendor": "支払先の名称",
  "vendor_registration_number": "適格請求書発行事業者の登録番号(T+13桁)、なければnull",
  "total_amount": 合計金額(数値),
  "tax_amount": 消費税額(数値、不明ならnull),
  "tax_rate": 税率(10 or 8、複数税率混在ならnull),
  "is_tax_included": 税込ならtrue,
  "items": [
    {"description": "品目名", "amount": 金額}
  ],
  "payment_method": "cash" | "card" | "bank" | "unknown",
  "confidence": 0.0〜1.0(読取確信度),
  "notes": "特記事項(手書き・かすれ・複数頁等があれば)"
}

ルール:
- 日付は和暦の場合も西暦に変換してください
- 金額は数値型(カンマ・円記号なし)
- 不明な項目は null
- 税率は10%/8%のどちらかが明記されていない場合はnull
- JSONのみを返し、それ以外の説明文は含めないでください
"""

# サポートする画像フォーマット
SUPPORTED_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
SUPPORTED_PDF_EXTENSIONS = {".pdf"}


# ===================================
# 公開関数
# ===================================

def extract_receipt(file_path: str | Path) -> dict[str, Any]:
    """
    領収書ファイルから構造化データを抽出する。

    OCRエンジンは環境変数 OCR_ENGINE で選択可能:
      - "stub": ダミー応答(APIキー不要・開発初期用)
      - "gemini": Google Gemini API(無料枠が大きい・推奨)
      - "claude": Anthropic Claude Vision(高精度・有料)
      - "auto"(デフォルト): GEMINI_API_KEY > ANTHROPIC_API_KEY > stub の優先順で自動選択

    Args:
        file_path: 領収書の画像 or PDFのパス

    Returns:
        構造化されたJSON(dict)。エラー時は error キーを含む。
    """
    path = Path(file_path)

    if not path.exists():
        return _error_response(f"ファイルが見つかりません: {path}")

    engine = _select_engine()

    if engine == "stub":
        return _stub_response(path)

    if engine == "gemini":
        from .ocr_gemini import extract_receipt_gemini
        return extract_receipt_gemini(path)

    if engine == "claude":
        return _extract_with_claude(path)

    return _error_response(f"不明なOCRエンジン: {engine}")


def _select_engine() -> str:
    """OCRエンジンを選択する"""
    # 明示指定があればそれを使う
    explicit = (os.getenv("OCR_ENGINE") or "").lower().strip()
    if explicit in ("stub", "gemini", "claude"):
        return explicit

    # 後方互換: OCR_STUB_MODE=1 は強制スタブ
    if os.getenv("OCR_STUB_MODE") == "1":
        return "stub"

    # 自動選択(無料枠の大きい Gemini を優先)
    if os.getenv("GEMINI_API_KEY"):
        return "gemini"
    if os.getenv("ANTHROPIC_API_KEY"):
        return "claude"
    return "stub"


def _extract_with_claude(path: Path) -> dict[str, Any]:
    """Claude Vision でOCRする(既存ロジック)"""
    ext = path.suffix.lower()
    if ext in SUPPORTED_IMAGE_EXTENSIONS:
        return _extract_from_image(path)
    elif ext in SUPPORTED_PDF_EXTENSIONS:
        return _extract_from_pdf(path)
    else:
        return _error_response(f"サポートされていない形式です: {ext}")


# ===================================
# 内部関数
# ===================================

def _is_stub_mode() -> bool:
    """スタブモードかどうか判定(後方互換用、新コードは _select_engine() を使う)"""
    return _select_engine() == "stub"


def _stub_response(path: Path) -> dict[str, Any]:
    """
    APIキー無しでも動くダミーレスポンス。
    開発初期・APIキー取得待ちの間に使用する。

    ファイル名から vendor / amount / date を推定するパターンを内蔵:
      amazon_3980_20260425.jpg → AMAZON.CO.JP, 3980円, 2026-04-25
      starbucks_580.jpg        → STARBUCKS COFFEE, 580円, デフォルト日付
      test1.jpg                → 汎用ダミー

    これにより突合エンジンの動作確認がスタブモードでも可能になる。
    """
    name = path.stem.lower()

    vendor_map = {
        "amazon": "AMAZON.CO.JP",
        "starbucks": "STARBUCKS COFFEE 渋谷店",
        "yodobashi": "ヨドバシカメラ",
        "jr": "JR東日本 みどりの窓口",
        "anthropic": "Anthropic PBC",
    }
    vendor = f"[STUB] サンプル株式会社 ({path.name})"
    for key, name_value in vendor_map.items():
        if key in name:
            vendor = name_value
            break

    # 金額をファイル名から拾う
    amount_match = re.search(r"(?<!\d)(\d{3,7})(?!\d)", name)
    amount = int(amount_match.group(1)) if amount_match else 3980

    # 日付をファイル名から拾う(20260425 形式)
    date = "2026-04-25"
    date_match = re.search(r"(20\d{2})(\d{2})(\d{2})", name)
    if date_match:
        date = f"{date_match.group(1)}-{date_match.group(2)}-{date_match.group(3)}"

    return {
        "doc_type": "receipt",
        "date": date,
        "vendor": vendor,
        "vendor_registration_number": None,
        "total_amount": amount,
        "tax_amount": int(amount * 10 / 110),
        "tax_rate": 10,
        "is_tax_included": True,
        "items": [{"description": "ダミー商品", "amount": amount}],
        "payment_method": "unknown",
        "confidence": 0.0,
        "notes": "スタブモード: 実際のOCRは実行されていません。.env でANTHROPIC_API_KEYを設定してください。",
        "_source_file": str(path),
        "_stub": True,
    }


def _extract_from_image(path: Path) -> dict[str, Any]:
    """画像ファイルからClaude Visionで抽出"""
    media_type = _get_media_type(path)
    image_data = _encode_file_base64(path)
    return _call_claude_vision(image_data, media_type, str(path))


def _extract_from_pdf(path: Path) -> dict[str, Any]:
    """
    PDFから1ページ目を画像化してOCR。
    複数ページの場合は1ページ目のみ処理する(MVP仕様)。
    """
    try:
        from pdf2image import convert_from_path
    except ImportError:
        return _error_response("pdf2image がインストールされていません")

    try:
        # 1ページ目だけ画像化
        images = convert_from_path(str(path), first_page=1, last_page=1, dpi=200)
        if not images:
            return _error_response("PDFの読み込みに失敗しました")

        # 一時的にPNGに保存してからbase64化
        from io import BytesIO
        buffer = BytesIO()
        images[0].save(buffer, format="PNG")
        image_data = base64.standard_b64encode(buffer.getvalue()).decode("utf-8")
        return _call_claude_vision(image_data, "image/png", str(path))
    except Exception as e:
        # poppler未インストールなどの環境問題
        return _error_response(f"PDF処理エラー: {e}. poppler のインストールが必要かもしれません")


def _call_claude_vision(image_base64: str, media_type: str, source_path: str) -> dict[str, Any]:
    """Claude Vision API を呼んで領収書情報を抽出"""
    try:
        from anthropic import Anthropic
    except ImportError:
        return _error_response("anthropic SDK がインストールされていません")

    import yaml
    config_path = Path(__file__).parent.parent / "config.yaml"
    if config_path.exists():
        with open(config_path, encoding="utf-8") as f:
            config = yaml.safe_load(f)
        model = config.get("claude", {}).get("model", "claude-sonnet-4-5-20250929")
        max_tokens = config.get("claude", {}).get("max_tokens", 2048)
    else:
        model = "claude-sonnet-4-5-20250929"
        max_tokens = 2048

    client = Anthropic()

    try:
        response = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": media_type,
                                "data": image_base64,
                            },
                        },
                        {"type": "text", "text": EXTRACTION_PROMPT},
                    ],
                }
            ],
        )

        # レスポンスからテキスト抽出
        text = response.content[0].text.strip()

        # 念のためJSONマーカーがあれば除去
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
            text = text.strip()

        result = json.loads(text)
        result["_source_file"] = source_path
        result["_stub"] = False
        return result
    except json.JSONDecodeError as e:
        return _error_response(f"AI応答のJSON解析失敗: {e}", raw_text=text if "text" in dir() else "")
    except Exception as e:
        return _error_response(f"Claude API呼出エラー: {e}")


def _encode_file_base64(path: Path) -> str:
    """ファイルをbase64エンコード"""
    with open(path, "rb") as f:
        return base64.standard_b64encode(f.read()).decode("utf-8")


def _get_media_type(path: Path) -> str:
    """拡張子からmedia typeを判定"""
    ext = path.suffix.lower()
    mapping = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
        ".gif": "image/gif",
    }
    return mapping.get(ext, "image/jpeg")


def _error_response(message: str, **extra: Any) -> dict[str, Any]:
    """エラーレスポンス共通フォーマット"""
    return {
        "error": message,
        "doc_type": None,
        "date": None,
        "vendor": None,
        "total_amount": None,
        "_stub": False,
        **extra,
    }


# ===================================
# CLI実行(動作確認用)
# ===================================

if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("使い方: python -m core.ocr <領収書ファイルのパス>")
        sys.exit(1)

    target = sys.argv[1]
    result = extract_receipt(target)
    print(json.dumps(result, ensure_ascii=False, indent=2))
