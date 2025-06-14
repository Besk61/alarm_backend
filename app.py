# --- START OF FILE app.py ---

from flask import Flask, request, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_cors import CORS
from models import Alarm, db, Reseller, Customer, Camera, ApprovalRequest
from datetime import datetime
from werkzeug.security import check_password_hash # Eklemeyi unutma
from werkzeug.security import generate_password_hash # Eklemeyi unutma
from sqlalchemy import func, extract

from sqlalchemy import func, extract, Date, cast
from sqlalchemy.orm import aliased
from collections import defaultdict
import calendar # Aylık veriler için
import json
import cv2
import base64
import os

app = Flask(__name__)

app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///alarm_merkezi.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

ADMIN_EMAIL = os.environ.get('ADMIN_EMAIL', 'admin@alarmmerkezi.com')
ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD', 'Alarm123!')

db.init_app(app)

CORS(app)

with app.app_context():
    db.create_all() # Yeni ApprovalRequest modelini oluşturması için
    print("Veritabanı tabloları oluşturuldu (eğer yoksa).")

    if not Reseller.query.filter_by(email='admin@bayi.com').first():
        print("Test bayisi oluşturuluyor...")
        test_reseller = Reseller(
            name='Test Bayisi A.Ş.',
            email='admin@bayi.com',
            password_hash='password123',
            phone='5551234567',
            status='Active',
            licenses=100,
            join_date=datetime.now().strftime('%Y-%m-%d')
        )
        db.session.add(test_reseller)
        db.session.commit()
        print(f"Test Bayisi '{test_reseller.name}' başarıyla oluşturuldu.")

# === API Endpoint'leri ===

@app.route('/dashboard/stats/hourly-detections', methods=['GET'])
def get_hourly_detections():
    customer_id = request.args.get('customerId', type=int)
    # reseller_id = request.args.get('resellerId', type=int) # Bayi için de eklenebilir

    today = datetime.utcnow().date()
    
    query = db.session.query(
        extract('hour', Alarm.timestamp).label('hour'),
        func.count(Alarm.id).label('value')
    ).filter(
        func.date(Alarm.timestamp) == today
    )

    if customer_id:
        # Müşteri var mı kontrol et
        customer = Customer.query.get(customer_id)
        if not customer:
            return jsonify({"error": "Müşteri bulunamadı"}), 404
        query = query.filter(Alarm.customer_id == customer_id)
    # else if reseller_id:
        # Bayiye ait müşterilerin alarmları... (daha karmaşık sorgu)
    # else:
        # Hiçbir ID yoksa, yetkiye göre tüm alarmlar veya hata. Şimdilik ID zorunlu varsayalım.
    #    return jsonify({"error": "customerId veya resellerId gereklidir"}), 400


    results = query.group_by(extract('hour', Alarm.timestamp)).order_by('hour').all()

    hourly_data = [{"hour": str(r.hour).zfill(2), "value": r.value} for r in results]
    all_hours = {str(h).zfill(2): 0 for h in range(24)}
    for item in hourly_data:
        all_hours[item['hour']] = item['value']
    
    final_data = [{"hour": h, "value": v} for h, v in sorted(all_hours.items())]
    
    return jsonify(final_data), 200


# YENİ: Kamera Bazlı Tespit Dağılımı
@app.route('/dashboard/stats/camera-detections', methods=['GET'])
def get_camera_detections():
    customer_id = request.args.get('customerId', type=int)
    if not customer_id:
        return jsonify({"error": "customerId gereklidir"}), 400

    customer = Customer.query.get(customer_id)
    if not customer:
        return jsonify({"error": "Müşteri bulunamadı"}), 404

    # Son X gün filtresi eklenebilir (örn: request.args.get('days', default=30, type=int))
    
    results = db.session.query(
        Camera.name.label('name'), # Veya Camera.id
        func.count(Alarm.id).label('value')
    ).join(Alarm, Camera.id == Alarm.camera_id)\
     .filter(Alarm.customer_id == customer_id)\
     .group_by(Camera.name)\
     .order_by(func.count(Alarm.id).desc())\
     .all() # .limit(10) gibi bir limit eklenebilir çok fazla kamera varsa

    # Frontend'in beklediği format: [{ name: 'Kamera 1', value: 10 }, ...]
    camera_data = [{"name": r.name if r.name else f"Kamera ID {r_id}", "value": r.value} 
                   for r_id, r in enumerate(results, 1)] # Eğer kamera adı yoksa ID kullan
                   # Düzeltme: result tuple değil, Camera.name ve value içeriyor
    camera_data = [{"name": r.name if r.name else f"Kamera_{r.camera_id}", "value": r.value} for r in results]


    # Eğer kamera adı yoksa, kamera ID'sini kullanmak için sorguyu değiştirebiliriz:
    # results = db.session.query(
    #     Alarm.camera_id.label('camera_id_val'), 
    #     Camera.name.label('camera_name_val'),
    #     func.count(Alarm.id).label('value')
    # ).outerjoin(Camera, Alarm.camera_id == Camera.id)\
    #  .filter(Alarm.customer_id == customer_id)\
    #  .group_by(Alarm.camera_id, Camera.name)\
    #  .order_by(func.count(Alarm.id).desc())\
    #  .all()
    # camera_data = [{"name": r.camera_name_val if r.camera_name_val else f"ID: {r.camera_id_val}", "value": r.value} for r in results]
    
    # Donut chart için renkleri backend'de de atayabiliriz veya frontend'de bırakabiliriz.
    # Örnek renkler (frontend'deki gibi)
    colors = ['#3B82F6', '#4F46E5', '#8B5CF6', '#EC4899', '#F97316', '#EF4444', '#10B981']
    for i, item in enumerate(camera_data):
        item['color'] = colors[i % len(colors)]
        
    return jsonify(camera_data), 200

