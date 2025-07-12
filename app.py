# --- START OF FILE app.py (MODÜL BAZLI LİSANS GÜNCELLENMİŞ HALİ) ---

import uuid
from flask import Flask, request, jsonify,send_from_directory
from flask_sqlalchemy import SQLAlchemy
from flask_cors import CORS
from operator import attrgetter # Bunu importların en başına ekle
# models.py içinden AVAILABLE_MODULES ve MODULE_KEYS'in de import edildiğini varsayıyoruz.
# Eğer models.py'de bu tanımlar yoksa, ya oraya ekleyin ya da buraya manuel kopyalayın.
from models import (
    Alarm, db, Reseller, Customer, Camera, ApprovalRequest,
    AVAILABLE_MODULES, MODULE_KEYS # Bu satır önemli!
)
from datetime import datetime, timedelta, timezone # datetime.timezone UTC için
from werkzeug.security import check_password_hash, generate_password_hash
from sqlalchemy import func, extract, Date, cast
from flask_migrate import Migrate
from sqlalchemy.orm import aliased, joinedload
from collections import defaultdict
import calendar
import json
import cv2
import base64
import os
import random
import string
import requests
# from PIL import Image # base64 decode için Pillow.Image'a gerek yok, BytesIO yeterli
from io import BytesIO
import pytz # Türkiye saati için

TR_TIMEZONE = pytz.timezone('Europe/Istanbul') # Alarm.to_json() içinde kullanılıyor
UTC_TIMEZONE = pytz.utc # datetime.now(timezone.utc) daha modern
from flask_mail import Mail, Message # Yorumu kaldır

app = Flask(__name__)
app.config['MAIL_SERVER'] = 'smtp.gmail.com' # Örnek: Gmail SMTP sunucusu
app.config['MAIL_PORT'] = 587                # Veya 465 (SSL için)
app.config['MAIL_USE_TLS'] = True            # TLS için True, SSL için False
app.config['MAIL_USE_SSL'] = False           # TLS kullanıyorsan bu False olmalı
app.config['MAIL_USERNAME'] = 'zubatgo0@gmail.com' # Environment variable'dan oku
app.config['MAIL_PASSWORD'] = str("dduemvskmsncrkqb") # Environment variable'dan oku
app.config['MAIL_DEFAULT_SENDER'] = ('Alarm Merkezi Bildirim', 'zubatgo0@gmail.com') # Gönderen adı ve e-postası
app.config['ONE_SIGNAL_APP_ID'] = os.environ.get('ONE_SIGNAL_APP_ID', '5f984948-efe9-4973-a697-a99374b9b491')
app.config['ONE_SIGNAL_API_KEY'] = os.environ.get('ONE_SIGNAL_REST_API_KEY', 'os_v2_app_l6messhp5fexhjuxvgjxjonusgngnlj4767exqndajtwo6bgswmgsugvg2ht3q2w5e3txv3wlebou55y6dibzlwg6pro2fd66q45esy')



app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///alarm_merkezi.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

ADMIN_EMAIL = os.environ.get('ADMIN_EMAIL', 'admin@alarmmerkezi.com')
ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD', 'Alarm123!') # Üretimde hashlenmeli!

migrate = Migrate(app, db)

db.init_app(app)
CORS(app) # Tüm domainlerden erişime izin verir, üretimde kısıtlanabilir



mail = Mail(app) # Flask-Mail'i başlat (Yorumu kaldır)



with app.app_context():
    db.create_all()
    print("Veritabanı tabloları oluşturuldu (eğer yoksa).")

    if not Reseller.query.filter_by(email='admin@bayi.com').first():
        print("Test bayisi oluşturuluyor...")
        hashed_password_for_test_reseller = generate_password_hash('password123')
        test_reseller = Reseller(
            name='Test Bayisi A.Ş.',
            email='admin@bayi.com',
            password_hash=hashed_password_for_test_reseller, # Hashlenmiş şifre
            phone='5551234567',
            status='Active',
            # licenses=100, # ESKİ LİSANS ALANI KALDIRILDI
            join_date=datetime.now(timezone.utc).strftime('%Y-%m-%d')
        )
        # Her modül için varsayılan 50 lisans ata (module_licenses setter'ı ile)
        initial_licenses = {key: 50 for key in MODULE_KEYS}
        test_reseller.module_licenses = initial_licenses

        db.session.add(test_reseller)
        db.session.commit()
        print(f"Test Bayisi '{test_reseller.name}' başarıyla oluşturuldu. Modül lisansları: {test_reseller.module_licenses}")

# === API Endpoint'leri ===

UPLOAD_FOLDER = "cdn_images"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
MAX_FILE_SIZE_KB = 100 # Örnek 100KB limit

@app.route('/customers/<int:customer_id>/cameras/indexed', methods=['GET'])
def get_customer_cameras_with_sequential_indices(customer_id):
    """
    Belirli bir müşterinin TÜM kameralarını veritabanı ID'lerine göre sıralar
    ve 1'den başlayan sıralı bir 'customer_camera_index' alanı ekleyerek döndürür.
    """
    # 1. Müşteriyi bul, bulunamazsa 404 hatası döndür.
    customer = Customer.query.get_or_404(customer_id)

    # 2. Müşterinin tüm kameralarını al ve veritabanındaki orijinal ID'lerine göre
    #    küçükten büyüğe doğru sırala. Bu, her zaman tutarlı bir sıra elde etmemizi sağlar.
    sorted_cameras = sorted(customer.cameras_rel, key=lambda cam: cam.id)

    # 3. Sıralanmış kameralar listesi üzerinde dönerek yeni bir liste oluştur.
    #    Her bir kameranın bilgisine 'customer_camera_index' alanını ekle.
    indexed_cameras_list = []
    for index, camera in enumerate(sorted_cameras, 1): # enumerate'e 1 vererek sayacı 1'den başlat
        # Kameranın mevcut JSON verisini al
        camera_data = camera.to_json()
        # Yeni sıralı numaramızı ekle
        camera_data['customer_camera_index'] = index
        indexed_cameras_list.append(camera_data)

    # 4. Yeni oluşturulan listeyi JSON olarak döndür.
    return jsonify(indexed_cameras_list), 200


@app.route('/customers/<int:customer_id>/save-player-id', methods=['POST'])
def save_player_id(customer_id):
    # Gerçek uygulamada bu customer_id'nin giriş yapan kullanıcıya ait olduğu
    # bir JWT veya session ile doğrulanmalıdır.
    customer = Customer.query.get_or_404(customer_id)
    data = request.get_json()
    
    player_id = data.get('player_id')
    if not player_id:
        return jsonify({"error": "player_id eksik."}), 400

    # Mevcut ID'leri yükle
    existing_ids = []
    if customer.onesignal_player_ids:
        try:
            existing_ids = json.loads(customer.onesignal_player_ids)
        except json.JSONDecodeError:
            existing_ids = []
            
    # Yeni ID zaten listede yoksa ekle
    if player_id not in existing_ids:
        existing_ids.append(player_id)
        customer.onesignal_player_ids = json.dumps(existing_ids)
        
        try:
            db.session.commit()
            return jsonify({"message": "Player ID başarıyla kaydedildi.", "player_ids": existing_ids}), 200
        except Exception as e:
            db.session.rollback()
            return jsonify({"error": "Veritabanına kaydederken hata oluştu.", "details": str(e)}), 500
    
    return jsonify({"message": "Bu Player ID zaten kayıtlı.", "player_ids": existing_ids}), 200

def send_onesignal_notification(player_ids, heading, content, data=None, image_url=None):
    """
    Belirtilen Player ID'lere OneSignal üzerinden anlık bildirim gönderir.
    
    :param player_ids: Bildirim gönderilecek cihazların ID listesi.
    :param heading: Bildirimin başlığı.
    :param content: Bildirimin içeriği.
    :param data: Bildirimle birlikte gönderilecek ek veri (örn: {'alarm_id': 123}).
    :param image_url: Bildirimde gösterilecek büyük resim URL'si.
    """
    if not player_ids or not isinstance(player_ids, list):
        print("[OneSignal] Hata: Geçerli player_ids listesi sağlanmadı.")
        return

    onesignal_app_id = app.config.get('ONE_SIGNAL_APP_ID')
    onesignal_api_key = app.config.get('ONE_SIGNAL_API_KEY')
    
    if not onesignal_app_id or not onesignal_api_key or 'YOUR_ONESIGNAL' in onesignal_app_id:
        print("[OneSignal] Hata: OneSignal APP ID veya API Key yapılandırılmamış.")
        return

    headers = {
        "accept": "application/json",
        "Authorization": f"Basic {onesignal_api_key}",
        "content-type": "application/json"
    }
    
    payload = {
        "app_id": onesignal_app_id,
        "include_player_ids": player_ids,
        "headings": {"en": heading}, # Birden fazla dil desteklenebilir
        "contents": {"en": content}
    }

    # Eğer ek veri varsa payload'a ekle
    if data and isinstance(data, dict):
        payload['data'] = data
        # Web push için, tıklandığında gidilecek URL'yi de ayarlayabiliriz.
        # Örneğin alarm detay sayfasına yönlendirme.
        if 'alarm_id' in data:
            payload['web_url'] = f"https://alarmmerkezibackend.online/alarms/{data['alarm_id']}"
            
    # Eğer resim URL'si varsa, büyük resim olarak ekle
    if image_url:
        payload['big_picture'] = image_url
        # iOS için de ekleyelim
        payload['ios_attachments'] = {"id1": image_url}

    try:
        response = requests.post(
            "https://onesignal.com/api/v1/notifications", 
            headers=headers, 
            json=payload
        )
        response.raise_for_status() # HTTP 2xx dışında bir kod varsa hata fırlatır
        print(f"[OneSignal] Bildirim başarıyla gönderildi. Yanıt: {response.json()}")
    except requests.exceptions.RequestException as e:
        print(f"[OneSignal] Bildirim gönderilirken HATA oluştu: {e}")
        if e.response:
            print(f"[OneSignal] Hata Detayı: {e.response.text}")

