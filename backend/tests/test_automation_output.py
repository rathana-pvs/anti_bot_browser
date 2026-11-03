"""Runner output must survive large final JSON messages without losing success."""
import asyncio
import json
import sys

from backend.services.automation_service import _iter_process_lines


def test_large_result_from_real_process_exceeds_default_stream_limit():
    async def run():
        script = "import json; print('stage log'); print(json.dumps({'status':'published','first_comment':'submitted_verified','telemetry':{'detail':'x'*250000}}))"
        proc = await asyncio.create_subprocess_exec(
            sys.executable, '-c', script, stdout=asyncio.subprocess.PIPE,
        )
        lines = [line async for line in _iter_process_lines(proc.stdout)]
        assert await proc.wait() == 0
        assert lines[0] == b'stage log\n'
        result = json.loads(lines[1])
        assert result['status'] == 'published'
        assert result['first_comment'] == 'submitted_verified'
        assert len(result['telemetry']['detail']) == 250000
        assert len(lines) == 2
    asyncio.run(run())


def test_chunk_boundaries_and_unterminated_utf8_line():
    async def run():
        data = ('first\n' + 'ឃ'*30000 + '\nlast').encode()
        stream = asyncio.StreamReader()
        stream.feed_data(data)
        stream.feed_eof()
        lines = [line async for line in _iter_process_lines(stream)]
        assert b''.join(lines) == data
        assert [line.decode() for line in lines] == ['first\n', 'ឃ'*30000+'\n', 'last']
    asyncio.run(run())
