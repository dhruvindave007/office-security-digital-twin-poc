"""End-to-end check of the operator dashboard against a RUNNING system (start it with scripts/demo.py).

Drives a real browser (Playwright with the installed Microsoft Edge; dev-only:
`uv pip install --python backend\\.venv\\Scripts\\python.exe playwright`) and checks, against the backend
API, what the operator sees. Every line printed is something that was observed, not assumed.

    backend\\.venv\\Scripts\\python.exe scripts\\e2e_dashboard.py [--base http://127.0.0.1:8000] [--ai]

--ai also runs one on-demand AI investigation (~$0.01, within the server's daily budget).
"""
import argparse
import json
import sys
import time
import urllib.request

from playwright.sync_api import sync_playwright

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok)))
    print(f"[{' ok ' if ok else 'FAIL'}] {name}{(' - ' + detail) if detail else ''}", flush=True)
    return ok


def api(base, path, method="GET", body=None):
    req = urllib.request.Request(base + path, method=method, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(req, timeout=120))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8000")
    ap.add_argument("--ai", action="store_true")
    ap.add_argument("--shots", default="")
    a = ap.parse_args()
    base = a.base

    check("API healthy", api(base, "/api/health")["status"] == "ok")
    live = api(base, "/api/live/status")
    n_live = sum(1 for c in live["cameras"] if c["source"]["state"] == "live")
    check("replayed camera streams live (API)", n_live == 6, f"{n_live}/6")
    site_cams = [c["camera_id"] for c in api(base, "/api/site")["cameras"]]
    # An interrupted earlier run can leave its simulated incident active; the rules would then (correctly)
    # add the new sighting to it instead of raising a new one. Close leftovers so this run starts clean.
    for i in api(base, "/api/twin/incidents?limit=500")["incidents"]:
        if i["track_id"] == "sim:lab_visitor" and i["status"] in ("OPEN", "INVESTIGATING"):
            api(base, f"/api/twin/incidents/{i['incident_id']}", "PATCH", {"status": "RESOLVED"})
            print(f"[info] resolved leftover simulated incident {i['incident_id'][:8]} from an earlier run")
    time.sleep(11)   # let the simulated track expire from the twin (site TTL 10 s) so it can enter again

    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge", headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        errors, navigations = [], []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("framenavigated", lambda f: f == page.main_frame and navigations.append(f.url))
        page.goto(base + "/")
        page.wait_for_selector('[data-testid="conn"]', timeout=30000)
        page.wait_for_function("document.querySelector('[data-testid=conn]').innerText.includes('LIVE')", timeout=15000)
        check("dashboard push channel LIVE", True)

        # cameras: every configured camera tile shows a decoded picture and LIVE
        page.wait_for_timeout(4000)
        tiles_ok = []
        for cam in site_cams:
            tile = page.locator(f'[data-testid="camera-{cam}"]')
            tiles_ok.append(tile.get_attribute("data-picture") == "fresh" and "LIVE" in tile.inner_text())
        bad = [c for c, ok in zip(site_cams, tiles_ok) if not ok]
        check("camera wall: every camera shows a live picture", all(tiles_ok), f"{sum(tiles_ok)}/{len(site_cams)} tiles" + (f", not fresh: {bad}" if bad else ""))

        zones = api(base, "/api/twin/zones")
        drawn = [z["zone_id"] for z in zones if page.locator(f'[data-testid="zone-{z["zone_id"]}"]').count()]
        check("floor plan draws the site's real rooms", len(drawn) == len([z for z in zones if len(z["vertices"]) >= 3]), f"{len(drawn)} rooms")
        state = api(base, "/api/twin/state")
        lab_ui = page.text_content('[data-testid="count-zone_corridor"]')
        check("room count on screen matches the twin (corridor)", abs(int(lab_ui) - state["zone_occupancy"].get("zone_corridor", 0)) <= 2,
              f"screen {lab_ui}, API {state['zone_occupancy'].get('zone_corridor', 0)} (counts move every second)")
        if a.shots:
            page.screenshot(path=f"{a.shots}/overview.png")

        # simulate from the UI; the new incident must arrive by push, without a reload
        nav_before = len(navigations)
        page.select_option('select[aria-label="Scenario to simulate"]', "lab_unauthorized_visitor")
        page.get_by_role("button", name="Run").click()
        page.wait_for_selector('header [role="status"]', timeout=15000)
        check("scenario ran from the UI", "incident(s) raised" in page.inner_text('header [role="status"]'),
              page.inner_text('header [role="status"]'))
        sim = [i for i in api(base, "/api/twin/incidents?limit=50")["incidents"] if i["track_id"] == "sim:lab_visitor" and i["status"] == "OPEN"]
        if not check("backend raised the simulated incident", sim):
            return 1
        inc = sim[0]
        row = page.locator(f'[data-testid="incident-{inc["incident_id"]}"]')
        row.wait_for(timeout=10000)
        check("incident appeared in the queue without a page reload", len(navigations) == nav_before, f"navigations {len(navigations) - nav_before}")

        # detail + evidence (the clip is written ~8 s after the incident)
        row.click()
        page.wait_for_selector('[data-testid="incident-detail"]')
        clip = page.locator('[data-testid="evidence-clip"] video')
        try:
            clip.wait_for(timeout=45000)
            page.wait_for_function("v => v.readyState >= 1 && v.duration > 0", arg=clip.element_handle(), timeout=20000)
            dur = clip.evaluate("v => v.duration")
            check("evidence clip loads and plays in the browser", dur > 0, f"{dur:.1f} s clip, src {clip.get_attribute('src')}")
            check("simulated-incident warning shown next to the clip", page.locator('[data-testid="simulated-note"]').count() == 1)
        except Exception as e:
            check("evidence clip loads and plays in the browser", False, str(e)[:120])
        ev_api = api(base, f"/api/twin/incidents/{inc['incident_id']}/evidence")
        check("clip shown is the backend's clip", ev_api["clips"] and clip.get_attribute("src") == ev_api["clips"][0]["video_url"])
        if a.shots:
            page.screenshot(path=f"{a.shots}/incident_evidence.png")

        # AI review tab
        page.get_by_role("tab", name="AI review").click()
        page.wait_for_timeout(1500)
        if a.ai:
            page.get_by_role("button", name="Investigate").click()
            page.wait_for_selector('[data-testid="verification-badge"]', timeout=120000)
        inv = api(base, f"/api/twin/incidents/{inc['incident_id']}/investigations")["investigations"]
        if inv:
            latest = sorted(inv, key=lambda r: r["created_at"])[-1]
            badge = page.locator('[data-testid="verification-badge"]').first.inner_text()
            passed = latest["verification"].get("passed")
            ok = (badge == "VERIFIED") == (passed is True and latest["status"] == "COMPLETED")
            check("AI verdict on screen matches the backend's verification", ok, f"screen {badge}, backend passed={passed}, status {latest['status']}")
        else:
            note = page.locator('[data-testid="auto-note"]')
            check("no AI investigation for this incident: screen says so", page.locator('[data-testid="ai-empty"]').count() == 1,
                  note.inner_text() if note.count() else "")
        if a.shots:
            page.screenshot(path=f"{a.shots}/incident_ai.png")

        # lifecycle through the UI, confirmed by the backend
        page.get_by_test_id("action-INVESTIGATING").click()
        page.wait_for_selector('[data-testid="action-RESOLVED"]')
        page.wait_for_function("() => document.querySelector('[data-testid=incident-detail]').innerText.toLowerCase().includes('investigating')", timeout=10000)
        check("Start investigating -> backend INVESTIGATING", api(base, f"/api/twin/incidents/{inc['incident_id']}")["status"] == "INVESTIGATING")
        page.get_by_test_id("action-RESOLVED").click()
        page.wait_for_selector('[data-testid="action-OPEN"]', timeout=10000)
        check("Resolve -> backend RESOLVED", api(base, f"/api/twin/incidents/{inc['incident_id']}")["status"] == "RESOLVED")
        page.keyboard.press("Escape")
        check("Esc returns to the queue", page.locator('[aria-label="Incident queue"]').count() == 1)
        check("no JavaScript errors", not errors, "; ".join(errors[:3]))
        browser.close()

    failed = [n for n, ok in RESULTS if not ok]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed" + (f"; FAILED: {failed}" if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
