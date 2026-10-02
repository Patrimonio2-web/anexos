import time
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import app as backend


class ReparacionesMobiliarioTests(unittest.TestCase):
    @staticmethod
    def _authenticated_client(username="admin-prueba", role="admin"):
        client = backend.app.test_client()
        with client.session_transaction() as current_session:
            current_session["username"] = username
            current_session["role"] = role
            current_session["user_checked_at"] = time.time()
            current_session["last_activity"] = time.time()
            current_session["csrf_token"] = "test-csrf-token"
        return client

    def test_history_requires_a_session(self):
        response = backend.app.test_client().get(
            "/api/mobiliario/100/reparaciones"
        )
        self.assertEqual(response.status_code, 401)

    def test_read_only_roles_can_read_repair_history(self):
        for username, role in (("lector", "viewer"), ("Comisario", "comisario")):
            with self.subTest(role=role):
                rows_result = MagicMock()
                rows_result.mappings.return_value.all.return_value = []
                mobiliario = SimpleNamespace(para_reparacion=True)

                with patch.object(
                    backend, "_ensure_reparaciones_mobiliario_table"
                ), patch.object(
                    backend.db.session, "get", return_value=mobiliario
                ), patch.object(
                    backend.db.session, "execute", return_value=rows_result
                ):
                    response = self._authenticated_client(username, role).get(
                        "/api/mobiliario/100/reparaciones"
                    )

                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.get_json()["para_reparacion"])
                self.assertEqual(response.get_json()["reparaciones"], [])

    def test_viewer_cannot_create_a_repair(self):
        response = self._authenticated_client(role="viewer").post(
            "/api/mobiliario/100/reparaciones",
            json={
                "reparado_por": "Servicio tecnico",
                "trabajo_realizado": "Cambio de fuente",
                "fecha_salida": "2026-10-01",
            },
            headers={"X-CSRF-Token": "test-csrf-token"},
        )
        self.assertEqual(response.status_code, 403)

    def test_return_date_cannot_precede_departure(self):
        mobiliario = SimpleNamespace(para_reparacion=False)
        with patch.object(
            backend, "_ensure_reparaciones_mobiliario_table"
        ), patch.object(
            backend.db.session, "get", return_value=mobiliario
        ):
            response = self._authenticated_client().post(
                "/api/mobiliario/100/reparaciones",
                json={
                    "reparado_por": "Servicio tecnico",
                    "trabajo_realizado": "Cambio de fuente",
                    "fecha_salida": "2026-10-02",
                    "fecha_retorno": "2026-10-01",
                },
                headers={"X-CSRF-Token": "test-csrf-token"},
            )

        self.assertEqual(response.status_code, 400)
        self.assertIn("anterior", response.get_json()["error"])


if __name__ == "__main__":
    unittest.main()
