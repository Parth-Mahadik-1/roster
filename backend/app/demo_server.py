"""Zero-dependency local demo server for the Smart Roster dashboard."""

from __future__ import annotations

import json
from datetime import date
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from app.sample_data import demo_data
from app.scheduler import RosterScheduler, validate_schedule


class DemoHandler(BaseHTTPRequestHandler):
    scheduler = RosterScheduler()

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/":
            content = (Path(__file__).parent / "static" / "index.html").read_bytes()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)
            return
        if self.path == "/api/summary":
            roster_input = demo_data()
            self._json(
                {
                    "employees": len(roster_input.employees),
                    "brands": len({role.brand for role in roster_input.roles}),
                    "processes": len({role.process for role in roster_input.roles}),
                    "pods": len({employee.pod for employee in roster_input.employees}),
                }
            )
            return
        if self.path == "/api/catalog/brands":
            brands = sorted({role.brand for role in demo_data().roles})
            self._json({"brands": [
                {"id": index, "name": name, "status": "Sample"}
                for index, name in enumerate(brands, start=1)
            ]})
            return
        if self.path == "/api/catalog/employees":
            self._json({"employees": [
                {"id": employee.id, "name": employee.name, "pod": employee.pod,
                 "designation": employee.designation, "pod_role": employee.pod_role, "status": "Sample"}
                for employee in demo_data().employees
            ]})
            return
        if self.path == "/api/roster-period":
            self._json({"start": date.today().replace(day=1).isoformat()})
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:  # noqa: N802
        payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if self.path == "/api/generate":
            roster_input = demo_data()
            result = self.scheduler.generate(roster_input, payload["year"], payload["month"])
            result.diagnostics.extend(validate_schedule(result, roster_input))
            self._json({"status": result.status, "assignments": result.rows(), "diagnostics": result.diagnostics})
            return
        if self.path == "/api/absence":
            roster_input = demo_data()
            absence_day = date.fromisoformat(payload["absence_date"])
            baseline = self.scheduler.generate(roster_input, payload["year"], payload["month"])
            affected = [
                row for row in baseline.rows()
                if row["employee_id"] == payload["employee_id"] and row["date"] == absence_day.isoformat()
            ]
            roster_input.unavailable.setdefault(absence_day, set()).add(payload["employee_id"])
            revised = self.scheduler.generate(roster_input, payload["year"], payload["month"])
            alternatives = [
                row for row in revised.rows()
                if row["date"] == absence_day.isoformat()
                and any(row["role_id"] == affected_row["role_id"] for affected_row in affected)
            ]
            self._json(
                {
                    "status": revised.status,
                    "affected_assignments": affected,
                    "alternative_assignments": alternatives,
                    "coverage_gaps": revised.diagnostics,
                    "explanation": "The revised roster is solved across the full month, preserving coverage and no-overlap constraints.",
                }
            )
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def _json(self, payload: dict) -> None:
        content = json.dumps(payload).encode()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)


if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", 8000), DemoHandler).serve_forever()
