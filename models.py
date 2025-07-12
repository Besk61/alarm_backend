# --- START OF FILE models.py (GÜNCELLENMİŞ VE SON HALİ) ---

from flask_sqlalchemy import SQLAlchemy
from datetime import datetime, timezone # Bu importlar modern kullanım için doğru
import json
import pytz # Zaman dilimi işlemleri için gerekli

# Zaman dilimi nesnelerini tanımlıyoruz. Bu, kodun okunabilirliğini artırır.
TR_TIMEZONE = pytz.timezone('Europe/Istanbul')
UTC_TIMEZONE = pytz.utc

db = SQLAlchemy()

AVAILABLE_MODULES = {
    "person_detection": "İnsan Tespiti",
    "fire_detection": "Yangın Tespiti",
}
MODULE_KEYS = list(AVAILABLE_MODULES.keys())

class Alarm(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    customer_id = db.Column(db.Integer, db.ForeignKey('customer.id'), nullable=False)
    camera_id = db.Column(db.Integer, db.ForeignKey('camera.id'), nullable=False)
    
    alarm_type = db.Column(db.String(100), nullable=False)
    category = db.Column(db.String(50), default='Critical')
    event_details = db.Column(db.String(255))
    module_name = db.Column(db.String(100))
    
    # Varsayılan zaman damgası "aware" UTC olarak ayarlandı. Bu en iyi ve modern pratiktir.
    timestamp = db.Column(db.DateTime, default=lambda: datetime.now(UTC_TIMEZONE), nullable=False)
    image_url = db.Column(db.String(255))
    
    customer = db.relationship('Customer', backref=db.backref('alarms', lazy='dynamic'))
    camera = db.relationship('Camera', backref=db.backref('alarms', lazy='dynamic', cascade="all, delete-orphan"))

    def to_json(self):
        # --- DEĞİŞİKLİK: Daha sağlam zaman dilimi çevirimi ---
        # Veritabanından gelen 'naive' zamanı, önce 'aware' UTC zamanına çevirip
        # sonra yerel saate dönüştürmek en doğru yöntemdir.
        if not self.timestamp:
            local_timestamp_str = None
        else:
            # 1. Veritabanından gelen naive datetime'ı UTC olarak "aware" yap.
            utc_timestamp = UTC_TIMEZONE.localize(self.timestamp)
            # 2. Türkiye saatine çevir.
            tr_timestamp = utc_timestamp.astimezone(TR_TIMEZONE)
            # 3. Frontend için formatla.
            local_timestamp_str = tr_timestamp.strftime('%d.%m.%Y %H:%M:%S')

        return {
            'id': self.id,
            'customerId': self.customer_id,
            'customerName': self.customer.name if self.customer else None,
            'cameraId': self.camera_id,
            'cameraName': self.camera.name if self.camera else None,
            'cameraIp': self.camera.rtsp_url if self.camera else None,
            'alarmType': self.alarm_type,
            'category': self.category,
            'event': self.event_details,
            'module': self.module_name,
            'datetime': local_timestamp_str,
            'imageUrl': self.image_url
        }

class Reseller(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False, unique=True)
    email = db.Column(db.String(120), nullable=False, unique=True)
    password_hash = db.Column(db.String(128), nullable=False)
    phone = db.Column(db.String(20))
    status = db.Column(db.String(20), default='Active')
    # licenses = db.Column(db.Integer, nullable=False, default=0)
    # Bu string olduğu için zaman dilimi etkilemez.
    join_date = db.Column(db.String(20)) 

    _module_licenses_json = db.Column(db.Text, nullable=False, 
                                      default=json.dumps({key: 0 for key in MODULE_KEYS}))

    customers_rel = db.relationship('Customer', backref='reseller', lazy='dynamic', cascade="all, delete-orphan")
    approval_requests_rel = db.relationship('ApprovalRequest', backref='reseller', lazy='dynamic', cascade="all, delete-orphan")

    @property
    def customer_count(self):
        return self.customers_rel.count()

    @property
    def camera_count(self): # Bu, toplam kamera sayısını verir, modülden bağımsız.
        total_cameras = 0
        for customer in self.customers_rel:
            total_cameras += len(customer.cameras_rel)
        return total_cameras

    @property
    def module_licenses(self):
        """Modül lisanslarını {'module_key': count} formatında bir sözlük olarak döndürür."""
        data = json.loads(self._module_licenses_json)
        # Veritabanında olmayan yeni modüller için varsayılan 0 ata
        for key in MODULE_KEYS:
            if key not in data:
                data[key] = 0
        return data

    @module_licenses.setter
    def module_licenses(self, value):
        """Modül lisanslarını ayarlar. Girdi olarak {'module_key': count} bekler."""
        if not isinstance(value, dict):
            raise ValueError("module_licenses bir sözlük olmalıdır.")
        
        sanitized_value = {key: 0 for key in MODULE_KEYS} # Temiz bir başlangıç
        for key, count in value.items():
            if key in MODULE_KEYS:
                try:
                    sanitized_value[key] = max(0, int(count)) # Negatif olamaz
                except (ValueError, TypeError):
                    sanitized_value[key] = 0 # Geçersizse 0
        self._module_licenses_json = json.dumps(sanitized_value)

    @property
    def used_module_licenses(self):
        """Her bir modül için kullanılan lisans sayısını hesaplar."""
        used = {key: 0 for key in MODULE_KEYS}
        for customer in self.customers_rel:
            for camera in customer.cameras_rel:
                if camera.assigned_module and camera.assigned_module in used:
                    used[camera.assigned_module] += 1
        return used

    @property
    def remaining_module_licenses(self):
        """Her bir modül için kalan lisans sayısını hesaplar."""
        total_lic = self.module_licenses
        used_lic = self.used_module_licenses
        remaining = {
            key: total_lic.get(key, 0) - used_lic.get(key, 0)
            for key in MODULE_KEYS
        }
        return remaining
    
    # remaining_licenses property'si artık doğrudan kullanılmayacak. Kaldırılabilir.

    def to_json(self):
        return {
            'id': self.id,
            'name': self.name,
            'email': self.email,
            'phone': self.phone,
            'customers': self.customer_count,
            'cameras': self.camera_count, # Toplam kamera sayısı
            'status': self.status,
            # 'licenses': self.licenses, # ESKİ
            'moduleLicenses': self.module_licenses, # Toplam atanan modül lisansları
            'usedModuleLicenses': self.used_module_licenses, # Kullanılan modül lisansları
            'remainingModuleLicenses': self.remaining_module_licenses, # Kalan modül lisansları
            'joinDate': self.join_date
        }

class Customer(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(128), nullable=True)
    phone = db.Column(db.String(20))
    national_id = db.Column(db.String(11), unique=True, nullable=False)
    # String olduğu için sorun yok, default'u modern kullanıma uygun hale getirdik.
    registration_date = db.Column(db.String(20), default=lambda: datetime.now(UTC_TIMEZONE).strftime('%Y-%m-%d'))
    notification_channels = db.Column(db.String(100))
    license_expiry = db.Column(db.String(20), nullable=False)
    status = db.Column(db.String(20), default='Active')
    
    
    siren_ip_address = db.Column(db.String(50), nullable=True)
    additional_id = db.Column(db.String(100), nullable=True)
    address = db.Column(db.Text, nullable=True) 
    alarm_center_ip = db.Column(db.String(50), nullable=True)
    cooldown = db.Column(db.Integer, nullable=False, default=60)
    telegram_chat_id = db.Column(db.String(100), nullable=True)
    
    # "Ben Buradayım" modunun bitiş zamanı. Veritabanında UTC olarak saklanır.
    is_present_until = db.Column(db.DateTime, nullable=True, default=None)
    
    onesignal_player_ids = db.Column(db.Text, nullable=True)

    reseller_id = db.Column(db.Integer, db.ForeignKey('reseller.id'), nullable=False)
    

    cameras_rel = db.relationship('Camera', backref='customer', lazy='subquery', cascade="all, delete-orphan")

    @property
    def camera_count(self):
        return len(self.cameras_rel)

    @property
    def is_present(self):
        """Hesaplanan property. is_present_until'in geçerli olup olmadığını UTC'ye göre kontrol eder."""
        # Eğer veritabanında tarih yoksa veya geçmişteyse, mod aktif değildir.
        if not self.is_present_until:
            return False
        
        # Veritabanından gelen naive zamanı "aware" yap ve karşılaştır.
        # Bu kısımda değişiklik yok, burası zaten doğruydu.
        db_time_aware = UTC_TIMEZONE.localize(self.is_present_until)
        return db_time_aware > datetime.now(UTC_TIMEZONE)


    def to_json(self):
        # --- İŞTE DÜZELTME BURADA ---
        present_until_iso = None
        # `self.is_present_until` None değilse localize işlemini yap.
        player_ids = []
        if self.onesignal_player_ids:
            try:
                player_ids = json.loads(self.onesignal_player_ids)
            except json.JSONDecodeError:
                player_ids = []

        if self.is_present_until:
            aware_utc_time = UTC_TIMEZONE.localize(self.is_present_until)
            present_until_iso = aware_utc_time.isoformat()
            
        return {
            'id': self.id,
            'name': self.name,
            'email': self.email,
            'phone': self.phone,
            'nationalId': self.national_id,
            'registrationDate': self.registration_date,
            'resellerId': self.reseller_id,
            'resellerName': self.reseller.name if self.reseller else None,
            'cameraCount': self.camera_count,
            'notificationChannels': self.notification_channels.split(',') if self.notification_channels else [],
            'telegram_chat_id': self.telegram_chat_id,
            'licenseExpiry': self.license_expiry,
            'isActive': self.status == 'Active',
            'sirenIpAddress': self.siren_ip_address, 
            'additionalId': self.additional_id,
            'address': self.address, 
            'alarmCenterIp': self.alarm_center_ip,
            'cooldown': self.cooldown,
            # `self.is_present` çağrısı artık `None` durumu için güvende.
            'is_present': self.is_present, 
            # `present_until_iso` da `None` durumu için güvende.
            'is_present_until': present_until_iso,
            'onesignal_player_ids': player_ids # YENİ
        }

class Camera(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=True)
    rtsp_url = db.Column(db.String(255), nullable=False, unique=True)
    roi_coordinates = db.Column(db.Text, nullable=True)
    analysis_time_range = db.Column(db.String(50), nullable=True)
    assigned_module = db.Column(db.String(50), nullable=True) # YENİ: örn; "fire_detection"
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    customer_id = db.Column(db.Integer, db.ForeignKey('customer.id'), nullable=False)
    confidence_threshold = db.Column(db.Float, default=0.25, nullable=False)

    def to_json(self):
        module_name_display = "Bilinmeyen Modül"
        if self.assigned_module and self.assigned_module in AVAILABLE_MODULES:
            module_name_display = AVAILABLE_MODULES[self.assigned_module]
        
        return {
            'id': self.id,
            'name': self.name,
            'rtspUrl': self.rtsp_url,
            'customerId': self.customer_id,
            'roiCoordinates': self.roi_coordinates,
            'analysisTimeRange': self.analysis_time_range,
            'assignedModule': self.assigned_module, # Modülün anahtar adı
            'assignedModuleDisplayName': module_name_display, # Modülün gösterim adı
            'is_active': self.is_active,
            "confidence_threshold": self.confidence_threshold
        }

class ApprovalRequest(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    reseller_id = db.Column(db.Integer, db.ForeignKey('reseller.id'), nullable=False)
    
    customer_name = db.Column(db.String(100), nullable=False)
    customer_email = db.Column(db.String(120), nullable=False)
    customer_phone = db.Column(db.String(20))
    customer_national_id = db.Column(db.String(11), nullable=False)
    customer_license_expiry = db.Column(db.String(20), nullable=False)

    customer_telegram_chat_id = db.Column(db.String(100), nullable=True)
    
    customer_siren_ip_address = db.Column(db.String(50), nullable=True)
    customer_additional_id = db.Column(db.String(100), nullable=True)
    customer_address = db.Column(db.Text, nullable=True) 
    customer_alarm_center_ip = db.Column(db.String(50), nullable=True) 

    _rtsp_urls_json = db.Column(db.Text, nullable=False) # Yapısı değişti
    
    status = db.Column(db.String(20), default='Pending')
    # String olduğu için sorun yok, default'u modern kullanıma uygun hale getirdik.
    request_date = db.Column(db.String(20), default=lambda: datetime.now(UTC_TIMEZONE).strftime('%Y-%m-%d'))
    approval_date = db.Column(db.String(20))

    email_verified = db.Column(db.Boolean, default=False, nullable=False)
    email_verification_code = db.Column(db.String(10), nullable=True) # Kodu hash'leyerek saklamak daha güvenli olur
    email_verification_code_sent_at = db.Column(db.DateTime, nullable=True)
    email_verification_code_expires_at = db.Column(db.DateTime, nullable=True)

    @property
    def rtsp_streams_info(self):
        """RTSP stream bilgilerini (url ve module) liste olarak döndürür."""
        try:
            streams = json.loads(self._rtsp_urls_json)
            if not isinstance(streams, list): return []
            # Her stream için 'url' ve 'module' anahtarlarının ve geçerli modülün varlığını kontrol et
            valid_streams = []
            for s in streams:
                if isinstance(s, dict) and 'url' in s and 'module' in s and s['module'] in MODULE_KEYS:
                    valid_streams.append(s)
            return valid_streams
        except (json.JSONDecodeError, TypeError):
            return []

    @rtsp_streams_info.setter
    def rtsp_streams_info(self, streams_data):
        """RTSP stream bilgilerini ayarlar. [{"url": "...", "module": "..."}, ...] formatında liste bekler."""
        if not isinstance(streams_data, list):
            raise ValueError("rtsp_streams_info bir liste olmalıdır.")
        
        valid_data = []
        for item in streams_data:
            if isinstance(item, dict) and 'url' in item and 'module' in item and \
               isinstance(item['url'], str) and item['url'] and \
               isinstance(item['module'], str) and item['module'] in MODULE_KEYS:
                valid_data.append({"url": item['url'], "module": item['module']})
            else:
                # Hatalı formatta bir stream bilgisi gelirse loglayabilir veya hata fırlatabiliriz.
                print(f"Uyarı: Kurulum talebinde geçersiz stream bilgisi atlandı: {item}")
        self._rtsp_urls_json = json.dumps(valid_data)

    @property
    def requested_modules_count(self):
        """Talep edilen her modül için kamera sayısını bir sözlükte döndürür."""
        counts = {key: 0 for key in MODULE_KEYS}
        for stream_info in self.rtsp_streams_info:
            if stream_info['module'] in counts:
                counts[stream_info['module']] += 1
        return counts

    def to_json(self):
        return {
            'id': self.id,
            'resellerId': self.reseller_id,
            'resellerName': self.reseller.name if self.reseller else None,
            'customerName': self.customer_name,
            'customerEmail': self.customer_email,
            'customerPhone': self.customer_phone,
            'customerNationalId': self.customer_national_id,
            'customerLicenseExpiry': self.customer_license_expiry,
            'customerSirenIpAddress': self.customer_siren_ip_address,
            'customerAdditionalId': self.customer_additional_id,
            'customerAddress': self.customer_address,
            'customerAlarmCenterIp': self.customer_alarm_center_ip,
            'rtspStreamsInfo': self.rtsp_streams_info,
            'requestedModulesCount': self.requested_modules_count,
            'status': self.status,
            'requestDate': self.request_date,
            'approvalDate': self.approval_date,
            'customerTelegramChatId': self.customer_telegram_chat_id,
            # --- YENİ ALAN ---
            'email_verified': self.email_verified,
        }