import unittest
from unittest.mock import patch

from flask import jsonify

import app as backend


class AltaEstadoCargaTests(unittest.TestCase):
    def test_rejects_invalid_manual_status(self):
        with backend.app.test_request_context(
            "/api/altas/12/estado-carga", method="PATCH", json={"estado": "revisada"}
        ):
            backend.session["username"] = "admin"
            backend.session["role"] = "admin"
            response, status = backend.api_cambiar_estado_carga_alta(12)
            self.assertEqual(status, 400)
            self.assertIn("pendiente", response.get_json()["error"])

    def test_approved_status_updates_only_the_selected_record(self):
        with backend.app.test_request_context(
            "/api/altas/12/estado-carga", method="PATCH", json={"estado": "aprobada"}
        ):
            backend.session["username"] = "admin"
            backend.session["role"] = "admin"
            with patch.object(backend, "_ensure_alta_estado_carga_column"), \
                 patch.object(backend.db.session, "execute") as execute, \
                 patch.object(backend.db.session, "commit"), \
                 patch.object(backend, "api_obtener_alta", return_value=jsonify({"id": 12, "estado_carga": "aprobada"})):
                execute.return_value.rowcount = 1
                response = backend.api_cambiar_estado_carga_alta(12)
                self.assertEqual(response.get_json()["estado_carga"], "aprobada")
                self.assertEqual(execute.call_args.args[1], {"id": 12, "estado": "aprobada"})


if __name__ == "__main__":
    unittest.main()
