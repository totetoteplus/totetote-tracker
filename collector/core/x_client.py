"""twitterapi.io クライアント（X公式APIではなく第三者の仲介API）。

認証は `x-api-key` ヘッダー1本（OAuth不要）。
エンドポイント仕様: https://docs.twitterapi.io の "Get User Last Tweets" を参照。

レスポンスの正確なフィールド構成はAPI提供元の仕様変更で変わりうるため、
未知のフィールドは無視し、想定フィールドが欠けている場合でも
呼び出し側(collectors/x_monitor.py)がNoneや空文字として安全に扱えるようにする。
"""

from __future__ import annotations

import os

import requests

from core.errors import FetchTimeoutError, HttpError

API_BASE = "https://api.twitterapi.io"
LAST_TWEETS_PATH = "/twitter/user/last_tweets"
TWEETS_BY_ID_PATH = "/twitter/tweets"
REQUEST_TIMEOUT_SECONDS = 20
TWEETS_BY_ID_BATCH_SIZE = 50  # twitterapi.io の上限(1リクエストあたり最大50件)


def _api_key() -> str:
    key = os.environ.get("TWITTERAPI_IO_KEY")
    if not key:
        raise HttpError(
            0, "TWITTERAPI_IO_KEY が未設定です（collector/.env に設定してください）"
        )
    return key


def get_last_tweets(user_name: str, cursor: str = "") -> dict:
    """指定アカウントの最新ツイート（1リクエストにつき最大20件程度）を取得する。"""

    try:
        response = requests.get(
            f"{API_BASE}{LAST_TWEETS_PATH}",
            params={"userName": user_name, "cursor": cursor},
            headers={"x-api-key": _api_key()},
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
    except requests.exceptions.Timeout as exc:
        raise FetchTimeoutError(f"twitterapi.io timed out for @{user_name}") from exc
    except requests.exceptions.RequestException as exc:
        raise HttpError(
            0, f"twitterapi.io request failed for @{user_name}: {exc}"
        ) from exc

    if response.status_code != 200:
        raise HttpError(
            response.status_code,
            f"twitterapi.io returned {response.status_code} for @{user_name}: "
            f"{response.text[:200]}",
        )

    data = response.json()
    if data.get("status") not in (None, "success"):
        raise HttpError(
            0, f"twitterapi.io error for @{user_name}: {data.get('message')}"
        )

    return data


def get_tweets_by_ids(tweet_ids: list[str]) -> list[dict]:
    """指定IDのツイートをまとめて取得する(タイムラインへの新着有無に関わらず個別取得可能)。

    「最新ツイート取得」は直近分しか返らないため、古いツイートの本文/リンクを
    再取得したい場合(バックフィル等)はこちらを使う。1回のリクエストで
    最大 TWEETS_BY_ID_BATCH_SIZE 件まで、それを超える場合は分割して呼び出す。
    """
    all_tweets: list[dict] = []
    for i in range(0, len(tweet_ids), TWEETS_BY_ID_BATCH_SIZE):
        batch = tweet_ids[i : i + TWEETS_BY_ID_BATCH_SIZE]
        try:
            response = requests.get(
                f"{API_BASE}{TWEETS_BY_ID_PATH}",
                params={"tweet_ids": ",".join(batch)},
                headers={"x-api-key": _api_key()},
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
        except requests.exceptions.Timeout as exc:
            raise FetchTimeoutError("twitterapi.io (tweets by id) timed out") from exc
        except requests.exceptions.RequestException as exc:
            raise HttpError(
                0, f"twitterapi.io (tweets by id) request failed: {exc}"
            ) from exc

        if response.status_code != 200:
            raise HttpError(
                response.status_code,
                f"twitterapi.io (tweets by id) returned {response.status_code}: "
                f"{response.text[:200]}",
            )

        data = response.json()
        all_tweets.extend(data.get("tweets", []))

    return all_tweets
