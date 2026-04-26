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

from .journal import generate_journal
from .mf_client import get_mf_client
from .ocr import extract_receipt

logger = logging.getLogger(__name__)


def process_receipt(
    file_path: str | Path,
    client_id: str = "client_a",
    auto_register: bool = False,
    archive: bool = False,
) -> dict[str, Any]:
    """
    領収書1枚を処理する。

    Args:
        file_path: 領収書ファイル
        client_id: クライアントID
        auto_register: True なら確認なしでマネフォ登録(needs_review=Trueでもスキップ無し)
                       False なら下書きのみ作成し、登録は呼び出し元(UI)に委ねる
        archive: True なら処理後に data/processed/ へ移動

    Returns:
        {"ocr": ..., "journal": ..., "registration": ...} の結合結果
    """
    path = Path(file_path)

    # 1. OCR
    logger.info(f"[OCR] {path}")
    ocr_result = extract_receipt(path)

    if ocr_result.get("error"):
        return {
            "status": "ocr_failed",
            "ocr": ocr_result,
            "journal": None,
            "registration": None,
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
        }

    # 3. 登録(オプション)
    registration = None
    if auto_register:
        logger.info(f"[MF Register] {path}")
        client = get_mf_client()
        registration = client.post_journal(journal)

    # 4. アーカイブ(オプション)
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
