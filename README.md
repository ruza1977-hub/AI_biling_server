# AI Billing Server v0.4

Billing + AI Gateway untuk aplikasi seperti OPR Android.

## Standard kredit

- 1 kredit = RM0.01 = 1 sen
- Pendaftaran baharu menerima 50 kredit sekali sahaja
- Top up minimum = RM10 = 1,000 kredit
- Top up v0.4 masih manual
- Integrasi ToyyibPay disediakan sebagai fasa akan datang

## Baharu v0.4

### Pendaftaran aplikasi awam yang selamat untuk klien Android
`POST /v1/public/register`

OPR Android hanya menghantar:
- nama aplikasi (`OPR Android`)
- e-mel
- ID peranti

Server menyimpan e-mel dan HMAC hash ID peranti. ID mentah tidak disimpan. Jika e-mel/peranti yang sama mendaftar semula, akaun lama dipulihkan tanpa bonus kedua. Jika e-mel atau peranti cuba dipadankan dengan pendaftaran lain, server menolak pendaftaran.

Server mengeluarkan token pengguna bertandatangan. Android menyimpan token itu menggunakan Android Keystore. `X-API-Key` aplikasi tidak perlu ditanam dalam APK.

### Akaun kredit pengguna
- `GET /v1/user/account/summary`
- `GET /v1/user/usage/recent`
- `GET /v1/user/topup/info`
- `GET /v1/user/models`

### Wajib anggar sebelum caj AI
1. Android memanggil `POST /v1/user/ai/estimate`.
2. Server mengira anggaran maksimum konservatif tanpa menolak kredit.
3. Server mengeluarkan `estimate_token` sah untuk tempoh pendek (default 5 minit).
4. Android memaparkan anggaran, baki semasa dan baki selepas anggaran.
5. Hanya selepas pengguna menekan Setuju, Android memanggil `POST /v1/user/ai/chat` bersama `estimate_token`.
6. Jika input/model berubah atau anggaran tamat tempoh, transaksi ditolak dan pengguna perlu melihat anggaran baharu.
7. Caj akhir menggunakan token sebenar dan lazimnya sama atau lebih rendah daripada anggaran maksimum.

Endpoint `/v1/ai/chat` v0.2/v0.3 kekal untuk klien lama yang menggunakan `X-API-Key`.

## Railway Variables

```text
DATABASE_URL=${{Postgres.DATABASE_URL}}
MASTER_ADMIN_KEY=<rahsia-admin>
OPENAI_API_KEY=<openai-service-key>
SERVICE_NAME=AI Billing Server
TRIAL_CREDITS=50
DEVICE_HASH_SECRET=<rahsia-random-panjang>
USER_TOKEN_SECRET=<rahsia-random-panjang-berbeza>
PUBLIC_OPR_APP_NAME=OPR Android
ESTIMATE_TTL_SECONDS=300
MIN_TOPUP_RM=10
```

`DEVICE_HASH_SECRET` dan `USER_TOKEN_SECRET` sangat digalakkan. Jika kosong, server menggunakan `MASTER_ADMIN_KEY` sebagai fallback.

## Deploy

Ganti kandungan repo server lama dengan v0.4 dan push ke GitHub. Railway akan deploy semula. PostgreSQL lama tidak perlu dipadam.

Semak:

```text
GET /health
```

Jangkaan:

```json
{
  "ok": true,
  "service": "AI Billing Server",
  "version": "0.4.0",
  "openai_configured": true
}
```

Pada startup, server memastikan aplikasi bernama `OPR Android` wujud. Jika belum ada, rekod AppClient dicipta secara automatik.
