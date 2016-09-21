from pkgutil import get_data
import logging

from . import config
from . import util
from .repository import Repository
from .mock_io import MockWorld

logging.basicConfig(level=logging.DEBUG)
log = logging.getLogger(__name__)


def test_walk():
    build_contents = lambda pkg_name: get_data('packages', pkg_name + '/BUILD')
    files = {
        'cloudabi-ports': {
            'packages': {
                'c-runtime': {
                    'BUILD': build_contents('c-runtime')
                }
            }
        }
    }
    io = MockWorld(files)
    actual = [(1, len(d), len(f))
              for (_, d, f)
              in list(util.walk(io.cwd() / 'packages'))]
    assert actual == [(1, 1, 0), (1, 0, 1)]


def test_get_target_packages_none() -> None:
    io = MockWorld(dict())

    target_packages = get_target_packages(io.cwd(), io.io_d(), io.io_b())

    assert len(target_packages) == len(config.ARCHITECTURES)

    an_arch = next(iter(config.ARCHITECTURES))
    assert ('everything', an_arch) in target_packages
    assert str(target_packages[('everything', an_arch)]) == (an_arch + '-everything 1.0')


def test_get_target_packages_one() -> None:
    build_contents = lambda pkg_name: get_data('packages', pkg_name + '/BUILD')
    files = {
        'cloudabi-ports': {
            'packages': {
                'c-runtime': {
                    'BUILD': build_contents('c-runtime')
                }
            }
        }
    }
    io = MockWorld(files)

    target_packages = get_target_packages(io.cwd(), io.io_d(), io.io_b())

    assert len(target_packages) == 2 * len(config.ARCHITECTURES)
    an_arch = next(iter(config.ARCHITECTURES))
    assert ('c-runtime', an_arch) in target_packages


def get_target_packages(cwd, io_d, io_b):  # TODO: move this to build_packages.py
    repo = Repository(cwd / '_obj/install', io_d, io_b)

    for file in util.walk_files(cwd / 'packages'):
        log.debug('walk file: %s .name = %s', file, file.name)
        if file.name == 'BUILD':
            log.debug('adding: %s', file)
            repo.add_build_file(file, cwd / '_obj/distfiles')
    return repo.get_target_packages()
