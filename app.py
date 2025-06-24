from flask import Flask, render_template, request, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_cors import CORS
from datetime import datetime
from werkzeug.utils import secure_filename
import cloudinary
import cloudinary.uploader
import os
import tempfile

app = Flask(__name__)
CORS(app)


# Configuración de la base de datos PostgreSQL
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
    _tablename_ = 'rubros'
    id_rubro = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.Text, nullable=False)

class ClaseBien(db.Model):
    _tablename_ = 'clases_bienes'
    id_clase = db.Column(db.Integer, primary_key=True)
    id_rubro = db.Column(db.Integer, db.ForeignKey('rubros.id_rubro'))
    descripcion = db.Column(db.Text, nullable=False)

class Anexo(db.Model):
    _tablename_ = 'anexos'
    id = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(255), nullable=False)
    direccion = db.Column(db.Text)

class Subdependencia(db.Model):
    _tablename_ = 'subdependencias'
    id = db.Column(db.Integer, primary_key=True)
    id_anexo = db.Column(db.Integer, db.ForeignKey('anexos.id', ondelete="CASCADE"), nullable=False)
    nombre = db.Column(db.String(255), nullable=False)

class Mobiliario(db.Model):
    _tablename_ = 'mobiliario'
    id = db.Column(db.String(50), primary_key=True)
    ubicacion_id = db.Column(db.Integer)
    id_clase = db.Column(db.Integer, db.ForeignKey('clases_bienes.id_clase'))  # 👈 Nuevo
    descripcion = db.Column(db.Text)
    resolucion = db.Column(db.Text)
    fecha_resolucion = db.Column(db.Date)
    estado_conservacion = db.Column(db.String(20))

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


#eliminar mobiliario ---------------------------------------------------------------
@app.route('/api/mobiliario/<string:id>', methods=['DELETE'])
def eliminar_mobiliario(id):
    mobiliario = Mobiliario.query.get_or_404(id)
    try:
        mobiliario = Mobiliario.query.get_or_404(id)
        db.session.delete(mobiliario)
        db.session.commit()
        return jsonify({"mensaje": "Registro eliminado correctamente"}), 200

    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500




# API para obtener todos los rubros ordenados por ID
@app.route('/api/rubros', methods=['GET'])
def obtener_rubros():
    rubros = Rubro.query.order_by(Rubro.id_rubro).all()
    data = [{'id_rubro': r.id_rubro, 'nombre': r.nombre} for r in rubros]
    return jsonify(data)

# API para obtener clases por rubro
@app.route('/api/clases-por-rubro', methods=['GET'])
def clases_por_rubro():
    rubro_id = request.args.get('rubro_id', type=int)
    if not rubro_id:
        return jsonify({'error': 'Falta el parámetro rubro_id'}), 400

    clases = ClaseBien.query.filter_by(id_rubro=rubro_id).order_by(ClaseBien.descripcion).all()
    data = [{'id_clase': c.id_clase, 'descripcion': c.descripcion, 'id_rubro': c.id_rubro} for c in clases]
    return jsonify(data)

# API nueva: devuelva un solo mobiliario por su id y permitirá precargar rubro y clase en el formulario al editar
@app.route('/api/mobiliario/<string:id>', methods=['GET'])
def obtener_mobiliario_por_id(id):
    resultado = db.session.query(
        Mobiliario,
        Subdependencia.nombre.label("subdependencia"),
        Anexo.nombre.label("anexo"),
        ClaseBien.id_clase,
        ClaseBien.descripcion.label("clase"),
        Rubro.id_rubro,
        Rubro.nombre.label("rubro")
    ).join(
        Subdependencia, Mobiliario.ubicacion_id == Subdependencia.id
    ).join(
        Anexo, Subdependencia.id_anexo == Anexo.id
    ).outerjoin(
        ClaseBien, ClaseBien.id_clase == Mobiliario.id_clase  # necesitas tener este campo en tu modelo
    ).outerjoin(
        Rubro, Rubro.id_rubro == ClaseBien.id_rubro
    ).filter(
        Mobiliario.id == id
    ).first()

    if not resultado:
        return jsonify({"error": "Mobiliario no encontrado"}), 404

    m, sub_nombre, anexo_nombre, id_clase, clase_desc, id_rubro, rubro_nombre = resultado

    return jsonify({
        "id": m.id,
        "descripcion": m.descripcion,
        "resolucion": m.resolucion,
        "fecha_resolucion": m.fecha_resolucion.isoformat() if m.fecha_resolucion else None,
        "estado_conservacion": m.estado_conservacion,
        "comentarios": m.comentarios,
        "foto_url": m.foto_url,
        "ubicacion_id": m.ubicacion_id,
        "subdependencia": sub_nombre,
        "anexo": anexo_nombre,
        "no_dado": m.no_dado,
        "para_reparacion": m.para_reparacion,
        "para_baja": m.para_baja,
        "faltante": m.faltante,
        "sobrante": m.sobrante,
        "problema_etiqueta": m.problema_etiqueta,
        "fecha_creacion": m.fecha_creacion.isoformat(),
        "fecha_actualizacion": m.fecha_actualizacion.isoformat(),
        "id_clase": id_clase,
        "clase": clase_desc,
        "id_rubro": id_rubro,
        "rubro": rubro_nombre
    })


