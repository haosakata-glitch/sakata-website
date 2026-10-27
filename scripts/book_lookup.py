#!/usr/bin/env python3
"""蔵書アーカイブ: 書名・著者の候補から書誌情報を取得して bookshelf/books.json に追記する。

取得元は国立国会図書館サーチ(OpenSearch)を優先し、見つからない・自信がない場合は
Google Books で補います。書影は NDL のサムネイル、無ければ Google Books から保存します。
標準ライブラリだけで動きます。

使い方:
  python3 scripts/book_lookup.py add-batch candidates.json
      candidates.json = [{"title": "書名", "author": "著者(任意)", "publisher": "出版社(任意)", "isbn": "(任意)"}]
  python3 scripts/book_lookup.py add --title "書名" [--author "著者"] [--isbn 978...]
  python3 scripts/book_lookup.py review                       # 要確認の一覧
  python3 scripts/book_lookup.py resolve ID --isbn 978...     # 要確認を ISBN 指定で確定
  python3 scripts/book_lookup.py resolve ID --pick 1          # 候補の1番目で確定

共通オプション: --dry-run (書き込まない) / --debug (取得結果の先頭を表示)
"""
from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BOOKS_JSON = ROOT / "bookshelf" / "books.json"
COVER_DIR = ROOT / "bookshelf" / "covers"

NDL_OPENSEARCH = "https://ndlsearch.ndl.go.jp/api/opensearch"
NDL_THUMBNAIL = "https://ndlsearch.ndl.go.jp/thumbnail/{isbn}.jpg"
GOOGLE_BOOKS = "https://www.googleapis.com/books/v1/volumes"
USER_AGENT = "sakata-website-bookshelf/1.0 (personal archive; non-commercial)"
REQUEST_INTERVAL = 1.0  # 秒。相手サーバーに負担をかけないための間隔
MATCH_THRESHOLD = 0.80
XSI_TYPE = "{http://www.w3.org/2001/XMLSchema-instance}type"
JST = timezone(timedelta(hours=9))


class FetchError(Exception):
    pass


# ---------------------------------------------------------------- 通信

_last_request = 0.0


def http_get(url: str, timeout: int = 20, retries: int = 2) -> tuple[bytes, str]:
    """URL を取得して (本文, Content-Type) を返す。テストでは FETCH を差し替える。"""
    global _last_request
    error: Exception | None = None
    for attempt in range(retries + 1):
        wait = REQUEST_INTERVAL - (time.monotonic() - _last_request)
        if wait > 0:
            time.sleep(wait)
        _last_request = time.monotonic()
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.read(), response.headers.get("Content-Type", "")
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            error = exc
            code = getattr(exc, "code", None)
            if code is not None and code < 500 and code != 429:
                break  # 404 などは再試行しても同じ
            time.sleep(2 * (attempt + 1))
    raise FetchError(f"{url} -> {error}")


FETCH = http_get


# ---------------------------------------------------------------- 文字・ISBN

_PUNCT = re.compile(r"[\s　\-‐‑–—―・=＝,，、。.:：;；/／()（）\[\]「」『』“”\"'!?！？~〜]+")


def norm(text: str | None) -> str:
    """比較用に表記ゆれを落とす(全角半角・大文字小文字・空白と記号)。"""
    return _PUNCT.sub("", unicodedata.normalize("NFKC", text or "").lower())


def tidy_name(name: str) -> str:
    """NDL の「栗原, 康」形式を「栗原康」に整える。欧文名はそのまま。"""
    name = unicodedata.normalize("NFKC", name or "").strip()
    if re.search(r"[^\x00-\x7f]", name):
        name = re.sub(r"(?<=[^\x00-\x7f]),\s*(?=[^\x00-\x7f])", "", name)
    return name.strip(" ,")


def to_isbn13(raw: str | None) -> str | None:
    s = re.sub(r"[^0-9Xx]", "", raw or "").upper()
    if len(s) == 13 and s.isdigit():
        total = sum(int(d) * (1 if i % 2 == 0 else 3) for i, d in enumerate(s[:12]))
        return s if (10 - total % 10) % 10 == int(s[12]) else None
    if len(s) == 10 and re.fullmatch(r"\d{9}[\dX]", s):
        total = sum((10 - i) * (10 if c == "X" else int(c)) for i, c in enumerate(s))
        if total % 11 != 0:
            return None
        body = "978" + s[:9]
        check = (10 - sum(int(d) * (1 if i % 2 == 0 else 3) for i, d in enumerate(body)) % 10) % 10
        return body + str(check)
    return None


