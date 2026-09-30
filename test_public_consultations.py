import time
import unittest
from datetime import datetime
from unittest.mock import MagicMock, patch

import app as backend


class PublicConsultationTests(unittest.TestCase):
    def setUp(self):
        backend.PUBLIC_CONSULTATION_ATTEMPTS.clear()

    @staticmethod
    def _authenticated_client(username, role="admin"):
        client = backend.app.test_client()
        with client.session_transaction() as current_session:
            current_session["username"] = username
            current_session["role"] = role
            current_session["user_checked_at"] = time.time()
            current_session["last_activity"] = time.time()
        return client

    def test_private_inbox_requires_a_session(self):
        response = backend.app.test_client().get("/api/consultas-publicas")
        self.assertEqual(response.status_code, 401)

    def test_private_inbox_rejects_an_unlisted_admin(self):
        response = self._authenticated_client("otro_admin").get(
            "/api/consultas-publicas"
        )
        self.assertEqual(response.status_code, 403)

    def test_private_inbox_allows_named_users_and_superadmin(self):
        for username, role in (("mauri", "admin"), ("hernan", "superadmin")):
            with self.subTest(username=username):
                client = self._authenticated_client(username, role)
                rows_result = MagicMock()
                rows_result.mappings.return_value.all.return_value = []
                summary_result = MagicMock()
                summary_result.mappings.return_value.all.return_value = []
                with patch.object(backend, "_ensure_public_consultations_table"), patch.object(
                    backend.db.session,
                    "execute",
                    side_effect=[rows_result, summary_result],
                ):
                    response = client.get("/api/consultas-publicas")
                self.assertEqual(response.status_code, 200)
                self.assertEqual(
                    response.get_json()["resumen"],
                    {"pendiente": 0, "en_revision": 0, "resuelta": 0, "total": 0},
                )

    def test_public_form_validates_required_fields_before_database(self):
        with patch.object(
            backend, "_public_consultation_rate_allowed", return_value=(True, 0)
        ):
            response = backend.app.test_client().post(
                "/api/consultas-publicas",
                json={"nombre_apellido": "Ana Perez"},
                headers={"Origin": backend.FRONTEND_ORIGINS[0]},
            )
        self.assertEqual(response.status_code, 400)
        self.assertIn("obligatorio", response.get_json()["error"])

    def test_public_form_rejects_an_untrusted_source_url(self):
        payload = {
            "nombre_apellido": "Ana Perez",
            "anexo": "Casa Central",
            "oficina": "Recepcion",
            "consulta": "Necesito informar un bien danado.",
            "pagina_origen": "javascript:alert(1)",
        }
        with patch.object(
            backend, "_public_consultation_rate_allowed", return_value=(True, 0)
        ):
            response = backend.app.test_client().post(
                "/api/consultas-publicas",
                json=payload,
                headers={"Origin": backend.FRONTEND_ORIGINS[0]},
            )
        self.assertEqual(response.status_code, 400)
        self.assertIn("pagina_origen", response.get_json()["error"])

    def test_public_form_creates_a_pending_case(self):
        created_at = datetime(2026, 9, 30, 12, 0, 0)
        insert_result = MagicMock()
        insert_result.mappings.return_value.first.return_value = {
            "id": 42,
            "fecha_creacion": created_at,
        }
        payload = {
            "nombre_apellido": "Ana Perez",
            "anexo": "Casa Central",
            "oficina": "Recepcion",
            "consulta": "Necesito informar un bien danado.",
            "pagina_origen": f"{backend.FRONTEND_ORIGINS[0]}/ver?id=10",
            "contexto": {"kind": "asset", "itemId": "10"},
        }
        with patch.object(
            backend, "_public_consultation_rate_allowed", return_value=(True, 0)
        ), patch.object(
            backend, "_ensure_public_consultations_table"
        ), patch.object(
            backend.db.session, "execute", return_value=insert_result
        ):
            response = backend.app.test_client().post(
                "/api/consultas-publicas",
                json=payload,
                headers={"Origin": backend.FRONTEND_ORIGINS[0]},
            )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.get_json()["id"], 42)


if __name__ == "__main__":
    unittest.main()
