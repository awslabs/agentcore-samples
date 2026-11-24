"""Bounded probe run INSIDE an offline container; never publish host ports.

The application runs with its normal image CMD. Mount this file read-only and
run it with docker exec. The container must use --network none; the probe also
rejects any connection except loopback. No AWS or model call is involved.
"""
import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import platform
import socket
import time

from fastmcp import Client
from fastmcp.exceptions import ToolError

_connect = socket.socket.connect
_connect_ex = socket.socket.connect_ex


def loopback_only(original):
    def checked(sock, address):
        if not isinstance(address, tuple) or address[0] not in ('127.0.0.1', '::1'):
            raise AssertionError(f'Non-loopback connection forbidden: {address}')
        return original(sock, address)
    return checked


socket.socket.connect = loopback_only(_connect)
socket.socket.connect_ex = loopback_only(_connect_ex)


async def probe(kind, expected_sha):
    source_sha = hashlib.sha256(Path('/app/server.py').read_bytes()).hexdigest()
    assert source_sha == expected_sha
    assert os.getuid() == 10001
    assert platform.machine() == 'x86_64'
    endpoint = 'http://127.0.0.1:8000/mcp' if kind == 'echo' else 'http://127.0.0.1:8001/stock-mcp/'
    async with Client(endpoint, mode='legacy', timeout=10, init_timeout=15) as client:
        initialized = client.initialize_result
        assert initialized is not None
        tools = {tool.name for tool in await client.list_tools()}
        if kind == 'echo':
            assert tools == {'echo', 'add', 'get_time'}
            assert (await client.call_tool('echo', {'message': 'offline-image'})).data == 'offline-image'
            assert (await client.call_tool('add', {'a': 2, 'b': 3})).data == 5
        else:
            assert tools == {'get_stock_price', 'get_market_summary'}
            assert (await client.call_tool('get_stock_price', {'symbol': 'AAPL'})).data['symbol'] == 'AAPL'
            assert 'indices' in (await client.call_tool('get_market_summary', {})).data
            assert 'error' in (await client.call_tool('get_stock_price', {'symbol': 'NOT_A_STOCK'})).data
        try:
            await client.call_tool('__offline_missing_tool__', {})
        except ToolError:
            error_verified = True
        else:
            raise AssertionError('Unknown tool unexpectedly succeeded')
        return {'server': kind, 'uid': os.getuid(), 'machine': platform.machine(), 'python': platform.python_version(),
                'source_sha256': source_sha, 'protocol_version': initialized.protocol_version,
                'tools': sorted(tools), 'valid_calls': True, 'unknown_tool_error': error_verified,
                'transport': 'actual streamable HTTP on container loopback'}


async def main(args):
    # Retry readiness only, never repeat a failed functional assertion/tool call.
    deadline = time.monotonic() + 75
    port = 8000 if args.server == 'echo' else 8001
    while True:
        try:
            with socket.create_connection(('127.0.0.1', port), timeout=2):
                break
        except OSError:
            if time.monotonic() >= deadline:
                raise
            await asyncio.sleep(1)
    result = await asyncio.wait_for(probe(args.server, args.source_sha256), timeout=30)
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--server', required=True, choices=['echo', 'stock'])
    parser.add_argument('--source-sha256', required=True)
    asyncio.run(main(parser.parse_args()))
