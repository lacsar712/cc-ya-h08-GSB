import asyncio
import json

import pytest

import api


class FakeResult:
    def __init__(self, row=None, rows=None):
        self._row = row
        self._rows = rows if rows is not None else []

    def fetchone(self):
        return self._row

    def fetchall(self):
        return self._rows


class FakeConn:
    """记录 INSERT 的假连接：不触碰真实数据库。"""

    inserts = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        head = sql.strip().upper()
        if head.startswith("INSERT"):
            type(self).inserts.append({"sql": sql, "params": params})
            return FakeResult(
                row={
                    "id": 1,
                    "turbine_code": params[0],
                    "yaw_err_deg": params[1],
                    "status": "pending",
                    "verdict": None,
                    "reason": None,
                    "created_by": params[2],
                    "created_at": params[3],
                    "processed_at": None,
                }
            )
        if "COUNT(*)" in sql.upper():
            return FakeResult(row={"n": 2})  # 库非空，跳过种子
        return FakeResult(rows=[])

    def commit(self):
        pass


@pytest.fixture(autouse=True)
def fake_db(monkeypatch):
    FakeConn.inserts = []
    monkeypatch.setattr(api, "connect", lambda: FakeConn())
    return FakeConn


async def _login(client, username, password):
    res = await client.post(
        "/api/auth/login",
        json={"username": username, "password": password},
    )
    assert res.status_code == 200
    body = json.loads(await res.get_data(as_text=True))
    return body["access_token"]


def _post_log(client, token=None, payload=None):
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return client.post(
        "/api/logs",
        json=payload
        if payload is not None
        else {"turbine_code": "W12", "yaw_err_deg": 0.4},
        headers=headers,
    )


def _run(coro):
    return asyncio.run(coro)


def test_reader_submit_is_rejected_403_and_writes_nothing(fake_db):
    async def scenario():
        async with api.app.test_client() as client:
            token = await _login(client, "observer", "obs123456")
            res = await _post_log(client, token)
            return res.status_code, await res.get_json()

    status, body = _run(scenario())

    # 拒收必须是真实的 403，而不是伪装成 201 成功
    assert status == 403
    assert body.get("ok") is not True
    assert body.get("fake") is not True
    assert "已入队" not in json.dumps(body, ensure_ascii=False)
    assert "仅现场技师" in body["detail"]

    # 库不得加行、不得插空行
    assert fake_db.inserts == []


def test_writer_submit_is_accepted_201_and_inserts_pending_row(fake_db):
    async def scenario():
        async with api.app.test_client() as client:
            token = await _login(client, "technician", "tech123456")
            res = await _post_log(client, token)
            return res.status_code, await res.get_json()

    status, body = _run(scenario())

    assert status == 201
    assert body["status"] == "pending"
    assert body["id"] is not None

    # 只有技师实写入库，才允许产生新行
    assert len(fake_db.inserts) == 1
    params = fake_db.inserts[0]["params"]
    assert params[0] == "W12"
    assert params[1] == 0.4
    assert params[2] == "technician"


def test_anonymous_submit_is_401_and_writes_nothing(fake_db):
    async def scenario():
        async with api.app.test_client() as client:
            res = await _post_log(client, token=None)
            return res.status_code

    status = _run(scenario())
    assert status == 401
    assert fake_db.inserts == []


def test_invalid_writer_payload_is_400_and_writes_nothing(fake_db):
    async def scenario():
        async with api.app.test_client() as client:
            token = await _login(client, "technician", "tech123456")
            res = await _post_log(
                client, token, {"turbine_code": "", "yaw_err_deg": 0.4}
            )
            return res.status_code

    status = _run(scenario())
    assert status == 400
    assert fake_db.inserts == []