# ---------------------------------------------------------------- 取得結果の解析

def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def parse_ndl(xml_bytes: bytes) -> list[dict]:
    """NDL サーチ OpenSearch の RSS を候補のリストにする。名前空間に依存しない読み方。"""
    root = ET.fromstring(xml_bytes)
    results = []
    for item in root.iter():
        if _local(item.tag) != "item":
            continue
        title = rss_title = volume = ""
        creators: list[str] = []
        authors_fallback = ""
        publisher = issued = link = ""
        isbn = None
        for child in item:
            name = _local(child.tag)
            text = (child.text or "").strip()
            if not text:
                continue
            if name == "title":
                if "purl.org/dc/elements" in child.tag:
                    title = title or text
                else:
                    rss_title = rss_title or text
            elif name == "volume":
                volume = volume or text
            elif name == "creator":
                creators.append(tidy_name(text))
            elif name == "author":
                authors_fallback = authors_fallback or text
            elif name == "publisher":
                publisher = publisher or text
            elif name == "issued":
                issued = issued or text
            elif name == "link":
                link = link or text
            elif name == "identifier":
                kind = child.attrib.get(XSI_TYPE, "")
                candidate = to_isbn13(text)
                if candidate and (kind.endswith("ISBN") or not kind):
                    isbn = isbn or candidate
        title = title or rss_title
        if not title:
            continue
        if volume and volume not in title:
            title = f"{title} {volume}"
        if not creators and authors_fallback:
            creators = [tidy_name(authors_fallback)]
        results.append({
            "title": title,
            "authors": creators,
            "publisher": publisher,
            "publishedDate": issued,
            "isbn": isbn,
            "infoLink": link,
            "coverUrl": None,
            "source": "ndl",
        })
    return results


def parse_google(data: dict) -> list[dict]:
    results = []
    for entry in data.get("items", []) or []:
        info = entry.get("volumeInfo", {}) or {}
        title = info.get("title", "")
        if not title:
            continue
        if info.get("subtitle"):
            title = f"{title} {info['subtitle']}"
        isbn = None
        for ident in info.get("industryIdentifiers", []) or []:
            converted = to_isbn13(ident.get("identifier"))
            if converted:
                isbn = converted
                if ident.get("type") == "ISBN_13":
                    break
        cover = (info.get("imageLinks") or {}).get("thumbnail")
        if cover:
            cover = cover.replace("http://", "https://").replace("&edge=curl", "")
        results.append({
            "title": title,
            "authors": [tidy_name(a) for a in info.get("authors", []) or []],
            "publisher": info.get("publisher", "") or "",
            "publishedDate": info.get("publishedDate", "") or "",
            "isbn": isbn,
            "infoLink": info.get("infoLink", "") or "",
            "coverUrl": cover,
            "source": "google",
        })
    return results


# ---------------------------------------------------------------- 検索

def _search_ndl(params: dict, debug: bool = False) -> list[dict]:
    query = dict(params, cnt=10, dpid="iss-ndl-opac")
    body, _ = FETCH(NDL_OPENSEARCH + "?" + urllib.parse.urlencode(query))
    found = parse_ndl(body)
    if debug and not found:
        print("  [debug] NDL 解析結果0件。応答の先頭:", body[:600].decode("utf-8", "replace"), file=sys.stderr)
    return found


def _search_google(query: str, debug: bool = False) -> list[dict]:
    url = GOOGLE_BOOKS + "?" + urllib.parse.urlencode({"q": query, "maxResults": 8, "printType": "books"})
    body, _ = FETCH(url)
    found = parse_google(json.loads(body.decode("utf-8")))
    if debug and not found:
        print("  [debug] Google Books 0件。応答の先頭:", body[:400].decode("utf-8", "replace"), file=sys.stderr)
    return found


def title_sim(query: str, candidate: str) -> float:
    a, b = norm(query), norm(candidate)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    short, long_ = sorted((a, b), key=len)
    if len(short) < 4:  # 「建築」のような短い語は偶然一致しやすいので上限を低くする
        return min(0.6, difflib.SequenceMatcher(None, a, b).ratio())
    if short in long_:
        return 0.9 + 0.1 * len(short) / len(long_)
    return difflib.SequenceMatcher(None, a, b).ratio()


