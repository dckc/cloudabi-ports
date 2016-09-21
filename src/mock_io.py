from io import BytesIO, StringIO
from pathlib import PurePosixPath
from typing import Any, AnyStr, BinaryIO, Dict, Iterator, List, Optional, Tuple
import logging

from .distfile import Distfile, RandomT
from .builder import Access as BuildAccess
from . import util

log = logging.getLogger(__name__)


def mock_random() -> RandomT:
    from random import Random
    return Random(1)


class _Writing(BytesIO):
    def __init__(self, finalize):
        BytesIO.__init__(self)
        self._finalize = finalize

    def __del__(self):
        self._finalize(self.getvalue())


class MockWorld(object):
    class Path(util.PathExt):
        State = Dict[PurePosixPath, Optional[bytes]]
        _flavour = None  # type: object

        # Override Path.__new__()
        def __new__(cls, _s, _p):
            self = object.__new__(cls)
            return self

        def __init__(self, state: State, ppp: PurePosixPath) -> None:
            self._ppp = ppp
            self._state = state

        def __repr__(self):
            return 'MockPath(%s)' % self._ppp

        def __str__(self):
            return str(self._ppp)

        @property
        def name(self):
            return self._ppp.name

        def joinpath(self, *other):
            return MockWorld.Path(self._state, self._ppp.joinpath(*other))

        def __truediv__(self, *other):
            return self.joinpath(*other)

        def exists(self):
            log.debug('%s exists()?', self)
            return self._ppp in self._state

        def stat(self):
            raise NotImplementedError

        def lstat(self):
            raise NotImplementedError

        def is_symlink(self):
            return False  # TODO: mock symlinks

        def is_file(self):
            out = isinstance(self._state.get(self._ppp, 0), bytes)
            log.debug('%s is_file()? %s', self, out)
            return out

        def open(self, mode='r'):
            if self.is_dir() or not self.exists():
                raise IOError

            if 'w' in mode:
                if 'b' not in mode:
                    raise NotImplementedError(mode)
                def done(content):
                    self._state[self._ppp] = content
                return _Writing(done)
            else:
                content = self._state[self._ppp]
                return (BytesIO(content) if 'b' in mode
                        else StringIO(str(content, encoding='utf-8')))

        def is_dir(self):
            v = self._state.get(self._ppp, 0)
            log.debug('%s is_dir()? %.10s is None?', self, v)
            return v is None

        def iterdir(self):
            for k in self._state:
                if self._ppp / k.name == k:
                    yield self / k.name

    def __init__(self, fs_data: Dict[str, Any]) -> None:
        def unwrap(parent, data) -> Iterator[Tuple[PurePosixPath, Optional[bytes]]]:
            for k, v in data.items():
                child = parent / k
                if isinstance(v, bytes):
                    yield child, v
                else:
                    yield child, None
                    if isinstance(data, dict):
                        yield from unwrap(child, v)
        proot = PurePosixPath('/')
        init_fs = dict(unwrap(proot, fs_data))
        root = MockWorld.Path(init_fs, proot)
        self._cwd =  root / 'cloudabi-ports'
        self._environ = {}  # type: Dict[str, str]

    def io_d(self) -> Distfile.Access:
        return Distfile.Access(self.urlopen, self.subprocess, mock_random())

    def io_b(self) -> BuildAccess:
        return BuildAccess(self.subprocess, self.chdir, self.getenv)

    def cwd(self) -> util.PathExt:
        return self._cwd

    def getenv(self, key: str) -> str:
        return self._environ[key]

    def chdir(self, path: util.PathExt) -> None:
        self._cwd = path

    def urlopen(self) -> BinaryIO:
        raise NotImplementedError


    class RC(util.RunCommand):
        pass

    subprocess = RC()
