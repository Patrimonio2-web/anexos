from flask import Flask, render_template, request, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

# Configuración de la base de datos PostgreSQL
app.config['SQLALCHEMY_DATABASE_URI'] = "postgresql://patrimonio_ppfk_user:SabopRq1mqHqRXBZaZBaWsEcqfHYJWM2@dpg-cv8oiprqf0us73bbbbfg-a.oregon-postgres.render.com/patrimonio_ppfk"
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)

# Modelos de la base de datos
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

# Ruta principal para la vista
@app.route('/')
def index():
    return render_template('index.html')

# Ruta para agregar un anexo
@app.route('/api/anexos', methods=['POST'])
def agregar_anexo():
    data = request.json
    nuevo_anexo = Anexo(id=data['id'], nombre=data['nombre'], direccion=data.get('direccion'))
    db.session.add(nuevo_anexo)
    db.session.commit()
    return jsonify({'mensaje': 'Anexo agregado correctamente'}), 201

# Ruta para obtener anexos (para selección en frontend)
@app.route('/api/anexos', methods=['GET'])
def obtener_anexos():
    anexos = Anexo.query.all()
    return jsonify([{'id': anexo.id, 'nombre': anexo.nombre} for anexo in anexos])

# Ruta para agregar una subdependencia
@app.route('/api/subdependencias', methods=['POST'])
def agregar_subdependencia():
    data = request.json
    nueva_subdependencia = Subdependencia(id=data['id'], id_anexo=data['id_anexo'], nombre=data['nombre'])
    db.session.add(nueva_subdependencia)
    db.session.commit()
    return jsonify({'mensaje': 'Subdependencia agregada correctamente'}), 201

# Ruta para obtener subdependencias de un anexo específico
@app.route('/api/anexos/<int:id_anexo>/subdependencias', methods=['GET'])
def obtener_subdependencias(id_anexo):
    subdependencias = Subdependencia.query.filter_by(id_anexo=id_anexo).all()
    return jsonify([{'id': sub.id, 'nombre': sub.nombre} for sub in subdependencias])

# Ejecutar la aplicación
if __name__ == '__main__':
    app.run(debug=True)