import unittest

from flask import Flask
from sqlalchemy import create_engine, text

from assistant_api import count_inventory, normalize


class AssistantApiTests(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.engine = create_engine("sqlite://")
        with self.engine.begin() as conn:
            conn.execute(text("CREATE TABLE anexos (id INTEGER, nombre TEXT)"))
            conn.execute(text("CREATE TABLE subdependencias (id INTEGER, id_anexo INTEGER, nombre TEXT)"))
            conn.execute(text("CREATE TABLE clases_bienes (id_clase INTEGER, descripcion TEXT)"))
            conn.execute(text("CREATE TABLE mobiliario (id TEXT, ubicacion_id INTEGER, clase_bien_id INTEGER, descripcion TEXT, faltante BOOLEAN, para_reparacion BOOLEAN, para_baja BOOLEAN, sobrante BOOLEAN, problema_etiqueta BOOLEAN)"))
            conn.execute(text("INSERT INTO anexos VALUES (1, 'Casa Central'), (2, 'Anexo Norte')"))
            conn.execute(text("INSERT INTO subdependencias VALUES (10, 1, 'Secretaria'), (20, 2, 'Archivo')"))
            conn.execute(text("INSERT INTO clases_bienes VALUES (1, 'SILLA')"))
            conn.execute(text("INSERT INTO mobiliario VALUES ('5209',10,1,'SILLA DE OFICINA',0,0,0,0,0), ('5210',10,1,'SILLA DE MADERA',0,0,0,0,0), ('5211',20,1,'SILLA',0,0,0,0,0)"))

    def test_count_filters_annex_and_hides_details(self):
        with self.app.app_context(), self.engine.connect() as conn:
            response = count_inventory(conn, normalize("Cuantas sillas hay en anexo Casa Central?"))
            data = response.get_json()
        self.assertIn("Hay 2", data["text"])
        self.assertNotIn("details", data)

    def test_detail_request_lists_only_matching_assets(self):
        with self.app.app_context(), self.engine.connect() as conn:
            response = count_inventory(conn, normalize("Cuantas sillas hay en anexo Casa Central? Listalas"))
            data = response.get_json()
        self.assertEqual([item["href"] for item in data["details"]], ["/patrimonio/guardado/5210", "/patrimonio/guardado/5209"])


if __name__ == "__main__":
    unittest.main()