def generate_verification_code(length=6):
    """Rastgele N haneli sayısal doğrulama kodu üretir."""
    return "".join(random.choices(string.digits, k=length))

@app.route('/installation-requests/<int:request_id>/send-verification-email', methods=['POST'])
def send_verification_email_for_request_endpoint(request_id):
    approval_request = ApprovalRequest.query.get_or_404(request_id)

    if approval_request.status != 'Pending':
        return jsonify({"error": "Bu talep zaten işlenmiş veya e-postası doğrulanmış."}), 400
    
    if approval_request.email_verified:
        return jsonify({"message": "Bu müşterinin e-postası zaten doğrulanmış."}), 200

    # Doğrulama kodu üret
    code = generate_verification_code()
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(minutes=15)

    approval_request.email_verification_code = generate_password_hash(code)
    approval_request.email_verification_code_sent_at = now
    approval_request.email_verification_code_expires_at = expires_at

    try:
        # ✅ Gerçek e-posta gönderimi
        msg = Message(
            subject="Alarm Merkezi E-posta Doğrulama Kodunuz",
            recipients=[approval_request.customer_email],
            html=f"""
                <p>Merhaba <strong>{approval_request.customer_name}</strong>,</p>
                <p>Kurulum talebiniz için e-posta doğrulama kodunuz:</p>
                <h2>{code}</h2>
                <p>Bu kod 15 dakika boyunca geçerlidir.</p>
                <p>Eğer bu talebi siz oluşturmadıysanız, bu e-postayı yok sayabilirsiniz.</p>
                <br>
                <p>Teşekkürler,<br><strong>Alarm Merkezi Ekibi</strong></p>
            """
        )
        mail.send(msg)

        db.session.commit()
        return jsonify({"message": f"{approval_request.customer_email} adresine doğrulama kodu gönderildi."}), 200
    except Exception as e:
        db.session.rollback()
        print(f"E-posta gönderme hatası: {str(e)}")
        return jsonify({"error": "Doğrulama kodu gönderilirken bir hata oluştu.", "details": str(e)}), 500


@app.route('/installation-requests/<int:request_id>/verify-email-code', methods=['POST'])
def verify_email_code_for_request_endpoint(request_id):
    approval_request = ApprovalRequest.query.get_or_404(request_id)
    data = request.get_json()

    if not data or 'code' not in data:
        return jsonify({"error": "Doğrulama kodu eksik."}), 400
    
    submitted_code = data['code']

    if approval_request.email_verified:
        return jsonify({"message": "E-posta zaten doğrulanmış.", "email_verified": True}), 200

    # --- DÜZELTİLECEK KISIM BURASI ---
    if not approval_request.email_verification_code or not approval_request.email_verification_code_expires_at:
        return jsonify({"error": "Doğrulama kodu süresi dolmuş veya hiç gönderilmemiş. Lütfen yeni kod isteyin."}), 400

    # 1. Veritabanından gelen "naive" datetime'ı al.
    expires_at_naive = approval_request.email_verification_code_expires_at
    
    # 2. Bu naive datetime'ı "aware" UTC datetime'ına dönüştür.
    expires_at_aware = UTC_TIMEZONE.localize(expires_at_naive)

    # 3. Şimdi "aware" olan iki datetime objesini karşılaştır.
    if datetime.now(UTC_TIMEZONE) > expires_at_aware:
         return jsonify({"error": "Doğrulama kodunun süresi dolmuş. Lütfen yeni kod isteyin."}), 400
    # --- DÜZELTME BİTTİ ---

    if check_password_hash(approval_request.email_verification_code, submitted_code):
        approval_request.email_verified = True
        approval_request.email_verification_code = None
        approval_request.email_verification_code_expires_at = None
        db.session.commit()
        return jsonify({"message": "E-posta başarıyla doğrulandı.", "email_verified": True}), 200
    else:
        return jsonify({"error": "Geçersiz doğrulama kodu."}), 400

def compress_image(image_bytes: bytes, max_kb: int = 100) -> bytes:
    """Verilen image bytes'larını PIL ile açıp max_kb altına sıkıştırır ve bytes döner."""
    from PIL import Image # Fonksiyon içinde import
    img = Image.open(BytesIO(image_bytes)).convert("RGB")
    
    # Gerekirse yeniden boyutlandır (opsiyonel, kaliteyi daha çok düşürebilir)
    # img.thumbnail((1280, 1280)) # Örneğin max 1280px

    quality = 90 # Başlangıç kalitesi
    step = 5
    buffer = BytesIO()

    while True:
        buffer.seek(0)
        buffer.truncate() # Buffer'ı her denemede temizle
        img.save(buffer, format="JPEG", quality=quality, optimize=True) # optimize=True eklendi
        size_kb = buffer.tell() / 1024

        if size_kb <= max_kb or quality <= 10: # Kalite çok düşerse dur
            break
        quality -= step
        if quality < 10: # Minimum kalite sınırı
            quality = 10

    return buffer.getvalue()

@app.route("/upload", methods=["POST"])
def upload_image():
    data = request.json
    if not data or "image" not in data:
        return jsonify({"error": "Missing 'image' field"}), 400

    try:
        base64_data = data["image"]
        # Base64 başlığını temizle (eğer varsa: "data:image/jpeg;base64,")
        if ',' in base64_data:
            base64_data = base64_data.split(',', 1)[1]
            
        image_data_bytes = base64.b64decode(base64_data)

        # Sıkıştır
        compressed_bytes = compress_image(image_data_bytes, MAX_FILE_SIZE_KB)

        filename = f"{uuid.uuid4().hex}.jpg"
        filepath = os.path.join(UPLOAD_FOLDER, filename)

        with open(filepath, "wb") as f:
            f.write(compressed_bytes)

        # Tam dosya yolu (frontend için gerekmeyebilir, sadece URL yeterli)
        # absolute_path = os.path.abspath(filepath)

        return jsonify({
            "message": "Image uploaded successfully",
            "filename": filename,
            "url": f"/cdn/{filename}", # Frontend bu URL'i kullanacak
            # "full_path": absolute_path
        }), 200 # Başarılı yükleme için 200 OK
    except base64.binascii.Error:
        return jsonify({"error": "Invalid base64 string"}), 400
    except Exception as e:
        # Daha iyi loglama için traceback
        import traceback
        print(f"Image upload error: {traceback.format_exc()}")
        return jsonify({"error": f"Error processing image: {str(e)}"}), 500

@app.route('/cdn/<path:filename>')
def serve_cdn_image(filename):
    try:
        return send_from_directory(UPLOAD_FOLDER, filename)
    except FileNotFoundError:
        return jsonify({"error": "File not found"}), 404

# --- Dashboard İstatistikleri (DEĞİŞİKLİK YOK) ---
@app.route('/dashboard/stats/hourly-detections', methods=['GET'])
def get_hourly_detections():
    customer_id = request.args.get('customerId', type=int)
    today = datetime.now(timezone.utc).date() # Aware datetime
    query = db.session.query(
        extract('hour', Alarm.timestamp).label('hour'),
        func.count(Alarm.id).label('value')
    ).filter(
        cast(Alarm.timestamp, Date) == today # SQLite için cast gerekebilir
    )
    if customer_id:
        customer = Customer.query.get(customer_id)
        if not customer:
            return jsonify({"error": "Müşteri bulunamadı"}), 404
        query = query.filter(Alarm.customer_id == customer_id)
    results = query.group_by(extract('hour', Alarm.timestamp)).order_by('hour').all()
    hourly_data = [{"hour": str(r.hour).zfill(2), "value": r.value} for r in results]
    all_hours = {str(h).zfill(2): 0 for h in range(24)}
    for item in hourly_data:
        all_hours[item['hour']] = item['value']
    final_data = [{"hour": h, "value": v} for h, v in sorted(all_hours.items())]
    return jsonify(final_data), 200

