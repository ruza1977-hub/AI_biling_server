# Changelog

## v0.6.0
- Akaun pengguna kini menggunakan e-mel + kata laluan.
- Kata laluan disimpan sebagai hash Argon2id, bukan plaintext.
- Endpoint baharu `POST /v1/public/login` untuk memulihkan dompet pada pemasangan/peranti lain.
- Pengguna baharu masih menerima 50 kredit sekali sahaja.
- Akaun lama v0.5 tanpa kata laluan boleh mengaktifkan kata laluan pada peranti yang telah dikenal pasti tanpa menerima bonus kedua.
- Jadual `user_devices` menyimpan hash peranti yang pernah log masuk; ID peranti mentah tidak disimpan.
- Token pengguna v2 mempunyai tempoh sah dan terikat kepada peranti sesi. Token v1 lama masih diterima untuk migrasi.
- Perlindungan percubaan kata laluan: maksimum 5 kegagalan sebelum kunci sementara 15 minit.
- Endpoint admin `POST /v1/admin/users/set-password` untuk pemulihan manual jika akaun lama tidak lagi mempunyai peranti asal.
- Semua wallet, ledger, penggunaan AI dan kredit versi lama dikekalkan.
