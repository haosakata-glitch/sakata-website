"""book_lookup.py のテスト。通信は模擬データに差し替えて行う。

注意: NDL / Google Books の応答は、公開されている仕様にもとづいて作った模擬データで、
実サーバーの応答そのものではない。実機での初回確認は `add --title ... --debug` で行う。
実行: python3 scripts/test_book_lookup.py
"""
import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import book_lookup as bl  # noqa: E402

def _isbn13(body12):
    total = sum(int(d) * (1 if i % 2 == 0 else 3) for i, d in enumerate(body12))
    return body12 + str((10 - total % 10) % 10)


ISBN_A = _isbn13("978404400000")
ISBN_B = _isbn13("978400000000")

NDL_XML = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:dc="http://purl.org/dc/elements/1.1/"
     xmlns:dcterms="http://purl.org/dc/terms/"
     xmlns:dcndl="http://ndl.go.jp/dcndl/terms/"
     xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
  <channel>
    <title>検索結果</title>
    <item>
      <title>大杉栄伝 : 永遠のアナキズム</title>
      <link>https://ndlsearch.ndl.go.jp/books/R100000002-I000000000001</link>
      <dc:title>大杉栄伝 : 永遠のアナキズム</dc:title>
      <dc:creator>栗原, 康</dc:creator>
      <dc:publisher>KADOKAWA</dc:publisher>
      <dcterms:issued xsi:type="dcterms:W3CDTF">2021.7</dcterms:issued>
      <dc:identifier xsi:type="dcndl:ISBN">__ISBN_A__</dc:identifier>
    </item>
    <item>
      <title>建築</title>
      <dc:title>建築</dc:title>
      <dc:creator>山田, 太郎</dc:creator>
      <dc:publisher>どこか出版</dc:publisher>
      <dc:identifier xsi:type="dcndl:ISBN">__ISBN_B__</dc:identifier>
    </item>
  </channel>
