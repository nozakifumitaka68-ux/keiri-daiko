"""
処理パイプライン

領収書ファイル → OCR → 仕訳生成 → マネフォ登録 を一気通貫で実行する。
CLI実行・StreamlitUIから共通で呼び出される中核モジュール。
"""

from __future__ import annotations

import json
import logging
import os
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

from .duplicate import calculate_file_hash_from_path, find_duplicate_receipts
from .journal import create_failed_placeholder, generate_journal
from .jst import now_compact_str
from .mf_client import get_mf_client
from .ocr import extract_receipt
from .storage import (
    get_receipt_image_bytes,
    load_history,
    save_entry,
    save_receipt_image,
    update_entry,
)

logger = logging.getLogger(__name__)


def process_receipt(
    file_path: str | Path,
    client_id: str = "client_a",
    auto_register: bool = False,
    archive: bool = False,
    skip_duplicates: bool = True,
    force_register: bool = False,
    save_image: bool = True,
    original_filename: str | None = None,
) -> dict[str, Any]:
    """
    領収書1枚を処理する。

    Args:
        file_path: 領収書ファイル
        client_id: クライアントID
        auto_register: True なら確認なしでマネフォ登録
        archive: True なら処理後に data/processed/ へ移動
        skip_duplicates: True なら既存の重複(ハッシュ一致or取引データ一致)を検出してスキップ
        force_register: True なら重複検出されても強制登録
        save_image: True なら領収書画像を data/receipts/ に保存
        original_filename: ユーザーがアップロードした元ファイル名(拡張子取得用)

    Returns:
        {
          "status": "ok" | "ocr_failed" | "journal_failed" | "duplicate_skipped",
          "ocr": ...,
          "journal": ...,
          "registration": ...,
          "duplicate_info": {...},  # 重複検出時のみ
          "receipt_path": ...,  # 保存先相対パス(save_image=True時)
        }
    """
    path = Path(file_path)

    # 0. ファイルハッシュ計算 + 画像保存
    file_hash = calculate_file_hash_from_path(path) if path.exists() else None
    receipt_path = None
    if save_image and path.exists() and file_hash:
        try:
            with open(path, "rb") as f:
                file_bytes = f.read()
            display_name = original_filename or path.name
            receipt_path = save_receipt_image(
                file_bytes=file_bytes,
                client_id=client_id,
                file_hash=file_hash,
                original_filename=display_name,
            )
        except Exception as e:
            logger.warning(f"画像保存失敗(処理は続行): {e}")

    # 1. OCR
    logger.info(f"[OCR] {path}")
    ocr_result = extract_receipt(path)

    if ocr_result.get("error"):
        # OCR失敗時もプレースホルダー仕訳を作成して画像と紐付け保存
        # → 後から「再OCR実行」または「手動入力」で完成させられる
        placeholder = create_failed_placeholder(
            client_id=client_id,
            file_hash=file_hash,
            receipt_path=receipt_path,
            receipt_filename=original_filename or path.name,
            error_message=ocr_result.get("error", "不明なエラー"),
        )
        saved_journal = None
        if auto_register:
            try:
                saved_journal = save_entry(placeholder)
            except Exception as e:
                logger.warning(f"プレースホルダー保存失敗: {e}")

        return {
            "status": "ocr_failed",
            "ocr": ocr_result,
            "journal": saved_journal or placeholder,
            "registration": None,
            "file_hash": file_hash,
            "receipt_path": receipt_path,
        }

    # 2. 仕訳生成
    logger.info(f"[Journal] {path}")
    journal = generate_journal(ocr_result, client_id=client_id)

    if journal.get("error"):
        return {
            "status": "journal_failed",
            "ocr": ocr_result,
            "journal": journal,
            "registration": None,
            "file_hash": file_hash,
        }

    # 3. 重複検出
    duplicate_info = find_duplicate_receipts(
        client_id=client_id,
        file_hash=file_hash,
        transaction_date=journal.get("transaction_date"),
        amount=journal.get("amount"),
        vendor=journal.get("vendor"),
    )

    if duplicate_info["has_duplicate"] and skip_duplicates and not force_register:
        logger.warning(f"[Duplicate detected] {path} - skipping registration")
        return {
            "status": "duplicate_skipped",
            "ocr": ocr_result,
            "journal": journal,
            "registration": None,
            "duplicate_info": duplicate_info,
            "file_hash": file_hash,
        }

    # 仕訳に file_hash と receipt_path を埋め込んで保存できるようにする
    journal["file_hash"] = file_hash
    journal["receipt_path"] = receipt_path
    journal["receipt_filename"] = original_filename or (path.name if path else None)

    # 4. 登録(オプション)
    registration = None
    if auto_register:
        logger.info(f"[MF Register] {path}")
        client = get_mf_client()
        registration = client.post_journal(journal)

    # 5. アーカイブ(オプション)
    if archive and path.exists():
        archive_dir = Path(__file__).parent.parent / "data" / "processed" / client_id
        archive_dir.mkdir(parents=True, exist_ok=True)
        timestamp = now_compact_str()
        archived_path = archive_dir / f"{timestamp}_{path.name}"
        shutil.copy2(path, archived_path)
        logger.info(f"[Archive] {path} -> {archived_path}")

    return {
        "status": "ok",
        "ocr": ocr_result,
        "journal": journal,
        "registration": registration,
        "duplicate_info": duplicate_info if duplicate_info["has_duplicate"] else None,
        "file_hash": file_hash,
        "receipt_path": receipt_path,
    }


