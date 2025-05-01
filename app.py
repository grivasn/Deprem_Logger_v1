import os
import re
import requests
import uuid
import logging
import time
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import cartopy.crs as ccrs
import cartopy.feature as cfeature
from datetime import datetime, timedelta, timezone
from supabase import create_client
from dotenv import load_dotenv
import threading
import tempfile
import hashlib
import bs4


load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
TURKEY_TZ = timezone(timedelta(hours=3))

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.FileHandler('deprem.log'), logging.StreamHandler()]
)

def extract_city(location: str) -> str | None:
    if not location:
        return None
    m = re.search(r'\(([^)]+)\)', location)
    return m.group(1).upper() if m else location.split()[-1].upper()

def stable_id(event: dict) -> str:
    raw = f"{event['eventDate']}_{event['latitude']}_{event['longitude']}_{event['magnitude']}"
    return str(uuid.uuid5(uuid.NAMESPACE_URL, raw))

def generate_quake_hash(quake: dict) -> str:
    raw = f"{quake['date']}_{quake['latitude']}_{quake['longitude']}_{quake['magnitude']}"
    return hashlib.md5(raw.encode()).hexdigest()

def generate_map_image(lat: float, lon: float, quake_id: str) -> str | None:
    try:
        fig = plt.figure(figsize=(8, 6))
        ax = fig.add_subplot(1, 1, 1, projection=ccrs.PlateCarree())
        ax.set_extent([lon - 5, lon + 5, lat - 5, lat + 5], ccrs.PlateCarree())
        ax.add_feature(cfeature.LAND)
        ax.add_feature(cfeature.OCEAN)
        ax.add_feature(cfeature.COASTLINE)
        ax.add_feature(cfeature.BORDERS, linestyle=':')
        ax.plot(lon, lat, 'ro', markersize=10, transform=ccrs.PlateCarree())
        ax.text(lon + 0.5, lat, 'Deprem', transform=ccrs.PlateCarree(), fontsize=12)
        with tempfile.NamedTemporaryFile(delete=False, suffix='.png') as tmp:
            plt.savefig(tmp.name, bbox_inches='tight', dpi=100)
        plt.close(fig)
        return tmp.name
    except Exception as e:
        logging.error(f"Map generation error for {quake_id}: {e}")
        return None

def deactivate_user(chat_id: int):
    try:
        supabase.table("users").update({"active": False}).eq("chat_id", chat_id).execute()
        logging.info(f"Kullanıcı {chat_id} sohbeti silmiş veya botu engellemiş. Pasif yapıldı.")
    except Exception as e:
        logging.error(f"Kullanıcı {chat_id} pasifleştirme hatası: {e}")

def send_telegram_message(chat_id: int, text: str) -> bool:
    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            data={'chat_id': chat_id, 'text': text, 'parse_mode': 'HTML'},
            timeout=5
        )
        resp.raise_for_status()
        logging.info(f"Text message sent to {chat_id}")
        return True
    except requests.exceptions.HTTPError as e:
        if e.response.status_code in (403, 400):
            deactivate_user(chat_id)
        logging.error(f"Telegram send error (text) to {chat_id}: {e}")
        return False
    except Exception as e:
        logging.error(f"Telegram send error (text) to {chat_id}: {e}")
        return False


def send_telegram_photo(chat_id: int, image_path: str) -> bool:
    try:
        with open(image_path, 'rb') as img:
            resp = requests.post(
                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto",
                data={'chat_id': chat_id},
                files={'photo': img},
                timeout=5
            )
            resp.raise_for_status()
        logging.info(f"Photo sent to {chat_id}")
        return True
    except requests.exceptions.HTTPError as e:
        if e.response.status_code in (403, 400):
            deactivate_user(chat_id)
        logging.error(f"Telegram send error (photo) to {chat_id}: {e}")
        return False
    except Exception as e:
        logging.error(f"Telegram send error (photo) to {chat_id}: {e}")
        return False


def get_user_threshold(chat_id: int) -> float:
    try:
        res = supabase.table('users').select('threshold').eq('chat_id', chat_id).execute()
        return float(res.data[0]['threshold']) if res.data else 3.0
    except:
        return 3.0

