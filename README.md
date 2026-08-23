# AI Billing Server v1.0 — Device Wallet + E-mel Rujukan

Prinsip kekal: **1 peranti = 1 Device Wallet = banyak aplikasi**.

E-mel kini digunakan sebagai **rujukan dompet dan top up**, bukan sebagai kaedah login. Tiada kata laluan diperlukan untuk aliran Device Wallet.

## Aliran

1. App bootstrap menggunakan Device ID.
2. E-mel dimasukkan sekali dan diikat kepada Device Wallet.
3. Selepas itu app auto login berdasarkan peranti.
4. Pengguna yang mahu top up hanya perlu memberikan e-mel yang telah diikat.
5. Admin cari e-mel dan tambah kredit.

Satu e-mel hanya boleh diikat kepada satu Device Wallet aktif supaya top up melalui e-mel tidak ambigu.

## Endpoint baharu / dikemas kini

- `POST /v1/device/bootstrap` — menerima `email` pilihan dan memulangkan `email`, `email_required`.
- `POST /v1/device/profile/email` — ikat e-mel kepada Device Wallet menggunakan token peranti.
- `GET /v1/device/account/summary` — memulangkan e-mel.
- `GET /v1/admin/device-wallets?email=guru@example.com` — carian tepat e-mel.
- `POST /v1/admin/device-wallets/topup-by-email` — top up manual dengan e-mel.

Contoh top up:

```json
{
  "email": "guru@example.com",
  "amount_rm": 10,
  "idempotency_key": "manual-uuid",
  "note": "Top up manual RM10"
}
```

Minimum kekal **RM10 = 1,000 kredit**.

## Migrasi

Deploy pada PostgreSQL yang sama. Jangan padam database. Startup menambah `device_accounts.email_normalized` dan cuba memindahkan e-mel lama secara automatik jika hanya satu e-mel lama sepadan dengan peranti itu.

## Health

`GET /health` menunjukkan `version: 1.0.0`, `auth_mode: device_wallet`, `shared_wallet: true`.
