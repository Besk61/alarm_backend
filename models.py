# --- START OF FILE models.py ---
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime
import json # json modülünü içe aktar

db = SQLAlchemy()
password_hash = db.Column(db.String(128), nullable=True) # Müşteri girişi için, başlangıçta null olabilir

class Alarm(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    customer_id = db.Column(db.Integer, db.ForeignKey('customer.id'), nullable=False)
    camera_id = db.Column(db.Integer, db.ForeignKey('camera.id'), nullable=False)
    
    alarm_type = db.Column(db.String(100), nullable=False) # Örn: 'Alan İhlali', 'İnsan Tespiti'
    category = db.Column(db.String(50), default='Critical') # Frontend'deki gibi
    event_details = db.Column(db.String(255)) # Frontend'deki 'event' (worker1 vs.)
    module_name = db.Column(db.String(100)) # Frontend'deki 'module' (Alan_Kontrol vs.)
    
    timestamp = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    image_url = db.Column(db.String(255)) # Alarm anı görüntüsü URL'i
    # video_clip_url = db.Column(db.String(255)) # Opsiyonel
    
    # İlişkiler (opsiyonel ama faydalı)
    customer = db.relationship('Customer', backref=db.backref('alarms', lazy='dynamic'))
    camera = db.relationship('Camera', backref=db.backref('alarms', lazy='dynamic'))

    def to_json(self):
        return {
            'id': self.id,
            'customerId': self.customer_id,
            'customerName': self.customer.name if self.customer else None,
            'cameraId': self.camera_id,
            'cameraName': self.camera.name if self.camera else None,
            'cameraIp': self.camera.rtsp_url if self.camera else None, # Frontend bunu 'cameraIp' olarak bekliyor
            'alarmType': self.alarm_type,
            'category': self.category,
            'event': self.event_details, # Frontend bunu 'event' olarak bekliyor
            'module': self.module_name, # Frontend bunu 'module' olarak bekliyor
            'datetime': self.timestamp.strftime('%d.%m.%Y %H:%M:%S') if self.timestamp else None,
            'imageUrl': self.image_url
        }

class Reseller(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False, unique=True)
    email = db.Column(db.String(120), nullable=False, unique=True)
    password_hash = db.Column(db.String(128), nullable=False)
    phone = db.Column(db.String(20))
    status = db.Column(db.String(20), default='Active') # 'Active' veya 'Inactive'
    licenses = db.Column(db.Integer, nullable=False, default=0) # Toplam lisans hakkı
    join_date = db.Column(db.String(20))

    customers_rel = db.relationship('Customer', backref='reseller', lazy='dynamic', cascade="all, delete-orphan")
    # Yeni: Bayi için onay talepleri
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
    password_hash = db.Column(db.String(128), nullable=True) # BU SATIRIN OLDUĞUNDAN EMİN OLUN
    phone = db.Column(db.String(20))
    national_id = db.Column(db.String(11), unique=True, nullable=False) # TC No zorunlu
    registration_date = db.Column(db.String(20), default=lambda: datetime.now().strftime('%Y-%m-%d')) # Müşterinin sisteme kayıt tarihi
    notification_channels = db.Column(db.String(100)) # Örn: "Telegram,Mobile App" (virgülle ayrılmış)
    license_expiry = db.Column(db.String(20), nullable=False) # Lisans bitiş tarihi (zorunlu)
    status = db.Column(db.String(20), default='Active') # 'Active' veya 'Inactive'
    
    # YENİ EKLENEN ALANLAR
    siren_ip_address = db.Column(db.String(50), nullable=True) # Siren IP adresi müşteri bazına taşındı
    additional_id = db.Column(db.String(100), nullable=True) # Ek kimlik alanı

    reseller_id = db.Column(db.Integer, db.ForeignKey('reseller.id'), nullable=False)

    cameras_rel = db.relationship('Camera', backref='customer', lazy='dynamic', cascade="all, delete-orphan")

    @property
    def camera_count(self):
        return self.cameras_rel.count()

    def to_json(self):
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
            'sirenIpAddress': self.siren_ip_address, # JSON çıktısına ekle
            'additionalId': self.additional_id # JSON çıktısına ekle
        }

class Camera(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=True) # Kameraya bir isim verilebilir (örn: "Giriş Kapısı")
    rtsp_url = db.Column(db.String(255), nullable=False, unique=True) # RTSP URL'si benzersiz olmalı
    roi_coordinates = db.Column(db.Text, nullable=True) # Alan tanımı için JSON string [[x,y], [x,y]]
    analysis_time_range = db.Column(db.String(50), nullable=True) # "HH:MM-HH:MM" formatında
    # siren_ip_address artık burada değil, Customer modeline taşındı.

    customer_id = db.Column(db.Integer, db.ForeignKey('customer.id'), nullable=False)

    def to_json(self):
        return {
            'id': self.id,
            'name': self.name,
            'rtspUrl': self.rtsp_url,
            # 'sirenIpAddress': self.siren_ip_address, # Artık JSON çıktısında yok
            'customerId': self.customer_id,
            'roiCoordinates': self.roi_coordinates,
            'analysisTimeRange': self.analysis_time_range
        }

class ApprovalRequest(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    reseller_id = db.Column(db.Integer, db.ForeignKey('reseller.id'), nullable=False)
    
    # Müşteri Adayı Bilgileri
    customer_name = db.Column(db.String(100), nullable=False)
    customer_email = db.Column(db.String(120), nullable=False)
    customer_phone = db.Column(db.String(20))
    customer_national_id = db.Column(db.String(11), nullable=False)
    customer_license_expiry = db.Column(db.String(20), nullable=False) # Müşteri lisans bitiş tarihi
    
    # YENİ EKLENEN ALANLAR (Talep aşamasında da bu bilgilerin tutulması için)
    customer_siren_ip_address = db.Column(db.String(50), nullable=True)
    customer_additional_id = db.Column(db.String(100), nullable=True)

    # RTSP URL'lerini JSON string olarak saklayalım
    _rtsp_urls_json = db.Column(db.Text, nullable=False) 
    
    status = db.Column(db.String(20), default='Pending') # 'Pending', 'Approved', 'Rejected'
    request_date = db.Column(db.String(20), default=lambda: datetime.now().strftime('%Y-%m-%d'))
    approval_date = db.Column(db.String(20)) # Onay/Reddetme tarihi, null olabilir

    # rtsp_urls özelliğini tanımla
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
            'customerSirenIpAddress': self.customer_siren_ip_address, # JSON çıktısına ekle
            'customerAdditionalId': self.customer_additional_id, # JSON çıktısına ekle
            'rtspUrls': self.rtsp_urls,
            'status': self.status,
            'requestDate': self.request_date,
            'approvalDate': self.approval_date
        }
# --- END OF FILE models.py ---