@app.route('/dashboard/stats/camera-detections', methods=['GET'])
def get_camera_detections():
    customer_id = request.args.get('customerId', type=int)
    if not customer_id:
        return jsonify({"error": "customerId gereklidir"}), 400
    customer = Customer.query.get(customer_id)
    if not customer:
        return jsonify({"error": "Müşteri bulunamadı"}), 404
    results = db.session.query(
        Camera.name.label('camera_display_name'),
        Camera.id.label('camera_id_val'),
        func.count(Alarm.id).label('value')
    ).join(Alarm, Camera.id == Alarm.camera_id)\
     .filter(Alarm.customer_id == customer_id)\
     .group_by(Camera.name, Camera.id)\
     .order_by(func.count(Alarm.id).desc())\
     .all()
    camera_data = [{"name": r.camera_display_name if r.camera_display_name else f"Kamera ID: {r.camera_id_val}", "value": r.value} for r in results]
    colors = ['#3B82F6', '#4F46E5', '#8B5CF6', '#EC4899', '#F97316', '#EF4444', '#10B981']
    for i, item in enumerate(camera_data):
        item['color'] = colors[i % len(colors)]
    return jsonify(camera_data), 200

@app.route('/dashboard/stats/module-detections', methods=['GET'])
def get_module_detections():
    customer_id = request.args.get('customerId', type=int)
    if not customer_id:
        return jsonify({"error": "customerId gereklidir"}), 400
    customer = Customer.query.get(customer_id)
    if not customer:
        return jsonify({"error": "Müşteri bulunamadı"}), 404
    results = db.session.query(
        Alarm.module_name.label('name'), # Alarm.module_name 'person_detection' gibi key tutar
        func.count(Alarm.id).label('value')
    ).filter(Alarm.customer_id == customer_id)\
     .group_by(Alarm.module_name)\
     .order_by(func.count(Alarm.id).desc())\
     .all()
    # Modül adını kullanıcı dostu hale getir
    module_data = [{"name": AVAILABLE_MODULES.get(r.name, r.name if r.name else "Bilinmeyen Modül"), "value": r.value} for r in results]
    colors = ['#4F46E5', '#F97316', '#3B82F6', '#EC4899', '#EF4444', '#10B981', '#8B5CF6']
    for i, item in enumerate(module_data):
        item['color'] = colors[i % len(colors)]
    return jsonify(module_data), 200

@app.route('/dashboard/stats/category-detections', methods=['GET'])
def get_category_detections():
    customer_id = request.args.get('customerId', type=int)
    if not customer_id: return jsonify({"error": "customerId gereklidir"}), 400
    customer = Customer.query.get(customer_id)
    if not customer: return jsonify({"error": "Müşteri bulunamadı"}), 404
    results = db.session.query(
        Alarm.category.label('name'),
        func.count(Alarm.id).label('value')
    ).filter(Alarm.customer_id == customer_id)\
     .group_by(Alarm.category)\
     .order_by(func.count(Alarm.id).desc()).all()
    category_data = [{"name": r.name if r.name else "Bilinmeyen Kategori", "value": r.value} for r in results]
    colors = ['#EF4444', '#F97316', '#4F46E5', '#3B82F6', '#EC4899', '#10B981', '#8B5CF6']
    for i, item in enumerate(category_data): item['color'] = colors[i % len(colors)]
    return jsonify(category_data), 200

@app.route('/dashboard/stats/monthly-detections', methods=['GET'])
def get_monthly_detections():
    customer_id = request.args.get('customerId', type=int)
    year = request.args.get('year', type=int, default=datetime.now(timezone.utc).year)
    if not customer_id: return jsonify({"error": "customerId gereklidir"}), 400
    customer = Customer.query.get(customer_id)
    if not customer: return jsonify({"error": "Müşteri bulunamadı"}), 404
    results = db.session.query(
        extract('month', Alarm.timestamp).label('month_num'),
        func.count(Alarm.id).label('value')
    ).filter(Alarm.customer_id == customer_id)\
     .filter(extract('year', Alarm.timestamp) == year)\
     .group_by(extract('month', Alarm.timestamp)).order_by('month_num').all()
    monthly_counts = {month_num: 0 for month_num in range(1, 13)}
    for r in results: monthly_counts[r.month_num] = r.value
    tr_month_names = ["", "Oca", "Şub", "Mar", "Nis", "May", "Haz", "Tem", "Ağu", "Eyl", "Eki", "Kas", "Ara"] # Kısaltılmış
    monthly_data = [{"month": tr_month_names[mn], "value": c} for mn, c in monthly_counts.items()]
    return jsonify(monthly_data), 200

# --- Alarm Listeleme (Müşteri ve Bayi için - DEĞİŞİKLİK YOK) ---
@app.route('/customers/<int:customer_id>/alarms', methods=['GET'])
def get_customer_alarms(customer_id):
    customer = Customer.query.get(customer_id)
    if not customer: return jsonify({"error": "Müşteri bulunamadı"}), 404
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 25, type=int)
    camera_id_filter = request.args.get('cameraId', type=int)
    query = Alarm.query.filter_by(customer_id=customer_id)
    if camera_id_filter:
        query = query.filter_by(camera_id=camera_id_filter)
        if not Camera.query.filter_by(id=camera_id_filter, customer_id=customer_id).first():
            return jsonify({"error": "Kamera bu müşteriye ait değil veya bulunamadı"}), 404
    query = query.order_by(Alarm.timestamp.desc())
    paginated_alarms = query.paginate(page=page, per_page=per_page, error_out=False)
    return jsonify({
        'alarms': [alarm.to_json() for alarm in paginated_alarms.items],
        'pagination': {'currentPage': paginated_alarms.page, 'totalPages': paginated_alarms.pages,
                       'totalItems': paginated_alarms.total, 'hasNext': paginated_alarms.has_next,
                       'hasPrev': paginated_alarms.has_prev}
    }), 200

@app.route('/resellers/<int:reseller_id>/alarms', methods=['GET']) # Bu genel bayi alarm listesi, sayfalama ve filtreleme eklenebilir.
def get_reseller_customer_alarms(reseller_id):
    reseller = Reseller.query.get(reseller_id)
    if not reseller: return jsonify({"error": "Bayi bulunamadı"}), 404
    customer_ids = [c.id for c in reseller.customers_rel]
    # Sayfalama ve daha fazla filtre eklenmeli (müşteri, tarih aralığı vb.)
    alarms = Alarm.query.filter(Alarm.customer_id.in_(customer_ids)).order_by(Alarm.timestamp.desc()).limit(100).all() # Örnek limit
    return jsonify([alarm.to_json() for alarm in alarms]), 200

# --- Test Alarmı Oluşturma ---
@app.route('/alarms/test-create', methods=['POST'])
def create_test_alarm():
    data = request.get_json()
    try:
        customer = Customer.query.get(data.get('customerId'))
        camera = Camera.query.get(data.get('cameraId'))
        if not customer or not camera:
            return jsonify({"error": "Customer or Camera not found"}), 404

        new_alarm = Alarm(
            customer_id=customer.id,
            camera_id=camera.id,
            alarm_type=data.get('alarmType', 'Test Alarmı'),
            category=data.get('category', 'Critical'),
            event_details=data.get('event', 'test_event_details'),
            module_name=data.get('module', camera.assigned_module or 'Test_Modul'), # Kameranın modülünü kullan
            # timestamp için: ya frontend TR saati gönderip burada UTC'ye çevirin ya da direkt UTC gönderin.
            # Model default'u UTC, bu yüzden UTC en iyisi.
            timestamp=datetime.now(timezone.utc), # Test için şimdiki UTC zamanı
            image_url=data.get('imageUrl', 'https://via.placeholder.com/640x480.png?text=Test+Alarm+Image')
        )
        # Eğer frontend'den TR saatli string geliyorsa:
        # if data.get('datetime_tr'):
        #     try:
        #         tr_dt = TR_TIMEZONE.localize(datetime.strptime(data.get('datetime_tr'), '%d.%m.%Y %H:%M:%S'))
        #         new_alarm.timestamp = tr_dt.astimezone(pytz.utc)
        #     except ValueError:
        #         pass # Hatalı formatta ise default UTC kullanılır

        db.session.add(new_alarm)
        db.session.commit()

        if customer.onesignal_player_ids:
            try:
                player_ids = json.loads(customer.onesignal_player_ids)
                if player_ids:
                    baslik = f"🚨 {new_alarm.alarm_type}!"
                    icerik = f"{customer.name} adlı müşterinin {camera.name} kamerasında bir alarm tetiklendi."
                    
                    # Bildirime tıklanınca alarm detayını açabilmek için veri gönderelim
                    alarm_data = {"alarm_id": new_alarm.id, "type": "alarm_notification"}

                    # Yardımcı fonksiyonu çağır
                    send_onesignal_notification(
                        player_ids=player_ids,
                        heading=baslik,
                        content=icerik,
                        data=alarm_data,
                        image_url=new_alarm.image_url # Alarm resmini de gönderelim
                    )
            except (json.JSONDecodeError, TypeError) as e:
                print(f"[OneSignal] Müşteri {customer.id} için player_id'ler okunamadı: {e}")

        return jsonify(new_alarm.to_json()), 201
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": "Test alarmı oluşturulurken hata", "details": str(e)}), 500

