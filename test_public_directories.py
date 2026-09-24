import unittest
from unittest.mock import patch

import app as backend


class FakeMappings:
    def __init__(self, *, first=None, all_rows=None):
        self._first = first
        self._all = all_rows or []

    def first(self):
        return self._first

    def all(self):
        return self._all


class FakeResult:
    def __init__(self, *, first=None, all_rows=None):
        self._mappings = FakeMappings(first=first, all_rows=all_rows)

    def mappings(self):
        return self._mappings


class PublicDirectoryTests(unittest.TestCase):
    def test_unknown_directory_is_not_public(self):
        response = backend.app.test_client().get(
            "/api/publico/directorios/no-autorizado"
        )
        self.assertEqual(response.status_code, 404)

    def test_private_api_still_requires_a_session(self):
        response = backend.app.test_client().get("/api/anexos")
        self.assertEqual(response.status_code, 401)

    def test_custom_directory_uses_closed_scope_and_configured_order(self):
        anexo = {
            "id": 100,
            "nombre": "Casa Central",
            "direccion": "Dalmacio Velez 874",
        }
        rows = [
            {"id": 125, "nombre": "Bloque salon nuevo", "total_bienes": 4},
            {"id": 124, "nombre": "Bloque recepcion", "total_bienes": 2},
            {"id": 1406, "nombre": "Bloque privado", "total_bienes": 3},
        ]

        def execute(statement, params):
            sql = str(statement)
            if "FROM anexos" in sql:
                self.assertEqual(params, {"id_anexo": 100})
                return FakeResult(first=anexo)

            self.assertIn("sd.id IN", sql)
            self.assertIn("COALESCE(m.faltante, FALSE) = FALSE", sql)
            self.assertEqual(params["subdependencia_ids"], [124, 1406, 125])
            return FakeResult(all_rows=rows)

        with patch.object(backend.db.session, "execute", side_effect=execute):
            response = backend.app.test_client().get(
                "/api/publico/directorios/bloques-casa-central"
            )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(
            [item["id"] for item in payload["subdependencias"]],
            [124, 1406, 125],
        )
        self.assertEqual(payload["total_bienes"], 9)
        self.assertEqual(payload["anexo"]["direccion"], "Dalmacio Vélez 874")
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")

    def test_each_configured_directory_has_a_fixed_annex(self):
        expected = {
            "residencia-oficial-2": 1000,
            "observatorio": 1101,
            "recinto": 1200,
            "centro-de-operaciones": 400,
            "bloques-casa-central": 100,
            "vicepresidencia-primera": 100,
            "presidencia": 100,
            "biblioteca-anexo-1": 200,
        }
        self.assertEqual(
            {
                slug: config["id_anexo"]
                for slug, config in backend.PUBLIC_DIRECTORY_CONFIGS.items()
            },
            expected,
        )
        self.assertEqual(
            backend.PUBLIC_DIRECTORY_CONFIGS["bloques-casa-central"][
                "subdependencia_ids"
            ],
            [124, 1406, 125],
        )
        self.assertEqual(
            backend.PUBLIC_DIRECTORY_CONFIGS["vicepresidencia-primera"][
                "subdependencia_ids"
            ],
            [1404, 105],
        )
        self.assertEqual(
            backend.PUBLIC_DIRECTORY_CONFIGS["presidencia"][
                "subdependencia_ids"
            ],
            [119, 117],
        )
        self.assertEqual(
            backend.PUBLIC_DIRECTORY_CONFIGS["biblioteca-anexo-1"][
                "subdependencia_ids"
            ],
            [222, 1400, 1401],
        )


if __name__ == "__main__":
    unittest.main()
