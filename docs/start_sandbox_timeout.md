# start sandbox timeout

## Source
- Log file: `AlphaDiana/docs/bqjg4idpm.output`
- Extracted lines: 570-631

## Timeout/Error snippet
```text
2026-03-26 21:07:18,299 [ERROR] [openclaw-aime24-glm5-001] root: Failed to post to http://127.0.0.1:9016/apis/envs/sandbox/v1/start_async: All connection attempts failed
Traceback (most recent call last):
  File "/home/user/AlphaDiana/.venv/lib/python3.11/site-packages/httpx/_transports/default.py", line 101, in map_httpcore_exceptions
    yield
  File "/home/user/AlphaDiana/.venv/lib/python3.11/site-packages/httpx/_transports/default.py", line 394, in handle_async_request
    resp = await self._pool.handle_async_request(req)
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/home/user/AlphaDiana/.venv/lib/python3.11/site-packages/httpcore/_async/connection_pool.py", line 256, in handle_async_request
    raise exc from None
  File "/home/user/AlphaDiana/.venv/lib/python3.11/site-packages/httpcore/_async/connection_pool.py", line 236, in handle_async_request
    response = await connection.handle_async_request(
               ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/home/user/AlphaDiana/.venv/lib/python3.11/site-packages/httpcore/_async/connection.py", line 101, in handle_async_request
    raise exc
  File "/home/user/AlphaDiana/.venv/lib/python3.11/site-packages/httpcore/_async/connection.py", line 78, in handle_async_request
    stream = await self._connect(request)
             ^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/home/user/AlphaDiana/.venv/lib/python3.11/site-packages/httpcore/_async/connection.py", line 124, in _connect
    stream = await self._network_backend.connect_tcp(**kwargs)
             ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/home/user/AlphaDiana/.venv/lib/python3.11/site-packages/httpcore/_backends/auto.py", line 31, in connect_tcp
    return await self._backend.connect_tcp(
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/home/user/AlphaDiana/.venv/lib/python3.11/site-packages/httpcore/_backends/anyio.py", line 113, in connect_tcp
    with map_exceptions(exc_map):
  File "/home/user/.local/share/uv/python/cpython-3.11.14-linux-x86_64-gnu/lib/python3.11/contextlib.py", line 158, in __exit__
    self.gen.throw(typ, value, traceback)
  File "/home/user/AlphaDiana/.venv/lib/python3.11/site-packages/httpcore/_exceptions.py", line 14, in map_exceptions
    raise to_exc(exc) from exc
httpcore.ConnectError: All connection attempts failed

The above exception was the direct cause of the following exception:

Traceback (most recent call last):
  File "/home/user/AlphaDiana/ref/ROCK/rock/utils/http.py", line 28, in post
    response: Response = await client.post(url, headers=headers, json=data)
                         ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/home/user/AlphaDiana/.venv/lib/python3.11/site-packages/httpx/_client.py", line 1859, in post
    return await self.request(
           ^^^^^^^^^^^^^^^^^^^
  File "/home/user/AlphaDiana/.venv/lib/python3.11/site-packages/httpx/_client.py", line 1540, in request
    return await self.send(request, auth=auth, follow_redirects=follow_redirects)
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/home/user/AlphaDiana/.venv/lib/python3.11/site-packages/httpx/_client.py", line 1629, in send
    response = await self._send_handling_auth(
               ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/home/user/AlphaDiana/.venv/lib/python3.11/site-packages/httpx/_client.py", line 1657, in _send_handling_auth
    response = await self._send_handling_redirects(
               ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/home/user/AlphaDiana/.venv/lib/python3.11/site-packages/httpx/_client.py", line 1694, in _send_handling_redirects
    response = await self._send_single_request(request)
               ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/home/user/AlphaDiana/.venv/lib/python3.11/site-packages/httpx/_client.py", line 1730, in _send_single_request
    response = await transport.handle_async_request(request)
               ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/home/user/AlphaDiana/.venv/lib/python3.11/site-packages/httpx/_transports/default.py", line 393, in handle_async_request
    with map_httpcore_exceptions():
  File "/home/user/.local/share/uv/python/cpython-3.11.14-linux-x86_64-gnu/lib/python3.11/contextlib.py", line 158, in __exit__
    self.gen.throw(typ, value, traceback)
  File "/home/user/AlphaDiana/.venv/lib/python3.11/site-packages/httpx/_transports/default.py", line 118, in map_httpcore_exceptions
    raise mapped_exc(message) from exc
```