# --- Müşteri Şifre Atama (DEĞİŞİKLİK YOK) ---
@app.route('/resellers/<int:reseller_id>/customers/<int:customer_id>/set-credentials', methods=['POST'])
def set_customer_credentials(reseller_id, customer_id):
    reseller = Reseller.query.get(reseller_id)
    if not reseller: return jsonify({"error": "Bayi bulunamadı"}), 404
    customer = Customer.query.filter_by(id=customer_id, reseller_id=reseller_id).first()
    if not customer: return jsonify({"error": "Bayiye ait müşteri bulunamadı"}), 404
    data = request.get_json()
    password = data.get('password')
    if not password or len(password) < 6:
        return jsonify({"error": "Şifre en az 6 karakter olmalı"}), 400
    customer.password_hash = generate_password_hash(password)
    try:
        db.session.commit()
        return jsonify({"message": f"{customer.name} için şifre başarıyla ayarlandı."}), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": "Müşteri şifresi ayarlanırken hata.", "details": str(e)}), 500

# --- AUTH ENDPOINTS ---
@app.route('/customer/login', methods=['POST'])
def customer_login():
    data = request.get_json()
    email = data.get('email')
    password = data.get('password')
    if not email or not password:
        return jsonify({"error": "E-posta ve şifre gerekli"}), 400
    customer = Customer.query.filter_by(email=email).first()
    if not customer or not customer.password_hash or not check_password_hash(customer.password_hash, password):
        return jsonify({"error": "Geçersiz e-posta veya şifre"}), 401
    if customer.status != 'Active':
        return jsonify({"error": "Hesabınız aktif değil. Bayinizle iletişime geçin."}), 403
    customer_data = customer.to_json()
    customer_data['role'] = 'customer'
    # Burada JWT token üretilip döndürülebilir
    return jsonify(customer_data), 200

@app.route('/admin/login', methods=['POST'])
def admin_login():
    data = request.get_json()
    email = data.get('email')
    password = data.get('password')
    if not email or not password:
        return jsonify({"error": "E-posta ve şifre gerekli"}), 400
    
    # GERÇEK SİSTEMDE ADMIN BİLGİLERİ GÜVENLİ SAKLANMALI (DB, HASHED PW)
    if email == ADMIN_EMAIL and password == ADMIN_PASSWORD: # Geçici kontrol
        admin_user_obj = {
            'id': 0, 'name': 'Sistem Yöneticisi', 'email': ADMIN_EMAIL,
            'phone': 'N/A', 
            'customers': Customer.query.count(), # Toplam müşteri sayısı
            'cameras': Camera.query.count(),     # Toplam kamera sayısı
            'status': 'Active',
            # Admin için modül lisansları (sınırsız gibi)
            'moduleLicenses': {key: 99999 for key in MODULE_KEYS},
            'usedModuleLicenses': {key: 0 for key in MODULE_KEYS}, # Admin doğrudan kamera kurmaz
            'remainingModuleLicenses': {key: 99999 for key in MODULE_KEYS},
            'joinDate': datetime.now(timezone.utc).strftime('%Y-%m-%d'),
            'role': 'admin'
        }
        return jsonify(admin_user_obj), 200
    else:
        return jsonify({"error": "Geçersiz admin bilgileri."}), 401

@app.route('/login', methods=['POST']) # Bayi Girişi
def login():
    data = request.get_json()
    email = data.get('email')
    password = data.get('password')
    if not email or not password:
        return jsonify({"error": "E-posta ve şifre gerekli"}), 400
    reseller = Reseller.query.filter_by(email=email).first()
    # Reseller password_hash alanı boş olabilir (eski kayıtlarda) veya şifre eşleşmeyebilir
    if not reseller or not reseller.password_hash or not check_password_hash(reseller.password_hash, password):
        return jsonify({"error": "Geçersiz e-posta veya şifre"}), 401
    if reseller.status != 'Active':
        return jsonify({"error": "Hesabınız pasif. Yöneticinizle iletişime geçin."}), 403
    reseller_data = reseller.to_json()
    reseller_data['role'] = 'reseller'
    # Burada JWT token üretilip döndürülebilir
    return jsonify(reseller_data), 200

# --- RESELLER ENDPOINTS (MODÜL LİSANS GÜNCELLENDİ) ---
@app.route('/resellers', methods=['POST'])
def add_reseller():
    data = request.get_json()
    if not data: return jsonify({"error": "Veri gönderilmedi"}), 400
    required_fields = ['name', 'email', 'password_hash'] # moduleLicenses opsiyonel
    for field in required_fields:
        if field not in data or not data[field]:
            return jsonify({"error": f"Eksik alan: {field}"}), 400
    if Reseller.query.filter_by(email=data['email']).first():
        return jsonify({"error": "Bu e-posta zaten kayıtlı."}), 409
    if Reseller.query.filter_by(name=data['name']).first():
        return jsonify({"error": "Bu bayi adı zaten kayıtlı."}), 409

    try:
        hashed_password = generate_password_hash(data['password_hash'])
        new_reseller = Reseller(
            name=data['name'], email=data['email'], password_hash=hashed_password,
            phone=data.get('phone', ''), status=data.get('status', 'Active'),
            join_date=data.get('joinDate', datetime.now(timezone.utc).strftime('%Y-%m-%d'))
        )
        # Modül lisanslarını ayarla (setter ile)
        if 'moduleLicenses' in data and isinstance(data['moduleLicenses'], dict):
            new_reseller.module_licenses = data['moduleLicenses']
        else: # Varsayılan olarak tüm modüllere 0 lisans
            new_reseller.module_licenses = {key: 0 for key in MODULE_KEYS}
        
        db.session.add(new_reseller)
        db.session.commit()
        return jsonify(new_reseller.to_json()), 201
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": "Bayi eklenirken hata.", "details": str(e)}), 500

@app.route('/resellers', methods=['GET'])
def get_resellers():
    try:
        resellers = Reseller.query.order_by(Reseller.name).all()
        return jsonify([reseller.to_json() for reseller in resellers]), 200
    except Exception as e:
        return jsonify({"error": "Bayiler listelenirken hata.", "details": str(e)}), 500

@app.route('/resellers/<int:id>', methods=['GET'])
def get_reseller(id):
    try:
        reseller = Reseller.query.get(id)
        if reseller: return jsonify(reseller.to_json()), 200
        else: return jsonify({"error": "Bayi bulunamadı"}), 404
    except Exception as e:
        return jsonify({"error": "Bayi getirilirken hata.", "details": str(e)}), 500

@app.route('/resellers/<int:id>', methods=['PUT'])
def update_reseller(id):
    data = request.get_json()
    if not data: return jsonify({"error": "Veri gönderilmedi"}), 400
    reseller = Reseller.query.get(id)
    if not reseller: return jsonify({"error": "Güncellenecek bayi bulunamadı"}), 404

    if 'email' in data and data['email'] != reseller.email:
        if Reseller.query.filter(Reseller.id != id, Reseller.email == data['email']).first():
            return jsonify({"error": "E-posta başka bayiye ait."}), 409
        reseller.email = data['email']
    if 'name' in data and data['name'] != reseller.name:
        if Reseller.query.filter(Reseller.id != id, Reseller.name == data['name']).first():
            return jsonify({"error": "Bayi adı başka bayiye ait."}), 409
        reseller.name = data['name']
    
    if 'password_hash' in data and data['password_hash']: # Şifre güncelleme isteği
        reseller.password_hash = generate_password_hash(data['password_hash'])

    # Modül lisanslarını güncelle (setter ile)
    if 'moduleLicenses' in data and isinstance(data['moduleLicenses'], dict):
        reseller.module_licenses = data['moduleLicenses']

    reseller.phone = data.get('phone', reseller.phone)
    # reseller.licenses = data.get('licenses', reseller.licenses) # ESKİ LİSANS KALDIRILDI
    reseller.status = data.get('status', reseller.status)
    reseller.join_date = data.get('joinDate', reseller.join_date)
    # reseller.password_hash = data.get('password_hash', reseller.password_hash) # YUKARIDA HALLEDİLDİ

    try:
        db.session.commit()
        return jsonify(reseller.to_json()), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": "Bayi güncellenirken hata.", "details": str(e)}), 500

@app.route('/resellers/<int:id>', methods=['DELETE'])
def delete_reseller(id):
    reseller = Reseller.query.get(id)
    if not reseller: return jsonify({"error": "Silinecek bayi bulunamadı"}), 404
    try:
        # İlişkili müşteriler, kameralar, alarmlar cascade ile silinecek (modellerde tanımlıysa)
        db.session.delete(reseller)
        db.session.commit()
        return jsonify({"message": "Bayi başarıyla silindi"}), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": "Bayi silinirken hata.", "details": str(e)}), 500

