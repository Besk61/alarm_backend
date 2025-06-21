# --- START OF FILE models.py (GÜNCELLENMİŞ VE SON HALİ) ---

from flask_sqlalchemy import SQLAlchemy
from datetime import datetime, timezone # Bu importlar modern kullanım için doğru
import json
import pytz # Zaman dilimi işlemleri için gerekli

# Zaman dilimi nesnelerini tanımlıyoruz. Bu, kodun okunabilirliğini artırır.
TR_TIMEZONE = pytz.timezone('Europe/Istanbul')
UTC_TIMEZONE = pytz.utc

db = SQLAlchemy()

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
    camera = db.relationship('Camera', backref=db.backref('alarms', lazy='dynamic'))

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
    licenses = db.Column(db.Integer, nullable=False, default=0)
    # Bu string olduğu için zaman dilimi etkilemez.
    join_date = db.Column(db.String(20)) 

    customers_rel = db.relationship('Customer', backref='reseller', lazy='dynamic', cascade="all, delete-orphan")
    approval_requests_rel = db.relationship('ApprovalRequest', backref='reseller', lazy='dynamic', cascade="all, delete-orphan")

    @property
    def customer_count(self):
        return self.customers_rel.count()

    @property
    def camera_count(self):
        total_cameras = 0
        for customer in self.customers_rel:
            total_cameras += customer.cameras_rel.count()
        return total_cameras
    
    @property
    def remaining_licenses(self):
        return self.licenses - self.camera_count

    def to_json(self):
        return {
            'id': self.id,
            'name': self.name,
            'email': self.email,
            'phone': self.phone,
            'customers': self.customer_count,
            'cameras': self.camera_count,
            'status': self.status,
            'licenses': self.licenses,
            'remainingLicenses': self.remaining_licenses,
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
    
    # "Ben Buradayım" modunun bitiş zamanı. Veritabanında UTC olarak saklanır.
    is_present_until = db.Column(db.DateTime, nullable=True, default=None)

    reseller_id = db.Column(db.Integer, db.ForeignKey('reseller.id'), nullable=False)

    cameras_rel = db.relationship('Camera', backref='customer', lazy='dynamic', cascade="all, delete-orphan")

    @property
    def camera_count(self):
        return self.cameras_rel.count()

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
            'is_present_until': present_until_iso
        }

class Camera(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=True)
    rtsp_url = db.Column(db.String(255), nullable=False, unique=True)
    roi_coordinates = db.Column(db.Text, nullable=True)
    analysis_time_range = db.Column(db.String(50), nullable=True)

    customer_id = db.Column(db.Integer, db.ForeignKey('customer.id'), nullable=False)

    def to_json(self):
        return {
            'id': self.id,
            'name': self.name,
            'rtspUrl': self.rtsp_url,
            'customerId': self.customer_id,
            'roiCoordinates': self.roi_coordinates,
            'analysisTimeRange': self.analysis_time_range
        }

class ApprovalRequest(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    reseller_id = db.Column(db.Integer, db.ForeignKey('reseller.id'), nullable=False)
    
    customer_name = db.Column(db.String(100), nullable=False)
    customer_email = db.Column(db.String(120), nullable=False)
    customer_phone = db.Column(db.String(20))
    customer_national_id = db.Column(db.String(11), nullable=False)
    customer_license_expiry = db.Column(db.String(20), nullable=False)
    
    customer_siren_ip_address = db.Column(db.String(50), nullable=True)
    customer_additional_id = db.Column(db.String(100), nullable=True)
    customer_address = db.Column(db.Text, nullable=True) 
    customer_alarm_center_ip = db.Column(db.String(50), nullable=True) 

    _rtsp_urls_json = db.Column(db.Text, nullable=False) 
    
    status = db.Column(db.String(20), default='Pending')
    # String olduğu için sorun yok, default'u modern kullanıma uygun hale getirdik.
    request_date = db.Column(db.String(20), default=lambda: datetime.now(UTC_TIMEZONE).strftime('%Y-%m-%d'))
    approval_date = db.Column(db.String(20))

    @property
    def rtsp_urls(self):
        return json.loads(self._rtsp_urls_json) if self._rtsp_urls_json else []

    @rtsp_urls.setter
    def rtsp_urls(self, urls):
        self._rtsp_urls_json = json.dumps(urls)

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
            'rtspUrls': self.rtsp_urls,
            'status': self.status,
            'requestDate': self.request_date,
            'approvalDate': self.approval_date
        }