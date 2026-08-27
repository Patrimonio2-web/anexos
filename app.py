from flask import (
    Flask, render_template, request, redirect, url_for, send_file,
    jsonify, session, flash, Blueprint, render_template_string
)
from flask_sqlalchemy import SQLAlchemy
from flask_cors import CORS

from sqlalchemy import text, asc  # <- text y asc en una sola línea

from werkzeug.utils import secure_filename
from werkzeug.security import check_password_hash, generate_password_hash

from datetime import datetime, timedelta
from decimal import Decimal

import os, tempfile, io, json, hmac, secrets, time
import pytz
import cloudinary, cloudinary.uploader
import psycopg2, psycopg2.extras
import qrcode
import pandas as pd
from dotenv import load_dotenv

from functools import wraps
from PIL import Image, ImageDraw, ImageFont
from openpyxl import Workbook


# ===================== APP & CONFIG =====================
load_dotenv()

IS_PRODUCTION = bool(os.getenv("RENDER") or os.getenv("RENDER_EXTERNAL_URL"))


def _split_env_list(name, fallback=None):
    raw = os.getenv(name)
    if not raw:
        return list(fallback or [])
    return [item.strip() for item in raw.split(",") if item.strip()]


def _required_env(name):
    value = os.getenv(name)
    if value:
        return value
    if IS_PRODUCTION:
        raise RuntimeError(f"Falta configurar la variable de entorno {name}")
    return ""


def _normalize_database_url(value):
    if value.startswith("postgres://"):
        return value.replace("postgres://", "postgresql://", 1)
    return value


DEFAULT_FRONTEND_ORIGINS = [
    "https://heritage-management.vercel.app",
    "https://control-personal-legislatura-lr.vercel.app",
    "http://localhost:3000",
]
FRONTEND_ORIGINS = _split_env_list("FRONTEND_ORIGINS", DEFAULT_FRONTEND_ORIGINS)
BACKEND_PUBLIC_URL = os.getenv("BACKEND_PUBLIC_URL") or os.getenv("RENDER_EXTERNAL_URL") or "https://anexos.onrender.com"
ALLOWED_ORIGINS = {origin.rstrip("/") for origin in (FRONTEND_ORIGINS + [BACKEND_PUBLIC_URL])}

SECRET_KEY = _required_env("SECRET_KEY") or "dev-secret-key-change-me"
DATABASE_URL = _normalize_database_url(_required_env("DATABASE_URL") or "sqlite:///local-dev.db")
CLOUDINARY_CLOUD_NAME = _required_env("CLOUDINARY_CLOUD_NAME")
CLOUDINARY_API_KEY = _required_env("CLOUDINARY_API_KEY")
CLOUDINARY_API_SECRET = _required_env("CLOUDINARY_API_SECRET")

app = Flask(__name__)

# SECRET_KEY viene desde variables de entorno.
app.secret_key = SECRET_KEY

# CORS permitido solo para los frontends configurados.
CORS(
    app,
    supports_credentials=True,
    origins=FRONTEND_ORIGINS,
    allow_headers=["Content-Type", "X-CSRF-Token"],
)

# Cookies de sesion para cross-site (Vercel <-> Render).
app.config["SESSION_COOKIE_SAMESITE"] = "None"
app.config["SESSION_COOKIE_SECURE"] = True
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.permanent_session_lifetime = timedelta(days=7)

# Base de datos configurada por DATABASE_URL.
app.config['SQLALCHEMY_DATABASE_URI'] = DATABASE_URL
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024

db = SQLAlchemy(app)

# Cloudinary configurado por variables de entorno.
cloudinary.config(
    cloud_name=CLOUDINARY_CLOUD_NAME,
    api_key=CLOUDINARY_API_KEY,
    api_secret=CLOUDINARY_API_SECRET
)

# ===================== HELPERS =====================
def get_conn_dict():
    conn = psycopg2.connect(
        DATABASE_URL,
        cursor_factory=psycopg2.extras.DictCursor,
        sslmode=os.getenv("DB_SSLMODE", "require")
    )
    cur = conn.cursor()
    return conn, cur

def login_required(f):
    @wraps(f)
    def wrapped(*args, **kwargs):
        if 'username' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return wrapped

def login_required_api(f):
    @wraps(f)
    def wrapped(*args, **kwargs):
        if 'username' not in session:
            return jsonify({"error": "unauthorized"}), 401
        return f(*args, **kwargs)
    return wrapped


def _main_username():
    return session.get("username")


def _personal_username():
    return session.get("username_personal")


SUPERADMIN_USERNAMES = {
    item.lower()
    for item in _split_env_list("SUPERADMIN_USERNAMES", ["hernan", "facu"])
}
VIEWER_USERNAMES = {
    item.lower()
    for item in _split_env_list("VIEWER_USERNAMES", ["dante", "vicegobernacion"])
}


def _normalize_main_role(username, role=None):
    clean_username = str(username or "").strip().lower()
    clean_role = str(role or "").strip().lower()
    if clean_username in SUPERADMIN_USERNAMES or clean_role == "superadmin":
        return "superadmin"
    if clean_username in VIEWER_USERNAMES or clean_role in {"viewer", "solo_lectura", "solo lectura", "read_only", "readonly"}:
        return "viewer"
    return "admin"


def _session_main_role():
    return _normalize_main_role(_main_username(), session.get("role"))


def _is_viewer_session():
    return _session_main_role() == "viewer"


def _is_superadmin_session():
    return _session_main_role() == "superadmin"


LOGIN_ATTEMPTS = {}
LOGIN_RATE_LIMIT = int(os.getenv("LOGIN_RATE_LIMIT", "8"))
LOGIN_RATE_WINDOW_SECONDS = int(os.getenv("LOGIN_RATE_WINDOW_SECONDS", "900"))
IDLE_SESSION_TIMEOUT_SECONDS = int(os.getenv("IDLE_SESSION_TIMEOUT_SECONDS", "600"))
PRESENCE_ONLINE_SECONDS = int(os.getenv("PRESENCE_ONLINE_SECONDS", "180"))


def _login_rate_key(username):
    ip = (request.headers.get("X-Forwarded-For") or request.remote_addr or "").split(",")[0].strip()
    return f"{ip}:{(username or '').strip().lower()}"


def _login_rate_allowed(username):
    key = _login_rate_key(username)
    now = time.time()
    attempts = [
        ts for ts in LOGIN_ATTEMPTS.get(key, [])
        if now - ts < LOGIN_RATE_WINDOW_SECONDS
    ]
    LOGIN_ATTEMPTS[key] = attempts
    if len(attempts) >= LOGIN_RATE_LIMIT:
        retry_after = max(1, int(LOGIN_RATE_WINDOW_SECONDS - (now - attempts[0])))
        return False, retry_after
    return True, 0


def _record_login_failure(username):
    key = _login_rate_key(username)
    LOGIN_ATTEMPTS.setdefault(key, []).append(time.time())


def _clear_login_failures(username):
    LOGIN_ATTEMPTS.pop(_login_rate_key(username), None)


def _ensure_csrf_token():
    token = session.get("csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["csrf_token"] = token
    return token


def _csrf_token_valid():
    expected = session.get("csrf_token")
    provided = request.headers.get("X-CSRF-Token") or ""
    return bool(expected and provided and hmac.compare_digest(expected, provided))


def _session_has_auth():
    return bool(_main_username() or _personal_username())


def _refresh_main_session_from_db():
    username = _main_username()
    if not username:
        return True
    try:
        row = db.session.execute(text("""
            SELECT username, COALESCE(role, 'usuario') AS role, COALESCE(activo, TRUE) AS activo
            FROM usuarios
            WHERE username = :username
            LIMIT 1
        """), {"username": username}).mappings().first()
        if not row or not row["activo"]:
            _clear_auth_session()
            return False
        session["username"] = row["username"]
        session["role"] = _normalize_main_role(row["username"], row["role"])
        return True
    except Exception:
        db.session.rollback()
        return True


def _clear_auth_session():
    for key in (
        "username",
        "role",
        "username_personal",
        "role_personal",
        "csrf_token",
        "last_activity",
    ):
        session.pop(key, None)


def _session_idle_expired():
    last_activity = session.get("last_activity")
    if not last_activity:
        return False
    try:
        return time.time() - float(last_activity) > IDLE_SESSION_TIMEOUT_SECONDS
    except (TypeError, ValueError):
        return True


def _touch_session_activity():
    session["last_activity"] = time.time()


def admin_required_api(f):
    @wraps(f)
    def wrapped(*args, **kwargs):
        if not _main_username():
            return jsonify({"error": "unauthorized"}), 401
        if _is_viewer_session():
            return jsonify({"error": "forbidden"}), 403
        return f(*args, **kwargs)
    return wrapped


def superadmin_required_api(f):
    @wraps(f)
    def wrapped(*args, **kwargs):
        if not _main_username():
            return jsonify({"error": "unauthorized"}), 401
        if not _is_superadmin_session():
            return jsonify({"error": "forbidden"}), 403
        return f(*args, **kwargs)
    return wrapped


def hernan_required_api(f):
    @wraps(f)
    def wrapped(*args, **kwargs):
        if not _main_username():
            return jsonify({"error": "unauthorized"}), 401
        if str(_main_username() or "").strip().lower() != "hernan":
            return jsonify({"error": "forbidden"}), 403
        return f(*args, **kwargs)
    return wrapped


def personal_required_api(f):
    @wraps(f)
    def wrapped(*args, **kwargs):
        if _personal_username() or (_main_username() and not _is_viewer_session()):
            return f(*args, **kwargs)
        return jsonify({"error": "unauthorized"}), 401
    return wrapped


PUBLIC_API_ENDPOINTS = {
    "api_login",
    "api_logout",
    "api_me",
    "api_login_personal",
    "api_logout_personal",
    "api_me_personal",
    "mobiliario_advertencia_por_id",
}
IDLE_EXEMPT_ENDPOINTS = {
    "api_login",
    "api_logout",
    "api_login_personal",
    "api_logout_personal",
    "mobiliario_advertencia_por_id",
}
UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
CSRF_EXEMPT_ENDPOINTS = PUBLIC_API_ENDPOINTS


def _origin_is_allowed():
    origin = (request.headers.get("Origin") or "").rstrip("/")
    if not origin:
        return True
    return origin in ALLOWED_ORIGINS


@app.before_request
def proteger_api():
    if not request.path.startswith("/api/"):
        return None
    if request.method == "OPTIONS":
        return None

    if request.method in UNSAFE_METHODS and not _origin_is_allowed():
        return jsonify({"error": "origin_forbidden"}), 403

    if _main_username() and request.endpoint not in {
        "api_login",
        "api_logout",
        "api_login_personal",
        "api_logout_personal",
    }:
        if not _refresh_main_session_from_db():
            return jsonify({"error": "unauthorized"}), 401

    if _session_has_auth() and request.endpoint not in IDLE_EXEMPT_ENDPOINTS:
        if _session_idle_expired():
            _clear_auth_session()
            return jsonify({"error": "session_expired"}), 401

    if request.endpoint in PUBLIC_API_ENDPOINTS:
        if _session_has_auth() and request.endpoint in {"api_me", "api_me_personal"}:
            _touch_session_activity()
        return None

    if not _session_has_auth():
        return jsonify({"error": "unauthorized"}), 401

    if request.method in UNSAFE_METHODS and request.endpoint not in CSRF_EXEMPT_ENDPOINTS:
        if not _csrf_token_valid():
            return jsonify({"error": "csrf_invalid"}), 403

    _touch_session_activity()

    return None


# MODELOS
# Modelos
class Rubro(db.Model):
    __tablename__ = 'rubros'
    id_rubro = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.Text, nullable=False)


class ClaseBien(db.Model):
    __tablename__ = 'clases_bienes'
    id_clase = db.Column(db.Integer, primary_key=True)  # 👈 correcto
    id_rubro = db.Column(db.Integer, db.ForeignKey('rubros.id_rubro'), nullable=False)
    descripcion = db.Column(db.Text, nullable=False)


_nomenclador_encoding_checked = False


def _limpiar_texto_nomenclador(value):
    if value is None:
        return None
    texto = str(value)
    return (
        texto
        .replace("\ufffd", "\u00d1")
        .replace("\u00c3\u2018", "\u00d1")
        .replace("\u00c3\u00b1", "\u00f1")
    )


def _ensure_nomenclador_encoding():
    # Corrige datos historicos del nomenclador que quedaron con la letra enye rota.
    global _nomenclador_encoding_checked
    if _nomenclador_encoding_checked:
        return

    try:
        db.session.execute(text("""
            UPDATE clases_bienes
            SET descripcion = replace(
                replace(
                    replace(descripcion, :bad_replacement, :enye_mayus),
                    :bad_enye_mayus,
                    :enye_mayus
                ),
                :bad_enye_minus,
                :enye_minus
            )
            WHERE descripcion LIKE :pattern_replacement
               OR descripcion LIKE :pattern_enye_mayus
               OR descripcion LIKE :pattern_enye_minus
        """), {
            "bad_replacement": "\ufffd",
            "bad_enye_mayus": "\u00c3\u2018",
            "bad_enye_minus": "\u00c3\u00b1",
            "enye_mayus": "\u00d1",
            "enye_minus": "\u00f1",
            "pattern_replacement": "%\ufffd%",
            "pattern_enye_mayus": "%\u00c3\u2018%",
            "pattern_enye_minus": "%\u00c3\u00b1%",
        })
        db.session.commit()
    except Exception:
        db.session.rollback()
    finally:
        _nomenclador_encoding_checked = True


class Anexo(db.Model):
    __tablename__ = 'anexos'
    id = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(255), nullable=False)
    direccion = db.Column(db.Text)

class UsuarioPersonal(db.Model):
    __tablename__ = 'usuariospersonal'

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False)
    password = db.Column(db.String(255), nullable=False)  # hash
    role = db.Column(db.String(20), nullable=False, default='personal')
    activo = db.Column(db.Boolean, nullable=False, default=True)
    fecha_creacion = db.Column(db.DateTime, server_default=db.func.now())

    def to_dict(self):
        return {
            "id": self.id,
            "username": self.username,
            "role": self.role,
            "activo": self.activo,
            "fecha_creacion": self.fecha_creacion
        }

class Usuario(db.Model):
    __tablename__ = 'usuarios'
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False)
    password = db.Column(db.String(255), nullable=False)  # hash
    nombre = db.Column(db.String(100))
    apellido = db.Column(db.String(100))
    role = db.Column(db.String(20), nullable=False, default='usuario')
    activo = db.Column(db.Boolean, nullable=False, default=True)
    fecha_creacion = db.Column(db.DateTime, server_default=db.func.now())

class Subdependencia(db.Model):
    __tablename__ = 'subdependencias'
    id = db.Column(db.Integer, primary_key=True)
    id_anexo = db.Column(db.Integer, db.ForeignKey('anexos.id', ondelete='CASCADE'), nullable=False)
    nombre = db.Column(db.String(255), nullable=False)
    piso = db.Column(db.Integer)  # 👈 este campo está en tu base (PDF), podés incluirlo si lo necesitás

class Auditoria(db.Model):
    __tablename__ = 'auditoria'

    id = db.Column(db.Integer, primary_key=True)
    fecha = db.Column(db.DateTime, server_default=db.func.now())
    tabla_afectada = db.Column(db.String(100), nullable=False)
    id_registro = db.Column(db.String(50), nullable=False)
    accion = db.Column(db.String(50), nullable=False)
    cambios = db.Column(db.Text)
    ip_origen = db.Column(db.String(50))
    user_agent = db.Column(db.Text)
    usuario = db.Column(db.String(100))  # 👈 nuevo campo

    def to_dict(self):
        return {
            "id": self.id,
            "fecha": self.fecha.strftime("%d/%m/%Y %H:%M"),
            "tabla_afectada": self.tabla_afectada,
            "id_registro": self.id_registro,
            "accion": self.accion,
            "cambios": self.cambios,
            "ip_origen": self.ip_origen,
            "user_agent": self.user_agent,
            "usuario": self.usuario
        }

class Agente(db.Model):
    __tablename__ = 'agentes'
    id = db.Column(db.Integer, primary_key=True)
    legajo = db.Column(db.String(20), unique=True, nullable=False)
    dni_cuil = db.Column(db.String(20), nullable=False)
    apellido = db.Column(db.String(100), nullable=False)
    nombre = db.Column(db.String(100), nullable=False)

    id_anexo = db.Column(db.Integer, db.ForeignKey('anexos.id', ondelete='SET NULL'))
    id_subdependencia = db.Column(db.Integer, db.ForeignKey('subdependencias.id', ondelete='SET NULL'))

    categoria = db.Column(db.String(10))
    tipo = db.Column(db.String(50))
    cargo = db.Column(db.String(100))
    telefono = db.Column(db.String(30))
    email = db.Column(db.String(150))
    foto_url = db.Column(db.Text)
    fecha_creacion = db.Column(db.DateTime, default=datetime.utcnow)

    anexo = db.relationship('Anexo', backref='agentes', lazy=True)
    subdependencia = db.relationship('Subdependencia', backref='agentes', lazy=True)

    def to_dict(self):
        return {
            "id": self.id,
            "legajo": self.legajo,
            "dni_cuil": self.dni_cuil,
            "apellido": self.apellido,
            "nombre": self.nombre,
            "categoria": self.categoria,
            "tipo": self.tipo,
            "cargo": self.cargo,
            "telefono": self.telefono,
            "email": self.email,
            "foto_url": self.foto_url,
            "id_anexo": self.id_anexo,
            "id_subdependencia": self.id_subdependencia,
            "anexo": self.anexo.nombre if self.anexo else None,
            "subdependencia": self.subdependencia.nombre if self.subdependencia else None,
            "fecha_creacion": self.fecha_creacion.strftime("%d/%m/%Y %H:%M") if self.fecha_creacion else None
        }




class Mobiliario(db.Model):
    __tablename__ = 'mobiliario'
    id = db.Column(db.String(50), primary_key=True)
    ubicacion_id = db.Column(db.Integer, db.ForeignKey('subdependencias.id'))  # 👈 clave foránea correcta
    clase_bien_id = db.Column(db.Integer, db.ForeignKey('clases_bienes.id_clase'))
    rubro_id = db.Column(db.Integer, db.ForeignKey('rubros.id_rubro'))

    descripcion = db.Column(db.Text)
    resolucion = db.Column(db.Text)
    fecha_resolucion = db.Column(db.Date)
    estado_conservacion = db.Column(db.String(20))
    estado_control = db.Column(db.String(20))
    historial_movimientos = db.Column(db.Text)

    no_dado = db.Column(db.Boolean, default=False)
    para_reparacion = db.Column(db.Boolean, default=False)
    para_baja = db.Column(db.Boolean, default=False)
    faltante = db.Column(db.Boolean, default=False)
    sobrante = db.Column(db.Boolean, default=False)
    problema_etiqueta = db.Column(db.Boolean, default=False)

    comentarios = db.Column(db.Text)
    foto_url = db.Column(db.String(255))
    foto_url_2 = db.Column(db.String(255))
    valor = db.Column(db.Numeric(12, 2))

    fecha_creacion = db.Column(db.DateTime, default=datetime.utcnow)
    fecha_actualizacion = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


_mobiliario_valor_column_ready = False
_mobiliario_foto_2_column_ready = False


def _ensure_mobiliario_valor_column():
    global _mobiliario_valor_column_ready
    if _mobiliario_valor_column_ready:
        return
    if db.engine.dialect.name == "sqlite":
        columns = db.session.execute(text("PRAGMA table_info(mobiliario)")).fetchall()
        if not any(row[1] == "valor" for row in columns):
            db.session.execute(text("ALTER TABLE mobiliario ADD COLUMN valor NUMERIC(12, 2)"))
    else:
        db.session.execute(text("""
            ALTER TABLE IF EXISTS mobiliario
            ADD COLUMN IF NOT EXISTS valor NUMERIC(12, 2)
        """))
    db.session.commit()
    _mobiliario_valor_column_ready = True


def _ensure_mobiliario_foto_2_column():
    global _mobiliario_foto_2_column_ready
    if _mobiliario_foto_2_column_ready:
        return
    if db.engine.dialect.name == "sqlite":
        columns = db.session.execute(text("PRAGMA table_info(mobiliario)")).fetchall()
        if not any(row[1] == "foto_url_2" for row in columns):
            db.session.execute(text("ALTER TABLE mobiliario ADD COLUMN foto_url_2 VARCHAR(255)"))
    else:
        db.session.execute(text("""
            ALTER TABLE IF EXISTS mobiliario
            ADD COLUMN IF NOT EXISTS foto_url_2 VARCHAR(255)
        """))
    db.session.commit()
    _mobiliario_foto_2_column_ready = True


def _parse_mobiliario_valor(value):
    if value is None or value == "":
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, (int, float)):
        return Decimal(str(value))

    raw = str(value).strip().replace("$", "").replace(" ", "")
    if not raw:
        return None
    if "," in raw:
        raw = raw.replace(".", "").replace(",", ".")
    return Decimal(raw)


def _mobiliario_valor_json(value):
    if value is None:
        return None
    return float(value)


ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif'}
def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def upload_to_cloudinary(filepath):
    result = cloudinary.uploader.upload(filepath, folder="mobiliario")
    return result.get("secure_url")

@app.route('/api/uploads', methods=['POST'])
@personal_required_api
def subir_imagen():
    if 'foto' not in request.files:
        return jsonify({"error": "No se envió la imagen"}), 400

    file = request.files['foto']
    if file and allowed_file(file.filename):
        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix='.jpg') as temp:
                file.save(temp.name)
                url = upload_to_cloudinary(temp.name)
                os.remove(temp.name)
                return jsonify({"url": url})
        except Exception as e:
            return jsonify({"error": str(e)}), 500
    return jsonify({"error": "Formato de archivo no permitido"}), 400


# ===================== AUTH API (JSON para el frontend) =====================
import psycopg2  # para capturar UndefinedColumn
from werkzeug.security import check_password_hash, generate_password_hash


def _password_esta_hasheada(value):
    return str(value or "").startswith(("pbkdf2:", "scrypt:", "argon2:"))


def _password_coincide(stored, provided):
    if _password_esta_hasheada(stored):
        try:
            return check_password_hash(stored, provided)
        except ValueError:
            return False
    return (stored or "") == (provided or "")


@app.post("/api/login")
def api_login():
    data = request.get_json() or {}
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    if not username or not password:
        return jsonify({"error": "missing_credentials"}), 400

    allowed, retry_after = _login_rate_allowed(username)
    if not allowed:
        return jsonify({
            "error": "too_many_attempts",
            "retry_after": retry_after,
        }), 429

    try:
        conn, cur = get_conn_dict()
        try:
            # Intento completo (si faltan columnas role/activo, hacemos fallback)
            cur.execute("""
                SELECT id, username, password,
                       COALESCE(role, 'usuario')  AS role,
                       COALESCE(activo, TRUE)     AS activo
                FROM usuarios
                WHERE username = %s
                LIMIT 1
            """, (username,))
            row = cur.fetchone()
            user = dict(row) if row else None
        except psycopg2.errors.UndefinedColumn:
            conn.rollback()
            cur.execute("""
                SELECT id, username, password
                FROM usuarios
                WHERE username = %s
                LIMIT 1
            """, (username,))
            row = cur.fetchone()
            user = dict(row) if row else None
            if user:
                user["role"] = "usuario"
                user["activo"] = True
        finally:
            cur.close(); conn.close()
    except Exception as e:
        print("🔴 DB ERROR /api/login:", e)
        return jsonify({"error": "db_error"}), 500

    if not user:
        _record_login_failure(username)
        return jsonify({"error": "invalid_credentials"}), 401
    if not user.get("activo", True):
        _record_login_failure(username)
        return jsonify({"error": "user_inactive"}), 403

    stored = user.get("password") or ""

    def is_hashed(p: str) -> bool:
        # Heurística para hashes de werkzeug (pbkdf2:sha256:...)
        return _password_esta_hasheada(p)

    # Caso 1: ya está hasheada → validar normal
    if is_hashed(stored):
        if not _password_coincide(stored, password):
            _record_login_failure(username)
            return jsonify({"error": "invalid_credentials"}), 401
    else:
        # Caso 2: estaba en texto plano → migrar si coincide
        if not _password_coincide(stored, password):
            _record_login_failure(username)
            return jsonify({"error": "invalid_credentials"}), 401
        try:
            new_hash = generate_password_hash(password)
            conn, cur = get_conn_dict()
            cur.execute("UPDATE usuarios SET password = %s WHERE id = %s", (new_hash, user["id"]))
            conn.commit()
            cur.close(); conn.close()
            user["password"] = new_hash
            print(f"✅ Password migrada a hash para usuario {user['username']}")
        except Exception as e:
            print("🔴 Error migrando password:", e)
            # No bloqueamos el login aunque falle el update

    session.permanent = True
    session["username"] = user["username"]
    session["role"] = _normalize_main_role(user["username"], user.get("role"))
    session["csrf_token"] = secrets.token_urlsafe(32)
    _touch_session_activity()
    _clear_login_failures(username)
    return jsonify({"username": session["username"], "role": session["role"]}), 200