</rss>""".replace("__ISBN_A__", ISBN_A).replace("__ISBN_B__", ISBN_B).encode("utf-8")

EMPTY_XML = b'<?xml version="1.0"?><rss version="2.0"><channel><title>x</title></channel></rss>'

GOOGLE_JSON = json.dumps({"items": [{
    "volumeInfo": {
        "title": "Some English Book", "authors": ["Smith, John"], "publisher": "Pub",
        "publishedDate": "2019-05-01",
        "industryIdentifiers": [{"type": "ISBN_10", "identifier": "0306406152"},
                                {"type": "ISBN_13", "identifier": "9780306406157"}],
        "imageLinks": {"thumbnail": "http://books.google.com/c?id=1&edge=curl"},
        "infoLink": "https://books.google.com/books?id=1",
    }}]}).encode("utf-8")

JPEG = b"\xff\xd8\xff" + b"0" * 4000


def valid_isbn13(body12: str) -> str:
    return _isbn13(body12)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.saved = (bl.ROOT, bl.BOOKS_JSON, bl.COVER_DIR, bl.FETCH)
        bl.ROOT, bl.BOOKS_JSON, bl.COVER_DIR = root, root / "bookshelf" / "books.json", root / "bookshelf" / "covers"
        self.requests = []
        assert str(bl.BOOKS_JSON).startswith(self.tmp.name), "テストが本番データを指しています"

    def tearDown(self):
        bl.ROOT, bl.BOOKS_JSON, bl.COVER_DIR, bl.FETCH = self.saved
        self.tmp.cleanup()

    def route(self, table):
        def fake(url, *a, **k):
            self.requests.append(url)
            for key, value in table.items():
                if key in url:
                    if isinstance(value, Exception):
                        raise value
                    return value
            raise bl.FetchError("no route: " + url)
        bl.FETCH = fake


class UtilTests(unittest.TestCase):
    def test_isbn(self):
        self.assertEqual(bl.to_isbn13("0-306-40615-2"), "9780306406157")
        self.assertEqual(bl.to_isbn13("978-4-04-400000-4"), valid_isbn13("978404400000"))
        self.assertIsNone(bl.to_isbn13("9784044000005"))  # チェックディジット不正
        self.assertIsNone(bl.to_isbn13("abc"))

    def test_names(self):
        self.assertEqual(bl.tidy_name("栗原, 康"), "栗原康")
        self.assertEqual(bl.tidy_name("Smith, John"), "Smith, John")

    def test_scoring(self):
        full = {"title": "大杉栄伝 : 永遠のアナキズム", "authors": ["栗原康"], "publisher": "", "isbn": "x"}
        self.assertGreaterEqual(bl.score(full, "大杉栄伝", "栗原康"), bl.MATCH_THRESHOLD)
        self.assertLess(bl.score(full, "大杉栄伝", "全然ちがう人"), bl.MATCH_THRESHOLD)
        short = {"title": "建築", "authors": ["山田太郎"], "publisher": "", "isbn": ""}
        self.assertLess(bl.score({"title": "建築論", "authors": [], "publisher": "", "isbn": ""}, "建築"), bl.MATCH_THRESHOLD)
        self.assertGreaterEqual(bl.score(short, "建築", "山田太郎"), bl.MATCH_THRESHOLD)


class ParseTests(unittest.TestCase):
    def test_ndl(self):
        items = bl.parse_ndl(NDL_XML)
        self.assertEqual(len(items), 2)
        first = items[0]
        self.assertEqual(first["title"], "大杉栄伝 : 永遠のアナキズム")
        self.assertEqual(first["authors"], ["栗原康"])
        self.assertEqual(first["publisher"], "KADOKAWA")
        self.assertEqual(first["publishedDate"], "2021.7")
        self.assertEqual(first["isbn"], valid_isbn13("978404400000"))

    def test_ndl_empty(self):
        self.assertEqual(bl.parse_ndl(EMPTY_XML), [])

    def test_google(self):
        item = bl.parse_google(json.loads(GOOGLE_JSON))[0]
        self.assertEqual(item["isbn"], "9780306406157")
        self.assertTrue(item["coverUrl"].startswith("https://"))
        self.assertNotIn("edge=curl", item["coverUrl"])


class AddTests(Base):
    def test_matched_with_cover(self):
        isbn = valid_isbn13("978404400000")
        self.route({"ndlsearch.ndl.go.jp/api/opensearch": (NDL_XML, "application/xml"),
                    f"thumbnail/{isbn}.jpg": (JPEG, "image/jpeg")})
        data = {"version": 1, "books": []}
        with contextlib.redirect_stdout(io.StringIO()):
            outcome = bl.add_one(data, {"title": "大杉栄伝", "author": "栗原康"})
        self.assertEqual(outcome, "added")
        book = data["books"][0]
        self.assertEqual(book["status"], "matched")
        self.assertEqual(book["id"], isbn)
        self.assertEqual(book["cover"], f"bookshelf/covers/{isbn}.jpg")
        self.assertTrue((bl.ROOT / book["cover"]).exists())
        self.assertEqual(book["recognized"]["title"], "大杉栄伝")
        self.assertFalse(any("googleapis" in u for u in self.requests))  # 十分一致ならNDLだけで済む

    def test_duplicate_is_skipped(self):
        isbn = valid_isbn13("978404400000")
        self.route({"api/opensearch": (NDL_XML, "application/xml"), f"thumbnail/{isbn}.jpg": (JPEG, "image/jpeg")})
        data = {"version": 1, "books": []}
        with contextlib.redirect_stdout(io.StringIO()):
            bl.add_one(data, {"title": "大杉栄伝", "author": "栗原康"})
            again = bl.add_one(data, {"title": "大杉栄伝 永遠のアナキズム", "author": "栗原康"})
        self.assertEqual(again, "skipped")
        self.assertEqual(len(data["books"]), 1)

    def test_falls_back_to_google_and_cover(self):
        self.route({"api/opensearch": (EMPTY_XML, "application/xml"),
                    "googleapis.com/books": (GOOGLE_JSON, "application/json"),
                    "thumbnail/9780306406157.jpg": bl.FetchError("404"),
                    "books.google.com/c": (JPEG, "image/jpeg")})
        data = {"version": 1, "books": []}
        with contextlib.redirect_stdout(io.StringIO()):
            outcome = bl.add_one(data, {"title": "Some English Book", "author": "John Smith"})
        self.assertEqual(outcome, "added")
        self.assertEqual(data["books"][0]["source"], "google")
        self.assertTrue(data["books"][0]["cover"])

    def test_low_score_goes_to_review(self):
        self.route({"api/opensearch": (NDL_XML, "application/xml"),
                    "googleapis.com/books": (json.dumps({}).encode(), "application/json")})
        data = {"version": 1, "books": []}
        with contextlib.redirect_stdout(io.StringIO()):
            outcome = bl.add_one(data, {"title": "まったく別の本のタイトル", "author": "誰か"})
        self.assertEqual(outcome, "review")
        book = data["books"][0]
        self.assertEqual(book["status"], "needs_review")
        self.assertIsNone(book["cover"])
        self.assertTrue(book["candidates"])

    def test_network_down_adds_nothing(self):
        self.route({"api/opensearch": bl.FetchError("down"), "googleapis": bl.FetchError("down")})
        data = {"version": 1, "books": []}
        with contextlib.redirect_stdout(io.StringIO()):
            outcome = bl.add_one(data, {"title": "大杉栄伝", "author": "栗原康"})
        self.assertEqual(outcome, "network")
        self.assertEqual(data["books"], [])

    def test_batch_cli_and_resolve(self):
        isbn = valid_isbn13("978404400000")
        self.route({"api/opensearch": (NDL_XML, "application/xml"),
                    "googleapis.com/books": (json.dumps({}).encode(), "application/json"),
                    f"thumbnail/{isbn}.jpg": (JPEG, "image/jpeg")})
        candidates = Path(self.tmp.name) / "c.json"
        candidates.write_text(json.dumps([{"title": "大杉栄傳", "author": "栗原"}]), encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(bl.main(["add-batch", str(candidates)]), 0)
        saved = bl.load_books()
        self.assertEqual(len(saved["books"]), 1)
        book_id = saved["books"][0]["id"]
        if saved["books"][0]["status"] == "needs_review":  # 表記ゆれで要確認になった場合を確定する
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(bl.main(["resolve", book_id, "--isbn", isbn]), 0)
            saved = bl.load_books()
            self.assertEqual(saved["books"][0]["status"], "matched")
            self.assertEqual(saved["books"][0]["id"], isbn)
        self.assertEqual(len(saved["books"]), 1)

    def test_dry_run_writes_nothing(self):
        self.route({"api/opensearch": (NDL_XML, "application/xml")})
        with contextlib.redirect_stdout(io.StringIO()):
            bl.main(["add", "--title", "大杉栄伝", "--author", "栗原康", "--dry-run"])
        self.assertFalse(bl.BOOKS_JSON.exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
