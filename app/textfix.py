from __future__ import annotations

import re


_MOJIBAKE_RUN = re.compile(r"[\u0080-\u00ff]{2,}")


def _score(text: str) -> int:
    cjk = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    mojibake = sum(1 for ch in text if "\u0080" <= ch <= "\u00ff")
    replacement = text.count("\ufffd")
    return cjk * 8 - mojibake * 2 - replacement * 20


def _repair_run(value: str) -> str:
    candidates = [value]
    for source_encoding in ("latin1", "cp1252"):
        try:
            raw = value.encode(source_encoding)
        except UnicodeEncodeError:
            continue
        for target_encoding in ("utf-8", "gb18030", "gbk"):
            try:
                candidates.append(raw.decode(target_encoding))
            except UnicodeDecodeError:
                continue
    return max(candidates, key=_score)


def repair_text(value: str) -> str:
    if not isinstance(value, str) or not value:
        return value
    repaired = _MOJIBAKE_RUN.sub(lambda match: _repair_run(match.group(0)), value)
    if _score(repaired) > _score(value):
        return repaired
    return value