# YENİ: Risk Dağılımı (Modül Bazlı)
@app.route('/dashboard/stats/module-detections', methods=['GET'])
def get_module_detections():
    customer_id = request.args.get('customerId', type=int)
    if not customer_id:
        return jsonify({"error": "customerId gereklidir"}), 400
    
    customer = Customer.query.get(customer_id)
    if not customer:
        return jsonify({"error": "Müşteri bulunamadı"}), 404

    results = db.session.query(
        Alarm.module_name.label('name'),
        func.count(Alarm.id).label('value')
    ).filter(Alarm.customer_id == customer_id)\
     .group_by(Alarm.module_name)\
     .order_by(func.count(Alarm.id).desc())\
     .all()

    module_data = [{"name": r.name if r.name else "Bilinmeyen Modül", "value": r.value} for r in results]
    
    # Renk ataması (opsiyonel)
    colors = ['#4F46E5', '#F97316', '#3B82F6', '#EC4899', '#EF4444', '#10B981', '#8B5CF6']
    for i, item in enumerate(module_data):
        item['color'] = colors[i % len(colors)]
        
    return jsonify(module_data), 200

# YENİ: Kategori Bazlı Tespit Dağılımı
@app.route('/dashboard/stats/category-detections', methods=['GET'])
def get_category_detections():
    customer_id = request.args.get('customerId', type=int)
    if not customer_id:
        return jsonify({"error": "customerId gereklidir"}), 400

    customer = Customer.query.get(customer_id)
    if not customer:
        return jsonify({"error": "Müşteri bulunamadı"}), 404
        
    results = db.session.query(
        Alarm.category.label('name'),
        func.count(Alarm.id).label('value')
    ).filter(Alarm.customer_id == customer_id)\
     .group_by(Alarm.category)\
     .order_by(func.count(Alarm.id).desc())\
     .all()

    category_data = [{"name": r.name if r.name else "Bilinmeyen Kategori", "value": r.value} for r in results]
    
    # Renk ataması (opsiyonel)
    colors = ['#EF4444', '#F97316', '#4F46E5', '#3B82F6', '#EC4899', '#10B981', '#8B5CF6']
    for i, item in enumerate(category_data):
        item['color'] = colors[i % len(colors)]
        
    return jsonify(category_data), 200

# YENİ: Aylık Tespitler
@app.route('/dashboard/stats/monthly-detections', methods=['GET'])
def get_monthly_detections():
    customer_id = request.args.get('customerId', type=int)
    year = request.args.get('year', type=int, default=datetime.utcnow().year)

    if not customer_id:
        return jsonify({"error": "customerId gereklidir"}), 400

    customer = Customer.query.get(customer_id)
    if not customer:
        return jsonify({"error": "Müşteri bulunamadı"}), 404

    results = db.session.query(
        extract('month', Alarm.timestamp).label('month_num'),
        func.count(Alarm.id).label('value')
    ).filter(Alarm.customer_id == customer_id)\
     .filter(extract('year', Alarm.timestamp) == year)\
     .group_by(extract('month', Alarm.timestamp))\
     .order_by('month_num')\
     .all()

    # Tüm ayları 0 değeriyle başlat
    monthly_counts = {month_num: 0 for month_num in range(1, 13)}
    for r in results:
        monthly_counts[r.month_num] = r.value

    # Ay isimlerini al (Türkçe için locale ayarı gerekebilir veya manuel liste)
    # calendar.month_name İngilizce döner, Türkçe için bir map kullanabiliriz.
    tr_month_names = ["", "Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran", 
                      "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık"]

    monthly_data = [{"month": tr_month_names[month_num], "value": count} 
                    for month_num, count in monthly_counts.items()]
    
    return jsonify(monthly_data), 200


# Müşterinin kendi alarmlarını listelemesi için
@app.route('/customers/<int:customer_id>/alarms', methods=['GET'])
def get_customer_alarms_list(customer_id):
    # Yetkilendirme: Giriş yapan müşteri sadece kendi alarmlarını görmeli
    # Bu kontrolü bir decorator veya token ile yapmak daha iyi olur.
    customer = Customer.query.get(customer_id)
    if not customer:
        return jsonify({"error": "Müşteri bulunamadı"}), 404
    
    # Filtreleme ve sayfalama parametreleri eklenebilir (request.args)
    alarms = Alarm.query.filter_by(customer_id=customer_id).order_by(Alarm.timestamp.desc()).all()
    return jsonify([alarm.to_json() for alarm in alarms]), 200

# Bayinin, müşterilerinin alarmlarını listelemesi için (opsiyonel, dashboard için gerekebilir)
@app.route('/resellers/<int:reseller_id>/alarms', methods=['GET'])
def get_reseller_customer_alarms(reseller_id):
    reseller = Reseller.query.get(reseller_id)
    if not reseller:
        return jsonify({"error": "Bayi bulunamadı"}), 404

    # Bu endpoint tüm müşterilerinin tüm alarmlarını getireceği için dikkatli kullanılmalı,
    # Sayfalama ve filtreleme kesinlikle eklenmeli.
    # Şimdilik örnek amaçlı, tümünü getiriyor.
    customer_ids = [customer.id for customer in reseller.customers_rel]
    alarms = Alarm.query.filter(Alarm.customer_id.in_(customer_ids)).order_by(Alarm.timestamp.desc()).all()
    return jsonify([alarm.to_json() for alarm in alarms]), 200

# Test amaçlı alarm oluşturma (Gerçekte bu, analiz sisteminden tetiklenir)
@app.route('/alarms/test-create', methods=['POST'])
def create_test_alarm():
    data = request.get_json()
    # customer_id, camera_id, alarm_type vb. data'dan alınır.
    # Örnek bir alarm:
    try:
        customer = Customer.query.get(data.get('customerId'))
        camera = Camera.query.get(data.get('cameraId'))
        if not customer or not camera:
            return jsonify({"error": "Customer or Camera not found"}), 404

        new_alarm = Alarm(
            customer_id=customer.id,
            camera_id=camera.id,
            alarm_type=data.get('alarmType', 'Test Alarm'),
            category=data.get('category', 'Critical'),
            event_details=data.get('event', 'test_event'),
            module_name=data.get('module', 'Test_Module'),
            timestamp=datetime.strptime(data.get('datetime'), '%d.%m.%Y %H:%M:%S') if data.get('datetime') else datetime.utcnow(),
            image_url=data.get('imageUrl', 'https://images.pexels.com/photos/9875441/pexels-photo-9875441.jpeg')
        )
        db.session.add(new_alarm)
        db.session.commit()
        return jsonify(new_alarm.to_json()), 201
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": "Error creating test alarm", "details": str(e)}), 500

