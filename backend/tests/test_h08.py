"""H08 拒收链路：被拒只展示原因、不伪装成功、写库 handler 绝不触达。

拒绝路径不连接数据库——被拒时任何 INSERT 都不允许发生，
因此本测试用 monkeypatch 替换 current_user，直接驱动 require_writer。
"""

import asyncio

import pytest
from quart import jsonify

import api
from api import app, require_writer


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture
def calls():
    return []


@pytest.fixture
def handler(calls):
    @require_writer
    async def create_log(user):
        # 真正写库的 handler；被拒时它一次都不允许执行
        calls.append(user["username"])
        return jsonify({"inserted": True}), 201

    return create_log


def _as_user(monkeypatch, user):
    async def fake_current_user():
        return user

    monkeypatch.setattr(api, "current_user", fake_current_user)


def _invoke(handler):
    async def main():
        async with app.test_request_context("/api/logs", method="POST"):
            response, status = await handler()
            return await response.get_json(), status

    return _run(main())


def test_reader_rejected_shows_reason_only(monkeypatch, handler, calls):
    _as_user(monkeypatch, {"username": "observer", "role": "reader"})

    body, status = _invoke(handler)

    assert status == 403
    assert body == {"detail": "仅现场技师可提交偏航记录"}
    # 不得伪装成成功：无 ok/message/fake/id，无“已入队”话术
    assert "已入队" not in str(body)
    for key in ("ok", "message", "fake", "id"):
        assert key not in body
    # 被拒不得触达写库 handler：库不加行、不插空行
    assert calls == []


def test_writer_reaches_insert_handler(monkeypatch, handler, calls):
    _as_user(monkeypatch, {"username": "technician", "role": "writer"})

    body, status = _invoke(handler)

    assert status == 201
    assert body == {"inserted": True}
    assert calls == ["technician"]


def test_anonymous_gets_401(monkeypatch, handler, calls):
    _as_user(monkeypatch, None)

    body, status = _invoke(handler)

    assert status == 401
    assert calls == []