def retry_ocr(journal_id: str, client_id: str = "client_a") -> dict[str, Any]:
    """
    OCR失敗状態の仕訳を再OCR実行する。

    保存済の領収書画像を取り出して、もう一度 Gemini Vision に投げる。
    成功したら仕訳の中身を更新(IDは維持)。

    Returns:
        {"status": "ok"|"still_failed"|"image_not_found"|"not_found", ...}
    """
    # 1. 既存仕訳を取得
    history = load_history(include_deleted=False)
    target = next((h for h in history if h.get("id") == journal_id), None)
    if not target:
        return {"status": "not_found", "error": "対象仕訳が見つかりません"}

    receipt_path = target.get("receipt_path")
    if not receipt_path:
        return {"status": "image_not_found", "error": "保存済み画像がありません(再アップロードが必要)"}

    # 2. 保存済画像を取得
    image_bytes = get_receipt_image_bytes(receipt_path)
    if not image_bytes:
        return {"status": "image_not_found", "error": "画像ファイルがストレージから取得できません"}

    # 3. 一時ファイルにして OCR 実行
    receipt_filename = target.get("receipt_filename") or "receipt.jpg"
    suffix = Path(receipt_filename).suffix or ".jpg"
    import tempfile
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(image_bytes)
        tmp_path = tmp.name

    try:
        ocr_result = extract_receipt(tmp_path)
        if ocr_result.get("error"):
            # まだ失敗 → エラーメッセージを更新
            update_entry(journal_id, {
                "review_reasons": [
                    "OCR再実行も失敗しました",
                    f"エラー: {ocr_result.get('error')}",
                ],
                "ocr_raw": {
                    "_failed": True,
                    "_error": ocr_result.get("error"),
                    "_retried_at": now_compact_str(),
                },
            })
            return {
                "status": "still_failed",
                "error": ocr_result.get("error"),
                "ocr": ocr_result,
            }

        # 4. 成功 → 仕訳生成して既存IDで上書き
        new_journal = generate_journal(ocr_result, client_id=client_id)
        # 画像情報を引き継ぐ
        new_journal["receipt_path"] = receipt_path
        new_journal["receipt_filename"] = receipt_filename
        new_journal["file_hash"] = target.get("file_hash")
        # 失敗状態 → cash_pending に戻す
        new_journal["match_status"] = "cash_pending"
        new_journal["matched_card_statement_id"] = None

        updated = update_entry(journal_id, new_journal)
        return {
            "status": "ok",
            "journal": updated,
            "ocr": ocr_result,
        }
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


def process_batch(
    file_paths: list[str | Path],
    client_id: str = "client_a",
    auto_register: bool = False,
) -> list[dict[str, Any]]:
    """複数領収書を一括処理"""
    results = []
    for fp in file_paths:
        try:
            result = process_receipt(fp, client_id=client_id, auto_register=auto_register)
            results.append(result)
        except Exception as e:
            logger.exception(f"処理失敗: {fp}")
            results.append({
                "status": "exception",
                "file": str(fp),
                "error": str(e),
            })
    return results


# CLI実行
if __name__ == "__main__":
    import sys

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    if len(sys.argv) < 2:
        print("使い方:")
        print("  python -m core.pipeline <領収書ファイル> [--register] [--archive] [--client=client_a]")
        sys.exit(1)

    target = sys.argv[1]
    auto_register = "--register" in sys.argv
    archive = "--archive" in sys.argv
    client_id = "client_a"
    for arg in sys.argv[2:]:
        if arg.startswith("--client="):
            client_id = arg.split("=", 1)[1]

    result = process_receipt(
        target,
        client_id=client_id,
        auto_register=auto_register,
        archive=archive,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