# --- MÜŞTERİ ENDPOINTS (Önceki haliyle korunuyor) ---

@app.route('/resellers/<int:reseller_id>/customers/<int:customer_id>/set-credentials', methods=['POST'])
def set_customer_credentials(reseller_id, customer_id):
    # Burada bayinin bu müşteriye erişim yetkisi olup olmadığını kontrol etmek iyi bir pratik olur.
    # Örneğin, request.headers'dan gelen bir bayi token'ı ile reseller_id doğrulanabilir.
    # Şimdilik basit tutuyoruz.
    
    reseller = Reseller.query.get(reseller_id)
    if not reseller:
        return jsonify({"error": "Bayi bulunamadı"}), 404

    customer = Customer.query.filter_by(id=customer_id, reseller_id=reseller_id).first()
    if not customer:
        return jsonify({"error": "Bayiye ait müşteri bulunamadı"}), 404

    data = request.get_json()
    password = data.get('password')

    if not password:
        return jsonify({"error": "Password is required"}), 400
    
    # Şifre için minimum uzunluk vs. gibi kontroller eklenebilir
    if len(password) < 6:
        return jsonify({"error": "Password must be at least 6 characters long"}), 400

    customer.password_hash = generate_password_hash(password)
    try:
        db.session.commit()
        return jsonify({"message": f"Customer {customer.name} credentials set successfully."}), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": "Error setting customer credentials.", "details": str(e)}), 500

# --- AUTH ENDPOINTS ---

@app.route('/customer/login', methods=['POST'])
def customer_login():
    data = request.get_json()
    email = data.get('email')
    password = data.get('password')

    if not email or not password:
        return jsonify({"error": "Email and password are required"}), 400

    customer = Customer.query.filter_by(email=email).first()

    if not customer: # Önce müşteri var mı kontrol et
        return jsonify({"error": "Invalid email or password"}), 401
    
    if not customer.password_hash: # Şifresi hiç atanmamış müşteri
        return jsonify({"error": "Account not fully set up. Please contact your reseller to set a password."}), 401
    
    if not check_password_hash(customer.password_hash, password):
        return jsonify({"error": "Invalid email or password"}), 401
    
    if customer.status != 'Active':
        return jsonify({"error": "Your account is inactive. Please contact your reseller."}), 403

    # Müşteri için dönecek JSON'ı özelleştirebiliriz
    # Örneğin, sadece gerekli bilgileri ve bir "role" bilgisi
    customer_data = customer.to_json() # to_json() metodunuz zaten gerekli bilgileri içeriyor
    customer_data['role'] = 'customer' 
    # Token da döndürebilirsiniz (JWT vb.)
    # customer_data['token'] = generate_customer_token(customer.id) # Örnek
    return jsonify(customer_data), 200

@app.route('/admin/login', methods=['POST'])
def admin_login():
    data = request.get_json()
    email = data.get('email')
    password = data.get('password')

    if not email or not password:
        return jsonify({"error": "Email and password are required"}), 400

    # Sadece ve sadece admin bilgileriyle karşılaştır.
    if email == ADMIN_EMAIL and password == ADMIN_PASSWORD:
        print("Admin girişi başarılı!")
        # Admin için özel bir "user" objesi oluşturalım.
        # Bu obje, frontend'deki Reseller arayüzü ile uyumlu olmalıdır.
        admin_user_obj = {
            'id': 0,
            'name': 'Sistem Yöneticisi',
            'email': ADMIN_EMAIL,
            'phone': 'N/A',
            'customers': 0,
            'cameras': 0,
            'status': 'Active',
            'licenses': 9999,
            'remainingLicenses': 9999,
            'joinDate': datetime.now().strftime('%Y-%m-%d')
        }
        return jsonify(admin_user_obj), 200
    else:
        # Gelen bilgiler admin'e ait değilse, giriş başarısızdır.
        print(f"Başarısız admin girişi denemesi: {email}")
        return jsonify({"error": "Invalid admin credentials. Access denied."}), 401

# --- MEVCUT AUTH ENDPOINTS (Bayiler için - DOKUNMA) ---

@app.route('/login', methods=['POST'])
def login():
    # BU FONKSİYON OLDUĞU GİBİ KALIYOR.
    # BAYİLERİN GİRİŞİ İÇİN KULLANILMAYA DEVAM EDECEK.
    data = request.get_json()
    email = data.get('email')
    password = data.get('password')

    if not email or not password:
        return jsonify({"error": "Email and password are required"}), 400

    reseller = Reseller.query.filter_by(email=email).first()

    if not reseller or reseller.password_hash != password:
        return jsonify({"error": "Invalid email or password"}), 401
    
    if reseller.status != 'Active':
        return jsonify({"error": "Hesabınız pasif. Lütfen yöneticinizle iletişime geçin."}), 403

    return jsonify(reseller.to_json()), 200

# --- RESELLER ENDPOINTS (Önceki haliyle korunuyor) ---

@app.route('/resellers', methods=['POST'])
def add_reseller():
    data = request.get_json()
    if not data:
        return jsonify({"error": "Veri gönderilmedi"}), 400

    required_fields = ['name', 'email', 'password_hash', 'licenses']
    for field in required_fields:
        if field not in data or not data[field]:
            return jsonify({"error": f"Eksik alan: {field}"}), 400

    if Reseller.query.filter_by(email=data['email']).first():
        return jsonify({"error": "Bu e-posta adresi zaten kayıtlı."}), 409
    if Reseller.query.filter_by(name=data['name']).first():
        return jsonify({"error": "Bu bayi adı zaten kayıtlı."}), 409

    try:
        new_reseller = Reseller(
            name=data['name'],
            email=data['email'],
            password_hash=data['password_hash'],
            phone=data.get('phone', ''),
            licenses=data['licenses'],
            status=data.get('status', 'Active'),
            join_date=data.get('joinDate', datetime.now().strftime('%Y-%m-%d'))
        )
        db.session.add(new_reseller)
        db.session.commit()
        return jsonify(new_reseller.to_json()), 201
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": "Bayi eklenirken bir hata oluştu.", "details": str(e)}), 500

