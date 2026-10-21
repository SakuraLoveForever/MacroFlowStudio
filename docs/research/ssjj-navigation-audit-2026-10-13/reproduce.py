"""Verify spatial actions and the unknown-obstacle limit; no game or OS input.

Run from any directory with the project's Python/dependencies. These simulations
demonstrate controller behavior, not a successful or failed live game run.
"""
from pathlib import Path
import json
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT), str(ROOT / '.deps'), str(ROOT / 'src')]

from tests.test_ssjj_continuous import Simulation
from tests.test_ssjj_waypoint_playback import PlaybackTests


def timed_jump():
    results = []
    for speed in (300, 600, 1200):
        action = {
            'type': 'ssjj_recording', 'map_id': 100001, 'duration_ms': 1200,
            'checkpoints': [
                {'at_ms': 0, 'position': [0, 0, 0], 'yaw': 0, 'pitch': 0, 'mode': 'travel'},
                {'at_ms': 1000, 'position': [600, 0, 0], 'yaw': 0, 'pitch': 0, 'mode': 'travel'},
            ],
            'trajectory': [{'at_ms': 0, 'position': [0, 0, 0]},
                           {'at_ms': 1000, 'position': [600, 0, 0]}],
            'inputs': [
                {'type': 'key', 'vk': 32, 'down': True, 'recorded_at_ms': 500},
                {'type': 'key', 'vk': 32, 'down': False, 'recorded_at_ms': 550},
            ],
        }
        # The helper patches SSJJReader and all key/mouse senders with test doubles.
        case = PlaybackTests()
        events = case.run_recording(action, speed=speed)
        jump = next(e for e in events if e[0] == 'send_key' and e[1] == (32, True))
        position = [round(v, 2) for v in jump[3]]
        assert abs(position[0] - 300) <= speed * .06 + 2, position
        results.append({'speed': speed, 'jump_position': position})
    return results


def blocked_straight_leg():
    sim = Simulation()
    wait = sim.wait

    def wall(ms=0):
        wait(ms)
        # A finite obstacle can be passed by moving beyond |y|=100 first.
        if abs(sim.position[1]) < 100:
            sim.position[0] = min(sim.position[0], 200)

    sim.wait = wall
    try:
        sim.run([(0, 0, 0), (600, 0, 0)])
    except RuntimeError as exc:
        error = str(exc)
    else:
        raise AssertionError('Expected blocked straight navigation to stop')
    assert '无进展' in error, error
    assert sim.position == [200, 0, 0], sim.position
    assert sim.maximum_y == 0
    assert not sim.forward
    return {'final_position': sim.position, 'maximum_y': sim.maximum_y,
            'forward_released': not sim.forward, 'error': error}


if __name__ == '__main__':
    print(json.dumps({'timed_jump': timed_jump(), 'blocked_straight_leg': blocked_straight_leg()},
                     ensure_ascii=True, indent=2))
