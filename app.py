from flask import Flask, request, jsonify
from flask_cors import CORS  # Importar CORS
import psycopg2

app = Flask(__name__)
CORS(app)  # Habilitar CORS para todas las rutas

# Cadena de conexión
DATABASE_URL = "postgresql://patrimonio_ppfk_user:SabopRq1mqHqRXBZaZBaWsEcqfHYJWM2@dpg-cv8oiprqf0us73bbbbfg-a.oregon-postgres.render.com/patrimonio_ppfk"

# Función para obtener la conexión a la base de datos
def get_db_connection():
    conn = psycopg2.connect(DATABASE_URL, sslmode="require")
    return conn

# API para obtener anexos
@app.route("/api/anexos", methods=["GET"])
def get_anexos():
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM anexos;")
    anexos = cur.fetchall()
    cur.close()
    conn.close()
    return jsonify(anexos)

# API para obtener subdependencias de un anexo
@app.route("/api/subdependencias/<int:anexo_id>", methods=["GET"])
def get_subdependencias(anexo_id):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM subdependencias WHERE anexo_id = %s;", (anexo_id,))
    subdependencias = cur.fetchall()
    cur.close()
    conn.close()
    return jsonify(subdependencias)

# API para crear un nuevo anexo
@app.route("/api/anexos", methods=["POST"])
def create_anexo():
    data = request.get_json()
    nombre = data.get("nombre")

    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("INSERT INTO anexos (nombre) VALUES (%s) RETURNING id;", (nombre,))
    anexo_id = cur.fetchone()[0]
    conn.commit()
    cur.close()
    conn.close()

    return jsonify({"id": anexo_id, "nombre": nombre}), 201

# API para crear una nueva subdependencia
@app.route("/api/subdependencias", methods=["POST"])
def create_subdependencia():
    data = request.get_json()
    nombre = data.get("nombre")
    anexo_id = data.get("anexo_id")

    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("INSERT INTO subdependencias (nombre, anexo_id) VALUES (%s, %s) RETURNING id;", (nombre, anexo_id))
    subdependencia_id = cur.fetchone()[0]
    conn.commit()
    cur.close()
    conn.close()

    return jsonify({"id": subdependencia_id, "nombre": nombre, "anexo_id": anexo_id}), 201

# API para eliminar un anexo
@app.route("/api/anexos/<int:anexo_id>", methods=["DELETE"])
def delete_anexo(anexo_id):
    conn = get_db_connection()
    cur = conn.cursor()

    # Primero, eliminar las subdependencias asociadas al anexo
    cur.execute("DELETE FROM subdependencias WHERE anexo_id = %s;", (anexo_id,))
    
    # Luego, eliminar el anexo
    cur.execute("DELETE FROM anexos WHERE id = %s;", (anexo_id,))
    conn.commit()
    cur.close()
    conn.close()

    return jsonify({"message": "Anexo eliminado correctamente"}), 200

# API para eliminar una subdependencia
@app.route("/api/subdependencias/<int:subdependencia_id>", methods=["DELETE"])
def delete_subdependencia(subdependencia_id):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM subdependencias WHERE id = %s;", (subdependencia_id,))
    conn.commit()
    cur.close()
    conn.close()

    return jsonify({"message": "Subdependencia eliminada correctamente"}), 200

# API para editar un anexo
@app.route("/api/anexos/<int:anexo_id>", methods=["PUT"])
def update_anexo(anexo_id):
    data = request.get_json()
    nombre = data.get("nombre")

    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("UPDATE anexos SET nombre = %s WHERE id = %s RETURNING id, nombre;", (nombre, anexo_id))
    updated_anexo = cur.fetchone()
    conn.commit()
    cur.close()
    conn.close()

    if updated_anexo:
        return jsonify({"id": updated_anexo[0], "nombre": updated_anexo[1]}), 200
    else:
        return jsonify({"error": "Anexo no encontrado"}), 404

# API para editar una subdependencia
@app.route("/api/subdependencias/<int:subdependencia_id>", methods=["PUT"])
def update_subdependencia(subdependencia_id):
    data = request.get_json()
    nombre = data.get("nombre")
    anexo_id = data.get("anexo_id")

    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        "UPDATE subdependencias SET nombre = %s, anexo_id = %s WHERE id = %s RETURNING id, nombre, anexo_id;",
        (nombre, anexo_id, subdependencia_id)
    )
    updated_subdependencia = cur.fetchone()
    conn.commit()
    cur.close()
    conn.close()

    if updated_subdependencia:
        return jsonify({
            "id": updated_subdependencia[0],
            "nombre": updated_subdependencia[1],
            "anexo_id": updated_subdependencia[2]
        }), 200
    else:
        return jsonify({"error": "Subdependencia no encontrada"}), 404

if __name__ == "__main__":
    app.run(debug=True)