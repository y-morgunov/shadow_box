"""Opt-in Chromium regression: SHADOW_BROWSER_TEST=1 python -m unittest test_web_browser.

Requires playwright and `python -m playwright install chromium` for development only.
"""
import os
import tempfile
import threading
import unittest
from pathlib import Path


@unittest.skipUnless(os.environ.get('SHADOW_BROWSER_TEST') == '1', 'Browser integration is opt-in')
class BrowserTest(unittest.TestCase):
    def test_generate_view_download_restore_and_cancel(self):
        from playwright.sync_api import sync_playwright, expect
        from web.server import BoundedServer, Handler, Service
        with tempfile.TemporaryDirectory() as directory:
            server = BoundedServer(('127.0.0.1', 0), Handler)
            origin = f'http://127.0.0.1:{server.server_port}'
            service = Service(directory, 'browser-test-password-only', origin, timeout=120)
            server.service = service
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with sync_playwright() as playwright:
                    browser = playwright.chromium.launch(headless=True)
                    try:
                        page = browser.new_page(viewport={'width':1440,'height':1000})
                        errors = []
                        page.on('pageerror', lambda error: errors.append(str(error)))
                        page.goto(origin)
                        page.locator('#password').fill(service.password)
                        page.get_by_role('button', name='Войти', exact=True).click()
                        expect(page.locator('#workspace')).to_be_visible()
                        # Qt spin increments must not become invalid HTML step bases.
                        self.assertTrue(page.locator('#controls input').evaluate_all(
                            '(inputs)=>inputs.every(input=>input.checkValidity())'))
                        page.locator('#draft').click()
                        page.locator('#box_y').fill('14')
                        page.locator('#box_y').dispatch_event('change')
                        page.locator('#source_diameter').fill('0')
                        page.locator('#generate').click()
                        expect(page.locator('#box_y')).to_be_disabled()
                        expect(page.locator('#downloads')).to_be_visible(timeout=120000)
                        expect(page.locator('#status')).to_have_text('Готово')
                        # Wait for the actual STL fetch/parse, beyond result metadata.
                        page.wait_for_function("performance.getEntriesByType('resource').some(r=>r.name.endsWith('/radial-box.stl'))")
                        with page.expect_download() as event:
                            page.locator('#kit').click()
                        path = Path(directory)/'download.zip'
                        event.value.save_as(path)
                        self.assertGreater(path.stat().st_size, 1000)
                        page.get_by_role('button',name='Идеальная тень',exact=True).click()
                        page.locator('#preview').evaluate('(img)=>img.decode()')
                        self.assertEqual(page.locator('#preview').evaluate('(img)=>img.naturalWidth'),300)
                        page.locator('#part').select_option('wall-1.stl')
                        expect(page.locator('#model')).to_be_visible()
                        href = page.locator('#kit').get_attribute('href')
                        page.locator('#generate').click()
                        page.locator('#cancel').click()
                        expect(page.locator('#status')).to_have_text('Расчёт отменён',timeout=15000)
                        self.assertEqual(page.locator('#kit').get_attribute('href'),href)
                        page.reload()
                        expect(page.locator('#box_y')).to_have_value('14')
                        expect(page.locator('#downloads')).to_be_visible()
                        self.assertEqual(page.locator('#kit').get_attribute('href'),href)
                        page.set_viewport_size({'width':390,'height':844})
                        self.assertFalse(page.evaluate('document.documentElement.scrollWidth > innerWidth'))
                        self.assertEqual(errors,[])
                    finally:
                        browser.close()
            finally:
                for ident in list(service.jobs):
                    service.cancel(ident)
                server.shutdown(); server.server_close(); thread.join()


if __name__ == '__main__':
    unittest.main()