@app.route('/resellers', methods=['GET'])
def get_resellers():
    try:
        resellers = Reseller.query.all()
        return jsonify([reseller.to_json() for reseller in resellers]), 200
    except Exception as e:
        return jsonify({"error": "Bayiler listelenirken bir hata oluştu.", "details": str(e)}), 500

@app.route('/resellers/<int:id>', methods=['GET'])
def get_reseller(id):
    try:
        reseller = Reseller.query.get(id)
        if reseller:
            return jsonify(reseller.to_json()), 200
        else:
            return jsonify({"error": "Bayi bulunamadı"}), 404
    except Exception as e:
        return jsonify({"error": "Bayi getirilirken bir hata oluştu.", "details": str(e)}), 500

@app.route('/resellers/<int:id>', methods=['PUT'])
def update_reseller(id):
    data = request.get_json()
    if not data:
        return jsonify({"error": "Veri gönderilmedi"}), 400

    reseller = Reseller.query.get(id)
    if not reseller:
        return jsonify({"error": "Güncellenecek bayi bulunamadı"}), 404

    if 'email' in data and data['email'] != reseller.email:
        if Reseller.query.filter(Reseller.id != id, Reseller.email == data['email']).first():
            return jsonify({"error": "Bu e-posta adresi başka bir bayiye ait."}), 409
        reseller.email = data['email']

    if 'name' in data and data['name'] != reseller.name:
        if Reseller.query.filter(Reseller.id != id, Reseller.name == data['name']).first():
            return jsonify({"error": "Bu bayi adı başka bir bayiye ait."}), 409
        reseller.name = data['name']
    
    reseller.phone = data.get('phone', reseller.phone)
    reseller.licenses = data.get('licenses', reseller.licenses)
    reseller.status = data.get('status', reseller.status)
    reseller.join_date = data.get('joinDate', reseller.join_date)
    reseller.password_hash = data.get('password_hash', reseller.password_hash)

    try:
        db.session.commit()
        return jsonify(reseller.to_json()), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": "Bayi güncellenirken bir hata oluştu.", "details": str(e)}), 500

@app.route('/resellers/<int:id>', methods=['DELETE'])
def delete_reseller(id):
    reseller = Reseller.query.get(id)
    if not reseller:
        return jsonify({"error": "Silinecek bayi bulunamadı"}), 404

    try:
        db.session.delete(reseller)
        db.session.commit()
        return jsonify({"message": "Bayi başarıyla silindi"}), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": "Bayi silinirken bir hata oluştu. İlişkili müşterileri olabilir.", "details": str(e)}), 500

# --- INSTALLATION REQUEST ENDPOINTS (YENİ EKLEME VE DEĞİŞİKLİK) ---

@app.route('/streams', methods=['GET'])
def get_streams_for_yolo():
    active_cameras_data = []
    customers = Customer.query.filter_by(status='Active').all()
    for customer in customers:
        cameras = Camera.query.filter_by(customer_id=customer.id).all()
        for camera in cameras:
            polygon_data = "0"
            if camera.roi_coordinates:
                try:
                    loaded_roi_objects = json.loads(camera.roi_coordinates) # Bu [{"x": val, "y": val}, ...] formatında

                    # Gelen formatın bir liste olup olmadığını ve elemanlarının sözlük olup olmadığını kontrol et
                    if isinstance(loaded_roi_objects, list) and all(isinstance(item, dict) for item in loaded_roi_objects):
                        # Şimdi [{"x": val, "y": val}, ...] formatından [[x,y], ...] formatına dönüştür
                        converted_polygon = []
                        valid_polygon_format = True
                        for point_obj in loaded_roi_objects:
                            if 'x' in point_obj and 'y' in point_obj and \
                               isinstance(point_obj['x'], (int, float)) and \
                               isinstance(point_obj['y'], (int, float)):
                                converted_polygon.append([point_obj['x'], point_obj['y']])
                            else:
                                valid_polygon_format = False
                                print(f"[!] Kamera {camera.id} ({camera.name}) için roi_coordinates içindeki bir nokta objesi hatalı formatta: {point_obj}")
                                break # Bir nokta bile hatalıysa dönüşümü durdur

                        if valid_polygon_format and len(converted_polygon) >= 3:
                            polygon_data = converted_polygon
                        elif not valid_polygon_format:
                            # Hata zaten yukarıda loglandı
                            pass
                        else: # len(converted_polygon) < 3
                            print(f"[!] Kamera {camera.id} ({camera.name}) için roi_coordinates geçerli bir poligona dönüştürülemedi (nokta sayısı < 3): {converted_polygon}")
                    else:
                        print(f"[!] Kamera {camera.id} ({camera.name}) için roi_coordinates beklenen formatta değil (objeler listesi olmalı): {loaded_roi_objects}")

                except json.JSONDecodeError:
                    print(f"[!] Kamera {camera.id} ({camera.name}) için roi_coordinates JSON parse edilemedi: {camera.roi_coordinates}")
                except Exception as e:
                    print(f"[!] Kamera {camera.id} ({camera.name}) için roi_coordinates işlenirken genel hata: {e}, Veri: {camera.roi_coordinates}")
            
            time_range_data = "0"
            if hasattr(camera, 'analysis_time_range') and camera.analysis_time_range:
                time_range_data = camera.analysis_time_range
            else: # Eğer analysis_time_range attribute'u yoksa veya None/boş ise
                if hasattr(camera, 'analysis_time_range'): # None veya boş olma durumu
                    pass # time_range_data zaten "0"
                else: # Attribute hiç yoksa (modelde tanımlanmamışsa)
                    print(f"[i] Kamera {camera.id} ({camera.name}) için 'analysis_time_range' özelliği bulunmuyor. Varsayılan ('0') kullanılıyor.")


            active_cameras_data.append({
                "rtsp_url": camera.rtsp_url,
                "camera_id": camera.id,
                "customer_id": customer.id,
                "customer_name": customer.name,
                "camera_name": camera.name,
                "polygon": polygon_data, # Artık doğru formatta olmalı
                "time_range": time_range_data,
                "module_name": "YOLOv8_Person_Detection",
                "alarm_type": "İnsan Tespiti"
            })
    return jsonify(active_cameras_data), 200



