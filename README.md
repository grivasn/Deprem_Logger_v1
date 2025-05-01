# 📡 Deprem Bildirim Botu

Bu proje, Türkiye'de gerçekleşen depremleri **AFAD** ve **Kandilli Rasathanesi** kaynaklarından takip ederek, kullanıcıları **Telegram üzerinden** anlık olarak bilgilendiren bir Python botudur.

## 🚀 Özellikler

- 🌐 AFAD ve Kandilli'den veri çekimi  
- 🗺️ Haritalı bildirim: Deprem konumu harita üzerinde gösterilir  
- 📩 Anlık Telegram mesajı gönderimi  
- 🛎️ Kullanıcıya özel eşik değeri belirleme (örnek: M3.5 ve üzeri)  
- 📊 `/last` ile son 5 deprem, `/biggest` ile 24 saatin en büyük depremi  
- 🔁 7/24 çalışan yapı (Linux + AWS EC2)  
- ☁️ Supabase ile kullanıcı ve deprem verisi saklama  

---

## 📁 Kurulum

### 1. Gerekli bağımlılıkları yükleyin

```bash
pip install -r requirements.txt
```

### 2. Ortam Değişkenleri

Proje dizinine `.env` dosyası oluşturun ve aşağıdaki değerleri girin:

```env
TELEGRAM_TOKEN=telegram_bot_tokeniniz
SUPABASE_URL=https://xxxx.supabase.co
SUPABASE_KEY=supabase_secret_key
```

### 🖥️ Başlatma

```bash
python app.py
```

Bot başlatıldığında:

- Son 24 saatlik depremleri çekip Supabase’e kaydeder  
- Yeni deprem verilerini belirli aralıklarla kontrol eder  
- Kullanıcı mesajlarını dinleyerek yanıt verir  
- Harita oluşturur ve mesajla birlikte gönderir

---

## 💬 Komutlar

| Komut                  | Açıklama                                                          |
|------------------------|-------------------------------------------------------------------|
| `/start`               | Bildirimleri başlatır / yeniden abone olur                        |
| `/stop`                | Bildirimleri durdurur                                             |
| `/deprem <eşik>`       | Deprem büyüklüğü eşiğini belirler (örnek: /deprem 4.0)           |
| `/last`                | Son 5 depremi listeler (eşik değerine göre)                      |
| `/biggest`             | Son 24 saatteki en büyük depremi gösterir                        |

---

## 🛠️ Kullanılan Teknolojiler

- **Python**: Bot altyapısı  
- **Telegram Bot API**: Mesajlaşma ve komut yönetimi  
- **Supabase**: Veritabanı ve kullanıcı yönetimi  
- **Matplotlib & Cartopy**: Harita çizimi  
- **Requests & BeautifulSoup**: AFAD & Kandilli veri çekimi  
- **AWS EC2 (Ubuntu)**: 7/24 çalışma ortamı  
- **WinSCP & SSH**: Sunucuya dosya yükleme ve kontrol

---

## 🔁 Canlı Kullanım

Botu test etmek için Telegram üzerinden erişebilirsiniz:

👉 [@FinansciBot](https://t.me/FinansciBot)

---

## 📌 Not

Proje açık kaynaklıdır ve aktif olarak geliştirilmeye devam etmektedir.  
Geliştirme sürecine katkı sunmak veya önerilerinizi paylaşmak isterseniz memnuniyetle karşılarım.

---

## 📄 Lisans

MIT Lisansı © 2025 Furkan Sipahi