#carga de nuevo registro---------
# Ruta para registrar un nuevo mobiliario
@app.route('/api/mobiliario', methods=['POST'])
def registrar_mobiliario():
    try:
        data = request.json

        # Validación de campos obligatorios
        if not data.get("id") or not data.get("resolucion_numero") or not data.get("resolucion_tipo"):
            return jsonify({"error": "Campos obligatorios faltantes"}), 400

        # Composición del texto de resolución
        tipo = data.get("resolucion_tipo").upper()
        if tipo == "PSA":
            tipo = "P.S.A"
        resolucion_texto = f"Resol Nº{data.get('resolucion_numero')} {tipo}"

        # Creación del nuevo registro
        nuevo = Mobiliario(
            id=data.get("id"),
            ubicacion_id=data.get("ubicacion_id"),
            clase_bien_id=data.get("id_clase"),  # Nombre correcto de la columna en la BD
            descripcion=data.get("descripcion"),
            resolucion=resolucion_texto,
            fecha_resolucion=data.get("fecha_resolucion"),
            estado_conservacion=data.get("estado_conservacion"),
            no_dado=data.get("no_dado", False),
            para_reparacion=data.get("reparacion", False),
            para_baja=data.get("para_baja", False),
            faltante=data.get("faltante", False),
            sobrante=data.get("sobrante", False),
            problema_etiqueta=data.get("etiqueta", False),
            comentarios=data.get("comentarios"),
            foto_url=data.get("foto_url", "")
        )

        # Guardar en base de datos
        db.session.add(nuevo)
        db.session.commit()
        return jsonify({"mensaje": "Registro guardado exitosamente"}), 201

    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500

#---------busca por impresora
@app.route('/api/buscar-clase', methods=['GET'])
def buscar_clase():
    query = request.args.get('query', '', type=str)

    if not query:
        return jsonify({'error': 'Falta el parámetro query'}), 400

    clases = ClaseBien.query.filter(ClaseBien.descripcion.ilike(f'%{query}%')).order_by(ClaseBien.descripcion).all()

    data = [{
        'id_clase': c.id_clase,
        'descripcion': c.descripcion,
        'id_rubro': c.id_rubro
    } for c in clases]

    return jsonify(data)
#-------busca por id clase 109
@app.route('/api/clase/<int:id_clase>', methods=['GET'])
def obtener_clase_por_id(id_clase):
    clase = ClaseBien.query.get(id_clase)

    if not clase:
        return jsonify({'error': 'Clase no encontrada'}), 404

    rubro = Rubro.query.get(clase.id_rubro)

    return jsonify({
        'id_clase': clase.id_clase,
        'descripcion': clase.descripcion,
        'id_rubro': clase.id_rubro,
        'rubro': rubro.nombre if rubro else 'Sin rubro'
    })


# --- ANEXOS ---
@app.route('/api/anexos', methods=['POST'])
def agregar_anexo():
    data = request.json
    nuevo_anexo = Anexo(id=data['id'], nombre=data['nombre'], direccion=data.get('direccion'))
    db.session.add(nuevo_anexo)
    db.session.commit()
    return jsonify({'mensaje': 'Anexo agregado correctamente'}), 201

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

@app.route('/api/anexos/<int:id_anexo>/subdependencias', methods=['GET'])
def obtener_subdependencias(id_anexo):
    subdependencias = Subdependencia.query.filter_by(id_anexo=id_anexo).all()
    return jsonify([{'id': sub.id, 'nombre': sub.nombre} for sub in subdependencias])

# --- nuevo MOBILIARIO ------------------------------------
#  @app.route('/api/mobiliario', methods=['GET'])
# def listar_mobiliario():
#     registros = Mobiliario.query.all()
#     resultado = []
#     for r in registros:
#         resultado.append({
#             "id": r.id,
#             "descripcion": r.descripcion,
#             "resolucion": r.resolucion,
#             "fecha_resolucion": r.fecha_resolucion.isoformat() if r.fecha_resolucion else None,
#             "estado_conservacion": r.estado_conservacion,
#             "comentarios": r.comentarios,
#             "foto_url": r.foto_url
#         })
#     return jsonify(resultado) 