@app.route('/api/rtsp-frame', methods=['GET'])
def get_rtsp_frame():
    rtsp_url = request.args.get('url')
    if not rtsp_url:
        return jsonify({"error": "RTSP URL parametresi (url) eksik."}), 400

    cap = None  # cap değişkenini try bloğunun dışında tanımla
    try:
        print(f"Frame alınmaya çalışılıyor: {rtsp_url}")
        
        # OpenCV'nin RTSP için TCP kullanmasını zorla (UDP bazen sorun çıkarabilir)
        # Bu satırı kullanmak için OpenCV'nin FFmpeg ile derlenmiş olması gerekir.
        # Bazı durumlarda `os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "rtsp_transport;tcp"`
        # veya `os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "rtsp_transport;udp"` 
        # gibi ortam değişkenlerini Flask uygulamanız başlamadan önce ayarlamak daha etkili olabilir.
        # cap = cv2.VideoCapture(rtsp_url, cv2.CAP_FFMPEG)
        
        cap = cv2.VideoCapture(rtsp_url)

        if not cap.isOpened():
            print(f"Hata: RTSP stream açılamadı - {rtsp_url}")
            # Daha detaylı hata loglaması eklenebilir
            # Örneğin, FFmpeg loglarını yakalamaya çalışmak (bu daha karmaşıktır)
            return jsonify({"error": f"RTSP stream açılamadı. URL'yi kontrol edin veya kamera offline olabilir. URL: {rtsp_url}"}), 500

        # Birkaç frame okumayı dene, bazen ilk frame'ler boş gelebilir
        ret = False
        for _ in range(5): # En fazla 5 frame dene
            ret, frame = cap.read()
            # if ret and frame is not None and frame.size > 0:
            #     break
            # cv2.waitKey(10) # Kısa bir bekleme (opsiyonel, bağlantı süresini uzatabilir)
        
        if not ret or frame is None or frame.size == 0:
            print(f"Hata: RTSP stream'den geçerli frame okunamadı - {rtsp_url}")
            return jsonify({"error": f"RTSP stream'den geçerli frame okunamadı. Kamera aktif mi? URL: {rtsp_url}"}), 500

        print(f"Frame başarıyla alındı: {rtsp_url}")
        
        # Frame'i JPEG formatına encode et
        ret, buffer = cv2.imencode('.jpg', frame)
        if not ret:
            print(f"Hata: Frame JPEG formatına encode edilemedi - {rtsp_url}")
            return jsonify({"error": "Frame encode edilirken hata oluştu."}), 500

        # Encode edilmiş frame'i base64 string'e çevir
        frame_base64 = base64.b64encode(buffer).decode('utf-8')
        
        # Frontend'in Blob URL oluşturabilmesi için image/jpeg tipini de belirtmek iyi olur
        # Ancak frontend tarafı zaten response.blob() ile tipi alabilir.
        # Biz direkt base64 string'i dönelim. Frontend bunu `data:image/jpeg;base64,` ile kullanacak.
        # return jsonify({"imageData": frame_base64, "imageType": "image/jpeg"}), 200
        
        # VEYA doğrudan response olarak image gönderebiliriz (frontend'deki fetch bunu Blob olarak alır)
        # Bu durumda frontend'de `URL.createObjectURL(response.blob())` kullanılacak.
        # `Settings.tsx` dosyasındaki `fetchCameraFrame` fonksiyonu zaten bu şekilde yazılmış.
        
        return buffer.tobytes(), 200, {'Content-Type': 'image/jpeg'}

    except cv2.error as e:
        print(f"OpenCV hatası ({rtsp_url}): {e}")
        return jsonify({"error": f"OpenCV hatası: {e}"}), 500
    except Exception as e:
        print(f"Genel hata ({rtsp_url}): {e}")
        # traceback modülü ile daha detaylı hata logu alınabilir
        import traceback
        print(traceback.format_exc())
        return jsonify({"error": f"Bilinmeyen bir hata oluştu: {e}"}), 500
    finally:
        if cap and cap.isOpened():
            cap.release()
            print(f"RTSP stream serbest bırakıldı: {rtsp_url}")

