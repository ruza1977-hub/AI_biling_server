# AI Biling Server 2.0 — Pelan Pengerasan (16 Oktober 2026)

Status: DEVELOPMENT ONLY. Branch ini tidak boleh dideploy ke produksi sebelum ujian lulus.

## Baseline
- FastAPI + PostgreSQL + SQLAlchemy + OpenAI Responses gateway.
- Kekalkan API v1, baki dompet, ledger, idempotency key dan pelanggan sedia ada.
- Jangan gunakan database produksi untuk ujian.
- Jangan letakkan API key atau DATABASE_URL sebenar dalam Git.

## P0: keselamatan dan integriti kewangan
1. Gantikan kepercayaan kepada Device ID yang boleh dikawal klien dengan pengesahan bukti pemilikan peranti / sesi yang selamat. Hadkan bootstrap, percubaan kredit, dan penyalahgunaan berbilang peranti. Uji ID yang dipalsukan.
2. Wujudkan migrasi SQL berversi, berasingan daripada startup aplikasi; backup dan ujian rollback. Alihkan create_all/ALTER TABLE/backfill dari lifespan selepas migrasi diuji.
3. Tambah pemulihan AIRequest pending yang terbiar, dengan semakan status provider sebelum refund untuk mengelakkan caj berganda atau kredit percuma. Uji crash di setiap fasa.
4. Pastikan ledger atomik, kekangan baki, idempotency, dan persaingan serentak diuji; tentukan polisi caj melebihi reserve.
5. Rate limit untuk bootstrap, login, reset kata laluan, estimate, chat, dan pentadbir. Jangan dedahkan mesej provider mentah kepada pengguna.

## P1: kualiti dan operasi
6. Pisahkan app/main.py kepada routers, services dan repositories secara berperingkat tanpa mengubah kontrak API.
7. Pin dependencies, tambah lockfile, semakan kerentanan dan CI.
8. Tambah ujian unit dan integrasi PostgreSQL untuk topup, refund, migrasi, 2+ permintaan serentak, restart dan gangguan rangkaian.
9. Tambah metrik, request ID, log tersaring dan amaran kos; jangan log prompt sensitif atau token.
10. Sediakan Docker build yang konsisten, health/readiness dan staging berasingan.

## Acceptance gates
- Semua ujian automatik PASS, termasuk concurrency dan crash recovery.
- Ujian migrasi pada salinan DB, kiraan baki dan ledger sebelum/selepas konsisten.
- Tiada secret dalam Git; konfigurasi production melalui secrets manager.
- Semakan manual sebelum merge ke main dan sebelum deployment.
