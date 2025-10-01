from flask import (
    Flask, render_template, request, redirect, url_for, send_file,
    jsonify, session, flash, Blueprint, render_template_string
)
from flask_sqlalchemy import SQLAlchemy
from flask_cors import CORS
from datetime import datetime, timedelta
from werkzeug.utils import secure_filename
from werkzeug.security import check_password_hash, generate_password_hash
import pytz 
import os, tempfile, io
import cloudinary, cloudinary.uploader
import psycopg2, psycopg2.extras
import qrcode
from sqlalchemy import asc

import pandas as pd
# from copy import deepcopy  # ← opcional: borrar si no se usa ..
from functools import wraps
from PIL import Image, ImageDraw, ImageFont
from openpyxl import Workbook

# ===================== APP & CONFIG =====================
app = Flask(__name__)

# 🔐 SECRET KEY (mover a env en prod)
app.secret_key = os.getenv("SECRET_KEY", "clave-secreta-segura-123")

# 🌐 CORS (Vercel + local) — una sola vez y con credenciales
CORS(app, supports_credentials=True, origins=[
    "https://heritage-management.vercel.app",
    "http://localhost:3000"
])

# 🍪 Cookies de sesión para cross-site (Vercel ↔ Render)
app.config["SESSION_COOKIE_SAMESITE"] = "None"
app.config["SESSION_COOKIE_SECURE"] = True      # Render usa HTTPS
app.permanent_session_lifetime = timedelta(days=7)

# 🗄️ Base de datos (mover a env en prod)
app.config['SQLALCHEMY_DATABASE_URI'] = (
    "postgresql://patrimonio_ppfk_user:SabopRq1mqHqRXBZaZBaWsEcqfHYJWM2"
    "@dpg-cv8oiprqf0us73bbbbfg-a.oregon-postgres.render.com/patrimonio_ppfk"
)
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024

db = SQLAlchemy(app)

# ☁️ Cloudinary (mover a env)
cloudinary.config(
    cloud_name="deokbrzem",
    api_key="628521442744972",
    api_secret="UI7D6jgGKoAzjB_NLAgTi1XAwXQ"
)

# ===================== HELPERS =====================
def get_conn_dict():
    conn = psycopg2.connect(
        host="dpg-cv8oiprqf0us73bbbbfg-a.oregon-postgres.render.com",
        database="patrimonio_ppfk",
        user="patrimonio_ppfk_user",
        password="SabopRq1mqHqRXBZaZBaWsEcqfHYJWM2",
        cursor_factory=psycopg2.extras.DictCursor,
        sslmode="require"  # 👈 importante en Render
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


# Configuración de la base de datos PostgreSQL-
# app.config['SQLALCHEMY_DATABASE_URI'] = "postgresql://patrimonio_ppfk_user:SabopRq1mqHqRXBZaZBaWsEcqfHYJWM2@dpg-cv8oiprqf0us73bbbbfg-a.oregon-postgres.render.com/patrimonio_ppfk"
# app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
# app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # Limite de 16MB para archivos

# Configuración Cloudinary
#cloudinary.config(
    #cloud_name="deokbrzem",
    #api_key="628521442744972",
    #api_secret="UI7D6jgGKoAzjB_NLAgTi1XAwXQ"
#)

#db = SQLAlchemy(app)

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


class Anexo(db.Model):
    __tablename__ = 'anexos'
    id = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(255), nullable=False)
    direccion = db.Column(db.Text)