@app.get("/api/me")
@login_required_api
def api_me():
    role = _session_main_role()
    session["role"] = role
    return jsonify({"username": session.get("username"), "role": role}), 200


@app.get("/api/csrf")
@login_required_api
def api_csrf():
    return jsonify({"csrf_token": _ensure_csrf_token()}), 200


# =============================================================================
# API NUEVA: USUARIOS EN LINEA
# -----------------------------------------------------------------------------
# Guarda un pulso liviano por usuario autenticado y permite que todos los roles
# vean quien tuvo actividad reciente.
# =============================================================================

def _ensure_presencia_usuarios_table():
    db.session.execute(text("""
        CREATE TABLE IF NOT EXISTS usuarios_online (
            username VARCHAR(50) PRIMARY KEY,
            role VARCHAR(20),
            ultima_actividad TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP
        )
    """))
    db.session.commit()


def _presencia_to_dict(row):
    value = row["ultima_actividad"]
    return {
        "username": row["username"],
        "role": _normalize_main_role(row["username"], row["role"]),
        "ultima_actividad": value.isoformat() if hasattr(value, "isoformat") else str(value),
    }


def _actualizar_presencia_actual():
    username = _main_username()
    if not username:
        return None
    role = _session_main_role()
    row = db.session.execute(text("""
        INSERT INTO usuarios_online (username, role, ultima_actividad)
        VALUES (:username, :role, CURRENT_TIMESTAMP)
        ON CONFLICT (username) DO UPDATE SET
            role = EXCLUDED.role,
            ultima_actividad = CURRENT_TIMESTAMP
        RETURNING username, role, ultima_actividad
    """), {"username": username, "role": role}).mappings().first()
    db.session.commit()
    return row


def _eliminar_presencia_usuario(username):
    if not username:
        return
    try:
        _ensure_presencia_usuarios_table()
        db.session.execute(text("""
            DELETE FROM usuarios_online
            WHERE username = :username
        """), {"username": username})
        db.session.commit()
    except Exception:
        db.session.rollback()


@app.post("/api/presencia/ping")
@login_required_api
def ping_presencia_usuario():
    try:
        _ensure_presencia_usuarios_table()
        row = _actualizar_presencia_actual()
        return jsonify(_presencia_to_dict(row)), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500


@app.get("/api/presencia/usuarios")
@login_required_api
def listar_usuarios_online():
    try:
        _ensure_presencia_usuarios_table()
        _actualizar_presencia_actual()
        rows = db.session.execute(text("""
            SELECT username, role, ultima_actividad
            FROM usuarios_online
            WHERE ultima_actividad >= CURRENT_TIMESTAMP - make_interval(secs => :seconds)
            ORDER BY ultima_actividad DESC, LOWER(username) ASC
        """), {"seconds": PRESENCE_ONLINE_SECONDS}).mappings().all()
        return jsonify([_presencia_to_dict(row) for row in rows]), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500


# =============================================================================
# API NUEVA: PERFIL USUARIO
# -----------------------------------------------------------------------------
# Bloque agregado para editar datos del usuario autenticado sin resetear cuentas
# existentes. Las columnas nombre/apellido son opcionales sobre usuarios.
# =============================================================================

def _ensure_perfil_usuario_columns():
    db.session.execute(text("""
        ALTER TABLE IF EXISTS usuarios
        ADD COLUMN IF NOT EXISTS nombre VARCHAR(100)
    """))
    db.session.execute(text("""
        ALTER TABLE IF EXISTS usuarios
        ADD COLUMN IF NOT EXISTS apellido VARCHAR(100)
    """))
    db.session.commit()


def _perfil_usuario_actual():
    return db.session.execute(text("""
        SELECT id, username, nombre, apellido
        FROM usuarios
        WHERE username = :username
        LIMIT 1
    """), {"username": session.get("username")}).mappings().first()


def _perfil_usuario_to_dict(row):
    role = _session_main_role()
    session["role"] = role
    return {
        "id": row["id"],
        "username": row["username"],
        "nombre": row["nombre"] or "",
        "apellido": row["apellido"] or "",
        "role": role,
    }


def _texto_perfil(data, key, max_length):
    value = str(data.get(key) or "").strip()
    if len(value) > max_length:
        raise ValueError(f"{key} supera el maximo de {max_length} caracteres")
    return value or None


@app.get("/api/perfil")
@login_required_api
def obtener_perfil_usuario():
    try:
        _ensure_perfil_usuario_columns()
        row = _perfil_usuario_actual()
        if not row:
            return jsonify({"error": "Usuario no encontrado"}), 404
        return jsonify(_perfil_usuario_to_dict(row)), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500


@app.put("/api/perfil")
@login_required_api
def actualizar_perfil_usuario():
    try:
        _ensure_perfil_usuario_columns()
        data = request.get_json(silent=True) or {}
        username = str(data.get("username") or "").strip()
        if not username:
            return jsonify({"error": "El usuario es obligatorio"}), 400
        if len(username) > 50:
            return jsonify({"error": "El usuario supera el maximo de 50 caracteres"}), 400

        actual = _perfil_usuario_actual()
        if not actual:
            return jsonify({"error": "Usuario no encontrado"}), 404

        nombre = _texto_perfil(data, "nombre", 100)
        apellido = _texto_perfil(data, "apellido", 100)
        repetido = db.session.execute(text("""
            SELECT id
            FROM usuarios
            WHERE LOWER(username) = LOWER(:username)
              AND id <> :id
            LIMIT 1
        """), {"username": username, "id": actual["id"]}).mappings().first()
        if repetido:
            db.session.rollback()
            return jsonify({"error": "Ese nombre de usuario ya esta en uso"}), 409

        row = db.session.execute(text("""
            UPDATE usuarios
            SET username = :username,
                nombre = :nombre,
                apellido = :apellido
            WHERE id = :id
            RETURNING id, username, nombre, apellido
        """), {
            "id": actual["id"],
            "username": username,
            "nombre": nombre,
            "apellido": apellido,
        }).mappings().first()
        db.session.commit()
        session["username"] = row["username"]
        return jsonify(_perfil_usuario_to_dict(row)), 200
    except ValueError as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500


@app.put("/api/perfil/password")
@login_required_api
def actualizar_password_perfil_usuario():
    try:
        data = request.get_json(silent=True) or {}
        actual_password = data.get("password_actual") or ""
        nueva_password = data.get("password_nueva") or ""
        confirmar_password = data.get("password_confirmacion") or ""
        if not actual_password or not nueva_password or not confirmar_password:
            return jsonify({"error": "Completa todos los campos de contrasena"}), 400
        if nueva_password != confirmar_password:
            return jsonify({"error": "La confirmacion de contrasena no coincide"}), 400
        if len(nueva_password) < 6:
            return jsonify({"error": "La nueva contrasena debe tener al menos 6 caracteres"}), 400

        row = db.session.execute(text("""
            SELECT id, password
            FROM usuarios
            WHERE username = :username
            LIMIT 1
        """), {"username": session.get("username")}).mappings().first()
        if not row:
            return jsonify({"error": "Usuario no encontrado"}), 404
        if not _password_coincide(row["password"], actual_password):
            return jsonify({"error": "La contrasena actual es incorrecta"}), 400

        db.session.execute(text("""
            UPDATE usuarios
            SET password = :password
            WHERE id = :id
        """), {
            "id": row["id"],
            "password": generate_password_hash(nueva_password),
        })
        db.session.commit()
        return jsonify({"mensaje": "Contrasena actualizada"}), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500


# =============================================================================
# API NUEVA: ADMIN USUARIOS
# -----------------------------------------------------------------------------
# Panel independiente para superadmin. Permite listar usuarios, crear cuentas,
# cambiar rol admin/viewer, activar/desactivar y resetear contrasena sin tocar
# los flujos existentes de inventario.
# =============================================================================

def _ensure_admin_usuarios_columns():
    _ensure_perfil_usuario_columns()
    db.session.execute(text("""
        ALTER TABLE IF EXISTS usuarios
        ADD COLUMN IF NOT EXISTS role VARCHAR(20) NOT NULL DEFAULT 'usuario'
    """))
    db.session.execute(text("""
        ALTER TABLE IF EXISTS usuarios
        ADD COLUMN IF NOT EXISTS activo BOOLEAN NOT NULL DEFAULT TRUE
    """))
    db.session.execute(text("""
        ALTER TABLE IF EXISTS usuarios
        ADD COLUMN IF NOT EXISTS fecha_creacion TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP
    """))
    db.session.commit()


def _admin_fecha_iso(value):
    if value is None:
        return None
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


def _admin_bool(value, default=True):
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "t", "yes", "si", "activo"}


def _admin_texto(data, key, max_length, required=False):
    value = str(data.get(key) or "").strip()
    if required and not value:
        raise ValueError(f"{key} es obligatorio")
    if len(value) > max_length:
        raise ValueError(f"{key} supera el maximo de {max_length} caracteres")
    return value or None


def _admin_role_para_guardar(username, role):
    clean_username = str(username or "").strip().lower()
    if clean_username in SUPERADMIN_USERNAMES:
        return "superadmin"

    clean_role = str(role or "").strip().lower()
    if clean_role in {"", "usuario"}:
        clean_role = "admin"
    if clean_role not in {"admin", "viewer"}:
        raise ValueError("Rol invalido")
    return clean_role


def _admin_usuario_to_dict(row):
    username = row["username"]
    role = _normalize_main_role(username, row["role"])
    return {
        "id": row["id"],
        "username": username,
        "nombre": row["nombre"] or "",
        "apellido": row["apellido"] or "",
        "role": role,
        "activo": _admin_bool(row["activo"]),
        "fecha_creacion": _admin_fecha_iso(row["fecha_creacion"]),
        "protegido": str(username or "").strip().lower() in SUPERADMIN_USERNAMES,
    }


def _admin_usuario_por_id(id_usuario):
    return db.session.execute(text("""
        SELECT
            id,
            username,
            nombre,
            apellido,
            COALESCE(role, 'usuario') AS role,
            COALESCE(activo, TRUE) AS activo,
            fecha_creacion
        FROM usuarios
        WHERE id = :id
        LIMIT 1
    """), {"id": id_usuario}).mappings().first()


def _admin_usuario_actual_id():
    row = db.session.execute(text("""
        SELECT id
        FROM usuarios
        WHERE username = :username
        LIMIT 1
    """), {"username": session.get("username")}).mappings().first()
    return row["id"] if row else None


@app.get("/api/admin/usuarios")
@superadmin_required_api
def admin_listar_usuarios():
    try:
        _ensure_admin_usuarios_columns()
        rows = db.session.execute(text("""
            SELECT
                id,
                username,
                nombre,
                apellido,
                COALESCE(role, 'usuario') AS role,
                COALESCE(activo, TRUE) AS activo,
                fecha_creacion
            FROM usuarios
            ORDER BY LOWER(username) ASC
        """)).mappings().all()
        return jsonify([_admin_usuario_to_dict(row) for row in rows]), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500


@app.post("/api/admin/usuarios")
@superadmin_required_api
def admin_crear_usuario():
    try:
        _ensure_admin_usuarios_columns()
        data = request.get_json(silent=True) or {}
        username = _admin_texto(data, "username", 50, required=True)
        nombre = _admin_texto(data, "nombre", 100)
        apellido = _admin_texto(data, "apellido", 100)
        password = str(data.get("password") or "")
        if len(password) < 6:
            return jsonify({"error": "La contrasena debe tener al menos 6 caracteres"}), 400
        role = _admin_role_para_guardar(username, data.get("role"))
        activo = _admin_bool(data.get("activo"), True)
        if role == "superadmin" and not activo:
            return jsonify({"error": "Un superadmin no puede quedar inactivo"}), 400

        repetido = db.session.execute(text("""
            SELECT id
            FROM usuarios
            WHERE LOWER(username) = LOWER(:username)
            LIMIT 1
        """), {"username": username}).mappings().first()
        if repetido:
            return jsonify({"error": "Ese usuario ya existe"}), 409

        row = db.session.execute(text("""
            INSERT INTO usuarios (username, password, nombre, apellido, role, activo)
            VALUES (:username, :password, :nombre, :apellido, :role, :activo)
            RETURNING id, username, nombre, apellido, role, activo, fecha_creacion
        """), {
            "username": username,
            "password": generate_password_hash(password),
            "nombre": nombre,
            "apellido": apellido,
            "role": role,
            "activo": activo,
        }).mappings().first()
        db.session.commit()
        return jsonify(_admin_usuario_to_dict(row)), 201
    except ValueError as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500


@app.patch("/api/admin/usuarios/<int:id_usuario>")
@superadmin_required_api
def admin_actualizar_usuario(id_usuario):
    try:
        _ensure_admin_usuarios_columns()
        data = request.get_json(silent=True) or {}
        actual = _admin_usuario_por_id(id_usuario)
        if not actual:
            return jsonify({"error": "Usuario no encontrado"}), 404

        username = _admin_texto(data, "username", 50, required=True)
        nombre = _admin_texto(data, "nombre", 100)
        apellido = _admin_texto(data, "apellido", 100)
        role = _admin_role_para_guardar(username, data.get("role", actual["role"]))
        activo = _admin_bool(data.get("activo"), True)

        actual_username = str(actual["username"] or "").strip().lower()
        next_username = str(username or "").strip().lower()
        if actual_username in SUPERADMIN_USERNAMES and next_username not in SUPERADMIN_USERNAMES:
            return jsonify({"error": "No se puede cambiar el usuario de un superadmin protegido"}), 400
        if role == "superadmin" and not activo:
            return jsonify({"error": "Un superadmin no puede quedar inactivo"}), 400

        current_id = _admin_usuario_actual_id()
        if current_id == id_usuario and (not activo or role != "superadmin"):
            return jsonify({"error": "No podes quitarte tus propios permisos"}), 400

        repetido = db.session.execute(text("""
            SELECT id
            FROM usuarios
            WHERE LOWER(username) = LOWER(:username)
              AND id <> :id
            LIMIT 1
        """), {"username": username, "id": id_usuario}).mappings().first()
        if repetido:
            return jsonify({"error": "Ese usuario ya existe"}), 409

        row = db.session.execute(text("""
            UPDATE usuarios
            SET username = :username,
                nombre = :nombre,
                apellido = :apellido,
                role = :role,
                activo = :activo
            WHERE id = :id
            RETURNING id, username, nombre, apellido, role, activo, fecha_creacion
        """), {
            "id": id_usuario,
            "username": username,
            "nombre": nombre,
            "apellido": apellido,
            "role": role,
            "activo": activo,
        }).mappings().first()
        db.session.commit()

        if current_id == id_usuario:
            session["username"] = row["username"]
            session["role"] = _normalize_main_role(row["username"], row["role"])

        return jsonify(_admin_usuario_to_dict(row)), 200
    except ValueError as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500


@app.patch("/api/admin/usuarios/<int:id_usuario>/password")
@superadmin_required_api
def admin_resetear_password_usuario(id_usuario):
    try:
        _ensure_admin_usuarios_columns()
        data = request.get_json(silent=True) or {}
        password = str(data.get("password") or "")
        if len(password) < 6:
            return jsonify({"error": "La contrasena debe tener al menos 6 caracteres"}), 400

        actual = _admin_usuario_por_id(id_usuario)
        if not actual:
            return jsonify({"error": "Usuario no encontrado"}), 404

        db.session.execute(text("""
            UPDATE usuarios
            SET password = :password
            WHERE id = :id
        """), {
            "id": id_usuario,
            "password": generate_password_hash(password),
        })
        db.session.commit()
        return jsonify({"mensaje": "Contrasena actualizada"}), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500


@app.post("/api/logout")
def api_logout():
    _eliminar_presencia_usuario(session.get("username"))
    _clear_auth_session()
    return jsonify({"ok": True}), 200



@app.route('/logout')
def logout():
    _eliminar_presencia_usuario(session.get("username"))
    _clear_auth_session()
    flash('Has cerrado sesión correctamente.', 'success')
    return redirect(url_for('login'))


# API para obtener todos los rubros ordenados por ID
@app.route('/api/rubros', methods=['GET'])
def obtener_rubros():
    _ensure_nomenclador_encoding()
    rubros = Rubro.query.order_by(Rubro.id_rubro).all()
    data = [{'id_rubro': r.id_rubro, 'nombre': _limpiar_texto_nomenclador(r.nombre)} for r in rubros]
    return jsonify(data)


# API para obtener clases por rubro
# http://127.0.0.1:5000/api/clases-por-rubro?rubro_id=437
@app.route('/api/clases-por-rubro', methods=['GET'])
def clases_por_rubro():
    try:
        _ensure_nomenclador_encoding()
        rubro_id = request.args.get('rubro_id', type=int)
        if not rubro_id:
            return jsonify({'error': 'Falta el parámetro rubro_id'}), 400

        clases = ClaseBien.query.filter_by(id_rubro=rubro_id).order_by(ClaseBien.descripcion).all()

        data = [{
            'id_clase': c.id_clase,
            'descripcion': _limpiar_texto_nomenclador(c.descripcion),
            'id_rubro': c.id_rubro
        } for c in clases]

        return jsonify(data)

    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ✅ NUEVA API PARA NOMENCLADOR (GLOBAL) 
@app.route('/api/buscar-clase-global', methods=['GET'])
def buscar_clase_global():
    try:
        _ensure_nomenclador_encoding()
        query = (request.args.get('query') or "").strip()
        if not query:
            return jsonify([])

        if query.isdigit():
            clases = (
                ClaseBien.query
                .filter(ClaseBien.id_clase == int(query))
                .order_by(ClaseBien.descripcion)
                .all()
            )
        else:
            clases = (
                ClaseBien.query
                .filter(ClaseBien.descripcion.ilike(f"%{query}%"))
                .order_by(ClaseBien.descripcion)
                .all()
            )

        data = [{
            'id_clase': c.id_clase,
            'descripcion': _limpiar_texto_nomenclador(c.descripcion),
            'id_rubro': c.id_rubro
        } for c in clases]

        return jsonify(data)

    except Exception as e:
        return jsonify({'error': str(e)}), 500

# AUDITORIAS ----------------------------------------------------------------------------------------------------------

from flask import request, jsonify, session
from sqlalchemy import text
from datetime import date
import json

# OJO: asumimos que existe `db` (SQLAlchemy) en este módulo.

def registrar_auditoria(accion, tabla, id_registro, before=None, after=None, descripcion=None):
    """
    Inserta una fila de auditoría. NO hace commit (lo hace quien llama).
    Guarda la fecha en AR usando timezone('America/Argentina/Buenos_Aires', now()) en SQL.
    """
    try:
        usuario = session.get("username") or session.get("username_personal") or "desconocido"
        ip = (request.headers.get("X-Forwarded-For") or request.remote_addr or "").split(",")[0].strip()
        ua = request.headers.get("User-Agent") or ""

        diff = None
        if isinstance(before, dict) and isinstance(after, dict):
            diff = {}
            keys = set(before.keys()) | set(after.keys())
            for k in sorted(keys):
                if before.get(k) != after.get(k):
                    diff[k] = [before.get(k), after.get(k)]

        db.session.execute(
            text("""
                INSERT INTO auditoria (
                    fecha, accion, tabla_afectada, id_registro,
                    datos_anteriores, datos_nuevos, cambios, descripcion,
                    usuario, ip_origen, user_agent
                )
                VALUES (
                    timezone('America/Argentina/Buenos_Aires', now()),
                    :accion, :tabla, :id_registro,
                    CAST(:before AS JSONB), CAST(:after AS JSONB), CAST(:cambios AS JSONB), :descripcion,
                    :usuario, :ip, :ua
                )
            """),
            {
                "accion": accion,
                "tabla": str(tabla).lower() if tabla else None,
                "id_registro": str(id_registro),
                "before": json.dumps(before) if before is not None else None,
                "after": json.dumps(after) if after is not None else None,
                "cambios": json.dumps(diff) if diff is not None else None,
                "descripcion": descripcion,
                "usuario": usuario,
                "ip": ip,
                "ua": ua,
            }
        )
    except Exception as e:
        print(f"⚠ Error registrando auditoría: {e}")


@app.route('/api/auditoria', methods=['GET'])
@admin_required_api
def get_auditoria():
    """
    Listado de auditoría (más reciente primero).
    Filtros: query, desde, hasta, tabla, id_registro, limit/offset.
    'desde' y 'hasta' en formato YYYY-MM-DD (inclusive).
    """
    try:
        limit = min(int(request.args.get('limit', 100)), 500)
        offset = max(int(request.args.get('offset', 0)), 0)
        query = (request.args.get('query') or '').strip().lower()
        desde = (request.args.get('desde') or '').strip()
        hasta = (request.args.get('hasta') or '').strip()
        tabla = (request.args.get('tabla') or '').strip().lower()
        id_reg = (request.args.get('id_registro') or '').strip()
    except ValueError:
        return jsonify({"error": "Parámetros inválidos"}), 400

    def _parse(d):
        try:
            y, m, dd = map(int, d.split("-"))
            return date(y, m, dd)
        except Exception:
            return None

    d_desde = _parse(desde) if desde else None
    d_hasta = _parse(hasta) if hasta else None

    if d_desde and d_hasta and d_desde > d_hasta:
        d_desde, d_hasta = d_hasta, d_desde

    sql = """
        SELECT
            id,
            to_char(fecha, 'DD/MM/YYYY HH24:MI') AS fecha_formateada,
            usuario,
            accion,
            tabla_afectada,
            id_registro,
            descripcion,
            datos_anteriores,
            datos_nuevos,
            ip_origen,
            user_agent
        FROM auditoria
        WHERE 1=1
    """
    params = {}

    if query:
        sql += """
            AND (
                LOWER(COALESCE(usuario,''))        LIKE :q OR
                LOWER(COALESCE(accion,''))         LIKE :q OR
                LOWER(COALESCE(tabla_afectada,'')) LIKE :q OR
                LOWER(COALESCE(id_registro,''))    LIKE :q OR
                LOWER(COALESCE(descripcion,''))    LIKE :q
            )
        """
        params["q"] = f"%{query}%"

    if d_desde:
        sql += " AND fecha >= CAST(:desde AS DATE) "
        params["desde"] = d_desde.isoformat()

    if d_hasta:
        sql += " AND fecha < (CAST(:hasta AS DATE) + INTERVAL '1 day') "
        params["hasta"] = d_hasta.isoformat()

    if tabla:
        sql += " AND LOWER(BTRIM(COALESCE(tabla_afectada,''))) = :tabla "
        params["tabla"] = tabla

    if id_reg:
        sql += " AND id_registro = :id_registro "
        params["id_registro"] = str(id_reg)

    sql += " ORDER BY auditoria.fecha DESC, auditoria.id DESC LIMIT :limit OFFSET :offset "
    params["limit"] = limit
    params["offset"] = offset

    print("[/api/auditoria] SQL params:", params)

    try:
        with db.engine.connect() as conn:
            result = conn.execute(text(sql), params)
            data = []

            for row in result:
                item = dict(row._mapping)
                item["fecha"] = item.pop("fecha_formateada")
                data.append(item)

        print("[/api/auditoria] rows:", len(data))
        return jsonify(data), 200
    except Exception as e:
        print("[/api/auditoria] ERROR:", e)
        return jsonify({"error": str(e)}), 500


