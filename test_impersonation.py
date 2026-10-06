import time
import unittest
from unittest.mock import MagicMock, patch

import app as backend


class ImpersonationTests(unittest.TestCase):
    @staticmethod
    def _client(username="Facu", role="superadmin"):
        client = backend.app.test_client()
        with client.session_transaction() as current:
            current["username"] = username
            current["role"] = role
            current["password_change_required"] = False
            current["user_checked_at"] = time.time()
            current["last_activity"] = time.time()
            current["csrf_token"] = "test-csrf-token"
        return client

    @staticmethod
    def _result(row):
        result = MagicMock()
        result.mappings.return_value.first.return_value = row
        return result

    def test_regular_admin_cannot_start_temporary_view(self):
        response = self._client("mauri", "admin").post(
            "/api/impersonacion/iniciar",
            json={"username": "Comisario"},
            headers={"X-CSRF-Token": "test-csrf-token"},
        )
        self.assertEqual(response.status_code, 403)

    def test_only_allowlisted_accounts_can_be_viewed(self):
        response = self._client().post(
            "/api/impersonacion/iniciar",
            json={"username": "mauri"},
            headers={"X-CSRF-Token": "test-csrf-token"},
        )
        self.assertEqual(response.status_code, 403)

    def test_database_role_alone_does_not_enable_temporary_view(self):
        response = self._client("otro-superadmin", "superadmin").post(
            "/api/impersonacion/iniciar",
            json={"username": "Comisario"},
            headers={"X-CSRF-Token": "test-csrf-token"},
        )
        self.assertEqual(response.status_code, 403)

    def test_start_uses_target_permissions_and_return_restores_superadmin(self):
        client = self._client()
        target = {"username": "Comisario", "role": "comisario", "activo": True}
        with patch.object(backend.db.session, "execute", return_value=self._result(target)):
            started = client.post(
                "/api/impersonacion/iniciar",
                json={"username": "Comisario"},
                headers={"X-CSRF-Token": "test-csrf-token"},
            )
        self.assertEqual(started.status_code, 200)
        self.assertEqual(started.get_json()["role"], "comisario")
        self.assertTrue(started.get_json()["impersonating"])

        forbidden = client.get("/api/impersonacion/usuarios")
        self.assertEqual(forbidden.status_code, 403)

        original = {
            "username": "Facu",
            "role": "superadmin",
            "activo": True,
            "password_change_required": False,
        }
        with patch.object(backend.db.session, "execute", return_value=self._result(original)):
            restored = client.post(
                "/api/impersonacion/finalizar",
                headers={"X-CSRF-Token": "test-csrf-token"},
            )
        self.assertEqual(restored.status_code, 200)
        self.assertEqual(restored.get_json()["username"], "Facu")
        self.assertEqual(restored.get_json()["role"], "superadmin")
        self.assertFalse(restored.get_json()["impersonating"])

    def test_normal_restricted_login_has_no_return_identity(self):
        response = self._client("Comisario", "comisario").get("/api/me")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.get_json()["impersonating"])
        self.assertIsNone(response.get_json()["impersonator_username"])


if __name__ == "__main__":
    unittest.main()