# --- STREAMS & RTSP FRAME (MODÜL BİLGİSİ EKLENDİ) ---
@app.route('/streams', methods=['GET'])
def get_streams_for_yolo():
    active_cameras_data = []
    customers = Customer.query.filter_by(status='Active').options(joinedload(Customer.cameras_rel)).all()

    for customer in customers:
        cameras_with_modules = [cam for cam in customer.cameras_rel if cam.assigned_module]
        is_customer_present = customer.is_present
        active_cameras_for_customer = [cam for cam in customer.cameras_rel if cam.assigned_module and cam.is_active]
        for camera in active_cameras_for_customer:
            polygon_data = "0"
            if camera.roi_coordinates:
                try:
                    loaded_roi_objects = json.loads(camera.roi_coordinates)
                    if isinstance(loaded_roi_objects, list) and all(isinstance(item, dict) for item in loaded_roi_objects):
                        converted_polygon = []
                        valid_format = True
                        for point_obj in loaded_roi_objects:
                            if all(k in point_obj for k in ('x', 'y')) and \
                               all(isinstance(point_obj[k], (int, float)) for k in ('x', 'y')):
                                converted_polygon.append([point_obj['x'], point_obj['y']])
                            else: valid_format = False; break
                        if valid_format and len(converted_polygon) >= 3:
                            polygon_data = converted_polygon
                except (json.JSONDecodeError, TypeError, Exception) as e:
                    print(f"[!] Kamera {camera.id} ROI işleme hatası: {e}, Veri: {camera.roi_coordinates}")
            
            time_range_data = camera.analysis_time_range or "0"
            
            module_key_for_stream = camera.assigned_module
            stream_module_name = module_key_for_stream # örn: "person_detection"
            
            # Modüle göre varsayılan alarm tipi
            default_alarm_type = AVAILABLE_MODULES.get(
                camera.assigned_module, f"{camera.assigned_module} Alarmı"
            )
            # İsterseniz burada daha spesifik alarm tipleri atayabilirsiniz
            # if module_key_for_stream == "person_detection": default_alarm_type = "İnsan Algılandı"

            active_cameras_data.append({
                "rtsp_url": camera.rtsp_url, "camera_id": camera.id, "customer_id": customer.id,
                "customer_name": customer.name, "camera_name": camera.name,
                "additional_id": customer.additional_id, "polygon": polygon_data,
                "cooldown": customer.cooldown, "time_range": time_range_data,
                "module_name": stream_module_name, # YOLO'nun kullanacağı modül anahtarı
                "alarm_type": default_alarm_type,
                "conf": camera.confidence_threshold,
                "cooldown": customer.cooldown, # Müşteriye özel cooldown süresi
                "telegram_chat_id": customer.telegram_chat_id, # Müşteriye özel Telegram ID
                "is_present": is_customer_present, # "Ben Buradayım" durumu
                "camera_is_active": camera.is_active # Bu zaten döngü koşulunda var ama yine de gönderelim # YOLO'nun kullanacağı güven eşiği   # YOLO'nun alarm oluştururken kullanabileceği tip
            })

            
    return jsonify(active_cameras_data), 200


@app.route('/cameras/<int:id>/toggle-status', methods=['POST'])
def toggle_camera_status(id):
    # Bu endpoint'e sadece ilgili müşteri erişebilmeli.
    # Gerçek bir uygulamada JWT ile gelen customer_id ile kameranın customer_id'si karşılaştırılmalı.
    # Şimdilik bu kontrolü atlıyoruz.
    
    camera = Camera.query.get_or_404(id)
    
    # Mevcut durumun tersini ata
    camera.is_active = not camera.is_active
    
    try:
        db.session.commit()
        # Güncellenmiş kamera bilgisini geri dön, frontend state'i kolayca güncellesin.
        return jsonify(camera.to_json()), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": "Kamera durumu güncellenirken bir hata oluştu.", "details": str(e)}), 500


@app.route('/api/rtsp-frame', methods=['GET'])
def get_rtsp_frame():
    rtsp_url = request.args.get('url')
    if not rtsp_url: return jsonify({"error": "RTSP URL parametresi eksik."}), 400
    cap = None
    try:
        # os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "rtsp_transport;tcp" # Denenebilir
        cap = cv2.VideoCapture(rtsp_url) # cv2.CAP_FFMPEG opsiyonel
        if not cap.isOpened():
            return jsonify({"error": f"RTSP stream açılamadı: {rtsp_url}. Kamera offline olabilir veya URL yanlış."}), 500
        
        # Daha sağlam frame okuma
        for _ in range(10): # Daha fazla deneme
            ret, frame = cap.read()
            if ret and frame is not None and frame.shape[0] > 0 and frame.shape[1] > 0 : # Frame'in geçerli olup olmadığını kontrol et
                break
            cv2.waitKey(30) # Kısa bekleme
        else: # Döngü break olmadan biterse
             return jsonify({"error": f"RTSP stream'den geçerli frame okunamadı (timeout veya boş frame): {rtsp_url}"}), 500

        ret, buffer = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 75]) # Kalite ayarı
        if not ret:
            return jsonify({"error": "Frame JPEG formatına encode edilemedi."}), 500
        return buffer.tobytes(), 200, {'Content-Type': 'image/jpeg'}
    except cv2.error as e:
        return jsonify({"error": f"OpenCV hatası ({rtsp_url}): {str(e)}"}), 500
    except Exception as e:
        import traceback
        return jsonify({"error": f"RTSP frame alınırken genel hata ({rtsp_url}): {str(e)}", "trace": traceback.format_exc()}), 500
    finally:
        if cap and cap.isOpened(): cap.release()

# --- KURULUM TALEPLERİ (MODÜL LİSANS GÜNCELLENDİ) ---
@app.route('/installation-requests', methods=['POST'])
def create_installation_request():
    data = request.get_json()
    if not data: return jsonify({"error": "Veri gönderilmedi"}), 400
    # rtspStreams -> rtspStreamsInfo olarak değişti, her stream için module içermeli
    required_fields = ['fullName', 'email', 'nationalId', 'rtspStreamsInfo', 'resellerId', 'licenseExpiry']
    for field in required_fields:
        if field not in data or \
           (field == 'rtspStreamsInfo' and not isinstance(data.get(field), list)) or \
           (field != 'rtspStreamsInfo' and not data.get(field)):
            return jsonify({"error": f"Eksik veya geçersiz alan: {field}"}), 400

    reseller = Reseller.query.get(data['resellerId'])
    if not reseller: return jsonify({"error": "Belirtilen bayi bulunamadı"}), 404
    
    # E-posta/TCKN benzersizlik kontrolü (Customer & pending ApprovalRequest)
    if Customer.query.filter_by(email=data['email']).first() or \
       ApprovalRequest.query.filter((ApprovalRequest.customer_email == data['email']) & (ApprovalRequest.status == 'Pending')).first():
        return jsonify({"error": "E-posta zaten kayıtlı veya beklemede."}), 409
    if Customer.query.filter_by(national_id=data['nationalId']).first() or \
       ApprovalRequest.query.filter((ApprovalRequest.customer_national_id == data['nationalId']) & (ApprovalRequest.status == 'Pending')).first():
        return jsonify({"error": "TCKN zaten kayıtlı veya beklemede."}), 409

    rtsp_streams_info_data = data.get('rtspStreamsInfo', [])
    if not rtsp_streams_info_data: # En az bir stream olmalı
         return jsonify({"error": "rtspStreamsInfo listesi boş olamaz."}), 400

    requested_counts_for_approval = {key: 0 for key in MODULE_KEYS}
    valid_streams_for_db = []
    processed_urls = set() # Aynı talepte duplicate URL kontrolü için

    for stream_item in rtsp_streams_info_data:
        if not (isinstance(stream_item, dict) and 'url' in stream_item and 'module' in stream_item and \
                isinstance(stream_item['url'], str) and stream_item['url'].strip() and \
                isinstance(stream_item['module'], str) and stream_item['module'] in MODULE_KEYS):
            return jsonify({"error": f"rtspStreamsInfo içinde geçersiz öğe: {stream_item}. 'url' (boş olamaz) ve geçerli bir 'module' içermelidir."}), 400
        
        module_key = stream_item['module']
        rtsp_url = stream_item['url'].strip()
        
        if Camera.query.filter_by(rtsp_url=rtsp_url).first():
            return jsonify({"error": f"RTSP URL '{rtsp_url}' zaten kullanılıyor."}), 409
        if rtsp_url in processed_urls:
            return jsonify({"error": f"Talep içinde aynı RTSP URL birden fazla kez belirtilmiş: '{rtsp_url}'"}), 409
        processed_urls.add(rtsp_url)

        requested_counts_for_approval[module_key] += 1
        valid_streams_for_db.append({"url": rtsp_url, "module": module_key})
    
    # Bayinin her modül için yeterli lisansı var mı?
    current_reseller_remaining = reseller.remaining_module_licenses
    for module_key, requested_count in requested_counts_for_approval.items():
        if requested_count > 0:
            if current_reseller_remaining.get(module_key, 0) < requested_count:
                mod_name = AVAILABLE_MODULES.get(module_key, module_key)
                return jsonify({
                    "error": f"Bayi '{reseller.name}' için '{mod_name}' modülünde yeterli lisans yok. {requested_count} talep edildi, kalan: {current_reseller_remaining.get(module_key, 0)}"
                }), 400
    try:
        new_request = ApprovalRequest(
            reseller_id=data['resellerId'], customer_name=data['fullName'], customer_email=data['email'],
            customer_phone=data.get('phone', ''), customer_national_id=data['nationalId'],
            customer_license_expiry=data['licenseExpiry'],
            customer_siren_ip_address=data.get('sirenIpAddress'), customer_additional_id=data.get('additionalId'),
            customer_address=data.get('address'), customer_alarm_center_ip=data.get('alarmCenterIp'),
            status='Pending', request_date=datetime.now(timezone.utc).strftime('%Y-%m-%d'),
            customer_telegram_chat_id=data.get('telegramChatId')
        )
        new_request.rtsp_streams_info = valid_streams_for_db # Setter ile ata (models.py'de güncellenmiş olmalı)
        
        db.session.add(new_request)
        db.session.commit()
        return jsonify(new_request.to_json()), 201
    except ValueError as ve:
        db.session.rollback()
        return jsonify({"error": "Kurulum talebi verisi işlenirken hata.", "details": str(ve)}), 400
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": "Kurulum talebi oluşturulurken hata.", "details": str(e)}), 500