def author_sim(query: str | None, candidates: list[str]) -> float | None:
    q = norm(query)
    if not q:
        return None
    # 「エドワード・レルフ」と「レルフ, エドワード」のような姓名の並び替えにも対応するため、語に分けて照合する
    tokens = [norm(t) for t in re.split(r"[・,，、=＝\s]+", unicodedata.normalize("NFKC", query or ""))]
    tokens = [t for t in tokens if len(t) >= 2]
    best = 0.0
    for name in candidates:
        n = norm(name)
        if not n:
            continue
        if q == n or (len(q) >= 2 and len(n) >= 2 and (q in n or n in q)):
            return 1.0
        if tokens:
            best = max(best, sum(1 for t in tokens if t in n) / len(tokens))
        best = max(best, difflib.SequenceMatcher(None, q, n).ratio())
    return best


def score(candidate: dict, title: str, author: str | None = None, publisher: str | None = None) -> float:
    t = title_sim(title, candidate["title"])
    a = author_sim(author, candidate["authors"])
    s = 0.75 * t + 0.25 * a if a is not None else 0.9 * t
    if publisher and candidate.get("publisher") and norm(publisher) in norm(candidate["publisher"]):
        s += 0.03
    if candidate.get("isbn"):
        s += 0.005  # ISBN があれば書影も引けるので僅差なら優先
    return min(s, 1.0)


def ndl_queries(title: str, author: str | None) -> list[tuple[str, str | None]]:
    """NDL サーチに投げる (書名, 著者) の候補を、厳しい順に並べる。"""
    base = re.sub(r"\s+", " ", unicodedata.normalize("NFKC", title or "")).strip()
    no_volume = re.sub(r"\s*(上|中|下|[0-9]+|第[0-9一二三四五六七八九十]+巻|[ⅠⅡⅢ]+)$", "", base).strip()
    head = re.split(r"[ :：]", no_volume)[0]
    queries: list[tuple[str, str | None]] = []
    for pair in ((base, author), (base, None), (no_volume, author), (head, author)):
        if pair[0] and pair not in queries:
            queries.append(pair)
    return queries


def search_candidates(title: str, author: str | None = None, publisher: str | None = None,
                      isbn: str | None = None, debug: bool = False) -> tuple[list[dict], list[str]]:
    """候補を点数つきで返す。(候補(高い順), 通信エラーの一覧)"""
    cands: list[dict] = []
    errors: list[str] = []

    def attempt(label: str, func):
        try:
            return func()
        except (FetchError, ET.ParseError, ValueError) as exc:
            errors.append(f"{label}: {exc}")
            return []

    wanted = to_isbn13(isbn) if isbn else None
    if isbn and not wanted:
        errors.append(f"ISBN の形式が正しくありません: {isbn}")
        return [], errors
    if wanted:
        cands += attempt("NDL", lambda: _search_ndl({"isbn": wanted}, debug))
        cands = [c for c in cands if c.get("isbn") == wanted] or cands
        if not cands:
            cands += attempt("Google", lambda: _search_google(f"isbn:{wanted}", debug))
        for c in cands:
            c["score"] = 1.0 if c.get("isbn") == wanted else 0.5
    else:
        # 見つからなければ、著者なし・巻数なし・書名の頭だけ、の順にゆるめて探す
        for query_title, query_author in ndl_queries(title, author):
            params = {"title": query_title}
            if query_author:
                params["creator"] = query_author
            before = len(errors)
            found = attempt("NDL", lambda p=params: _search_ndl(p, debug))
            if found:
                cands += found
                break
            if len(errors) > before:
                break  # 通信そのものに失敗したときは、言い換えても同じなので打ち切る
        for c in cands:
            c["score"] = score(c, title, author, publisher)
        if max((c["score"] for c in cands), default=0.0) < MATCH_THRESHOLD:
            q = f"intitle:{title}" + (f" inauthor:{author}" if author else "")
            extra = attempt("Google", lambda: _search_google(q, debug))
            for c in extra:
                c["score"] = score(c, title, author, publisher)
            cands += extra
    cands.sort(key=lambda c: c["score"], reverse=True)
    return cands, errors


# ---------------------------------------------------------------- 書影

def download_cover(urls: list[str], dest_stem: Path) -> str | None:
    """候補の URL を順に試し、最初に画像として使えたものを保存する。サイトからの相対パスを返す。"""
    for url in urls:
        if not url:
            continue
        try:
            data, ctype = FETCH(url)
        except FetchError:
            continue
        is_jpeg = data[:3] == b"\xff\xd8\xff"
        is_png = data[:8] == b"\x89PNG\r\n\x1a\n"
        if len(data) < 2048 or not (is_jpeg or is_png or ctype.lower().startswith("image")):
            continue
        suffix = ".png" if is_png else ".jpg"
        dest = dest_stem.with_suffix(suffix)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        return dest.relative_to(ROOT).as_posix()
    return None


