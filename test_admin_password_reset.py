import time
import unittest
from unittest.mock import MagicMock, patch

from werkzeug.security import check_password_hash

import app as backend


class AdminPasswordResetTests(unittest.TestCase):
    @staticmethod
    def _client(username, role):
        client = backend.app.test_client()
        with client.session_transaction() as current_session:
            current_session["username"] = username
            current_session["role"] = role
            current_session["password_change_required"] = False
            current_session["user_checked_at"] = time.time()
            current_session["last_activity"] = time.time()
            current_session["csrf_token"] = "test-csrf-token"
        return client

    def test_facu_and_hernan_can_assign_a_temporary_password(self):
        mauri = {
            "id": 25,
            "username": "mauri",
            "nombre": "Mauricio",
            "apellido": "Usuario",
            "role": "admin",
            "activo": True,
            "password_change_required": False,
            "fecha_creacion": None,
        }

        for superadmin in ("Facu", "Hernan"):
            with self.subTest(superadmin=superadmin):
                execute = MagicMock()
                with patch.object(
                    backend, "_ensure_admin_usuarios_columns"
                ), patch.object(
                    backend, "_admin_usuario_por_id", return_value=mauri
                ), patch.object(
                    backend, "_admin_usuario_actual_id", return_value=1
                ), patch.object(
                    backend.db.session, "execute", execute
                ):
                    response = self._client(superadmin, "superadmin").patch(
                        "/api/admin/usuarios/25/password",
                        json={"password": "Provisoria9!"},
                        headers={"X-CSRF-Token": "test-csrf-token"},
                    )

                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.get_json()["username"], "mauri")
                self.assertTrue(response.get_json()["password_change_required"])

                sql = " ".join(str(execute.call_args.args[0]).split()).lower()
                params = execute.call_args.args[1]
                self.assertIn("set password = :password", sql)
                self.assertIn("password_change_required = true", sql)
                self.assertNotIn("role =", sql)
                self.assertNotIn("activo =", sql)
                self.assertEqual(params["id"], 25)
                self.assertTrue(
                    check_password_hash(params["password"], "Provisoria9!")
                )

    def test_regular_admin_cannot_reset_another_users_password(self):
        response = self._client("mauri", "admin").patch(
            "/api/admin/usuarios/25/password",
            json={"password": "Provisoria9!"},
            headers={"X-CSRF-Token": "test-csrf-token"},
        )
        self.assertEqual(response.status_code, 403)

    def test_temporary_password_keeps_the_security_policy(self):
        with patch.object(backend, "_ensure_admin_usuarios_columns"):
            response = self._client("Facu", "superadmin").patch(
                "/api/admin/usuarios/25/password",
                json={"password": "1234"},
                headers={"X-CSRF-Token": "test-csrf-token"},
            )
        self.assertEqual(response.status_code, 400)
        self.assertIn("8 caracteres", response.get_json()["error"])


if __name__ == "__main__":
    unittest.main()