@app.route('/installation-requests', methods=['GET']) # Admin için
def get_installation_requests():
    try:
        # Filtreleme opsiyonları
        status_filter = request.args.get('status')
        reseller_id_filter = request.args.get('resellerId', type=int)
        
        query = ApprovalRequest.query.options(joinedload(ApprovalRequest.reseller)) # N+1 önleme
        if status_filter:
            query = query.filter(ApprovalRequest.status == status_filter)
        if reseller_id_filter:
            query = query.filter(ApprovalRequest.reseller_id == reseller_id_filter)
            
        all_requests = query.order_by(ApprovalRequest.request_date.desc()).all()
        return jsonify([req.to_json() for req in all_requests]), 200
    except Exception as e:
        return jsonify({"error": "Kurulum talepleri listelenirken hata.", "details": str(e)}), 500

@app.route('/installation-requests/<int:request_id>/approve', methods=['PUT']) # Admin için
def approve_installation_request(request_id):
    approval_req = ApprovalRequest.query.get_or_404(request_id)
    
    if approval_req.status != 'Pending':
        return jsonify({"error": "Bu talep zaten işlenmiş."}), 400

    # --- E-POSTA DOĞRULAMA KONTROLÜ ---
    if not approval_req.email_verified:
        return jsonify({"error": "Müşterinin e-postası henüz doğrulanmadı. Lütfen önce e-postayı doğrulayın."}), 400
    # --- E-POSTA DOĞRULAMA KONTROLÜ BİTİŞ ---

    reseller = Reseller.query.get(approval_req.reseller_id)
    if not reseller: 
        approval_req.status = 'Rejected'
        approval_req.approval_date = datetime.now(timezone.utc).strftime('%Y-%m-%d')
        db.session.commit()
        return jsonify({"error": "Taleple ilişkili bayi bulunamadı. Talep reddedildi."}), 500
    
    requested_mod_counts = approval_req.requested_modules_count
    reseller_remaining_now = reseller.remaining_module_licenses
    for mod_key, count_needed in requested_mod_counts.items():
        if count_needed > 0:
            if reseller_remaining_now.get(mod_key, 0) < count_needed:
                mod_name = AVAILABLE_MODULES.get(mod_key, mod_key)
                approval_req.status = 'Rejected'
                approval_req.approval_date = datetime.now(timezone.utc).strftime('%Y-%m-%d')
                db.session.commit()
                return jsonify({
                    "error": f"Onay sırasında bayi '{reseller.name}' için '{mod_name}' modülünde yeterli lisans kalmamış ({reseller_remaining_now.get(mod_key,0)}/{count_needed}). Talep reddedildi."
                }), 400
    
    if Customer.query.filter_by(email=approval_req.customer_email).first() or \
       Customer.query.filter_by(national_id=approval_req.customer_national_id).first():
        approval_req.status = 'Rejected'
        approval_req.approval_date = datetime.now(timezone.utc).strftime('%Y-%m-%d')
        db.session.commit()
        return jsonify({"error": "E-posta veya TCKN zaten kayıtlı bir müşteriye ait. Talep reddedildi."}), 409

    try:
        new_customer = Customer(
            name=approval_req.customer_name, email=approval_req.customer_email,
            phone=approval_req.customer_phone, national_id=approval_req.customer_national_id,
            registration_date=approval_req.request_date, license_expiry=approval_req.customer_license_expiry,
            siren_ip_address=approval_req.customer_siren_ip_address,
            additional_id=approval_req.customer_additional_id, address=approval_req.customer_address,
            alarm_center_ip=approval_req.customer_alarm_center_ip, reseller_id=approval_req.reseller_id,
            telegram_chat_id=approval_req.customer_telegram_chat_id,
            status='Active'
        )
        db.session.add(new_customer)
        db.session.flush()

        for stream_info in approval_req.rtsp_streams_info:
            if Camera.query.filter_by(rtsp_url=stream_info['url']).first():
                db.session.rollback()
                approval_req.status = 'Rejected'
                approval_req.approval_date = datetime.now(timezone.utc).strftime('%Y-%m-%d')
                db.session.commit()
                return jsonify({"error": f"Onay sırasında RTSP URL '{stream_info['url']}' başkası tarafından alındı. Talep reddedildi."}), 409

            cam_name = f"Kamera - {AVAILABLE_MODULES.get(stream_info['module'], stream_info['module'])}"
            new_camera = Camera(
                name=cam_name, rtsp_url=stream_info['url'],
                assigned_module=stream_info['module'], customer_id=new_customer.id
            )
            db.session.add(new_camera)
        
        approval_req.status = 'Approved'
        approval_req.approval_date = datetime.now(timezone.utc).strftime('%Y-%m-%d')
        db.session.commit()
        return jsonify({"message": "Kurulum talebi onaylandı.", "customer": new_customer.to_json()}), 200
    except Exception as e:
        db.session.rollback()
        import traceback
        print(traceback.format_exc()) # Hatanın tam dökümünü görmek için
        return jsonify({"error": "Kurulum talebi onaylanırken beklenmedik bir hata oluştu.", "details": str(e)}), 500

@app.route('/installation-requests/<int:request_id>/reject', methods=['PUT']) # Admin için
def reject_installation_request(request_id):
    approval_req = ApprovalRequest.query.get(request_id)
    if not approval_req: return jsonify({"error": "Reddedilecek talep bulunamadı"}), 404
    if approval_req.status != 'Pending': return jsonify({"error": "Bu talep zaten işlenmiş."}), 400
    try:
        approval_req.status = 'Rejected'
        approval_req.approval_date = datetime.now(timezone.utc).strftime('%Y-%m-%d')
        db.session.commit()
        return jsonify({"message": "Kurulum talebi reddedildi."}), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": "Kurulum talebi reddedilirken hata.", "details": str(e)}), 500

# --- CUSTOMER CRUD (Çoğunlukla Değişiklik Yok, update_customer'da bayi taşıma lisans kontrolü eklendi) ---
@app.route('/alarms', methods=['GET']) # Bu genel alarm listesi (Admin/Yetkili Bayi için)
def get_all_alarms():
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 20, type=int)
    search = request.args.get('search', type=str)
    reseller_id = request.args.get('resellerId', type=int) # Admin için
    customer_id = request.args.get('customerId', type=int) # Admin veya Bayi için (kendi müşterisi)
    start_date_str = request.args.get('startDate', type=str)
    end_date_str = request.args.get('endDate', type=str)

    query = Alarm.query.options(
        joinedload(Alarm.customer).joinedload(Customer.reseller), # Bayi adını almak için
        joinedload(Alarm.camera) # Kamera adını almak için
    )
    if reseller_id: # Admin bir bayinin alarmlarını filtreliyorsa
        query = query.join(Alarm.customer).filter(Customer.reseller_id == reseller_id)
    if customer_id:
        query = query.filter(Alarm.customer_id == customer_id)
    
    # Tarih filtreleri UTC'ye göre olmalı (Alarm.timestamp UTC)
    if start_date_str:
        try:
            start_date = datetime.strptime(start_date_str, '%Y-%m-%d').replace(tzinfo=timezone.utc)
            query = query.filter(Alarm.timestamp >= start_date)
        except ValueError: return jsonify({"error": "Geçersiz başlangıç tarihi formatı (YYYY-MM-DD)."}), 400
    if end_date_str:
        try:
            end_date = datetime.strptime(end_date_str, '%Y-%m-%d').replace(hour=23, minute=59, second=59, tzinfo=timezone.utc)
            query = query.filter(Alarm.timestamp <= end_date)
        except ValueError: return jsonify({"error": "Geçersiz bitiş tarihi formatı (YYYY-MM-DD)."}), 400

    if search:
        search_term = f"%{search}%"
        # Customer ve Camera join'leri options ile yüklendiği için tekrar join'e gerek yok
        # Eğer isouter=True ile yapılıyorsa ve options yoksa join gerekir.
        CustomerAlias = aliased(Customer) # Farklı bir isimle Customer'a referans
        CameraAlias = aliased(Camera)     # Farklı bir isimle Camera'ya referans
        query = query.join(CustomerAlias, Alarm.customer_id == CustomerAlias.id, isouter=True)\
                     .join(CameraAlias, Alarm.camera_id == CameraAlias.id, isouter=True) # isouter=True eğer alarmın kamerası/müşterisi silinmişse bile listelemek için
        query = query.filter(
            db.or_(
                Alarm.alarm_type.ilike(search_term), Alarm.category.ilike(search_term),
                Alarm.module_name.ilike(search_term), # Modül key'i üzerinden arama
                CustomerAlias.name.ilike(search_term), CameraAlias.name.ilike(search_term)
            )
        )
    query = query.order_by(Alarm.timestamp.desc())
    paginated_alarms = query.paginate(page=page, per_page=per_page, error_out=False)
    return jsonify({
        'alarms': [alarm.to_json() for alarm in paginated_alarms.items],
        'pagination': {'currentPage': paginated_alarms.page, 'totalPages': paginated_alarms.pages,
                       'totalItems': paginated_alarms.total, 'hasNext': paginated_alarms.has_next,
                       'hasPrev': paginated_alarms.has_prev}
    }), 200

