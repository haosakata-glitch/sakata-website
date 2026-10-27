/* 本棚のページ（books.html / book-note.html）で共通に使う小さな関数。
   データ由来の文字列は、想定した形のものだけを通し、画面には textContent で入れる（HTML としては解釈しない）。 */
(function (global) {
  "use strict";

  var NOTE_PAGE = "book-note.html";

  // サイト内のパス（自分で撮った書影など）。英数字・_ - . / だけ。
  function safeLocal(path) {
    return typeof path === "string" && /^[\w./-]+$/.test(path) && path.indexOf("..") === -1 ? path : "";
  }

  function safeHttps(url) {
    return typeof url === "string" && /^https:\/\//.test(url) ? url : "";
  }

  function authorsOf(book) {
    return Array.isArray(book.authors) ? book.authors.filter(Boolean) : [];
  }

  // ISBN-13（978 始まり）→ ISBN-10。Amazon の画像の URL は ISBN-10 で決まる。979 始まりには ISBN-10 が無い。
  function isbn10Of(isbn13) {
    var s = String(isbn13 || "");
    if (!/^978\d{10}$/.test(s)) {
      return "";
    }
    var body = s.slice(3, 12);
    var sum = 0;
    for (var i = 0; i < 9; i++) {
      sum += Number(body.charAt(i)) * (10 - i);
    }
    var check = (11 - (sum % 11)) % 11;
    return body + (check === 10 ? "X" : String(check));
  }

  // 書影の候補を、試す順に並べる。
  //  1. サイト内の画像（自分で撮った写真など）
  //  2. 記録してある提供元の URL（Google Books の検索結果が教えてくれたもの）
  //  3. ISBN から作る URL: Amazon → 国立国会図書館サーチ → Google Books
  // 他者の書影は、コピーを保存せず、提供元の画像をそのまま表示する（ブラウザが直接取りに行く）。
  // Amazon の画像は公式な API ではなく、将来使えなくなる可能性がある。その場合は次の候補、最後は書名のタイルになる。
  function coverCandidates(book) {
    var list = [];
    var local = safeLocal(book.cover);
    var stored = safeHttps(book.coverUrl);
    var isbn = /^\d{13}$/.test(String(book.isbn || "")) ? String(book.isbn) : "";
    var isbn10 = isbn10Of(isbn);
    if (local) { list.push(local); }
    if (stored) { list.push(stored); }
    if (isbn10) {
      list.push("https://images-na.ssl-images-amazon.com/images/P/" + isbn10 + ".09.LZZZZZZZ.jpg");
      list.push("https://m.media-amazon.com/images/P/" + isbn10 + ".09.LZZZZZZZ.jpg");
    }
    if (isbn) {
      list.push("https://ndlsearch.ndl.go.jp/thumbnail/" + isbn + ".jpg");
      list.push("https://books.google.com/books/content?vid=ISBN" + isbn + "&printsec=frontcover&img=1&zoom=1");
    }
    return list;
  }

  function titleTile(title) {
    var tile = document.createElement("div");
    tile.className = "cover noimg";
    tile.textContent = title || "";
    return tile;
  }

  // 書影の要素。候補を順に試し、最初に表示できたものを使う。どれも駄目なら書名のタイルにする。
  function coverFor(book, lazy) {
    var covers = coverCandidates(book);
    if (!covers.length) {
      return titleTile(book.title);
    }
    var media = document.createElement("img");
    media.className = "cover";
    media.alt = "";
    media.referrerPolicy = "no-referrer";
    if (lazy) { media.loading = "lazy"; }
    var next = 0;
    var tryNext = function () {
      if (next < covers.length) {
        media.src = covers[next++];
      } else if (media.parentNode) {
        media.parentNode.replaceChild(titleTile(book.title), media);
      }
    };
    media.addEventListener("error", tryNext);
    media.addEventListener("load", function () {
      if (media.naturalWidth < 20) {
        tryNext();  // 「画像なし」の1ピクセルの画像などは使わない
      }
    });
    tryNext();
    return media;
  }

  function getJson(path) {
    return fetch(path, { cache: "no-cache" }).then(function (response) {
      if (!response.ok) {
        throw new Error("HTTP " + response.status + " " + path);
      }
      return response.json();
    });
  }

  function loadBooks() {
    return getJson("bookshelf/books.json").then(function (data) {
      return Array.isArray(data) ? data : (data && data.books) || [];
    });
  }

  // 感想の一覧（bookshelf/notes.json）。読めなくても本棚は表示できるよう、失敗したら「感想なし」として扱う。
  function loadNotes() {
    return getJson("bookshelf/notes.json").then(function (data) {
      var notes = data && typeof data === "object" && data.notes && typeof data.notes === "object" ? data.notes : {};
      return notes;
    }).catch(function (error) {
      console.error(error);
      return {};
    });
  }

  function isListed(book) {
    return book && book.status !== "needs_review" && book.status !== "ignored";
  }

  // その本の感想があれば { href, file } を返す。無ければ null（その本は押せない）。
  //   {"file": "9784....txt"} … bookshelf/notes/ の文章を book-note.html で読む
  //   {"page": "text-02.html"} … すでにあるサイト内のページ（TEXT の文章など）へ飛ぶ
  function noteTarget(book, notes) {
    if (!isListed(book) || typeof book.id !== "string" || !/^[\w-]+$/.test(book.id)) {
      return null;
    }
    var entry = Object.prototype.hasOwnProperty.call(notes, book.id) ? notes[book.id] : null;
    if (!entry || typeof entry !== "object") {
      return null;
    }
    if (typeof entry.page === "string" && /^[\w-]+\.html$/.test(entry.page)) {
      return { href: entry.page, file: "", updated: "" };
    }
    if (typeof entry.file === "string" && /^[\w-]+\.txt$/.test(entry.file)) {
      var updated = typeof entry.updated === "string" && /^\d{4}-\d{2}-\d{2}$/.test(entry.updated) ? entry.updated : "";
      return { href: NOTE_PAGE + "?id=" + encodeURIComponent(book.id), file: entry.file, updated: updated };
    }
    return null;
  }

  global.Shelf = {
    safeLocal: safeLocal,
    safeHttps: safeHttps,
    authorsOf: authorsOf,
    isbn10Of: isbn10Of,
    coverCandidates: coverCandidates,
    coverFor: coverFor,
    loadBooks: loadBooks,
    loadNotes: loadNotes,
    noteTarget: noteTarget,
    isListed: isListed
  };
})(window);
