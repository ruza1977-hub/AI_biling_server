# AI Billing Server v0.5

Billing + AI Gateway untuk OPR Android dan aplikasi lain.

## Standard kredit

- 1 kredit = RM0.01 = 1 sen
- Pendaftaran baharu menerima 50 kredit sekali sahaja
- Top up minimum = RM10 = 1,000 kredit
- Top up sekarang manual
- ToyyibPay dirancang untuk fasa akan datang

## Harga pengguna v0.5

Harga yang dikenakan kepada pengguna dikira di server:

`kos sebenar penyedia dalam RM × 10 = harga pengguna`

Harga pengguna kemudian dibundarkan ke kredit penuh kerana 1 kredit = 1 sen.

Contoh: jika kos penyedia bagi satu permintaan ialah RM0.023, harga pengguna sebelum pembundaran ialah RM0.23, iaitu 23 kredit.

`ModelPrice` hendaklah menyimpan kadar kos sebenar OpenAI dalam RM bagi setiap 1 juta token input/output. `CONSUMER_PRICE_MULTIPLIER` lalai ialah 10.

## Model AI dipilih di server

Klien pengguna tidak memilih model. Endpoint pengguna memilih model dengan keutamaan:

1. `DEFAULT_AI_MODEL` jika aktif dan mempunyai harga;
2. model aktif yang namanya mengandungi `luna`;
3. model OpenAI aktif pertama sebagai fallback.

Nilai lalai `DEFAULT_AI_MODEL` ialah `gpt-5.6-luna`.

## Aliran persetujuan transaksi

1. Klien menghantar kandungan kepada `POST /v1/user/ai/estimate` tanpa memilih model.
2. Server memilih AI lalai dan mengira anggaran maksimum.
3. Tiada kredit ditolak semasa anggaran.
4. Server mengembalikan jumlah kredit, harga paparan RM dan `estimate_token`.
5. Pengguna menekan **Setuju**.
6. Klien memanggil `POST /v1/user/ai/chat` dengan `estimate_token`.
7. Server memilih semula model, mengesahkan anggaran dan menyemak baki muktamad.
8. Jika baki tidak cukup, server memulangkan HTTP 402 dan OpenAI tidak dipanggil.
9. Jika cukup, kredit direservasi, OpenAI dipanggil dan caj akhir dilaraskan berdasarkan token sebenar.

Jika input atau konfigurasi model berubah selepas anggaran, pengguna perlu melihat anggaran baharu.

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
CONSUMER_PRICE_MULTIPLIER=10
DEFAULT_AI_MODEL=gpt-5.6-luna
```

## Deploy

Ganti kandungan repo server v0.4 dengan v0.5 dan push ke GitHub. PostgreSQL lama tidak perlu dipadam.

Semak `/health`. Jangkaan:

```json
{
  "ok": true,
  "service": "AI Billing Server",
  "version": "0.5.0",
  "openai_configured": true,
  "default_ai_model": "gpt-5.6-luna",
  "consumer_price_multiplier": 10
}
```
