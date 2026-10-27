# sakata-website 作業メモ（Claude 用）

個人サイト（GitHub Pages）。本人が理解して更新を続けられる、単純な構成を保つ。
実際に行っていない変更を「反映済み」と報告しない。

## 蔵書アーカイブ（books.html）

本棚の写真から、書名・著者・出版社・書影を集めて `bookshelf/books.json` に蓄える仕組み。
一覧は `books.html` が `bookshelf/books.json` を読んで表示する。

### 携帯から取り込む（パソコン不要・基本の流れ）

本人は携帯だけで行う。クラウド上の作業環境からは書誌 API に接続できないため、取得は GitHub Actions に任せる。

1. 本人が NotebookLM アプリで写真を読み取り、書名・著者・出版社の JSON 配列をこのチャットに貼る。
2. 貼られた JSON を確認する（配列であること、各要素に title があること）。壊れていたら、直す箇所を伝えて貼り直してもらう。
3. `bookshelf/inbox/YYYY-MM-DD-連番.json` として保存し、そのファイルだけをコミットして `main` に push する（本人了承済み）。
   push すると `.github/workflows/add-books.yml` が動き、書誌と書影を取得して `bookshelf/books.json` に追加し、`bookshelf/processed/` に移す。
4. 「取り込みを開始した。1〜2分後に books.html に出る。自信のない本は books.html?review=1 に出る」と短く伝える。
   Actions の結果は見えないので、「追加済み」とは言わない。確認できたことだけを報告する。
5. 本人が GitHub のアプリ／ブラウザで `bookshelf/inbox/` に直接 JSON を作成しても、同じ処理が走る（Claude を通さない場合）。

### パソコンのセッションで写真を渡されたとき、または「本を取り込んで」と言われたとき

1. **写真から文字を読む作業は、NotebookLM などのプラグイン・最適な skill を先に使う**（本人の恒常的な指示）。
   - この PC には `notebooklm` コマンド（WSL）と `book-intake` skill がある。使い方は `notebooklm --help` で確認する。
   - 写真を自分で開いて読まず、ファイルを NotebookLM のソースに登録し、書名と著者の一覧を文字で受け取る（使用量を抑えるため）。
   - NotebookLM が使えないときだけ、自分で画像を読む。その場合は使えなかった理由を一言報告する。
   - 読めない背表紙は推測で補わず、飛ばして冊数だけ報告する。
2. 読み取った結果を `candidates.json` にする（リポジトリの外、または作業用フォルダに置く）。
   `[{"title": "書名", "author": "著者(分かれば)", "publisher": "出版社(分かれば)"}]`
3. 書誌を取得して追記する。
   `python3 scripts/book_lookup.py add-batch candidates.json`
   - 国立国会図書館サーチを優先し、足りなければ Google Books で補う。書影は `bookshelf/covers/` に保存される。
   - 一致に自信がない本は `needs_review`（要確認）になり、一覧には出ない。
4. `python3 scripts/book_lookup.py review` で要確認を確認し、本人に報告する。
   本人が確定したら `python3 scripts/book_lookup.py resolve ID --pick 1`（または `--isbn`）。
5. `bookshelf/` 以下の変更だけをコミットして push する。結果（追加○冊・要確認○冊・書影なし○冊）を短く報告する。
   push に失敗したら、その旨を正直に伝える。

### 注意
- 書誌 API の応答形式は仕様書をもとに実装しており、実機での初回確認が済んでいない。
  最初の1冊は `--debug` をつけて実行し、解析が0件になるときは応答の先頭を見て `scripts/book_lookup.py` の `parse_ndl` を直す。
- 一覧の確認は `books.html?review=1`（要確認も表示）。
- 通信に失敗した分は `bookshelf/inbox/` に残る。Actions タブの「Add books from inbox」→「Run workflow」で再実行できる。
- テスト: `python3 scripts/test_book_lookup.py`（通信は模擬データ）。
