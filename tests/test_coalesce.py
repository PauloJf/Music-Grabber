from fake_servers import FakeTimers

from music_grabber.coalesce import Coalescer


def _co(ft, fires, **kw):
    opts = dict(debounce=10, max_wait=60, min_interval=30)
    opts.update(kw)
    return Coalescer(lambda: fires.append(ft.now), clock=ft.clock, timer_factory=ft.factory, **opts)


def test_a_burst_fires_once_after_the_quiet_period():
    ft, fires = FakeTimers(), []
    co = _co(ft, fires)
    for _ in range(5):
        co.notify()
        ft.advance(3)                       # tracks keep arriving every 3 s
    assert fires == []                      # 3 s of quiet so far
    ft.advance(6)
    assert fires == []                      # 9 s < the 10 s debounce
    ft.advance(1.5)
    assert len(fires) == 1 and not co.pending


def test_max_wait_forces_a_fire_during_an_endless_stream():
    ft, fires = FakeTimers(), []
    co = _co(ft, fires, debounce=10, max_wait=25, min_interval=0)
    for _ in range(40):                     # a notification every 5 s, forever
        co.notify()
        ft.advance(5)
    assert len(fires) >= 2                  # fired at the cap instead of never
    assert fires[0] - 1000.0 <= 25.5


def test_min_interval_spaces_fires_and_keeps_one_follow_up():
    ft, fires = FakeTimers(), []
    co = _co(ft, fires, debounce=2, max_wait=60, min_interval=30)
    co.notify()
    ft.advance(3)
    assert len(fires) == 1
    first = fires[0]
    co.notify()                             # dirty: arrives right after a fire
    co.notify()
    ft.advance(5)
    assert len(fires) == 1                  # held back by the minimum interval
    ft.advance(40)
    assert len(fires) == 2 and fires[1] - first >= 30   # exactly one follow-up


def test_no_notification_means_no_fire():
    ft, fires = FakeTimers(), []
    _co(ft, fires)
    ft.advance(1000)
    assert fires == [] and ft.armed == []


def test_fire_can_ask_for_a_retry_and_keeps_the_pending_flag():
    ft, calls = FakeTimers(), []

    def fire():
        calls.append(ft.now)
        return 100.0 if len(calls) == 1 else None

    co = Coalescer(fire, debounce=1, max_wait=10, min_interval=0, clock=ft.clock,
                   timer_factory=ft.factory)
    co.notify()
    ft.advance(2)
    assert len(calls) == 1 and co.pending
    ft.advance(50)
    assert len(calls) == 1                  # backing off
    ft.advance(60)
    assert len(calls) == 2 and not co.pending


def test_a_failing_fire_is_swallowed():
    ft = FakeTimers()

    def boom():
        raise RuntimeError("down")

    co = Coalescer(boom, debounce=1, max_wait=5, min_interval=0, clock=ft.clock, timer_factory=ft.factory)
    co.notify()
    ft.advance(2)                           # must not raise
    assert not co.pending


def test_flush_and_close():
    ft, fires = FakeTimers(), []
    co = _co(ft, fires)
    co.notify()
    co.flush()
    assert len(fires) == 1
    co.notify()
    co.close()
    ft.advance(500)
    assert len(fires) == 1                  # closed: nothing more fires
    co.notify()
    assert ft.armed == []
