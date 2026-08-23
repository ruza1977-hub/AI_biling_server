# AI Billing Server v0.9.0

## Device Wallet

- Tambah akaun `DeviceAccount` global yang tidak terikat kepada satu app.
- Tambah `DeviceAppLink` untuk mengaitkan satu peranti kepada banyak aplikasi.
- Tambah `POST /v1/device/bootstrap` untuk daftar/auto login tanpa e-mel atau kata laluan.
- Satu peranti menerima 50 kredit percubaan sekali sahaja untuk seluruh ekosistem aplikasi.
- Satu wallet boleh dikongsi oleh OPR Android, GBK Digital, SPK dan StudyMate.
- Token sesi Device Wallet terikat kepada hash peranti dan app yang sedang menggunakan wallet.
- ID peranti mentah tidak disimpan; server menyimpan HMAC-SHA256.

## Migrasi v0.3-v0.8

- Jika server mengenali device hash lama, v0.9 mencipta Device Wallet dan memindahkan baki wallet lama secara automatik.
- Bonus 50 kredit tidak diberikan semula kepada device lama.
- Rekod penggunaan AI lama dipautkan kepada ID Device Wallet baharu supaya sejarah penggunaan boleh diteruskan.
- Endpoint e-mel/kata laluan lama dikekalkan sementara untuk keserasian, tetapi OPR v0.7 tidak menggunakannya.

## Billing

- Anggaran kredit sebelum transaksi kekal wajib.
- Harga pengguna kekal kos sebenar AI dalam RM x `CONSUMER_PRICE_MULTIPLIER` (lalai 10).
- 1 kredit = RM0.01.
- Top up manual Device Wallet minimum RM10 = 1,000 kredit.
- Tambah endpoint admin untuk senarai dan top up Device Wallet.
