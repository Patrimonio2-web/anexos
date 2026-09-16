import time
import unittest
from unittest.mock import patch

import app as backend


class ProfilePermissionsTests(unittest.TestCase):
    def test_restricted_users_can_read_but_cannot_edit_profile(self):
        for username, role in (
            ("CONTINGENCIA1", "matafuegos"),
            ("CONTINGENCIA2", "matafuegos"),
            ("Comisario", "comisario"),
        ):
            with self.subTest(username=username):
                client = backend.app.test_client()
                with client.session_transaction() as session:
                    session["username"] = username
                    session["role"] = role
                    session["user_checked_at"] = time.time()
                    session["csrf_token"] = "test-csrf-token"

                profile = {
                    "id": 1,
                    "username": username,
                    "nombre": "Nombre",
                    "apellido": "Apellido",
                }
                with patch.object(backend, "_ensure_perfil_usuario_columns"), patch.object(
                    backend, "_perfil_usuario_actual", return_value=profile
                ) as current_profile:
                    get_response = client.get("/api/perfil")
                    self.assertEqual(get_response.status_code, 200)
                    self.assertEqual(get_response.get_json()["username"], username)
                    self.assertEqual(get_response.get_json()["role"], role)

                    headers = {"X-CSRF-Token": "test-csrf-token"}
                    update_response = client.put(
                        "/api/perfil",
                        json={"username": "otro", "nombre": "Otro"},
                        headers=headers,
                    )
                    password_response = client.put(
                        "/api/perfil/password",
                        json={"password_actual": "a", "password_nueva": "secreto", "password_confirmacion": "secreto"},
                        headers=headers,
                    )
                    self.assertEqual(update_response.status_code, 403)
                    self.assertEqual(password_response.status_code, 403)
                    self.assertEqual(current_profile.call_count, 1)

    def test_other_roles_keep_profile_edit_access(self):
        for role in ("admin", "superadmin", "viewer"):
            with backend.app.test_request_context("/api/perfil", method="PUT"):
                self.assertTrue(backend._restricted_role_request_allowed(role))
            with backend.app.test_request_context("/api/perfil/password", method="PUT"):
                self.assertTrue(backend._restricted_role_request_allowed(role))


if __name__ == "__main__":
    unittest.main()
