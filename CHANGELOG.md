# AI Billing Server v0.7

- Tambah `POST /v1/public/forgot-password`.
- Kata laluan asal tidak boleh diperoleh semula kerana server menyimpan Argon2id hash sahaja. Sistem menghantar kata laluan sementara melalui e-mel.
- Kata laluan sementara tamat selepas 30 minit secara lalai dan wajib ditukar selepas log masuk.
- Permintaan lupa kata laluan **tidak membatalkan kata laluan asal**; server menyimpan hash sementara berasingan sehingga pengguna membuktikan akses e-mel.
- Tambah `POST /v1/user/password/change`.
- Tambah sokongan SMTP Railway melalui environment variables.
- Templat e-mel menggunakan warna OPR: kuning, merah dan biru navy.
- Dompet, ledger, 50 kredit percubaan dan semua endpoint v0.6 dikekalkan.