# api modificada
@app.route('/api/mobiliario', methods=['GET'])
def listar_mobiliario():
    try:
        resultados = db.session.query(
            Mobiliario,
            Subdependencia.nombre.label("subdependencia"),
            Anexo.nombre.label("anexo")
        ).join(
            Subdependencia, Mobiliario.ubicacion_id == Subdependencia.id
        ).join(
            Anexo, Subdependencia.id_anexo == Anexo.id
        ).all()

        salida = []
        for m, sub_nombre, anexo_nombre in resultados:
            salida.append({
                "id": m.id,
                "descripcion": m.descripcion,
                "resolucion": m.resolucion,
                "fecha_resolucion": m.fecha_resolucion.isoformat() if m.fecha_resolucion else None,
                "estado_conservacion": m.estado_conservacion,
                "comentarios": m.comentarios,
                "foto_url": m.foto_url,
                "ubicacion_id": m.ubicacion_id,
                "subdependencia": sub_nombre,
                "anexo": anexo_nombre,
                "no_dado": m.no_dado,
                "para_reparacion": m.para_reparacion,
                "para_baja": m.para_baja,
                "faltante": m.faltante,
                "sobrante": m.sobrante,
                "problema_etiqueta": m.problema_etiqueta,
                "fecha_creacion": m.fecha_creacion.isoformat() if m.fecha_creacion else None,
                "fecha_actualizacion": m.fecha_actualizacion.isoformat() if m.fecha_actualizacion else None,
            })

        return jsonify(salida)

    except Exception as e:
        print("🔴 Error en /api/mobiliario:", str(e))
        return jsonify({"error": str(e)}), 500

# Eliminar patrimonio (mobiliario)
@app.route('/api/patrimonio/<int:id>', methods=['DELETE'])
def eliminar_patrimonio(id):
    try:
        registro = db.session.get(id)
        if not registro:
            return jsonify({'error': 'Registro no encontrado'}), 404
        db.session.delete(registro)
        db.session.commit()
        return jsonify({'mensaje': 'Registro eliminado exitosamente'}), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500
        
#--------- editar mobiliario -----------------------------------------------------
@app.route('/api/mobiliario/<string:id>', methods=['PUT'])
def editar_mobiliario(id):
    mobiliario = Mobiliario.query.get_or_404(id)
    try:
        mobiliario = Mobiliario.query.get_or_404(id)
        data = request.json

        tipo = data.get("resolucion_tipo", "").upper()
        if tipo == "PSA":
            tipo = "P.S.A"
        resolucion_texto = f"Resol Nº{data.get('resolucion_numero')} {tipo}"

        mobiliario.ubicacion_id = data.get("ubicacion_id", mobiliario.ubicacion_id)
        mobiliario.descripcion = data.get("descripcion", mobiliario.descripcion)
        mobiliario.resolucion = resolucion_texto
        mobiliario.fecha_resolucion = data.get("fecha_resolucion", mobiliario.fecha_resolucion)
        mobiliario.estado_conservacion = data.get("estado_conservacion", mobiliario.estado_conservacion)
        mobiliario.no_dado = data.get("no_dado", mobiliario.no_dado)
        mobiliario.para_reparacion = data.get("reparacion", mobiliario.para_reparacion)
        mobiliario.para_baja = data.get("para_baja", mobiliario.para_baja)
        mobiliario.faltante = data.get("faltante", mobiliario.faltante)
        mobiliario.sobrante = data.get("sobrante", mobiliario.sobrante)
        mobiliario.problema_etiqueta = data.get("etiqueta", mobiliario.problema_etiqueta)
        mobiliario.comentarios = data.get("comentarios", mobiliario.comentarios)
        mobiliario.foto_url = data.get("foto_url", mobiliario.foto_url)
        mobiliario.id_clase = data.get("id_clase", mobiliario.id_clase) # --- nuevo

        db.session.commit()
        return jsonify({"mensaje": "Registro actualizado correctamente"}), 200

    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500



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



# Última ruta nueva prueba sin LIMIT
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
        ORDER BY m.fecha_creacion DESC;
        """

        conn = db.engine.raw_connection()
        cur  = conn.cursor()
        cur.execute(query)
        columns  = [col[0] for col in cur.description]
        results  = [dict(zip(columns, row)) for row in cur.fetchall()]
        cur.close()
        conn.close()

        return jsonify(results)
    except Exception as e:
        print("🔴 Error en /api/mobiliario/ultimos:", e)
        return jsonify({'error': str(e)}), 500



# EJECUCIÓN
if __name__ == '__main__':
    app.run(debug=True)

