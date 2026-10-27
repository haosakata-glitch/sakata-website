#!/usr/bin/env python3
"""bookshelf/inbox/*.json にある候補をまとめて取り込み、処理した分は bookshelf/processed/ に移す。

GitHub Actions(.github/workflows/add-books.yml)から呼ばれる。携帯から inbox に JSON を置くだけで
書誌と書影の取得・追記まで進めるための入口。通信に失敗した分は inbox に残るので、後から再実行できる。

環境変数: BOOK_DEBUG=1 で、取得結果が0件のときに応答の先頭をログへ出す。
"""
from __future__ import annotations

import json
import os
import sys

import book_lookup as bl


def main() -> int:
    inbox = bl.ROOT / "bookshelf" / "inbox"
    processed = bl.ROOT / "bookshelf" / "processed"
    files = sorted(inbox.glob("*.json"))
    if not files:
        print("inbox に取り込む JSON はありません")
        return 0

    debug = os.environ.get("BOOK_DEBUG") == "1"
    data = bl.load_books()
    totals = {"added": 0, "review": 0, "skipped": 0, "network": 0}
    kept = 0

    for path in files:
        print(f"== {path.name}")
        try:
            entries = json.loads(path.read_text(encoding="utf-8"))
        except ValueError as exc:
            print(f"  ! JSON として読めません（inbox に残します）: {exc}")
            kept += 1
            continue
        if isinstance(entries, dict):
            entries = entries.get("books") or entries.get("candidates") or []
        if not isinstance(entries, list):
            print("  ! 配列ではありません（inbox に残します）")
            kept += 1
            continue

        file_counts = {"added": 0, "review": 0, "skipped": 0, "network": 0}
        for entry in entries:
            if isinstance(entry, dict):
                file_counts[bl.add_one(data, entry, debug=debug)] += 1
        for key, value in file_counts.items():
            totals[key] += value

        if file_counts["network"]:
            print("  ! 通信に失敗した本があるため、このファイルは inbox に残します（再実行すると続きから取り込みます）")
            kept += 1
            continue
        processed.mkdir(parents=True, exist_ok=True)
        target = processed / path.name
        if target.exists():
            target = processed / f"{path.stem}-{bl.now_iso()[:19].replace(':', '')}{path.suffix}"
        path.replace(target)

    if totals["added"] or totals["review"]:
        bl.save_books(data)
    print(f"\n合計: 追加 {totals['added']} / 要確認 {totals['review']} / 既存 {totals['skipped']} / 通信失敗 {totals['network']}")
    if kept and not (totals["added"] or totals["review"] or totals["skipped"]):
        return 1  # 何も進まなかったときだけ失敗扱いにする
    return 0


if __name__ == "__main__":
    sys.exit(main())
