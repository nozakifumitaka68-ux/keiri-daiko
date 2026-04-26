"""
処理パイプライン

領収書ファイル → OCR → 仕訳生成 → マネフォ登録 を一気通貫で実行する。
CLI実行・StreamlitUIから共通で呼び出される中核モジュール。
"""

from __future__ import annotations

import json
import logging
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

from .duplicate import calculate_file_hash_from_path, find_duplicate_receipts
from .journal import generate_journal
from .mf_client import get_mf_client
from .ocr import extract_receipt
from .storage import save_receipt_image

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
        return {
            "status": "ocr_failed",
            "ocr": ocr_result,
            "journal": None,
            "registration": None,
            "file_hash": file_hash,
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
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
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
