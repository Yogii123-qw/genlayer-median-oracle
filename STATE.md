# STATE.md - Median Oracle (GenLayer Intelligent Contract)

## Status saat ini
- Fase: kode ditulis ulang ke gaya lama 22 Nov 2026 (header # v0.2.16, pin 1jb45aa8, gl.Contract, TreeMap telanjang) sesudah Studio menolak pin v0.4.0; uji lokal LULUS 19/19 atas file kirim yang byte-identik, tanpa swap. Repo dibuat dan di-push. Studio deploy BELUM dilakukan (butuh browser task induk).
- UJI TAMBAHAN 22 Nov 2026 malam: kontrak TIDAK diubah (dukungan json_path sudah ada dan terbukti benar); satu tes baru mematok path untuk tiga bentuk live (CoinGecko bitcoin.usd, Coinbase data.amount, Kraken result.XXBTZUSD.c.0). Uji lokal 20/20. Deploy versi baru (file sama, state bersih) BELUM dilakukan (butuh browser task induk).
- JANGAN SUBMIT ke portal GenLayer tanpa persetujuan Monch.

## SOP dan skill yang dibaca utuh sebelum kerja
- ~/workspace/AGENT_SOP.md (utuh, 562 baris)
- ~/AGENTS.md (bootloader, utuh)
- ~/workspace/multi-wallet-sop.md (utuh; tidak ada kerja multi-wallet di build ini)
- Skill: security_audit, evm_audit (pola audit diadaptasi ke Python Intelligent Contracts)
- Spesifikasi: ~/workspace/garapan-genlayer/BUILDER-IDEAS-2026-11-22.md (Ide 1, utuh)
- Blueprint mengikat: ~/workspace/garapan-genlayer/BUILD-BLUEPRINT-2026-11-22.md (utuh)
- Pola kontrak terbukti jalan: ~/workspace/garapan-genlayer/fairdeal-escrow/ (contracts/fairdeal.py, tests/test_fairdeal.py, STATE.md)

## Keputusan desain (mengikuti blueprint, bukan desain sendiri)
- Validator kustom via gl.vm.run_nondet_unsafe(leader_fn, validator_fn). Validator menjalankan ulang fetch dan hitung median, menerima hasil leader bila selisih median dalam 0,5 persen. run_nondet_safe, field_eq, numeric_tolerance TIDAK dipakai (tidak ada di dokumen resmi).
- Minimal 2 sumber sehat; sumber mati atau berubah format dibuang, bukan menggagalkan jalan. Outlier lebih dari 2 persen dari median awal dibuang.
- Harga disimpan skala 10**8. Timestamp dari datetime.now(timezone.utc), pola resmi GenLayer (jam GenVM dipatok ke datetime transaksi, deterministik di semua validator).
- Sumber disalin ke plain values sebelum blok nondet; blok nondet tidak menyentuh storage.
- Error leader yang diharapkan memakai prefix [EXPECTED] dan dibandingkan persis oleh validator; error lain berarti disagree.

## Verifikasi prasyarat (dilakukan sebelum koding)
- gl.vm.run_nondet_unsafe ADA di bundle lokal v0.3.0-rc7 (genlayer/gl/vm.py baris 143). gl.nondet.web.get ADA (genlayer/gl/nondet/web.py). Keduanya terbukti, bukan asumsi.
- Pola validator kustom dikonfirmasi dari dokumen resmi GenLayer (Equivalence Principle, docs.genlayer.com) dan skill write-contract (Project-Y / internet-court-skill): run_nondet_unsafe adalah pola produksi, validator menerima gl.vm.Result dan memeriksa isinstance(leaders_res, gl.vm.Return) sebelum membaca .calldata.
- Direct Mode mendukung mock_web dan run_validator (validator tertangkap, mock bisa diganti antara leader dan validator).

## Uji lokal (genlayer-test Direct Mode, 22 Nov 2026)
- 20/20 LULUS dalam 0,91 detik. Cakupan: add_source dan validasi input, get_price sebelum fetch, tiga sumber sepakat, bobot menggeser median, satu outlier dibuang, satu sumber mati (tanpa mock), sumber status 500 dibuang, body tak terurai dibuang, kurang dari 2 sumber sehat revert, tidak ada kesepakatan revert, fetch tanpa sumber revert, validator setuju di dalam pita 0,5 persen (gerak 0,2 persen), validator menolak di luar pita (gerak 2 persen), validator menolak leader yang berbohong (median 200 saat sumber bilang 100), is_fresh dengan warp waktu (30 detik, fresh 60 ya, fresh 10 tidak), remove_source, JSON path pin, parsing tiga format nyata (CoinGecko, Binance, CoinDesk), dan path yang dipatok untuk tiga bentuk live (CoinGecko bitcoin.usd int, Coinbase data.amount string, Kraken result.XXBTZUSD.c[0] string; harga 60795, 60702.755, 60795, median 60795).
- Batasan uji: kontrak diuji sebagai file kirim yang byte-identik (header # v0.2.16, pin SDK 1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6, satu-satunya runner di bundle GenVM v0.3.0-rc7), tanpa swap header dan tanpa penyesuaian API. Pin ini terbukti bisa di-deploy di Studio pada 22 Nov 2026 (kontrak minimal gaya lama, alamat 0x05534FE75D83950cFE6C5d54486d20366576Ac1D). Bukti deploy Studio atas kontrak ini tetap menjadi cek otoritatif.
- Batasan uji tambahan: jalur kesepakatan error leader (validator menyetujui UserError [EXPECTED] yang sama) TIDAK bisa diuji di Direct Mode, karena bila leader_fn raise, harness direct tidak menangkap validatornya. Jalur itu hanya bisa dibuktikan di deploy Studio.

## Lint
- genvm-lint check LULUS (3 checks) atas kontrak gaya lama pin 1jb45aa8, 22 Nov 2026 malam. Catatan lama soal validasi schema gagal berlaku untuk pin v0.4.0 (kzybg95x) yang sudah tidak dipakai; pin kirim sekarang ada di cache GenVM v0.3.0-rc7 linter.

## Review akhir Groq (22 Nov 2026, openai/gpt-oss-120b)
- Review uji tambahan (diff tes saja, malam 22 Nov 2026): VERDICT: PUSH, 0 temuan. Review memeriksa tiga path yang dipatok tersimpan benar, median terhitung benar, dan tidak ada float, nondeterminisme, atau logika konsensus baru yang masuk.
- VERDICT Groq: FIX FIRST, 8 temuan. Satu DIPERBAIKI, tujuh DITOLAK dengan bukti:
- DIPERBAIKI: URL duplikat tidak ditolak. Sekarang add_source menolak URL yang sudah aktif ("Source URL already registered"), plus satu assert di test_add_source_rejects_bad_input. Tes ulang 19/19 lolos.
- DITOLAK 1: "validator tidak boleh memanggil nondet di dalam validator". Salah. Dokumen resmi GenLayer (Equivalence Principle) menetapkan pola validator adalah menjalankan ulang leader_fn dan membandingkan hasilnya, dan skill write-contract mencontohkan persis itu. strict_eq bawaan SDK juga menjalankan ulang fungsi nondet di dalam validator lewat spawn_sandbox.
- DITOLAK 2: "leaders_res.calldata mungkin tidak ada". Salah. Bundle lokal membuktikan Return adalah dataclass dengan field calldata (genlayer/py/calldata.py dan genlayer/gl/vm.py), dan dokumen resmi menulis "access the value via .calldata".
- DITOLAK 3: "gl.nondet.web.get mungkin tidak ada, mestinya http.get". Salah. Bundle lokal membuktikan gl.nondet.web.get ada (genlayer/gl/nondet/web.py), dan skill write-contract mencontohkan gl.nondet.web.post.
- DITOLAK 4: "run_nondet_unsafe mungkin salah, mestinya run_nondet atau run_nondet_safe". Salah. Bundle lokal membuktikan run_nondet_unsafe ada (vm.py baris 143), dokumen resmi menulis "use run_nondet_unsafe... This is what production contracts do", dan blueprint melarang run_nondet_safe karena API itu tidak ada.
- DITOLAK 5: "datetime.now tidak deterministik, pakai gl.block.timestamp". Salah. Dokumen resmi Transaction Context: jam stdlib dipatok ke datetime transaksi dan sama di semua validator. Contoh kontrak Studio menulis "Do not use gl.block.timestamp". gl.block tidak ada di SDK.
- DITOLAK 6: "median awal dihitung sebelum buang outlier". Bukan cacat: itu desain yang ditetapkan spesifikasi ide ("membuang outlier yang selisihnya lebih dari 2 persen dari median awal").
- DITOLAK 7: "cap harga 1e9 bisa menolak aset mahal". Bukan cacat: tidak ada aset seharga 1 miliar dolar per unit; cap adalah penjaga kewarasan terhadap data sampah, bukan batas harga nyata.

## Audit teks
- README.md: 0 em/en dash, 0 klise AI, 0 pola retoris (dicek dengan rg sesuai AGENT_SOP.md).

## File aktif
- contracts/median_oracle.py (kontrak utama)
- tests/test_median_oracle.py (20 uji lokal)
- README.md
- STATE.md
- LICENSE (MIT)

## Alamat / hash
- Studio deploy versi lama (20:15 WIB 22 Nov 2026): 0x79896b1fecc2500CEFF08E3fb1B7dD43eDC4aFDa, deploy tx 0x4c5a5823c069198452789efb7705159a0563b8144196262eee40ea8c5d12f12c. add_source CoinGecko dan Binance FINALIZED, fetch_price FINALIZED tapi konsensus USER_ERROR "Fewer than 2 healthy price sources" (Binance mengembalikan JSON error; CoinGecko bersarang tanpa path yang dipatok). Deploy ulang file yang sama dengan path dipatok sejak awal menunggu browser task.
- Repo GitHub: https://github.com/Yogii123-qw/genlayer-median-oracle
- Commit: f0cd880 (main, terverifikasi di remote)

## Patch determinisme 22 Nov 2026
- float() dihapus dari kontrak: _scale_price dan _as_number lama diganti _parse_decimal dan _as_number baru, semua aritmatika integer skala 10**8. JSON fractions dibaca sebagai teks lewat json.loads(parse_float=str). grep float( di contracts/median_oracle.py = 0.
- Tes lokal diulang sesudah patch: 19/19 lolos.
- Review Groq (gpt-oss-120b) atas diff patch: PASS, 0 temuan.
- .gitignore diperluas: *.log, logs/, .genlayer/, .env*.
- README ditambah bagian Verification & Test Results dan Live Deployment Proof (GenLayer Studio) dengan slot TBD.
- Catatan: harga dengan lebih dari 18 digit signifikan ditolak sebagai sumber tidak sehat, bukan sebagai harga salah. Sumber itu dibuang dan fetch tetap jalan bila dua sumber lain sehat.

## Langkah berikutnya
1. Serahkan ke induk untuk uji deploy Studio lewat cloud browser (akun dev Studio, bukan wallet Monch). Tempel kontrak, deploy dengan argumen konstruktor BTC/USD, daftarkan CoinGecko dengan path bitcoin.usd dan Coinbase dengan path data.amount (masing-masing bobot 1), panggil fetch_price sampai ada transaksi FINALIZED dan consensus tercapai, lalu verifikasi get_price (price lebih dari 0, has_price true). Kraken (path result.XXBTZUSD.c.0) hanya fallback bila Coinbase gagal live. Catat alamat kontrak dan tx hash.
2. Setelah deploy Studio terverifikasi, serahkan draf submission ke Monch untuk disetujui. JANGAN submit ke Portal tanpa itu.