# --- vista para ver la auditoría ---
@app.route("/auditoria")
def vista_auditoria():
    return render_template("auditoria.html")

@app.route("/api/subdependencias_por_anexo/<anexo_id>")
def subdependencias_por_anexo(anexo_id):
    conn = db.engine.raw_connection()
    cur = conn.cursor()

    try:
        if anexo_id.strip().lower() == "todos":
            cur.execute("SELECT id, nombre FROM subdependencias ORDER BY nombre ASC")
        else:
            cur.execute(
                "SELECT id, nombre FROM subdependencias WHERE id_anexo = %s ORDER BY nombre ASC",
                (anexo_id,)
            )

        data = cur.fetchall()
        subdependencias = [{"id": row[0], "nombre": row[1]} for row in data]
        return jsonify(subdependencias)

    except Exception as e:
        print("⚠️ Error al obtener subdependencias:", e)
        return jsonify({"error": str(e)}), 500

    finally:
        conn.close()



#--------- busca por clase (legacy / impresora)
@app.route('/api/buscar-clase', methods=['GET'])
def buscar_clase():
    _ensure_nomenclador_encoding()
    query = request.args.get('query', '', type=str)

    if not query:
        return jsonify({'error': 'Falta el parámetro query'}), 400

    clases = ClaseBien.query.filter(
        ClaseBien.descripcion.ilike(f'%{query}%')
    ).order_by(ClaseBien.descripcion).all()

    data = [{
        'id_clase': c.id_clase,
        'descripcion': _limpiar_texto_nomenclador(c.descripcion),
        'id_rubro': c.id_rubro
    } for c in clases]

    return jsonify(data)


#------- busca por id de clase
@app.route('/api/clase/<int:id_clase>', methods=['GET'])
def obtener_clase_por_id(id_clase):
    _ensure_nomenclador_encoding()
    clase = ClaseBien.query.get(id_clase)

    if not clase:
        return jsonify({'error': 'Clase no encontrada'}), 404

    rubro = Rubro.query.get(clase.id_rubro)

    return jsonify({
        'id_clase': clase.id_clase,
        'descripcion': _limpiar_texto_nomenclador(clase.descripcion),
        'id_rubro': clase.id_rubro,
        'rubro': _limpiar_texto_nomenclador(rubro.nombre) if rubro else 'Sin rubro'
    })

#Editar anexos y subdependencias -------------------------------------------------------------------
from flask import request, jsonify
from sqlalchemy.exc import IntegrityError

@app.route('/editaranexos')
def editar_anexos():
    return render_template('editaranexos.html')



# ======================
# EDITAR ANEXOS
# ======================
@app.route('/api/anexos/<int:id>', methods=['PUT', 'PATCH'])
@admin_required_api
def editar_anexo(id):
    try:
        data = request.get_json(silent=True) or {}
        anexo = db.session.get(Anexo, id)
        if not anexo:
            return jsonify({'error': 'Anexo no encontrado'}), 404

        # No permitimos cambiar el ID por seguridad/consistencia
        if 'id' in data and data['id'] != id:
            return jsonify({'error': 'No se permite cambiar el ID del anexo'}), 400

        if 'nombre' in data:
            anexo.nombre = (data['nombre'] or '').strip()
        if 'direccion' in data:
            anexo.direccion = (data['direccion'] or '').strip()

        db.session.commit()
        return jsonify({
            'mensaje': 'Anexo actualizado correctamente',
            'anexo': {'id': anexo.id, 'nombre': anexo.nombre, 'direccion': anexo.direccion}
        }), 200

    except IntegrityError as e:
        db.session.rollback()
        return jsonify({'error': 'Conflicto de integridad de datos', 'detalle': str(e.orig)}), 409
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500


# ======================
# EDITAR SUBDEPENDENCIAS
# ======================
@app.route('/api/subdependencias/<int:id>', methods=['PUT', 'PATCH'])
@admin_required_api
def editar_subdependencia(id):
    try:
        data = request.get_json(silent=True) or {}
        sub = db.session.get(Subdependencia, id)
        if not sub:
            return jsonify({'error': 'Subdependencia no encontrada'}), 404

        # No permitimos cambiar el ID por seguridad/consistencia
        if 'id' in data and data['id'] != id:
            return jsonify({'error': 'No se permite cambiar el ID de la subdependencia'}), 400

        # Validar cambio de anexo (FK) si viene
        if 'id_anexo' in data and data['id_anexo'] is not None:
            anexo_destino = db.session.get(Anexo, data['id_anexo'])
            if not anexo_destino:
                return jsonify({'error': 'El anexo destino no existe'}), 400
            sub.id_anexo = data['id_anexo']

        if 'nombre' in data:
            sub.nombre = (data['nombre'] or '').strip()

        db.session.commit()
        return jsonify({
            'mensaje': 'Subdependencia actualizada correctamente',
            'subdependencia': {'id': sub.id, 'id_anexo': sub.id_anexo, 'nombre': sub.nombre}
        }), 200

    except IntegrityError as e:
        db.session.rollback()
        return jsonify({'error': 'Conflicto de integridad de datos', 'detalle': str(e.orig)}), 409
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500
#---------------------------------------------------------------------------------------------------------


# API para AGREGAR anexos
@app.route('/api/anexos', methods=['POST'])
@admin_required_api
def agregar_anexo():
    data = request.json
    nuevo_anexo = Anexo(id=data['id'], nombre=data['nombre'], direccion=data.get('direccion'))
    db.session.add(nuevo_anexo)
    db.session.commit()
    return jsonify({'mensaje': 'Anexo agregado correctamente'}), 201

# API para obtener todos los anexos
@app.route('/api/anexos', methods=['GET'])
def obtener_anexos():
    anexos = Anexo.query.all()
    return jsonify([{'id': anexo.id, 'nombre': anexo.nombre} for anexo in anexos])

# --- SUBDEPENDENCIAS ---
@app.route('/api/subdependencias', methods=['POST'])
@admin_required_api
def agregar_subdependencia():
    data = request.json
    nueva_subdependencia = Subdependencia(id=data['id'], id_anexo=data['id_anexo'], nombre=data['nombre'])
    db.session.add(nueva_subdependencia)
    db.session.commit()
    return jsonify({'mensaje': 'Subdependencia agregada correctamente'}), 201


# API para obtener todas las subdependencias
@app.route('/api/anexos/<int:id_anexo>/subdependencias', methods=['GET'])
def obtener_subdependencias(id_anexo):
    subdependencias = Subdependencia.query.filter_by(id_anexo=id_anexo).all()
    return jsonify([{'id': sub.id, 'nombre': sub.nombre} for sub in subdependencias])


        


# Eliminar anexo------------------------------------------------------
@app.route('/api/anexos/<int:id>', methods=['DELETE'])
@admin_required_api
def eliminar_anexo(id):
    try:
        anexo = db.session.get(Anexo, id)
        if not anexo:
            return jsonify({'error': 'Anexo no encontrado'}), 404
        db.session.delete(anexo)
        db.session.commit()
        return jsonify({'mensaje': 'Anexo eliminado correctamente'}), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

# Eliminar subdependencia---------------------------------------------
@app.route('/api/subdependencias/<int:id>', methods=['DELETE'])
@admin_required_api
def eliminar_subdependencia(id):
    try:
        sub = db.session.get(Subdependencia, id)
        if not sub:
            return jsonify({'error': 'Subdependencia no encontrada'}), 404
        db.session.delete(sub)
        db.session.commit()
        return jsonify({'mensaje': 'Subdependencia eliminada correctamente'}), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500


# =============================================================================
# API NUEVA: RELEVAMIENTOS
# -----------------------------------------------------------------------------
# Bloque agregado para registrar fechas de relevamiento completado sin modificar
# las tablas existentes de anexos, subdependencias ni mobiliario.
# Crea y usa tablas independientes:
#   - relevamientos_anexos
#   - relevamientos_anexos_historial
#   - relevamientos_subdependencias
#   - relevamientos_subdependencias_historial
# =============================================================================

def _ensure_relevamientos_tables():
    db.session.execute(text("""
        CREATE TABLE IF NOT EXISTS relevamientos_anexos (
            id_anexo INTEGER PRIMARY KEY REFERENCES anexos(id) ON DELETE CASCADE,
            fecha_completado DATE NOT NULL,
            fecha_creacion TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP,
            fecha_actualizacion TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP
        )
    """))
    db.session.execute(text("""
        CREATE TABLE IF NOT EXISTS relevamientos_anexos_historial (
            id SERIAL PRIMARY KEY,
            id_anexo INTEGER NOT NULL REFERENCES anexos(id) ON DELETE CASCADE,
            fecha_completado DATE NOT NULL,
            comentario TEXT,
            fecha_creacion TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP
        )
    """))
    db.session.execute(text("""
        CREATE TABLE IF NOT EXISTS relevamientos_subdependencias (
            id_subdependencia INTEGER PRIMARY KEY REFERENCES subdependencias(id) ON DELETE CASCADE,
            fecha_completado DATE NOT NULL,
            fecha_creacion TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP,
            fecha_actualizacion TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP
        )
    """))
    db.session.execute(text("""
        CREATE TABLE IF NOT EXISTS relevamientos_subdependencias_historial (
            id SERIAL PRIMARY KEY,
            id_subdependencia INTEGER NOT NULL REFERENCES subdependencias(id) ON DELETE CASCADE,
            fecha_completado DATE NOT NULL,
            comentario TEXT,
            fecha_creacion TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP
        )
    """))
    db.session.execute(text("""
        CREATE INDEX IF NOT EXISTS idx_relevamientos_anexos_historial_anexo
        ON relevamientos_anexos_historial (id_anexo, fecha_completado DESC, fecha_creacion DESC)
    """))
    db.session.execute(text("""
        CREATE INDEX IF NOT EXISTS idx_relevamientos_subdependencias_historial_subdependencia
        ON relevamientos_subdependencias_historial (
            id_subdependencia,
            fecha_completado DESC,
            fecha_creacion DESC
        )
    """))
    db.session.execute(text("""
        INSERT INTO relevamientos_anexos_historial (id_anexo, fecha_completado)
        SELECT ra.id_anexo, ra.fecha_completado
        FROM relevamientos_anexos ra
        WHERE NOT EXISTS (
            SELECT 1
            FROM relevamientos_anexos_historial rah
            WHERE rah.id_anexo = ra.id_anexo
              AND rah.fecha_completado = ra.fecha_completado
        )
    """))
    db.session.execute(text("""
        INSERT INTO relevamientos_subdependencias_historial (
            id_subdependencia,
            fecha_completado
        )
        SELECT rs.id_subdependencia, rs.fecha_completado
        FROM relevamientos_subdependencias rs
        WHERE NOT EXISTS (
            SELECT 1
            FROM relevamientos_subdependencias_historial rsh
            WHERE rsh.id_subdependencia = rs.id_subdependencia
              AND rsh.fecha_completado = rs.fecha_completado
        )
    """))
    db.session.commit()


def _parse_fecha_relevamiento(data):
    fecha = str(data.get("fecha_completado") or data.get("fecha") or "").strip()
    if not fecha:
        raise ValueError("Falta la fecha de relevamiento")
    try:
        return datetime.strptime(fecha, "%Y-%m-%d").date()
    except ValueError:
        raise ValueError("La fecha debe tener formato YYYY-MM-DD")


def _parse_comentario_relevamiento(data):
    comentario = str(data.get("comentario") or "").strip()
    return comentario or None


def _fecha_iso(value):
    if value is None:
        return None
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


def _relevamiento_anexo_to_dict(row):
    return {
        "id_anexo": row["id_anexo"],
        "fecha_completado": _fecha_iso(row["fecha_completado"]),
        "fecha_creacion": _fecha_iso(row["fecha_creacion"]),
        "fecha_actualizacion": _fecha_iso(row["fecha_actualizacion"]),
    }


def _relevamiento_subdependencia_to_dict(row):
    return {
        "id_subdependencia": row["id_subdependencia"],
        "id_anexo": row["id_anexo"] if "id_anexo" in row else None,
        "fecha_completado": _fecha_iso(row["fecha_completado"]),
        "fecha_creacion": _fecha_iso(row["fecha_creacion"]),
        "fecha_actualizacion": _fecha_iso(row["fecha_actualizacion"]),
    }


def _historial_relevamiento_anexo_to_dict(row):
    return {
        "id": row["id"],
        "id_anexo": row["id_anexo"],
        "fecha_completado": _fecha_iso(row["fecha_completado"]),
        "comentario": row["comentario"],
        "fecha_creacion": _fecha_iso(row["fecha_creacion"]),
    }


def _historial_relevamiento_subdependencia_to_dict(row):
    return {
        "id": row["id"],
        "id_subdependencia": row["id_subdependencia"],
        "id_anexo": row["id_anexo"] if "id_anexo" in row else None,
        "fecha_completado": _fecha_iso(row["fecha_completado"]),
        "comentario": row["comentario"],
        "fecha_creacion": _fecha_iso(row["fecha_creacion"]),
    }


@app.route('/api/relevamientos/anexos', methods=['GET'])
def obtener_relevamientos_anexos():
    try:
        _ensure_relevamientos_tables()
        rows = db.session.execute(text("""
            SELECT id_anexo, fecha_completado, fecha_creacion, fecha_actualizacion
            FROM relevamientos_anexos
            ORDER BY id_anexo ASC
        """)).mappings().all()
        return jsonify([_relevamiento_anexo_to_dict(row) for row in rows])
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500


@app.route('/api/relevamientos/anexos/<int:id_anexo>', methods=['PUT'])
@admin_required_api
def guardar_relevamiento_anexo(id_anexo):
    try:
        _ensure_relevamientos_tables()
        if not db.session.get(Anexo, id_anexo):
            return jsonify({"error": "Anexo no encontrado"}), 404

        data = request.get_json(silent=True) or {}
        fecha = _parse_fecha_relevamiento(data)
        comentario = _parse_comentario_relevamiento(data)
        row = db.session.execute(text("""
            INSERT INTO relevamientos_anexos (id_anexo, fecha_completado)
            VALUES (:id_anexo, :fecha_completado)
            ON CONFLICT (id_anexo) DO UPDATE SET
                fecha_completado = EXCLUDED.fecha_completado,
                fecha_actualizacion = CURRENT_TIMESTAMP
            RETURNING id_anexo, fecha_completado, fecha_creacion, fecha_actualizacion
        """), {
            "id_anexo": id_anexo,
            "fecha_completado": fecha,
        }).mappings().first()
        db.session.execute(text("""
            INSERT INTO relevamientos_anexos_historial (
                id_anexo,
                fecha_completado,
                comentario
            )
            SELECT :id_anexo, :fecha_completado, :comentario
            WHERE NOT EXISTS (
                SELECT 1
                FROM relevamientos_anexos_historial rah
                WHERE rah.id_anexo = :id_anexo
                  AND rah.fecha_completado = :fecha_completado
                  AND COALESCE(rah.comentario, '') = COALESCE(:comentario, '')
            )
        """), {
            "id_anexo": id_anexo,
            "fecha_completado": fecha,
            "comentario": comentario,
        })
        db.session.commit()
        return jsonify(_relevamiento_anexo_to_dict(row)), 200
    except ValueError as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500


@app.route('/api/relevamientos/anexos/<int:id_anexo>', methods=['DELETE'])
@admin_required_api
def eliminar_relevamiento_anexo(id_anexo):
    try:
        _ensure_relevamientos_tables()
        db.session.execute(text("""
            DELETE FROM relevamientos_anexos
            WHERE id_anexo = :id_anexo
        """), {"id_anexo": id_anexo})
        db.session.commit()
        return jsonify({"mensaje": "Relevamiento de anexo eliminado"}), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500


@app.route('/api/relevamientos/anexos/<int:id_anexo>/historial', methods=['GET'])
def obtener_historial_relevamientos_anexo(id_anexo):
    try:
        _ensure_relevamientos_tables()
        rows = db.session.execute(text("""
            SELECT id, id_anexo, fecha_completado, comentario, fecha_creacion
            FROM relevamientos_anexos_historial
            WHERE id_anexo = :id_anexo
            ORDER BY fecha_completado DESC, fecha_creacion DESC, id DESC
        """), {"id_anexo": id_anexo}).mappings().all()
        return jsonify([_historial_relevamiento_anexo_to_dict(row) for row in rows])
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500


@app.route('/api/relevamientos/anexos/<int:id_anexo>/subdependencias', methods=['GET'])
def obtener_relevamientos_subdependencias_por_anexo(id_anexo):
    try:
        _ensure_relevamientos_tables()
        rows = db.session.execute(text("""
            SELECT
                rs.id_subdependencia,
                s.id_anexo,
                rs.fecha_completado,
                rs.fecha_creacion,
                rs.fecha_actualizacion
            FROM relevamientos_subdependencias rs
            JOIN subdependencias s ON s.id = rs.id_subdependencia
            WHERE s.id_anexo = :id_anexo
            ORDER BY rs.id_subdependencia ASC
        """), {"id_anexo": id_anexo}).mappings().all()
        return jsonify([_relevamiento_subdependencia_to_dict(row) for row in rows])
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500


@app.route('/api/relevamientos/subdependencias/<int:id_subdependencia>/historial', methods=['GET'])
def obtener_historial_relevamientos_subdependencia(id_subdependencia):
    try:
        _ensure_relevamientos_tables()
        rows = db.session.execute(text("""
            SELECT
                rh.id,
                rh.id_subdependencia,
                s.id_anexo,
                rh.fecha_completado,
                rh.comentario,
                rh.fecha_creacion
            FROM relevamientos_subdependencias_historial rh
            JOIN subdependencias s ON s.id = rh.id_subdependencia
            WHERE rh.id_subdependencia = :id_subdependencia
            ORDER BY rh.fecha_completado DESC, rh.fecha_creacion DESC, rh.id DESC
        """), {"id_subdependencia": id_subdependencia}).mappings().all()
        return jsonify([_historial_relevamiento_subdependencia_to_dict(row) for row in rows])
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500


@app.route('/api/relevamientos/subdependencias/<int:id_subdependencia>', methods=['PUT'])
@admin_required_api
def guardar_relevamiento_subdependencia(id_subdependencia):
    try:
        _ensure_relevamientos_tables()
        if not db.session.get(Subdependencia, id_subdependencia):
            return jsonify({"error": "Subdependencia no encontrada"}), 404

        data = request.get_json(silent=True) or {}
        fecha = _parse_fecha_relevamiento(data)
        comentario = _parse_comentario_relevamiento(data)
        row = db.session.execute(text("""
            INSERT INTO relevamientos_subdependencias (id_subdependencia, fecha_completado)
            VALUES (:id_subdependencia, :fecha_completado)
            ON CONFLICT (id_subdependencia) DO UPDATE SET
                fecha_completado = EXCLUDED.fecha_completado,
                fecha_actualizacion = CURRENT_TIMESTAMP
            RETURNING id_subdependencia, fecha_completado, fecha_creacion, fecha_actualizacion
        """), {
            "id_subdependencia": id_subdependencia,
            "fecha_completado": fecha,
        }).mappings().first()
        db.session.execute(text("""
            INSERT INTO relevamientos_subdependencias_historial (
                id_subdependencia,
                fecha_completado,
                comentario
            )
            SELECT :id_subdependencia, :fecha_completado, :comentario
            WHERE NOT EXISTS (
                SELECT 1
                FROM relevamientos_subdependencias_historial rsh
                WHERE rsh.id_subdependencia = :id_subdependencia
                  AND rsh.fecha_completado = :fecha_completado
                  AND COALESCE(rsh.comentario, '') = COALESCE(:comentario, '')
            )
        """), {
            "id_subdependencia": id_subdependencia,
            "fecha_completado": fecha,
            "comentario": comentario,
        })
        db.session.commit()
        return jsonify(_relevamiento_subdependencia_to_dict(row)), 200
    except ValueError as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500


@app.route('/api/relevamientos/subdependencias/<int:id_subdependencia>', methods=['DELETE'])
@admin_required_api
def eliminar_relevamiento_subdependencia(id_subdependencia):
    try:
        _ensure_relevamientos_tables()
        db.session.execute(text("""
            DELETE FROM relevamientos_subdependencias
            WHERE id_subdependencia = :id_subdependencia
        """), {"id_subdependencia": id_subdependencia})
        db.session.commit()
        return jsonify({"mensaje": "Relevamiento de subdependencia eliminado"}), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500

# =============================================================================
# API NUEVA: MATAFUEGOS
# -----------------------------------------------------------------------------
# Modulo independiente para controlar matafuegos por anexo/subdependencia sin
# modificar las tablas existentes de anexos, subdependencias ni mobiliario.
# Permite vincular registros ya cargados como mobiliario mediante id_mobiliario.
# =============================================================================

