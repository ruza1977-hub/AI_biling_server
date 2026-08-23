# AI Billing Server v0.9.0 — Shared Device Wallet

Versi ini menukar aliran pengguna daripada akaun e-mel/kata laluan kepada **Device Wallet**.

## Prinsip

`1 peranti = 1 Device Wallet = banyak aplikasi`

Aplikasi yang disokong secara lalai:

- OPR Android
- GBK Digital
- SPK
- StudyMate

Semua aplikasi pada peranti yang sama boleh melihat dan menggunakan baki kredit yang sama, dengan syarat aplikasi Android menggunakan signing certificate/keystore yang sama supaya `ANDROID_ID` konsisten antara aplikasi.

## Auto login

Klien menghantar:

```json
{
  "app_name": "OPR Android",
  "device_id": "android:..."
}
```

ke:

`POST /v1/device/bootstrap`

Server hanya menyimpan HMAC hash bagi ID peranti. Jika Device Wallet sudah wujud, server mengeluarkan token sesi baharu. Tiada e-mel atau kata laluan diperlukan.

## Kredit percubaan

Peranti baharu menerima 50 kredit sekali sahaja. App kedua, ketiga atau keempat pada peranti yang sama tidak menerima bonus tambahan.

## Migrasi pengguna OPR lama

Apabila device yang pernah berdaftar dalam v0.3-v0.8 memanggil bootstrap buat kali pertama, server cuba:

1. mencari pendaftaran lama berdasarkan hash peranti;
2. mencipta Device Wallet;
3. memindahkan baki semasa wallet lama ke Device Wallet;
4. tidak memberi bonus 50 kredit kali kedua;
5. mengekalkan rekod penggunaan AI lama di bawah ID Device Wallet baharu.

Database PostgreSQL lama **jangan dipadam**.

## Endpoint Device Wallet

- `POST /v1/device/bootstrap`
- `GET /v1/device/account/summary`
- `GET /v1/device/usage/recent`
- `GET /v1/device/topup/info`
- `POST /v1/device/ai/estimate`
- `POST /v1/device/ai/chat`

Admin:

- `GET /v1/admin/device-wallets`
- `POST /v1/admin/device-wallets/topup-credits`

## Top up manual

Minimum top up masih RM10 = 1,000 kredit. ToyyibPay boleh ditambah kemudian tanpa mengubah konsep Device Wallet.

Contoh body admin:

```json
{
  "external_user_id": "dev_xxx",
  "credits": 1000,
  "idempotency_key": "manual-20260823-001",
  "note": "Top up RM10"
}
```

## Railway Variables

Kekalkan:

```text
DATABASE_URL=${{Postgres.DATABASE_URL}}
MASTER_ADMIN_KEY=...
OPENAI_API_KEY=...
TRIAL_CREDITS=50
MIN_TOPUP_RM=10
CONSUMER_PRICE_MULTIPLIER=10
DEFAULT_AI_MODEL=gpt-5.6-luna
DEVICE_HASH_SECRET=...
USER_TOKEN_SECRET=...
```

Pilihan:

```text
PUBLIC_DEVICE_APPS=OPR Android,GBK Digital,SPK,StudyMate
DEVICE_WALLET_HUB_APP_NAME=__DEVICE_WALLET__
```

Selepas deploy, `/health` sepatutnya menunjukkan:

```json
{
  "version": "0.9.0",
  "auth_mode": "device_wallet",
  "shared_wallet": true
}
```