# ---------------------------------------------------------------- データ入出力

def load_books(path: Path | None = None) -> dict:
    path = path or BOOKS_JSON  # 呼び出し時に解決する(テストで差し替えられるように)
    if not path.exists():
        return {"version": 1, "books": []}
    return json.loads(path.read_text(encoding="utf-8"))


def save_books(data: dict, path: Path | None = None) -> None:
    path = path or BOOKS_JSON
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def now_iso() -> str:
    return datetime.now(JST).isoformat(timespec="seconds")


def _slug(title: str, author: str | None) -> str:
    return "t-" + hashlib.sha1((norm(title) + "|" + norm(author)).encode("utf-8")).hexdigest()[:10]


def find_existing(books: list[dict], isbn: str | None, title: str, author: str | None) -> dict | None:
    key = norm(title) + "|" + norm(author)
    for book in books:
        if isbn and book.get("isbn") == isbn:
            return book
        first_author = (book.get("authors") or [""])[0]
        if norm(book.get("title")) + "|" + norm(first_author) == key:
            return book
        rec = book.get("recognized") or {}
        if rec and norm(rec.get("title")) + "|" + norm(rec.get("author")) == key:
            return book
    return None


# ---------------------------------------------------------------- 1冊の取り込み

def add_one(data: dict, entry: dict, dry_run: bool = False, debug: bool = False) -> str:
    """結果を 'added' / 'review' / 'skipped' / 'network' のいずれかで返す。"""
    title = (entry.get("title") or "").strip()
    author = (entry.get("author") or "").strip() or None
    publisher = (entry.get("publisher") or "").strip() or None
    isbn_in = to_isbn13(entry.get("isbn")) if entry.get("isbn") else None
    if not title and not isbn_in:
        print("  - 書名もISBNも無い項目をスキップしました")
        return "skipped"

    books = data["books"]
    existing = find_existing(books, isbn_in, title, author)
    if existing and existing.get("status") == "matched":
        print(f"  = 既に登録済み: {existing['title']}")
        return "skipped"

    cands, errors = search_candidates(title, author, publisher, isbn_in, debug)
    if not cands and errors:
        print(f"  ! 通信できませんでした（{title}）: {errors[0]}")
        return "network"

    best = cands[0] if cands else None
    matched = bool(best and best["score"] >= MATCH_THRESHOLD)
    if matched:
        if existing is None:
            existing = find_existing(books, best.get("isbn"), best["title"], (best["authors"] or [""])[0])
        if existing and existing.get("status") == "matched":
            print(f"  = 既に登録済み: {existing['title']}")
            return "skipped"
        book_id = best["isbn"] or _slug(best["title"], (best["authors"] or [""])[0])
        cover = None
        if not dry_run:
            urls = []
            if best.get("isbn"):
                urls.append(NDL_THUMBNAIL.format(isbn=best["isbn"]))
            urls.append(best.get("coverUrl"))
            cover = download_cover(urls, COVER_DIR / book_id)
        record = {
            "id": book_id,
            "title": best["title"],
            "authors": best["authors"],
            "publisher": best["publisher"],
            "publishedDate": best["publishedDate"],
            "isbn": best["isbn"] or "",
            "cover": cover,
            "infoLink": best["infoLink"],
            "source": best["source"],
            "status": "matched",
            "matchScore": round(best["score"], 3),
            "addedAt": (existing or {}).get("addedAt") or now_iso(),
            "recognized": {"title": title, "author": author or ""},
        }
        label = f"  ✓ {record['title']} / {'、'.join(record['authors'])} / {record['publisher']} (ISBN {record['isbn'] or '-'}, {record['matchScore']})"
        label += "" if cover or dry_run else "  ※書影なし"
        print(label)
        outcome = "added"
    else:
        book_id = _slug(title, author)
        record = {
            "id": book_id,
            "title": title,
            "authors": [author] if author else [],
            "publisher": publisher or "",
            "publishedDate": "",
            "isbn": "",
            "cover": None,
            "infoLink": "",
            "source": "recognized",
            "status": "needs_review",
            "matchScore": round(best["score"], 3) if best else 0.0,
            "addedAt": (existing or {}).get("addedAt") or now_iso(),
            "recognized": {"title": title, "author": author or ""},
            "candidates": [
                {"title": c["title"], "authors": c["authors"], "publisher": c["publisher"],
                 "isbn": c["isbn"] or "", "score": round(c["score"], 3)}
                for c in cands[:3]
            ],
        }
        print(f"  ? 要確認: {title} / {author or '-'}（最有力: {best['title'] if best else 'なし'}, {record['matchScore']}）")
        outcome = "review"

    if not dry_run:
        if existing and existing in books:
            books[books.index(existing)] = record
        else:
            books.append(record)
    return outcome


