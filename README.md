# AI Billing Server v0.1

Backend billing AI multi-app untuk Railway + PostgreSQL.

## Konsep
- Satu billing server boleh digunakan oleh banyak aplikasi.
- Setiap aplikasi mempunyai `X-API-Key` tersendiri.
- Wallet dipisahkan mengikut `app + external_user_id`.
- Nilai wang disimpan sebagai integer micro-RM: `RM1 = 1,000,000 micros`.
- Semua topup/charge direkod dalam ledger.
- `idempotency_key` mengelakkan caj/topup berganda apabila request diulang.
- Harga AI dikonfigurasi pada server; client tidak menentukan harga sendiri.

## Deploy Railway
1. Buat Project Railway.
2. Tambah PostgreSQL.
3. Tambah service daripada repo GitHub yang mengandungi projek ini.
4. Di Variables service API, tetapkan:
   - `DATABASE_URL=${{Postgres.DATABASE_URL}}`
   - `MASTER_ADMIN_KEY=<rahsia-panjang-rawak>`
   - `SERVICE_NAME=AI Billing Server`
5. Generate Domain pada Settings > Networking.
6. Uji `GET /health`.

> Jika nama service PostgreSQL anda bukan `Postgres`, tukar namespace reference variable itu mengikut nama service sebenar.

## Aliran ujian

### 1) Daftar aplikasi
```bash
curl -X POST "$BASE_URL/v1/admin/apps" \
  -H "X-Admin-Key: $MASTER_ADMIN_KEY" \
  -H "Content-Type: application/json" \
  -d '{"name":"GBK-Mobile"}'
```
Simpan `api_key` yang dipulangkan. Server hanya menyimpan hash.

### 2) Tetapkan harga model
Contoh sahaja — masukkan kadar sebenar yang anda mahu bil kepada pengguna.
```bash
curl -X POST "$BASE_URL/v1/admin/pricing" \
  -H "X-Admin-Key: $MASTER_ADMIN_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "provider":"openai",
    "model":"MODEL_NAME",
    "input_rm_per_million":"1.00",
    "output_rm_per_million":"4.00",
    "markup_percent":"20"
  }'
```

### 3) Tambah kredit RM10
```bash
curl -X POST "$BASE_URL/v1/admin/topup" \
  -H "X-Admin-Key: $MASTER_ADMIN_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "app_name":"GBK-Mobile",
    "external_user_id":"guru-001",
    "amount_rm":"10.00",
    "idempotency_key":"topup-guru001-0001",
    "note":"Topup ujian"
  }'
```

### 4) Semak wallet
```bash
curl "$BASE_URL/v1/wallet?external_user_id=guru-001" \
  -H "X-API-Key: $APP_API_KEY"
```

### 5) Caj penggunaan AI
```bash
curl -X POST "$BASE_URL/v1/usage/charge" \
  -H "X-API-Key: $APP_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "external_user_id":"guru-001",
    "provider":"openai",
    "model":"MODEL_NAME",
    "input_tokens":1200,
    "output_tokens":500,
    "idempotency_key":"req-gbk-20260822-000001",
    "metadata":{"feature":"jana_rph"}
  }'
```

## Nota keselamatan penting
`X-API-Key` aplikasi ini direka untuk **server-to-server**. Jangan hardcode key rahsia kekal di APK Android kerana ia boleh diekstrak. Untuk aplikasi mudah alih terus ke billing server, versi seterusnya perlu tambah login/JWT atau signed session token per pengguna/peranti.
