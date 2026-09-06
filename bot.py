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
CHANNEL_USERNAME = "@Q4_92"          # يوزر القناة (لازم يبدأ بـ @)
CHANNEL_URL = "https://t.me/Q4_92"   # رابط القناة للزر

# ============== النسخ الاحتياطي على قناة خاصة ==============
BACKUP_CHANNEL_ID = None

# ============== إعدادات الحماية من الضغط السريع (Rate Limiting) ==============
RATE_LIMIT_SECONDS = 0.6
RATE_LIMIT_STRIKES = 3
RATE_LIMIT_LOCK_SECONDS = 3

DB_FILE = "users.db"

# ======================================================================
# ========================= تسجيل الأخطاء (Logging) =====================
# ======================================================================

logging.basicConfig(
    filename="bot_errors.log",
    level=logging.ERROR,
    format="%(asctime)s - %(levelname)s - %(message)s",
    encoding="utf-8"
)

def notify_super_admins_of_error(context, error):
    for admin_id in SUPER_ADMIN_IDS:
        try:
            bot.send_message(admin_id, f"⚠️ حدث خطأ في البوت ({context}):\n{error}")
        except Exception:
            pass

def safe_handler(func):
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

_last_action_time = {}
_rapid_press_count = {}
_lock_until = {}