@app.route('/customers', methods=['POST']) # Admin'in doğrudan müşteri eklemesi için (genelde talep üzerinden)
def add_customer():
    data = request.get_json()
    if not data: return jsonify({"error": "Veri gönderilmedi"}), 400
    required_fields = ['name', 'email', 'nationalId', 'resellerId', 'licenseExpiry']
    for field in required_fields:
        if field == 'resellerId' and (data.get(field) is None or data.get(field) == 0): # 0 geçerli bir bayi ID'si olmamalı
            return jsonify({"error": "Geçerli bir bayi seçilmelidir (resellerId)."}), 400
        if not data.get(field) and field != 'resellerId': # resellerId yukarıda kontrol edildi
            return jsonify({"error": f"Eksik alan: {field}"}), 400

    if Customer.query.filter_by(email=data['email']).first(): return jsonify({"error": "E-posta kayıtlı."}), 409
    if Customer.query.filter_by(national_id=data['nationalId']).first(): return jsonify({"error": "TCKN kayıtlı."}), 409
    reseller = Reseller.query.get(data['resellerId'])
    if not reseller: return jsonify({"error": "Belirtilen bayi bulunamadı."}), 404
    
    # Bu yolla müşteri eklenirken kamera eklenmediği için modül lisans kontrolü yok.
    try:
        new_customer = Customer(
            name=data['name'], email=data['email'], phone=data.get('phone', ''),
            national_id=data['nationalId'], reseller_id=data['resellerId'],
            license_expiry=data['licenseExpiry'],
            notification_channels=','.join(data.get('notificationChannels', [])),
            status= 'Active' if data.get('isActive', True) else 'Inactive', # isActive boolean kabul et
            registration_date=data.get('registrationDate', datetime.now(timezone.utc).strftime('%Y-%m-%d')),
            siren_ip_address=data.get('sirenIpAddress'), additional_id=data.get('additionalId'),
            address=data.get('address'), alarm_center_ip=data.get('alarmCenterIp'),
            cooldown=data.get('cooldown', 60) # Default cooldown
        )
        db.session.add(new_customer)
        db.session.commit()
        return jsonify(new_customer.to_json()), 201
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": "Müşteri eklenirken hata.", "details": str(e)}), 500

@app.route('/customers', methods=['GET']) # Admin ve Bayi için müşteri listeleme
def get_customers():
    try:
        reseller_id_filter = request.args.get('resellerId', type=int) # Admin için
        search_term = request.args.get('search', type=str)
        
        # Giriş yapan kullanıcıya göre filtreleme eklenebilir (JWT ile rol/id alınarak)
        # Şimdilik, eğer resellerId gelirse ona göre, yoksa tümünü listeliyor (Admin varsayımı)
        
        customer_query = Customer.query.options(joinedload(Customer.reseller)) # Reseller bilgisini önceden yükle
        if reseller_id_filter:
            customer_query = customer_query.filter_by(reseller_id=reseller_id_filter)
        if search_term:
            s = f"%{search_term}%"
            customer_query = customer_query.filter(
                db.or_(Customer.name.ilike(s), Customer.email.ilike(s), Customer.national_id.ilike(s), Customer.additional_id.ilike(s)))
        
        active_customers = customer_query.order_by(Customer.name).all()
        customer_list = [c.to_json() for c in active_customers]

        # Bekleyen talepleri de ekle (opsiyonel, frontend'de ayrı bir sekmede de olabilir)
        if not search_term: # Sadece arama yapılmıyorsa göster
            pending_req_query = ApprovalRequest.query.filter_by(status='Pending').options(joinedload(ApprovalRequest.reseller))
            if reseller_id_filter:
                pending_req_query = pending_req_query.filter_by(reseller_id=reseller_id_filter)
            
            for req in pending_req_query.all():
                customer_list.append({
                    'id': f"pending_{req.id}", 'name': req.customer_name, 'email': req.customer_email,
                    'phone': req.customer_phone, 'nationalId': req.customer_national_id,
                    'registrationDate': req.request_date, 'resellerId': req.reseller_id,
                    'resellerName': req.reseller.name if req.reseller else None,
                    'cameraCount': len(req.rtsp_streams_info), # Düzeltme: rtsp_streams_info
                    'notificationChannels': [], 'licenseExpiry': req.customer_license_expiry,
                    'isActive': False, 'status': 'Pending', # Frontend'de 'status' alanı kullanılabilir
                    'sirenIpAddress': req.customer_siren_ip_address, 'additionalId': req.customer_additional_id,
                    'address': req.customer_address, 'alarmCenterIp': req.customer_alarm_center_ip,
                    'requestedModulesCount': req.requested_modules_count # Modül talepleri
                })
        # Son listeyi kayıt/talep tarihine göre tersten sırala
        sorted_list = sorted(customer_list, key=lambda x: x.get('registrationDate', '0'), reverse=True)
        return jsonify(sorted_list), 200
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({"error": "Müşteriler listelenirken hata.", "details": str(e)}), 500

@app.route('/customers/<int:id>', methods=['GET'])
def get_customer(id):
    try:
        customer = Customer.query.get(id)
        if customer: return jsonify(customer.to_json()), 200
        else: return jsonify({"error": "Müşteri bulunamadı"}), 404
    except Exception as e: return jsonify({"error": "Müşteri getirilirken hata.", "details": str(e)}), 500

@app.route('/customers/<int:id>', methods=['PUT'])
def update_customer(id):
    data = request.get_json()
    if not data: return jsonify({"error": "Veri gönderilmedi"}), 400
    customer = Customer.query.get(id)
    if not customer: return jsonify({"error": "Güncellenecek müşteri bulunamadı"}), 404

    if 'email' in data and data['email'] != customer.email:
        if Customer.query.filter(Customer.id != id, Customer.email == data['email']).first():
            return jsonify({"error": "E-posta başka müşteriye ait."}), 409
        customer.email = data['email']
    if 'nationalId' in data and data['nationalId'] != customer.national_id:
        if Customer.query.filter(Customer.id != id, Customer.national_id == data['nationalId']).first():
            return jsonify({"error": "TCKN başka müşteriye ait."}), 409
        customer.national_id = data['nationalId']
    
    customer.name = data.get('name', customer.name)
    customer.phone = data.get('phone', customer.phone)
    customer.notification_channels = ','.join(data.get('notificationChannels', customer.notification_channels.split(',') if customer.notification_channels else []))
    customer.telegram_chat_id = data.get('telegram_chat_id', customer.telegram_chat_id)
    customer.license_expiry = data.get('licenseExpiry', customer.license_expiry)
    # isActive (boolean) -> status (string) dönüşümü
    if 'isActive' in data: # Frontend boolean gönderiyor
        customer.status = 'Active' if data['isActive'] else 'Inactive'
    elif 'status' in data: # Frontend string gönderiyor
        customer.status = data['status']

    customer.registration_date = data.get('registrationDate', customer.registration_date)
    customer.siren_ip_address = data.get('sirenIpAddress', customer.siren_ip_address)
    customer.additional_id = data.get('additionalId', customer.additional_id)
    customer.address = data.get('address', customer.address)
    customer.alarm_center_ip = data.get('alarmCenterIp', customer.alarm_center_ip)
    if 'cooldown' in data:
        try:
            cooldown_val = int(data['cooldown'])
            if cooldown_val >= 0: customer.cooldown = cooldown_val
            else: return jsonify({"error": "Cooldown negatif olamaz."}), 400
        except (ValueError, TypeError): return jsonify({"error": "Geçersiz cooldown değeri."}), 400

    # Bayi değişikliği ve lisans kontrolü
    if 'resellerId' in data and data['resellerId'] != customer.reseller_id:
        new_reseller = Reseller.query.get(data['resellerId'])
        if not new_reseller: return jsonify({"error": "Yeni bayi bulunamadı"}), 404
        
        # Müşterinin mevcut kameralarının modül sayılarını hesapla
        customer_camera_modules_count = defaultdict(int)
        for cam in customer.cameras_rel: # Customer.cameras_rel lazy='dynamic' olmalı
            if cam.assigned_module:
                customer_camera_modules_count[cam.assigned_module] += 1
        
        new_reseller_remaining = new_reseller.remaining_module_licenses
        for mod_key, count_needed in customer_camera_modules_count.items():
            if new_reseller_remaining.get(mod_key, 0) < count_needed:
                mod_name = AVAILABLE_MODULES.get(mod_key, mod_key)
                return jsonify({"error": f"Yeni bayi '{new_reseller.name}' için '{mod_name}' modülünde yeterli lisans yok ({new_reseller_remaining.get(mod_key,0)}/{count_needed}). Müşteri transfer edilemiyor."}), 400
        customer.reseller_id = data['resellerId']

    try:
        db.session.commit()
        return jsonify(customer.to_json()), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": "Müşteri güncellenirken hata.", "details": str(e)}), 500