# ---------------------------------------------------------------- コマンド

def cmd_add_batch(args) -> int:
    entries = json.loads(Path(args.file).read_text(encoding="utf-8"))
    if isinstance(entries, dict):
        entries = entries.get("books") or entries.get("candidates") or []
    return run_entries(entries, args)


def cmd_add(args) -> int:
    return run_entries([{"title": args.title or "", "author": args.author, "publisher": args.publisher,
                         "isbn": args.isbn}], args)


def run_entries(entries: list[dict], args) -> int:
    data = load_books()
    counts = {"added": 0, "review": 0, "skipped": 0, "network": 0}
    for entry in entries:
        counts[add_one(data, entry, args.dry_run, args.debug)] += 1
    if not args.dry_run and (counts["added"] or counts["review"]):
        save_books(data)
    print(f"\n追加 {counts['added']} / 要確認 {counts['review']} / 既存・スキップ {counts['skipped']} / 通信失敗 {counts['network']}")
    if counts["network"] and not (counts["added"] or counts["review"]):
        return 3
    return 0


def cmd_review(args) -> int:
    data = load_books()
    pending = [b for b in data["books"] if b.get("status") == "needs_review"]
    if not pending:
        print("要確認はありません")
        return 0
    for book in pending:
        print(f"[{book['id']}] {book['title']} / {'、'.join(book.get('authors') or []) or '-'}")
        for i, c in enumerate(book.get("candidates") or [], 1):
            print(f"    {i}. {c['title']} / {'、'.join(c['authors'])} / {c['publisher']} / ISBN {c['isbn'] or '-'} ({c['score']})")
    return 0


def cmd_resolve(args) -> int:
    data = load_books()
    book = next((b for b in data["books"] if b["id"] == args.id), None)
    if not book:
        print(f"ID が見つかりません: {args.id}")
        return 1
    isbn = args.isbn
    if args.pick:
        candidates = book.get("candidates") or []
        if not 1 <= args.pick <= len(candidates) or not candidates[args.pick - 1].get("isbn"):
            print("その番号の候補が無い、または ISBN がありません。--isbn で指定してください")
            return 1
        isbn = candidates[args.pick - 1]["isbn"]
    if not isbn:
        print("--isbn か --pick のどちらかを指定してください")
        return 1
    recognized = book.get("recognized") or {"title": book["title"], "author": ""}
    probe = {"data": {"version": data.get("version", 1), "books": [b for b in data["books"] if b is not book]}}
    outcome = add_one(probe["data"], {"title": recognized["title"], "author": recognized.get("author"), "isbn": isbn},
                      args.dry_run, args.debug)
    if outcome != "added":
        return 1
    new_record = probe["data"]["books"][-1]
    new_record["addedAt"] = book.get("addedAt") or new_record["addedAt"]
    if not args.dry_run:
        save_books({"version": data.get("version", 1), "books": probe["data"]["books"]})
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="蔵書アーカイブの書誌取得")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--dry-run", action="store_true", help="ファイルを書き換えない")
    common.add_argument("--debug", action="store_true", help="取得結果の先頭を表示する")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("add-batch", parents=[common], help="候補の JSON をまとめて取り込む")
    p.add_argument("file")
    p.set_defaults(func=cmd_add_batch)

    p = sub.add_parser("add", parents=[common], help="1冊を取り込む")
    p.add_argument("--title")
    p.add_argument("--author")
    p.add_argument("--publisher")
    p.add_argument("--isbn")
    p.set_defaults(func=cmd_add)

    p = sub.add_parser("review", parents=[common], help="要確認の一覧")
    p.set_defaults(func=cmd_review)

    p = sub.add_parser("resolve", parents=[common], help="要確認を確定する")
    p.add_argument("id")
    p.add_argument("--isbn")
    p.add_argument("--pick", type=int)
    p.set_defaults(func=cmd_resolve)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "add" and not (args.title or args.isbn):
        print("--title か --isbn を指定してください")
        return 1
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