def rate_limited(seconds=RATE_LIMIT_SECONDS):
    def decorator(func):
        def wrapper(update_obj, *args, **kwargs):
            uid = update_obj.from_user.id
            if is_admin(uid):
                return func(update_obj, *args, **kwargs)

            now = time.time()

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
                count = _rapid_press_count.get(uid, 0) + 1
                _rapid_press_count[uid] = count
                _last_action_time[uid] = now

                if count >= RATE_LIMIT_STRIKES:
                    _lock_until[uid] = now + RATE_LIMIT_LOCK_SECONDS
                    _rapid_press_count[uid] = 0
                    try:
                        bot.answer_callback_query(update_obj.id, f"🚦 لا تضغط بسرعة، انتظر {RATE_LIMIT_LOCK_SECONDS} ثواني حتى لا يتوقف البوت", show_alert=True)
                    except Exception:
                        pass
                else:
                    try:
                        bot.answer_callback_query(update_obj.id)
                    except Exception:
                        pass
                return

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
    # عمود "category" هنا أصبح يُستخدم كـ "location" عام: يقبل الآن ثلاثة أشكال:
    #   - فئة فصل/وحدة عادية (كما كانت، مثال: test_math_ch1)
    #   - "subj_<subject_key>" يعني: داخل القائمة الرئيسية للمادة (بجانب الوحدات)
    #   - "box_<box_id>" يعني: داخل خانة أخرى (تعشيش)
    c.execute('''CREATE TABLE IF NOT EXISTS tests (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        category TEXT,
        name TEXT,
        url TEXT
    )''')
    # جدول "الخانات" — عناصر تشبه المجلدات: تُضاف بأي مكان (مادة/فصل/خانة أخرى)
    # ولما الطالب يضغط عليها تفتح له شاشة فرعية فاضية تحتوي اختبارات أو خانات أخرى
    c.execute('''CREATE TABLE IF NOT EXISTS boxes (
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
    c.execute('''CREATE TABLE IF NOT EXISTS view_counts (
        scope TEXT,
        key TEXT,
        count INTEGER DEFAULT 0,
        PRIMARY KEY (scope, key)
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS short_link_cache (
        long_url TEXT PRIMARY KEY,
        short_url TEXT
    )''')
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
        "ALTER TABLE boxes ADD COLUMN is_active INTEGER DEFAULT 1",
        "ALTER TABLE boxes ADD COLUMN short_url TEXT",
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
    return get_setting("channel_username", CHANNEL_USERNAME)

def get_channel_url():
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

def is_vip(user_id):
    if is_admin(user_id):
        return True
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

# ---------- دوال عامة مشتركة (تُستخدم للاختبارات والخانات معاً) ----------
# نفس المنطق يتكرر لجدولين (tests و boxes)، فبدل تكرار الكود، هذه دوال
# عامة تأخذ اسم الجدول كمعامل وتشتغل على أي منهما.

def _save_item(table, category, name, url):
    short = shorten_url(url)
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute(
        f"INSERT INTO {table} (category, name, url, short_url, is_active) VALUES (?,?,?,?,1)",
        (category, name, url, short)
    )
    conn.commit()
    conn.close()

def _get_db_items(table, category):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute(f"SELECT id, name, url, short_url FROM {table} WHERE category=? AND is_active=1 ORDER BY id", (category,))
    rows = [{"id": r[0], "name": r[1], "url": (r[3] or r[2])} for r in c.fetchall()]
    conn.close()
    return rows

def _get_db_items_full(table, category):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute(f"SELECT id, name, url, short_url, is_active FROM {table} WHERE category=? ORDER BY id", (category,))
    rows = [{"id": r[0], "name": r[1], "url": r[2], "short_url": r[3], "is_active": r[4]} for r in c.fetchall()]
    conn.close()
    return rows

def _get_all_items_full(table):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute(f"SELECT id, category, name, url, short_url, is_active FROM {table} ORDER BY id")
    rows = [{"id": r[0], "category": r[1], "name": r[2], "url": r[3], "short_url": r[4], "is_active": r[5]} for r in c.fetchall()]
    conn.close()
    return rows

def _get_item_by_id(table, item_id):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute(f"SELECT id, category, name, url, short_url, is_active FROM {table} WHERE id=?", (item_id,))
    row = c.fetchone()
    conn.close()
    if row:
        return {"id": row[0], "category": row[1], "name": row[2], "url": row[3], "short_url": row[4], "is_active": row[5]}
    return None

def _update_item(table, item_id, name, url):
    short = shorten_url(url)
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute(f"UPDATE {table} SET name=?, url=?, short_url=? WHERE id=?", (name, url, short, item_id))
    conn.commit()
    conn.close()

def _set_item_active(table, item_id, status):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute(f"UPDATE {table} SET is_active=? WHERE id=?", (status, item_id))
    conn.commit()
    conn.close()

def _delete_item(table, item_id):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute(f"DELETE FROM {table} WHERE id=?", (item_id,))
    conn.commit()
    conn.close()

# ---------- الاختبارات (Tests) — تظهر للطالب بلون أزرق، وتفتح رابط خارجي ----------

def save_test(category, name, url):
    _save_item("tests", category, name, url)

def get_db_tests(category):
    return _get_db_items("tests", category)

def get_db_tests_full(category):
    return _get_db_items_full("tests", category)

def get_test_by_id(test_id):
    return _get_item_by_id("tests", test_id)

def update_test(test_id, name, url):
    _update_item("tests", test_id, name, url)

def set_test_active(test_id, status):
    _set_item_active("tests", test_id, status)

def delete_test(test_id):
    _delete_item("tests", test_id)

def get_display_tests(category):
    """الاختبارات النهائية اللي تنعرض للطالب (من لوحة الأدمن فقط الآن)."""
    return get_db_tests(category)

# ---------- الخانات (Boxes) — تظهر للطالب بلون أخضر، وتفتح شاشة فرعية (مو رابط) ----------
# الخانة ليس لها رابط؛ عمود url يبقى فارغاً دوماً. عمود category هنا يُستخدم
# كـ "موقع" الخانة (أين تظهر): داخل مادة / داخل فصل / داخل خانة أخرى.

def save_box(location, name):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute(
        "INSERT INTO boxes (category, name, url, short_url, is_active) VALUES (?,?,?,?,1)",
        (location, name, None, None)
    )
    conn.commit()
    conn.close()

def get_db_boxes(location):
    return _get_db_items("boxes", location)

def get_db_boxes_full(location):
    return _get_db_items_full("boxes", location)

def get_all_boxes_full():
    return _get_all_items_full("boxes")

def get_box_by_id(box_id):
    return _get_item_by_id("boxes", box_id)

def update_box(box_id, name):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("UPDATE boxes SET name=? WHERE id=?", (name, box_id))
    conn.commit()
    conn.close()

def set_box_active(box_id, status):
    _set_item_active("boxes", box_id, status)

def delete_box(box_id):
    _delete_item("boxes", box_id)

init_db()
migrate_db()

# ======================================================================
# ============================ حالات الذاكرة المؤقتة ======================
# ======================================================================

admin_state = {}          # حالة عامة: بث / حظر / فك حظر / تغيير قناة الاشتراك
add_test_state = {}       # حالة إضافة اختبار جديد
edit_test_state = {}      # حالة تعديل اختبار
add_box_state = {}        # حالة إضافة خانة جديدة
edit_box_state = {}       # حالة تعديل خانة
admin_mgmt_state = {}     # حالة إدارة الأدمنية

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

SUBJECT_LABELS = dict(SUBJECTS)

def build_category_labels():
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
# ==================== دوال مساعدة عامة للمواقع (Locations) ================
# ======================================================================
# "الموقع" (location) الآن قد يكون:
#   - فئة فصل عادية (مثال: test_math_ch1)
#   - "subj_<subject_key>"  → داخل القائمة الرئيسية لمادة معينة
#   - "box_<box_id>"        → داخل خانة أخرى (تعشيش)

def subject_for_category(category):
    """يحدد المادة التي تتبعها فئة فصل عادية (يُستخدم لزر الرجوع)."""
    if "isl" in category:
        return "islamic"
    elif "ar_" in category:
        return "arabic"
    elif "en_" in category:
        return "english"
    elif "math" in category:
        return "math"
    elif "bio" in category:
        return "biology"
    elif "phy" in category:
        return "physics"
    elif "chem" in category:
        return "chemistry"
    else:
        return "main_menu"

def back_target_for_location(location):
    """يحدد الـ callback_data المناسب لزر الرجوع اعتماداً على نوع الموقع."""
    if location.startswith("subj_"):
        return location[len("subj_"):]
    elif location.startswith("box_"):
        return location  # سيفتح الخانة الأب مباشرة (نفس معالج box_)
    else:
        return subject_for_category(location)

def location_label(location):
    """وصف نصي مقروء لموقع خانة أو اختبار، يُستخدم بلوحة الأدمن فقط."""
    if location.startswith("subj_"):
        key = location[len("subj_"):]
        return f"{SUBJECT_LABELS.get(key, key)} (القائمة الرئيسية)"
    elif location.startswith("box_"):
        try:
            parent_id = int(location[len("box_"):])
        except ValueError:
            parent_id = None
        parent = get_box_by_id(parent_id) if parent_id else None
        if parent:
            return f"📦 داخل خانة: {parent['name']}"
        return "📦 داخل خانة محذوفة"
    else:
        return CATEGORY_LABELS.get(location, location)

def build_box_picker_markup(callback_prefix, cancel_data="addtest_cancel", only_active=True):
    """يبني قائمة بكل الخانات الموجودة (لاختيار خانة أب/حاضنة). يرجع None لو ما فيه خانات."""
    boxes = get_all_boxes_full()
    if only_active:
        boxes = [b for b in boxes if b["is_active"]]
    if not boxes:
        return None
    markup = InlineKeyboardMarkup(row_width=1)
    for b in boxes:
        markup.add(InlineKeyboardButton(f"📦 {b['name']} ({location_label(b['category'])})",
                                         callback_data=f"{callback_prefix}{b['id']}", style="success"))
    markup.add(InlineKeyboardButton("❌ إلغاء", callback_data=cancel_data, style="danger"))
    return markup

# ======================================================================
# ============================== نظام VIP ================================
# ======================================================================

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
    if is_category_free(category) or is_vip(user_id):
        return label
    return f"🔒 {label}"

# ======================================================================
# ============================ بيانات الاختبارات ==========================
# ======================================================================
# ⚠️ كل الاختبارات والخانات تُضاف حصرياً من لوحة الأدمن وتُحفظ بقاعدة البيانات.

data = {}

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
        InlineKeyboardButton("➕ إضافة خانة", callback_data="admin_add_box", style="success"),
        InlineKeyboardButton("✏️ تعديل/حذف خانة", callback_data="admin_edit_box", style="success"),
        InlineKeyboardButton("📡 تغيير قناة الاشتراك", callback_data="admin_set_channel", style="primary"),
        InlineKeyboardButton("⭐ إدارة VIP", callback_data="admin_manage_vip", style="success"),
    )
    if is_super_admin(user_id):
        markup.add(InlineKeyboardButton("👑 إدارة الأدمنية", callback_data="admin_manage_admins", style="danger"))
    return markup

def subject_markup(callback_prefix, include_cancel=True):
    markup = InlineKeyboardMarkup(row_width=2)
    for key, label in SUBJECTS:
        markup.add(InlineKeyboardButton(label, callback_data=f"{callback_prefix}{key}", style="primary"))
    if include_cancel:
        markup.add(InlineKeyboardButton("❌ إلغاء", callback_data="addtest_cancel", style="danger"))
    return markup

def chapter_markup(subject, callback_prefix, include_cancel=True):
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

def append_subject_boxes(markup, subject_key):
    """يضيف بآخر القائمة الرئيسية لمادة معينة أي خانات (📦) مربوطة بها مباشرة."""
    boxes = get_db_boxes(f"subj_{subject_key}")
    for b in boxes:
        markup.add(InlineKeyboardButton(b['name'], callback_data=f"box_{b['id']}", style="success"))

# ======================================================================
# ========================= أوامر المستخدم العادي =========================
# ======================================================================

def is_subscribed(user_id):
    try:
        member = bot.get_chat_member(get_channel_username(), user_id)
        return member.status not in ["left", "kicked"]
    except Exception:
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
        markup = InlineKeyboardMarkup(row_width=1)
        markup.add(
            InlineKeyboardButton("📂 داخل وحدة/فصل", callback_data="addtestloc_chapter", style="primary"),
            InlineKeyboardButton("📦 داخل خانة موجودة", callback_data="addtestloc_box", style="success"),
            InlineKeyboardButton("❌ إلغاء", callback_data="addtest_cancel", style="danger"),
        )
        bot.send_message(call.message.chat.id, "أين تريد إضافة الاختبار؟", reply_markup=markup)

    elif call.data == "admin_edit_test":
        markup = InlineKeyboardMarkup(row_width=1)
        markup.add(
            InlineKeyboardButton("📂 حسب الوحدة/الفصل", callback_data="edittestloc_chapter", style="primary"),
            InlineKeyboardButton("📦 داخل خانة", callback_data="edittestloc_box", style="success"),
            InlineKeyboardButton("❌ إلغاء", callback_data="addtest_cancel", style="danger"),
        )
        bot.send_message(call.message.chat.id, "من أين تريد تعديل/حذف الاختبار؟", reply_markup=markup)

    elif call.data == "admin_add_box":
        markup = InlineKeyboardMarkup(row_width=1)
        markup.add(
            InlineKeyboardButton("📚 داخل مادة (بجانب الوحدات)", callback_data="addboxloc_subject", style="primary"),
            InlineKeyboardButton("📂 داخل وحدة/فصل", callback_data="addboxloc_chapter", style="success"),
            InlineKeyboardButton("📦 داخل خانة موجودة", callback_data="addboxloc_box", style="success"),
            InlineKeyboardButton("❌ إلغاء", callback_data="addtest_cancel", style="danger"),
        )
        bot.send_message(call.message.chat.id, "أين تريد إضافة الخانة؟", reply_markup=markup)

    elif call.data == "admin_edit_box":
        boxes = get_all_boxes_full()
        if not boxes:
            bot.send_message(call.message.chat.id, "لا توجد أي خانات مضافة بعد ⏳")
        else:
            markup = InlineKeyboardMarkup(row_width=1)
            for b in boxes:
                status_emoji = "🟢" if b["is_active"] else "🔴"
                markup.add(InlineKeyboardButton(f"{status_emoji} {b['name']} — {location_label(b['category'])}",
                                                 callback_data=f"editboxitem_{b['id']}", style="success"))
            markup.add(InlineKeyboardButton("❌ إلغاء", callback_data="addtest_cancel", style="danger"))
            bot.send_message(call.message.chat.id, "🛠️ اختر الخانة التي تريد تعديلها أو حذفها:\n(🟢 مفعّلة / 🔴 معطّلة)",
                              reply_markup=markup)

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

@bot.callback_query_handler(func=lambda call: call.data == "addtestloc_chapter")
@safe_handler
def handle_addtestloc_chapter(call):
    if not is_admin(call.from_user.id):
        bot.answer_callback_query(call.id, "🚫 ليس لديك صلاحية", show_alert=True)
        return
    bot.edit_message_text("📚 اختر المادة التي تريد إضافة اختبار لها:", chat_id=call.message.chat.id,
                           message_id=call.message.message_id, reply_markup=subject_markup("addsubj_"))
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data == "addtestloc_box")
@safe_handler
def handle_addtestloc_box(call):
    if not is_admin(call.from_user.id):
        bot.answer_callback_query(call.id, "🚫 ليس لديك صلاحية", show_alert=True)
        return
    markup = build_box_picker_markup("addtestparentbox_")
    if markup is None:
        bot.answer_callback_query(call.id, "⚠️ لا توجد أي خانات بعد. أنشئ خانة أولاً.", show_alert=True)
        return
    bot.edit_message_text("📦 اختر الخانة التي تريد إضافة الاختبار بداخلها:", chat_id=call.message.chat.id,
                           message_id=call.message.message_id, reply_markup=markup)
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data.startswith("addtestparentbox_"))
@safe_handler
def handle_add_test_parent_box(call):
    if not is_admin(call.from_user.id):
        bot.answer_callback_query(call.id, "🚫 ليس لديك صلاحية", show_alert=True)
        return
    box_id = int(call.data.replace("addtestparentbox_", ""))
    add_test_state[call.from_user.id] = {"stage": "waiting_name", "category": f"box_{box_id}"}
    bot.edit_message_text("✏️ أرسل الآن اسم الاختبار:", chat_id=call.message.chat.id, message_id=call.message.message_id)
    bot.answer_callback_query(call.id)

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
    add_box_state.pop(call.from_user.id, None)
    edit_box_state.pop(call.from_user.id, None)
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
        f"✅ تمت إضافة الاختبار بنجاح!\n\n📍 الموقع: {location_label(state['category'])}\n📝 الاسم: {state['name']}\n🔗 الرابط: {url}"
    )

# ======================================================================
# ========================== تعديل/حذف اختبار ============================
# ======================================================================

def render_tests_management_list(category, chat_id, message_id):
    """يعرض قائمة الاختبارات الموجودة بفئة/موقع معين لتعديلها أو حذفها. يرجع True لو فيه اختبارات."""
    tests = get_db_tests_full(category)
    if not tests:
        return False
    markup = InlineKeyboardMarkup(row_width=1)
    for t in tests:
        status_emoji = "🟢" if t["is_active"] else "🔴"
        markup.add(InlineKeyboardButton(f"{status_emoji} {t['name']}", callback_data=f"edititem_{t['id']}", style="primary"))
    markup.add(InlineKeyboardButton("❌ إلغاء", callback_data="addtest_cancel", style="danger"))
    bot.edit_message_text("🛠️ اختر الاختبار الذي تريد تعديله أو حذفه:\n(🟢 مفعّل / 🔴 معطّل)",
                           chat_id=chat_id, message_id=message_id, reply_markup=markup)
    return True

@bot.callback_query_handler(func=lambda call: call.data == "edittestloc_chapter")
@safe_handler
def handle_edittestloc_chapter(call):
    if not is_admin(call.from_user.id):
        bot.answer_callback_query(call.id, "🚫 ليس لديك صلاحية", show_alert=True)
        return
    bot.edit_message_text("📚 اختر المادة التي تريد تعديل/حذف اختبار منها:", chat_id=call.message.chat.id,
                           message_id=call.message.message_id, reply_markup=subject_markup("editsubj_"))
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data == "edittestloc_box")
@safe_handler
def handle_edittestloc_box(call):
    if not is_admin(call.from_user.id):
        bot.answer_callback_query(call.id, "🚫 ليس لديك صلاحية", show_alert=True)
        return
    markup = build_box_picker_markup("editboxtestsparent_")
    if markup is None:
        bot.answer_callback_query(call.id, "⚠️ لا توجد أي خانات بعد.", show_alert=True)
        return
    bot.edit_message_text("📦 اختر الخانة التي تريد تعديل/حذف اختبار من داخلها:", chat_id=call.message.chat.id,
                           message_id=call.message.message_id, reply_markup=markup)
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data.startswith("editboxtestsparent_"))
@safe_handler
def handle_edit_test_box_parent(call):
    if not is_admin(call.from_user.id):
        bot.answer_callback_query(call.id, "🚫 ليس لديك صلاحية", show_alert=True)
        return
    box_id = int(call.data.replace("editboxtestsparent_", ""))
    category = f"box_{box_id}"
    ok = render_tests_management_list(category, call.message.chat.id, call.message.message_id)
    if not ok:
        bot.answer_callback_query(call.id, "لا توجد اختبارات داخل هذه الخانة بعد ⏳", show_alert=True)
        return
    bot.answer_callback_query(call.id)

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
    ok = render_tests_management_list(category, call.message.chat.id, call.message.message_id)
    if not ok:
        bot.answer_callback_query(call.id, "لا توجد اختبارات مضافة من لوحة الأدمن بهذا القسم ⏳", show_alert=True)
        return
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
        f"📝 الاسم: {test['name']}\n🔗 الرابط: {test['url']}\n📍 الموقع: {location_label(test['category'])}\nالحالة: {status_text}\n\nاختر الإجراء:",
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
        f"📝 الاسم: {test['name']}\n🔗 الرابط: {test['url']}\n📍 الموقع: {location_label(test['category'])}\nالحالة: {status_text}\n\nاختر الإجراء:",
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
# ==================== إضافة خانة جديدة من لوحة الأدمن (أخضر) ================
# ======================================================================
# الخانة الآن مجرد زر يفتح شاشة فرعية فاضية (بدون رابط)، وتُبنى بأي موقع:
# داخل مادة (بجانب الوحدات) / داخل فصل أو وحدة / داخل خانة أخرى (تعشيش).

@bot.callback_query_handler(func=lambda call: call.data == "addboxloc_subject")
@safe_handler
def handle_addboxloc_subject(call):
    if not is_admin(call.from_user.id):
        bot.answer_callback_query(call.id, "🚫 ليس لديك صلاحية", show_alert=True)
        return
    bot.edit_message_text("📚 اختر المادة (ستظهر الخانة بجانب الوحدات مباشرة):", chat_id=call.message.chat.id,
                           message_id=call.message.message_id, reply_markup=subject_markup("addboxsubjmain_"))
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data.startswith("addboxsubjmain_"))
@safe_handler
def handle_add_box_subject_main(call):
    if not is_admin(call.from_user.id):
        bot.answer_callback_query(call.id, "🚫 ليس لديك صلاحية", show_alert=True)
        return
    subject = call.data.replace("addboxsubjmain_", "")
    add_box_state[call.from_user.id] = {"stage": "waiting_name", "category": f"subj_{subject}"}
    bot.edit_message_text("✏️ أرسل الآن اسم الخانة:", chat_id=call.message.chat.id, message_id=call.message.message_id)
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data == "addboxloc_chapter")
@safe_handler
def handle_addboxloc_chapter(call):
    if not is_admin(call.from_user.id):
        bot.answer_callback_query(call.id, "🚫 ليس لديك صلاحية", show_alert=True)
        return
    bot.edit_message_text("📚 اختر المادة التي تريد إضافة خانة لها:", chat_id=call.message.chat.id,
                           message_id=call.message.message_id, reply_markup=subject_markup("addboxsubj_"))
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data == "addboxloc_box")
@safe_handler
def handle_addboxloc_box(call):
    if not is_admin(call.from_user.id):
        bot.answer_callback_query(call.id, "🚫 ليس لديك صلاحية", show_alert=True)
        return
    markup = build_box_picker_markup("addboxparentbox_")
    if markup is None:
        bot.answer_callback_query(call.id, "⚠️ لا توجد أي خانات بعد. أنشئ خانة أولاً من خيار آخر.", show_alert=True)
        return
    bot.edit_message_text("📦 اختر الخانة الأب التي تريد إضافة الخانة الجديدة بداخلها:", chat_id=call.message.chat.id,
                           message_id=call.message.message_id, reply_markup=markup)
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data.startswith("addboxparentbox_"))
@safe_handler
def handle_add_box_parent_box(call):
    if not is_admin(call.from_user.id):
        bot.answer_callback_query(call.id, "🚫 ليس لديك صلاحية", show_alert=True)
        return
    parent_id = int(call.data.replace("addboxparentbox_", ""))
    add_box_state[call.from_user.id] = {"stage": "waiting_name", "category": f"box_{parent_id}"}
    bot.edit_message_text("✏️ أرسل الآن اسم الخانة:", chat_id=call.message.chat.id, message_id=call.message.message_id)
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data.startswith("addboxsubj_"))
@safe_handler
def handle_add_box_subject(call):
    if not is_admin(call.from_user.id):
        bot.answer_callback_query(call.id, "🚫 ليس لديك صلاحية", show_alert=True)
        return
    subject = call.data.replace("addboxsubj_", "")
    bot.edit_message_text("📂 اختر الفصل/القسم الذي تريد إضافة الخانة فيه:", chat_id=call.message.chat.id, message_id=call.message.message_id,
                           reply_markup=chapter_markup(subject, "addboxchap_"))
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data.startswith("addboxchap_"))
@safe_handler
def handle_add_box_chapter(call):
    if not is_admin(call.from_user.id):
        bot.answer_callback_query(call.id, "🚫 ليس لديك صلاحية", show_alert=True)
        return

    category = call.data.replace("addboxchap_", "")
    add_box_state[call.from_user.id] = {"stage": "waiting_name", "category": category}
    bot.edit_message_text("✏️ أرسل الآن اسم الخانة:", chat_id=call.message.chat.id, message_id=call.message.message_id)
    bot.answer_callback_query(call.id)

@bot.message_handler(func=lambda m: add_box_state.get(m.from_user.id, {}).get("stage") == "waiting_name")
@safe_handler
def handle_add_box_name(message):
    state = add_box_state.pop(message.from_user.id)
    name = message.text.strip()
    save_box(state["category"], name)
    bot.send_message(
        message.chat.id,
        f"✅ تمت إضافة الخانة بنجاح! (ستظهر للطلاب كزر أخضر يفتح شاشة فرعية)\n\n"
        f"📍 الموقع: {location_label(state['category'])}\n📝 الاسم: {name}"
    )

# ======================================================================
# ============================ تعديل/حذف خانة ============================
# ======================================================================

@bot.callback_query_handler(func=lambda call: call.data.startswith("editboxitem_"))
@safe_handler
def handle_edit_box_item(call):
    if not is_admin(call.from_user.id):
        bot.answer_callback_query(call.id, "🚫 ليس لديك صلاحية", show_alert=True)
        return

    box_id = int(call.data.replace("editboxitem_", ""))
    box = get_box_by_id(box_id)
    if not box:
        bot.answer_callback_query(call.id, "⚠️ هذه الخانة غير موجودة (ربما تم حذفها)", show_alert=True)
        return

    status_text = "🟢 مفعّلة" if box["is_active"] else "🔴 معطّلة"
    toggle_label = "⏸️ تعطيل الخانة" if box["is_active"] else "▶️ تفعيل الخانة"

    markup = InlineKeyboardMarkup(row_width=1)
    markup.add(
        InlineKeyboardButton("✏️ تعديل الاسم", callback_data=f"editboxdo_{box_id}", style="success"),
        InlineKeyboardButton(toggle_label, callback_data=f"editboxtoggle_{box_id}", style="primary"),
        InlineKeyboardButton("🗑️ حذف الخانة", callback_data=f"editboxdel_{box_id}", style="danger"),
        InlineKeyboardButton("❌ إلغاء", callback_data="addtest_cancel", style="danger"),
    )
    bot.edit_message_text(
        f"📝 الاسم: {box['name']}\n📍 الموقع: {location_label(box['category'])}\nالحالة: {status_text}\n\nاختر الإجراء:",
        chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=markup
    )
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data.startswith("editboxtoggle_"))
@safe_handler
def handle_edit_box_toggle(call):
    if not is_admin(call.from_user.id):
        bot.answer_callback_query(call.id, "🚫 ليس لديك صلاحية", show_alert=True)
        return

    box_id = int(call.data.replace("editboxtoggle_", ""))
    box = get_box_by_id(box_id)
    if not box:
        bot.answer_callback_query(call.id, "⚠️ هذه الخانة غير موجودة", show_alert=True)
        return

    new_status = 0 if box["is_active"] else 1
    set_box_active(box_id, new_status)
    box["is_active"] = new_status

    status_text = "🟢 مفعّلة" if box["is_active"] else "🔴 معطّلة"
    toggle_label = "⏸️ تعطيل الخانة" if box["is_active"] else "▶️ تفعيل الخانة"
    markup = InlineKeyboardMarkup(row_width=1)
    markup.add(
        InlineKeyboardButton("✏️ تعديل الاسم", callback_data=f"editboxdo_{box_id}", style="success"),
        InlineKeyboardButton(toggle_label, callback_data=f"editboxtoggle_{box_id}", style="primary"),
        InlineKeyboardButton("🗑️ حذف الخانة", callback_data=f"editboxdel_{box_id}", style="danger"),
        InlineKeyboardButton("❌ إلغاء", callback_data="addtest_cancel", style="danger"),
    )
    bot.edit_message_text(
        f"📝 الاسم: {box['name']}\n📍 الموقع: {location_label(box['category'])}\nالحالة: {status_text}\n\nاختر الإجراء:",
        chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=markup
    )
    bot.answer_callback_query(call.id, "تم التفعيل ✅" if new_status else "تم التعطيل ⏸️")

@bot.callback_query_handler(func=lambda call: call.data.startswith("editboxdel_"))
@safe_handler
def handle_edit_box_delete(call):
    if not is_admin(call.from_user.id):
        bot.answer_callback_query(call.id, "🚫 ليس لديك صلاحية", show_alert=True)
        return

    box_id = int(call.data.replace("editboxdel_", ""))
    box = get_box_by_id(box_id)
    if not box:
        bot.answer_callback_query(call.id, "⚠️ هذه الخانة غير موجودة (ربما تم حذفها مسبقاً)", show_alert=True)
        return

    delete_box(box_id)
    bot.edit_message_text(
        f"🗑️ تم حذف الخانة \"{box['name']}\" بنجاح.\n"
        "⚠️ ملاحظة: أي اختبارات أو خانات فرعية كانت بداخلها تبقى موجودة بقاعدة البيانات لكنها لن تظهر لأي طالب.",
        chat_id=call.message.chat.id, message_id=call.message.message_id)
    bot.answer_callback_query(call.id, "تم الحذف ✅")

@bot.callback_query_handler(func=lambda call: call.data.startswith("editboxdo_"))
@safe_handler
def handle_edit_box_start(call):
    if not is_admin(call.from_user.id):
        bot.answer_callback_query(call.id, "🚫 ليس لديك صلاحية", show_alert=True)
        return

    box_id = int(call.data.replace("editboxdo_", ""))
    box = get_box_by_id(box_id)
    if not box:
        bot.answer_callback_query(call.id, "⚠️ هذه الخانة غير موجودة", show_alert=True)
        return

    edit_box_state[call.from_user.id] = {"stage": "waiting_name", "id": box_id}
    bot.edit_message_text(f"✏️ أرسل الاسم الجديد للخانة (الحالي: {box['name']}):",
                           chat_id=call.message.chat.id, message_id=call.message.message_id)
    bot.answer_callback_query(call.id)

@bot.message_handler(func=lambda m: edit_box_state.get(m.from_user.id, {}).get("stage") == "waiting_name")
@safe_handler
def handle_edit_box_name(message):
    state = edit_box_state.pop(message.from_user.id)
    name = message.text.strip()
    update_box(state["id"], name)
    bot.send_message(message.chat.id, f"✅ تم تعديل اسم الخانة بنجاح!\n\n📝 الاسم الجديد: {name}")

# ======================================================================
# ============================ إدارة الأدمنية ============================
# ======================================================================

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
    BACKUP_INTERVAL_SECONDS = 24 * 60 * 60
    while True:
        time.sleep(BACKUP_INTERVAL_SECONDS)
        caption = f"📦 نسخة احتياطية تلقائية - {datetime.now().strftime('%Y-%m-%d %H:%M')}"
        try:
            send_backup_everywhere(caption)
        except FileNotFoundError:
            pass

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

    save_user(call.from_user)
    markup = InlineKeyboardMarkup(row_width=1)

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
        append_subject_boxes(markup, "islamic")
        markup.add(back_btn("main_menu"))
        bot.edit_message_text("🕋 التربية الإسلامية - اختر القسم:", chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=markup)

    elif call.data == "arabic":
        uid = call.from_user.id
        markup.add(
            InlineKeyboardButton(chapter_label("القواعد", "test_ar_grammar", uid), callback_data="test_ar_grammar", style="success"),
            InlineKeyboardButton(chapter_label("الأدب والنصوص", "test_ar_literature", uid), callback_data="test_ar_literature", style="success")
        )
        append_subject_boxes(markup, "arabic")
        markup.add(back_btn("main_menu"))
        bot.edit_message_text("📝 اللغة العربية - اختر القسم:", chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=markup)

    elif call.data == "english":
        uid = call.from_user.id
        markup.add(
            InlineKeyboardButton(chapter_label("القواعد (Grammar)", "test_en_grammar", uid), callback_data="test_en_grammar", style="success"),
            InlineKeyboardButton(chapter_label("قطع الكتاب (Textbook)", "test_en_passages", uid), callback_data="test_en_passages", style="success"),
            InlineKeyboardButton(chapter_label("الأدب (Literature)", "test_en_literature", uid), callback_data="test_en_literature", style="success")
        )
        append_subject_boxes(markup, "english")
        markup.add(back_btn("main_menu"))
        bot.edit_message_text("🔠 اللغة الإنكليزية - اختر القسم:", chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=markup)

    elif call.data == "math":
        uid = call.from_user.id
        for i in range(1, 6):
            cat = f"test_math_ch{i}"
            markup.add(InlineKeyboardButton(chapter_label(f"الفصل {i}", cat, uid), callback_data=cat, style="success"))
        append_subject_boxes(markup, "math")
        markup.add(back_btn("main_menu"))
        bot.edit_message_text("🔢 الرياضيات - اختر الفصل:", chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=markup)

    elif call.data == "biology":
        uid = call.from_user.id
        for i in range(1, 6):
            cat = f"test_bio_ch{i}"
            markup.add(InlineKeyboardButton(chapter_label(f"الفصل {i}", cat, uid), callback_data=cat, style="success"))
        append_subject_boxes(markup, "biology")
        markup.add(back_btn("main_menu"))
        bot.edit_message_text("🧬 الأحياء - اختر الفصل:", chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=markup)

    elif call.data == "physics":
        uid = call.from_user.id
        for i in range(1, 11):
            cat = f"test_phy_ch{i}"
            markup.add(InlineKeyboardButton(chapter_label(f"الفصل {i}", cat, uid), callback_data=cat, style="success"))
        append_subject_boxes(markup, "physics")
        markup.add(back_btn("main_menu"))
        bot.edit_message_text("⚡ الفيزياء - اختر الفصل:", chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=markup)

    elif call.data == "chemistry":
        uid = call.from_user.id
        for i in range(1, 9):
            cat = f"test_chem_ch{i}"
            markup.add(InlineKeyboardButton(chapter_label(f"الفصل {i}", cat, uid), callback_data=cat, style="success"))
        append_subject_boxes(markup, "chemistry")
        markup.add(back_btn("main_menu"))
        bot.edit_message_text("🧪 الكيمياء - اختر الفصل:", chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=markup)

    elif call.data.startswith("test_"):
        category = call.data
        b_target = subject_for_category(category)

        if not is_category_free(category) and not is_vip(call.from_user.id):
            bot.edit_message_text(
                VIP_UPSELL_TEXT,
                chat_id=call.message.chat.id, message_id=call.message.message_id,
                reply_markup=vip_upsell_markup(b_target)
            )
            return

        tests = get_display_tests(category)   # أزرق (style="primary") - رابط مباشر
        boxes = get_db_boxes(category)         # أخضر (style="success") - تفتح شاشة فرعية

        if not tests and not boxes:
            bot.answer_callback_query(call.id, "عذراً، لم يتم إضافة أي محتوى لهذا القسم بعد ⏳", show_alert=True)
            return

        log_view("chapter", category)

        for test in tests:
            markup.add(InlineKeyboardButton(test["name"], url=test["url"], style="primary"))
        for box in boxes:
            markup.add(InlineKeyboardButton(box['name'], callback_data=f"box_{box['id']}", style="success"))

        markup.add(back_btn(b_target))
        bot.edit_message_text("📚 اختر موضوع الاختبار للانتقال للموقع:", chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=markup)

    elif call.data.startswith("box_"):
        try:
            box_id = int(call.data.replace("box_", "", 1))
        except ValueError:
            bot.answer_callback_query(call.id, "⚠️ خطأ بالبيانات", show_alert=True)
            return

        box = get_box_by_id(box_id)
        if not box or not box["is_active"]:
            bot.answer_callback_query(call.id, "⚠️ هذه الخانة غير متوفرة حالياً", show_alert=True)
            return

        location = f"box_{box_id}"
        tests = get_db_tests(location)     # أزرق - رابط مباشر
        subboxes = get_db_boxes(location)  # أخضر - خانات متداخلة

        if not tests and not subboxes:
            bot.answer_callback_query(call.id, "📦 لا يوجد محتوى داخل هذه الخانة بعد ⏳", show_alert=True)
            return

        log_view("box", location)

        for test in tests:
            markup.add(InlineKeyboardButton(test["name"], url=test["url"], style="primary"))
        for sb in subboxes:
            markup.add(InlineKeyboardButton(sb['name'], callback_data=f"box_{sb['id']}", style="success"))

        back_target = back_target_for_location(box["category"])
        markup.add(back_btn(back_target))
        bot.edit_message_text(f"{box['name']} - اختر:", chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=markup)

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
