import builtins
import sys
from pathlib import Path
from types import SimpleNamespace

from hdrimg import cli


def test_jpeg_command_has_separate_options_and_dispatch(monkeypatch, tmp_path):
    captured = []
    monkeypatch.setattr(cli, 'run_jpeg_command', lambda args: captured.append(args) or 0)
    assert cli.main(['jpeg-hdr', 'photo.jpg', '--output', str(tmp_path / 'out.jpg'),
                     '--protect', 'regions.json', '--max-ev', '2']) == 0
    args = captured[0]
    assert args.input == Path('photo.jpg')
    assert args.protect == Path('regions.json')
    assert args.ai_size == 768 and args.max_ev == 2
    assert args.fp32 is False


def test_raw_help_never_imports_optional_ai(monkeypatch):
    real_import = builtins.__import__
    def guarded(name, *args, **kwargs):
        assert name.split('.')[0] not in {'torch', 'cv2', 'sam2', 'geffnet'}
        return real_import(name, *args, **kwargs)
    monkeypatch.setattr(builtins, '__import__', guarded)
    assert cli._parser().parse_args(['render', 'photo.RAF']).style == 'phone-clear'


def test_missing_optional_dependency_returns_actionable_exit(monkeypatch, capsys):
    real_import = builtins.__import__
    def missing(name, *args, **kwargs):
        if name == 'pipeline' and kwargs.get('level', args[3] if len(args) > 3 else 0) == 1:
            raise ModuleNotFoundError('No module named torch')
        return real_import(name, *args, **kwargs)
    monkeypatch.setattr(builtins, '__import__', missing)
    assert cli.main(['jpeg-hdr', 'photo.jpg', '--output', 'out.jpg']) == 3
    assert 'docs/jpeg-hdr.md' in capsys.readouterr().err
