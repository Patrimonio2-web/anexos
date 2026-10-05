import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import app as backend


class DiputadosTests(unittest.TestCase):
    @staticmethod
    def _client(role="admin"):
        client = backend.app.test_client()
        with client.session_transaction() as current_session:
            current_session["username"] = "usuario-prueba"
            current_session["role"] = role
            current_session["user_checked_at"] = time.time()
            current_session["last_activity"] = time.time()
            current_session["csrf_token"] = "test-csrf-token"
        return client

    def test_viewer_cannot_update_a_deputy(self):
        response = self._client("viewer").patch(
            "/api/diputados/1",
            json={"nombre": "Nombre nuevo"},
            headers={"X-CSRF-Token": "test-csrf-token"},
        )
        self.assertEqual(response.status_code, 403)

    def test_serialization_derives_annex_from_the_linked_office(self):
        office = SimpleNamespace(id=302, id_anexo=300, nombre="DESPACHO DIPUTADO CARLA NOELIA ALIENDRO")
        annex = SimpleNamespace(id=300, nombre="ANEXO II: COPIAPO 266", direccion="COPIAPO 266")
        deputy = SimpleNamespace(
            id=1,
            nombre="Carla Noelia Aliendro",
            departamento="Chamical",
            partido="Agrupación Municipal",
            bloque="Bloque Justicialista",
            mandato="2023 - 2027",
            foto_url="/fotos/aliendro.png",
            ubicacion_id=302,
            ubicacion=office,
            activo=True,
            fecha_creacion=None,
            fecha_actualizacion=None,
        )
        with backend.app.app_context(), patch.object(backend.db.session, "get", return_value=annex):
            result = backend._diputado_json(deputy)

        self.assertEqual(result["ubicacion_id"], 302)
        self.assertEqual(result["anexo_id"], 300)
        self.assertEqual(result["anexo"], annex.nombre)
        self.assertEqual(result["subdependencia"], office.nombre)

    def test_payload_rejects_unknown_office(self):
        payload = {
            "nombre": "Nombre Apellido",
            "departamento": "Capital",
            "partido": "Partido",
            "mandato": "2023 - 2027",
            "ubicacion_id": 999999,
        }
        with backend.app.app_context(), patch.object(backend.db.session, "get", return_value=None):
            with self.assertRaisesRegex(ValueError, "no existe"):
                backend._diputado_payload(payload)


if __name__ == "__main__":
    unittest.main()