@app.route('/customers/<int:id>', methods=['DELETE'])
def delete_customer(id):
    customer = Customer.query.get(id)
    if not customer: return jsonify({"error": "Silinecek müşteri bulunamadı"}), 404
    try:
        # İlişkili kameralar, alarmlar cascade ile silinir (modellerde ayarlıysa)
        db.session.delete(customer)
        db.session.commit()
        return jsonify({"message": "Müşteri başarıyla silindi"}), 200
    except Exception as e: # Genelde Foreign Key constraint hatası olabilir eğer cascade düzgün değilse
        db.session.rollback()
        return jsonify({"error": "Müşteri silinirken hata. İlişkili kayıtları olabilir.", "details": str(e)}), 500

# --- CAMERA ENDPOINTS (MODÜL LİSANS GÜNCELLENDİ) ---
@app.route('/customers/<int:customer_id>/cameras', methods=['GET'])
def get_customer_cameras(customer_id):
    customer = Customer.query.get(customer_id)
    if not customer: return jsonify({"error": "Müşteri bulunamadı"}), 404
    try:
        # Python'un sorted() fonksiyonu ile sıralama yapıyoruz
        # attrgetter, birden fazla özelliğe göre sıralamayı kolaylaştırır
        sorted_cameras = sorted(
            customer.cameras_rel, 
            key=lambda cam: (
                cam.assigned_module is None, # Önce modülü olmayanları grupla (ve sona at)
                cam.assigned_module,         # Sonra modül adına göre sırala
                cam.name is None,            # Sonra adı olmayanları grupla (ve sona at)
                cam.name                     # Sonra kamera adına göre sırala
            )
        )
        return jsonify([camera.to_json() for camera in sorted_cameras]), 200
    except Exception as e:
        import traceback
        traceback.print_exc() # Hatanın detayını terminalde görmek için
        return jsonify({"error": "Kameralar listelenirken hata.", "details": str(e)}), 500

@app.route('/customers/<int:customer_id>/cameras', methods=['POST'])
def add_camera_to_customer(customer_id):
    data = request.get_json()
    if not data: return jsonify({"error": "Veri gönderilmedi"}), 400
    required_fields = ['rtspUrl', 'assignedModule']
    for field in required_fields:
        if not data.get(field): # assignedModule boş string olamaz
            return jsonify({"error": f"Eksik veya geçersiz alan: {field}"}), 400

    assigned_module_key = data['assignedModule']
    if assigned_module_key not in MODULE_KEYS:
        return jsonify({"error": f"Geçersiz modül: {assigned_module_key}. Kullanılabilir: {', '.join(MODULE_KEYS)}"}), 400

    customer = Customer.query.get(customer_id)
    if not customer: return jsonify({"error": "Müşteri bulunamadı"}), 404
    reseller = customer.reseller
    if not reseller: return jsonify({"error": "Müşterinin bayisi bulunamadı."}), 500

    # Modül bazlı lisans kontrolü
    if reseller.remaining_module_licenses.get(assigned_module_key, 0) < 1:
        mod_name = AVAILABLE_MODULES.get(assigned_module_key, assigned_module_key)
        return jsonify({
            "error": f"Bayi '{reseller.name}' için '{mod_name}' modülünde yeterli lisans yok. Kalan: {reseller.remaining_module_licenses.get(assigned_module_key, 0)}"
        }), 400
    
    rtsp_url_to_add = data['rtspUrl'].strip()
    if not rtsp_url_to_add: return jsonify({"error": "RTSP URL boş olamaz."}), 400
    if Camera.query.filter_by(rtsp_url=rtsp_url_to_add).first():
        return jsonify({"error": f"RTSP URL '{rtsp_url_to_add}' zaten kullanılıyor."}), 409

    try:
        mod_disp_name = AVAILABLE_MODULES.get(assigned_module_key, assigned_module_key)
        cam_name = data.get('name', f"Kamera - {mod_disp_name}")
        new_camera = Camera(
            name=cam_name, rtsp_url=rtsp_url_to_add, assigned_module=assigned_module_key,
            customer_id=customer_id, roi_coordinates=data.get('roiCoordinates'),
            analysis_time_range=data.get('analysisTimeRange')
        )
        db.session.add(new_camera)
        db.session.commit()
        return jsonify(new_camera.to_json()), 201
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": "Kamera eklenirken hata.", "details": str(e)}), 500

@app.route('/cameras/<int:id>', methods=['GET'])
def get_camera(id):
    try:
        camera = Camera.query.get(id)
        if camera: return jsonify(camera.to_json()), 200
        else: return jsonify({"error": "Kamera bulunamadı"}), 404
    except Exception as e: return jsonify({"error": "Kamera getirilirken hata.", "details": str(e)}), 500

@app.route('/cameras/<int:id>', methods=['PUT'])
def update_camera(id):
    data = request.get_json()
    if not data: return jsonify({"error": "Veri gönderilmedi"}), 400
    camera = Camera.query.get(id)
    if not camera: return jsonify({"error": "Güncellenecek kamera bulunamadı"}), 404
    
    # Kameranın modülünü değiştirmeye izin VERMİYORUZ.
    if 'assignedModule' in data and data['assignedModule'] != camera.assigned_module:
        return jsonify({"error": "Kameranın modülü değiştirilemez. Silip yeniden ekleyin."}), 400

    if 'rtspUrl' in data and data['rtspUrl'].strip() != camera.rtsp_url:
        new_rtsp_url = data['rtspUrl'].strip()
        if not new_rtsp_url: return jsonify({"error": "RTSP URL boş olamaz."}), 400
        if Camera.query.filter(Camera.id != id, Camera.rtsp_url == new_rtsp_url).first():
            return jsonify({"error": "Bu RTSP URL başka kameraya ait."}), 409
        camera.rtsp_url = new_rtsp_url

    camera.name = data.get('name', camera.name)
    if 'roiCoordinates' in data: camera.roi_coordinates = data.get('roiCoordinates')

    if 'confidence_threshold' in data:
        try:
            # Gelen değerin float olup olmadığını ve 0-1 aralığında olup olmadığını kontrol et
            conf_value = float(data['confidence_threshold'])
            if 0.0 <= conf_value <= 1.0:
                camera.confidence_threshold = conf_value
            else:
                return jsonify({"error": "Güven eşiği 0.0 ile 1.0 arasında olmalıdır."}), 400
        except (ValueError, TypeError):
            return jsonify({"error": "Geçersiz güven eşiği değeri. Sayı olmalıdır."}), 400
        
    if 'analysisTimeRange' in data:
        time_range = data.get('analysisTimeRange')
        if time_range == "" or time_range is None: camera.analysis_time_range = None
        elif time_range:
            import re
            if re.match(r"^\d{2}:\d{2}-\d{2}:\d{2}$", time_range): camera.analysis_time_range = time_range
            else: return jsonify({"error": "Zaman aralığı formatı hatalı (HH:MM-HH:MM)."}), 400
    try:
        db.session.commit()
        return jsonify(camera.to_json()), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": "Kamera güncellenirken hata.", "details": str(e)}), 500

@app.route('/cameras/<int:id>', methods=['DELETE'])
def delete_camera(id):
    camera = Camera.query.get(id)
    if not camera: return jsonify({"error": "Silinecek kamera bulunamadı"}), 404
    try:
        # Kamera silinince, bayinin o modül için lisansı otomatik artar (used_module_licenses dinamik)
        db.session.delete(camera) # İlişkili alarmlar cascade ile silinir (modelde ayarlıysa)
        db.session.commit()
        return jsonify({"message": "Kamera başarıyla silindi"}), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": "Kamera silinirken hata.", "details": str(e)}), 500
        
# --- Müşteri Varlık Modu (DEĞİŞİKLİK YOK) ---
@app.route('/customers/<int:id>/toggle-presence', methods=['POST'])
def toggle_customer_presence(id):
    customer = db.session.get(Customer, id)
    if not customer: return jsonify({"error": "Müşteri bulunamadı"}), 404
    now_utc = datetime.now(timezone.utc)
    data = request.get_json()
    action = data.get('action', 'toggle') # 'activate', 'cancel', or 'toggle'
    try:
        if action == 'cancel':
            customer.is_present_until = None
        elif action == 'activate':
            customer.is_present_until = now_utc + timedelta(minutes=15)
        else: # toggle
            if customer.is_present: customer.is_present_until = None
            else: customer.is_present_until = now_utc + timedelta(minutes=15)
        db.session.commit()
        return jsonify(customer.to_json()), 200 # Güncel müşteri bilgisini dön
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": "Varlık modu güncellenirken hata.", "details": str(e)}), 500

if __name__ == '__main__':
    # Geliştirme için debug=True, üretimde False olmalı.
    # host='0.0.0.0' ağdaki diğer cihazlardan erişim için.
    app.run(debug=True, host='0.0.0.0', port=5000)

# --- END OF FILE app.py (MODÜL BAZLI LİSANS GÜNCELLENMİŞ HALİ) ---