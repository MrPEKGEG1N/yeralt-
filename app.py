from flask import Flask, render_template, request, redirect, url_for, session, jsonify, flash
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, timedelta
import random, os, sqlite3, secrets, smtplib
from email.mime.text import MIMEText

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'mafya-ultra-gizli-2024')
DB = os.environ.get('DB_PATH', 'mafya.db')

# Mail ayarları (Railway'de environment variable olarak set edilecek)
MAIL_HOST = os.environ.get('MAIL_HOST', 'smtp.gmail.com')
MAIL_PORT = int(os.environ.get('MAIL_PORT', '587'))
MAIL_USER = os.environ.get('MAIL_USER', '')
MAIL_PASS = os.environ.get('MAIL_PASS', '')

# ── VERİTABANI ────────────────────────────────────────────────────────────────

def get_db():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn

def init_db():
    with get_db() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS oyuncu (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kullanici_adi TEXT UNIQUE NOT NULL,
            email TEXT UNIQUE NOT NULL,
            sifre_hash TEXT NOT NULL,
            dogrulandi INTEGER DEFAULT 0,
            dogrulama_kodu TEXT,
            kod_gecerlilik TEXT,
            seviye INTEGER DEFAULT 1,
            tecrube INTEGER DEFAULT 0,
            para INTEGER DEFAULT 10000,
            saglik INTEGER DEFAULT 100,
            max_saglik INTEGER DEFAULT 100,
            guc INTEGER DEFAULT 500,
            sayginlik INTEGER DEFAULT 1500,
            icraat INTEGER DEFAULT 25,
            max_icraat INTEGER DEFAULT 25,
            son_icraat_yenileme TEXT,
            son_saldiri TEXT,
            grup_id INTEGER,
            grup_rol TEXT DEFAULT 'uye',
            kayit_tarihi TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS grup (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ad TEXT UNIQUE NOT NULL,
            aciklama TEXT,
            banka INTEGER DEFAULT 0,
            kurucu_id INTEGER,
            olusturma TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS saldiri_kaydi (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            saldiran_id INTEGER,
            hedef_id INTEGER,
            kazanan_id INTEGER,
            para_transfer INTEGER DEFAULT 0,
            xp_kazanilan INTEGER DEFAULT 0,
            sayginlik_kazanilan INTEGER DEFAULT 0,
            tarih TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS is_kaydi (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            oyuncu_id INTEGER,
            is_adi TEXT,
            kazanc INTEGER DEFAULT 0,
            tarih TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS liman (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            oyuncu_id INTEGER,
            liman_adi TEXT,
            ele_gecirme TEXT DEFAULT CURRENT_TIMESTAMP
        );
        """)

# ── YARDIMCI ──────────────────────────────────────────────────────────────────

    def giris_gerekli(f):
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'oyuncu_id' not in session:
            session['oyuncu_id'] = 1
        return f(*args, **kwargs)
    return decorated

def get_oyuncu(oid):
    with get_db() as c:
        row = c.execute("SELECT * FROM oyuncu WHERE id=?", (oid,)).fetchone()
        if not row: return None
        o = dict(row)
        if o['grup_id']:
            g = c.execute("SELECT * FROM grup WHERE id=?", (o['grup_id'],)).fetchone()
            o['grup'] = dict(g) if g else None
        else:
            o['grup'] = None
        o['seviye_atlama_xp'] = o['seviye'] * 150
        o['xp_yuzde'] = min(100, int(o['tecrube'] / o['seviye_atlama_xp'] * 100))
        o['hp_yuzde'] = int(o['saglik'] / o['max_saglik'] * 100)
        o['icraat_yuzde'] = int(o['icraat'] / o['max_icraat'] * 100)
        return o

def icraat_yenile(oyuncu_id):
    """Her 12 dakikada bir icraat hakkı verir"""
    with get_db() as c:
        row = c.execute("SELECT icraat, max_icraat, son_icraat_yenileme FROM oyuncu WHERE id=?", (oyuncu_id,)).fetchone()
        if not row: return
        icraat, max_icraat, son = row
        if icraat >= max_icraat: return
        simdi = datetime.utcnow()
        if son:
            try:
                son_dt = datetime.fromisoformat(son)
                dakika = (simdi - son_dt).total_seconds() / 60
                eklenecek = int(dakika / 12)
                if eklenecek > 0:
                    yeni = min(max_icraat, icraat + eklenecek)
                    c.execute("UPDATE oyuncu SET icraat=?, son_icraat_yenileme=? WHERE id=?",
                              (yeni, simdi.isoformat(), oyuncu_id))
            except: pass
        else:
            c.execute("UPDATE oyuncu SET son_icraat_yenileme=? WHERE id=?",
                      (simdi.isoformat(), oyuncu_id))

def xp_ekle(c, oyuncu_id, miktar):
    row = c.execute("SELECT seviye, tecrube, max_saglik FROM oyuncu WHERE id=?", (oyuncu_id,)).fetchone()
    seviye, tecrube, max_saglik = row
    tecrube += miktar
    atladimi = False
    while tecrube >= seviye * 150:
        tecrube -= seviye * 150
        seviye += 1
        max_saglik += 25
        atladimi = True
        c.execute("UPDATE oyuncu SET guc=guc+100 WHERE id=?", (oyuncu_id,))
    if atladimi:
        c.execute("UPDATE oyuncu SET seviye=?,tecrube=?,max_saglik=?,saglik=? WHERE id=?",
                  (seviye, tecrube, max_saglik, max_saglik, oyuncu_id))
    else:
        c.execute("UPDATE oyuncu SET seviye=?,tecrube=? WHERE id=?",
                  (seviye, tecrube, oyuncu_id))

def mail_gonder(alici, konu, icerik):
    if not MAIL_USER or not MAIL_PASS:
        return False
    try:
        msg = MIMEText(icerik, 'html', 'utf-8')
        msg['Subject'] = konu
        msg['From'] = f"Mafya Dünyası <{MAIL_USER}>"
        msg['To'] = alici
        with smtplib.SMTP(MAIL_HOST, MAIL_PORT) as s:
            s.starttls()
            s.login(MAIL_USER, MAIL_PASS)
            s.send_message(msg)
        return True
    except Exception as e:
        print(f"Mail hatası: {e}")
        return False

# ── KİMLİK DOĞRULAMA ──────────────────────────────────────────────────────────

@app.route('/')
def anasayfa():
    session['oyuncu_id'] = 1
    return redirect(url_for('panel'))

@app.route('/kayit', methods=['GET', 'POST'])
def kayit():
    if request.method == 'POST':
        ku = request.form['kullanici_adi'].strip()
        email = request.form['email'].strip().lower()
        sifre = request.form['sifre']
        if len(ku) < 3:
            flash('Kullanıcı adı en az 3 karakter olmalı.', 'hata')
            return redirect(url_for('kayit'))
        if len(sifre) < 6:
            flash('Şifre en az 6 karakter olmalı.', 'hata')
            return redirect(url_for('kayit'))
        with get_db() as c:
            if c.execute("SELECT id FROM oyuncu WHERE kullanici_adi=?", (ku,)).fetchone():
                flash('Bu kullanıcı adı alınmış.', 'hata')
                return redirect(url_for('kayit'))
            if c.execute("SELECT id FROM oyuncu WHERE email=?", (email,)).fetchone():
                flash('Bu e-posta zaten kayıtlı.', 'hata')
                return redirect(url_for('kayit'))
            kod = str(random.randint(100000, 999999))
            gecerlilik = (datetime.utcnow() + timedelta(minutes=30)).isoformat()
            c.execute("""INSERT INTO oyuncu (kullanici_adi,email,sifre_hash,dogrulama_kodu,kod_gecerlilik)
                         VALUES (?,?,?,?,?)""",
                      (ku, email, generate_password_hash(sifre), kod, gecerlilik))
            yeni_id = c.execute("SELECT id FROM oyuncu WHERE email=?", (email,)).fetchone()[0]

        # Mail gönder
        mail_icerik = f"""
        <div style="background:#1a1a1a;color:#fff;padding:30px;font-family:sans-serif;max-width:500px;margin:0 auto;border-radius:12px;">
            <h2 style="color:#c8a84b;">🔫 Mafya Dünyası</h2>
            <p>Merhaba <strong>{ku}</strong>,</p>
            <p>Hesabını doğrulamak için aşağıdaki kodu kullan:</p>
            <div style="background:#333;padding:20px;border-radius:8px;text-align:center;margin:20px 0;">
                <span style="font-size:36px;font-weight:bold;color:#c8a84b;letter-spacing:8px;">{kod}</span>
            </div>
            <p style="color:#888;font-size:13px;">Bu kod 30 dakika geçerlidir.</p>
        </div>
        """
        mail_gonderildi = mail_gonder(email, '🔫 Mafya Dünyası - E-posta Doğrulama', mail_icerik)

        session['dogrulama_id'] = yeni_id
        if mail_gonderildi:
            flash(f'{email} adresine doğrulama kodu gönderildi.', 'basari')
        else:
            # Mail ayarı yoksa kodu direkt göster (geliştirme modu)
            flash(f'Doğrulama kodun: {kod} (Mail ayarı yapılmadığı için burada gösteriliyor)', 'basari')
        return redirect(url_for('dogrula'))
    return render_template('kayit.html')

@app.route('/dogrula', methods=['GET', 'POST'])
def dogrula():
    oid = session.get('dogrulama_id')
    if not oid:
        return redirect(url_for('kayit'))
    if request.method == 'POST':
        kod = request.form['kod'].strip()
        with get_db() as c:
            row = c.execute("SELECT * FROM oyuncu WHERE id=?", (oid,)).fetchone()
            if not row:
                flash('Hata oluştu.', 'hata')
                return redirect(url_for('kayit'))
            if row['dogrulama_kodu'] != kod:
                flash('Kod hatalı!', 'hata')
                return redirect(url_for('dogrula'))
            try:
                gecerlilik = datetime.fromisoformat(row['kod_gecerlilik'])
                if datetime.utcnow() > gecerlilik:
                    flash('Kodun süresi dolmuş. Yeniden kayıt ol.', 'hata')
                    return redirect(url_for('kayit'))
            except: pass
            c.execute("UPDATE oyuncu SET dogrulandi=1, dogrulama_kodu=NULL WHERE id=?", (oid,))
        session.pop('dogrulama_id', None)
        session['oyuncu_id'] = oid
        flash('Hesabın doğrulandı! Mafya dünyasına hoş geldin.', 'basari')
        return redirect(url_for('panel'))
    return render_template('dogrula.html')

@app.route('/giris', methods=['GET', 'POST'])
def giris():
    if request.method == 'POST':
        ku = request.form['kullanici_adi'].strip()
        sifre = request.form['sifre']
        with get_db() as c:
            row = c.execute("SELECT * FROM oyuncu WHERE kullanici_adi=?", (ku,)).fetchone()
        if row and check_password_hash(row['sifre_hash'], sifre):
            if not row['dogrulandi']:
                session['dogrulama_id'] = row['id']
                flash('Hesabını henüz doğrulamadın.', 'hata')
                return redirect(url_for('dogrula'))
            session['oyuncu_id'] = row['id']
            icraat_yenile(row['id'])
            return redirect(url_for('panel'))
        flash('Kullanıcı adı veya şifre hatalı.', 'hata')
    return render_template('giris.html')

@app.route('/cikis')
def cikis():
    session.clear()
    return redirect(url_for('giris'))

# ── ANA PANEL ─────────────────────────────────────────────────────────────────

@app.route('/panel')
@giris_gerekli
def panel():
    icraat_yenile(session['oyuncu_id'])
    oyuncu = get_oyuncu(session['oyuncu_id'])
    with get_db() as c:
        kayitlar = c.execute("""
            SELECT sk.*, s.kullanici_adi as saldiran_ad, h.kullanici_adi as hedef_ad
            FROM saldiri_kaydi sk
            JOIN oyuncu s ON sk.saldiran_id=s.id
            JOIN oyuncu h ON sk.hedef_id=h.id
            WHERE sk.saldiran_id=? OR sk.hedef_id=?
            ORDER BY sk.tarih DESC LIMIT 5
        """, (oyuncu['id'], oyuncu['id'])).fetchall()
        son_isler = c.execute("""
            SELECT * FROM is_kaydi WHERE oyuncu_id=? ORDER BY tarih DESC LIMIT 5
        """, (oyuncu['id'],)).fetchall()
    return render_template('panel.html', oyuncu=oyuncu,
                           son_saldirilar=[dict(r) for r in kayitlar],
                           son_isler=[dict(r) for r in son_isler])

# ── İŞLER ─────────────────────────────────────────────────────────────────────

ISLER = {
    'mahalle': [
        {'id': 'market', 'ad': 'Köşedeki Marketi Haraca Bağla', 'kazanc': 800, 'sayginlik': 10, 'icraat': 1, 'min_guc': 300, 'gorsel': '🛒'},
        {'id': 'tamirhane', 'ad': 'Kaçak Otomobil Tamirhanesi İşlet', 'kazanc': 1500, 'sayginlik': 20, 'icraat': 1, 'min_guc': 600, 'gorsel': '🔧'},
        {'id': 'koruma', 'ad': 'Esnafa Güvence Sağla', 'kazanc': 2800, 'sayginlik': 35, 'icraat': 2, 'min_guc': 1200, 'gorsel': '🛡️'},
        {'id': 'kumarhane', 'ad': 'Gizli Yeraltı Zar Salonu Aç', 'kazanc': 4500, 'sayginlik': 60, 'icraat': 2, 'min_guc': 2500, 'gorsel': '🎲'},
    ],
    'semt': [
        {'id': 'gecekulubu', 'ad': 'Lüks Gece Kulübü Güvenliği', 'kazanc': 12000, 'sayginlik': 120, 'icraat': 3, 'min_guc': 6000, 'gorsel': '🏢'},
        {'id': 'kacakci', 'ad': 'Kaçakçılık Güzergahı Kur', 'kazanc': 20000, 'sayginlik': 200, 'icraat': 3, 'min_guc': 10000, 'gorsel': '🚛'},
        {'id': 'kumar', 'ad': 'Büyük Kumar Masası Kur', 'kazanc': 30000, 'sayginlik': 280, 'icraat': 4, 'min_guc': 15000, 'gorsel': '🎰'},
    ],
    'sehir': [
        {'id': 'ihale', 'ad': 'Büyük Lojistik İhalesini Al', 'kazanc': 45000, 'sayginlik': 400, 'icraat': 5, 'min_guc': 20000, 'gorsel': '🏗️'},
        {'id': 'banka', 'ad': 'Para Aklaması Organizasyonu', 'kazanc': 80000, 'sayginlik': 700, 'icraat': 6, 'min_guc': 35000, 'gorsel': '🏦'},
        {'id': 'senatör', 'ad': 'Siyasetçi Satın Al', 'kazanc': 150000, 'sayginlik': 1200, 'icraat': 8, 'min_guc': 60000, 'gorsel': '🎩'},
    ],
}

EKIPLER = [
    {'id': 'delikanlı', 'ad': 'Mahalle Delikanlısı', 'guc': 50, 'fiyat': 500, 'aciklama': 'Sokağın gözü kulağı, genç kardeşlerimiz.'},
    {'id': 'bodyguard', 'ad': 'BodyGuard', 'guc': 250, 'fiyat': 2000, 'aciklama': 'Giriş çıkışları tutan, düşmana korku salan duvar.'},
    {'id': 'koruma', 'ad': 'Profesyonel Koruma', 'guc': 1100, 'fiyat': 8000, 'aciklama': 'Takım elbiseli, özel eğitimli yakın korumalar.'},
    {'id': 'harekât', 'ad': 'Özel Harekat Emeklisi', 'guc': 4500, 'fiyat': 30000, 'aciklama': 'Dağlardan şehre inmiş tecrübe abidesi.'},
]

SILAHLAR = [
    {'id': 'baretta', 'ad': 'Baretta Tabanca', 'guc': 100, 'fiyat': 1200, 'aciklama': 'Hafif, taşınması kolay, yakın mesafe raconların vazgeçilmezi.'},
    {'id': 'pompali', 'ad': 'Taktik Pompalı Tüfek', 'guc': 450, 'fiyat': 4500, 'aciklama': 'Yakın mesafede barikatları dağıtan sokağın gürültüsü.'},
    {'id': 'keles', 'ad': 'Gaddar Keleş (AK-47)', 'guc': 1800, 'fiyat': 15000, 'aciklama': 'Çamura batsa da çalışır, her masayı devirir.'},
    {'id': 'golge', 'ad': 'Görünmez Gölge (Ağır Silah Kasası)', 'guc': 6000, 'fiyat': 45000, 'aciklama': 'Özel operasyonlar için susturuculu tam donanım.'},
    {'id': 'awm', 'ad': 'Masa Deviren (AWM)', 'guc': 7500, 'fiyat': 55000, 'aciklama': 'Kilometrelerce öteden hedefleri sıfır hatayla indirir.'},
]

LIMANLAR = [
    {'id': 'istanbul', 'ad': 'İstanbul Limanı', 'min_guc': 25000, 'kazanc': 50000, 'sayginlik': 500},
    {'id': 'izmir', 'ad': 'İzmir Limanı', 'min_guc': 40000, 'kazanc': 80000, 'sayginlik': 800},
    {'id': 'mersin', 'ad': 'Mersin Limanı', 'min_guc': 60000, 'kazanc': 120000, 'sayginlik': 1200},
]

@app.route('/isler/<kategori>')
@giris_gerekli
def isler(kategori):
    icraat_yenile(session['oyuncu_id'])
    oyuncu = get_oyuncu(session['oyuncu_id'])
    if kategori not in ISLER:
        return redirect(url_for('panel'))
    return render_template('isler.html', oyuncu=oyuncu, isler=ISLER[kategori], kategori=kategori)

@app.route('/is-yap', methods=['POST'])
@giris_gerekli
def is_yap():
    icraat_yenile(session['oyuncu_id'])
    oyuncu = get_oyuncu(session['oyuncu_id'])
    is_id = request.form.get('is_id')
    kategori = request.form.get('kategori')

    if kategori not in ISLER:
        return jsonify({'hata': 'Geçersiz iş!'})

    is_data = next((i for i in ISLER[kategori] if i['id'] == is_id), None)
    if not is_data:
        return jsonify({'hata': 'İş bulunamadı!'})

    if oyuncu['guc'] < is_data['min_guc']:
        return jsonify({'hata': f"Gücün yetersiz! Gereken: {is_data['min_guc']:,} Güç"})
    if oyuncu['icraat'] < is_data['icraat']:
        return jsonify({'hata': f"İcraat hakkın yok! Gereken: {is_data['icraat']}"})

    with get_db() as c:
        c.execute("UPDATE oyuncu SET para=para+?, sayginlik=sayginlik+?, icraat=icraat-? WHERE id=?",
                  (is_data['kazanc'], is_data['sayginlik'], is_data['icraat'], oyuncu['id']))
        xp_ekle(c, oyuncu['id'], is_data['sayginlik'] // 2)
        c.execute("INSERT INTO is_kaydi (oyuncu_id, is_adi, kazanc) VALUES (?,?,?)",
                  (oyuncu['id'], is_data['ad'], is_data['kazanc']))

    return jsonify({
        'basari': True,
        'mesaj': f"{is_data['gorsel']} {is_data['ad']} tamamlandı!",
        'kazanc': is_data['kazanc'],
        'sayginlik': is_data['sayginlik']
    })

@app.route('/guclen/<tur>')
@giris_gerekli
def guclen(tur):
    icraat_yenile(session['oyuncu_id'])
    oyuncu = get_oyuncu(session['oyuncu_id'])
    if tur == 'ekip':
        return render_template('guclen.html', oyuncu=oyuncu, liste=EKIPLER, tur='ekip', baslik='Ekip Kirala', ikon='👥')
    elif tur == 'silah':
        return render_template('guclen.html', oyuncu=oyuncu, liste=SILAHLAR, tur='silah', baslik='Silahlan', ikon='🔫')
    return redirect(url_for('panel'))

@app.route('/satin-al', methods=['POST'])
@giris_gerekli
def satin_al():
    oyuncu = get_oyuncu(session['oyuncu_id'])
    tur = request.form.get('tur')
    item_id = request.form.get('item_id')

    liste = EKIPLER if tur == 'ekip' else SILAHLAR
    item = next((i for i in liste if i['id'] == item_id), None)
    if not item:
        return jsonify({'hata': 'Bulunamadı!'})
    if oyuncu['para'] < item['fiyat']:
        return jsonify({'hata': 'Yeterli paran yok!'})

    with get_db() as c:
        c.execute("UPDATE oyuncu SET para=para-?, guc=guc+? WHERE id=?",
                  (item['fiyat'], item['guc'], oyuncu['id']))
    return jsonify({'basari': f"{item['ad']} temin edildi! +{item['guc']:,} Güç"})

# ── LIMAN SAVAŞLARI ───────────────────────────────────────────────────────────

@app.route('/limanlar')
@giris_gerekli
def limanlar():
    icraat_yenile(session['oyuncu_id'])
    oyuncu = get_oyuncu(session['oyuncu_id'])
    with get_db() as c:
        sahipler = {}
        for r in c.execute("SELECT liman_adi, oyuncu_id FROM liman").fetchall():
            sahipler[r['liman_adi']] = r['oyuncu_id']
        oyuncu_limanlar = [r['liman_adi'] for r in
                           c.execute("SELECT liman_adi FROM liman WHERE oyuncu_id=?", (oyuncu['id'],)).fetchall()]
    return render_template('limanlar.html', oyuncu=oyuncu, limanlar=LIMANLAR,
                           sahipler=sahipler, oyuncu_limanlar=oyuncu_limanlar)

@app.route('/liman-ele-gecir', methods=['POST'])
@giris_gerekli
def liman_ele_gecir():
    oyuncu = get_oyuncu(session['oyuncu_id'])
    liman_id = request.form.get('liman_id')
    liman = next((l for l in LIMANLAR if l['id'] == liman_id), None)
    if not liman:
        return jsonify({'hata': 'Liman bulunamadı!'})
    if oyuncu['guc'] < liman['min_guc']:
        return jsonify({'hata': f"Gücün yetersiz! Gereken: {liman['min_guc']:,} Güç"})

    with get_db() as c:
        mevcut = c.execute("SELECT oyuncu_id FROM liman WHERE liman_adi=?", (liman_id,)).fetchone()
        if mevcut and mevcut['oyuncu_id'] == oyuncu['id']:
            return jsonify({'hata': 'Bu liman zaten senin!'})
        if mevcut:
            c.execute("DELETE FROM liman WHERE liman_adi=?", (liman_id,))
        c.execute("INSERT INTO liman (oyuncu_id, liman_adi) VALUES (?,?)", (oyuncu['id'], liman_id))
        c.execute("UPDATE oyuncu SET para=para+?, sayginlik=sayginlik+? WHERE id=?",
                  (liman['kazanc'], liman['sayginlik'], oyuncu['id']))
        xp_ekle(c, oyuncu['id'], liman['sayginlik'] // 2)

    return jsonify({'basari': f"⚓ {liman['ad']} ele geçirildi! +{liman['kazanc']:,}₺ +{liman['sayginlik']} Saygınlık"})

# ── KONSEY ────────────────────────────────────────────────────────────────────

@app.route('/konsey', methods=['GET', 'POST'])
@giris_gerekli
def konsey():
    icraat_yenile(session['oyuncu_id'])
    oyuncu = get_oyuncu(session['oyuncu_id'])
    if request.method == 'POST':
        if oyuncu['para'] < 20000:
            return jsonify({'hata': 'Konseyi toplamak için 20.000₺ gerekli!'})
        with get_db() as c:
            c.execute("UPDATE oyuncu SET para=para-20000, guc=CAST(guc*1.3 AS INTEGER) WHERE id=?",
                      (oyuncu['id'],))
        return jsonify({'basari': '🚬 Masayı topladın! Tüm ekibin biati tazelendi. Gücün %30 arttı!'})
    return render_template('konsey.html', oyuncu=oyuncu)

# ── OYUNCULAR & SAVAŞ ─────────────────────────────────────────────────────────

@app.route('/oyuncular')
@giris_gerekli
def oyuncular():
    icraat_yenile(session['oyuncu_id'])
    oyuncu = get_oyuncu(session['oyuncu_id'])
    with get_db() as c:
        rows = c.execute("""
            SELECT o.id, o.kullanici_adi, o.seviye, o.guc, o.sayginlik,
                   o.saglik, o.max_saglik, o.grup_id, g.ad as grup_adi
            FROM oyuncu o LEFT JOIN grup g ON o.grup_id=g.id
            WHERE o.id != ? AND o.dogrulandi=1
            ORDER BY o.sayginlik DESC
        """, (oyuncu['id'],)).fetchall()
    return render_template('oyuncular.html', oyuncu=oyuncu, liste=[dict(r) for r in rows])

@app.route('/saldir/<int:hedef_id>', methods=['POST'])
@giris_gerekli
def saldir(hedef_id):
    icraat_yenile(session['oyuncu_id'])
    saldiran = get_oyuncu(session['oyuncu_id'])
    hedef = get_oyuncu(hedef_id)

    if not hedef or not hedef['dogrulandi']:
        return jsonify({'hata': 'Oyuncu bulunamadı!'})
    if saldiran['id'] == hedef_id:
        return jsonify({'hata': 'Kendine saldıramazsın!'})
    if saldiran['saglik'] < 20:
        return jsonify({'hata': 'Sağlığın çok düşük! Önce iyileş.'})
    if saldiran['icraat'] < 1:
        return jsonify({'hata': 'İcraat hakkın yok! Bekle veya dinlen.'})

    if saldiran['son_saldiri']:
        try:
            fark = (datetime.utcnow() - datetime.fromisoformat(saldiran['son_saldiri'])).total_seconds()
            if fark < 30:
                return jsonify({'hata': f"Saldırı bekleme: {int(30-fark)} saniye"})
        except: pass

    sal_guc = saldiran['guc'] + random.randint(1, int(saldiran['guc'] * 0.1) + 10)
    hdf_guc = hedef['guc'] + random.randint(1, int(hedef['guc'] * 0.08) + 8)

    with get_db() as c:
        c.execute("UPDATE oyuncu SET son_saldiri=?, icraat=icraat-1 WHERE id=?",
                  (datetime.utcnow().isoformat(), saldiran['id']))
        if sal_guc > hdf_guc:
            para = max(100, min(int(hedef['para'] * 0.08), 5000))
            xp = random.randint(20, 50) + hedef['seviye'] * 10
            sayginlik = random.randint(10, 30)
            c.execute("UPDATE oyuncu SET para=MAX(0,para-?), saglik=MAX(0,saglik-?) WHERE id=?",
                      (para, random.randint(5, 20), hedef_id))
            c.execute("UPDATE oyuncu SET para=para+?, sayginlik=sayginlik+? WHERE id=?",
                      (para, sayginlik, saldiran['id']))
            xp_ekle(c, saldiran['id'], xp)
            c.execute("INSERT INTO saldiri_kaydi VALUES (NULL,?,?,?,?,?,?,?)",
                      (saldiran['id'], hedef_id, saldiran['id'], para, xp, sayginlik, datetime.utcnow().isoformat()))
            return jsonify({'kazanan': 'sen', 'para': para, 'xp': xp, 'sayginlik': sayginlik,
                            'mesaj': f"Zafer! {hedef['kullanici_adi']} ezildi. +{para:,}₺ +{xp} XP +{sayginlik} Saygınlık"})
        else:
            para = max(50, min(int(saldiran['para'] * 0.04), 2000))
            c.execute("UPDATE oyuncu SET para=MAX(0,para-?), saglik=MAX(0,saglik-?) WHERE id=?",
                      (para, random.randint(10, 30), saldiran['id']))
            c.execute("INSERT INTO saldiri_kaydi VALUES (NULL,?,?,?,?,?,?,?)",
                      (saldiran['id'], hedef_id, hedef_id, para, 0, 0, datetime.utcnow().isoformat()))
            return jsonify({'kazanan': 'hedef', 'para': para, 'xp': 0, 'sayginlik': 0,
                            'mesaj': f"{hedef['kullanici_adi']} seni geri püskürttü. -{para:,}₺"})

@app.route('/iyiles', methods=['POST'])
@giris_gerekli
def iyiles():
    oyuncu = get_oyuncu(session['oyuncu_id'])
    eksik = oyuncu['max_saglik'] - oyuncu['saglik']
    if eksik == 0:
        return jsonify({'hata': 'Zaten tam sağlıkta!'})
    maliyet = eksik * 5
    if oyuncu['para'] < maliyet:
        return jsonify({'hata': f'Yeterli paran yok. Gerekli: {maliyet:,}₺'})
    with get_db() as c:
        c.execute("UPDATE oyuncu SET para=para-?, saglik=max_saglik WHERE id=?",
                  (maliyet, oyuncu['id']))
    return jsonify({'basari': f'Tamamen iyileştin! -{maliyet:,}₺'})

# ── SIRALAMA ──────────────────────────────────────────────────────────────────

@app.route('/siralama')
@giris_gerekli
def siralama():
    icraat_yenile(session['oyuncu_id'])
    oyuncu = get_oyuncu(session['oyuncu_id'])
    with get_db() as c:
        rows = c.execute("""
            SELECT o.id, o.kullanici_adi, o.seviye, o.sayginlik, o.guc, o.para,
                   g.ad as grup_adi,
                   (SELECT COUNT(*) FROM saldiri_kaydi WHERE kazanan_id=o.id) as galibiyet
            FROM oyuncu o LEFT JOIN grup g ON o.grup_id=g.id
            WHERE o.dogrulandi=1
            ORDER BY o.sayginlik DESC LIMIT 50
        """).fetchall()
    return render_template('siralama.html', oyuncu=oyuncu, oyuncular=[dict(r) for r in rows])

# ── MAFYA GRUPLARI ────────────────────────────────────────────────────────────

@app.route('/gruplar')
@giris_gerekli
def gruplar():
    icraat_yenile(session['oyuncu_id'])
    oyuncu = get_oyuncu(session['oyuncu_id'])
    with get_db() as c:
        rows = c.execute("""
            SELECT g.*, COUNT(o.id) as uye_sayisi
            FROM grup g LEFT JOIN oyuncu o ON o.grup_id=g.id
            GROUP BY g.id ORDER BY uye_sayisi DESC
        """).fetchall()
    return render_template('gruplar.html', oyuncu=oyuncu, gruplar=[dict(r) for r in rows])

@app.route('/grup-kur', methods=['POST'])
@giris_gerekli
def grup_kur():
    oyuncu = get_oyuncu(session['oyuncu_id'])
    if oyuncu['grup_id']:
        return jsonify({'hata': 'Zaten bir gruba üyesin!'})
    if oyuncu['para'] < 5000:
        return jsonify({'hata': 'Grup kurmak 5.000₺ gerektirir!'})
    ad = request.form.get('ad', '').strip()
    aciklama = request.form.get('aciklama', '').strip()
    if len(ad) < 3:
        return jsonify({'hata': 'Grup adı en az 3 karakter!'})
    with get_db() as c:
        if c.execute("SELECT id FROM grup WHERE ad=?", (ad,)).fetchone():
            return jsonify({'hata': 'Bu isim alınmış!'})
        c.execute("INSERT INTO grup (ad, aciklama, kurucu_id) VALUES (?,?,?)",
                  (ad, aciklama, oyuncu['id']))
        grup_id = c.execute("SELECT id FROM grup WHERE ad=?", (ad,)).fetchone()[0]
        c.execute("UPDATE oyuncu SET para=para-5000, grup_id=?, grup_rol='lider' WHERE id=?",
                  (grup_id, oyuncu['id']))
    return jsonify({'basari': f'{ad} mafya grubu kuruldu!'})

@app.route('/grup-katil/<int:grup_id>', methods=['POST'])
@giris_gerekli
def grup_katil(grup_id):
    oyuncu = get_oyuncu(session['oyuncu_id'])
    if oyuncu['grup_id']:
        return jsonify({'hata': 'Zaten bir gruba üyesin!'})
    with get_db() as c:
        g = c.execute("SELECT * FROM grup WHERE id=?", (grup_id,)).fetchone()
        if not g:
            return jsonify({'hata': 'Grup bulunamadı!'})
        c.execute("UPDATE oyuncu SET grup_id=?, grup_rol='uye' WHERE id=?", (grup_id, oyuncu['id']))
    return jsonify({'basari': f"{g['ad']} grubuna katıldın!"})

@app.route('/gruptan-ayril', methods=['POST'])
@giris_gerekli
def gruptan_ayril():
    oyuncu = get_oyuncu(session['oyuncu_id'])
    if not oyuncu['grup_id']:
        return jsonify({'hata': 'Bir gruba üye değilsin!'})
    if oyuncu['grup_rol'] == 'lider':
        return jsonify({'hata': 'Lider grubu terk edemez!'})
    with get_db() as c:
        c.execute("UPDATE oyuncu SET grup_id=NULL, grup_rol='uye' WHERE id=?", (oyuncu['id'],))
    return jsonify({'basari': 'Gruptan ayrıldın.'})

@app.route('/profil/<int:oid>')
@giris_gerekli
def profil(oid):
    icraat_yenile(session['oyuncu_id'])
    oyuncu = get_oyuncu(session['oyuncu_id'])
    hedef = get_oyuncu(oid)
    if not hedef:
        return redirect(url_for('oyuncular'))
    with get_db() as c:
        galibiyet = c.execute("SELECT COUNT(*) FROM saldiri_kaydi WHERE kazanan_id=? AND saldiran_id=?",
                               (oid, oid)).fetchone()[0]
        limanlar_list = c.execute("SELECT liman_adi FROM liman WHERE oyuncu_id=?", (oid,)).fetchall()
    return render_template('profil.html', oyuncu=oyuncu, hedef=hedef,
                           galibiyet=galibiyet, limanlar=[r[0] for r in limanlar_list])

init_db()

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
