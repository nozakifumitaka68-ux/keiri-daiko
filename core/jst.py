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


def to_jst_display(iso_str: str | None, fmt: str = "%Y-%m-%d %H:%M:%S") -> str:
    """
    ISO文字列(UTC含む)を JST に変換して指定フォーマットで返す。

    Supabase の TIMESTAMPTZ は UTC で返ってくるため、
    UI 表示時に JST に戻すために使う。

    Args:
        iso_str: ISO8601 形式の文字列(タイムゾーン情報あり/なし両対応)
        fmt: strftime フォーマット
    Returns:
        JST に変換された文字列。パース失敗時は元の文字列の先頭19文字
    """
    if not iso_str:
        return ""
    try:
        # 末尾が 'Z' なら UTC → +00:00 に置換(Pythonで解釈可能に)
        normalized = iso_str.replace("Z", "+00:00")
        dt = datetime.fromisoformat(normalized)
        # タイムゾーン情報がない場合は UTC とみなす(Supabaseの返却仕様)
        if dt.tzinfo is None:
            from datetime import timezone
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(JST).strftime(fmt)
    except (ValueError, TypeError):
        # フォールバック: 元の文字列の先頭19文字を「日付 時刻」形式に
        return str(iso_str)[:19].replace("T", " ")