# 1. Yeni Kurulum Talebi Oluştur (POST /installation-requests)
@app.route('/installation-requests', methods=['POST'])
def create_installation_request():
    data = request.get_json()
    if not data:
        return jsonify({"error": "Veri gönderilmedi"}), 400

    # 'customerSirenIpAddress' ve 'customerAdditionalId' artık opsiyonel
    required_fields = ['fullName', 'email', 'nationalId', 'rtspStreams', 'resellerId', 'licenseExpiry']
    for field in required_fields:
        if field not in data or not data[field]:
            return jsonify({"error": f"Eksik alan: {field}"}), 400

    reseller = Reseller.query.get(data['resellerId'])
    if not reseller:
        return jsonify({"error": "Belirtilen bayi bulunamadı"}), 404
    
    # E-posta veya TC Kimlik Numarası zaten mevcut bir müşteri veya beklemedeki talepte var mı kontrol et
    if Customer.query.filter_by(email=data['email']).first() or \
       ApprovalRequest.query.filter_by(customer_email=data['email'], status='Pending').first():
        return jsonify({"error": "Bu e-posta adresi zaten kayıtlı veya beklemede."}), 409
    if Customer.query.filter_by(national_id=data['nationalId']).first() or \
       ApprovalRequest.query.filter_by(customer_national_id=data['nationalId'], status='Pending').first():
        return jsonify({"error": "Bu TC Kimlik Numarası zaten kayıtlı veya beklemede."}), 409

    # RTSP URL'lerinin de benzersizliğini kontrol et (hem mevcut kameralar hem de beklemedeki talepler içinde)
    for rtsp_url in data.get('rtspStreams', []):
        if Camera.query.filter_by(rtsp_url=rtsp_url).first():
            return jsonify({"error": f"RTSP URL '{rtsp_url}' zaten bir kamera tarafından kullanılıyor."}), 409
        # Not: ApprovalRequest'teki rtsp_urls bir liste olduğu için her birini kontrol etmek daha karmaşıktır.
        # Basitlik adına, sadece Customer/Camera tablosundaki benzersizliği kontrol edelim.

    # Lisans kontrolü: Talep anında da lisans yetersizse bildirebiliriz.
    # Ancak asıl kontrol, onaylama aşamasında yapılacaktır.
    requested_camera_count = len(data.get('rtspStreams', []))
    if reseller.remaining_licenses < requested_camera_count:
        return jsonify({
            "error": f"Bayi '{reseller.name}' için yeterli lisans yok. {requested_camera_count} kamera için lisans talep edildi, bayinin kalan lisansı: {reseller.remaining_licenses}"
        }), 400

    try:
        new_request = ApprovalRequest(
            reseller_id=data['resellerId'],
            customer_name=data['fullName'],
            customer_email=data['email'],
            customer_phone=data.get('phone', ''),
            customer_national_id=data['nationalId'],
            customer_license_expiry=data['licenseExpiry'],
            customer_siren_ip_address=data.get('sirenIpAddress'), # Yeni eklendi
            customer_additional_id=data.get('additionalId'),     # Yeni eklendi
            rtsp_urls=data['rtspStreams'],
            status='Pending',
            request_date=datetime.now().strftime('%Y-%m-%d')
        )
        db.session.add(new_request)
        db.session.commit()
        return jsonify(new_request.to_json()), 201
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": "Kurulum talebi oluşturulurken bir hata oluştu.", "details": str(e)}), 500

# (ADMIN TARAFINDAN KULLANILACAK ENDPOINT'LER - ÖN YÜZDE İŞLENMEYECEK)
# 2. Kurulum Taleplerini Listele (GET /installation-requests)
@app.route('/installation-requests', methods=['GET'])
def get_installation_requests():
    try:
        requests = ApprovalRequest.query.all()
        return jsonify([req.to_json() for req in requests]), 200
    except Exception as e:
        return jsonify({"error": "Kurulum talepleri listelenirken bir hata oluştu.", "details": str(e)}), 500

# 3. Kurulum Talebini Onayla (PUT /installation-requests/<id>/approve)
@app.route('/installation-requests/<int:request_id>/approve', methods=['PUT'])
def approve_installation_request(request_id):
    approval_request = ApprovalRequest.query.get(request_id)
    if not approval_request:
        return jsonify({"error": "Onay talebi bulunamadı"}), 404
    
    if approval_request.status != 'Pending':
        return jsonify({"error": "Bu talep zaten işlenmiş."}), 400

    reseller = Reseller.query.get(approval_request.reseller_id)
    if not reseller:
        return jsonify({"error": "Talep ile ilişkili bayi bulunamadı."}), 500
    
    # Onay anında lisans kontrolü
    requested_camera_count = len(approval_request.rtsp_urls)
    if reseller.remaining_licenses < requested_camera_count:
        # Lisans yetersizse, talebi otomatik olarak reddet
        approval_request.status = 'Rejected'
        approval_request.approval_date = datetime.now().strftime('%Y-%m-%d')
        db.session.commit()
        return jsonify({
            "error": f"Bayi '{reseller.name}' için yeterli lisans yok. Talep reddedildi.",
            "details": f"{requested_camera_count} kamera için lisans talep edildi, bayinin kalan lisansı: {reseller.remaining_licenses}"
        }), 400
    
    # Müşteri Email veya TC No zaten mevcut mu kontrol et (kesinleşmiş müşteriler arasında)
    if Customer.query.filter_by(email=approval_request.customer_email).first() or \
       Customer.query.filter_by(national_id=approval_request.customer_national_id).first():
        approval_request.status = 'Rejected' # Çakışma durumunda reddet
        approval_request.approval_date = datetime.now().strftime('%Y-%m-%d')
        db.session.commit()
        return jsonify({"error": "Bu e-posta veya TC Kimlik Numarası zaten kayıtlı bir müşteriye ait. Talep reddedildi."}), 409

    try:
        # Yeni müşteri oluştur
        new_customer = Customer(
            name=approval_request.customer_name,
            email=approval_request.customer_email,
            phone=approval_request.customer_phone,
            national_id=approval_request.customer_national_id,
            registration_date=approval_request.request_date,
            license_expiry=approval_request.customer_license_expiry,
            siren_ip_address=approval_request.customer_siren_ip_address, # Yeni eklendi
            additional_id=approval_request.customer_additional_id,     # Yeni eklendi
            reseller_id=approval_request.reseller_id,
            status='Active'
        )
        db.session.add(new_customer)
        db.session.flush() # new_customer.id'yi almak için

        # Kameraları ekle
        for rtsp_url in approval_request.rtsp_urls:
            if Camera.query.filter_by(rtsp_url=rtsp_url).first(): # Tekrar benzersizlik kontrolü
                raise Exception(f"RTSP URL '{rtsp_url}' zaten başka bir kamera tarafından kullanılıyor. Onay iptal edildi.")
            new_camera = Camera(
                name=f"Kamera {len(new_customer.cameras_rel.all()) + 1}",
                rtsp_url=rtsp_url,
                # siren_ip_address artık burada yok
                customer_id=new_customer.id
            )
            db.session.add(new_camera)
        
        # Talep durumunu güncelle
        approval_request.status = 'Approved'
        approval_request.approval_date = datetime.now().strftime('%Y-%m-%d')
        
        db.session.commit()
        return jsonify({"message": "Kurulum talebi başarıyla onaylandı ve müşteri oluşturuldu.", "customer": new_customer.to_json()}), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": "Kurulum talebi onaylanırken bir hata oluştu.", "details": str(e)}), 500

