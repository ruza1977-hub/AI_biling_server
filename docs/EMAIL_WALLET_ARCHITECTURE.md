# Keputusan Seni Bina: Satu E-mel, Satu Dompet

Tarikh: 2026-10-16
Status: DIPUTUSKAN — untuk pelaksanaan AI Biling 2.0; belum dimigrasikan.

## Prinsip
1. Satu e-mel ternormal yang telah disahkan = tepat satu dompet kredit AI Biling.
2. Satu dompet boleh digunakan merentas peranti dan aplikasi, termasuk SPA, tanpa menggandakan baki.
3. AI Biling kekal servis dan PostgreSQL berasingan; SPA mengaksesnya melalui API server-ke-server.
4. E-mel ialah pengecam dompet, bukan bukti hak akses. Permintaan mesti dibenarkan melalui identiti sah / token servis dan pemetaan pengguna yang dipercayai.
5. Top up melalui e-mel masuk ke dompet tunggal yang sama; setiap transaksi mempunyai idempotency key dan ledger audit.
6. Akaun SPA menggunakan sesi SPA sedia ada; tiada kata laluan AI Biling tambahan diperlukan bagi pengguna SPA.
7. Jangan membenarkan klien menukar e-mel dompet atau memilih e-mel orang lain tanpa pengesahan pemilikan.

## Migrasi daripada Device Wallet v1
- Buat salinan DB ujian dan inventori semua device_accounts, wallets, ledger_entries, usage_records.
- Tentukan pemetaan e-mel sah; konflik (satu e-mel pada beberapa peranti, satu peranti dengan e-mel berbeza, e-mel belum disahkan) masuk ke proses semakan, bukan digabung secara automatik.
- Gunakan jadual pemetaan dan migrasi berversi; gabungkan baki melalui transaksi ledger yang boleh diaudit dan idempotent.
- Pastikan jumlah baki sebelum dan selepas migrasi sama, tiada transaksi berganda, dan sejarah penggunaan kekal.
- Kekalkan API v1 melalui lapisan keserasian sementara; jangan deploy atau ubah data produksi tanpa kelulusan.
- Uji akses berbilang peranti, pengguna lain yang tahu e-mel, pertukaran e-mel, top up serentak, dan rollback.

## Acceptance
- E-mel unik pada identiti dompet dan telah disahkan.
- Akses rentas peranti sah, akses tanpa autorisasi ditolak.
- Jumlah kredit dan ledger konsisten selepas migrasi.
- SPA terus berfungsi jika AI Biling tidak tersedia.
