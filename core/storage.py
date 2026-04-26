"""
データ永続化モジュール

仕訳履歴・カード利用明細をJSONファイルに保存・読込する。
SQLiteの代わりにシンプルなJSONベースで運用(MVP仕様)。
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

# ファイルパス
DATA_DIR = Path(__file__).parent.parent / "data"
HISTORY_PATH = DATA_DIR / "history.json"
CARD_STATEMENTS_PATH = DATA_DIR / "card_statements.json"
BANK_STATEMENTS_PATH = DATA_DIR / "bank_statements.json"


# ===================================
# 共通
# ===================================

def _ensure_dir() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def _read_json(path: Path, default: Any) -> Any:
    _ensure_dir()
    if not path.exists():
        return default
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _write_json(path: Path, data: Any) -> None:
    _ensure_dir()
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ===================================
# 仕訳履歴(receipts/journals)
# ===================================

def init_storage() -> None:
    """ストレージ初期化"""
    if not HISTORY_PATH.exists():
        _write_json(HISTORY_PATH, [])
    if not CARD_STATEMENTS_PATH.exists():
        _write_json(CARD_STATEMENTS_PATH, [])
    if not BANK_STATEMENTS_PATH.exists():
        _write_json(BANK_STATEMENTS_PATH, [])


def load_history(include_deleted: bool = False) -> list[dict[str, Any]]:
    """
    仕訳履歴を読込。
    Args:
        include_deleted: True なら削除済も含める(ゴミ箱表示用)
    """
    init_storage()
    history = _read_json(HISTORY_PATH, [])
    if include_deleted:
        return history
    return [h for h in history if not h.get("is_deleted")]


def load_deleted_history() -> list[dict[str, Any]]:
    """削除済の仕訳のみを読込(ゴミ箱用)"""
    init_storage()
    history = _read_json(HISTORY_PATH, [])
    return [h for h in history if h.get("is_deleted")]


def save_entry(entry: dict[str, Any]) -> dict[str, Any]:
    """仕訳履歴に1件追加"""
    init_storage()
    enriched = {
        "id": str(uuid.uuid4()),
        "created_at": datetime.now().isoformat(),
        **entry,
    }
    history = load_history()
    history.append(enriched)
    _write_json(HISTORY_PATH, history)
    return enriched


def update_entry(entry_id: str, updates: dict[str, Any]) -> dict[str, Any] | None:
    """既存仕訳を更新"""
    history = load_history(include_deleted=True)
    for i, e in enumerate(history):
        if e.get("id") == entry_id:
            history[i] = {**e, **updates, "updated_at": datetime.now().isoformat()}
            _write_json(HISTORY_PATH, history)
            return history[i]
    return None


def delete_entry(entry_id: str, reason: str = "") -> dict[str, Any] | None:
    """仕訳をソフト削除(復元可能)"""
    return update_entry(entry_id, {
        "is_deleted": True,
        "deleted_at": datetime.now().isoformat(),
        "delete_reason": reason,
    })


def restore_entry(entry_id: str) -> dict[str, Any] | None:
    """削除済仕訳を復元"""
    return update_entry(entry_id, {
        "is_deleted": False,
        "deleted_at": None,
        "delete_reason": None,
        "restored_at": datetime.now().isoformat(),
    })


def hard_delete_entry(entry_id: str) -> bool:
    """物理削除(完全消去・復元不可)。慎重に使うこと"""
    history = load_history(include_deleted=True)
    new_history = [e for e in history if e.get("id") != entry_id]
    if len(new_history) == len(history):
        return False
    _write_json(HISTORY_PATH, new_history)
    return True


def find_by_client(client_id: str, include_deleted: bool = False) -> list[dict[str, Any]]:
    """指定クライアントの仕訳のみ抽出"""
    return [
        e for e in load_history(include_deleted=include_deleted)
        if e.get("client_id") == client_id
    ]


def find_pending_receipts(client_id: str) -> list[dict[str, Any]]:
    """突合待ち(cash_pending)の仕訳のみ抽出(削除済は除外)"""
    return [
        e for e in find_by_client(client_id)
        if e.get("match_status") == "cash_pending"
    ]


def update_journal_match(
    journal_id: str,
    card_statement_id: str,
    new_credit: str = "未払金",
) -> dict[str, Any] | None:
    """
    カード明細との突合成功時に仕訳を更新。

    貸方:現金 → 貸方:未払金 に書き換え、
    match_status を "card_matched" に変更する。
    """
    return update_entry(journal_id, {
        "credit": new_credit,
        "match_status": "card_matched",
        "matched_card_statement_id": card_statement_id,
    })


# ===================================
# カード利用明細
# ===================================

def load_card_statements(include_deleted: bool = False) -> list[dict[str, Any]]:
    """カード明細を読込"""
    init_storage()
    statements = _read_json(CARD_STATEMENTS_PATH, [])
    if include_deleted:
        return statements
    return [s for s in statements if not s.get("is_deleted")]


def load_deleted_card_statements() -> list[dict[str, Any]]:
    """削除済のカード明細のみ"""
    init_storage()
    statements = _read_json(CARD_STATEMENTS_PATH, [])
    return [s for s in statements if s.get("is_deleted")]


def save_card_statement(statement: dict[str, Any]) -> dict[str, Any]:
    """カード明細1件を保存"""
    init_storage()
    enriched = {
        "id": str(uuid.uuid4()),
        "imported_at": datetime.now().isoformat(),
        "match_status": "unmatched",
        "matched_journal_id": None,
        **statement,
    }
    statements = load_card_statements()
    statements.append(enriched)
    _write_json(CARD_STATEMENTS_PATH, statements)
    return enriched


def save_card_statements_bulk(statements: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """カード明細を一括保存"""
    return [save_card_statement(s) for s in statements]


def update_card_statement(
    statement_id: str,
    updates: dict[str, Any],
) -> dict[str, Any] | None:
    """カード明細を更新"""
    statements = load_card_statements(include_deleted=True)
    for i, s in enumerate(statements):
        if s.get("id") == statement_id:
            statements[i] = {**s, **updates, "updated_at": datetime.now().isoformat()}
            _write_json(CARD_STATEMENTS_PATH, statements)
            return statements[i]
    return None


def delete_card_statement(statement_id: str, reason: str = "") -> dict[str, Any] | None:
    """カード明細をソフト削除"""
    return update_card_statement(statement_id, {
        "is_deleted": True,
        "deleted_at": datetime.now().isoformat(),
        "delete_reason": reason,
    })


def restore_card_statement(statement_id: str) -> dict[str, Any] | None:
    """削除済カード明細を復元"""
    return update_card_statement(statement_id, {
        "is_deleted": False,
        "deleted_at": None,
        "delete_reason": None,
        "restored_at": datetime.now().isoformat(),
    })


def find_unmatched_card_statements(client_id: str | None = None) -> list[dict[str, Any]]:
    """突合未済のカード明細のみ抽出"""
    statements = load_card_statements()
    result = [s for s in statements if s.get("match_status") == "unmatched"]
    if client_id:
        result = [s for s in result if s.get("client_id") == client_id]
    return result


def find_card_statements_by_client(client_id: str) -> list[dict[str, Any]]:
    """指定クライアントのカード明細"""
    return [s for s in load_card_statements() if s.get("client_id") == client_id]


# ===================================
# 銀行明細
# ===================================

def load_bank_statements(include_deleted: bool = False) -> list[dict[str, Any]]:
    """銀行明細を読込"""
    init_storage()
    statements = _read_json(BANK_STATEMENTS_PATH, [])
    if include_deleted:
        return statements
    return [s for s in statements if not s.get("is_deleted")]


def load_deleted_bank_statements() -> list[dict[str, Any]]:
    """削除済の銀行明細のみ"""
    init_storage()
    statements = _read_json(BANK_STATEMENTS_PATH, [])
    return [s for s in statements if s.get("is_deleted")]


def save_bank_statement(statement: dict[str, Any]) -> dict[str, Any]:
    """銀行明細1件を保存"""
    init_storage()
    enriched = {
        "id": str(uuid.uuid4()),
        "imported_at": datetime.now().isoformat(),
        "match_status": "unmatched",
        "matched_card_statement_ids": [],
        "settlement_journal_id": None,
        **statement,
    }
    statements = load_bank_statements()
    statements.append(enriched)
    _write_json(BANK_STATEMENTS_PATH, statements)
    return enriched


def save_bank_statements_bulk(statements: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """銀行明細を一括保存"""
    return [save_bank_statement(s) for s in statements]


def update_bank_statement(
    statement_id: str,
    updates: dict[str, Any],
) -> dict[str, Any] | None:
    """銀行明細を更新"""
    statements = load_bank_statements(include_deleted=True)
    for i, s in enumerate(statements):
        if s.get("id") == statement_id:
            statements[i] = {**s, **updates, "updated_at": datetime.now().isoformat()}
            _write_json(BANK_STATEMENTS_PATH, statements)
            return statements[i]
    return None


def delete_bank_statement(statement_id: str, reason: str = "") -> dict[str, Any] | None:
    """銀行明細をソフト削除"""
    return update_bank_statement(statement_id, {
        "is_deleted": True,
        "deleted_at": datetime.now().isoformat(),
        "delete_reason": reason,
    })


def restore_bank_statement(statement_id: str) -> dict[str, Any] | None:
    """削除済銀行明細を復元"""
    return update_bank_statement(statement_id, {
        "is_deleted": False,
        "deleted_at": None,
        "delete_reason": None,
        "restored_at": datetime.now().isoformat(),
    })


def find_bank_statements_by_client(client_id: str) -> list[dict[str, Any]]:
    """指定クライアントの銀行明細"""
    return [s for s in load_bank_statements() if s.get("client_id") == client_id]


def find_unmatched_bank_payments(client_id: str) -> list[dict[str, Any]]:
    """
    未突合のカード引落と思われる銀行明細を抽出。
    出金(amount<0) かつ match_status='unmatched' のもの。
    """
    return [
        s for s in find_bank_statements_by_client(client_id)
        if s.get("match_status") == "unmatched"
        and (s.get("amount") or 0) < 0
    ]


def find_settled_card_statements(client_id: str, card_name: str | None = None) -> list[dict[str, Any]]:
    """銀行引落で決済済(settled)のカード明細"""
    statements = [
        s for s in find_card_statements_by_client(client_id)
        if s.get("settlement_status") == "settled"
    ]
    if card_name:
        statements = [s for s in statements if s.get("card_name") == card_name]
    return statements


def find_unsettled_card_statements(client_id: str, card_name: str | None = None) -> list[dict[str, Any]]:
    """
    銀行引落で未決済のカード明細(card_matched 済 かつ settlement_status != settled)。
    引落突合の対象になるもの。
    """
    statements = [
        s for s in find_card_statements_by_client(client_id)
        if s.get("match_status") == "matched"
        and s.get("settlement_status") != "settled"
    ]
    if card_name:
        statements = [s for s in statements if s.get("card_name") == card_name]
    return statements


# CLIテスト用
if __name__ == "__main__":
    init_storage()
    print(f"History: {HISTORY_PATH} ({len(load_history())} entries)")
    print(f"Card statements: {CARD_STATEMENTS_PATH} ({len(load_card_statements())} entries)")
    print(f"Bank statements: {BANK_STATEMENTS_PATH} ({len(load_bank_statements())} entries)")
