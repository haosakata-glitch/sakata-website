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

  // ISBN が無い本のために、Google Books をタイトルで検索して書影を探す。
  // 対象は、ISBN-10 を作れない本（ISBN が無い、または 979 始まり）。本の記録に coverSearch: true を付ければ ISBN がある本も、false を付ければ検索しない。
  // ブラウザが直接検索する（GitHub Actions からは Google Books が 429 になるため）。
  // 書名が一致しない結果は使わない（別の本の書影を出すより、書名のタイルのほうがよい）。
  function normText(value) {
    return String(value == null ? "" : value).normalize("NFKC").toLowerCase().replace(/[\s　]+/g, "");
  }

  // 「悲しき熱帯 上」→「悲しき熱帯」、「大杉栄伝　永遠のアナキズム」→「大杉栄伝」（最初の空白・コロンまで）
  function mainTitle(title) {
    return String(title || "").split(/[\s　:：]/)[0];
  }

  function searchQuery(book) {
    var q = "intitle:" + mainTitle(book.title);
    var author = authorsOf(book)[0];
    if (author) {
      // 「Lévi-Strauss, Claude, 1908-2009」のような NDL の形は検索に向かないので、日本語の著者名のときだけ使う
      var name = String(author).replace(/,?\s*\d{4}-(\d{4})?$/, "");
      if (/[\u3040-\u30ff\u3400-\u9fff]/.test(name)) {
        q += "+inauthor:" + name;
      }
    }
    return "https://www.googleapis.com/books/v1/volumes?q=" + encodeURIComponent(q).replace(/%2B/g, "+") + "&maxResults=10&printType=books";
  }

  // 検索結果から、書名が一致し、書影のあるものを選ぶ。出版社・著者が合うものを優先する。
  function pickCover(book, items) {
    var wanted = normText(mainTitle(book.title));
    var publisher = normText(book.publisher);
    var authors = authorsOf(book).map(normText).filter(Boolean);
    var best = null;
    var bestScore = -1;
    (Array.isArray(items) ? items : []).forEach(function (item) {
      var info = item && item.volumeInfo;
      var links = info && info.imageLinks;
      var url = links && (links.thumbnail || links.smallThumbnail);
      if (!url || !wanted || normText(info.title).indexOf(wanted) === -1) {
        return;
      }
      var score = 1;
      if (publisher && normText(info.publisher).indexOf(publisher) !== -1) { score += 2; }
      var infoAuthors = normText((info.authors || []).join(" "));
      if (authors.some(function (a) { return a.length >= 2 && infoAuthors.indexOf(a.split(",")[0]) !== -1; })) { score += 2; }
      if (score > bestScore) {
        best = url;
        bestScore = score;
      }
    });
    if (!best) {
      return "";
    }
    // Google Books の画像: https にし、紙のめくれ（edge=curl）の加工を外す
    return String(best).replace(/^http:/, "https:").replace(/&edge=curl/, "");
  }

  function wantsSearch(book) {
    if (book.coverSearch === true) { return true; }
    if (book.coverSearch === false) { return false; }
    return !isbn10Of(book.isbn);
  }

  var searchCache = {};

  // 本ごとに1回だけ検索する（一覧は検索・並び替えのたびに作り直されるため）。見つからなければ ""。
  function searchCover(book) {
    var key = String(book.id || book.title);
    if (!searchCache[key]) {
      searchCache[key] = fetch(searchQuery(book)).then(function (response) {
        if (!response.ok) {
          throw new Error("HTTP " + response.status);
        }
        return response.json();
      }).then(function (data) {
        return safeHttps(pickCover(book, data && data.items));
      }).catch(function (error) {
        console.error(error);
        return "";
      });
    }
    return searchCache[key];
  }

  function titleTile(title) {
    var tile = document.createElement("div");
    tile.className = "cover noimg";
    tile.textContent = title || "";
    return tile;
  }

  // 書影の要素。候補を順に試し、最初に表示できたものを使う。どれも駄目なら書名のタイルにする。
  // ISBN-10 を作れない本（coverSearch: true の本も）は、候補を使い切ったあと、Google Books のタイトル検索を最後に試す。
  function coverFor(book, lazy) {
    var covers = coverCandidates(book);
    var searchable = wantsSearch(book);
    if (!covers.length && !searchable) {
      return titleTile(book.title);
    }
    var media = document.createElement("img");
    media.className = "cover";
    media.alt = "";
    media.referrerPolicy = "no-referrer";
    if (lazy) { media.loading = "lazy"; }
    var next = 0;
    var searched = false;
    var giveUp = function () {
      if (media.parentNode) {
        media.parentNode.replaceChild(titleTile(book.title), media);
      }
    };
    var tryNext = function () {
      if (next < covers.length) {
        media.src = covers[next++];
      } else if (searchable && !searched) {
        searched = true;
        searchCover(book).then(function (url) {
          if (url) {
            media.src = url;  // これが表示できなければ、error で giveUp になる
          } else {
            giveUp();
          }
        });
      } else {
        giveUp();
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

  // 分類の一覧（bookshelf/categories.json）。読めなくても本棚は表示できるよう、失敗したら「分類なし」として扱う。
  //   fields / kinds: 分野と種類の名前（表示の並び順の目安）
  //   publisherAliases: 出版社の表記ゆれ・レーベル名 → まとめる名前
  //   books: 本のID → { field, kind }（title は人が読むためのメモで、画面では使わない）
  function emptyCategories() {
    return { fields: [], kinds: [], publisherAliases: {}, books: {} };
  }

  function loadCategories() {
    return getJson("bookshelf/categories.json").then(function (data) {
      var cats = emptyCategories();
      if (data && typeof data === "object") {
        cats.fields = Array.isArray(data.fields) ? data.fields.filter(function (x) { return typeof x === "string"; }) : [];
        cats.kinds = Array.isArray(data.kinds) ? data.kinds.filter(function (x) { return typeof x === "string"; }) : [];
        if (data.publisherAliases && typeof data.publisherAliases === "object") { cats.publisherAliases = data.publisherAliases; }
        if (data.books && typeof data.books === "object") { cats.books = data.books; }
      }
      return cats;
    }).catch(function (error) {
      console.error(error);
      return emptyCategories();
    });
  }

  function keyOf(value) {
    return String(value == null ? "" : value).normalize("NFKC").toLowerCase().replace(/[\s　]+/g, "");
  }

  // 出版社の名前。表記ゆれ（新潮文庫→新潮社 など）は publisherAliases でまとめる。出版社が空なら ""。
  function publisherLabel(book, cats) {
    var name = String((book && book.publisher) || "").trim();
    if (!name) { return ""; }
    var aliases = (cats && cats.publisherAliases) || {};
    var key = keyOf(name);
    for (var from in aliases) {
      if (Object.prototype.hasOwnProperty.call(aliases, from) && keyOf(from) === key && typeof aliases[from] === "string") {
        return aliases[from];
      }
    }
    return name;
  }

  var OTHER_LABEL = "Other";
  var UNSORTED_LABEL = "未分類";

  // 本を分ける。mode: "all" | "field" | "kind" | "publisher"
  // 戻り値: [{ key, label, books, tail }]（tail が true のグループは、並びの最後に置く Other・未分類）
  // 出版社は、1冊しかない出版社をまとめて Other にする。1冊かどうかは universe（検索で絞る前の全体）で数えるので、
  // 検索で絞っても Other の中身は変わらない。
  function groupBooks(list, mode, cats, universe) {
    cats = cats || emptyCategories();
    universe = universe || list;
    var labelOf;
    var order = [];
    if (mode === "field" || mode === "kind") {
      var attr = mode;
      order = mode === "field" ? cats.fields : cats.kinds;
      labelOf = function (book) {
        var entry = Object.prototype.hasOwnProperty.call(cats.books, book.id) ? cats.books[book.id] : null;
        var value = entry && typeof entry[attr] === "string" ? entry[attr] : "";
        return value || UNSORTED_LABEL;
      };
    } else if (mode === "publisher") {
      var counts = {};
      universe.forEach(function (book) {
        var name = publisherLabel(book, cats);
        if (name) { counts[name] = (counts[name] || 0) + 1; }
      });
      labelOf = function (book) {
        var name = publisherLabel(book, cats);
        return name && counts[name] > 1 ? name : OTHER_LABEL;
      };
    } else {
      return [{ key: "all", label: "ALL", books: list.slice(), tail: false }];
    }

    var byLabel = {};
    var groups = [];
    list.forEach(function (book) {
      var label = labelOf(book);
      if (!Object.prototype.hasOwnProperty.call(byLabel, label)) {
        byLabel[label] = { key: "g" + groups.length, label: label, books: [], tail: label === OTHER_LABEL || label === UNSORTED_LABEL || label === "その他" };
        groups.push(byLabel[label]);
      }
      byLabel[label].books.push(book);
    });
    // 冊数の多い順（同数なら、分野・種類は名簿の順、出版社は五十音順）。Other・未分類・その他は最後。
    groups.sort(function (a, b) {
      if (a.tail !== b.tail) { return a.tail ? 1 : -1; }
      if (b.books.length !== a.books.length) { return b.books.length - a.books.length; }
      var ia = order.indexOf(a.label), ib = order.indexOf(b.label);
      if (ia !== -1 || ib !== -1) { return (ia === -1 ? 999 : ia) - (ib === -1 ? 999 : ib); }
      return a.label.localeCompare(b.label, "ja");
    });
    groups.forEach(function (group, index) { group.key = "g" + index; });
    return groups;
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
    wantsSearch: wantsSearch,
    searchQuery: searchQuery,
    pickCover: pickCover,
    coverFor: coverFor,
    loadBooks: loadBooks,
    loadNotes: loadNotes,
    loadCategories: loadCategories,
    publisherLabel: publisherLabel,
    groupBooks: groupBooks,
    noteTarget: noteTarget,
    isListed: isListed
  };
})(window);
