import runpy

import pytest


def test_session_start_never_reaches_provider_without_approval(monkeypatch):
    main = runpy.run_path('scripts/approved_gpu_session.py')['main']

    def forbidden(*args):
        pytest.fail('provider or credential access before approval validation')

    monkeypatch.setitem(main.__globals__, 'load_connection', forbidden)
    monkeypatch.setattr('sys.argv', ['session', 'start', '--max-seconds', '5400'])
    with pytest.raises(ValueError, match='explicit approval'):
        main()


def test_explicit_session_duration_is_bounded_before_provider_access(monkeypatch):
    main = runpy.run_path('scripts/approved_gpu_session.py')['main']

    def stop_before_network(*args):
        raise RuntimeError('duration validated; no network access')

    monkeypatch.setitem(main.__globals__, 'load_connection', stop_before_network)
    for seconds in (1200, 5400):
        monkeypatch.setattr('sys.argv', ['session', 'start', '--approve-paid-start-and-stop',
                                        '--max-seconds', str(seconds)])
        with pytest.raises(RuntimeError, match='duration validated'):
            main()
    monkeypatch.setattr('sys.argv', ['session', 'start', '--approve-paid-start-and-stop',
                                    '--max-seconds', '5401'])
    with pytest.raises(ValueError, match='5400'):
        main()
