import time
import unittest
from unittest.mock import patch

import app as backend


class ProfilePermissionsTests(unittest.TestCase):
    def test_password_policy_requires_every_requested_character_group(self):
        self.assertIsNotNone(backend._password_policy_error("Corta1!"))
        self.assertIsNotNone(backend._password_policy_error("12345678!"))
        self.assertIsNotNone(backend._password_policy_error("Segura!!"))
        self.assertIsNotNone(backend._password_policy_error("Segura123"))
        self.assertIsNone(backend._password_policy_error("Segura123!"))

    def test_pending_password_change_blocks_private_apis(self):
        client = backend.app.test_client()
        with client.session_transaction() as session:
            session["username"] = "usuario-prueba"
            session["role"] = "admin"
            session["password_change_required"] = True
            session["user_checked_at"] = time.time()
            session["csrf_token"] = "test-csrf-token"

        blocked = client.get("/api/anexos")
        self.assertEqual(blocked.status_code, 403)
        self.assertEqual(blocked.get_json()["error"], "password_change_required")
        self.assertEqual(client.get("/api/csrf").status_code, 200)

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

    def test_restricted_roles_can_change_password_only_when_forced(self):
        for role in ("matafuegos", "comisario"):
            with backend.app.test_request_context("/api/perfil/password", method="PUT"):
                backend.session["password_change_required"] = True
                self.assertTrue(backend._restricted_role_request_allowed(role))
                backend.session["password_change_required"] = False
                self.assertFalse(backend._restricted_role_request_allowed(role))


if __name__ == "__main__":
    unittest.main()
