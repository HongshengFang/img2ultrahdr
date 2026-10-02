import json
import subprocess
import sys


def test_malformed_requests_do_not_kill_controller_or_reuse_previous_id():
    requests = '\n'.join([
        '{broken', '[]', 'null',
        '{"command":"unsupported","id":"previous"}',
        '{also-broken', '{"command":"close"}', '',
    ])
    process = subprocess.run([sys.executable, '-m', 'hdrimg.worker'],
                             input=requests, capture_output=True, text=True, timeout=10)
    assert process.returncode == 0, process.stderr
    events = [json.loads(line) for line in process.stdout.splitlines()]
    assert len(events) == 5
    assert all(event['event'] == 'error' for event in events)
    assert events[3]['id'] == 'previous'
    assert all('id' not in events[index] for index in [0, 1, 2, 4])
    assert events[1]['error_code'] == events[2]['error_code'] == 'invalid_input'
