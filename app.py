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

def send_telegram_message(chat_id: int, text: str) -> bool:
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            data={'chat_id': chat_id, 'text': text, 'parse_mode': 'HTML'},
            timeout=5
        ).raise_for_status()
        logging.info(f"Text message sent to {chat_id}")
        return True
    except Exception as e:
        logging.error(f"Telegram send error (text) to {chat_id}: {e}")
        return False

def send_telegram_photo(chat_id: int, image_path: str) -> bool:
    try:
        with open(image_path, 'rb') as img:
            requests.post(
                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto",
                data={'chat_id': chat_id},
                files={'photo': img},
                timeout=5
            ).raise_for_status()
        logging.info(f"Photo sent to {chat_id}")
        return True
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
                    "created_at": now.isoformat()
                })
        logging.info(f"Fetched {len(quakes)} quakes from AFAD")
        return quakes
    except Exception as e:
        logging.error(f"AFAD fetch error: {e}")
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
        latest_notified = supabase.table('earthquakes').select('earthquake_id, date, magnitude, latitude, longitude').eq('notified', True).order('date', desc=True).limit(1).execute().data or []
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
        
        query = supabase.table('earthquakes').select('earthquake_id,date,magnitude,city,latitude,longitude').eq('notified', False).order('date', desc=True).limit(1)
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
                text = f"⚠️ Yeni Deprem!\nTarih: {quake_time.strftime('%d.%m.%Y %H:%M')}\nBüyüklük: M{quake_magnitude:.1f}\nKonum: {quake['city'] or 'Bilinmiyor'}"
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
    q = supabase.table('earthquakes').select('earthquake_id,date,magnitude,city,latitude,longitude')
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
                    supabase.table('users').upsert({"chat_id": chat_id, "active": True, "threshold": 3.0}).execute()
                    send_telegram_message(chat_id, "Deprem bildirimlerine abone oldunuz! Varsayılan eşik: M3.0\nEşiği değiştirmek için: /deprem <sınır>")
                    logging.info(f"New subscriber: {chat_id}")

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
                        msg = f"⚠️ Son Deprem!\nTarih: {qt.strftime('%d.%m.%Y %H:%M')}\nBüyüklük: M{q['magnitude']:.1f}\nKonum: {q['city'] or 'Bilinmiyor'}"
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
                            msg += f"{i}. Tarih: {qt.strftime('%d.%m.%Y %H:%M')}\n   Büyüklük: M{q['magnitude']:.1f}\n   Konum: {q['city'] or 'Bilinmiyor'}\n\n"
                        send_telegram_message(chat_id, msg.strip())
                    else:
                        send_telegram_message(chat_id, f"M{th:.1f} üzeri son 5 deprem bulunamadı")

                elif text == "/biggest":
                    th = get_user_threshold(chat_id)
                    big = get_quakes(threshold=th, last_24h=True, biggest=True)
                    if big:
                        q = big[0]
                        qt = datetime.fromisoformat(q['date']).astimezone(TURKEY_TZ)
                        msg = f"⚠️ Son 24 Saatin En Büyük Depremi (Eşik: M{th:.1f})!\nTarih: {qt.strftime('%d.%m.%Y %H:%M')}\nBüyüklük: M{q['magnitude']:.1f}\nKonum: {q['city'] or 'Bilinmiyor'}"
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
        quakes = get_afad_data()
        upsert_quakes(quakes)
        notify_new()
        time.sleep(20)

if __name__ == "__main__":
    try:
        main_loop()
    except KeyboardInterrupt:
        logging.info("Program durduruldu")
    except Exception as e:
        logging.error(f"Critical error: {e}")