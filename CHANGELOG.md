# Changelog

## v0.5.0
- Harga pengguna = kos sebenar RM penyedia × 10.
- 1 kredit kekal 1 sen.
- Model untuk endpoint pengguna dipilih di server; klien tidak perlu menghantar model.
- `DEFAULT_AI_MODEL` ditambah dengan lalai `gpt-5.6-luna`.
- Server mengutamakan model aktif yang mengandungi `luna` jika model lalai tidak ditemui.
- Endpoint anggaran mengembalikan `model`, `display_price_rm`, `estimate_credits` dan `estimate_token`.
- Anggaran tidak menolak kredit.
- Baki disemak semula hanya apabila pengguna bersetuju meneruskan transaksi.
- Jika baki tidak mencukupi, HTTP 402 dipulangkan sebelum OpenAI dipanggil.
- Top up manual minimum RM10 / 1,000 kredit dikekalkan.
