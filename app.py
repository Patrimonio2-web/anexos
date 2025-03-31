from flask import Flask, render_template, request, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_cors import CORS
from datetime import datetime

app = Flask(__name__)
CORS(app)

# Configuración de la base de datos PostgreSQL
app.config['SQLALCHEMY_DATABASE_URI'] = "postgresql://patrimonio_ppfk_user:SabopRq1mqHqRXBZaZBaWsEcqfHYJWM2@dpg-cv8oiprqf0us73bbbbfg-a.oregon-postgres.render.com/patrimonio_ppfk"
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)

# MODELOS
class Anexo(db.Model):
    __tablename__ = 'anexos'
    id = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(255), nullable=False)
    direccion = db.Column(db.Text)

class Subdependencia(db.Model):
    __tablename__ = 'subdependencias'
    id = db.Column(db.Integer, primary_key=True)
    id_anexo = db.Column(db.Integer, db.ForeignKey('anexos.id', ondelete="CASCADE"), nullable=False)
    nombre = db.Column(db.String(255), nullable=False)

class Mobiliario(db.Model):
    __tablename__ = 'mobiliario'

    id = db.Column(db.String(50), primary_key=True)
    ubicacion_id = db.Column(db.Integer, db.ForeignKey('subdependencias.id', ondelete="SET NULL"))
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

# RUTAS

@app.route('/')
def index():
    return render_template('index.html')


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

# --- MOBILIARIO ---
@app.route('/api/mobiliario', methods=['POST'])
def guardar_mobiliario():
    try:
        data = request.json

        if not data.get("id") or not data.get("resolucion_numero") or not data.get("resolucion_tipo"):
            return jsonify({"error": "Campos obligatorios faltantes"}), 400

        tipo = data.get("resolucion_tipo").upper()
        if tipo == "PSA":
            tipo = "P.S.A"
        resolucion_texto = f"Resol Nº{data.get('resolucion_numero')} {tipo}"

        nuevo = Mobiliario(
            id=data.get("id"),
            ubicacion_id=data.get("ubicacion_id"),
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

        db.session.add(nuevo)
        db.session.commit()

        return jsonify({"mensaje": "Registro guardado exitosamente"}), 201

    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500

# --- OPCIONAL: GET para visualizar datos cargados ---
@app.route('/api/mobiliario', methods=['GET'])
def listar_mobiliario():
    registros = Mobiliario.query.all()
    resultado = []
    for r in registros:
        resultado.append({
            "id": r.id,
            "descripcion": r.descripcion,
            "resolucion": r.resolucion,
            "fecha_resolucion": r.fecha_resolucion.isoformat() if r.fecha_resolucion else None,
            "estado_conservacion": r.estado_conservacion,
            "comentarios": r.comentarios,
            "foto_url": r.foto_url
        })
    return jsonify(resultado)


# EJECUCIÓN
if __name__ == '__main__':
    app.run(debug=True)
