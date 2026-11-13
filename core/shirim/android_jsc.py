"""yt-dlp JS challenge solver for Android: runs the EJS solver scripts in the app's WebView (V8).

Android has no Deno/Node binary, but every device ships a fast JavaScript engine inside WebView.
The Android app passes a bridge object whose evaluate(script, timeout_ms) runs the script in a hidden
WebView and returns whatever the script printed with console.log (or None on timeout).
"""

from yt_dlp.extractor.youtube.jsc._builtin.ejs import EJSBaseJCP
from yt_dlp.extractor.youtube.jsc.provider import (
    JsChallengeProviderError,
    register_preference,
    register_provider,
)
from yt_dlp.extractor.youtube.pot._provider import BuiltinIEContentProvider

_SOLVE_TIMEOUT_MS = 120_000
_bridge = None


def set_bridge(bridge):
    global _bridge
    _bridge = bridge


@register_provider
class WebViewJCP(EJSBaseJCP, BuiltinIEContentProvider):
    PROVIDER_NAME = 'webview'
    JS_RUNTIME_NAME = 'webview'

    def is_available(self, /) -> bool:
        return _bridge is not None and self._available

    def _run_js_runtime(self, stdin: str, /) -> str:
        try:
            output = _bridge.evaluate(stdin, _SOLVE_TIMEOUT_MS)
        except Exception as e:
            raise JsChallengeProviderError(f'WebView challenge solver failed: {e}') from e
        if output is None:
            raise JsChallengeProviderError('WebView challenge solver timed out')
        return str(output)


@register_preference(WebViewJCP)
def _preference(provider, requests):
    return 1000
