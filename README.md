# AI Billing Server v0.6

FastAPI + PostgreSQL untuk billing AI OPR Android.

## Prinsip kredit
- 1 kredit = 1 sen.
- Akaun baharu menerima 50 kredit sekali sahaja.
- Top up minimum RM10 = 1,000 kredit.
- Harga pengguna AI = kos sebenar penyedia dalam RM × `CONSUMER_PRICE_MULTIPLIER` (lalai 10), kemudian dibundarkan kepada kredit penuh.
- Sebelum AI dipanggil, server mengeluarkan anggaran bertandatangan. Baki diperiksa semula selepas pengguna bersetuju.

## Akaun v0.6
Pengguna mempunyai dompet kekal berdasarkan akaun e-mel.

### Pengguna baharu
`POST /v1/public/register`

```json
{
  "app_name": "OPR Android",
  "email": "guru@example.com",
  "password": "kata-laluan-minimum-8",
  "device_id": "android:..."
}
```

Pendaftaran pertama memberikan 50 kredit dan mengeluarkan token sesi.

### Log masuk
`POST /v1/public/login`

```json
{
  "app_name": "OPR Android",
  "email": "guru@example.com",
  "password": "kata-laluan-minimum-8",
  "device_id": "android:..."
}
```

Log masuk tidak menghasilkan bonus baharu. Token sesi baharu dikeluarkan untuk peranti tersebut dan dompet lama terus digunakan.

### Akaun lama v0.5
Rekod lama tidak mempunyai kata laluan. Jika pengguna menjalankan `register` menggunakan e-mel lama pada peranti yang pernah dikenal pasti, server menetapkan kata laluan pada akaun lama dan memulangkan dompet lama tanpa 50 kredit tambahan.

Jika peranti asal sudah tiada, pentadbir boleh menggunakan endpoint terlindung `POST /v1/admin/users/set-password` dengan `X-Admin-Key`. Ini ialah pemulihan manual sehingga pengesahan e-mel automatik ditambah pada versi akan datang.

## Keselamatan kata laluan
- Hash Argon2id (`argon2-cffi`).
- Kata laluan mentah tidak disimpan dalam PostgreSQL.
- ID peranti mentah tidak disimpan; server menyimpan HMAC hash.
- Selepas 5 kata laluan salah, akaun dikunci sementara 15 minit.
- Token pengguna v2 tamat selepas 30 hari secara lalai.

## Migrasi Railway
Gantikan kod v0.5 dengan v0.6 dan deploy pada PostgreSQL yang sama. Jangan padam database.

Startup v0.6 menambah secara selamat:
- `password_hash`
- `password_set_at`
- `failed_login_count`
- `locked_until`
- jadual `user_devices`

Wallet dan ledger lama tidak diubah.

## Variables
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
USER_TOKEN_DAYS=30
LOGIN_MAX_ATTEMPTS=5
LOGIN_LOCK_MINUTES=15
```

## Health
`GET /health`

Sasaran:
```json
{
  "ok": true,
  "service": "AI Billing Server",
  "version": "0.6.0",
  "openai_configured": true
}
```