def get_afad_data() -> list[dict]:
    url = 'https://deprem.afad.gov.tr/EventData/GetEventsByFilter'
    now = datetime.now(timezone.utc)
    payload = {
        "EventSearchFilterList": [
            {"FilterType": 9, "Value": now.strftime("%Y-%m-%dT%H:%M:%S.%fZ")},
            {"FilterType": 8, "Value": (now - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")}
        ],
        "Skip": 0,
        "Take": 50,
        "SortDescriptor": {"field": "eventDate", "dir": "desc"}
    }
    try:
        resp = requests.post(url, json=payload, headers={'Content-Type': 'application/json'}, timeout=10)
        resp.raise_for_status()
        events = resp.json().get("eventList", [])
        quakes = []
        for ev in events:
            if ev.get("magnitudeType") == "ML":
                quake_dt = datetime.strptime(ev["eventDate"], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
                quakes.append({
                    "earthquake_id": stable_id(ev),
                    "date": quake_dt.isoformat(),
                    "magnitude": float(ev["magnitude"]),
                    "latitude": float(ev["latitude"]),
                    "longitude": float(ev["longitude"]),
                    "city": extract_city(ev.get("location", "")),
                    "notified": False,
                    "created_at": now.isoformat(),
                    "source": "AFAD"
                })
        logging.info(f"Fetched {len(quakes)} quakes from AFAD")
        return quakes
    except Exception as e:
        logging.error(f"AFAD fetch error: {e}")
        return []

def get_kandilli_data() -> list[dict]:
    try:
        url = "http://www.koeri.boun.edu.tr/scripts/lst9.asp"
        response = requests.get(url, timeout=10)
        soup = bs4.BeautifulSoup(response.content, "html.parser")
        raw_text = soup.find_all("pre")[0].text

        lines = raw_text.strip().split("\n")
        clean_lines = [
            line for line in lines
            if line.strip() and not any(keyword in line.lower() for keyword in ["tarih", "--------", "analiz", "son 500"])
        ]

        quakes = []
        for line in clean_lines:
            try:
                parts = line.split()
                date_str, time_str = parts[0], parts[1]
                lat, lon = float(parts[2]), float(parts[3])
                magnitude = float(parts[6]) if parts[6].replace('.', '', 1).isdigit() else 0.0
                city = " ".join(parts[8:-1])
                date_time = datetime.strptime(date_str + " " + time_str, "%Y.%m.%d %H:%M:%S").replace(tzinfo=TURKEY_TZ).astimezone(timezone.utc)

                quake = {
                    "earthquake_id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"{date_time}_{lat}_{lon}_{magnitude}_K")),
                    "date": date_time.isoformat(),
                    "latitude": lat,
                    "longitude": lon,
                    "magnitude": magnitude,
                    "city": city.strip().upper(),
                    "notified": False,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "source": "KANDILLI"
                }
                quakes.append(quake)
            except Exception as e:
                continue

        logging.info(f"Fetched {len(quakes)} quakes from Kandilli")
        return quakes
    except Exception as e:
        logging.error(f"Kandilli fetch error: {e}")
        return []


def upsert_quakes(quakes: list[dict]):
    if not quakes:
        return
    try:
        existing_quakes = supabase.table('earthquakes').select('earthquake_id, date, latitude, longitude, magnitude').execute().data or []
        existing_hashes = {generate_quake_hash(q) for q in existing_quakes}
        new_quakes = [q for q in quakes if generate_quake_hash(q) not in existing_hashes]
        
        if new_quakes:
            supabase.table('earthquakes').upsert(new_quakes, on_conflict='earthquake_id').execute()
            logging.info(f"Upserted {len(new_quakes)} new quakes")
        else:
            logging.info("No new quakes to upsert")
    except Exception as e:
        logging.error(f"Upsert error: {e}")

def get_latest_notified_quake_info() -> tuple[dict | None, str | None]:
    try:
        latest_notified = supabase.table('earthquakes').select('earthquake_id, date, magnitude, latitude, longitude,source').eq('notified', True).order('date', desc=True).limit(1).execute().data or []
        if latest_notified:
            quake_info = latest_notified[0]
            quake_hash = generate_quake_hash(quake_info)
            logging.info(f"Last notified quake: ID={quake_info['earthquake_id']}, Date={quake_info['date']}, Hash={quake_hash}")
            return quake_info, quake_hash
        logging.info("No notified quakes yet")
        return None, None
    except Exception as e:
        logging.error(f"Error fetching latest notified quake info: {e}")
        return None, None

def notify_new():
    try:
        last_notified_info, last_notified_hash = get_latest_notified_quake_info()
        
        query = supabase.table('earthquakes').select('earthquake_id,date,magnitude,city,latitude,longitude,source').eq('notified', False).order('date', desc=True).limit(1)
        if last_notified_info:
            query = query.gt('date', last_notified_info['date'])
            
        pending = query.execute().data or []
        if not pending:
            logging.info("No new unnotified quake found")
            return

        quake = pending[0]
        quake_id = quake['earthquake_id']
        quake_hash = generate_quake_hash(quake)
        logging.info(f"Checking quake: ID={quake_id}, Date={quake['date']}, Magnitude={quake['magnitude']}, Hash={quake_hash}")

        if last_notified_hash and quake_hash == last_notified_hash:
            logging.warning(f"Quake {quake_id} already notified (same location, time, magnitude), skipping")
            supabase.table('earthquakes').update({'notified': True}).eq('earthquake_id', quake_id).execute()
            return

        quake_time = datetime.fromisoformat(quake['date']).astimezone(TURKEY_TZ)
        quake_magnitude = quake['magnitude']

        users = supabase.table('users').select('chat_id,threshold').eq('active', True).execute().data or []
        if not users:
            logging.info("No active users found")
            return

        img_path = None
        notification_sent = False
        for usr in users:
            if quake_magnitude >= float(usr['threshold']):
                if not img_path:
                    img_path = generate_map_image(quake['latitude'], quake['longitude'], quake_id)
                source = quake.get("source", "Bilinmiyor")
                text = (
                    f"⚠️ Yeni Deprem!\n"
                    f"Tarih: {quake_time.strftime('%d.%m.%Y %H:%M')}\n"
                    f"Büyüklük: M{quake_magnitude:.1f}\n"
                    f"Konum: {quake['city'] or 'Bilinmiyor'}\n"
                    f"Kaynak: {source}"
                )
                send_success = send_telegram_message(usr['chat_id'], text)
                if send_success and img_path:
                    send_telegram_photo(usr['chat_id'], img_path)
                notification_sent = True

        if notification_sent:
            supabase.table('earthquakes').update({'notified': True}).eq('earthquake_id', quake_id).execute()
            logging.info(f"Quake {quake_id} (M{quake_magnitude:.1f}) notified")
        else:
            logging.info(f"Quake {quake_id} (M{quake_magnitude:.1f}) below user thresholds")

        if img_path:
            try:
                os.unlink(img_path)
                logging.info(f"Temporary map file {img_path} deleted")
            except:
                pass

    except Exception as e:
        logging.error(f"Notification error: {e}")

def get_quakes(threshold: float=None, limit: int=1, last_24h: bool=False, biggest: bool=False) -> list[dict]:
    q = supabase.table('earthquakes').select('earthquake_id,date,magnitude,city,latitude,longitude,source')
    if threshold is not None:
        q = q.gte('magnitude', threshold)
    if last_24h:
        since = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        q = q.gte('date', since)
    if biggest:
        q = q.order('magnitude', desc=True).limit(1)
    else:
        q = q.order('date', desc=True).limit(limit)
    return q.execute().data or []

def handle_telegram_updates():
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates"
    offset = None
    while True:
        try:
            resp = requests.get(url, params={"timeout": 60, "offset": offset}, timeout=65)
            resp.raise_for_status()
            for upd in resp.json().get("result", []):
                offset = upd["update_id"] + 1
                text = upd.get("message", {}).get("text", "")
                chat_id = upd.get("message", {}).get("chat", {}).get("id")
                if not chat_id or not text:
                    continue

                if text == "/start":
                    try:
                        existing = supabase.table('users').select('threshold').eq('chat_id', chat_id).execute()

                        if existing.data:
                            supabase.table('users').update({"active": True}).eq("chat_id", chat_id).execute()
                            send_telegram_message(chat_id, "🔔 Deprem bildirimlerine yeniden abone oldunuz!")
                            logging.info(f"Kullanıcı yeniden aktif oldu: {chat_id}")
                        else:
                            supabase.table('users').insert({"chat_id": chat_id, "active": True, "threshold": 3.0}).execute()
                            send_telegram_message(chat_id, "✅ Deprem bildirimlerine abone oldunuz!\nVarsayılan eşik: M3.0\nEşiği değiştirmek için: /deprem <sınır>")
                            logging.info(f"Yeni abone eklendi: {chat_id}")
                    except Exception as e:
                        logging.error(f"/start komutu hatası: {e}")
                        send_telegram_message(chat_id, "⚠️ Abonelik işlemi sırasında bir hata oluştu. Lütfen tekrar deneyin.")


                elif text == "/stop":
                    supabase.table('users').update({"active": False}).eq("chat_id", chat_id).execute()
                    send_telegram_message(chat_id, "Deprem bildirimlerinden çıktınız")
                    logging.info(f"Unsubscribed: {chat_id}")

                elif text.startswith("/deprem"):
                    parts = text.split()
                    if len(parts) != 2:
                        send_telegram_message(chat_id, "Geçerli bir eşik girin: /deprem <sınır>")
                        continue
                    try:
                        th = float(parts[1])
                        if th < 0:
                            raise ValueError
                    except:
                        send_telegram_message(chat_id, "Geçerli bir sayı girin: /deprem <sınır>")
                        continue
                    supabase.table('users').upsert({"chat_id": chat_id, "active": True, "threshold": th}).execute()
                    send_telegram_message(chat_id, f"Deprem eşiğiniz M{th:.1f} olarak güncellendi")
                    logging.info(f"Threshold updated for {chat_id}: {th}")

                    latest = get_quakes(threshold=th, limit=1)
                    if latest:
                        q = latest[0]
                        qt = datetime.fromisoformat(q['date']).astimezone(TURKEY_TZ)
                        source = q.get('source', 'Bilinmiyor')
                        msg = (
                            f"⚠️ Son Deprem!\n"
                            f"Tarih: {qt.strftime('%d.%m.%Y %H:%M')}\n"
                            f"Büyüklük: M{q['magnitude']:.1f}\n"
                            f"Konum: {q['city'] or 'Bilinmiyor'}\n"
                            f"Kaynak: {source}"
                        )
                        send_telegram_message(chat_id, msg)
                        img = generate_map_image(q['latitude'], q['longitude'], q['earthquake_id'])
                        if img:
                            send_telegram_photo(chat_id, img)
                            try:
                                os.unlink(img)
                            except:
                                pass

                elif text == "/last":
                    th = get_user_threshold(chat_id)
                    last5 = get_quakes(threshold=th, limit=5)
                    if last5:
                        msg = f"⚠️ Son 5 Deprem (Eşik: M{th:.1f}):\n\n"
                        for i, q in enumerate(last5, 1):
                            qt = datetime.fromisoformat(q['date']).astimezone(TURKEY_TZ)
                            msg += (
                            f"{i}. Tarih     : {qt.strftime('%d.%m.%Y %H:%M')}\n"
                            f"   Büyüklük  : M{q['magnitude']:.1f}\n"
                            f"   Konum     : {q['city'].strip() if q['city'] else 'Bilinmiyor'}\n"
                            f"   Kaynak    : {q['source']}\n\n"
                        )

                        send_telegram_message(chat_id, msg.strip())
                    else:
                        send_telegram_message(chat_id, f"M{th:.1f} üzeri son 5 deprem bulunamadı")

                elif text == "/biggest":
                    th = get_user_threshold(chat_id)
                    big = get_quakes(threshold=th, last_24h=True, biggest=True)
                    if big:
                        q = big[0]
                        qt = datetime.fromisoformat(q['date']).astimezone(TURKEY_TZ)
                        msg = (
                        f"⚠️ Son 24 Saatin En Büyük Depremi (Eşik: M{th:.1f})!\n"
                        f"Tarih: {qt.strftime('%d.%m.%Y %H:%M')}\n"
                        f"Büyüklük: M{q['magnitude']:.1f}\n"
                        f"Konum: {q['city'] or 'Bilinmiyor'}\n"
                        f"Kaynak: {q.get('source', 'Bilinmiyor')}"
                        )
                        send_telegram_message(chat_id, msg)
                        img = generate_map_image(q['latitude'], q['longitude'], q['earthquake_id'])
                        if img:
                            send_telegram_photo(chat_id, img)
                            try:
                                os.unlink(img)
                            except:
                                pass
                    else:
                        send_telegram_message(chat_id, f"Son 24 saatte M{th:.1f} üzeri deprem bulunamadı")

        except Exception as e:
            logging.error(f"Telegram update loop error: {e}")
            time.sleep(10)

def main_loop():
    if not TELEGRAM_TOKEN:
        logging.error("Telegram token missing")
        return
    threading.Thread(target=handle_telegram_updates, daemon=True).start()
    logging.info("Telegram handler started")
    while True:
        logging.info("Main loop iteration: fetching & notifying")
        quakes_afad = get_afad_data()
        quakes_kandilli = get_kandilli_data()
        all_quakes = quakes_afad + quakes_kandilli
        upsert_quakes(all_quakes)
        notify_new()
        time.sleep(20)

if __name__ == "__main__":
    try:
        main_loop()
    except KeyboardInterrupt:
        logging.info("Program durduruldu")
    except Exception as e:
        logging.error(f"Critical error: {e}")
