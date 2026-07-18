import unittest

from app.main import app


class RouteBoundaryTests(unittest.TestCase):
    def test_management_routes_are_not_exposed_on_cabinet_apis(self) -> None:
        routes = {
            (method, route.path)
            for route in app.routes
            for method in (getattr(route, "methods", None) or ())
        }

        cabinet_prefixes = ("/api/connector", "/api/tjarepo")
        management_suffixes = (
            "/cabinets",
            "/library/manage",
            "/library/upload",
            "/library/songs",
        )
        for _, path in routes:
            for prefix in cabinet_prefixes:
                if path.startswith(prefix):
                    suffix = path.removeprefix(prefix)
                    self.assertFalse(suffix.startswith(management_suffixes), path)

        expected_ui_routes = {
            ("GET", "/api/ui/cabinets"),
            ("GET", "/api/ui/library/manage"),
            ("POST", "/api/ui/library/upload/osz"),
            ("PUT", "/api/ui/cabinets/{cabinet_id}/selection"),
            ("PUT", "/api/ui/cabinets/{cabinet_id}/config"),
        }
        self.assertLessEqual(expected_ui_routes, routes)


if __name__ == "__main__":
    unittest.main()