# 4. Kurulum Talebini Reddet (PUT /installation-requests/<id>/reject)
@app.route('/installation-requests/<int:request_id>/reject', methods=['PUT'])
def reject_installation_request(request_id):
    approval_request = ApprovalRequest.query.get(request_id)
    if not approval_request:
        return jsonify({"error": "Reddedilecek talep bulunamadı"}), 404
    
    if approval_request.status != 'Pending':
        return jsonify({"error": "Bu talep zaten işlenmiş."}), 400

    try:
        approval_request.status = 'Rejected'
        approval_request.approval_date = datetime.now().strftime('%Y-%m-%d')
        db.session.commit()
        return jsonify({"message": "Kurulum talebi başarıyla reddedildi."}), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": "Kurulum talebi reddedilirken bir hata oluştu.", "details": str(e)}), 500


# --- CUSTOMER ENDPOINTS (Önceki haliyle korunuyor, sadece GET/PUT/DELETE) ---

@app.route('/customers', methods=['GET'])
def get_customers():
    try:
        reseller_id_filter = request.args.get('resellerId', type=int)
        search_term = request.args.get('search', type=str)

        query = Customer.query

        if reseller_id_filter:
            query = query.filter_by(reseller_id=reseller_id_filter)
        
        if search_term:
            search_ilike = f"%{search_term}%"
            query = query.filter(
                db.or_(
                    Customer.name.ilike(search_ilike),
                    Customer.email.ilike(search_ilike),
                    Customer.national_id.ilike(search_ilike),
                    Customer.additional_id.ilike(search_ilike) # Yeni eklendi
                )
            )
        
        customers = query.order_by(Customer.name).all()
        return jsonify([customer.to_json() for customer in customers]), 200
    except Exception as e:
        return jsonify({"error": "Müşteriler listelenirken bir hata oluştu.", "details": str(e)}), 500

@app.route('/customers/<int:id>', methods=['GET'])
def get_customer(id):
    try:
        customer = Customer.query.get(id)
        if customer:
            return jsonify(customer.to_json()), 200
        else:
            return jsonify({"error": "Müşteri bulunamadı"}), 404
    except Exception as e:
        return jsonify({"error": "Müşteri getirilirken bir hata oluştu.", "details": str(e)}), 500

@app.route('/customers/<int:id>', methods=['PUT'])
def update_customer(id):
    data = request.get_json()
    if not data:
        return jsonify({"error": "Veri gönderilmedi"}), 400

    customer = Customer.query.get(id)
    if not customer:
        return jsonify({"error": "Güncellenecek müşteri bulunamadı"}), 404

    if 'email' in data and data['email'] != customer.email:
        if Customer.query.filter(Customer.id != id, Customer.email == data['email']).first():
            return jsonify({"error": "Bu e-posta adresi başka bir müşteriye ait."}), 409
        customer.email = data['email']
    
    if 'nationalId' in data and data['nationalId'] != customer.national_id:
        if Customer.query.filter(Customer.id != id, Customer.national_id == data['nationalId']).first():
            return jsonify({"error": "Bu TC Kimlik Numarası başka bir müşteriye ait."}), 409
        customer.national_id = data['nationalId']
    
    # additionalId için benzersizlik kontrolü yapmıyoruz, isteğe bağlı
    if 'additionalId' in data and data['additionalId'] != customer.additional_id:
        # eğer additionalId benzersiz olması gerekiyorsa buraya kontrol eklenmeli
        customer.additional_id = data['additionalId']

    customer.name = data.get('name', customer.name)
    customer.phone = data.get('phone', customer.phone)
    customer.notification_channels = ','.join(data.get('notificationChannels', customer.notification_channels.split(',') if customer.notification_channels else []))
    customer.license_expiry = data.get('licenseExpiry', customer.license_expiry)
    customer.status = data.get('status', customer.status)
    customer.registration_date = data.get('registrationDate', customer.registration_date)
    customer.siren_ip_address = data.get('sirenIpAddress', customer.siren_ip_address) # Yeni eklendi

    if 'resellerId' in data and data['resellerId'] != customer.reseller_id:
        new_reseller = Reseller.query.get(data['resellerId'])
        if not new_reseller:
            return jsonify({"error": "Yeni belirtilen bayi bulunamadı"}), 404
        
        if new_reseller.remaining_licenses < customer.camera_count:
            return jsonify({"error": f"Yeni bayi '{new_reseller.name}' için yeterli lisans yok. Müşterinin {customer.camera_count} kamerası var, bayinin {new_reseller.remaining_licenses} lisansı kalmış."}), 400
        customer.reseller_id = data['resellerId']

    try:
        db.session.commit()
        return jsonify(customer.to_json()), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": "Müşteri güncellenirken bir hata oluştu.", "details": str(e)}), 500

@app.route('/customers/<int:id>', methods=['DELETE'])
def delete_customer(id):
    customer = Customer.query.get(id)
    if not customer:
        return jsonify({"error": "Silinecek müşteri bulunamadı"}), 404

    try:
        db.session.delete(customer)
        db.session.commit()
        return jsonify({"message": "Müşteri başarıyla silindi"}), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": "Müşteri silinirken bir hata oluştu.", "details": str(e)}), 500


# --- CAMERA ENDPOINTS (Önceki haliyle korunuyor) ---

@app.route('/customers/<int:customer_id>/cameras', methods=['GET'])
def get_customer_cameras(customer_id):
    customer = Customer.query.get(customer_id)
    if not customer:
        return jsonify({"error": "Müşteri bulunamadı"}), 404
    
    try:
        cameras = customer.cameras_rel.all()
        return jsonify([camera.to_json() for camera in cameras]), 200
    except Exception as e:
        return jsonify({"error": "Kameralar listelenirken bir hata oluştu.", "details": str(e)}), 500

