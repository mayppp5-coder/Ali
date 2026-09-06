import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
import sqlite3
from datetime import datetime
import threading
import time
import logging
import traceback
import requests

# ======================================================================
# ====================== الإعدادات العامة (Config) =====================
# ======================================================================

TOKEN = '8879116228:AAHEiwJ8sUBV3TgMkpgWiQiUyiUHDtnEWEM'
bot = telebot.TeleBot(TOKEN)

# ⚠️ ضع يوزر حسابك الشخصي بتليجرام هنا (بدون تغيير @ لو موجود)
DEVELOPER_USERNAME = "@Q4_91"
DEVELOPER_ID = 1329831028  # آيدي المطور (احتياطي، يُستخدم كمرجع فقط)

# 👑 الأدمنية الأساسيين (Super Admins) — لا يمكن حذفهم من داخل البوت
# فقط هؤلاء يقدرون يضيفون/يحذفون أدمنية آخرين
SUPER_ADMIN_IDS = [1329831028]

# ============== الاشتراك الإجباري ==============
# هذه القيم تُستخدم فقط كقيمة افتراضية أول مرة يشتغل فيها البوت.
# بعد ذلك، يمكن للأدمن تغيير القناة من داخل لوحة التحكم مباشرة
# (يُحفظ التغيير بقاعدة البيانات ولا يحتاج تعديل الكود ولا إعادة رفع البوت).

CHANNEL_USERNAME = "@Q4_92"          # يوزر القناة (لازم يبدأ بـ @)
CHANNEL_URL = "https://t.me/Q4_92"   # رابط القناة للزر

# ============== النسخ الاحتياطي على قناة خاصة ==============
# ضع هنا آيدي قناة خاصة (البوت لازم يكون أدمن فيها) عشان تُرسل لها النسخ
# الاحتياطية تلقائياً، بالإضافة لإرسالها للأدمنية الأساسيين. القناة أضمن
# لأنها ما تضيع حتى لو تغيّر آيدي المطور أو حظر الشخصي.
# مثال: BACKUP_CHANNEL_ID = -1001234567890
BACKUP_CHANNEL_ID = None

# ============== إعدادات الحماية من الضغط السريع (Rate Limiting) ==============
RATE_LIMIT_SECONDS = 0.6   # أقل مدة مسموحة بين ضغطتين متتاليتين لنفس المستخدم
RATE_LIMIT_STRIKES = 3     # عدد الضغطات السريعة المسموحة قبل التنبيه
RATE_LIMIT_LOCK_SECONDS = 3  # مدة الحظر المؤقت بعد تجاوز عدد الضغطات المسموح

DB_FILE = "users.db"

# ======================================================================
# ========================= تسجيل الأخطاء (Logging) =====================
# ======================================================================
# كل خطأ غير متوقع بيتسجل بملف bot_errors.log مع الوقت وتفاصيل الخطأ،
# حتى لو ما كنت مراقب شاشة البوت وقت حدوثه.

logging.basicConfig(
    filename="bot_errors.log",
    level=logging.ERROR,
    format="%(asctime)s - %(levelname)s - %(message)s",
    encoding="utf-8"
)

def notify_super_admins_of_error(context, error):
    """يرسل تنبيه مختصر للأدمنية الأساسيين عند حدوث خطأ (اختياري لكن مفيد)."""
    for admin_id in SUPER_ADMIN_IDS:
        try:
            bot.send_message(admin_id, f"⚠️ حدث خطأ في البوت ({context}):\n{error}")
        except Exception:
            pass

def safe_handler(func):
    """ديكوريتر يلف أي هاندلر ليضمن تسجيل أي استثناء بملف اللوغ بدل ما يوقف البوت."""
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            logging.exception(f"خطأ في {func.__name__}: {e}")
            notify_super_admins_of_error(func.__name__, e)
    wrapper.__name__ = func.__name__
    return wrapper

# ======================================================================
# ==================== الحماية من الضغط السريع (Rate Limit) ================
# ======================================================================
# القاعدة الجديدة: أول ضغطتين سريعتين متتاليتين يتم تجاهلهما بهدوء (بدون
# إزعاج المستخدم برسالة كل مرة)، وبالضغطة الثالثة السريعة يظهر تنبيه
# "انتظر 3 ثواني" ويتم قفل الأزرار له لمدة 3 ثواني كاملة حتى لا يُغرق
# البوت بالطلبات ويتوقف (يكرش).
# هذا حد بسيط بالذاكرة (مو قاعدة بيانات) لأنه مؤقت بطبيعته.

_last_action_time = {}     # آخر وقت ضغط لكل مستخدم
_rapid_press_count = {}    # عداد الضغطات السريعة المتتالية لكل مستخدم
_lock_until = {}           # وقت انتهاء القفل المؤقت لكل مستخدم

def rate_limited(seconds=RATE_LIMIT_SECONDS):
    def decorator(func):
        def wrapper(update_obj, *args, **kwargs):
            uid = update_obj.from_user.id
            # الأدمنية معفيين من التحديد عشان ما يعيقهم أثناء الإدارة
            if is_admin(uid):
                return func(update_obj, *args, **kwargs)

            now = time.time()

            # المستخدم مقفول مؤقتاً بسبب ضغطات سريعة سابقة
            lock_until = _lock_until.get(uid, 0)
            if now < lock_until:
                remaining = max(1, int(lock_until - now + 0.999))
                try:
                    bot.answer_callback_query(update_obj.id, f"⏳ الرجاء الانتظار {remaining} ثانية قبل المحاولة مرة أخرى", show_alert=True)
                except Exception:
                    pass
                return

            last = _last_action_time.get(uid, 0)
            if now - last < seconds:
                # ضغطة سريعة جداً بعد الضغطة السابقة
                count = _rapid_press_count.get(uid, 0) + 1
                _rapid_press_count[uid] = count
                _last_action_time[uid] = now

                if count >= RATE_LIMIT_STRIKES:
                    # وصل لعدد الضغطات المسموح تجاوزه: يُقفل ويُنبَّه
                    _lock_until[uid] = now + RATE_LIMIT_LOCK_SECONDS
                    _rapid_press_count[uid] = 0
                    try:
                        bot.answer_callback_query(update_obj.id, f"🚦 لا تضغط بسرعة، انتظر {RATE_LIMIT_LOCK_SECONDS} ثواني حتى لا يتوقف البوت", show_alert=True)
                    except Exception:
                        pass
                else:
                    # ضغطة سريعة لكن لم تصل للحد بعد: تجاهل هادئ بدون رسالة مزعجة
                    try:
                        bot.answer_callback_query(update_obj.id)
                    except Exception:
                        pass
                return

            # ضغطة طبيعية (مر وقت كافٍ منذ آخر ضغطة): صفّر العداد ونفّذ الأمر
            _rapid_press_count[uid] = 0
            _last_action_time[uid] = now
            return func(update_obj, *args, **kwargs)
        wrapper.__name__ = func.__name__
        return wrapper
    return decorator

# ======================================================================
# ============================ قاعدة البيانات ============================
# ======================================================================

