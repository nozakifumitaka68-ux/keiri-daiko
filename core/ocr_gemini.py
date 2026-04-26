"""
Google Gemini API で領収書OCR

Anthropic Claude の代替として、Gemini Vision で画像から構造化データを抽出する。
無料枠が大きい(月45,000枚相当)ので運用コストゼロで動かせる。

環境変数:
  GEMINI_API_KEY: Google AI Studio で取得した APIキー
  GEMINI_MODEL: 使用モデル(デフォルト: gemini-2.5-flash)
"""

from __future__ import annotations

import json
import os
from io import BytesIO
from pathlib import Path
from typing import Any

# 共通プロンプトを ocr.py から再利用
from .ocr import EXTRACTION_PROMPT


def extract_receipt_gemini(file_path: Path) -> dict[str, Any]:
    """Gemini APIで領収書から構造化JSONを抽出する"""
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        return _error("GEMINI_API_KEY が設定されていません")

    try:
        import google.generativeai as genai
    except ImportError:
        return _error("google-generativeai パッケージがインストールされていません")

    try:
        from PIL import Image
    except ImportError:
        return _error("Pillow がインストールされていません")

    # API設定
    genai.configure(api_key=api_key)

    model_name = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
    model = genai.GenerativeModel(model_name)

    # 画像読込(PDFの場合は1ページ目を画像化)
    image = _load_image(file_path)
    if isinstance(image, dict):  # エラー
        return image

    raw_text = ""
    try:
        response = model.generate_content(
            [EXTRACTION_PROMPT, image],
            generation_config={
                "temperature": 0,
                "response_mime_type": "application/json",
            },
        )
        raw_text = response.text.strip() if response.text else ""

        # JSON抽出(念のためコードブロック対応)
        cleaned = raw_text
        if cleaned.startswith("```"):
            cleaned = cleaned.split("```")[1]
            if cleaned.startswith("json"):
                cleaned = cleaned[4:]
            cleaned = cleaned.strip()

        result = json.loads(cleaned)
        result["_source_file"] = str(file_path)
        result["_stub"] = False
        result["_engine"] = "gemini"
        result["_model"] = model_name
        return result

    except json.JSONDecodeError as e:
        return _error(f"AI応答のJSON解析失敗: {e}", raw_text=raw_text)
    except Exception as e:
        return _error(f"Gemini API呼出エラー: {e}")


def _load_image(file_path: Path):
    """画像読込(PDFの場合は1ページ目を画像化)"""
    from PIL import Image

    ext = file_path.suffix.lower()
    if ext == ".pdf":
        try:
            from pdf2image import convert_from_path
            images = convert_from_path(
                str(file_path), first_page=1, last_page=1, dpi=200
            )
            if not images:
                return _error("PDFの読み込みに失敗しました")
            return images[0]
        except Exception as e:
            return _error(f"PDF処理エラー: {e}. poppler のインストールが必要かもしれません")
    else:
        return Image.open(file_path)


def _error(message: str, **extra: Any) -> dict[str, Any]:
    return {
        "error": message,
        "doc_type": None,
        "date": None,
        "vendor": None,
        "total_amount": None,
        "_stub": False,
        "_engine": "gemini",
        **extra,
    }


# CLI実行
if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("使い方: python -m core.ocr_gemini <領収書ファイル>")
        sys.exit(1)

    target = Path(sys.argv[1])
    result = extract_receipt_gemini(target)
    print(json.dumps(result, ensure_ascii=False, indent=2))
