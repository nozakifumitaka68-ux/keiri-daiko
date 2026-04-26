"""
日本時間(JST)ヘルパ

Streamlit Cloud等のサーバーは UTC で動くため、
タイムスタンプを記録する時は明示的に JST に揃える。
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")


def now_jst() -> datetime:
    """JST(日本標準時)の現在時刻を返す"""
    return datetime.now(JST)


def now_iso() -> str:
    """JST の現在時刻を ISO8601 文字列で返す(タイムゾーン情報付き)"""
    return now_jst().isoformat()


def now_date_str() -> str:
    """JST の今日の日付を YYYY-MM-DD 形式で返す"""
    return now_jst().strftime("%Y-%m-%d")


def now_compact_str() -> str:
    """JST の現在時刻を YYYYMMDD_HHMMSS 形式で返す(ファイル名用)"""
    return now_jst().strftime("%Y%m%d_%H%M%S")


def now_yyyymmdd() -> str:
    """JST の今日の日付を YYYYMMDD 形式で返す(ファイル名用)"""
    return now_jst().strftime("%Y%m%d")
