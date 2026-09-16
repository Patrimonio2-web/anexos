import time
import unittest
from unittest.mock import patch

from werkzeug.security import generate_password_hash

import app as backend


class FakeCursor:
    def __init__(self, users):
        self.users = users
        self.result = None

    def execute(self, query, params):
        sql = " ".join(query.split()).lower()
        if sql.startswith("select id, username, password"):
            self.result = next(
                (dict(user) for user in self.users if user["username"].lower() == params[0].lower()),
                None,
            )
        elif sql.startswith("update usuarios set username"):
            target, user_id, old_name = params
            user = next(
                (user for user in self.users if user["id"] == user_id and user["username"].lower() == old_name.lower()),
                None,
            )
            if user and not any(other["username"].lower() == target.lower() for other in self.users):
                user["username"] = target
                self.result = (target,)
            else:
                self.result = None
        else:
            raise AssertionError(f"Unexpected SQL: {sql}")

    def fetchone(self):
        return self.result

    def close(self):
        pass


class FakeConnection:
    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        pass


class MatafuegosUserRenameTests(unittest.TestCase):
    def setUp(self):
        self.password = "test-password"
        self.users = [
            {
                "id": index,
                "username": f"CONTIGENCIA{index}",
                "password": generate_password_hash(self.password),
                "role": "usuario",
                "activo": True,
            }
            for index in (1, 2)
        ]
        self.patches = [
            patch.object(backend, "get_conn_dict", side_effect=lambda: (FakeConnection(), FakeCursor(self.users))),
            patch.object(backend, "_login_rate_allowed", return_value=(True, 0)),
            patch.object(backend, "_record_login_failure"),
            patch.object(backend, "_clear_login_failures"),
        ]
        for item in self.patches:
            item.start()
        self.addCleanup(lambda: [item.stop() for item in reversed(self.patches)])

    def test_both_accounts_keep_password_and_matafuegos_role(self):
        client = backend.app.test_client()
        for index in (1, 2):
            username = f"CONTINGENCIA{index}"
            original_hash = self.users[index - 1]["password"]
            response = client.post("/api/login", json={"username": username, "password": self.password})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.get_json(), {"username": username, "role": "matafuegos"})
            self.assertEqual(self.users[index - 1]["username"], username)
            self.assertEqual(self.users[index - 1]["password"], original_hash)
            self.assertEqual(self.users[index - 1]["id"], index)
            repeated = client.post("/api/login", json={"username": username, "password": self.password})
            self.assertEqual(repeated.status_code, 200)
            self.assertEqual(repeated.get_json(), {"username": username, "role": "matafuegos"})
            self.assertEqual(
                client.post("/api/login", json={"username": f"CONTIGENCIA{index}", "password": self.password}).status_code,
                401,
            )

    def test_wrong_password_does_not_rename(self):
        client = backend.app.test_client()
        response = client.post("/api/login", json={"username": "CONTINGENCIA1", "password": "wrong"})
        self.assertEqual(response.status_code, 401)
        self.assertEqual(self.users[0]["username"], "CONTIGENCIA1")

    def test_old_session_is_invalidated_even_when_recently_checked(self):
        client = backend.app.test_client()
        with client.session_transaction() as session:
            session["username"] = "CONTIGENCIA1"
            session["role"] = "matafuegos"
            session["user_checked_at"] = time.time()
        response = client.get("/api/me")
        self.assertEqual(response.status_code, 401)

    def test_role_still_only_reads_and_edits_matafuegos(self):
        self.assertEqual(backend._normalize_main_role("CONTINGENCIA1", "superadmin"), "matafuegos")
        for method, path, allowed in (
            ("GET", "/api/matafuegos", True),
            ("PUT", "/api/matafuegos/8", True),
            ("POST", "/api/matafuegos", False),
            ("GET", "/api/mobiliario", False),
        ):
            with backend.app.test_request_context(path, method=method):
                self.assertEqual(backend._restricted_role_request_allowed("matafuegos"), allowed)


if __name__ == "__main__":
    unittest.main()