def _ensure_matafuegos_tables():
    _ensure_mobiliario_foto_2_column()
    db.session.execute(text("""
        CREATE TABLE IF NOT EXISTS matafuegos (
            id SERIAL PRIMARY KEY,
            id_anexo INTEGER NOT NULL REFERENCES anexos(id) ON DELETE CASCADE,
            id_subdependencia INTEGER REFERENCES subdependencias(id) ON DELETE SET NULL,
            id_mobiliario VARCHAR(50) REFERENCES mobiliario(id) ON DELETE SET NULL,
            codigo VARCHAR(80),
            ubicacion_detalle TEXT,
            tipo VARCHAR(80),
            capacidad VARCHAR(80),
            fecha_vencimiento DATE,
            fecha_control DATE,
            estado VARCHAR(20) DEFAULT 'activo' NOT NULL,
            observaciones TEXT,
            fecha_creacion TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP,
            fecha_actualizacion TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP
        )
    """))
    db.session.execute(text("ALTER TABLE IF EXISTS matafuegos ADD COLUMN IF NOT EXISTS id_anexo INTEGER"))
    db.session.execute(text("ALTER TABLE IF EXISTS matafuegos ADD COLUMN IF NOT EXISTS id_subdependencia INTEGER"))
    db.session.execute(text("ALTER TABLE IF EXISTS matafuegos ADD COLUMN IF NOT EXISTS id_mobiliario VARCHAR(50)"))
    db.session.execute(text("ALTER TABLE IF EXISTS matafuegos ADD COLUMN IF NOT EXISTS codigo VARCHAR(80)"))
    db.session.execute(text("ALTER TABLE IF EXISTS matafuegos ADD COLUMN IF NOT EXISTS ubicacion_detalle TEXT"))
    db.session.execute(text("ALTER TABLE IF EXISTS matafuegos ADD COLUMN IF NOT EXISTS tipo VARCHAR(80)"))
    db.session.execute(text("ALTER TABLE IF EXISTS matafuegos ADD COLUMN IF NOT EXISTS capacidad VARCHAR(80)"))
    db.session.execute(text("ALTER TABLE IF EXISTS matafuegos ADD COLUMN IF NOT EXISTS fecha_vencimiento DATE"))
    db.session.execute(text("ALTER TABLE IF EXISTS matafuegos ADD COLUMN IF NOT EXISTS fecha_control DATE"))
    db.session.execute(text("ALTER TABLE IF EXISTS matafuegos ADD COLUMN IF NOT EXISTS estado VARCHAR(20) DEFAULT 'activo' NOT NULL"))
    db.session.execute(text("ALTER TABLE IF EXISTS matafuegos ADD COLUMN IF NOT EXISTS observaciones TEXT"))
    db.session.execute(text("ALTER TABLE IF EXISTS matafuegos ADD COLUMN IF NOT EXISTS fecha_creacion TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP"))
    db.session.execute(text("ALTER TABLE IF EXISTS matafuegos ADD COLUMN IF NOT EXISTS fecha_actualizacion TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP"))
    db.session.execute(text("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_matafuegos_id_mobiliario
        ON matafuegos (id_mobiliario)
        WHERE id_mobiliario IS NOT NULL
    """))
    db.session.execute(text("""
        CREATE INDEX IF NOT EXISTS idx_matafuegos_anexo
        ON matafuegos (id_anexo, estado, fecha_vencimiento)
    """))
    db.session.execute(text("""
        CREATE INDEX IF NOT EXISTS idx_matafuegos_subdependencia
        ON matafuegos (id_subdependencia)
    """))
    db.session.commit()


def _parse_fecha_matafuego(data, key, required=False):
    fecha = str(data.get(key) or "").strip()
    if not fecha:
        if required:
            raise ValueError(f"Falta la fecha {key}")
        return None
    try:
        return datetime.strptime(fecha, "%Y-%m-%d").date()
    except ValueError:
        raise ValueError("La fecha debe tener formato YYYY-MM-DD")


def _text_or_none(value):
    clean = str(value or "").strip()
    return clean or None


def _estado_matafuego(value):
    clean = str(value or "activo").strip().lower()
    return clean if clean in {"activo", "inactivo"} else "activo"


def _dias_matafuego(fecha_vencimiento):
    if not fecha_vencimiento:
        return None
    today = datetime.now(pytz.timezone("America/Argentina/Buenos_Aires")).date()
    return (fecha_vencimiento - today).days


def _alerta_matafuego(fecha_vencimiento, estado="activo"):
    if estado != "activo":
        return "inactivo"
    dias = _dias_matafuego(fecha_vencimiento)
    if dias is None:
        return "sin_fecha"
    if dias < 0:
        return "vencido"
    if dias <= 10:
        return "vence_10"
    if dias <= 30:
        return "vence_30"
    if dias <= 90:
        return "vence_90"
    return "vigente"


MATAFUEGOS_SELECT = """
    SELECT
        mf.id,
        mf.id_anexo,
        a.nombre AS anexo,
        mf.id_subdependencia,
        s.nombre AS subdependencia,
        mf.id_mobiliario,
        m.descripcion AS descripcion_mobiliario,
        m.foto_url AS foto_url,
        m.foto_url_2 AS foto_url_2,
        mf.codigo,
        mf.ubicacion_detalle,
        mf.tipo,
        mf.capacidad,
        mf.fecha_vencimiento,
        mf.fecha_control,
        mf.estado,
        mf.observaciones,
        mf.fecha_creacion,
        mf.fecha_actualizacion
    FROM matafuegos mf
    JOIN anexos a ON a.id = mf.id_anexo
    LEFT JOIN subdependencias s ON s.id = mf.id_subdependencia
    LEFT JOIN mobiliario m ON m.id = mf.id_mobiliario
"""


def _matafuego_to_dict(row):
    fecha_vencimiento = row["fecha_vencimiento"]
    dias = _dias_matafuego(fecha_vencimiento)
    return {
        "id": row["id"],
        "id_anexo": row["id_anexo"],
        "anexo": row["anexo"],
        "id_subdependencia": row["id_subdependencia"],
        "subdependencia": row["subdependencia"],
        "id_mobiliario": row["id_mobiliario"],
        "descripcion_mobiliario": row["descripcion_mobiliario"],
        "foto_url": row["foto_url"],
        "foto_url_2": row["foto_url_2"],
        "codigo": row["codigo"],
        "ubicacion_detalle": row["ubicacion_detalle"],
        "tipo": row["tipo"],
        "capacidad": row["capacidad"],
        "fecha_vencimiento": _fecha_iso(fecha_vencimiento),
        "fecha_control": _fecha_iso(row["fecha_control"]),
        "estado": row["estado"],
        "observaciones": row["observaciones"],
        "dias_para_vencer": dias,
        "alerta": _alerta_matafuego(fecha_vencimiento, row["estado"]),
        "fecha_creacion": _fecha_iso(row["fecha_creacion"]),
        "fecha_actualizacion": _fecha_iso(row["fecha_actualizacion"]),
    }


def _matafuego_row(id_matafuego):
    return db.session.execute(text(f"""
        {MATAFUEGOS_SELECT}
        WHERE mf.id = :id_matafuego
    """), {"id_matafuego": id_matafuego}).mappings().first()


def _validar_ubicacion_matafuego(data):
    id_anexo = data.get("id_anexo")
    id_subdependencia = data.get("id_subdependencia")

    if id_subdependencia in ("", None):
        id_subdependencia = None
    else:
        id_subdependencia = int(id_subdependencia)
        sub = db.session.get(Subdependencia, id_subdependencia)
        if not sub:
            raise ValueError("Subdependencia no encontrada")
        id_anexo = sub.id_anexo

    if id_anexo in ("", None):
        raise ValueError("Falta el anexo")

    id_anexo = int(id_anexo)
    if not db.session.get(Anexo, id_anexo):
        raise ValueError("Anexo no encontrado")

    return id_anexo, id_subdependencia


def _matafuegos_resumen_from_rows(rows):
    resumen = {}
    for row in rows:
        id_anexo = row["id_anexo"]
        item = resumen.setdefault(id_anexo, {
            "id_anexo": id_anexo,
            "total": 0,
            "vencidos": 0,
            "vence_10": 0,
            "vence_30": 0,
            "vence_90": 0,
            "sin_fecha": 0,
            "vigentes": 0,
            "criticos": 0,
            "alerta_principal": "sin_matafuegos",
        })
        alerta = _alerta_matafuego(row["fecha_vencimiento"], row["estado"])
        item["total"] += 1
        if alerta == "vencido":
            item["vencidos"] += 1
        elif alerta == "vence_10":
            item["vence_10"] += 1
        elif alerta == "vence_30":
            item["vence_30"] += 1
        elif alerta == "vence_90":
            item["vence_90"] += 1
        elif alerta == "sin_fecha":
            item["sin_fecha"] += 1
        elif alerta == "vigente":
            item["vigentes"] += 1
        item["criticos"] = item["vencidos"] + item["vence_10"]

    prioridad = ["vencido", "vence_10", "vence_30", "vence_90", "sin_fecha", "vigente"]
    for item in resumen.values():
        for alerta in prioridad:
            if (
                (alerta == "vencido" and item["vencidos"])
                or (alerta == "vence_10" and item["vence_10"])
                or (alerta == "vence_30" and item["vence_30"])
                or (alerta == "vence_90" and item["vence_90"])
                or (alerta == "sin_fecha" and item["sin_fecha"])
                or (alerta == "vigente" and item["vigentes"])
            ):
                item["alerta_principal"] = alerta
                break
    return resumen


@app.route('/api/matafuegos/anexos/resumen', methods=['GET'])
def obtener_resumen_matafuegos_anexos():
    try:
        _ensure_matafuegos_tables()
        rows = db.session.execute(text("""
            SELECT id_anexo, fecha_vencimiento, estado
            FROM matafuegos
            WHERE estado <> 'inactivo'
        """)).mappings().all()
        return jsonify(list(_matafuegos_resumen_from_rows(rows).values()))
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500


@app.route('/api/matafuegos/anexos/<int:id_anexo>', methods=['GET'])
def obtener_matafuegos_por_anexo(id_anexo):
    try:
        _ensure_matafuegos_tables()
        if not db.session.get(Anexo, id_anexo):
            return jsonify({"error": "Anexo no encontrado"}), 404
        incluir_inactivos = str(request.args.get("incluir_inactivos") or "").lower() == "true"
        where_estado = "" if incluir_inactivos else "AND mf.estado <> 'inactivo'"
        rows = db.session.execute(text(f"""
            {MATAFUEGOS_SELECT}
            WHERE mf.id_anexo = :id_anexo
            {where_estado}
            ORDER BY
                mf.fecha_vencimiento ASC NULLS LAST,
                mf.id ASC
        """), {"id_anexo": id_anexo}).mappings().all()
        return jsonify([_matafuego_to_dict(row) for row in rows])
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500


@app.route('/api/matafuegos/candidatos', methods=['GET'])
def obtener_candidatos_matafuegos():
    try:
        _ensure_matafuegos_tables()
        id_anexo = request.args.get("anexo_id", type=int)
        params = {}
        where_anexo = ""
        if id_anexo:
            where_anexo = "AND a.id = :id_anexo"
            params["id_anexo"] = id_anexo

        rows = db.session.execute(text(f"""
            SELECT
                m.id AS id_mobiliario,
                m.descripcion,
                m.foto_url,
                m.foto_url_2,
                m.comentarios,
                sd.id AS id_subdependencia,
                sd.nombre AS subdependencia,
                a.id AS id_anexo,
                a.nombre AS anexo,
                cb.descripcion AS clase_bien,
                r.nombre AS rubro
            FROM mobiliario m
            JOIN subdependencias sd ON sd.id = m.ubicacion_id
            JOIN anexos a ON a.id = sd.id_anexo
            LEFT JOIN clases_bienes cb ON cb.id_clase = m.clase_bien_id
            LEFT JOIN rubros r ON r.id_rubro = m.rubro_id
            LEFT JOIN matafuegos mf ON mf.id_mobiliario = m.id
            WHERE mf.id IS NULL
              {where_anexo}
              AND (
                LOWER(COALESCE(m.descripcion, '')) LIKE '%matafuego%'
                OR LOWER(COALESCE(m.descripcion, '')) LIKE '%mata fuego%'
                OR LOWER(COALESCE(m.descripcion, '')) LIKE '%mata-fuego%'
                OR LOWER(COALESCE(m.descripcion, '')) LIKE '%extintor%'
                OR LOWER(COALESCE(m.descripcion, '')) LIKE '%extintores%'
                OR LOWER(COALESCE(cb.descripcion, '')) LIKE '%matafuego%'
                OR LOWER(COALESCE(cb.descripcion, '')) LIKE '%extintor%'
                OR LOWER(COALESCE(r.nombre, '')) LIKE '%matafuego%'
                OR LOWER(COALESCE(r.nombre, '')) LIKE '%extintor%'
              )
            ORDER BY a.id ASC, sd.id ASC, m.id ASC
        """), params).mappings().all()
        return jsonify([dict(row) for row in rows])
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500


@app.route('/api/matafuegos', methods=['POST'])
@admin_required_api
def crear_matafuego():
    try:
        _ensure_matafuegos_tables()
        data = request.get_json(silent=True) or {}
        id_anexo, id_subdependencia = _validar_ubicacion_matafuego(data)
        fecha_vencimiento = _parse_fecha_matafuego(data, "fecha_vencimiento")
        fecha_control = _parse_fecha_matafuego(data, "fecha_control")

        new_id = db.session.execute(text("""
            INSERT INTO matafuegos (
                id_anexo,
                id_subdependencia,
                id_mobiliario,
                codigo,
                ubicacion_detalle,
                tipo,
                capacidad,
                fecha_vencimiento,
                fecha_control,
                estado,
                observaciones
            )
            VALUES (
                :id_anexo,
                :id_subdependencia,
                :id_mobiliario,
                :codigo,
                :ubicacion_detalle,
                :tipo,
                :capacidad,
                :fecha_vencimiento,
                :fecha_control,
                :estado,
                :observaciones
            )
            RETURNING id
        """), {
            "id_anexo": id_anexo,
            "id_subdependencia": id_subdependencia,
            "id_mobiliario": _text_or_none(data.get("id_mobiliario")),
            "codigo": _text_or_none(data.get("codigo")),
            "ubicacion_detalle": _text_or_none(data.get("ubicacion_detalle")),
            "tipo": _text_or_none(data.get("tipo")),
            "capacidad": _text_or_none(data.get("capacidad")),
            "fecha_vencimiento": fecha_vencimiento,
            "fecha_control": fecha_control,
            "estado": _estado_matafuego(data.get("estado")),
            "observaciones": _text_or_none(data.get("observaciones")),
        }).scalar()
        db.session.commit()
        return jsonify(_matafuego_to_dict(_matafuego_row(new_id))), 201
    except ValueError as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500


@app.route('/api/matafuegos/<int:id_matafuego>', methods=['PUT'])
@admin_required_api
def editar_matafuego(id_matafuego):
    try:
        _ensure_matafuegos_tables()
        if not _matafuego_row(id_matafuego):
            return jsonify({"error": "Matafuego no encontrado"}), 404
        data = request.get_json(silent=True) or {}
        id_anexo, id_subdependencia = _validar_ubicacion_matafuego(data)
        fecha_vencimiento = _parse_fecha_matafuego(data, "fecha_vencimiento")
        fecha_control = _parse_fecha_matafuego(data, "fecha_control")

        db.session.execute(text("""
            UPDATE matafuegos
            SET
                id_anexo = :id_anexo,
                id_subdependencia = :id_subdependencia,
                codigo = :codigo,
                ubicacion_detalle = :ubicacion_detalle,
                tipo = :tipo,
                capacidad = :capacidad,
                fecha_vencimiento = :fecha_vencimiento,
                fecha_control = :fecha_control,
                estado = :estado,
                observaciones = :observaciones,
                fecha_actualizacion = CURRENT_TIMESTAMP
            WHERE id = :id_matafuego
        """), {
            "id_matafuego": id_matafuego,
            "id_anexo": id_anexo,
            "id_subdependencia": id_subdependencia,
            "codigo": _text_or_none(data.get("codigo")),
            "ubicacion_detalle": _text_or_none(data.get("ubicacion_detalle")),
            "tipo": _text_or_none(data.get("tipo")),
            "capacidad": _text_or_none(data.get("capacidad")),
            "fecha_vencimiento": fecha_vencimiento,
            "fecha_control": fecha_control,
            "estado": _estado_matafuego(data.get("estado")),
            "observaciones": _text_or_none(data.get("observaciones")),
        })
        db.session.commit()
        return jsonify(_matafuego_to_dict(_matafuego_row(id_matafuego))), 200
    except ValueError as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500


@app.route('/api/matafuegos/<int:id_matafuego>', methods=['DELETE'])
@admin_required_api
def desactivar_matafuego(id_matafuego):
    try:
        _ensure_matafuegos_tables()
        db.session.execute(text("""
            UPDATE matafuegos
            SET estado = 'inactivo',
                fecha_actualizacion = CURRENT_TIMESTAMP
            WHERE id = :id_matafuego
        """), {"id_matafuego": id_matafuego})
        db.session.commit()
        return jsonify({"mensaje": "Matafuego marcado como inactivo"}), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500


@app.route('/api/matafuegos/importar-mobiliario', methods=['POST'])
@admin_required_api
def importar_matafuegos_desde_mobiliario():
    try:
        _ensure_matafuegos_tables()
        data = request.get_json(silent=True) or {}
        ids = data.get("ids_mobiliario") or []
        if data.get("id_mobiliario"):
            ids.append(data.get("id_mobiliario"))
        ids = [str(item).strip() for item in ids if str(item).strip()]
        if not ids:
            return jsonify({"error": "Selecciona al menos un mobiliario"}), 400

        fecha_vencimiento = _parse_fecha_matafuego(data, "fecha_vencimiento")
        fecha_control = _parse_fecha_matafuego(data, "fecha_control")
        observaciones = _text_or_none(data.get("observaciones"))
        imported_ids = []

        for id_mobiliario in ids:
            candidate = db.session.execute(text("""
                SELECT
                    m.id AS id_mobiliario,
                    m.descripcion,
                    sd.id AS id_subdependencia,
                    sd.nombre AS subdependencia,
                    a.id AS id_anexo
                FROM mobiliario m
                JOIN subdependencias sd ON sd.id = m.ubicacion_id
                JOIN anexos a ON a.id = sd.id_anexo
                WHERE m.id = :id_mobiliario
                LIMIT 1
            """), {"id_mobiliario": id_mobiliario}).mappings().first()
            if not candidate:
                continue

            existing = db.session.execute(text("""
                SELECT id
                FROM matafuegos
                WHERE id_mobiliario = :id_mobiliario
                LIMIT 1
            """), {"id_mobiliario": id_mobiliario}).mappings().first()

            if existing:
                db.session.execute(text("""
                    UPDATE matafuegos
                    SET
                        id_anexo = :id_anexo,
                        id_subdependencia = :id_subdependencia,
                        ubicacion_detalle = COALESCE(ubicacion_detalle, :ubicacion_detalle),
                        fecha_vencimiento = COALESCE(:fecha_vencimiento, fecha_vencimiento),
                        fecha_control = COALESCE(:fecha_control, fecha_control),
                        observaciones = COALESCE(:observaciones, observaciones),
                        estado = 'activo',
                        fecha_actualizacion = CURRENT_TIMESTAMP
                    WHERE id = :id_matafuego
                """), {
                    "id_matafuego": existing["id"],
                    "id_anexo": candidate["id_anexo"],
                    "id_subdependencia": candidate["id_subdependencia"],
                    "ubicacion_detalle": candidate["subdependencia"],
                    "fecha_vencimiento": fecha_vencimiento,
                    "fecha_control": fecha_control,
                    "observaciones": observaciones,
                })
                imported_ids.append(existing["id"])
            else:
                new_id = db.session.execute(text("""
                    INSERT INTO matafuegos (
                        id_anexo,
                        id_subdependencia,
                        id_mobiliario,
                        ubicacion_detalle,
                        fecha_vencimiento,
                        fecha_control,
                        estado,
                        observaciones
                    )
                    VALUES (
                        :id_anexo,
                        :id_subdependencia,
                        :id_mobiliario,
                        :ubicacion_detalle,
                        :fecha_vencimiento,
                        :fecha_control,
                        'activo',
                        :observaciones
                    )
                    RETURNING id
                """), {
                    "id_anexo": candidate["id_anexo"],
                    "id_subdependencia": candidate["id_subdependencia"],
                    "id_mobiliario": id_mobiliario,
                    "ubicacion_detalle": candidate["subdependencia"],
                    "fecha_vencimiento": fecha_vencimiento,
                    "fecha_control": fecha_control,
                    "observaciones": observaciones,
                }).scalar()
                imported_ids.append(new_id)

        db.session.commit()
        rows = []
        for item_id in imported_ids:
            row = _matafuego_row(item_id)
            if row:
                rows.append(_matafuego_to_dict(row))
        return jsonify({"importados": rows, "cantidad": len(rows)}), 201
    except ValueError as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500


# API para obtener los registros de mobiliario-----------------------
from datetime import timedelta

@app.route('/api/mobiliario/ultimos', methods=['GET'])
def ultimos_mobiliarios():
    try:
        _ensure_mobiliario_valor_column()
        _ensure_mobiliario_foto_2_column()
        include_historial = str(request.args.get("lite") or "").lower() not in ("1", "true", "si")
        historial_select = (
            "m.historial_movimientos"
            if include_historial
            else "NULL AS historial_movimientos"
        )
        ubicacion_id = request.args.get("ubicacion_id", type=int)
        ubicacion_filter = "AND m.ubicacion_id = %s" if ubicacion_id is not None else ""
        params = (ubicacion_id,) if ubicacion_id is not None else ()
        query = f"""
        SELECT 
            m.id                      AS id_mobiliario,
            m.ubicacion_id            AS ubicacion_id,          -- ✅ agregado
            m.descripcion,
            m.estado_conservacion,
            m.estado_control,
            m.resolucion,
            m.fecha_resolucion,
            m.no_dado,
            m.para_reparacion,
            m.para_baja,
            m.faltante,
            m.sobrante,
            m.problema_etiqueta,
            m.comentarios,
            m.foto_url,
            m.foto_url_2,
            m.valor,
            m.fecha_creacion,
            m.fecha_actualizacion,
            {historial_select},
            r.nombre                  AS rubro,
            cb.descripcion            AS clase_bien,
            sd.id                     AS id_subdependencia,     -- opcional, útil para edición
            sd.nombre                 AS subdependencia,
            a.id                      AS id_anexo,              -- opcional, útil para edición
            a.nombre                  AS anexo,
            a.direccion               AS direccion_anexo
        FROM    mobiliario m
        LEFT JOIN clases_bienes   cb ON m.clase_bien_id  = cb.id_clase
        LEFT JOIN rubros           r ON m.rubro_id       = r.id_rubro
        LEFT JOIN subdependencias sd ON m.ubicacion_id   = sd.id
        LEFT JOIN anexos           a ON sd.id_anexo      = a.id
        WHERE m.id ~ '^[0-9]+$'
        {ubicacion_filter}
        ORDER BY m.id::integer DESC;
        """

        conn = db.engine.raw_connection()
        cur  = conn.cursor()
        cur.execute(query, params)
        columns = [col[0] for col in cur.description]
        results = [dict(zip(columns, row)) for row in cur.fetchall()]
        cur.close()
        conn.close()

        # ✅ Formatear fechas y procesar historial
        for r in results:
            # Convertir historial en lista
            historial = r.get("historial_movimientos")
            if historial:
                r["historial"] = [line.strip() for line in historial.split("\n") if line.strip()]
            else:
                r["historial"] = []
            del r["historial_movimientos"]

            # Formatear fechas (hora argentina)
            if r["fecha_creacion"]:
                r["fecha_creacion"] = (r["fecha_creacion"] - timedelta(hours=3)).strftime("%d/%m/%Y %H:%M")
            if r["fecha_actualizacion"]:
                r["fecha_actualizacion"] = (r["fecha_actualizacion"] - timedelta(hours=3)).strftime("%d/%m/%Y %H:%M")
            r["valor"] = _mobiliario_valor_json(r.get("valor"))

        return jsonify(results)
    except Exception as e:
        print("🔴 Error en /api/mobiliario/ultimos:", e)
        return jsonify({'error': str(e)}), 500



# ====== HELPERS DE AUDITORÍA ======
from datetime import datetime, date, timedelta
import json
from sqlalchemy import text

def _serialize(v):
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    if isinstance(v, Decimal):
        return float(v)
    return v

def model_to_dict(instance, exclude=('fecha_creacion', 'fecha_actualizacion')):
    """Convierte un modelo SQLAlchemy a dict JSON-serializable."""
    data = {}
    for col in instance.__table__.columns:
        if exclude and col.name in exclude:
            continue
        data[col.name] = _serialize(getattr(instance, col.name))
    return data

def _compute_diff(before: dict, after: dict):
    keys = set(before.keys()) | set(after.keys())
    diff = {}
    for k in sorted(keys):
        if before.get(k) != after.get(k):
            diff[k] = [before.get(k), after.get(k)]
    return diff



# nueva api de buscador avanzado
@app.route("/api/mobiliario/buscar", methods=["GET"])
def buscar_mobiliario_avanzado():
    try:
        _ensure_mobiliario_valor_column()
        _ensure_mobiliario_foto_2_column()
        q = (request.args.get("q") or "").strip()
        anexo_id = request.args.get("anexo_id", type=int)
        subdependencia_id = request.args.get("subdependencia_id", type=int)
        rubro_id = request.args.get("rubro_id", type=int)
        clase_id = request.args.get("clase_id", type=int)
        estado_conservacion = (request.args.get("estado_conservacion") or "").strip()

        flags = request.args.getlist("flag")

        page = max(request.args.get("page", default=1, type=int), 1)
        per_page = min(max(request.args.get("per_page", default=30, type=int), 1), 200)
        offset = (page - 1) * per_page

        order_by = (request.args.get("order_by") or "id").strip().lower()
        order_dir = (request.args.get("order_dir") or "desc").strip().lower()
        if order_dir not in ("asc", "desc"):
            order_dir = "desc"

        ORDER_MAP = {
            "id": "m.id::integer",
            "fecha_creacion": "m.fecha_creacion",
            "fecha_actualizacion": "m.fecha_actualizacion",
            "descripcion": "m.descripcion",
            "anexo": "a.nombre",
            "subdependencia": "sd.nombre",
            "rubro": "r.nombre",
            "clase": "cb.descripcion",
        }
        order_sql = ORDER_MAP.get(order_by, "m.id::integer")

        where = ["m.id ~ '^[0-9]+$'"]
        params = {}

        # ---- filtros exactos ----
        if anexo_id is not None:
            where.append("a.id = :anexo_id")
            params["anexo_id"] = anexo_id

        if subdependencia_id is not None:
            where.append("sd.id = :subdependencia_id")
            params["subdependencia_id"] = subdependencia_id

        if rubro_id is not None:
            where.append("m.rubro_id = :rubro_id")
            params["rubro_id"] = rubro_id

        if clase_id is not None:
            where.append("m.clase_bien_id = :clase_id")
            params["clase_id"] = clase_id

        if estado_conservacion:
            where.append("LOWER(COALESCE(m.estado_conservacion,'')) = LOWER(:estado_conservacion)")
            params["estado_conservacion"] = estado_conservacion

        # ---- flags ----
        ALLOWED_FLAGS = {
            "no_dado",
            "para_reparacion",
            "para_baja",
            "faltante",
            "sobrante",
            "problema_etiqueta",
        }
        for f in flags:
            f = (f or "").strip()
            if f in ALLOWED_FLAGS:
                where.append(f"m.{f} = TRUE")

        # ---- búsqueda texto ----
        search_rank_sql = "0"

        if q:
            q_lower = q.lower()
            params["q_like"] = f"%{q_lower}%"
            params["q_prefix"] = f"{q_lower}%"
            params["q_exact"] = q_lower

            conds = [
                "LOWER(COALESCE(m.descripcion,'')) LIKE :q_like",
                "LOWER(COALESCE(r.nombre,'')) LIKE :q_like",
                "LOWER(COALESCE(cb.descripcion,'')) LIKE :q_like",
                "LOWER(COALESCE(sd.nombre,'')) LIKE :q_like",
                "LOWER(COALESCE(a.nombre,'')) LIKE :q_like",
            ]

            if q.isdigit():
                params["q_id"] = q
                conds.append("m.id = :q_id")

            where.append("(" + " OR ".join(conds) + ")")

            # ---- ranking inteligente ----
            # menor valor = mayor prioridad
            # 1) ID exacto
            # 2) clase exacta
            # 3) descripción exacta
            # 4) clase empieza con el término
            # 5) descripción empieza con el término
            # 6) clase contiene el término
            # 7) descripción contiene el término
            # 8) rubro contiene
            # 9) subdependencia contiene
            # 10) anexo contiene
            rank_cases = []

            if q.isdigit():
                rank_cases.append("WHEN m.id = :q_id THEN 1")

            rank_cases.extend([
                "WHEN LOWER(COALESCE(cb.descripcion,'')) = :q_exact THEN 2",
                "WHEN LOWER(COALESCE(m.descripcion,'')) = :q_exact THEN 3",
                "WHEN LOWER(COALESCE(cb.descripcion,'')) LIKE :q_prefix THEN 4",
                "WHEN LOWER(COALESCE(m.descripcion,'')) LIKE :q_prefix THEN 5",
                "WHEN LOWER(COALESCE(cb.descripcion,'')) LIKE :q_like THEN 6",
                "WHEN LOWER(COALESCE(m.descripcion,'')) LIKE :q_like THEN 7",
                "WHEN LOWER(COALESCE(r.nombre,'')) LIKE :q_like THEN 8",
                "WHEN LOWER(COALESCE(sd.nombre,'')) LIKE :q_like THEN 9",
                "WHEN LOWER(COALESCE(a.nombre,'')) LIKE :q_like THEN 10",
            ])

            search_rank_sql = f"""
                CASE
                    {' '.join(rank_cases)}
                    ELSE 999
                END
            """

        where_sql = " AND ".join(where) if where else "1=1"

        base_from = """
            FROM mobiliario m
            LEFT JOIN clases_bienes   cb ON m.clase_bien_id  = cb.id_clase
            LEFT JOIN rubros          r  ON m.rubro_id       = r.id_rubro
            LEFT JOIN subdependencias sd ON m.ubicacion_id   = sd.id
            LEFT JOIN anexos          a  ON sd.id_anexo      = a.id
        """

        sql_count = f"SELECT COUNT(*) {base_from} WHERE {where_sql};"

        sql_items = f"""
            SELECT
                m.id                      AS id_mobiliario,
                m.ubicacion_id            AS ubicacion_id,
                m.descripcion,
                m.estado_conservacion,
                m.estado_control,
                m.resolucion,
                m.fecha_resolucion,
                m.no_dado,
                m.para_reparacion,
                m.para_baja,
                m.faltante,
                m.sobrante,
                m.problema_etiqueta,
                m.comentarios,
                m.foto_url,
                m.foto_url_2,
                m.valor,
                m.fecha_creacion,
                m.fecha_actualizacion,
                r.nombre                  AS rubro,
                cb.descripcion            AS clase_bien,
                sd.id                     AS id_subdependencia,
                sd.nombre                 AS subdependencia,
                a.id                      AS id_anexo,
                a.nombre                  AS anexo,
                a.direccion               AS direccion_anexo,
                {search_rank_sql}         AS search_rank
            {base_from}
            WHERE {where_sql}
            ORDER BY
                search_rank ASC,
                {order_sql} {order_dir},
                m.id::integer {order_dir}
            LIMIT :limit OFFSET :offset;
        """

        params_items = dict(params)
        params_items["limit"] = per_page
        params_items["offset"] = offset

        with db.engine.connect() as conn:
            total = conn.execute(text(sql_count), params).scalar() or 0
            rows = conn.execute(text(sql_items), params_items).mappings().all()
            items = [dict(r) for r in rows]

        for it in items:
            if it.get("fecha_creacion"):
                it["fecha_creacion"] = (it["fecha_creacion"] - timedelta(hours=3)).strftime("%d/%m/%Y %H:%M")
            if it.get("fecha_actualizacion"):
                it["fecha_actualizacion"] = (it["fecha_actualizacion"] - timedelta(hours=3)).strftime("%d/%m/%Y %H:%M")
            it["valor"] = _mobiliario_valor_json(it.get("valor"))

            # no hace falta mandarlo al front
            if "search_rank" in it:
                del it["search_rank"]

        return jsonify({
            "items": items,
            "meta": {
                "total": int(total),
                "page": int(page),
                "per_page": int(per_page),
                "pages": int((total + per_page - 1) // per_page) if per_page else 1,
                "order_by": order_by,
                "order_dir": order_dir,
            }
        }), 200

    except Exception as e:
        print("🔴 Error en /api/mobiliario/buscar:", e)
        return jsonify({"error": str(e)}), 500

# ====== API para eliminar un registro de patrimonio -----------------------------
@app.route('/api/patrimonio/<string:id>', methods=['DELETE'])
@hernan_required_api
def eliminar_patrimonio(id):
    try:
        _ensure_mobiliario_foto_2_column()
        registro = db.session.get(Mobiliario, id)
        if not registro:
            return jsonify({'error': 'Registro no encontrado'}), 404

        # Snapshot ANTES para auditoría
        datos_previos = model_to_dict(registro)

        # Eliminar
        db.session.delete(registro)

        # Auditoría
        registrar_auditoria(
            accion="DELETE",
            tabla="mobiliario",
            id_registro=id,
            before=datos_previos,
            after=None,
            descripcion="Eliminación de mobiliario"
        )

        db.session.commit()
        return jsonify({'mensaje': 'Registro eliminado exitosamente'}), 200

    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500



# ====== API para editar mobiliario ---------------------------------------------
@app.route('/api/mobiliario/<string:id>', methods=['PUT'])
@admin_required_api
def editar_mobiliario(id):
    _ensure_mobiliario_valor_column()
    _ensure_mobiliario_foto_2_column()
    mobiliario = Mobiliario.query.get_or_404(id)
    try:
        data = request.json or {}

        # ✅ Evitar que cambien el ID manualmente (por seguridad)
        if 'id' in data and data['id'] != id:
            return jsonify({"error": "No se puede modificar el ID del bien"}), 400

        # ✅ Validar campos obligatorios
        campos_obligatorios = ['ubicacion_id', 'rubro_id', 'clase_bien_id']
        for campo in campos_obligatorios:
            if data.get(campo) is None:
                return jsonify({"error": f"Falta el campo obligatorio: {campo}"}), 400

        # 🕒 Hora de Argentina (UTC-3)
        ahora = (datetime.utcnow() - timedelta(hours=3)).strftime("%d-%m-%Y %H:%M")
        historial = mobiliario.historial_movimientos or ""

        # Snapshot ANTES para auditoría
        before = model_to_dict(mobiliario)

        # Detectar cambio de ubicación
        nueva_ubicacion_id = data.get("ubicacion_id", mobiliario.ubicacion_id)
        if nueva_ubicacion_id != mobiliario.ubicacion_id:
            sub_old = Subdependencia.query.get(mobiliario.ubicacion_id)
            sub_new = Subdependencia.query.get(nueva_ubicacion_id)
            anexo_old = Anexo.query.get(sub_old.id_anexo) if sub_old else None
            anexo_new = Anexo.query.get(sub_new.id_anexo) if sub_new else None

            ubicacion_old = f"{sub_old.nombre} - {anexo_old.nombre}" if sub_old and anexo_old else "Desconocido"
            ubicacion_new = f"{sub_new.nombre} - {anexo_new.nombre}" if sub_new and anexo_new else "Desconocido"
            historial += f"\n[{ahora}] Cambio de ubicación: de '{ubicacion_old}' a '{ubicacion_new}'"

        # Guardar cambio genérico
        historial += f"\n[{ahora}] Registro editado"

        # Formatear tipo de resolución
        tipos_resolucion = {
            "PSA": "P.S.A",
            "DECRETO": "Decreto",
            "SL": "S.L",
            "PSL": "P.S.L"
        }

        tipo = data.get("resolucion_tipo", "").upper()
        tipo_formateado = tipos_resolucion.get(tipo, tipo)

        resolucion_texto = (
            f"Resol Nº{data.get('resolucion_numero')} {tipo_formateado}"
            if data.get("resolucion_numero")
            else data.get("resolucion", mobiliario.resolucion)
        )

        # Actualizar datos
        mobiliario.ubicacion_id = nueva_ubicacion_id
        mobiliario.clase_bien_id = data.get("clase_bien_id", mobiliario.clase_bien_id)
        mobiliario.rubro_id = data.get("rubro_id", mobiliario.rubro_id)
        mobiliario.descripcion = data.get("descripcion", mobiliario.descripcion)
        mobiliario.resolucion = resolucion_texto
        mobiliario.fecha_resolucion = data.get("fecha_resolucion", mobiliario.fecha_resolucion)
        mobiliario.estado_conservacion = data.get("estado_conservacion", mobiliario.estado_conservacion)
        mobiliario.estado_control = data.get("estado_control", mobiliario.estado_control)
        mobiliario.historial_movimientos = historial
        mobiliario.no_dado = data.get("no_dado", mobiliario.no_dado)
        mobiliario.para_reparacion = data.get("para_reparacion", mobiliario.para_reparacion)
        mobiliario.para_baja = data.get("para_baja", mobiliario.para_baja)
        mobiliario.faltante = data.get("faltante", mobiliario.faltante)
        mobiliario.sobrante = data.get("sobrante", mobiliario.sobrante)
        mobiliario.problema_etiqueta = data.get("problema_etiqueta", mobiliario.problema_etiqueta)
        mobiliario.comentarios = data.get("comentarios", mobiliario.comentarios)
        mobiliario.foto_url = data.get("foto_url", mobiliario.foto_url)
        mobiliario.foto_url_2 = data.get("foto_url_2", mobiliario.foto_url_2)
        if "valor" in data:
            mobiliario.valor = _parse_mobiliario_valor(data.get("valor"))

        # Snapshot DESPUÉS
        after = model_to_dict(mobiliario)

        # Auditoría
        registrar_auditoria(
            accion="UPDATE",
            tabla="mobiliario",
            id_registro=id,
            before=before,
            after=after,
            descripcion="Edición de mobiliario"
        )

        db.session.commit()
        return jsonify({"mensaje": "Registro actualizado correctamente"}), 200

    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500


# =============================================================================
# API NUEVA: FOTO RAPIDA DE MOBILIARIO
# -----------------------------------------------------------------------------
# Permite actualizar solo la imagen desde el front movil sin pasar por el PUT
# completo de edicion, conservando auditoria e historial.
# =============================================================================
@app.route('/api/mobiliario/<string:id>/foto', methods=['PATCH'])
@admin_required_api
def actualizar_foto_mobiliario(id):
    _ensure_mobiliario_foto_2_column()
    mobiliario = Mobiliario.query.get_or_404(id)
    try:
        data = request.get_json(silent=True) or {}
        foto_url = str(data.get("foto_url") or "").strip() if "foto_url" in data else None
        foto_url_2 = str(data.get("foto_url_2") or "").strip() if "foto_url_2" in data else None
        if foto_url is None and foto_url_2 is None:
            return jsonify({"error": "Falta foto_url o foto_url_2"}), 400
        if foto_url == "" or foto_url_2 == "":
            return jsonify({"error": "La URL de foto no puede estar vacia"}), 400

        ahora = (datetime.utcnow() - timedelta(hours=3)).strftime("%d-%m-%Y %H:%M")
        historial = mobiliario.historial_movimientos or ""

        before = model_to_dict(mobiliario)

        campos_actualizados = []
        if foto_url is not None:
            mobiliario.foto_url = foto_url
            campos_actualizados.append("foto principal")
        if foto_url_2 is not None:
            mobiliario.foto_url_2 = foto_url_2
            campos_actualizados.append("segunda foto")

        mobiliario.historial_movimientos = (
            historial + f"\n[{ahora}] Foto actualizada: {', '.join(campos_actualizados)}"
        ).strip()

        after = model_to_dict(mobiliario)
        registrar_auditoria(
            accion="UPDATE",
            tabla="mobiliario",
            id_registro=id,
            before=before,
            after=after,
            descripcion="Actualizacion rapida de foto"
        )

        db.session.commit()
        return jsonify({
            "mensaje": "Foto actualizada correctamente",
            "id": id,
            "foto_url": mobiliario.foto_url,
            "foto_url_2": mobiliario.foto_url_2,
            "fecha_actualizacion": (
                (mobiliario.fecha_actualizacion - timedelta(hours=3)).strftime("%d/%m/%Y %H:%M")
                if mobiliario.fecha_actualizacion
                else None
            ),
        }), 200

    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500



# ====== API para registrar un nuevo mobiliario ---------------------------------
@app.route('/api/mobiliario', methods=['POST'])
@admin_required_api
def registrar_mobiliario():
    try:
        _ensure_mobiliario_valor_column()
        _ensure_mobiliario_foto_2_column()
        data = request.json or {}
        print("🟢 Data recibida en /api/mobiliario:", data)

        # Diccionario de tipos de resolución formateados
        tipos_resolucion = {
            "PSA": "P.S.A",
            "DECRETO": "Decreto",
            "SL": "S.L",
            "PSL": "P.S.L"
        }

        tipo = (data.get("resolucion_tipo") or "").upper()
        tipo_formateado = tipos_resolucion.get(tipo, tipo)

        resolucion_numero = data.get('resolucion_numero')
        if resolucion_numero and str(resolucion_numero).strip() != "":
            resolucion_texto = f"Resol Nº{str(resolucion_numero).strip()} {str(tipo_formateado)}"
        else:
            resolucion_texto = data.get("resolucion") or ""

        # Usar el ID proporcionado si es válido, o generar uno nuevo
        id_mob = data.get("id")
        if id_mob and str(id_mob).isdigit():
            id_mob = str(id_mob)
        else:
            ids_actuales = db.session.query(Mobiliario.id).all()
            ids_numericos = [int(x[0]) for x in ids_actuales if x[0] and str(x[0]).isdigit()]
            id_mob = str(max(ids_numericos) + 1) if ids_numericos else "1"
        print("🟡 ID generado para nuevo mobiliario:", id_mob)

        # Validar campos opcionales vacíos
        estado_conservacion = data.get("estado_conservacion") or None
        estado_control = data.get("estado_control") or None
        historial_movimientos = data.get("historial_movimientos") or None
        comentarios = data.get("comentarios") or None

        nuevo = Mobiliario(
            id=id_mob,
            ubicacion_id=data.get("ubicacion_id"),
            clase_bien_id=data.get("clase_bien_id"),
            rubro_id=data.get("rubro_id"),
            descripcion=data.get("descripcion"),
            resolucion=resolucion_texto,
            fecha_resolucion=data.get("fecha_resolucion"),
            estado_conservacion=estado_conservacion,
            estado_control=estado_control,
            historial_movimientos=historial_movimientos,
            no_dado=data.get("no_dado", False),
            para_reparacion=data.get("para_reparacion", False),
            para_baja=data.get("para_baja", False),
            faltante=data.get("faltante", False),
            sobrante=data.get("sobrante", False),
            problema_etiqueta=data.get("problema_etiqueta", False),
            comentarios=comentarios,
            foto_url=data.get("foto_url", ""),
            foto_url_2=data.get("foto_url_2", ""),
            valor=_parse_mobiliario_valor(data.get("valor"))
        )

        db.session.add(nuevo)
        db.session.flush()  # asegura tener el ID en la sesión

        # Auditoría (snapshot después)
        after = model_to_dict(nuevo)
        registrar_auditoria(
            accion="CREATE",
            tabla="mobiliario",
            id_registro=nuevo.id,
            before=None,
            after=after,
            descripcion="Alta de mobiliario"
        )

        db.session.commit()
        print("✅ Registro guardado correctamente.")
        return jsonify({"mensaje": "Registro creado exitosamente", "id_generado": id_mob}), 201

    except Exception as e:
        db.session.rollback()
        print("🔴 Error en /api/mobiliario:", str(e))
        return jsonify({"error": str(e)}), 500










def _generar_ids_mobiliario(cantidad):
    ids_actuales = db.session.query(Mobiliario.id).all()
    ids_numericos = [
        int(item[0])
        for item in ids_actuales
        if item[0] and str(item[0]).isdigit()
    ]
    siguiente = (max(ids_numericos) + 1) if ids_numericos else 1
    return [str(siguiente + idx) for idx in range(cantidad)]


def _clonar_mobiliario(origen, nuevo_id):
    return Mobiliario(
        id=nuevo_id,
        ubicacion_id=origen.ubicacion_id,
        clase_bien_id=origen.clase_bien_id,
        rubro_id=origen.rubro_id,
        descripcion=origen.descripcion,
        resolucion=origen.resolucion,
        fecha_resolucion=origen.fecha_resolucion,
        estado_conservacion=origen.estado_conservacion,
        estado_control=origen.estado_control,
        historial_movimientos=origen.historial_movimientos,
        no_dado=origen.no_dado,
        para_reparacion=origen.para_reparacion,
        para_baja=origen.para_baja,
        faltante=origen.faltante,
        sobrante=origen.sobrante,
        problema_etiqueta=origen.problema_etiqueta,
        comentarios=origen.comentarios,
        foto_url=origen.foto_url,
        foto_url_2=origen.foto_url_2,
        valor=origen.valor,
    )


@app.post("/api/mobiliario/<string:id>/duplicar")
@admin_required_api
def duplicar_mobiliario(id):
    try:
        _ensure_mobiliario_valor_column()
        _ensure_mobiliario_foto_2_column()
        data = request.get_json(silent=True) or {}
        try:
            cantidad = int(data.get("cantidad", 1))
        except (TypeError, ValueError):
            return jsonify({"error": "La cantidad debe ser numerica"}), 400
        if cantidad < 1 or cantidad > 100:
            return jsonify({"error": "La cantidad debe estar entre 1 y 100"}), 400

        origen = db.session.get(Mobiliario, id)
        if not origen:
            return jsonify({"error": "Mobiliario no encontrado"}), 404

        ids_generados = _generar_ids_mobiliario(cantidad)
        for nuevo_id in ids_generados:
            nuevo = _clonar_mobiliario(origen, nuevo_id)
            db.session.add(nuevo)
            db.session.flush()
            registrar_auditoria(
                accion="CREATE",
                tabla="mobiliario",
                id_registro=nuevo.id,
                before=None,
                after=model_to_dict(nuevo),
                descripcion=f"Duplicacion de mobiliario desde ID {id}"
            )

        db.session.commit()
        return jsonify({
            "mensaje": "Mobiliario duplicado correctamente",
            "id_origen": id,
            "cantidad": cantidad,
            "ids_generados": ids_generados,
        }), 201
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500


# Ruta para obtener un mobiliario por ID--------------------------------------
@app.route('/api/mobiliario/<string:id>', methods=['GET'])
def obtener_mobiliario_por_id(id):
    _ensure_mobiliario_valor_column()
    _ensure_mobiliario_foto_2_column()
    resultado = db.session.query(
        Mobiliario,
        Subdependencia.nombre.label("subdependencia"),
        Subdependencia.id_anexo,
        Anexo.nombre.label("anexo"),
        Anexo.direccion.label("direccion_anexo"),
        ClaseBien.id_clase,
        ClaseBien.descripcion.label("clase"),
        Rubro.id_rubro,
        Rubro.nombre.label("rubro")
    ).outerjoin(
        Subdependencia, Mobiliario.ubicacion_id == Subdependencia.id
    ).outerjoin(
        Anexo, Subdependencia.id_anexo == Anexo.id
    ).outerjoin(
        ClaseBien, ClaseBien.id_clase == Mobiliario.clase_bien_id
    ).outerjoin(
        Rubro, Rubro.id_rubro == Mobiliario.rubro_id
    ).filter(
        Mobiliario.id == id
    ).first()

    if not resultado:
        return jsonify({"error": "Mobiliario no encontrado"}), 404

    m, sub_nombre, id_anexo, anexo_nombre, direccion_anexo, id_clase, clase_desc, id_rubro, rubro_nombre = resultado

    return jsonify({
        "id": m.id,
        "descripcion": m.descripcion,
        "resolucion": m.resolucion,
        "fecha_resolucion": m.fecha_resolucion.isoformat() if m.fecha_resolucion else None,
        "estado_conservacion": m.estado_conservacion,
        "estado_control": m.estado_control,
        "historial_movimientos": m.historial_movimientos,
        "comentarios": m.comentarios,
        "foto_url": m.foto_url,
        "foto_url_2": m.foto_url_2,
        "valor": _mobiliario_valor_json(m.valor),
        "ubicacion_id": m.ubicacion_id,
        "subdependencia": sub_nombre,
        "id_anexo": id_anexo,
        "anexo": anexo_nombre,
        "direccion_anexo": direccion_anexo,
        "no_dado": m.no_dado,
        "para_reparacion": m.para_reparacion,
        "para_baja": m.para_baja,
        "faltante": m.faltante,
        "sobrante": m.sobrante,
        "problema_etiqueta": m.problema_etiqueta,
        "fecha_creacion": (m.fecha_creacion - timedelta(hours=3)).strftime("%d/%m/%Y %H:%M") if m.fecha_creacion else None,
        "fecha_actualizacion": (m.fecha_actualizacion - timedelta(hours=3)).strftime("%d/%m/%Y %H:%M") if m.fecha_actualizacion else None,
        "clase_bien_id": id_clase,
        "clase": clase_desc,
        "rubro_id": id_rubro,
        "rubro": rubro_nombre
    })



@app.route('/api/mobiliario/para-baja', methods=['GET'])
def obtener_mobiliarios_para_baja():
    try:
        _ensure_mobiliario_valor_column()
        _ensure_mobiliario_foto_2_column()
        query = """
        SELECT
            m.id                      AS id,
            m.ubicacion_id            AS ubicacion_id,
            m.descripcion,
            m.estado_conservacion,
            m.estado_control,
            m.resolucion,
            m.fecha_resolucion,
            m.no_dado,
            m.para_reparacion,
            m.para_baja,
            m.faltante,
            m.sobrante,
            m.problema_etiqueta,
            m.comentarios,
            m.foto_url,
            m.foto_url_2,
            m.valor,
            m.fecha_creacion,
            m.fecha_actualizacion,
            m.historial_movimientos,
            r.nombre                  AS rubro,
            cb.descripcion            AS clase_bien,
            sd.id                     AS id_subdependencia,
            sd.nombre                 AS subdependencia,
            a.id                      AS id_anexo,
            a.nombre                  AS anexo,
            a.direccion               AS direccion_anexo
        FROM mobiliario m
        LEFT JOIN clases_bienes   cb ON m.clase_bien_id = cb.id_clase
        LEFT JOIN rubros          r  ON m.rubro_id = r.id_rubro
        LEFT JOIN subdependencias sd ON m.ubicacion_id = sd.id
        LEFT JOIN anexos          a  ON sd.id_anexo = a.id
        WHERE m.id ~ '^[0-9]+$'
          AND m.para_baja = TRUE
        ORDER BY m.id::integer DESC;
        """

        conn = db.engine.raw_connection()
        cur = conn.cursor()
        cur.execute(query)
        columns = [col[0] for col in cur.description]
        results = [dict(zip(columns, row)) for row in cur.fetchall()]
        cur.close()
        conn.close()

        for r in results:
            historial = r.get("historial_movimientos")
            if historial:
                r["historial"] = [line.strip() for line in historial.split("\n") if line.strip()]
            else:
                r["historial"] = []
            del r["historial_movimientos"]

            if r.get("fecha_creacion"):
                r["fecha_creacion"] = (r["fecha_creacion"] - timedelta(hours=3)).strftime("%d/%m/%Y %H:%M")
            if r.get("fecha_actualizacion"):
                r["fecha_actualizacion"] = (r["fecha_actualizacion"] - timedelta(hours=3)).strftime("%d/%m/%Y %H:%M")

            if r.get("fecha_resolucion"):
                try:
                    r["fecha_resolucion"] = r["fecha_resolucion"].isoformat()
                except Exception:
                    pass
            r["valor"] = _mobiliario_valor_json(r.get("valor"))

        return jsonify(results), 200

    except Exception as e:
        print("🔴 Error en /api/mobiliario/para-baja:", e)
        return jsonify({"error": str(e)}), 500


from datetime import datetime
import qrcode
from flask import send_file, url_for, request, render_template
from PIL import Image, ImageDraw, ImageFont
import io

@app.route('/mobiliario/ver_etiqueta/<string:id>')
def ver_etiqueta(id):
    etiqueta_url = url_for('generar_etiqueta', id=id)
    return render_template('ver_etiqueta.html', id=id, etiqueta_url=etiqueta_url)
        
@app.route('/Etiqueta/<string:id>')
def ver_mobiliario_por_id(id):
    return f"Mobiliario {id}"
@app.route('/mobiliario/etiqueta/ver/<string:id>')
def ver_etiqueta_para_imprimir(id):
    etiqueta_url = url_for('generar_etiqueta', id=id)
    return render_template('ver_etiqueta.html', id=id, etiqueta_url=etiqueta_url)


@app.route('/mobiliario/etiqueta/<string:id>')
def generar_etiqueta(id):
    import qrcode
    from PIL import Image, ImageDraw, ImageFont
    import io
    from flask import send_file, url_for
    from datetime import datetime

    # =========================================================
    # URL QR
    # =========================================================
    BASE_URL = "https://anexos.onrender.com"
    url_qr = f"{BASE_URL}/ver?id={id}"

    # =========================================================
    # TAMAÑO (65mm x 24mm)
    # =========================================================
    dpi = 300
    mm_to_inch = 25.4

    width = int((71 / mm_to_inch) * dpi)
    height = int((24 / mm_to_inch) * dpi)

    etiqueta = Image.new('RGB', (width, height), 'black')
    draw = ImageDraw.Draw(etiqueta)

    padding = int(width * 0.04)

    # =========================================================
    # QR
    # =========================================================
    qr_size = int(height * 0.85)

    qr = qrcode.QRCode(border=1)
    qr.add_data(url_qr)
    qr.make(fit=True)

    qr_img = qr.make_image(fill_color="black", back_color="white").convert("RGB")
    qr_img = qr_img.resize((qr_size, qr_size))

    qr_x = padding
    qr_y = (height - qr_size) // 2

    draw.rectangle(
        [(qr_x - 4, qr_y - 4), (qr_x + qr_size + 4, qr_y + qr_size + 4)],
        fill="white"
    )

    etiqueta.paste(qr_img, (qr_x, qr_y))

    # =========================================================
    # TEXTO (CENTRADO PROFESIONAL)
    # =========================================================
    text_x = qr_x + qr_size + padding
    text_width = width - text_x - padding

    # Fuentes (escala optimizada)
    font_title = ImageFont.truetype("DejaVuSans-Bold.ttf", int(height * 0.14))
    font_sub   = ImageFont.truetype("DejaVuSans-Bold.ttf", int(height * 0.12))
    font_id    = ImageFont.truetype("DejaVuSans-Bold.ttf", int(height * 0.30))
    font_year  = ImageFont.truetype("DejaVuSans-Bold.ttf", int(height * 0.15))


    # Textos
    titulo = "FUNCION LEGISLATIVA"
    sub = "Dirección de Patrimonio"
    texto_id = f"ID: {id.zfill(6)}"
    anio = f"AÑO {datetime.now().year}"
    

    # Espaciado
    spacing_small = int(height * 0.05)
    spacing_big = int(height * 0.09)

    # Función altura real
    def h(text, font):
        bbox = draw.textbbox((0, 0), text, font=font)
        return bbox[3] - bbox[1]

    h_title = h(titulo, font_title)
    h_sub   = h(sub, font_sub)
    h_id    = h(texto_id, font_id)
    h_year  = h(anio, font_year)


    # Altura total bloque
    total_height = (
        h_title +
        spacing_small +
        h_sub +
        spacing_big +
        h_id +
        spacing_small +
        h_year
    )

    # CENTRADO VERTICAL
    y = (height - total_height) // 2

    # Función centrado horizontal
    def draw_centered(text, y, font):
        bbox = draw.textbbox((0, 0), text, font=font)
        w = bbox[2] - bbox[0]
        x = text_x + (text_width - w) // 2
        draw.text((x, y), text, fill="white", font=font)

    # DIBUJO

    draw_centered(titulo, y, font_title)
    y += h_title + spacing_small

    draw_centered(sub, y, font_sub)
    y += h_sub + spacing_big

    draw_centered(texto_id, y, font_id)
    y += h_id + int(height * 0.08)

    draw_centered(anio, y, font_year)
    y += h_year + spacing_big

    # =========================================================
    # EXPORTAR
    # =========================================================
    buffer = io.BytesIO()
    etiqueta.save(buffer, format='PNG')
    buffer.seek(0)

    return send_file(buffer, mimetype='image/png')

#vista que me llevan los qr---------------------------------------------------------------------
@app.route('/api/mobiliario/<mobiliario_id>/advertencia', methods=['GET'])
def mobiliario_advertencia_por_id(mobiliario_id):
    try:
        _ensure_mobiliario_foto_2_column()
        query = """
        SELECT 
            m.id AS id_mobiliario,
            m.foto_url,
            m.foto_url_2,
            m.descripcion,
            r.nombre AS rubro,
            cb.descripcion AS clase_bien,
            sd.nombre AS subdependencia,
            a.nombre AS anexo
        FROM mobiliario m
        LEFT JOIN clases_bienes cb ON m.clase_bien_id = cb.id_clase
        LEFT JOIN rubros r ON m.rubro_id = r.id_rubro
        LEFT JOIN subdependencias sd ON m.ubicacion_id = sd.id
        LEFT JOIN anexos a ON sd.id_anexo = a.id
        WHERE m.id = %s
        LIMIT 1;
        """

        conn = db.engine.raw_connection()
        cur = conn.cursor()
        cur.execute(query, (str(mobiliario_id),))  # <-- pasamos como string
        row = cur.fetchone()
        columns = [col[0] for col in cur.description]
        cur.close()
        conn.close()

        if not row:
            return jsonify({'error': 'Mobiliario no encontrado'}), 404

        result = dict(zip(columns, row))
        result["advertencia"] = (
            "Si este mobiliario se encuentra fuera de la ubicación correspondiente, "
            "avisar a la Dirección de Patrimonio en Dalmacio Vélez 743."
        )

        return jsonify(result)

    except Exception as e:
        print("🔴 Error en /api/mobiliario/<id>/advertencia:", e)
        return jsonify({'error': str(e)}), 500


@app.route('/ver')
def ver_mobiliario():
    # No hace falta capturar el id aquí, el JS en ver.html lo lee del query string
    return render_template('ver.html')


#imprimir listados ------------------------------------------------------------


@app.route('/imprimir')
def imprimir():
    # 🔹 Cargar datos base
    anexos = Anexo.query.order_by(Anexo.nombre.asc()).all()
    subdependencias = Subdependencia.query.order_by(Subdependencia.nombre.asc()).all()
    rubros = Rubro.query.order_by(Rubro.nombre.asc()).all()
    clases = ClaseBien.query.order_by(ClaseBien.descripcion.asc()).all()

    # 🔹 Diccionario de etiquetas de filtros
    campos = {
        "no_dado": "No Dado",
        "para_reparacion": "Reparación",
        "para_baja": "Para baja",
        "faltante": "Faltante",
        "sobrante": "Sobrante",
        "problema_etiqueta": "Problema etiqueta"
    }

    # 🔹 Filtros seleccionados (desde los checkboxes del GET)
    filtros_estado = request.args.getlist('estado')
    filtros_conservacion = request.args.getlist('conservacion')

    # 🔹 Inicialmente sin resultados
    mobiliario = []

    return render_template(
        'imprimir.html',
        anexos=anexos,
        subdependencias=subdependencias,
        rubros=rubros,
        clases=clases,
        campos=campos,
        filtros_estado=filtros_estado,
        filtros_conservacion=filtros_conservacion,
        mobiliario=mobiliario
    )




from datetime import datetime

from datetime import datetime

from flask import request, render_template
from datetime import datetime
@app.route('/imprimir_listado')
def imprimir_listado():
    from datetime import datetime
    conn, cur = get_conn_dict()

    # --- Parámetros GET ---
    anexo_id = request.args.get('anexo')
    subdep_id = request.args.get('subdependencia')
    rubro_id = request.args.get('rubro')
    clase_id = request.args.get('clase')
    estado_conservacion = request.args.get('estado_conservacion')
    tipo_listado = request.args.get('tipo_listado', 'clasico')
    filtros = request.args.getlist('filtros')

    # 🔥 NUEVO: detectar si quieren incluir faltantes
    incluir_faltantes = request.args.get("incluir_faltantes", "false").lower() == "true"

    # --- Nombre de anexo y subdependencia (maneja "todos"/"todas") ---
    if anexo_id and anexo_id.isdigit():
        cur.execute("SELECT nombre FROM anexos WHERE id = %s", (anexo_id,))
        row = cur.fetchone()
        anexo_nombre = row[0] if row else "Todos"
    else:
        anexo_nombre = "Todos"

    if subdep_id and subdep_id.isdigit():
        cur.execute("SELECT nombre FROM subdependencias WHERE id = %s", (subdep_id,))
        row = cur.fetchone()
        subdependencia_nombre = row[0] if row else "Todas"
    else:
        subdependencia_nombre = "Todas"

    # --- Base query ---
    query = """
        SELECT 
            r.nombre AS rubro,
            c.descripcion AS clase,
            m.id AS id_mobiliario,
            m.descripcion,
            m.estado_conservacion,
            m.no_dado,
            m.para_reparacion,
            m.para_baja,
            m.faltante,
            m.sobrante,
            m.problema_etiqueta,
            r.id_rubro AS rubro_id,
            c.id_clase AS clase_id
        FROM mobiliario m
        LEFT JOIN rubros r ON m.rubro_id = r.id_rubro
        LEFT JOIN clases_bienes c ON m.clase_bien_id = c.id_clase
        LEFT JOIN subdependencias s ON m.ubicacion_id = s.id
        LEFT JOIN anexos a ON s.id_anexo = a.id
        WHERE 1=1
    """

    params = []

    # --- Filtros por Anexo y Subdependencia ---
    if anexo_id and anexo_id.isdigit():
        query += " AND a.id = %s"
        params.append(anexo_id)

    if subdep_id and subdep_id.isdigit():
        query += " AND s.id = %s"
        params.append(subdep_id)

    # --- Filtros por Rubro, Clase y Estado ---
    if rubro_id and rubro_id.isdigit():
        query += " AND m.rubro_id = %s"
        params.append(rubro_id)

    if clase_id and clase_id.isdigit():
        query += " AND m.clase_bien_id = %s"
        params.append(clase_id)

    if estado_conservacion:
        query += " AND m.estado_conservacion = %s"
        params.append(estado_conservacion)

    # --- Filtros de estado (checkboxes) ---
    for f in filtros:
        query += f" AND m.{f} = TRUE"

    # 🔥🔥 NUEVO BLOQUE — Excluir faltantes si NO marcaron incluir faltantes
    if not incluir_faltantes:
        query += " AND (m.faltante IS NULL OR m.faltante = FALSE)"

    query += " ORDER BY r.nombre, c.descripcion, m.id ASC"

    # --- Ejecutar y procesar ---
    cur.execute(query, tuple(params))
    resultados = cur.fetchall()
    conn.close()

    # --- Agrupar Rubro > Clase ---
    grupos = {}
    
    for fila in resultados:
        rubro_nombre = fila[0] or "SIN RUBRO"
        clase_nombre = fila[1] or "SIN CLASE"
    
        rubro_id = fila[11]  # r.id_rubro
        clase_id = fila[12]  # c.id_clase
    
        # Llaves únicas usando ID + nombre
        rubro_key = f"{rubro_id}|{rubro_nombre}"
        clase_key = f"{clase_id}|{clase_nombre}"
    
        grupos.setdefault(rubro_key, {}).setdefault(clase_key, []).append(fila)




    total_bienes = sum(len(items) for clases in grupos.values() for items in clases.values())

    # --- Elegir plantilla ---
    plantilla = "listado_impresion_entrega.html" if tipo_listado == "entrega" else "listado_impresion.html"

    return render_template(
        plantilla,
        grupos=grupos,
        anexo_nombre=anexo_nombre,
        subdependencia_nombre=subdependencia_nombre,
        ahora=datetime.now(),
        filtros=filtros,
        estado_conservacion=estado_conservacion,
        total_bienes=total_bienes
    )








# 🧩 Funciones auxiliares opcionales
def obtener_nombre_anexo(anexo_id):
    if not anexo_id or anexo_id == "todos":
        return "Todos"
    conn = db.engine.raw_connection()
    cur = conn.cursor()
    cur.execute("SELECT nombre FROM anexos WHERE id = %s", (anexo_id,))
    row = cur.fetchone()
    conn.close()
    return row[0] if row else "Desconocido"

def obtener_nombre_subdependencia(sub_id):
    if not sub_id or sub_id == "todas":
        return "Todas"
    conn = db.engine.raw_connection()
    cur = conn.cursor()
    cur.execute("SELECT nombre FROM subdependencias WHERE id = %s", (sub_id,))
    row = cur.fetchone()
    conn.close()
    return row[0] if row else "Desconocida"






# --- Serializer simple para enviar lo que la vista espera ---
def mob_to_dict(m):
    def iso(d):
        if not d: return None
        try: return d.isoformat()[:10]
        except: return str(d)[:10]
    return {
        "id": m.id,
        "ubicacion_id": m.ubicacion_id,
        "descripcion": m.descripcion or "",
        "estado_conservacion": m.estado_conservacion or "",
        "resolucion": m.resolucion or "",
        "fecha_resolucion": iso(m.fecha_resolucion),
        "no_dado": bool(m.no_dado),
        "para_reparacion": bool(m.para_reparacion),
        "para_baja": bool(m.para_baja),
        "faltante": bool(m.faltante),
        "sobrante": bool(m.sobrante),
        "problema_etiqueta": bool(m.problema_etiqueta),
        "comentarios": m.comentarios or "",
        "foto_url": m.foto_url or "",
        "foto_url_2": m.foto_url_2 or "",
        "valor": _mobiliario_valor_json(m.valor),
    }

# --- Listar mobiliario por subdependencia ---
@app.route('/api/mobiliario_por_subdependencia/<int:sub_id>', methods=['GET'])
def mobiliario_por_subdependencia(sub_id):
    _ensure_mobiliario_valor_column()
    _ensure_mobiliario_foto_2_column()
    items = Mobiliario.query.filter_by(ubicacion_id=sub_id)\
                            .order_by(Mobiliario.id.asc()).all()
    return jsonify([mob_to_dict(m) for m in items])



@app.route('/api/mobiliario_filtrado', methods=['POST'])
def mobiliario_filtrado():
    _ensure_mobiliario_valor_column()
    _ensure_mobiliario_foto_2_column()
    data = request.get_json()
    subdep_id = data['subdependencia_id']
    filtros = data.get('filtros', [])

    query = Mobiliario.query.filter_by(ubicacion_id=subdep_id)

    # Aplicar filtros
    for campo in ['no_dado', 'para_reparacion', 'para_baja', 'faltante', 'sobrante', 'problema_etiqueta']:
        if campo not in filtros:
            query = query.filter((getattr(Mobiliario, campo) != True) | (getattr(Mobiliario, campo) == None))

    resultados = query.order_by(Mobiliario.id.desc()).all()
    return jsonify([
        {
            "id": m.id,
            "descripcion": m.descripcion
        } for m in resultados
    ])


from flask import render_template_string
from datetime import datetime

from flask import render_template_string, request
from datetime import datetime

@app.route('/imprimir_listado_preview')
def imprimir_listado_preview():
    anexo_id = request.args.get('anexo')
    sub_id = request.args.get('subdependencia')
    filtros = request.args.get('filtros', '').split(',')
    incluir_faltantes = request.args.get("incluir_faltantes", "false").lower() == "true"
    estado_conservacion = request.args.get("estado_conservacion")

    # Base de la consulta
    query = """
        SELECT m.descripcion, m.id, m.estado_conservacion
        FROM mobiliario m
        JOIN subdependencias sd ON m.ubicacion_id = sd.id
        JOIN anexos a ON sd.id_anexo = a.id
        WHERE a.id = %s AND sd.id = %s
    """
    params = [anexo_id, sub_id]

    # Filtros booleanos (checkboxes)
    for campo in filtros:
        if campo and campo != "faltante":
            query += f" AND m.{campo} = TRUE"

    # Incluir o excluir faltantes
    if not incluir_faltantes:
        query += " AND (m.faltante IS NULL OR m.faltante = FALSE)"

    # Filtro por estado de conservación
    if estado_conservacion:
        query += " AND m.estado_conservacion = %s"
        params.append(estado_conservacion)

    # Ejecutar consulta
    conn = db.engine.raw_connection()
    cur = conn.cursor()
    cur.execute(query, tuple(params))
    mobiliarios = cur.fetchall()
    conn.close()

    # Render rápido en HTML (preview)
    return render_template_string("""
    <table class="w-full table-auto border border-gray-300 text-sm mt-4">
      <thead class="bg-gray-100">
        <tr>
          <th class="border px-2 py-1 text-left">Descripción</th>
          <th class="border px-2 py-1 text-center">ID</th>
          <th class="border px-2 py-1 text-center">Estado de conservación</th>
        </tr>
      </thead>
      <tbody>
        {% for m in mobiliarios %}
        <tr class="hover:bg-gray-50">
          <td class="border px-2 py-1">{{ m[0] }}</td>
          <td class="border px-2 py-1 text-center">{{ m[1] }}</td>
          <td class="border px-2 py-1 text-center">{{ m[2] or '-' }}</td>
        </tr>
        {% endfor %}
        {% if mobiliarios|length == 0 %}
        <tr>
          <td colspan="3" class="text-center p-4 text-gray-500">
            No se encontraron resultados con los filtros seleccionados.
          </td>
        </tr>
        {% endif %}
      </tbody>
    </table>
    """, mobiliarios=mobiliarios)




# =========================
# NUEVA API JSON PARA NEXT
# =========================
@app.route('/api/listados/generar-json', methods=['GET'])
def generar_listado_json():
    try:
        conn, cur = get_conn_dict()

        anexo_id = request.args.get('anexo')
        subdep_id = request.args.get('subdependencia')
        rubro_id = request.args.get('rubro')
        clase_id = request.args.get('clase')
        estado_conservacion = request.args.get('estado_conservacion')
        tipo_listado = request.args.get('tipo_listado', 'clasico')
        filtros = request.args.getlist('filtros')
        incluir_faltantes = request.args.get("incluir_faltantes", "false").lower() == "true"

        campos = {
            "no_dado": "No Dado",
            "para_reparacion": "Reparación",
            "para_baja": "Para baja",
            "faltante": "Faltante",
            "sobrante": "Sobrante",
            "problema_etiqueta": "Problema etiqueta"
        }

        # -------- nombre + id de anexo ----------
        anexo_nombre = "Todos"
        anexo_id_resolved = None

        if anexo_id and anexo_id.isdigit():
            cur.execute("SELECT id, nombre FROM anexos WHERE id = %s", (anexo_id,))
            row = cur.fetchone()
            if row:
                anexo_id_resolved = row[0]
                anexo_nombre = row[1]

        # -------- nombre + id de subdependencia ----------
        subdependencia_nombre = "Todas"
        subdependencia_id_resolved = None

        if subdep_id and subdep_id.isdigit():
            cur.execute("SELECT id, nombre FROM subdependencias WHERE id = %s", (subdep_id,))
            row = cur.fetchone()
            if row:
                subdependencia_id_resolved = row[0]
                subdependencia_nombre = row[1]

        # -------- query base ----------
        query = """
            SELECT 
                r.nombre AS rubro_nombre,
                c.descripcion AS clase_nombre,
                m.id AS id_mobiliario,
                m.descripcion,
                m.estado_conservacion,
                m.no_dado,
                m.para_reparacion,
                m.para_baja,
                m.faltante,
                m.sobrante,
                m.problema_etiqueta,
                r.id_rubro AS rubro_id,
                c.id_clase AS clase_id
            FROM mobiliario m
            LEFT JOIN rubros r ON m.rubro_id = r.id_rubro
            LEFT JOIN clases_bienes c ON m.clase_bien_id = c.id_clase
            LEFT JOIN subdependencias s ON m.ubicacion_id = s.id
            LEFT JOIN anexos a ON s.id_anexo = a.id
            WHERE 1=1
        """

        params = []

        # -------- filtros ubicación ----------
        if anexo_id and anexo_id.isdigit():
            query += " AND a.id = %s"
            params.append(anexo_id)

        if subdep_id and subdep_id.isdigit():
            query += " AND s.id = %s"
            params.append(subdep_id)

        # -------- filtros categoría ----------
        if rubro_id and rubro_id.isdigit():
            query += " AND m.rubro_id = %s"
            params.append(rubro_id)

        if clase_id and clase_id.isdigit():
            query += " AND m.clase_bien_id = %s"
            params.append(clase_id)

        # -------- estado conservación ----------
        if estado_conservacion:
            query += " AND m.estado_conservacion = %s"
            params.append(estado_conservacion)

        # -------- flags ----------
        allowed_flags = {
            "no_dado",
            "para_reparacion",
            "para_baja",
            "faltante",
            "sobrante",
            "problema_etiqueta"
        }

        for f in filtros:
            if f in allowed_flags:
                query += f" AND m.{f} = TRUE"

        # -------- incluir/excluir faltantes ----------
        if not incluir_faltantes:
            query += " AND (m.faltante IS NULL OR m.faltante = FALSE)"

        query += " ORDER BY r.nombre, c.descripcion, m.id ASC"

        cur.execute(query, tuple(params))
        resultados = cur.fetchall()

        grupos_map = {}

        for fila in resultados:
            rubro_nombre = fila[0] or "SIN RUBRO"
            clase_nombre = fila[1] or "SIN CLASE"
            id_mobiliario = fila[2]
            descripcion = fila[3]
            estado = fila[4]
            no_dado = fila[5]
            para_reparacion = fila[6]
            para_baja = fila[7]
            faltante = fila[8]
            sobrante = fila[9]
            problema_etiqueta = fila[10]
            rubro_id_row = fila[11]
            clase_id_row = fila[12]

            observaciones = []
            if no_dado:
                observaciones.append("No dado")
            if para_reparacion:
                observaciones.append("Para reparación")
            if para_baja:
                observaciones.append("Para baja")
            if faltante:
                observaciones.append("Faltante")
            if sobrante:
                observaciones.append("Sobrante")
            if problema_etiqueta:
                observaciones.append("Problema etiqueta")

            rubro_key = f"{rubro_id_row}|{rubro_nombre}"
            clase_key = f"{clase_id_row}|{clase_nombre}"

            if rubro_key not in grupos_map:
                grupos_map[rubro_key] = {
                    "rubro_id": rubro_id_row,
                    "rubro_nombre": rubro_nombre,
                    "clases": {}
                }

            if clase_key not in grupos_map[rubro_key]["clases"]:
                grupos_map[rubro_key]["clases"][clase_key] = {
                    "clase_id": clase_id_row,
                    "clase_nombre": clase_nombre,
                    "items": []
                }

            grupos_map[rubro_key]["clases"][clase_key]["items"].append({
                "id": str(id_mobiliario),
                "descripcion": descripcion,
                "estado_conservacion": estado,
                "observaciones": observaciones
            })

        grupos = []
        for _, rubro_data in grupos_map.items():
            clases = list(rubro_data["clases"].values())
            grupos.append({
                "rubro_id": rubro_data["rubro_id"],
                "rubro_nombre": rubro_data["rubro_nombre"],
                "clases": clases
            })

        total_bienes = sum(
            len(clase["items"])
            for rubro in grupos
            for clase in rubro["clases"]
        )

        cur.close()
        conn.close()

        return jsonify({
            "anexo_id": anexo_id_resolved,
            "anexo_nombre": anexo_nombre,
            "subdependencia_id": subdependencia_id_resolved,
            "subdependencia_nombre": subdependencia_nombre,
            "fecha_emision": datetime.now().strftime("%d/%m/%Y"),
            "tipo_listado": tipo_listado,
            "total_bienes": total_bienes,
            "filtros_aplicados": {
                "filtros": filtros,
                "filtros_labels": [campos[f] for f in filtros if f in campos],
                "estado_conservacion": estado_conservacion or "",
                "incluir_faltantes": incluir_faltantes
            },
            "grupos": grupos
        }), 200

    except Exception as e:
        print("🔴 Error en /api/listados/generar-json:", e)
        return jsonify({"error": str(e)}), 500


# EJECUCIÓN
#if __name__ == '__main__':
 #   app.run(debug=not IS_PRODUCTION)




# sistema para planillas --------------------------------------------------------------------------------------------------------------------------------------------------


from flask import Flask, Blueprint, render_template, request, redirect, send_file,flash,url_for
import pandas as pd
from io import BytesIO
from datetime import datetime
import psycopg2
from openpyxl import Workbook




# 📌 Conexión directa a Render PostgreSQL
def get_db_connection():
    return psycopg2.connect(
        DATABASE_URL,
        sslmode=os.getenv("DB_SSLMODE", "require")
    )

# 📦 Blueprint
bp = Blueprint('altas', __name__)

@bp.route('/altas', methods=['GET', 'POST'])
def altas():
    if request.method == 'POST':
        data = request.form

        def parse_numeric(value):
            try:
                if isinstance(value, str):
                    value = value.replace("$", "").replace(",", ".").strip()
                return float(value)
            except (ValueError, TypeError):
                return 0.0

        # ✅ Recolección segura de datos
        fecha_alta = data['fecha_alta']
        cantidad = int(data['cantidad']) if data['cantidad'] else None
        concepto = data['concepto']
        disposicion = data['disposicion']
        fecha_resolucion = data.get('fecha_resolucion')  # ← nuevo campo
        valor_unitario = parse_numeric(data.get('valor_unitario'))
        valor_total = parse_numeric(data.get('valor_total'))
        causa_alta = data['causa_alta']
        codigo_presup = data['codigo_presup']
        identidad = data['identidad']
        mes_planilla = data['mes_planilla']
        anio_planilla = data['anio_planilla']
        id_rubro = int(data['id_rubro']) if data['id_rubro'] else None
        id_clase = int(data['id_clase']) if data['id_clase'] else 0

        # ✅ Ejecutar INSERT con fecha_resolucion incluida
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO movimientos_altas (
                        fecha_alta, cantidad, concepto, disposicion, fecha_resolucion,
                        valor_unitario, valor_total, causa_alta,
                        codigo_presup, identidad,
                        mes_planilla, anio_planilla, id_rubro, id_clase
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, (
                    fecha_alta,
                    cantidad,
                    concepto,
                    disposicion,
                    fecha_resolucion,
                    valor_unitario,
                    valor_total,
                    causa_alta,
                    codigo_presup,
                    identidad,
                    mes_planilla,
                    anio_planilla,
                    id_rubro,
                    id_clase
                ))
        return redirect('/altas')

    # GET: obtener datos con filtros
    mes = request.args.get('mes')
    anio = request.args.get('anio')

    with get_db_connection() as conn:
        query = """
            SELECT m.*, r.nombre AS rubro_nombre, c.descripcion AS clase_nombre
            FROM movimientos_altas m
            LEFT JOIN rubros r ON m.id_rubro = r.id_rubro
            LEFT JOIN clases_bienes c ON m.id_clase = c.id_clase
            WHERE 1=1
        """
        params = []

        if mes:
            query += " AND m.mes_planilla = %s"
            params.append(mes)
        if anio:
            query += " AND m.anio_planilla = %s"
            params.append(anio)

        query += " ORDER BY m.fecha_alta DESC"

        df = pd.read_sql(query, conn, params=params)

        def parse_column_numeric(col):
            return col.apply(lambda x: float(str(x).replace(",", ".").replace("$", "").strip()) if x not in [None, "", "None"] else 0.0)

        df['valor_total'] = parse_column_numeric(df['valor_total'])
        df['valor_unitario'] = parse_column_numeric(df['valor_unitario'])

        rubros = pd.read_sql("SELECT id_rubro, nombre FROM rubros ORDER BY nombre", conn)
        clases = pd.read_sql("SELECT id_clase, id_rubro, descripcion FROM clases_bienes ORDER BY descripcion", conn)

    return render_template('altas.html',
                           registros=df.to_dict(orient='records'),
                           rubros=rubros.to_dict(orient='records'),
                           clases=clases.to_dict(orient='records'))







@bp.route('/altas/editar/<int:id>', methods=['GET', 'POST'])
def editar_alta(id):
    with get_db_connection() as conn:
        if request.method == 'POST':
            data = request.form

            def parse_numeric(value):
                try:
                    if isinstance(value, str):
                        value = value.replace("$", "").replace(",", ".").strip()
                    return float(value)
                except (ValueError, TypeError):
                    return 0.0

            cur = conn.cursor()
            cur.execute("""
                UPDATE movimientos_altas
                SET fecha_alta = %s,
                    cantidad = %s,
                    concepto = %s,
                    disposicion = %s,
                    valor_unitario = %s,
                    valor_total = %s,
                    causa_alta = %s,
                    codigo_presup = %s,
                    identidad = %s,
                    id_rubro = %s,
                    id_clase = %s
                WHERE id = %s
            """, (
                data['fecha_alta'],
                int(data['cantidad']),
                data['concepto'],
                data['disposicion'],
                parse_numeric(data['valor_unitario']),
                parse_numeric(data['valor_total']),
                data['causa_alta'],
                data['codigo_presup'],
                data['identidad'],
                int(data['id_rubro']) if data['id_rubro'] else None,
                int(data['id_clase']) if data['id_clase'] else 0,
                id
            ))
            conn.commit()
            return redirect('/altas')

        # GET: cargar datos del registro a editar
        cur = conn.cursor()
        cur.execute("SELECT * FROM movimientos_altas WHERE id = %s", (id,))
        registro = cur.fetchone()

        columnas = [desc[0] for desc in cur.description]
        registro_dict = dict(zip(columnas, registro))

        rubros = pd.read_sql("SELECT id_rubro, nombre FROM rubros ORDER BY nombre", conn)
        clases = pd.read_sql("SELECT id_clase, id_rubro, descripcion FROM clases_bienes ORDER BY descripcion", conn)

    return render_template('editar_alta.html',
                           registro=registro_dict,
                           rubros=rubros.to_dict(orient='records'),
                           clases=clases.to_dict(orient='records'))






@bp.route('/altas/eliminar/<int:id>', methods=['POST'])
def eliminar_alta(id):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('DELETE FROM movimientos_altas WHERE id = %s', (id,))
    conn.commit()
    cur.close()
    conn.close()
    flash('Registro eliminado con éxito.', 'success')
    return redirect(url_for('altas.altas'))

#@bp.route('/')
#def index():
#    return render_template('altas.html')


# =============================================================================
# API NUEVA: PLANILLAS - ALTAS
# -----------------------------------------------------------------------------
# Endpoints JSON para usar la carga de altas desde el frontend Next sin romper
# las pantallas HTML existentes (/altas, /altas/editar, /altas/exportar_pdf).
# =============================================================================

def _parse_numeric_alta(value):
    try:
        if isinstance(value, str):
            value = value.replace("$", "").replace(" ", "").strip()
            if "," in value:
                value = value.replace(".", "").replace(",", ".")
        return float(value or 0)
    except (ValueError, TypeError):
        return 0.0


def _parse_int_or_none(value):
    try:
        if value in ("", None):
            return None
        return int(value)
    except (ValueError, TypeError):
        return None


def _parse_clase_alta(value):
    parsed = _parse_int_or_none(value)
    return parsed if parsed is not None else 0


def _alta_to_dict(row):
    id_clase = row["id_clase"] if row["id_clase"] is not None else 0
    return {
        "id": row["id"],
        "fecha_alta": _fecha_iso(row["fecha_alta"]),
        "cantidad": row["cantidad"],
        "concepto": row["concepto"],
        "disposicion": row["disposicion"],
        "fecha_resolucion": _fecha_iso(row["fecha_resolucion"]),
        "valor_unitario": float(row["valor_unitario"] or 0),
        "valor_total": float(row["valor_total"] or 0),
        "causa_alta": row["causa_alta"],
        "codigo_presup": row["codigo_presup"],
        "identidad": row["identidad"],
        "mes_planilla": row["mes_planilla"],
        "anio_planilla": row["anio_planilla"],
        "id_rubro": row["id_rubro"],
        "id_clase": id_clase,
        "rubro_nombre": row["rubro_nombre"],
        "clase_nombre": row["clase_nombre"] or ("0000" if id_clase == 0 else None),
    }


def _alta_payload(data):
    cantidad = _parse_int_or_none(data.get("cantidad"))
    valor_unitario = _parse_numeric_alta(data.get("valor_unitario"))
    valor_total = _parse_numeric_alta(data.get("valor_total"))
    if not valor_total and cantidad:
        valor_total = cantidad * valor_unitario

    id_rubro = _parse_int_or_none(data.get("id_rubro"))
    id_clase = _parse_clase_alta(data.get("id_clase"))
    codigo_presup = _text_or_none(data.get("codigo_presup"))
    if not codigo_presup and id_rubro:
        codigo_presup = str(id_rubro)

    return {
        "fecha_alta": _text_or_none(data.get("fecha_alta")),
        "cantidad": cantidad,
        "concepto": _text_or_none(data.get("concepto")),
        "disposicion": _text_or_none(data.get("disposicion")),
        "fecha_resolucion": _text_or_none(data.get("fecha_resolucion")),
        "valor_unitario": valor_unitario,
        "valor_total": valor_total,
        "causa_alta": _text_or_none(data.get("causa_alta")),
        "codigo_presup": codigo_presup,
        "identidad": _text_or_none(data.get("identidad")),
        "mes_planilla": _text_or_none(data.get("mes_planilla")),
        "anio_planilla": _text_or_none(data.get("anio_planilla")),
        "id_rubro": id_rubro,
        "id_clase": id_clase,
    }


def _validar_alta_payload(payload):
    required = ["fecha_alta", "cantidad", "concepto", "mes_planilla", "anio_planilla"]
    for key in required:
        if payload.get(key) in ("", None):
            raise ValueError(f"Falta el campo obligatorio: {key}")


@bp.route('/api/altas', methods=['GET'])
def api_listar_altas():
    try:
        mes = request.args.get("mes")
        anio = request.args.get("anio")
        params = {}
        where = []
        if mes:
            where.append("m.mes_planilla = :mes")
            params["mes"] = mes
        if anio:
            where.append("m.anio_planilla = :anio")
            params["anio"] = anio

        where_sql = "WHERE " + " AND ".join(where) if where else ""
        rows = db.session.execute(text(f"""
            SELECT
                m.*,
                r.nombre AS rubro_nombre,
                c.descripcion AS clase_nombre
            FROM movimientos_altas m
            LEFT JOIN rubros r ON m.id_rubro = r.id_rubro
            LEFT JOIN clases_bienes c ON m.id_clase = c.id_clase
            {where_sql}
            ORDER BY m.fecha_alta DESC, m.id DESC
        """), params).mappings().all()
        return jsonify([_alta_to_dict(row) for row in rows])
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500


@bp.route('/api/altas/<int:id>', methods=['GET'])
def api_obtener_alta(id):
    try:
        row = db.session.execute(text("""
            SELECT
                m.*,
                r.nombre AS rubro_nombre,
                c.descripcion AS clase_nombre
            FROM movimientos_altas m
            LEFT JOIN rubros r ON m.id_rubro = r.id_rubro
            LEFT JOIN clases_bienes c ON m.id_clase = c.id_clase
            WHERE m.id = :id
        """), {"id": id}).mappings().first()
        if not row:
            return jsonify({"error": "Alta no encontrada"}), 404
        return jsonify(_alta_to_dict(row))
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500


@bp.route('/api/altas', methods=['POST'])
@admin_required_api
def api_crear_alta():
    try:
        payload = _alta_payload(request.get_json(silent=True) or {})
        _validar_alta_payload(payload)
        row = db.session.execute(text("""
            INSERT INTO movimientos_altas (
                fecha_alta,
                cantidad,
                concepto,
                disposicion,
                fecha_resolucion,
                valor_unitario,
                valor_total,
                causa_alta,
                codigo_presup,
                identidad,
                mes_planilla,
                anio_planilla,
                id_rubro,
                id_clase
            )
            VALUES (
                :fecha_alta,
                :cantidad,
                :concepto,
                :disposicion,
                :fecha_resolucion,
                :valor_unitario,
                :valor_total,
                :causa_alta,
                :codigo_presup,
                :identidad,
                :mes_planilla,
                :anio_planilla,
                :id_rubro,
                :id_clase
            )
            RETURNING id
        """), payload).mappings().first()
        db.session.commit()
        return api_obtener_alta(row["id"])
    except ValueError as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500


@bp.route('/api/altas/<int:id>', methods=['PUT'])
@admin_required_api
def api_editar_alta(id):
    try:
        if not db.session.execute(text("SELECT 1 FROM movimientos_altas WHERE id = :id"), {"id": id}).first():
            return jsonify({"error": "Alta no encontrada"}), 404
        payload = _alta_payload(request.get_json(silent=True) or {})
        _validar_alta_payload(payload)
        db.session.execute(text("""
            UPDATE movimientos_altas
            SET
                fecha_alta = :fecha_alta,
                cantidad = :cantidad,
                concepto = :concepto,
                disposicion = :disposicion,
                fecha_resolucion = :fecha_resolucion,
                valor_unitario = :valor_unitario,
                valor_total = :valor_total,
                causa_alta = :causa_alta,
                codigo_presup = :codigo_presup,
                identidad = :identidad,
                mes_planilla = :mes_planilla,
                anio_planilla = :anio_planilla,
                id_rubro = :id_rubro,
                id_clase = :id_clase
            WHERE id = :id
        """), {**payload, "id": id})
        db.session.commit()
        return api_obtener_alta(id)
    except ValueError as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500


@bp.route('/api/altas/<int:id>', methods=['DELETE'])
@admin_required_api
def api_eliminar_alta(id):
    try:
        result = db.session.execute(text("""
            DELETE FROM movimientos_altas
            WHERE id = :id
        """), {"id": id})
        db.session.commit()
        if result.rowcount == 0:
            return jsonify({"error": "Alta no encontrada"}), 404
        return jsonify({"mensaje": "Alta eliminada"}), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500


# =============================================================================
# API NUEVA: REVISION DE PLANILLAS DE ALTAS
# -----------------------------------------------------------------------------
# Capa independiente para que superadmin revise/apruebe planillas de altas sin
# modificar la tabla principal movimientos_altas ni el flujo normal de carga.
# =============================================================================

def _ensure_altas_revision_tables():
    db.session.execute(text("""
        CREATE TABLE IF NOT EXISTS altas_planilla_revisiones (
            id SERIAL PRIMARY KEY,
            mes_planilla VARCHAR(20) NOT NULL,
            anio_planilla VARCHAR(10) NOT NULL,
            estado VARCHAR(20) NOT NULL DEFAULT 'pendiente',
            comentario TEXT,
            revisado_por VARCHAR(50),
            fecha_aprobacion TIMESTAMP WITHOUT TIME ZONE,
            fecha_creacion TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP,
            fecha_actualizacion TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (mes_planilla, anio_planilla)
        )
    """))
    db.session.execute(text("""
        CREATE TABLE IF NOT EXISTS altas_movimiento_revisiones (
            id_alta INTEGER PRIMARY KEY REFERENCES movimientos_altas(id) ON DELETE CASCADE,
            revisado BOOLEAN NOT NULL DEFAULT FALSE,
            comentario TEXT,
            revisado_por VARCHAR(50),
            fecha_revision TIMESTAMP WITHOUT TIME ZONE,
            fecha_actualizacion TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP
        )
    """))
    db.session.commit()


def _alta_revision_estado(value):
    estado = str(value or "pendiente").strip().lower()
    if estado not in {"pendiente", "en_revision", "aprobada"}:
        raise ValueError("Estado de revision invalido")
    return estado


def _alta_revision_key(mes, anio):
    mes_clean = str(mes or "").strip()
    anio_clean = str(anio or "").strip()
    if not mes_clean or not anio_clean:
        raise ValueError("Faltan mes y anio de planilla")
    return mes_clean, anio_clean


def _ensure_altas_revision_planilla(mes, anio):
    db.session.execute(text("""
        INSERT INTO altas_planilla_revisiones (mes_planilla, anio_planilla)
        VALUES (:mes, :anio)
        ON CONFLICT (mes_planilla, anio_planilla) DO NOTHING
    """), {"mes": mes, "anio": anio})


def _alta_revision_resumen_to_dict(row):
    total = int(row["total_movimientos"] or 0)
    revisados = int(row["movimientos_revisados"] or 0)
    estado = row["estado"] or "pendiente"
    return {
        "mes_planilla": row["mes_planilla"],
        "anio_planilla": row["anio_planilla"],
        "estado": estado,
        "comentario": row["comentario"],
        "revisado_por": row["revisado_por"],
        "fecha_aprobacion": _fecha_iso(row["fecha_aprobacion"]),
        "fecha_actualizacion": _fecha_iso(row["fecha_actualizacion"]),
        "total_movimientos": total,
        "movimientos_revisados": revisados,
        "movimientos_pendientes": max(total - revisados, 0),
        "total_valor": float(row["total_valor"] or 0),
    }


def _alta_revision_item_to_dict(row):
    alta = _alta_to_dict(row)
    alta["revision"] = {
        "revisado": bool(row["revision_revisado"]),
        "comentario": row["revision_comentario"],
        "revisado_por": row["revision_revisado_por"],
        "fecha_revision": _fecha_iso(row["revision_fecha_revision"]),
        "fecha_actualizacion": _fecha_iso(row["revision_fecha_actualizacion"]),
    }
    return alta


def _alta_revision_resumen_sql(where_sql="", extra_params=None):
    params = extra_params or {}
    return db.session.execute(text(f"""
        WITH planillas AS (
            SELECT
                m.mes_planilla,
                m.anio_planilla,
                COUNT(*) AS total_movimientos,
                COALESCE(SUM(COALESCE(m.valor_total, 0)), 0) AS total_valor,
                SUM(CASE WHEN COALESCE(amr.revisado, FALSE) THEN 1 ELSE 0 END) AS movimientos_revisados
            FROM movimientos_altas m
            LEFT JOIN altas_movimiento_revisiones amr ON amr.id_alta = m.id
            WHERE COALESCE(TRIM(m.mes_planilla), '') <> ''
              AND COALESCE(TRIM(m.anio_planilla), '') <> ''
              {where_sql}
            GROUP BY m.mes_planilla, m.anio_planilla
        )
        SELECT
            p.mes_planilla,
            p.anio_planilla,
            p.total_movimientos,
            p.total_valor,
            p.movimientos_revisados,
            COALESCE(apr.estado, 'pendiente') AS estado,
            apr.comentario,
            apr.revisado_por,
            apr.fecha_aprobacion,
            apr.fecha_actualizacion
        FROM planillas p
        LEFT JOIN altas_planilla_revisiones apr
          ON apr.mes_planilla = p.mes_planilla
         AND apr.anio_planilla = p.anio_planilla
        ORDER BY p.anio_planilla DESC,
            CASE LOWER(p.mes_planilla)
                WHEN 'enero' THEN 1
                WHEN 'febrero' THEN 2
                WHEN 'marzo' THEN 3
                WHEN 'abril' THEN 4
                WHEN 'mayo' THEN 5
                WHEN 'junio' THEN 6
                WHEN 'julio' THEN 7
                WHEN 'agosto' THEN 8
                WHEN 'septiembre' THEN 9
                WHEN 'octubre' THEN 10
                WHEN 'noviembre' THEN 11
                WHEN 'diciembre' THEN 12
                ELSE 99
            END DESC
    """), params).mappings().all()


@bp.route('/api/altas/revisiones', methods=['GET'])
@superadmin_required_api
def api_listar_revisiones_altas():
    try:
        _ensure_altas_revision_tables()
        rows = _alta_revision_resumen_sql()
        return jsonify([_alta_revision_resumen_to_dict(row) for row in rows]), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500


@bp.route('/api/altas/revisiones/notificaciones', methods=['GET'])
@superadmin_required_api
def api_notificaciones_revisiones_altas():
    try:
        _ensure_altas_revision_tables()
        rows = _alta_revision_resumen_sql()
        pendientes = [
            _alta_revision_resumen_to_dict(row)
            for row in rows
            if (row["estado"] or "pendiente") != "aprobada"
        ]
        return jsonify({
            "pendientes": len(pendientes),
            "planillas": pendientes[:5],
        }), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500


@bp.route('/api/altas/revisiones/movimientos/<int:id_alta>', methods=['PUT'])
@superadmin_required_api
def api_guardar_revision_movimiento_alta(id_alta):
    try:
        _ensure_altas_revision_tables()
        if not db.session.execute(text("SELECT 1 FROM movimientos_altas WHERE id = :id"), {"id": id_alta}).first():
            return jsonify({"error": "Alta no encontrada"}), 404

        data = request.get_json(silent=True) or {}
        revisado = bool(data.get("revisado"))
        comentario = _text_or_none(data.get("comentario"))
        usuario = _main_username()

        row = db.session.execute(text("""
            INSERT INTO altas_movimiento_revisiones (
                id_alta,
                revisado,
                comentario,
                revisado_por,
                fecha_revision,
                fecha_actualizacion
            )
            VALUES (
                :id_alta,
                :revisado,
                :comentario,
                :revisado_por,
                CASE WHEN :revisado THEN CURRENT_TIMESTAMP ELSE NULL END,
                CURRENT_TIMESTAMP
            )
            ON CONFLICT (id_alta) DO UPDATE SET
                revisado = EXCLUDED.revisado,
                comentario = EXCLUDED.comentario,
                revisado_por = EXCLUDED.revisado_por,
                fecha_revision = CASE WHEN EXCLUDED.revisado THEN CURRENT_TIMESTAMP ELSE NULL END,
                fecha_actualizacion = CURRENT_TIMESTAMP
            RETURNING id_alta, revisado, comentario, revisado_por, fecha_revision, fecha_actualizacion
        """), {
            "id_alta": id_alta,
            "revisado": revisado,
            "comentario": comentario,
            "revisado_por": usuario,
        }).mappings().first()
        db.session.commit()
        return jsonify({
            "id_alta": row["id_alta"],
            "revisado": bool(row["revisado"]),
            "comentario": row["comentario"],
            "revisado_por": row["revisado_por"],
            "fecha_revision": _fecha_iso(row["fecha_revision"]),
            "fecha_actualizacion": _fecha_iso(row["fecha_actualizacion"]),
        }), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500


@bp.route('/api/altas/revisiones/<mes>/<anio>', methods=['GET'])
@superadmin_required_api
def api_obtener_revision_alta(mes, anio):
    try:
        _ensure_altas_revision_tables()
        mes, anio = _alta_revision_key(mes, anio)
        _ensure_altas_revision_planilla(mes, anio)
        db.session.commit()

        resumen_rows = _alta_revision_resumen_sql(
            "AND m.mes_planilla = :mes AND m.anio_planilla = :anio",
            {"mes": mes, "anio": anio},
        )
        if not resumen_rows:
            return jsonify({"error": "Planilla sin movimientos"}), 404

        rows = db.session.execute(text("""
            SELECT
                m.*,
                r.nombre AS rubro_nombre,
                c.descripcion AS clase_nombre,
                COALESCE(amr.revisado, FALSE) AS revision_revisado,
                amr.comentario AS revision_comentario,
                amr.revisado_por AS revision_revisado_por,
                amr.fecha_revision AS revision_fecha_revision,
                amr.fecha_actualizacion AS revision_fecha_actualizacion
            FROM movimientos_altas m
            LEFT JOIN rubros r ON m.id_rubro = r.id_rubro
            LEFT JOIN clases_bienes c ON m.id_clase = c.id_clase
            LEFT JOIN altas_movimiento_revisiones amr ON amr.id_alta = m.id
            WHERE m.mes_planilla = :mes AND m.anio_planilla = :anio
            ORDER BY m.codigo_presup ASC NULLS LAST, m.id_clase ASC NULLS LAST, m.fecha_alta ASC, m.id ASC
        """), {"mes": mes, "anio": anio}).mappings().all()

        return jsonify({
            "planilla": _alta_revision_resumen_to_dict(resumen_rows[0]),
            "movimientos": [_alta_revision_item_to_dict(row) for row in rows],
        }), 200
    except ValueError as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500


@bp.route('/api/altas/revisiones/<mes>/<anio>', methods=['PUT'])
@superadmin_required_api
def api_actualizar_revision_alta(mes, anio):
    try:
        _ensure_altas_revision_tables()
        mes, anio = _alta_revision_key(mes, anio)
        data = request.get_json(silent=True) or {}
        estado = _alta_revision_estado(data.get("estado"))
        comentario = _text_or_none(data.get("comentario"))
        usuario = _main_username()

        row = db.session.execute(text("""
            INSERT INTO altas_planilla_revisiones (
                mes_planilla,
                anio_planilla,
                estado,
                comentario,
                revisado_por,
                fecha_aprobacion,
                fecha_actualizacion
            )
            VALUES (
                :mes,
                :anio,
                :estado,
                :comentario,
                :revisado_por,
                CASE WHEN :estado = 'aprobada' THEN CURRENT_TIMESTAMP ELSE NULL END,
                CURRENT_TIMESTAMP
            )
            ON CONFLICT (mes_planilla, anio_planilla) DO UPDATE SET
                estado = EXCLUDED.estado,
                comentario = EXCLUDED.comentario,
                revisado_por = EXCLUDED.revisado_por,
                fecha_aprobacion = CASE WHEN EXCLUDED.estado = 'aprobada' THEN CURRENT_TIMESTAMP ELSE NULL END,
                fecha_actualizacion = CURRENT_TIMESTAMP
            RETURNING mes_planilla, anio_planilla
        """), {
            "mes": mes,
            "anio": anio,
            "estado": estado,
            "comentario": comentario,
            "revisado_por": usuario,
        }).mappings().first()
        db.session.commit()

        resumen_rows = _alta_revision_resumen_sql(
            "AND m.mes_planilla = :mes AND m.anio_planilla = :anio",
            {"mes": row["mes_planilla"], "anio": row["anio_planilla"]},
        )
        return jsonify(_alta_revision_resumen_to_dict(resumen_rows[0])), 200
    except ValueError as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500



@bp.route("/altas/exportar_pdf")
def exportar_pdf_altas():
    mes = request.args.get("mes")
    anio = request.args.get("anio")

    if not mes or not anio:
        return "Faltan parámetros de mes o año", 400

    conn = get_db_connection()
    query = """
        SELECT m.*, r.nombre AS rubro_nombre, c.descripcion AS clase_nombre
        FROM movimientos_altas m
        LEFT JOIN rubros r ON m.id_rubro = r.id_rubro
        LEFT JOIN clases_bienes c ON m.id_clase = c.id_clase
        WHERE m.mes_planilla = %s AND m.anio_planilla = %s
        ORDER BY r.nombre, c.descripcion
    """
    df = pd.read_sql(query, conn, params=(mes, anio))

    # Extraer rubro_codigo desde codigo_presup
    df["rubro_codigo"] = df["codigo_presup"].astype(str).str.extract(r'(\d{2})')

    # Generar rubro_general a partir del código
    mapa_rubro_general = {
        "43": "MAQUINARIA Y EQUIPO",
        "44": "INMUEBLES",
        "45": "VEHÍCULOS",
        "46": "MOBILIARIO",
        "47": "EQUIPO DE COMUNICACIONES",
    }
    df["rubro_general"] = df["rubro_codigo"].map(mapa_rubro_general).fillna("SIN RUBRO")

    # ✅ Conversión robusta de valor_total y valor_unitario
    df["valor_total"] = pd.to_numeric(
        df["valor_total"].astype(str)
        .str.replace("$", "", regex=False)
        .str.replace(",", "", regex=False)
        .str.strip(),
        errors="coerce"
    ).fillna(0)

    df["valor_unitario"] = pd.to_numeric(
        df["valor_unitario"].astype(str)
        .str.replace("$", "", regex=False)
        .str.replace(",", "", regex=False)
        .str.strip(),
        errors="coerce"
    ).fillna(0)

    # ✅ Cálculo total general
    total_general = df["valor_total"].sum()

    # Debug en consola
    print(df[["valor_total"]].head(10))
    print("TOTAL GENERAL CALCULADO:", total_general)

    fecha_presentacion = datetime.now().strftime("%d/%m/%Y")

    return render_template("formato_oficial_altas.html",
                           registros=df.to_dict(orient="records"),
                           mes=mes,
                           anio=anio,
                           fecha_presentacion=fecha_presentacion,
                           total_general=total_general)






#DASHBOARD-----------------------------------------------------------------------------------------------------------------
# ---------- DASHBOARD -------------------------------------------------------------------------------------------------------------
from sqlalchemy import text

@app.route('/dashboard')
def dashboard():
    return render_template('dashboard.html')

@app.route('/api/dashboard', methods=['GET'])
def dashboard_data():
    try:
        with db.engine.connect() as conn:
            # KPIs
            total_mobiliario = conn.execute(text("""
                SELECT COUNT(*) FROM mobiliario
            """)).scalar() or 0

            total_anexos = conn.execute(text("""
                SELECT COUNT(*) FROM anexos
            """)).scalar() or 0

            total_subdeps = conn.execute(text("""
                SELECT COUNT(*) FROM subdependencias
            """)).scalar() or 0

            total_altas = conn.execute(text("""
                SELECT COUNT(*) FROM movimientos_altas
            """)).scalar() or 0

            # Distribución por estado de conservación
            por_estado = conn.execute(text("""
                SELECT COALESCE(NULLIF(TRIM(LOWER(estado_conservacion)), ''), 'sin dato') AS estado,
                       COUNT(*) AS cantidad
                FROM mobiliario
                GROUP BY 1
                ORDER BY 2 DESC
            """)).mappings().all()

            # Conteo por rubro
            por_rubro = conn.execute(text("""
                SELECT COALESCE(r.nombre, 'Sin rubro') AS rubro, COUNT(*) AS cantidad
                FROM mobiliario m
                LEFT JOIN rubros r ON r.id_rubro = m.rubro_id
                GROUP BY 1
                ORDER BY 2 DESC
                LIMIT 12
            """)).mappings().all()

            # Conteo por anexo (top 12)
            por_anexo = conn.execute(text("""
                SELECT COALESCE(a.nombre, 'Sin anexo') AS anexo, COUNT(*) AS cantidad
                FROM mobiliario m
                LEFT JOIN subdependencias sd ON sd.id = m.ubicacion_id
                LEFT JOIN anexos a ON a.id = sd.id_anexo
                GROUP BY 1
                ORDER BY 2 DESC
                LIMIT 12
            """)).mappings().all()

            # Serie mensual: cantidad de mobiliario creado (últimos 12 meses)
            serie_mob = conn.execute(text("""
                SELECT to_char(date_trunc('month',
                           (m.fecha_creacion AT TIME ZONE 'UTC') - interval '3 hour'),
                           'YYYY-MM') AS mes,
                       COUNT(*) AS cantidad
                FROM mobiliario m
                WHERE m.fecha_creacion IS NOT NULL
                GROUP BY 1
                ORDER BY 1
                LIMIT 36
            """)).mappings().all()

            # Serie mensual: total de ALTAS en ARS (últimos 24 meses)
            # Ajuste robusto por si valor_total es texto: quita $ y comas antes de castear
            serie_altas = conn.execute(text("""
                SELECT to_char(make_date(anio_planilla::int, mes_planilla::int, 1), 'YYYY-MM') AS mes,
                       SUM(
                           NULLIF(
                               REPLACE(REPLACE(COALESCE(valor_total::text, '0'),'$',''),',','')
                           ,'')::numeric
                       ) AS total
                FROM movimientos_altas
                WHERE anio_planilla ~ '^[0-9]{4}$' AND mes_planilla ~ '^[0-9]{1,2}$'
                GROUP BY 1
                ORDER BY 1
                LIMIT 36
            """)).mappings().all()

        data = {
            "kpis": {
                "mobiliario": int(total_mobiliario),
                "anexos": int(total_anexos),
                "subdependencias": int(total_subdeps),
                "altas": int(total_altas),
            },
            "por_estado": [{"label": r["estado"], "value": int(r["cantidad"])} for r in por_estado],
            "por_rubro":  [{"label": r["rubro"], "value": int(r["cantidad"])} for r in por_rubro],
            "por_anexo":  [{"label": r["anexo"], "value": int(r["cantidad"])} for r in por_anexo],
            "serie_mobiliario": [{"mes": r["mes"], "value": int(r["cantidad"])} for r in serie_mob],
            "serie_altas": [{"mes": r["mes"], "value": float(r["total"] or 0)} for r in serie_altas],
        }
        return jsonify(data)

    except Exception as e:
        print("🔴 Error /api/dashboard:", e)
        return jsonify({"error": str(e)}), 500
# ---------- /DASHBOARD ----------
#LISTADO DE CONTROL-------------------------------------------------------------------------------------
@app.route('/control')
def control():
    return render_template('control.html')




# 🚀 Crear app y registrar blueprint
#app = Flask(__name__)
# La clave de sesion se configura una sola vez desde SECRET_KEY.
app.register_blueprint(bp)
# 🔢 Filtro para convertir strings tipo "$ 12,345.67" a float
def to_float(value):
    try:
        if isinstance(value, str):
            value = value.replace('$', '').replace(',', '').strip()
        return float(value)
    except:
        return 0.0

# 📎 Registrar el filtro en la app Flask (no en el Blueprint)
app.add_template_filter(to_float, 'to_float')




@app.route('/mobiliario_filtros')
def mobiliario_filtros():
    anexos = Anexo.query.order_by(Anexo.nombre).all()
    rubros = Rubro.query.order_by(Rubro.nombre).all()
    return render_template("mobiliario_filtros.html", anexos=anexos, rubros=rubros)


#SISTEMA DE PERSONAL ----------------------------------------------------------------------------------------
# =======================================================
# 🧭 API REST para la gestión de agentes
# =======================================================

# 🟢 1️⃣ CREAR UN NUEVO AGENTE -------------------------------------------------
@app.route('/api/agentes', methods=['POST'])
@personal_required_api
def crear_agente():
    """
    Crea un nuevo agente en la base de datos.
    Permite subir una imagen (campo 'foto') que se guarda en Cloudinary.
    Requiere: legajo, dni_cuil, apellido, nombre.
    Opcional: id_anexo, id_subdependencia, categoria, tipo, cargo, telefono, email, foto_url.
    """
    try:
        # Si viene JSON (sin archivo)
        if request.is_json:
            data = request.get_json() or {}
            foto_url = data.get("foto_url")

        # Si viene como formulario multipart (con archivo)
        else:
            data = request.form.to_dict()
            foto_url = None

            # 📸 Subir imagen si está presente
            if "foto" in request.files:
                file = request.files["foto"]
                if file and file.filename != "":
                    # Guardar temporalmente el archivo
                    with tempfile.NamedTemporaryFile(delete=False, suffix='.jpg') as temp:
                        file.save(temp.name)
                        # Subir a Cloudinary (carpeta agentes)
                        result = cloudinary.uploader.upload(temp.name, folder="agentes")
                        foto_url = result.get("secure_url")
                        os.remove(temp.name)

        # Validar campos obligatorios
        campos_obligatorios = ['legajo', 'dni_cuil', 'apellido', 'nombre']
        for campo in campos_obligatorios:
            if not data.get(campo):
                return jsonify({"error": f"Falta el campo obligatorio: {campo}"}), 400

        # Crear el nuevo objeto Agente
        nuevo = Agente(
            legajo=data['legajo'],
            dni_cuil=data['dni_cuil'],
            apellido=data['apellido'],
            nombre=data['nombre'],
            id_anexo=data.get('id_anexo'),
            id_subdependencia=data.get('id_subdependencia'),
            categoria=data.get('categoria'),
            tipo=data.get('tipo'),
            cargo=data.get('cargo'),
            telefono=data.get('telefono'),
            email=data.get('email'),
            foto_url=foto_url  # ✅ se carga automáticamente desde Cloudinary
        )

        db.session.add(nuevo)
        db.session.commit()

        return jsonify({
            "mensaje": "Agente registrado correctamente",
            "agente": nuevo.to_dict()
        }), 201

    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500



# 🟠 2️⃣ LISTAR TODOS LOS AGENTES ----------------------------------------------
@app.route('/api/agentes', methods=['GET'])
def listar_agentes():
    """
    Devuelve un listado completo de todos los agentes,
    con sus anexos y subdependencias asociados.
    """
    try:
        agentes = Agente.query.order_by(Agente.apellido, Agente.nombre).all()
        return jsonify([a.to_dict() for a in agentes]), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# 🔵 3️⃣ OBTENER UN AGENTE POR ID ----------------------------------------------
@app.route('/api/agentes/<int:id>', methods=['GET'])
def obtener_agente(id):
    """Obtiene la información detallada de un agente por su ID."""
    agente = Agente.query.get(id)
    if not agente:
        return jsonify({"error": "Agente no encontrado"}), 404
    return jsonify(agente.to_dict()), 200


# 🟣 4️⃣ EDITAR UN AGENTE EXISTENTE --------------------------------------------
@app.route('/api/agentes/<int:id>', methods=['PUT', 'PATCH'])
@personal_required_api
def editar_agente(id):
    """
    Actualiza los datos de un agente existente.
    Permite modificar cualquiera de los campos opcionales.
    """
    try:
        agente = Agente.query.get(id)
        if not agente:
            return jsonify({"error": "Agente no encontrado"}), 404

        data = request.get_json() or {}

        # Actualizar solo los campos presentes en el request
        for campo in [
            "legajo", "dni_cuil", "apellido", "nombre",
            "id_anexo", "id_subdependencia",
            "categoria", "tipo", "cargo", "telefono", "email", "foto_url"
        ]:
            if campo in data:
                setattr(agente, campo, data[campo])

        db.session.commit()
        return jsonify({
            "mensaje": "Agente actualizado correctamente",
            "agente": agente.to_dict()
        }), 200

    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500


# 🔴 5️⃣ ELIMINAR UN AGENTE ----------------------------------------------------
@app.route('/api/agentes/<int:id>', methods=['DELETE'])
@personal_required_api
def eliminar_agente(id):
    """
    Elimina un agente por su ID.
    """
    try:
        agente = Agente.query.get(id)
        if not agente:
            return jsonify({"error": "Agente no encontrado"}), 404

        db.session.delete(agente)
        db.session.commit()
        return jsonify({"mensaje": "Agente eliminado correctamente"}), 200

    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500


# 🟡 6️⃣ LISTAR AGENTES POR ANEXO ----------------------------------------------
@app.route('/api/reportes/agentes_por_anexo', methods=['GET'])
def reportes_agentes_por_anexo():
    """
    Devuelve un resumen de cantidad de agentes por anexo para gráficos.
    """
    try:
        rows = db.session.query(
            Anexo.nombre,
            db.func.count(Agente.id)
        ).outerjoin(Agente, Agente.id_anexo == Anexo.id)\
         .group_by(Anexo.nombre)\
         .order_by(Anexo.nombre.asc())\
         .all()

        data = {nombre: cantidad for nombre, cantidad in rows}

        return jsonify(data), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500



# 🟤 7️⃣ LISTAR AGENTES POR SUBDEPENDENCIA -------------------------------------
@app.route('/api/agentes/subdependencia/<int:sub_id>', methods=['GET'])
def agentes_por_subdependencia(sub_id):
    """
    Lista todos los agentes que pertenecen a una subdependencia específica.
    """
    try:
        agentes = Agente.query.filter_by(id_subdependencia=sub_id)\
                              .order_by(Agente.apellido).all()
        return jsonify([a.to_dict() for a in agentes]), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/agentes')
def agentes():
    return render_template('agentes.html')


#API 1 — Total de empleados-------------------------
@app.route('/api/reportes/total_agentes', methods=['GET'])
def total_agentes():
    try:
        total = Agente.query.count()
        return jsonify({"total": total}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500
        
#API 2 — Empleados por tipo
@app.route('/api/reportes/agentes_por_tipo', methods=['GET'])
def agentes_por_tipo():
    try:
        rows = db.session.query(
            Agente.tipo,
            db.func.count(Agente.id)
        ).group_by(Agente.tipo).all()

        data = {tipo or "Sin tipo": cantidad for tipo, cantidad in rows}

        return jsonify(data), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

#API 3 — Empleados por anexo------------
@app.route('/api/reportes/agentes_por_anexo', methods=['GET'])
def agentes_por_anexo():
    try:
        rows = db.session.query(
            Anexo.nombre,
            db.func.count(Agente.id)
        ).outerjoin(Agente, Agente.id_anexo == Anexo.id)\
         .group_by(Anexo.nombre)\
         .order_by(Anexo.nombre.asc())\
         .all()

        data = {nombre: cantidad for nombre, cantidad in rows}

        return jsonify(data), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


#Login personal ------------------------------
@app.post("/api/login_personal")
def api_login_personal():
    data = request.get_json() or {}
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""

    if not username or not password:
        return jsonify({"error": "missing_credentials"}), 400

    allowed, retry_after = _login_rate_allowed(username)
    if not allowed:
        return jsonify({
            "error": "too_many_attempts",
            "retry_after": retry_after,
        }), 429

    # --------------------------------------------------
    # 1) Buscar usuario en texto plano (SIN HASH)
    # --------------------------------------------------
    try:
        conn, cur = get_conn_dict()
        cur.execute("""
            SELECT id, username, password, role, activo
            FROM usuariospersonal
            WHERE username = %s
            LIMIT 1
        """, (username,))
        row = cur.fetchone()
        cur.close()
        conn.close()
    except Exception as e:
        print("DB ERROR /api/login_personal:", e)
        return jsonify({"error": "db_error"}), 500

    if not row:
        _record_login_failure(username)
        return jsonify({"error": "invalid_credentials"}), 401

    user = dict(row)

    if not user.get("activo", True):
        _record_login_failure(username)
        return jsonify({"error": "user_inactive"}), 403

    # --------------------------------------------------
    # 2) Validar contrasena y migrar a hash si estaba en texto plano
    # --------------------------------------------------
    stored_password = user.get("password", "")

    if _password_esta_hasheada(stored_password):
        if not _password_coincide(stored_password, password):
            _record_login_failure(username)
            return jsonify({"error": "invalid_credentials"}), 401
    else:
        if not _password_coincide(stored_password, password):
            _record_login_failure(username)
            return jsonify({"error": "invalid_credentials"}), 401
        try:
            new_hash = generate_password_hash(password)
            conn, cur = get_conn_dict()
            cur.execute(
                "UPDATE usuariospersonal SET password = %s WHERE id = %s",
                (new_hash, user["id"])
            )
            conn.commit()
            cur.close()
            conn.close()
        except Exception as e:
            print("DB ERROR migrando password personal:", e)

    # --------------------------------------------------
    # 3) Crear sesión
    # --------------------------------------------------
    session.permanent = True
    session["username_personal"] = user["username"]
    session["role_personal"] = user.get("role", "personal")
    session["csrf_token"] = secrets.token_urlsafe(32)
    _touch_session_activity()
    _clear_login_failures(username)

    return jsonify({
        "username": user["username"],
        "role": user.get("role", "personal")
    }), 200




@app.get("/api/me_personal")
def api_me_personal():
    if "username_personal" not in session:
        return jsonify({"error": "not_logged_in"}), 401

    return jsonify({
        "username": session.get("username_personal"),
        "role": session.get("role_personal")
    }), 200

#cierre de sesion -----------
@app.post("/api/logout_personal")
def api_logout_personal():
    _clear_auth_session()
    return jsonify({"ok": True}), 200

#Decorador para proteger rutas del personal
def login_required_personal(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if "username_personal" not in session:
            return jsonify({"error": "auth_required"}), 401
        return f(*args, **kwargs)
    return wrapper


# ▶️ Ejecutar con python app.py
if __name__ == '__main__':
    app.run(debug=not IS_PRODUCTION)