def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        username TEXT,
        first_name TEXT,
        joined_at TEXT,
        last_seen TEXT,
        is_banned INTEGER DEFAULT 0
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS tests (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        category TEXT,
        name TEXT,
        url TEXT
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS admins (
        user_id INTEGER PRIMARY KEY,
        added_by INTEGER,
        added_at TEXT
    )''')
    # عدّاد المشاهدات: يحسب كم مرة انفتحت كل مادة/فصل، لمعرفة أكثر شي يستخدمه الطلاب
    c.execute('''CREATE TABLE IF NOT EXISTS view_counts (
        scope TEXT,
        key TEXT,
        count INTEGER DEFAULT 0,
        PRIMARY KEY (scope, key)
    )''')
    # كاش الروابط المختصرة، عشان ما نطلب نفس الرابط الطويل مرتين من خدمة الاختصار
    c.execute('''CREATE TABLE IF NOT EXISTS short_link_cache (
        long_url TEXT PRIMARY KEY,
        short_url TEXT
    )''')
    # إعدادات عامة قابلة للتغيير من لوحة الأدمن (مثل قناة الاشتراك الإجباري)
    c.execute('''CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY,
        value TEXT
    )''')
    conn.commit()
    conn.close()

def migrate_db():
    """يضيف أعمدة جديدة لقاعدة بيانات قديمة موجودة أصلاً بدون ما يمسح البيانات."""
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    for stmt in [
        "ALTER TABLE tests ADD COLUMN is_active INTEGER DEFAULT 1",
        "ALTER TABLE tests ADD COLUMN short_url TEXT",
        "ALTER TABLE users ADD COLUMN is_vip INTEGER DEFAULT 0",
    ]:
        try:
            c.execute(stmt)
        except sqlite3.OperationalError:
            pass  # العمود موجود مسبقاً
    conn.commit()
    conn.close()

# ---------- إعدادات عامة (Settings) ----------

def get_setting(key, default=None):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT value FROM settings WHERE key=?", (key,))
    row = c.fetchone()
    conn.close()
    return row[0] if row and row[0] else default

def set_setting(key, value):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute(
        "INSERT INTO settings (key, value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=?",
        (key, value, value)
    )
    conn.commit()
    conn.close()

def get_channel_username():
    """يوزر قناة الاشتراك الإجباري الحالية (من قاعدة البيانات، أو القيمة
    الافتراضية بالأعلى لو الأدمن ما غيّرها بعد)."""
    return get_setting("channel_username", CHANNEL_USERNAME)

def get_channel_url():
    """رابط قناة الاشتراك الإجباري الحالية (لزر '📢 اشترك بالقناة')."""
    return get_setting("channel_url", CHANNEL_URL)

def set_channel(username, url):
    set_setting("channel_username", username)
    set_setting("channel_url", url)

# ---------- المستخدمون ----------

def save_user(user):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    c.execute("SELECT user_id FROM users WHERE user_id=?", (user.id,))
    exists = c.fetchone()
    if exists:
        c.execute("UPDATE users SET last_seen=?, username=?, first_name=? WHERE user_id=?",
                  (now, user.username, user.first_name, user.id))
    else:
        c.execute(
            "INSERT INTO users (user_id, username, first_name, joined_at, last_seen, is_banned) VALUES (?,?,?,?,?,0)",
            (user.id, user.username, user.first_name, now, now)
        )
    conn.commit()
    conn.close()

def is_user_banned(user_id):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT is_banned FROM users WHERE user_id=?", (user_id,))
    row = c.fetchone()
    conn.close()
    return row is not None and row[0] == 1

def set_ban_status(user_id, status):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("UPDATE users SET is_banned=? WHERE user_id=?", (status, user_id))
    conn.commit()
    conn.close()

def get_all_user_ids(exclude_banned=True):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    if exclude_banned:
        c.execute("SELECT user_id FROM users WHERE is_banned=0")
    else:
        c.execute("SELECT user_id FROM users")
    rows = [r[0] for r in c.fetchall()]
    conn.close()
    return rows

def get_stats():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM users")
    total = c.fetchone()[0]
    today = datetime.now().strftime("%Y-%m-%d")
    c.execute("SELECT COUNT(*) FROM users WHERE joined_at LIKE ?", (f"{today}%",))
    new_today = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM users WHERE last_seen LIKE ?", (f"{today}%",))
    active_today = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM users WHERE is_banned=1")
    banned = c.fetchone()[0]
    conn.close()
    return total, new_today, active_today, banned

# ---------- اشتراك VIP ----------
# الاشتراك دائم لكامل العام الدراسي (لا ينتهي تلقائياً)، يُدار يدوياً من
# لوحة الأدمن بواسطة يوزر الطالب. يشترط أن الطالب ضغط /start قبل، حتى
# يكون يوزره محفوظاً بجدول users.

def is_vip(user_id):
    if is_admin(user_id):
        return True  # الأدمنية يشوفون كل شيء دائماً بدون قيود
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT is_vip FROM users WHERE user_id=?", (user_id,))
    row = c.fetchone()
    conn.close()
    return row is not None and row[0] == 1

def get_user_id_by_username(username):
    username = username.lstrip('@')
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT user_id FROM users WHERE username = ? COLLATE NOCASE", (username,))
    row = c.fetchone()
    conn.close()
    return row[0] if row else None

def set_vip_status_by_username(username, status):
    """يفعّل/يلغي VIP لمستخدم بالاعتماد على يوزره. يرجّع آيدي المستخدم لو
    لقاه، أو None لو ما كان بقاعدة البيانات (يعني ما ضغط /start أبداً)."""
    uid = get_user_id_by_username(username)
    if uid is None:
        return None
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("UPDATE users SET is_vip=? WHERE user_id=?", (status, uid))
    conn.commit()
    conn.close()
    return uid

def get_all_vip_users():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT user_id, username, first_name FROM users WHERE is_vip=1")
    rows = c.fetchall()
    conn.close()
    return rows

# ---------- الأدمنية (Admins) ----------

def is_super_admin(user_id):
    return user_id in SUPER_ADMIN_IDS

def get_db_admin_ids():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT user_id FROM admins")
    rows = [r[0] for r in c.fetchall()]
    conn.close()
    return rows

def is_admin(user_id):
    return user_id in SUPER_ADMIN_IDS or user_id in get_db_admin_ids()

def add_admin_db(user_id, added_by):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    c.execute("INSERT OR IGNORE INTO admins (user_id, added_by, added_at) VALUES (?,?,?)",
              (user_id, added_by, now))
    conn.commit()
    conn.close()

def remove_admin_db(user_id):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("DELETE FROM admins WHERE user_id=?", (user_id,))
    conn.commit()
    conn.close()

def get_all_admins_display():
    """يرجع نص يوضح الأدمنية الأساسيين والمضافين."""
    lines = ["👑 الأدمنية الأساسيون (لا يمكن حذفهم):"]
    for a in SUPER_ADMIN_IDS:
        lines.append(f"  • {a}")
    db_admins = get_db_admin_ids()
    lines.append("\n➕ الأدمنية المضافون:")
    if db_admins:
        for a in db_admins:
            lines.append(f"  • {a}")
    else:
        lines.append("  (لا يوجد)")
    return "\n".join(lines)

# ---------- روابط مختصرة (Short Links) ----------

def shorten_url(long_url):
    """يختصر رابط طويل عن طريق خدمة is.gd المجانية، ويحتفظ بنسخة بالكاش
    حتى لا يطلب نفس الرابط مرتين. لو فشل الاختصار لأي سبب (بدون إنترنت،
    الخدمة متوقفة...) يرجّع الرابط الأصلي بدون ما يوقف البوت."""
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT short_url FROM short_link_cache WHERE long_url=?", (long_url,))
    row = c.fetchone()
    if row and row[0]:
        conn.close()
        return row[0]

    short = long_url
    try:
        resp = requests.get(
            "https://is.gd/create.php",
            params={"format": "simple", "url": long_url},
            timeout=5
        )
        if resp.status_code == 200 and resp.text.startswith("http"):
            short = resp.text.strip()
    except Exception as e:
        logging.exception(f"فشل اختصار الرابط {long_url}: {e}")

    c.execute("INSERT OR REPLACE INTO short_link_cache (long_url, short_url) VALUES (?, ?)", (long_url, short))
    conn.commit()
    conn.close()
    return short

# ---------- إحصائيات الاستخدام (View Counts) ----------

def log_view(scope, key):
    """يزيد عداد المشاهدات لمادة (scope='subject') أو فصل/قسم (scope='chapter')."""
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute(
        """INSERT INTO view_counts (scope, key, count) VALUES (?, ?, 1)
           ON CONFLICT(scope, key) DO UPDATE SET count = count + 1""",
        (scope, key)
    )
    conn.commit()
    conn.close()

def get_top_views(scope, limit=5):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT key, count FROM view_counts WHERE scope=? ORDER BY count DESC LIMIT ?", (scope, limit))
    rows = c.fetchall()
    conn.close()
    return rows

# ---------- الاختبارات (Tests) ----------

def save_test(category, name, url):
    short = shorten_url(url)
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute(
        "INSERT INTO tests (category, name, url, short_url, is_active) VALUES (?,?,?,?,1)",
        (category, name, url, short)
    )
    conn.commit()
    conn.close()

def get_db_tests(category):
    """الاختبارات المفعّلة فقط، مع استخدام الرابط المختصر إن وجد — هذا ما يشوفه الطالب."""
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT name, url, short_url FROM tests WHERE category=? AND is_active=1 ORDER BY id", (category,))
    rows = [{"name": r[0], "url": (r[2] or r[1])} for r in c.fetchall()]
    conn.close()
    return rows

def get_db_tests_full(category):
    """يرجع كل الاختبارات (مفعّلة ومعطّلة) بكل تفاصيلها، مستخدم بلوحة الإدارة."""
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT id, name, url, short_url, is_active FROM tests WHERE category=? ORDER BY id", (category,))
    rows = [{"id": r[0], "name": r[1], "url": r[2], "short_url": r[3], "is_active": r[4]} for r in c.fetchall()]
    conn.close()
    return rows

def get_test_by_id(test_id):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT id, category, name, url, short_url, is_active FROM tests WHERE id=?", (test_id,))
    row = c.fetchone()
    conn.close()
    if row:
        return {"id": row[0], "category": row[1], "name": row[2], "url": row[3], "short_url": row[4], "is_active": row[5]}
    return None

def update_test(test_id, name, url):
    short = shorten_url(url)
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("UPDATE tests SET name=?, url=?, short_url=? WHERE id=?", (name, url, short, test_id))
    conn.commit()
    conn.close()

def set_test_active(test_id, status):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("UPDATE tests SET is_active=? WHERE id=?", (status, test_id))
    conn.commit()
    conn.close()

def delete_test(test_id):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("DELETE FROM tests WHERE id=?", (test_id,))
    conn.commit()
    conn.close()

def get_display_tests(category):
    """قائمة الاختبارات النهائية اللي تنعرض للطالب: المكتوبة بالكود (بروابط
    مختصرة أيضاً) + المضافة من لوحة الأدمن والمفعّلة فقط."""
    tests = []
    for t in data.get(category, []):
        tests.append({"name": t["name"], "url": shorten_url(t["url"])})
    tests += get_db_tests(category)
    return tests

init_db()
migrate_db()

# ======================================================================
# ============================ حالات الذاكرة المؤقتة ======================
# ======================================================================
# هذه القواميس تحفظ بالذاكرة فقط (تنتهي عند إعادة تشغيل البوت) لتتبع
# ماذا ينتظر كل أدمن من إدخال (نص/رقم) بالخطوة التالية.

admin_state = {}          # حالة عامة: بث / حظر / فك حظر / تغيير قناة الاشتراك
add_test_state = {}       # حالة إضافة اختبار جديد: {admin_id: {"stage", "category", "name"}}
edit_test_state = {}      # حالة تعديل اختبار: {admin_id: {"stage", "id", "name"}}
admin_mgmt_state = {}     # حالة إدارة الأدمنية: {admin_id: "waiting_add_admin" / "waiting_remove_admin"}

# إعدادات المواد والفصول المستخدمة عند إضافة/تعديل/حذف اختبار من لوحة الأدمن
SUBJECTS = [
    ("islamic", "التربية الإسلامية 🕋"),
    ("arabic", "اللغة العربية 📝"),
    ("english", "اللغة الإنكليزية 🔠"),
    ("math", "الرياضيات 🔢"),
    ("biology", "الأحياء 🧬"),
    ("physics", "الفيزياء ⚡"),
    ("chemistry", "الكيمياء 🧪"),
]

CHAPTER_CONFIG = {
    "math":      {"type": "numeric", "count": 5,  "prefix": "test_math_ch"},
    "biology":   {"type": "numeric", "count": 5,  "prefix": "test_bio_ch"},
    "physics":   {"type": "numeric", "count": 10, "prefix": "test_phy_ch"},
    "chemistry": {"type": "numeric", "count": 8,  "prefix": "test_chem_ch"},
    "islamic": {"type": "fixed", "options": [
        ("أحكام التلاوة", "test_isl_ahkam"),
        ("الوحدة 1", "test_isl_u1"),
        ("الوحدة 2", "test_isl_u2"),
        ("الوحدة 3", "test_isl_u3"),
        ("الوحدة 4", "test_isl_u4"),
        ("الوحدة 5", "test_isl_u5"),
    ]},
    "arabic": {"type": "fixed", "options": [
        ("القواعد", "test_ar_grammar"),
        ("الأدب والنصوص", "test_ar_literature"),
    ]},
    "english": {"type": "fixed", "options": [
        ("القواعد (Grammar)", "test_en_grammar"),
        ("قطع الكتاب (Textbook)", "test_en_passages"),
        ("الأدب (Literature)", "test_en_literature"),
    ]},
}

SUBJECT_LABELS = dict(SUBJECTS)  # key -> الاسم المعروض، يُستخدم بعرض الإحصائيات

def build_category_labels():
    """يبني قاموس category -> اسم مقروء (مثلاً 'الرياضيات - الفصل 1')
    يُستخدم فقط لعرض الإحصائيات بشكل مفهوم."""
    labels = {}
    for key, subj_label in SUBJECTS:
        config = CHAPTER_CONFIG.get(key)
        if not config:
            continue
        if config["type"] == "numeric":
            for i in range(1, config["count"] + 1):
                labels[f"{config['prefix']}{i}"] = f"{subj_label} - الفصل {i}"
        else:
            for label, category in config["options"]:
                labels[category] = f"{subj_label} - {label}"
    return labels

CATEGORY_LABELS = build_category_labels()

# ======================================================================
# ============================== نظام VIP ================================
# ======================================================================
# الأقسام المجانية المتاحة للجميع: الفصل الأول من كل مادة فيها فصول رقمية،
# بالإضافة لقسم واحد محدد من كل مادة نصية. أي قسم غير موجود بهذه القائمة
# يعتبر تلقائياً حصري لمشتركي VIP.

FREE_CATEGORIES = {
    "test_math_ch1",
    "test_bio_ch1",
    "test_phy_ch1",
    "test_chem_ch1",
    "test_ar_grammar",
    "test_en_grammar",
    "test_isl_ahkam",
}

def is_category_free(category):
    return category in FREE_CATEGORIES

VIP_PRICE = "20,000"

VIP_UPSELL_TEXT = (
    "🔒 هذا القسم حصري لمشتركي VIP\n\n"
    "اشترك الآن بخدمة VIP واحصل على:\n"
    "✅ فتح جميع الفصول وجميع المواد بالكامل\n"
    "✅ فتح جميع مميزات البوت بدون أي قيود\n"
    "✅ دعم مباشر وأولوية بالرد على استفساراتك\n\n"
    f"💵 السعر: {VIP_PRICE} د.ع (اشتراك لكامل العام الدراسي)\n\n"
    "للاشتراك، تواصل مباشرة مع المطور 👇"
)

def vip_upsell_markup(back_target):
    markup = InlineKeyboardMarkup(row_width=1)
    markup.add(InlineKeyboardButton("💬 تواصل للاشتراك", url=f"https://t.me/{DEVELOPER_USERNAME.lstrip('@')}", style="success"))
    markup.add(back_btn(back_target))
    return markup

def chapter_label(label, category, user_id):
    """يضيف 🔒 أمام اسم القسم لو كان حصري VIP والمستخدم مو مشترك، عشان
    الطالب يعرف مسبقاً وش مفتوح ووش مقفول قبل ما يضغط."""
    if is_category_free(category) or is_vip(user_id):
        return label
    return f"🔒 {label}"

# ======================================================================
# ============================ بيانات الاختبارات ==========================
# ======================================================================

data = {
    "test_math_ch1": [
        {"name": "الصيغة العادية للعدد المركب", "url": "https://reliable-macaron-1a86c6.netlify.app/"},
        {"name": "قيم X، y", "url": "https://willowy-rolypoly-f5eaa6.netlify.app/"},
        {"name": "الجذور التربيعية", "url": "https://zesty-dragon-1890d4.netlify.app/"},
        {"name": "حل المعادلة في C", "url": "https://jazzy-kleicha-9b72e0.netlify.app/"},
        {"name": "تكوين المعادلة التربيعية", "url": "https://darling-marigold-a87bae.netlify.app/"},
        {"name": "الصيغة القطبية", "url": "https://coruscating-blancmange-d2e88f.netlify.app/"},
        {"name": "مبرهنة ديموافر", "url": "https://curious-kelpie-a1ca79.netlify.app/"},
        {"name": "نتيجة مبرهنة ديموافر", "url": "https://stellular-axolotl-d54d1a.netlify.app/"},
        {"name": "اوميكا", "url": "https://gilded-truffle-d58aa4.netlify.app/"}
    ],
    "test_bio_ch1": [
        {"name": "من بداية الفصل الى خلية حقيقية النواة", "url": "https://enchanting-chaja-6ac535.netlify.app/"},
        {"name": "من خلية حقيقية النواة الى جهاز كولجي", "url": "https://vocal-shortbread-e5673b.netlify.app/"},
        {"name": "من جهاز كولجي الى الجسيمات الحالة", "url": "https://magical-malasada-cf8bf6.netlify.app/"},
        {"name": "من الجسيمات الحالة الى الجسيم الحركي", "url": "https://splendid-paprenjak-0646d4.netlify.app/"},
        {"name": "من الجسيم الحركي الى النواة", "url": "https://moonlit-pixie-df2b42.netlify.app/"},
        {"name": "من النواة الى الانشطة الخلوية", "url": "https://unrivaled-daffodil-84f736.netlify.app/"},
        {"name": "من الانشطة الخلوية الى الايض الخلوي", "url": "https://subtle-strudel-930e47.netlify.app/"},
        {"name": "من الايض الخلوي الى الانقسامات", "url": "https://prismatic-pixie-398ad2.netlify.app/"},
        {"name": "الانقسامات", "url": "https://poetic-tanuki-405148.netlify.app/"}
    ],
    "test_bio_ch2": [
        {"name": "من المقدمة الى نسيج الاساس", "url": "https://zingy-pudding-a3ec29.netlify.app/"},
        {"name": "من نسيج الاساس الى نسيج الحيوان", "url": "https://coruscating-dolphin-f6100a.netlify.app/"},
        {"name": "من نسيج الحيوان الى نسيج الظهاري المطبق", "url": "https://shiny-squirrel-d9b312.netlify.app/"},
        {"name": "من نسيج الظهاري المطبق الى نسيج الضام الرابط", "url": "https://peaceful-frangipane-98ca70.netlify.app/"},
        {"name": "من نسيج الظام الرابط الى نسيج الظام المتخصص", "url": "https://papaya-narwhal-488037.netlify.app/"},
        {"name": "من نسيج الظام المتخصص الى الدم", "url": "https://profound-kangaroo-3be276.netlify.app/"},
        {"name": "من الدم الى النسيج العضلي", "url": "https://cozy-paprenjak-bf0774.netlify.app/"},
        {"name": "النسيج العضلي والعصبي", "url": "https://zesty-donut-e97995.netlify.app/"},
        {"name": "ما نوع النسيج", "url": "http://boisterous-bonbon-a17dfd.netlify.app"}
    ]
}

# ======================================================================
# ============================ قوائم الأزرار (Keyboards) ==================
# ======================================================================

def back_btn(callback_data):
    return InlineKeyboardButton("🔙 رجوع", callback_data=callback_data, style="danger")

def subscription_markup():
    markup = InlineKeyboardMarkup(row_width=1)
    markup.add(
        InlineKeyboardButton("📢 اشترك بالقناة", url=get_channel_url(), style="primary"),
        InlineKeyboardButton("✅ تحقق من الاشتراك", callback_data="check_sub", style="success")
    )
    return markup

def main_menu():
    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(
        InlineKeyboardButton("التربية الإسلامية 🕋", callback_data="islamic", style="primary"),
        InlineKeyboardButton("اللغة العربية 📝", callback_data="arabic", style="primary"),
        InlineKeyboardButton("اللغة الإنكليزية 🔠", callback_data="english", style="primary"),
        InlineKeyboardButton("الرياضيات 🔢", callback_data="math", style="primary"),
        InlineKeyboardButton("الأحياء 🧬", callback_data="biology", style="primary"),
        InlineKeyboardButton("الفيزياء ⚡", callback_data="physics", style="primary"),
        InlineKeyboardButton("الكيمياء 🧪", callback_data="chemistry", style="primary")
    )
    # زر المطور: يفتح مباشرة محادثة خاصة مع حساب المطور الشخصي
    markup.add(
        InlineKeyboardButton("👨‍💻 المطور", url=f"https://t.me/{DEVELOPER_USERNAME.lstrip('@')}", style="danger")
    )
    return markup

def admin_menu(user_id):
    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(
        InlineKeyboardButton("📢 إذاعة", callback_data="admin_broadcast", style="primary"),
        InlineKeyboardButton("📊 الإحصائيات", callback_data="admin_stats", style="success"),
        InlineKeyboardButton("📈 الأكثر استخداماً", callback_data="admin_top_stats", style="success"),
        InlineKeyboardButton("🚫 حظر مستخدم", callback_data="admin_ban", style="danger"),
        InlineKeyboardButton("✅ فك الحظر", callback_data="admin_unban", style="success"),
        InlineKeyboardButton("➕ إضافة اختبار", callback_data="admin_add_test", style="primary"),
        InlineKeyboardButton("✏️ تعديل/حذف اختبار", callback_data="admin_edit_test", style="primary"),
        InlineKeyboardButton("📡 تغيير قناة الاشتراك", callback_data="admin_set_channel", style="primary"),
        InlineKeyboardButton("⭐ إدارة VIP", callback_data="admin_manage_vip", style="success"),
    )
    # إدارة الأدمنية متاحة فقط للأدمن الأساسي
    if is_super_admin(user_id):
        markup.add(InlineKeyboardButton("👑 إدارة الأدمنية", callback_data="admin_manage_admins", style="danger"))
    return markup

def subject_markup(callback_prefix, include_cancel=True):
    """قائمة اختيار المادة، تُستخدم عند إضافة أو تعديل/حذف اختبار."""
    markup = InlineKeyboardMarkup(row_width=2)
    for key, label in SUBJECTS:
        markup.add(InlineKeyboardButton(label, callback_data=f"{callback_prefix}{key}", style="primary"))
    if include_cancel:
        markup.add(InlineKeyboardButton("❌ إلغاء", callback_data="addtest_cancel", style="danger"))
    return markup

def chapter_markup(subject, callback_prefix, include_cancel=True):
    """قائمة اختيار الفصل/القسم لمادة معينة."""
    config = CHAPTER_CONFIG[subject]
    markup = InlineKeyboardMarkup(row_width=3)
    if config["type"] == "numeric":
        for i in range(1, config["count"] + 1):
            category = f"{config['prefix']}{i}"
            markup.add(InlineKeyboardButton(f"الفصل {i}", callback_data=f"{callback_prefix}{category}", style="success"))
    else:
        for label, category in config["options"]:
            markup.add(InlineKeyboardButton(label, callback_data=f"{callback_prefix}{category}", style="success"))
    if include_cancel:
        markup.add(InlineKeyboardButton("❌ إلغاء", callback_data="addtest_cancel", style="danger"))
    return markup

# ======================================================================
# ========================= أوامر المستخدم العادي =========================
# ======================================================================

def is_subscribed(user_id):
    try:
        member = bot.get_chat_member(get_channel_username(), user_id)
        return member.status not in ["left", "kicked"]
    except Exception:
        # لو صار خطأ (مثلاً البوت مو أدمن بالقناة)، نعتبره غير مشترك احتياطياً
        return False

@bot.message_handler(commands=['start'])
@safe_handler
def send_welcome(message):
    if is_user_banned(message.from_user.id):
        bot.send_message(message.chat.id, "🚫 تم حظرك من استخدام هذا البوت.")
        return

    if not is_subscribed(message.from_user.id):
        bot.send_message(
            message.chat.id,
            "⚠️ يجب الاشتراك بالقناة أولاً لاستخدام البوت:",
            reply_markup=subscription_markup()
        )
        return

    save_user(message.from_user)
    bot.send_message(message.chat.id, "أهلاً بك في بوت اختبارات السادس الإعدادي 🎓\nاختر المادة:", reply_markup=main_menu())

@bot.message_handler(commands=['dev'])
@safe_handler
def developer_info(message):
    markup = InlineKeyboardMarkup(row_width=1)
    markup.add(InlineKeyboardButton("💬 تواصل مع المطور", url=f"https://t.me/{DEVELOPER_USERNAME.lstrip('@')}", style="primary"))
    bot.send_message(
        message.chat.id,
        f"👨‍💻 تم تطوير هذا البوت بواسطة {DEVELOPER_USERNAME}\nلأي استفسار أو اقتراح، تواصل مباشرة 👇",
        reply_markup=markup
    )

@bot.callback_query_handler(func=lambda call: call.data == "check_sub")
@safe_handler
def handle_check_sub(call):
    if is_subscribed(call.from_user.id):
        save_user(call.from_user)
        bot.edit_message_text(
            "✅ تم التحقق من اشتراكك بنجاح!\nاختر المادة:",
            chat_id=call.message.chat.id, message_id=call.message.message_id,
            reply_markup=main_menu()
        )
    else:
        bot.answer_callback_query(call.id, "⚠️ لم تشترك بالقناة بعد!", show_alert=True)

# ======================================================================
# ============================ لوحة تحكم الأدمن ===========================
# ======================================================================

@bot.message_handler(commands=['admin'])
@safe_handler
def admin_panel(message):
    if not is_admin(message.from_user.id):
        return
    bot.send_message(message.chat.id, "🛠️ لوحة تحكم الأدمن:", reply_markup=admin_menu(message.from_user.id))

@bot.callback_query_handler(func=lambda call: call.data.startswith("admin_"))
@safe_handler
def handle_admin_callback(call):
    if not is_admin(call.from_user.id):
        bot.answer_callback_query(call.id, "🚫 ليس لديك صلاحية", show_alert=True)
        return

    if call.data == "admin_broadcast":
        admin_state[call.from_user.id] = "waiting_broadcast"
        bot.send_message(call.message.chat.id, "✏️ أرسل الآن الرسالة (نص/صورة/فيديو/ملف) التي تريد إذاعتها لجميع الطلاب:")

    elif call.data == "admin_stats":
        total, new_today, active_today, banned = get_stats()
        text = (
            f"📊 الإحصائيات:\n\n"
            f"👥 إجمالي المستخدمين: {total}\n"
            f"🆕 مستخدمين جدد اليوم: {new_today}\n"
            f"🟢 نشطين اليوم: {active_today}\n"
            f"🚫 محظورين: {banned}"
        )
        bot.send_message(call.message.chat.id, text)

    elif call.data == "admin_top_stats":
        top_subjects = get_top_views("subject", limit=7)
        top_chapters = get_top_views("chapter", limit=7)

        lines = ["📈 الأكثر استخداماً:\n", "📚 حسب المادة:"]
        if top_subjects:
            for key, count in top_subjects:
                label = SUBJECT_LABELS.get(key, key)
                lines.append(f"  • {label}: {count} فتحة")
        else:
            lines.append("  (لا توجد بيانات بعد)")

        lines.append("\n📂 حسب الفصل/القسم:")
        if top_chapters:
            for key, count in top_chapters:
                label = CATEGORY_LABELS.get(key, key)
                lines.append(f"  • {label}: {count} فتحة")
        else:
            lines.append("  (لا توجد بيانات بعد)")

        bot.send_message(call.message.chat.id, "\n".join(lines))

    elif call.data == "admin_ban":
        admin_state[call.from_user.id] = "waiting_ban"
        bot.send_message(call.message.chat.id, "أرسل آيدي المستخدم (رقم) المراد حظره:")

    elif call.data == "admin_unban":
        admin_state[call.from_user.id] = "waiting_unban"
        bot.send_message(call.message.chat.id, "أرسل آيدي المستخدم (رقم) المراد فك حظره:")

    elif call.data == "admin_add_test":
        bot.send_message(call.message.chat.id, "📚 اختر المادة التي تريد إضافة اختبار لها:",
                          reply_markup=subject_markup("addsubj_"))

    elif call.data == "admin_edit_test":
        bot.send_message(call.message.chat.id, "📚 اختر المادة التي تريد تعديل/حذف اختبار منها:",
                          reply_markup=subject_markup("editsubj_"))

    elif call.data == "admin_set_channel":
        admin_state[call.from_user.id] = "waiting_channel"
        current = get_channel_username()
        bot.send_message(
            call.message.chat.id,
            f"📡 قناة الاشتراك الإجباري الحالية: {current}\n\n"
            "أرسل الآن يوزر القناة الجديدة (يجب أن يبدأ بـ @، مثال: @MyChannel).\n"
            "⚠️ تأكد أن البوت مضاف كأدمن بالقناة الجديدة قبل الإرسال، وإلا لن يستطيع التحقق من اشتراك الطلاب."
        )

    elif call.data == "admin_manage_vip":
        markup = InlineKeyboardMarkup(row_width=1)
        markup.add(
            InlineKeyboardButton("➕ إضافة مشترك VIP", callback_data="vipmgmt_add", style="success"),
            InlineKeyboardButton("➖ إزالة مشترك VIP", callback_data="vipmgmt_remove", style="danger"),
            InlineKeyboardButton("📋 عرض قائمة VIP", callback_data="vipmgmt_list", style="primary"),
        )
        bot.send_message(call.message.chat.id, "⭐ إدارة مشتركي VIP:", reply_markup=markup)

    elif call.data == "admin_manage_admins":
        if not is_super_admin(call.from_user.id):
            bot.answer_callback_query(call.id, "🚫 هذه الميزة للأدمن الأساسي فقط", show_alert=True)
            return
        markup = InlineKeyboardMarkup(row_width=1)
        markup.add(
            InlineKeyboardButton("➕ إضافة أدمن", callback_data="admmgmt_add", style="success"),
            InlineKeyboardButton("➖ حذف أدمن", callback_data="admmgmt_remove", style="danger"),
            InlineKeyboardButton("📋 عرض القائمة", callback_data="admmgmt_list", style="primary"),
        )
        bot.send_message(call.message.chat.id, "👑 إدارة الأدمنية:", reply_markup=markup)

    bot.answer_callback_query(call.id)

@bot.message_handler(func=lambda m: admin_state.get(m.from_user.id) == "waiting_broadcast",
                      content_types=['text', 'photo', 'video', 'document', 'audio'])
@safe_handler
def handle_broadcast(message):
    admin_state.pop(message.from_user.id, None)
    user_ids = get_all_user_ids()
    sent, failed = 0, 0
    status_msg = bot.send_message(message.chat.id, f"⏳ جاري الإرسال إلى {len(user_ids)} مستخدم...")

    for uid in user_ids:
        try:
            bot.copy_message(uid, message.chat.id, message.message_id)
            sent += 1
        except Exception:
            failed += 1

    bot.edit_message_text(
        f"✅ تم الإرسال بنجاح إلى {sent} مستخدم\n❌ فشل الإرسال لـ {failed} مستخدم",
        chat_id=message.chat.id, message_id=status_msg.message_id
    )

@bot.message_handler(func=lambda m: admin_state.get(m.from_user.id) == "waiting_ban")
@safe_handler
def handle_ban(message):
    admin_state.pop(message.from_user.id, None)
    try:
        uid = int(message.text.strip())
        set_ban_status(uid, 1)
        bot.send_message(message.chat.id, f"🚫 تم حظر المستخدم {uid}")
    except ValueError:
        bot.send_message(message.chat.id, "⚠️ آيدي غير صحيح، أرسل رقم فقط")

@bot.message_handler(func=lambda m: admin_state.get(m.from_user.id) == "waiting_unban")
@safe_handler
def handle_unban(message):
    admin_state.pop(message.from_user.id, None)
    try:
        uid = int(message.text.strip())
        set_ban_status(uid, 0)
        bot.send_message(message.chat.id, f"✅ تم فك حظر المستخدم {uid}")
    except ValueError:
        bot.send_message(message.chat.id, "⚠️ آيدي غير صحيح، أرسل رقم فقط")

@bot.message_handler(func=lambda m: admin_state.get(m.from_user.id) == "waiting_channel")
@safe_handler
def handle_set_channel(message):
    admin_state.pop(message.from_user.id, None)
    username = message.text.strip()

    if not username.startswith("@"):
        bot.send_message(message.chat.id, "⚠️ يجب أن يبدأ يوزر القناة بـ @. افتح لوحة الأدمن وحاول مرة أخرى.")
        return

    url = f"https://t.me/{username.lstrip('@')}"

    # تحقق أن البوت فعلاً يقدر يتعامل مع هذه القناة (أي أنه أدمن فيها)
    try:
        bot.get_chat(username)
    except Exception:
        bot.send_message(
            message.chat.id,
            "⚠️ تعذر الوصول لهذه القناة. تأكد أن:\n"
            "1) اليوزر مكتوب صح\n"
            "2) البوت مُضاف كأدمن بالقناة\n\n"
            "ثم افتح لوحة الأدمن وحاول مرة أخرى."
        )
        return

    set_channel(username, url)
    bot.send_message(message.chat.id, f"✅ تم تحديث قناة الاشتراك الإجباري بنجاح إلى {username}")

# ======================================================================
# ============================ إدارة مشتركي VIP ==========================
# ======================================================================
# يتم التعامل مع الطالب بيوزره (وليس آيدي)، لذا يشترط أن الطالب يكون
# ضغط /start مرة واحدة على الأقل حتى يكون يوزره محفوظاً بقاعدة البيانات.

@bot.callback_query_handler(func=lambda call: call.data.startswith("vipmgmt_"))
@safe_handler
def handle_vip_management(call):
    if not is_admin(call.from_user.id):
        bot.answer_callback_query(call.id, "🚫 ليس لديك صلاحية", show_alert=True)
        return

    if call.data == "vipmgmt_add":
        admin_state[call.from_user.id] = "waiting_vip_add"
        bot.send_message(call.message.chat.id, "أرسل يوزر الطالب المراد ترقيته لـ VIP (يبدأ بـ @):")

    elif call.data == "vipmgmt_remove":
        admin_state[call.from_user.id] = "waiting_vip_remove"
        bot.send_message(call.message.chat.id, "أرسل يوزر الطالب المراد إزالته من VIP (يبدأ بـ @):")

    elif call.data == "vipmgmt_list":
        vip_users = get_all_vip_users()
        if not vip_users:
            bot.send_message(call.message.chat.id, "⭐ لا يوجد مشتركين VIP حالياً.")
        else:
            lines = [f"⭐ قائمة مشتركي VIP ({len(vip_users)}):\n"]
            for uid, username, first_name in vip_users:
                uname = f"@{username}" if username else "(بدون يوزر)"
                lines.append(f"  • {uname} — {first_name or ''} ({uid})")
            bot.send_message(call.message.chat.id, "\n".join(lines))

    bot.answer_callback_query(call.id)

@bot.message_handler(func=lambda m: admin_state.get(m.from_user.id) == "waiting_vip_add")
@safe_handler
def handle_vip_add(message):
    admin_state.pop(message.from_user.id, None)
    username = message.text.strip().lstrip('@')
    if not username:
        bot.send_message(message.chat.id, "⚠️ أرسل يوزر صحيح.")
        return

    uid = set_vip_status_by_username(username, 1)
    if uid is None:
        bot.send_message(
            message.chat.id,
            f"⚠️ لم يتم العثور على مستخدم بيوزر @{username}.\n"
            "تأكد أن الطالب ضغط /start بالبوت من قبل، وأن اليوزر مكتوب صح، ثم حاول مرة أخرى."
        )
        return

    bot.send_message(message.chat.id, f"✅ تمت ترقية @{username} إلى VIP بنجاح.")
    try:
        bot.send_message(
            uid,
            "🎉 مبروك! تم تفعيل اشتراك VIP لك بنجاح.\n"
            "صار عندك فتح جميع الفصول وجميع المواد وجميع المميزات بالكامل 🎓"
        )
    except Exception:
        pass

@bot.message_handler(func=lambda m: admin_state.get(m.from_user.id) == "waiting_vip_remove")
@safe_handler
def handle_vip_remove(message):
    admin_state.pop(message.from_user.id, None)
    username = message.text.strip().lstrip('@')
    if not username:
        bot.send_message(message.chat.id, "⚠️ أرسل يوزر صحيح.")
        return

    uid = set_vip_status_by_username(username, 0)
    if uid is None:
        bot.send_message(message.chat.id, f"⚠️ لم يتم العثور على مستخدم بيوزر @{username}.")
        return

    bot.send_message(message.chat.id, f"✅ تم إزالة @{username} من قائمة VIP.")

# ======================================================================
# ==================== إضافة اختبار جديد من لوحة الأدمن =====================
# ======================================================================

@bot.callback_query_handler(func=lambda call: call.data.startswith("addsubj_"))
@safe_handler
def handle_add_test_subject(call):
    if not is_admin(call.from_user.id):
        bot.answer_callback_query(call.id, "🚫 ليس لديك صلاحية", show_alert=True)
        return
    subject = call.data.replace("addsubj_", "")
    bot.edit_message_text("📂 اختر الفصل/القسم:", chat_id=call.message.chat.id, message_id=call.message.message_id,
                           reply_markup=chapter_markup(subject, "addchap_"))
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data.startswith("addchap_"))
@safe_handler
def handle_add_test_chapter(call):
    if not is_admin(call.from_user.id):
        bot.answer_callback_query(call.id, "🚫 ليس لديك صلاحية", show_alert=True)
        return

    category = call.data.replace("addchap_", "")
    add_test_state[call.from_user.id] = {"stage": "waiting_name", "category": category}
    bot.edit_message_text("✏️ أرسل الآن اسم الاختبار:", chat_id=call.message.chat.id, message_id=call.message.message_id)
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data == "addtest_cancel")
@safe_handler
def handle_add_test_cancel(call):
    add_test_state.pop(call.from_user.id, None)
    edit_test_state.pop(call.from_user.id, None)
    bot.edit_message_text("❌ تم إلغاء العملية.", chat_id=call.message.chat.id, message_id=call.message.message_id)
    bot.answer_callback_query(call.id)

@bot.message_handler(func=lambda m: add_test_state.get(m.from_user.id, {}).get("stage") == "waiting_name")
@safe_handler
def handle_add_test_name(message):
    add_test_state[message.from_user.id]["name"] = message.text.strip()
    add_test_state[message.from_user.id]["stage"] = "waiting_url"
    bot.send_message(message.chat.id, "🔗 الآن أرسل رابط الاختبار (يبدأ بـ http:// أو https://):")

@bot.message_handler(func=lambda m: add_test_state.get(m.from_user.id, {}).get("stage") == "waiting_url")
@safe_handler
def handle_add_test_url(message):
    url = message.text.strip()
    if not url.startswith("http"):
        bot.send_message(message.chat.id, "⚠️ الرابط غير صحيح، تأكد إنه يبدأ بـ http:// أو https:// وأرسله مرة أخرى:")
        return

    state = add_test_state.pop(message.from_user.id)
    save_test(state["category"], state["name"], url)
    bot.send_message(
        message.chat.id,
        f"✅ تمت إضافة الاختبار بنجاح!\n\n📌 القسم: {state['category']}\n📝 الاسم: {state['name']}\n🔗 الرابط: {url}"
    )

# ======================================================================
# ========================== تعديل/حذف اختبار ============================
# ======================================================================
# ملاحظة: يمكن تعديل/حذف فقط الاختبارات المضافة من لوحة الأدمن (المخزنة
# بقاعدة البيانات). الاختبارات المكتوبة يدوياً بالكود (قاموس data) تحتاج
# تعديل الكود مباشرة.

@bot.callback_query_handler(func=lambda call: call.data.startswith("editsubj_"))
@safe_handler
def handle_edit_test_subject(call):
    if not is_admin(call.from_user.id):
        bot.answer_callback_query(call.id, "🚫 ليس لديك صلاحية", show_alert=True)
        return
    subject = call.data.replace("editsubj_", "")
    bot.edit_message_text("📂 اختر الفصل/القسم:", chat_id=call.message.chat.id, message_id=call.message.message_id,
                           reply_markup=chapter_markup(subject, "editchap_"))
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data.startswith("editchap_"))
@safe_handler
def handle_edit_test_chapter(call):
    if not is_admin(call.from_user.id):
        bot.answer_callback_query(call.id, "🚫 ليس لديك صلاحية", show_alert=True)
        return

    category = call.data.replace("editchap_", "")
    tests = get_db_tests_full(category)

    if not tests:
        bot.answer_callback_query(call.id, "لا توجد اختبارات مضافة من لوحة الأدمن بهذا القسم ⏳", show_alert=True)
        return

    markup = InlineKeyboardMarkup(row_width=1)
    for t in tests:
        status_emoji = "🟢" if t["is_active"] else "🔴"
        markup.add(InlineKeyboardButton(f"{status_emoji} {t['name']}", callback_data=f"edititem_{t['id']}", style="primary"))
    markup.add(InlineKeyboardButton("❌ إلغاء", callback_data="addtest_cancel", style="danger"))
    bot.edit_message_text("🛠️ اختر الاختبار الذي تريد تعديله أو حذفه:\n(🟢 مفعّل / 🔴 معطّل)",
                           chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=markup)
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data.startswith("edititem_"))
@safe_handler
def handle_edit_test_item(call):
    if not is_admin(call.from_user.id):
        bot.answer_callback_query(call.id, "🚫 ليس لديك صلاحية", show_alert=True)
        return

    test_id = int(call.data.replace("edititem_", ""))
    test = get_test_by_id(test_id)
    if not test:
        bot.answer_callback_query(call.id, "⚠️ هذا الاختبار غير موجود (ربما تم حذفه)", show_alert=True)
        return

    status_text = "🟢 مفعّل" if test["is_active"] else "🔴 معطّل"
    toggle_label = "⏸️ تعطيل الاختبار" if test["is_active"] else "▶️ تفعيل الاختبار"

    markup = InlineKeyboardMarkup(row_width=1)
    markup.add(
        InlineKeyboardButton("✏️ تعديل الاسم والرابط", callback_data=f"editdo_{test_id}", style="success"),
        InlineKeyboardButton(toggle_label, callback_data=f"edittoggle_{test_id}", style="primary"),
        InlineKeyboardButton("🗑️ حذف الاختبار", callback_data=f"editdel_{test_id}", style="danger"),
        InlineKeyboardButton("❌ إلغاء", callback_data="addtest_cancel", style="danger"),
    )
    bot.edit_message_text(
        f"📝 الاسم: {test['name']}\n🔗 الرابط: {test['url']}\nالحالة: {status_text}\n\nاختر الإجراء:",
        chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=markup
    )
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data.startswith("edittoggle_"))
@safe_handler
def handle_edit_test_toggle(call):
    if not is_admin(call.from_user.id):
        bot.answer_callback_query(call.id, "🚫 ليس لديك صلاحية", show_alert=True)
        return

    test_id = int(call.data.replace("edittoggle_", ""))
    test = get_test_by_id(test_id)
    if not test:
        bot.answer_callback_query(call.id, "⚠️ هذا الاختبار غير موجود", show_alert=True)
        return

    new_status = 0 if test["is_active"] else 1
    set_test_active(test_id, new_status)
    test["is_active"] = new_status

    status_text = "🟢 مفعّل" if test["is_active"] else "🔴 معطّل"
    toggle_label = "⏸️ تعطيل الاختبار" if test["is_active"] else "▶️ تفعيل الاختبار"
    markup = InlineKeyboardMarkup(row_width=1)
    markup.add(
        InlineKeyboardButton("✏️ تعديل الاسم والرابط", callback_data=f"editdo_{test_id}", style="success"),
        InlineKeyboardButton(toggle_label, callback_data=f"edittoggle_{test_id}", style="primary"),
        InlineKeyboardButton("🗑️ حذف الاختبار", callback_data=f"editdel_{test_id}", style="danger"),
        InlineKeyboardButton("❌ إلغاء", callback_data="addtest_cancel", style="danger"),
    )
    bot.edit_message_text(
        f"📝 الاسم: {test['name']}\n🔗 الرابط: {test['url']}\nالحالة: {status_text}\n\nاختر الإجراء:",
        chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=markup
    )
    bot.answer_callback_query(call.id, "تم التفعيل ✅" if new_status else "تم التعطيل ⏸️")

@bot.callback_query_handler(func=lambda call: call.data.startswith("editdel_"))
@safe_handler
def handle_edit_test_delete(call):
    if not is_admin(call.from_user.id):
        bot.answer_callback_query(call.id, "🚫 ليس لديك صلاحية", show_alert=True)
        return

    test_id = int(call.data.replace("editdel_", ""))
    test = get_test_by_id(test_id)
    if not test:
        bot.answer_callback_query(call.id, "⚠️ هذا الاختبار غير موجود (ربما تم حذفه مسبقاً)", show_alert=True)
        return

    delete_test(test_id)
    bot.edit_message_text(f"🗑️ تم حذف الاختبار \"{test['name']}\" بنجاح.",
                           chat_id=call.message.chat.id, message_id=call.message.message_id)
    bot.answer_callback_query(call.id, "تم الحذف ✅")

@bot.callback_query_handler(func=lambda call: call.data.startswith("editdo_"))
@safe_handler
def handle_edit_test_start(call):
    if not is_admin(call.from_user.id):
        bot.answer_callback_query(call.id, "🚫 ليس لديك صلاحية", show_alert=True)
        return

    test_id = int(call.data.replace("editdo_", ""))
    test = get_test_by_id(test_id)
    if not test:
        bot.answer_callback_query(call.id, "⚠️ هذا الاختبار غير موجود", show_alert=True)
        return

    edit_test_state[call.from_user.id] = {"stage": "waiting_name", "id": test_id}
    bot.edit_message_text(f"✏️ أرسل الاسم الجديد للاختبار (الحالي: {test['name']}):",
                           chat_id=call.message.chat.id, message_id=call.message.message_id)
    bot.answer_callback_query(call.id)

@bot.message_handler(func=lambda m: edit_test_state.get(m.from_user.id, {}).get("stage") == "waiting_name")
@safe_handler
def handle_edit_test_name(message):
    edit_test_state[message.from_user.id]["name"] = message.text.strip()
    edit_test_state[message.from_user.id]["stage"] = "waiting_url"
    bot.send_message(message.chat.id, "🔗 الآن أرسل الرابط الجديد (يبدأ بـ http:// أو https://):")

@bot.message_handler(func=lambda m: edit_test_state.get(m.from_user.id, {}).get("stage") == "waiting_url")
@safe_handler
def handle_edit_test_url(message):
    url = message.text.strip()
    if not url.startswith("http"):
        bot.send_message(message.chat.id, "⚠️ الرابط غير صحيح، تأكد إنه يبدأ بـ http:// أو https:// وأرسله مرة أخرى:")
        return

    state = edit_test_state.pop(message.from_user.id)
    update_test(state["id"], state["name"], url)
    bot.send_message(
        message.chat.id,
        f"✅ تم تعديل الاختبار بنجاح!\n\n📝 الاسم الجديد: {state['name']}\n🔗 الرابط الجديد: {url}"
    )

# ======================================================================
# ============================ إدارة الأدمنية ============================
# ======================================================================
# متاحة فقط للأدمنية الأساسيين (SUPER_ADMIN_IDS) لمنع أي أدمن مضاف من
# حذف الأدمن الأساسي أو ترقية نفسه.

@bot.callback_query_handler(func=lambda call: call.data.startswith("admmgmt_"))
@safe_handler
def handle_admin_management(call):
    if not is_super_admin(call.from_user.id):
        bot.answer_callback_query(call.id, "🚫 هذه الميزة للأدمن الأساسي فقط", show_alert=True)
        return

    if call.data == "admmgmt_add":
        admin_mgmt_state[call.from_user.id] = "waiting_add_admin"
        bot.send_message(call.message.chat.id, "أرسل آيدي المستخدم (رقم) المراد ترقيته لأدمن:")

    elif call.data == "admmgmt_remove":
        admin_mgmt_state[call.from_user.id] = "waiting_remove_admin"
        bot.send_message(call.message.chat.id, "أرسل آيدي الأدمن (رقم) المراد حذفه:")

    elif call.data == "admmgmt_list":
        bot.send_message(call.message.chat.id, get_all_admins_display())

    bot.answer_callback_query(call.id)

@bot.message_handler(func=lambda m: admin_mgmt_state.get(m.from_user.id) == "waiting_add_admin")
@safe_handler
def handle_add_admin_msg(message):
    admin_mgmt_state.pop(message.from_user.id, None)
    try:
        uid = int(message.text.strip())
        if is_admin(uid):
            bot.send_message(message.chat.id, "⚠️ هذا المستخدم أدمن أصلاً.")
            return
        add_admin_db(uid, message.from_user.id)
        bot.send_message(message.chat.id, f"✅ تمت ترقية {uid} إلى أدمن.")
        try:
            bot.send_message(uid, "🎉 تم ترقيتك إلى أدمن بهذا البوت. استخدم /admin لفتح لوحة التحكم.")
        except Exception:
            pass
    except ValueError:
        bot.send_message(message.chat.id, "⚠️ آيدي غير صحيح، أرسل رقم فقط")

@bot.message_handler(func=lambda m: admin_mgmt_state.get(m.from_user.id) == "waiting_remove_admin")
@safe_handler
def handle_remove_admin_msg(message):
    admin_mgmt_state.pop(message.from_user.id, None)
    try:
        uid = int(message.text.strip())
        if is_super_admin(uid):
            bot.send_message(message.chat.id, "🚫 لا يمكن حذف أدمن أساسي من داخل البوت.")
            return
        if uid not in get_db_admin_ids():
            bot.send_message(message.chat.id, "⚠️ هذا المستخدم ليس أدمن مضاف.")
            return
        remove_admin_db(uid)
        bot.send_message(message.chat.id, f"✅ تم حذف صلاحية الأدمن عن {uid}.")
    except ValueError:
        bot.send_message(message.chat.id, "⚠️ آيدي غير صحيح، أرسل رقم فقط")

# ======================================================================
# ============================== النسخ الاحتياطي ==========================
# ======================================================================

def send_backup_everywhere(caption):
    """يرسل نسخة من قاعدة البيانات لكل الأدمنية الأساسيين + القناة الخاصة
    للنسخ الاحتياطي (لو محددة بـ BACKUP_CHANNEL_ID). هذا أضمن من الاعتماد
    على حساب شخصي واحد فقط."""
    targets = list(SUPER_ADMIN_IDS)
    if BACKUP_CHANNEL_ID:
        targets.append(BACKUP_CHANNEL_ID)

    for target in targets:
        try:
            with open(DB_FILE, 'rb') as f:
                bot.send_document(target, f, caption=caption)
        except FileNotFoundError:
            raise
        except Exception as e:
            logging.exception(f"فشل إرسال نسخة احتياطية إلى {target}: {e}")

@bot.message_handler(commands=['backup'])
@safe_handler
def manual_backup(message):
    if not is_admin(message.from_user.id):
        return
    caption = f"📦 نسخة احتياطية يدوية - {datetime.now().strftime('%Y-%m-%d %H:%M')}"
    try:
        send_backup_everywhere(caption)
        bot.send_message(message.chat.id, "✅ تم إرسال النسخة الاحتياطية.")
    except FileNotFoundError:
        bot.send_message(message.chat.id, "⚠️ لا توجد قاعدة بيانات بعد (لم يدخل أي مستخدم للبوت).")

def auto_backup_loop():
    # ترسل نسخة تلقائية كل 24 ساعة للأدمنية الأساسيين + القناة الخاصة (إن وجدت)
    BACKUP_INTERVAL_SECONDS = 24 * 60 * 60  # غيّر الرقم لو تريد فترة مختلفة (مثلاً 12*60*60 لكل 12 ساعة)
    while True:
        time.sleep(BACKUP_INTERVAL_SECONDS)
        caption = f"📦 نسخة احتياطية تلقائية - {datetime.now().strftime('%Y-%m-%d %H:%M')}"
        try:
            send_backup_everywhere(caption)
        except FileNotFoundError:
            pass  # لا توجد قاعدة بيانات بعد

# ======================================================================
# ======================== قوائم المواد والفصول (للطلاب) ===================
# ======================================================================

@bot.callback_query_handler(func=lambda call: True)
@safe_handler
@rate_limited()
def handle_query(call):
    if is_user_banned(call.from_user.id):
        bot.answer_callback_query(call.id, "🚫 تم حظرك من استخدام هذا البوت.", show_alert=True)
        return

    if not is_subscribed(call.from_user.id):
        bot.answer_callback_query(call.id, "⚠️ يجب الاشتراك بالقناة أولاً!", show_alert=True)
        bot.send_message(call.message.chat.id, "⚠️ يجب الاشتراك بالقناة أولاً لاستخدام البوت:", reply_markup=subscription_markup())
        return

    save_user(call.from_user)  # تحديث آخر ظهور للمستخدم
    markup = InlineKeyboardMarkup(row_width=1)

    # تسجيل فتح المادة لأغراض الإحصائيات (📈 الأكثر استخداماً)
    if call.data in SUBJECT_LABELS:
        log_view("subject", call.data)

    if call.data == "main_menu":
        bot.edit_message_text("اختر المادة:", chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=main_menu())

    elif call.data == "islamic":
        uid = call.from_user.id
        markup.add(InlineKeyboardButton(chapter_label("أحكام التلاوة", "test_isl_ahkam", uid), callback_data="test_isl_ahkam", style="success"))
        for i in range(1, 6):
            cat = f"test_isl_u{i}"
            markup.add(InlineKeyboardButton(chapter_label(f"الوحدة {i}", cat, uid), callback_data=cat, style="success"))
        markup.add(back_btn("main_menu"))
        bot.edit_message_text("🕋 التربية الإسلامية - اختر القسم:", chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=markup)

    elif call.data == "arabic":
        uid = call.from_user.id
        markup.add(
            InlineKeyboardButton(chapter_label("القواعد", "test_ar_grammar", uid), callback_data="test_ar_grammar", style="success"),
            InlineKeyboardButton(chapter_label("الأدب والنصوص", "test_ar_literature", uid), callback_data="test_ar_literature", style="success")
        )
        markup.add(back_btn("main_menu"))
        bot.edit_message_text("📝 اللغة العربية - اختر القسم:", chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=markup)

    elif call.data == "english":
        uid = call.from_user.id
        markup.add(
            InlineKeyboardButton(chapter_label("القواعد (Grammar)", "test_en_grammar", uid), callback_data="test_en_grammar", style="success"),
            InlineKeyboardButton(chapter_label("قطع الكتاب (Textbook)", "test_en_passages", uid), callback_data="test_en_passages", style="success"),
            InlineKeyboardButton(chapter_label("الأدب (Literature)", "test_en_literature", uid), callback_data="test_en_literature", style="success")
        )
        markup.add(back_btn("main_menu"))
        bot.edit_message_text("🔠 اللغة الإنكليزية - اختر القسم:", chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=markup)

    elif call.data == "math":
        uid = call.from_user.id
        for i in range(1, 6):
            cat = f"test_math_ch{i}"
            markup.add(InlineKeyboardButton(chapter_label(f"الفصل {i}", cat, uid), callback_data=cat, style="success"))
        markup.add(back_btn("main_menu"))
        bot.edit_message_text("🔢 الرياضيات - اختر الفصل:", chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=markup)

    elif call.data == "biology":
        uid = call.from_user.id
        for i in range(1, 6):
            cat = f"test_bio_ch{i}"
            markup.add(InlineKeyboardButton(chapter_label(f"الفصل {i}", cat, uid), callback_data=cat, style="success"))
        markup.add(back_btn("main_menu"))
        bot.edit_message_text("🧬 الأحياء - اختر الفصل:", chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=markup)

    elif call.data == "physics":
        uid = call.from_user.id
        for i in range(1, 11):
            cat = f"test_phy_ch{i}"
            markup.add(InlineKeyboardButton(chapter_label(f"الفصل {i}", cat, uid), callback_data=cat, style="success"))
        markup.add(back_btn("main_menu"))
        bot.edit_message_text("⚡ الفيزياء - اختر الفصل:", chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=markup)

    elif call.data == "chemistry":
        uid = call.from_user.id
        for i in range(1, 9):
            cat = f"test_chem_ch{i}"
            markup.add(InlineKeyboardButton(chapter_label(f"الفصل {i}", cat, uid), callback_data=cat, style="success"))
        markup.add(back_btn("main_menu"))
        bot.edit_message_text("🧪 الكيمياء - اختر الفصل:", chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=markup)

    elif call.data.startswith("test_"):
        category = call.data

        # تحديد زر الرجوع أولاً (قبل التحقق من VIP) عشان يكون جاهز بكلتا الحالتين
        if "isl" in category:
            b_target = "islamic"
        elif "ar_" in category:
            b_target = "arabic"
        elif "en_" in category:
            b_target = "english"
        elif "math" in category:
            b_target = "math"
        elif "bio" in category:
            b_target = "biology"
        elif "phy" in category:
            b_target = "physics"
        elif "chem" in category:
            b_target = "chemistry"
        else:
            b_target = "main_menu"

        # القسم حصري VIP والطالب مو مشترك: نعرض رسالة الترقية بدل الاختبارات
        if not is_category_free(category) and not is_vip(call.from_user.id):
            bot.edit_message_text(
                VIP_UPSELL_TEXT,
                chat_id=call.message.chat.id, message_id=call.message.message_id,
                reply_markup=vip_upsell_markup(b_target)
            )
            return

        tests = get_display_tests(category)

        if not tests:
            bot.answer_callback_query(call.id, "عذراً، لم يتم إضافة اختبارات لهذا القسم بعد ⏳", show_alert=True)
            return

        # تسجيل فتح الفصل/القسم لأغراض الإحصائيات (📈 الأكثر استخداماً)
        log_view("chapter", category)

        for test in tests:
            markup.add(InlineKeyboardButton(test["name"], url=test["url"], style="primary"))

        markup.add(back_btn(b_target))
        bot.edit_message_text("📚 اختر موضوع الاختبار للانتقال للموقع:", chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=markup)

# ======================================================================
# ================================ تشغيل البوت =============================
# ======================================================================

backup_thread = threading.Thread(target=auto_backup_loop, daemon=True)
backup_thread.start()

print("البوت يعمل الآن...")

try:
    bot.infinity_polling()
except Exception as e:
    logging.exception(f"توقف البوت بسبب خطأ غير متوقع: {e}")
