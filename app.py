from flask import Flask, render_template, request, redirect, url_for, send_file, jsonify,session, flash
from flask_sqlalchemy import SQLAlchemy
from flask_cors import CORS
from datetime import datetime
from werkzeug.utils import secure_filename
import cloudinary
import cloudinary.uploader
import os
import tempfile
from datetime import timedelta
from werkzeug.security import check_password_hash, generate_password_hash
import psycopg2.extras

app = Flask(__name__)
CORS(app)


# Configuración de la base de datos PostgreSQL-
app.config['SQLALCHEMY_DATABASE_URI'] = "postgresql://patrimonio_ppfk_user:SabopRq1mqHqRXBZaZBaWsEcqfHYJWM2@dpg-cv8oiprqf0us73bbbbfg-a.oregon-postgres.render.com/patrimonio_ppfk"
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # Limite de 16MB para archivos

# Configuración Cloudinary
cloudinary.config(
    cloud_name="deokbrzem",
    api_key="628521442744972",
    api_secret="UI7D6jgGKoAzjB_NLAgTi1XAwXQ"
)

db = SQLAlchemy(app)

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



class Subdependencia(db.Model):
    __tablename__ = 'subdependencias'
    id = db.Column(db.Integer, primary_key=True)
    id_anexo = db.Column(db.Integer, db.ForeignKey('anexos.id', ondelete='CASCADE'), nullable=False)
    nombre = db.Column(db.String(255), nullable=False)
    piso = db.Column(db.Integer)  # 👈 este campo está en tu base (PDF), podés incluirlo si lo necesitás


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
            m.comentarios,
            m.foto_url,
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






# API para eliminar un registro de patrimonio-----------------------------
@app.route('/api/patrimonio/<string:id>', methods=['DELETE'])
def eliminar_patrimonio(id):
    try:
        registro = db.session.get(Mobiliario, id)
        if not registro:
            return jsonify({'error': 'Registro no encontrado'}), 404
        db.session.delete(registro)
        db.session.commit()
        return jsonify({'mensaje': 'Registro eliminado exitosamente'}), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500




# API para editar mobiliario-----------------------------------------------------

from datetime import datetime, timedelta

@app.route('/api/mobiliario/<string:id>', methods=['PUT'])
def editar_mobiliario(id):
    mobiliario = Mobiliario.query.get_or_404(id)
    try:
        data = request.json

        # ✅ Evitar que cambien el ID manualmente (por seguridad)
        if 'id' in data and data['id'] != id:
            return jsonify({"error": "No se puede modificar el ID del bien"}), 400

        # ✅ Validar campos obligatorios
        campos_obligatorios = ['ubicacion_id', 'rubro_id', 'clase_bien_id']
        for campo in campos_obligatorios:
            if data.get(campo) is None:
                return jsonify({"error": f"Falta el campo obligatorio: {campo}"}), 400

        # 🕒 Hora de Argentina (UTC-3)
        ahora = (datetime.utcnow() - timedelta(hours=3)).strftime("%Y-%m-%d %H:%M:%S")
        historial = mobiliario.historial_movimientos or ""

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

        db.session.commit()
        return jsonify({"mensaje": "Registro actualizado correctamente"}), 200

    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500






# Ruta para registrar un nuevo mobiliario
# Esta ruta permite registrar un nuevo mobiliario con los datos proporcionados en el cuerpo de la 
@app.route('/api/mobiliario', methods=['POST'])
def registrar_mobiliario():
    try:
        data = request.json
        print("🟢 Data recibida en /api/mobiliario:", data)

        # Diccionario de tipos de resolución formateados
        tipos_resolucion = {
            "PSA": "P.S.A",
            "DECRETO": "Decreto",
            "SL": "S.L",
            "PSL": "P.S.L"
        }

        tipo = data.get("resolucion_tipo", "").upper()
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
            foto_url=data.get("foto_url", "")
        )

        db.session.add(nuevo)
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
        "problema_etiqueta": m.problema_etiqueta,
        "fecha_creacion": (m.fecha_creacion - timedelta(hours=3)).strftime("%d/%m/%Y %H:%M") if m.fecha_creacion else None,
        "fecha_actualizacion": (m.fecha_actualizacion - timedelta(hours=3)).strftime("%d/%m/%Y %H:%M") if m.fecha_actualizacion else None,
        "clase_bien_id": id_clase,
        "clase": clase_desc,
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
    import os
    size_px = 283  # 24 mm a 300 dpi
    etiqueta = Image.new('RGB', (size_px, size_px), 'black')
    draw = ImageDraw.Draw(etiqueta)

    try:
        # Fuentes compatibles con Linux/Render
        font_titulo = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 24)
        font_id = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 50)
        font_fecha = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 35)
    except:
        # En caso de falla, usar fuente por defecto
        font_titulo = font_id = font_fecha = ImageFont.load_default()

    # Función para centrar texto
    def centrar(texto, fuente, y):
        w, h = draw.textbbox((0, 0), texto, font=fuente)[2:]
        draw.text(((size_px - w) // 2, y), texto, font=fuente, fill='white')
        return y + h + 4

    y = 5
    y = centrar("Dir. de Patrimonio", font_titulo, y)
    y = centrar(f"{id.zfill(6)}", font_id, y)

    # Código 
    qr_size = 150
    qr = qrcode.make(f"https://anexos.onrender.com/ver?id={id}").resize((qr_size, qr_size))
    qr_y = (size_px - qr_size) // 2 + 20
    etiqueta.paste(qr, ((size_px - qr_size) // 2, qr_y))

    # Año actual
    fecha = datetime.now().strftime("%Y")
    centrar(fecha, font_fecha, size_px - 44)

    # Convertir a imagen PNG
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




from functools import wraps
from flask import redirect, url_for

def login_required(f):
    @wraps(f)
    def wrapped(*args, **kwargs):
        if 'username' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return wrapped


# --------- AUTH -------------------------------------------------------------------------------------
@app.route('/login', methods=['GET', 'POST'])
def login():
    # si ya está logueado
    if session.get('username'):
        return redirect(url_for('inicio'))

    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')

        try:
            conn, cur = get_conn_dict()
            cur.execute("""
                SELECT id, username, password, role, COALESCE(activo, TRUE) AS activo
                FROM usuarios
                WHERE username = %s
                LIMIT 1
            """, (username,))
            user = cur.fetchone()
            cur.close(); conn.close()
        except Exception as e:
            flash(f'Error de conexión: {e}', 'error')
            return render_template('login.html')

        if not user:
            flash('Usuario o contraseña incorrectos', 'error')
            return render_template('login.html')

        if not user['activo']:
            flash('Usuario inactivo. Contacte al administrador.', 'error')
            return render_template('login.html')

        # password hasheada
        if check_password_hash(user['password'], password):
            session.permanent = True
            session['username'] = user['username']
            session['role'] = user['role']
            return redirect(url_for('inicio'))

        flash('Usuario o contraseña incorrectos', 'error')

    return render_template('login.html')


@app.route('/inicio')
@login_required
def inicio():
    return render_template('inicio.html')  # o tu dashboard principal


@app.route('/logout')
def logout():
    session.pop('username', None)
    session.pop('role', None)
    flash('Has cerrado sesión correctamente.', 'success')
    return redirect(url_for('login'))
# --------- /AUTH ---------------------------------------------------------------------------

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




# ▶️ Ejecutar con python app.py
if __name__ == '__main__':
    app.run(debug=True)



