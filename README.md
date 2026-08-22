# AI Billing Server v0.2

FastAPI + PostgreSQL backend untuk Railway. v0.2 mengekalkan semua endpoint billing v0.1 dan menambah **OpenAI AI Gateway**, reservation kredit, settlement token sebenar, serta endpoint dashboard admin.

## Ciri v0.2

- `POST /v1/ai/chat` memanggil OpenAI Responses API dari server.
- `OPENAI_API_KEY` hanya berada pada Railway/backend, bukan dalam aplikasi klien.
- Semak harga model dan wallet sebelum panggilan OpenAI.
- Reserve kredit secara konservatif sebelum kos provider berlaku.
- Selepas respons: guna `usage.input_tokens` + `usage.output_tokens` sebenar untuk caj akhir.
- Pulangkan baki reservation yang tidak digunakan.
- Jika panggilan OpenAI gagal, reservation dipulangkan sepenuhnya.
- Idempotency: request sama tidak sepatutnya memanggil OpenAI dua kali selepas ia selesai.
- Respons OpenAI dibuat dengan `store=false`.
- v0.1 endpoints kekal untuk keserasian modul Admin macOS sedia ada.
- Admin API baharu: dashboard, list apps, list pricing, ledger, usage.

## Railway Variables

```text
DATABASE_URL=${{Postgres.DATABASE_URL}}
MASTER_ADMIN_KEY=<rahsia-admin-anda>
OPENAI_API_KEY=sk-proj-...
SERVICE_NAME=AI Billing Server
```

Pilihan:

```text
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_TIMEOUT_SECONDS=120
```

Jangan commit `.env` atau API key sebenar ke GitHub.

## Upgrade daripada v0.1

v0.2 menambah jadual baru `ai_requests`. Jadual v0.1 tidak dipadam atau dinamakan semula. Pada startup, SQLAlchemy `create_all()` akan mencipta jadual baru itu.

## Aliran penggunaan

### 1. Uji server

```bash
curl "$BASE_URL/health"
```

### 2. Tetapkan harga model

Harga billing server adalah **RM per 1 juta token**. Tentukan kadar RM sendiri berdasarkan kos provider, kadar USD/RM dan margin anda.

```bash
curl -X POST "$BASE_URL/v1/admin/pricing" \
  -H "X-Admin-Key: $MASTER_ADMIN_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "provider":"openai",
    "model":"gpt-5.6-luna",
    "input_rm_per_million":"2.50",
    "output_rm_per_million":"15.00",
    "markup_percent":"20"
  }'
```

> Nilai di atas hanya contoh konfigurasi billing anda, bukan janji harga provider semasa.

### 3. Top-up pengguna

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

### 4. Panggil AI Gateway

```bash
curl -X POST "$BASE_URL/v1/ai/chat" \
  -H "X-API-Key: $APP_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "external_user_id":"guru-001",
    "model":"gpt-5.6-luna",
    "input":"Terangkan fotosintesis secara ringkas dalam Bahasa Melayu.",
    "instructions":"Jawab sebagai pembantu guru sekolah.",
    "max_output_tokens":700,
    "idempotency_key":"gbk-ai-20260822-000001",
    "metadata":{"feature":"nota_ai"}
  }'
```

Contoh respons:

```json
{
  "ok": true,
  "duplicate": false,
  "model": "gpt-5.6-luna",
  "output_text": "...",
  "input_tokens": 32,
  "output_tokens": 180,
  "charged_rm": "0.003210",
  "balance_rm": "9.996790"
}
```

## Endpoint utama

### Admin (`X-Admin-Key`)

- `POST /v1/admin/apps`
- `GET /v1/admin/apps`
- `POST /v1/admin/pricing`
- `GET /v1/admin/pricing`
- `POST /v1/admin/topup`
- `GET /v1/admin/dashboard`
- `GET /v1/admin/ledger`
- `GET /v1/admin/usage`

### App (`X-API-Key`)

- `GET /v1/wallet?external_user_id=...`
- `GET /v1/usage/recent?external_user_id=...`
- `POST /v1/usage/charge` (legacy/manual billing)
- `GET /v1/ai/models`
- `POST /v1/ai/chat`

## Keselamatan

`X-API-Key` app masih merupakan rahsia server-to-server. Untuk APK Android/iOS yang bercakap terus dengan server, langkah seterusnya ialah akaun pengguna + access token/JWT atau token sesi bertandatangan. Jangan hardcode `MASTER_ADMIN_KEY`, `OPENAI_API_KEY`, atau app API key kekal dalam APK.

v0.2 menyimpan `output_text` untuk replay idempotent. Jika data aplikasi anda sangat sensitif, v0.3 patut menambah polisi retention/enkripsi output atau mod `store_output=false` selepas identiti pengguna/JWT tersedia.
