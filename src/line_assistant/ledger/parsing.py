import re
import unicodedata

from line_assistant.core.errors import DomainError

_SEPARATOR_PATTERN = re.compile(r"[,，、;；\n\r]+")
_AMOUNT_PATTERN = re.compile(r"^[0-9]+$")


def parse_batch_names(text: str, *, max_items: int = 30, max_length: int = 100) -> list[str]:
    """解析記帳子分類或付款工具的批次設定名稱。"""

    normalized = unicodedata.normalize("NFKC", text).strip()
    if not normalized or normalized in {"跳過", "略過"}:
        return []

    result: list[str] = []
    seen: set[str] = set()
    for raw_name in _SEPARATOR_PATTERN.split(normalized):
        name = " ".join(raw_name.split())
        if not name:
            continue
        if len(name) > max_length:
            raise DomainError(f"名稱「{name[:12]}…」超過 {max_length} 個字元")
        key = name.casefold()
        if key not in seen:
            seen.add(key)
            result.append(name)

    if len(result) > max_items:
        raise DomainError(f"一次最多可輸入 {max_items} 個項目")
    return result


def parse_twd_amount(text: str, *, maximum: int = 999_999_999) -> int:
    """將常見台幣整數輸入正規化成整數。"""

    normalized = unicodedata.normalize("NFKC", text).strip()
    normalized = re.sub(r"^(?:NT\$|NTD|\$)", "", normalized, flags=re.IGNORECASE)
    normalized = re.sub(r"元$", "", normalized).replace(",", "").replace(" ", "")
    if not normalized or not _AMOUNT_PATTERN.fullmatch(normalized):
        raise DomainError("金額只能輸入正整數，例如：120 或 1,200")
    amount = int(normalized)
    if amount <= 0:
        raise DomainError("金額必須大於 0")
    if amount > maximum:
        raise DomainError(f"金額不可超過 {maximum:,} 元")
    return amount