class Usuario(db.Model):
    __tablename__ = 'usuarios'
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False)
    password = db.Column(db.String(255), nullable=False)  # hash
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
    cambios = db.Column(db.Text)  # 👈 existe en la tabla
    datos_anteriores = db.Column(db.JSON)
    datos_nuevos = db.Column(db.JSON)
    descripcion = db.Column(db.Text)
    ip_origen = db.Column(db.String(50))
    user_agent = db.Column(db.Text)
    usuario = db.Column(db.String(100))

    def to_dict(self):
        return {
            "id": self.id,
            "fecha": self.fecha.strftime("%d/%m/%Y %H:%M") if self.fecha else None,
            "tabla_afectada": self.tabla_afectada,
            "id_registro": self.id_registro,
            "accion": self.accion,
            "cambios": self.cambios,
            "datos_anteriores": self.datos_anteriores,
            "datos_nuevos": self.datos_nuevos,
            "descripcion": self.descripcion,
            "ip_origen": self.ip_origen,
            "user_agent": self.user_agent,
            "usuario": self.usuario
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
    valor = db.Column(db.Numeric(12, 2), default=0)  # 👈 nuevo campo

    no_dado = db.Column(db.Boolean, default=False)
    para_reparacion = db.Column(db.Boolean, default=False)
    para_baja = db.Column(db.Boolean, default=False)
    faltante = db.Column(db.Boolean, default=False)
    sobrante = db.Column(db.Boolean, default=False)
    problema_etiqueta = db.Column(db.Boolean, default=False)
    privado = db.Column(db.Boolean, default=False)  # 👈 nuevo campo

    comentarios = db.Column(db.Text)
    foto_url = db.Column(db.String(255))

    fecha_creacion = db.Column(db.DateTime, default=datetime.utcnow)
    fecha_actualizacion = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif'}
def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def upload_to_cloudinary(filepath):
    result = cloudinary.uploader.upload(filepath, folder="mobiliario")
    return result.get("secure_url")

@app.route('/api/uploads', methods=['POST'])
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

@app.post("/api/login")
def api_login():
    data = request.get_json() or {}
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    if not username or not password:
        return jsonify({"error": "missing_credentials"}), 400

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
        return jsonify({"error": f"db_error: {str(e)}"}), 500

    if not user:
        return jsonify({"error": "invalid_credentials"}), 401
    if not user.get("activo", True):
        return jsonify({"error": "user_inactive"}), 403

    stored = user.get("password") or ""

    def is_hashed(p: str) -> bool:
        # Heurística para hashes de werkzeug (pbkdf2:sha256:...)
        return p.startswith("pbkdf2:")

    # Caso 1: ya está hasheada → validar normal
    if is_hashed(stored):
        if not check_password_hash(stored, password):
            return jsonify({"error": "invalid_credentials"}), 401
    else:
        # Caso 2: estaba en texto plano → migrar si coincide
        if stored != password:
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
    session["role"] = user.get("role", "usuario")
    return jsonify({"username": session["username"], "role": session["role"]}), 200

@app.get("/api/me")
@login_required_api
def api_me():
    return jsonify({"username": session.get("username"), "role": session.get("role")}), 200

@app.post("/api/logout")
def api_logout():
    session.pop("username", None)
    session.pop("role", None)
    return jsonify({"ok": True}), 200



@app.route('/logout')
def logout():
    session.pop('username', None)
    session.pop('role', None)
    flash('Has cerrado sesión correctamente.', 'success')
    return redirect(url_for('login'))


# API para obtener todos los rubros ordenados por ID
@app.route('/api/rubros', methods=['GET'])
def obtener_rubros():
    rubros = Rubro.query.order_by(Rubro.id_rubro).all()
    data = [{'id_rubro': r.id_rubro, 'nombre': r.nombre} for r in rubros]
    return jsonify(data)


# API para obtener clases por rubro
#http://127.0.0.1:5000/api/clases-por-rubro?rubro_id=437
@app.route('/api/clases-por-rubro', methods=['GET'])
def clases_por_rubro():
    try:
        rubro_id = request.args.get('rubro_id', type=int)
        if not rubro_id:
            return jsonify({'error': 'Falta el parámetro rubro_id'}), 400

        clases = ClaseBien.query.filter_by(id_rubro=rubro_id).order_by(ClaseBien.descripcion).all()

        data = [{
            'id_clase': c.id_clase,            # 👈 ya no usamos clase_bien_id
            'descripcion': c.descripcion,
            'id_rubro': c.id_rubro
        } for c in clases]

        return jsonify(data)

    except Exception as e:
        return jsonify({'error': str(e)}), 500


#AUDITORIAS----------------------------------------------------------------------------------------------------------
from datetime import datetime
import json

from flask import session, request

def registrar_auditoria(accion, tabla, id_registro, before=None, after=None, descripcion=None):
    try:
        usuario = session.get("username") or request.headers.get("X-User") or "desconocido"
        ip = request.remote_addr
        ua = request.headers.get("User-Agent")

        diff = None
        if before and after:
            diff = {}
            keys = set(before.keys()) | set(after.keys())
            for k in sorted(keys):
                if before.get(k) != after.get(k):
                    diff[k] = [before.get(k), after.get(k)]

        db.session.execute(
            text("""
                INSERT INTO auditoria (
                    fecha,
                    accion,
                    tabla_afectada,
                    id_registro,
                    datos_anteriores,
                    datos_nuevos,
                    cambios,
                    descripcion,
                    usuario,
                    ip_origen,
                    user_agent
                )
                VALUES (
                    timezone('America/Argentina/Buenos_Aires', now()),
                    :accion,
                    :tabla,
                    :id_registro,
                    CAST(:before AS JSONB),
                    CAST(:after  AS JSONB),
                    :cambios,
                    :descripcion,
                    :usuario,
                    :ip,
                    :ua
                )
            """),
            {
                "accion": accion,
                "tabla": tabla,
                "id_registro": str(id_registro),
                "before": json.dumps(before) if before else None,
                "after":  json.dumps(after)  if after  else None,
                "cambios": json.dumps(diff) if diff else None,
                "descripcion": descripcion,
                "usuario": usuario,
                "ip": ip,
                "ua": ua
            }
        )
    except Exception as e:
        print(f"⚠ Error registrando auditoría: {e}")





from datetime import datetime, timedelta

from sqlalchemy import text

@app.route('/api/auditoria', methods=['GET'])
def get_auditoria():
    try:
        limit = int(request.args.get('limit', 100))
        offset = int(request.args.get('offset', 0))
        query = request.args.get('query', '').strip()
    except ValueError:
        return jsonify({"error": "Parámetros inválidos"}), 400

    sql = """
        SELECT
            id,
            to_char(
                fecha,  -- ✅ ya está en hora AR, no volvemos a aplicar timezone
                'DD/MM/YYYY HH24:MI'
            ) AS fecha,
            usuario,
            accion,
            tabla_afectada,
            id_registro,
            datos_anteriores,
            datos_nuevos,
            descripcion,
            ip_origen,
            user_agent
        FROM auditoria
        WHERE 1=1
    """
    params = {}

    # 🔍 Filtro por texto opcional
    if query:
        sql += """
            AND (
                LOWER(usuario) LIKE :q OR
                LOWER(accion) LIKE :q OR
                LOWER(tabla_afectada) LIKE :q OR
                LOWER(id_registro) LIKE :q OR
                LOWER(descripcion) LIKE :q
            )
        """
        params["q"] = f"%{query.lower()}%"

    sql += " ORDER BY fecha DESC LIMIT :limit OFFSET :offset"
    params["limit"] = limit
    params["offset"] = offset

    try:
        with db.engine.connect() as conn:
            result = conn.execute(text(sql), params)
            data = [dict(row._mapping) for row in result]
        return jsonify(data), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# --- vista para ver la auditoría ---
@app.route("/auditoria")
def vista_auditoria():
    return render_template("auditoria.html")



#---------busca por impresora
@app.route('/api/buscar-clase', methods=['GET'])
def buscar_clase():
    query = request.args.get('query', '', type=str)

    if not query:
        return jsonify({'error': 'Falta el parámetro query'}), 400

    clases = ClaseBien.query.filter(ClaseBien.descripcion.ilike(f'%{query}%')).order_by(ClaseBien.descripcion).all()

    data = [{
        'clase_bien_id': c.clase_bien_id,
        'descripcion': c.descripcion,
        'id_rubro': c.id_rubro
    } for c in clases]

    return jsonify(data)

#-------busca por id clase 109
@app.route('/api/clase/<int:clase_bien_id>', methods=['GET'])
def obtener_clase_por_id(clase_bien_id):
    clase = ClaseBien.query.get(clase_bien_id)

    if not clase:
        return jsonify({'error': 'Clase no encontrada'}), 404

    rubro = Rubro.query.get(clase.id_rubro)

    return jsonify({
        'clase_bien_id': clase.clase_bien_id,
        'descripcion': clase.descripcion,
        'id_rubro': clase.id_rubro,
        'rubro': rubro.nombre if rubro else 'Sin rubro'
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







# API para obtener los registros de mobiliario-----------------------
from datetime import timedelta

@app.route('/api/mobiliario/ultimos', methods=['GET'])
def ultimos_mobiliarios():
    try:
        query = """
        SELECT 
            m.id                    AS id_mobiliario,
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
            m.privado,   -- 👈 nuevo campo
            m.comentarios,
            m.foto_url,
            m.valor,
            m.fecha_creacion,
            m.fecha_actualizacion,
            m.historial_movimientos,
            r.nombre               AS rubro,
            cb.descripcion         AS clase_bien,
            sd.nombre              AS subdependencia,
            a.nombre               AS anexo,
            a.direccion            AS direccion_anexo
        FROM    mobiliario m
        LEFT JOIN clases_bienes cb ON m.clase_bien_id = cb.id_clase
        LEFT JOIN rubros        r  ON m.rubro_id = r.id_rubro
        LEFT JOIN subdependencias sd ON m.ubicacion_id = sd.id
        LEFT JOIN anexos a ON sd.id_anexo = a.id
        WHERE m.id ~ '^[0-9]+$'
        ORDER BY m.id::integer DESC;
        """

        conn = db.engine.raw_connection()
        cur  = conn.cursor()
        cur.execute(query)
        columns  = [col[0] for col in cur.description]
        results  = [dict(zip(columns, row)) for row in cur.fetchall()]
        cur.close()
        conn.close()

        # ✅ Formatear fechas y procesar historial
        for r in results:
            # Convertir historial en lista
            historial = r.get("historial_movimientos")
            if historial:
                r["historial"] = [line.strip() for line in historial.split('\n') if line.strip()]
            else:
                r["historial"] = []
            del r["historial_movimientos"]

            # Formatear fechas con hora argentina
            if r["fecha_creacion"]:
                r["fecha_creacion"] = (r["fecha_creacion"] - timedelta(hours=3)).strftime("%d/%m/%Y %H:%M")
            if r["fecha_actualizacion"]:
                r["fecha_actualizacion"] = (r["fecha_actualizacion"] - timedelta(hours=3)).strftime("%d/%m/%Y %H:%M")

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



# ====== API para eliminar un registro de patrimonio -----------------------------
@app.route('/api/patrimonio/<string:id>', methods=['DELETE'])
def eliminar_patrimonio(id):
    try:
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
def editar_mobiliario(id):
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
        mobiliario.privado = data.get("privado", mobiliario.privado)
        mobiliario.valor = data.get("valor", mobiliario.valor)  # 👈 nuevo campo
        mobiliario.problema_etiqueta = data.get("problema_etiqueta", mobiliario.problema_etiqueta)
        mobiliario.comentarios = data.get("comentarios", mobiliario.comentarios)
        mobiliario.foto_url = data.get("foto_url", mobiliario.foto_url)

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



# ====== API para registrar un nuevo mobiliario ---------------------------------
@app.route('/api/mobiliario', methods=['POST'])
def registrar_mobiliario():
    try:
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
            privado=data.get("privado", False),
            valor=data.get("valor", 0),   # 👈 nuevo campo
            comentarios=comentarios,
            foto_url=data.get("foto_url", "")
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










# Ruta para obtener un mobiliario por ID--------------------------------------
@app.route('/api/mobiliario/<string:id>', methods=['GET'])
def obtener_mobiliario_por_id(id):
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
        "privado": m.privado,  # 👈 nuevo campo
        "problema_etiqueta": m.problema_etiqueta,
        "fecha_creacion": (m.fecha_creacion - timedelta(hours=3)).strftime("%d/%m/%Y %H:%M") if m.fecha_creacion else None,
        "fecha_actualizacion": (m.fecha_actualizacion - timedelta(hours=3)).strftime("%d/%m/%Y %H:%M") if m.fecha_actualizacion else None,
        "clase_bien_id": id_clase,
        "clase": clase_desc,
        "valor": float(m.valor) if m.valor is not None else 0,
        "rubro_id": id_rubro,
        "rubro": rubro_nombre
    })



@app.route('/api/mobiliario/etiqueta/ver/<string:id>')
def ver_etiqueta_para_imprimir(id):
    # URL de descarga de la etiqueta (usa la función actual que ya genera el PNG)
    etiqueta_url = url_for('generar_etiqueta_png', id=id)
    return render_template('ver_etiqueta.html', id=id, etiqueta_url=etiqueta_url)



from flask import send_file
import code
from PIL import Image, ImageDraw, ImageFont
from datetime import datetime
import io
import qrcode

@app.route('/api/mobiliario/etiqueta/png/<string:id>')
def generar_etiqueta_png(id):
    import os, io
    from datetime import datetime
    from flask import send_file
    from PIL import Image, ImageDraw, ImageFont
    import qrcode

    size_px = 283  # 24 mm a 300 dpi
    etiqueta = Image.new('RGB', (size_px, size_px), 'black')
    draw = ImageDraw.Draw(etiqueta)

    try:
        # Fuentes
        font_titulo = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 20)
        font_id = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 40)
        font_fecha = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 22)
    except:
        font_titulo = font_id = font_fecha = ImageFont.load_default()

    # Título arriba
    titulo = "Dir. de Patrimonio"
    w, h = draw.textbbox((0, 0), titulo, font=font_titulo)[2:]
    draw.text(((size_px - w) // 2, 5), titulo, font=font_titulo, fill='white')

    # Generar QR grande
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=8,
        border=1
    )
    qr.add_data(f"https://anexos.onrender.com/ver?id={id}")
    qr.make(fit=True)
    img_qr = qr.make_image(fill_color="black", back_color="white").resize((200, 200))

    # Pegar QR centrado en el medio
    qr_x = (size_px - 200) // 2
    qr_y = (size_px - 200) // 2
    etiqueta.paste(img_qr, (qr_x, qr_y))

    # Número ID debajo del QR
    texto_id = f"{id.zfill(6)}"
    w, h = draw.textbbox((0, 0), texto_id, font=font_id)[2:]
    draw.text(((size_px - w) // 2, qr_y + 200 + 2), texto_id, font=font_id, fill='white')

  

    # Exportar PNG
    buffer = io.BytesIO()
    etiqueta.save(buffer, format='PNG')
    buffer.seek(0)
    return send_file(buffer, mimetype='image/png')


#vista que me llevan los qr---------------------------------------------------------------------
@app.route('/api/mobiliario/<mobiliario_id>/advertencia', methods=['GET'])
def mobiliario_advertencia_por_id(mobiliario_id):
    try:
        query = """
        SELECT 
            m.id AS id_mobiliario,
            m.foto_url,
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
    anexos = Anexo.query.all()
    return render_template('imprimir.html', anexos=anexos)


from datetime import datetime

from datetime import datetime

from flask import request, render_template
from datetime import datetime

@app.route('/imprimir_listado')
def imprimir_listado():
    anexo_id = request.args.get('anexo')
    sub_id = request.args.get('subdependencia')
    filtros = request.args.get('filtros', '').split(',')
    incluir_faltantes = request.args.get("incluir_faltantes", "false").lower() == "true"

    campos = {
        "no_dado": "No Dado",
        "para_reparacion": "Reparación",
        "para_baja": "Para baja",
        "faltante": "Faltante",
        "sobrante": "Sobrante",
        "problema_etiqueta": "Problema etiqueta"
    }

    query = """
        SELECT m.descripcion, m.id
        FROM mobiliario m
        JOIN subdependencias sd ON m.ubicacion_id = sd.id
        JOIN anexos a ON sd.id_anexo = a.id
        WHERE a.id = %s AND sd.id = %s
    """

    for campo in filtros:
        if campo and campo != "faltante":
            query += f" AND m.{campo} = TRUE"

    if not incluir_faltantes:
        query += " AND (m.faltante IS NULL OR m.faltante = FALSE)"

    conn = db.engine.raw_connection()
    cur = conn.cursor()
    cur.execute(query, (anexo_id, sub_id))
    mobiliarios = cur.fetchall()

    cur.execute("SELECT nombre FROM anexos WHERE id = %s", (anexo_id,))
    anexo_nombre = cur.fetchone()[0]

    cur.execute("SELECT nombre FROM subdependencias WHERE id = %s", (sub_id,))
    subdependencia_nombre = cur.fetchone()[0]

    conn.close()

    return render_template(
        "listado_impresion.html",
        mobiliarios=mobiliarios,
        campos=campos,
        ahora=datetime.now(),
        anexo_nombre=anexo_nombre,
        subdependencia_nombre=subdependencia_nombre,
        subdependencia_id=sub_id
    )









@app.route('/api/subdependencias_por_anexo/<int:anexo_id>')
def subdependencias_por_anexo(anexo_id):
    subdeps = Subdependencia.query.filter_by(id_anexo=anexo_id).all()
    return jsonify([{"id": s.id, "nombre": s.nombre} for s in subdeps])

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
    }

# --- Listar mobiliario por subdependencia ---
@app.route('/api/mobiliario_por_subdependencia/<int:sub_id>', methods=['GET'])
def mobiliario_por_subdependencia(sub_id):
    items = Mobiliario.query.filter_by(ubicacion_id=sub_id)\
                            .order_by(Mobiliario.id.asc()).all()
    return jsonify([mob_to_dict(m) for m in items])



@app.route('/api/mobiliario_filtrado', methods=['POST'])
def mobiliario_filtrado():
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

    query = """
        SELECT m.descripcion, m.id
        FROM mobiliario m
        JOIN subdependencias sd ON m.ubicacion_id = sd.id
        JOIN anexos a ON sd.id_anexo = a.id
        WHERE a.id = %s AND sd.id = %s
    """

    for campo in filtros:
        if campo and campo != "faltante":
            query += f" AND m.{campo} = TRUE"

    if not incluir_faltantes:
        query += " AND (m.faltante IS NULL OR m.faltante = FALSE)"

    conn = db.engine.raw_connection()
    cur = conn.cursor()
    cur.execute(query, (anexo_id, sub_id))
    mobiliarios = cur.fetchall()
    conn.close()

    return render_template_string("""
    <table class="w-full table-auto border border-gray-300 text-sm mt-4">
      <thead class="bg-gray-100">
        <tr>
          <th class="border px-2 py-1">Descripción</th>
          <th class="border px-2 py-1">ID</th>
        </tr>
      </thead>
      <tbody>
        {% for m in mobiliarios %}
        <tr class="hover:bg-gray-50">
          <td class="border px-2 py-1">{{ m[0] }}</td>
          <td class="border px-2 py-1">{{ m[1] }}</td>
        </tr>
        {% endfor %}
        {% if mobiliarios|length == 0 %}
        <tr><td colspan="2" class="text-center p-4 text-gray-500">No se encontraron resultados.</td></tr>
        {% endif %}
      </tbody>
    </table>
    """, mobiliarios=mobiliarios)







# EJECUCIÓN
#if __name__ == '__main__':
 #   app.run(debug=True)





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
        host="dpg-cv8oiprqf0us73bbbbfg-a.oregon-postgres.render.com",
        database="patrimonio_ppfk",
        user="patrimonio_ppfk_user",
        password="SabopRq1mqHqRXBZaZBaWsEcqfHYJWM2"
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
        id_clase = int(data['id_clase']) if data['id_clase'] else None

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
                int(data['id_clase']) if data['id_clase'] else None,
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
app.secret_key = 'clave-secreta-segura-123'  # 🔐 solo esta instancia
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

# VER ANEXOS Y FILTRAR MOBILIARIOS POR RUBRO Y CLASE
@app.route('/api/mobiliario_por_anexo/<int:anexo_id>', methods=['GET'])
def mobiliario_por_anexo(anexo_id):
    rubro_id = request.args.get('rubro_id', type=int)
    clase_id = request.args.get('clase_id', type=int)   # 👈 nuevo
    sub_id = request.args.get('subdependencia_id', type=int)
    descripcion = request.args.get('descripcion', type=str)

    query = db.session.query(
        Mobiliario.id,
        Mobiliario.descripcion,
        Mobiliario.estado_conservacion,
        Mobiliario.estado_control,
        Rubro.id_rubro,
        Rubro.nombre.label("rubro"),
        ClaseBien.id_clase,
        ClaseBien.descripcion.label("clase_bien"),
        Subdependencia.id.label("subdependencia_id"),
        Subdependencia.nombre.label("subdependencia"),
        Anexo.nombre.label("anexo")
    ).join(
        Subdependencia, Mobiliario.ubicacion_id == Subdependencia.id
    ).join(
        Anexo, Subdependencia.id_anexo == Anexo.id
    ).outerjoin(
        Rubro, Mobiliario.rubro_id == Rubro.id_rubro
    ).outerjoin(
        ClaseBien, Mobiliario.clase_bien_id == ClaseBien.id_clase
    ).filter(
        Anexo.id == anexo_id
    )

    # Filtros dinámicos
    if rubro_id:
        query = query.filter(Mobiliario.rubro_id == rubro_id)
    if clase_id:
        query = query.filter(Mobiliario.clase_bien_id == clase_id)  # 👈 nuevo filtro
    if sub_id:
        query = query.filter(Subdependencia.id == sub_id)
    if descripcion:
        query = query.filter(Mobiliario.descripcion.ilike(f"%{descripcion}%"))

    resultados = [dict(r._mapping) for r in query.all()]
    return jsonify(resultados)




# ▶️ Ejecutar con python app.py
if __name__ == '__main__':
    app.run(debug=True)