@app.route('/customers/<int:customer_id>/cameras', methods=['POST'])
def add_camera_to_customer(customer_id):
    data = request.get_json()
    if not data:
        return jsonify({"error": "Veri gönderilmedi"}), 400

    required_fields = ['rtspUrl']
    for field in required_fields:
        if field not in data or not data[field]:
            return jsonify({"error": f"Eksik alan: {field}"}), 400

    customer = Customer.query.get(customer_id)
    if not customer:
        return jsonify({"error": "Müşteri bulunamadı"}), 404

    reseller = customer.reseller
    if not reseller:
        return jsonify({"error": "Müşterinin bağlı olduğu bayi bulunamadı."}), 500

    if reseller.remaining_licenses < 1:
        return jsonify({"error": f"Bayi '{reseller.name}' için yeterli lisans yok. Kalan lisans: {reseller.remaining_licenses}"}), 400
    
    if Camera.query.filter_by(rtsp_url=data['rtspUrl']).first():
        return jsonify({"error": "Bu RTSP URL adresi zaten bir kamera tarafından kullanılıyor."}), 409

    try:
        new_camera = Camera(
            name=data.get('name', f"Kamera {customer.camera_count + 1}"),
            rtsp_url=data['rtspUrl'],
            # siren_ip_address artık burada yok
            customer_id=customer_id
        )
        db.session.add(new_camera)
        db.session.commit()
        return jsonify(new_camera.to_json()), 201
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": "Kamera eklenirken bir hata oluştu.", "details": str(e)}), 500

@app.route('/cameras/<int:id>', methods=['GET'])
def get_camera(id):
    try:
        camera = Camera.query.get(id)
        if camera:
            return jsonify(camera.to_json()), 200
        else:
            return jsonify({"error": "Kamera bulunamadı"}), 404
    except Exception as e:
        return jsonify({"error": "Kamera getirilirken bir hata oluştu.", "details": str(e)}), 500

@app.route('/cameras/<int:id>', methods=['PUT'])
def update_camera(id):
    data = request.get_json()
    if not data:
        return jsonify({"error": "Veri gönderilmedi"}), 400

    camera = Camera.query.get(id)
    if not camera:
        return jsonify({"error": "Güncellenecek kamera bulunamadı"}), 404
    
    if 'rtspUrl' in data and data['rtspUrl'] != camera.rtsp_url:
        if Camera.query.filter(Camera.id != id, Camera.rtsp_url == data['rtspUrl']).first():
            return jsonify({"error": "Bu RTSP URL adresi başka bir kameraya ait."}), 409
        camera.rtsp_url = data['rtspUrl']

    camera.name = data.get('name', camera.name)
    camera.roi_coordinates = data.get('roiCoordinates', camera.roi_coordinates) # JSON string
    
    # YENİ: Zaman aralığı güncellemesi
    # Frontend'den "HH:MM-HH:MM" formatında veya boş string gelmesini bekliyoruz.
    # Eğer null gelirse veya alan hiç yoksa, mevcut değeri koru.
    # Eğer boş string gelirse, DB'de null veya boş string olarak sakla (modeline göre)
    if 'analysisTimeRange' in data: # Eğer frontend 'analysisTimeRange' anahtarını gönderdiyse
        time_range_value = data.get('analysisTimeRange')
        if time_range_value == "": # Eğer boş string geldiyse, zaman aralığını temizle
            camera.analysis_time_range = None # Veya modelinize göre ""
        elif time_range_value: # Eğer dolu bir string geldiyse (format kontrolü eklenebilir)
            # Basit format kontrolü: "HH:MM-HH:MM"
            import re
            if re.match(r"^\d{2}:\d{2}-\d{2}:\d{2}$", time_range_value):
                camera.analysis_time_range = time_range_value
            else:
                # Format hatalıysa, isteği reddetmek veya loglamak daha iyi olabilir.
                # Şimdilik, format hatalıysa değişikliği yapmıyoruz veya bir hata döndürebiliriz.
                # return jsonify({"error": "Zaman aralığı formatı hatalı (HH:MM-HH:MM bekleniyor)."}), 400
                print(f"Uyarı: Kamera {id} için hatalı zaman aralığı formatı: {time_range_value}. Değişiklik uygulanmadı.")
                # Mevcut değeri korumak için bir şey yapmaya gerek yok, aşağıdaki commit'e kadar eski değer kalır.
                # Eğer hatalı formatta güncelleme yapılmamasını istiyorsan, bu bloğu boş bırakabilirsin
                # ya da hata döndürebilirsin.
                pass # Hatalı formatta bir şey yapma, mevcut değeri koru.
        # Eğer `time_range_value` None ise (data.get('analysisTimeRange') None döndürürse),
        # bu bloklara girmez ve camera.analysis_time_range değişmez.
        # Bu, frontend'in sadece değişen alanları göndermesi durumunda işe yarar.
        # Eğer frontend her zaman tüm alanları gönderiyorsa ve bir alan null ise,
        # DB'de de null olarak ayarlanır (eğer `camera.analysis_time_range = data.get(...)` direkt kullanılırsa).

    try:
        db.session.commit()
        return jsonify(camera.to_json()), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": "Kamera güncellenirken bir hata oluştu.", "details": str(e)}), 500

@app.route('/cameras/<int:id>', methods=['DELETE'])
def delete_camera(id):
    camera = Camera.query.get(id)
    if not camera:
        return jsonify({"error": "Silinecek kamera bulunamadı"}), 404

    try:
        db.session.delete(camera)
        db.session.commit()
        return jsonify({"message": "Kamera başarıyla silindi"}), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": "Kamera silinirken bir hata oluştu.", "details": str(e)}), 500


if __name__ == '__main__':
    app.run(debug=True, port=5000)
# --- END OF FILE app.py ---