"""Collector 共通の例外階層。

BaseCollector.run() はこれらを個別に捕捉して collector_runs へ記録し、
1サイトの失敗が他サイトの収集を止めないようにする（Phase 6 で本格運用）。
"""


class CollectorError(Exception):
    """すべての Collector 関連エラーの基底クラス。"""


class FetchTimeoutError(CollectorError):
    """リクエスト/ブラウザ操作がタイムアウトした。"""


class HttpError(CollectorError):
    """HTTPステータスエラー（4xx/5xx）。"""

    def __init__(self, status_code: int, message: str = ""):
        self.status_code = status_code
        super().__init__(message or f"HTTP {status_code}")


class DnsError(CollectorError):
    """名前解決に失敗した。"""


class PageStructureChangedError(CollectorError):
    """想定していたHTML構造・APIレスポンス形式が変わり parse() が失敗した。"""


class CaptchaDetectedError(CollectorError):
    """CAPTCHA等のボット対策を検知した。リトライせず即座に中断する。"""


class BrowserAutomationError(CollectorError):
    """Playwright操作中の想定外エラー。"""


class DatabaseError(CollectorError):
    """Supabaseへの接続・書き込みエラー。"""
