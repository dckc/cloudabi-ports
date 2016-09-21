# Copyright (c) 2015 Nuxi, https://nuxi.nl/
#
# This file is distributed under a 2-clause BSD license.
# See the LICENSE file for details.

from random import Random as RandomT
from typing import Any, Callable, Dict, List, IO, NamedTuple, Optional, cast
import logging

from . import config
from . import util
from .distfile import Distfile
from .util import PathExt
from .version import AnyVersion

log = logging.getLogger(__name__)


class DiffCreator:

    def __init__(self, source_directory: PathExt,
                 build_directory: 'BuildDirectory',
                 filename: PathExt, subprocess: util.RunCommand) -> None:
        self._source_directory = source_directory
        self._build_directory = build_directory
        self._filename = filename
        self._subprocess = subprocess

    def __enter__(self) -> None:
        # Create a backup of the source directory.
        self._backup_directory = self._build_directory.get_new_directory()
        for source_file, backup_file in util.walk_files_concurrently(
            self._source_directory, self._backup_directory):
            util.make_parent_dir(backup_file)
            util.copy_file(source_file, backup_file, False)

    def __exit__(self, type, value, traceback) -> None:
        # Create a diff to store the changes that were made to the original.
        util.diff(self._backup_directory, self._source_directory,
                  self._filename, self._subprocess)


Access = NamedTuple('B_Access', [
    ('subprocess', util.RunCommand),
    ('chdir', Callable[[PathExt], None]),
    ('getenv', Callable[[str], str])])


class FileHandle:

    def __init__(self, builder: 'Builder', path: PathExt, io: Access) -> None:
        self._builder = builder
        self._path = path
        self._io = io

    def __str__(self):
        return self._path

    def gnu_configure(self, args=[], inplace=False):
        for path in util.walk_files(self._path):
            filename = path.name
            if filename in {'config.guess', 'config.sub'}:
                # Replace the config.guess and config.sub files by
                # up-to-date copies. The copies provided by the tarball
                # rarely support CloudABI.
                path.unlink()
                path.open('wb').write(config.RESOURCES[filename])
            elif filename == 'ltmain.sh':
                # Patch up libtool to archive object files in sorted
                # order. This has been fixed in the meantime.
                with path.open('r') as fin, (path + '.new').open('w') as fout:
                    for l in fin.readlines():
                        # Add sort to the pipeline.
                        fout.write(l.replace(
                            '-print | $NL2SP', '-print | sort | $NL2SP'))
                path.copymode(path + '.new')
                (path + '.new').rename(path)
            elif filename == 'configure':
                # Patch up configure scripts to remove constructs that are known
                # to fail, for example due to functions being missing.
                with path.open('rb') as fin, (path + '.new').open('wb') as fout:
                    for l in fin.readlines():
                        # Bad C99 features test.
                        if l.startswith(b'#define showlist(...)'):
                            l = b'#define showlist(...) fputs (stderr, #__VA_ARGS__)\n'
                        elif l.startswith(b'#define report(test,...)'):
                            l = b'#define report(...) fprintf (stderr, __VA_ARGS__)\n'
                        fout.write(l)
                path.copymode(path + '.new')
                (path + '.new').rename(path)

        # Run the configure script in a separate directory.
        builddir = (self._path
                    if inplace
                    else self._builder._build_directory.get_new_directory())
        self._builder.gnu_configure(
            builddir, self._path / 'configure', args)
        return FileHandle(self._builder, builddir, self._io)

    def compile(self, args=[]):
        subprocess = self._io.subprocess
        output = self._path + '.o'
        self._io.chdir(self._path.parent)
        ext = self._path.suffix
        if ext in {'.c', '.S'}:
            log.info('CC %s', self._path)
            subprocess.check_call(
                [self._builder.get_cc()] + self._builder.get_cflags() +
                args + ['-c', '-o', output, self._path])
        elif ext == '.cpp':
            log.info('CXX %s', self._path)
            subprocess.check_call(
                [self._builder.get_cxx()] + self._builder.get_cxxflags() +
                args + ['-c', '-o', output, self._path])
        else:
            raise Exception('Unknown file extension: %s' % ext)
        return FileHandle(self._builder, output, self._io)

    def debug_shell(self) -> None:
        os = self._io
        self.run([
            'HOME=' + os.getenv('HOME'),
            'LC_CTYPE=' + os.getenv('LC_CTYPE'),
            'TERM=' + os.getenv('TERM'),
            'sh',
        ])

    def diff(self, filename: PathExt) -> DiffCreator:
        return DiffCreator(self._path, self._builder._build_directory, filename,
                           self._io.subprocess)

    def host(self) -> 'FileHandle':
        builder = cast(TargetBuilder, self._builder)
        return FileHandle(builder._host_builder, self._path, self._io)

    def rename(self, dst: 'FileHandle') -> None:
        self._path.rename(dst._path)

    def cmake(self, args: List[str]=[]) -> 'FileHandle':
        builddir = self._builder._build_directory.get_new_directory()
        self._builder.cmake(builddir, self._path, args)
        return FileHandle(self._builder, builddir, self._io)

    def install(self, path='.'):
        self._builder.install(self._path, path)

    def make(self, args=['all']):
        self.run(['make', '-j6'] + args)

    def make_install(self, args=['install']):
        stagedir = self._builder._build_directory.get_new_directory()
        self.run(['make', 'DESTDIR=' + stagedir] + args)
        return FileHandle(
            self._builder,
            stagedir.pathjoin(self._builder.get_prefix()[1:]),
            self._io)

    def ninja(self):
        self.run(['ninja'])

    def ninja_install(self):
        stagedir = self._builder._build_directory.get_new_directory()
        self.run(['DESTDIR=' + stagedir, 'ninja', 'install'])
        return FileHandle(
            self._builder,
            stagedir.pathjoin(self._builder.get_prefix()[1:]),
            self._io)

    def open(self, mode: str) -> IO[Any]:
        return self._path.open(mode)

    def path(self, path: str) -> 'FileHandle':
        return FileHandle(self._builder, self._path / path, self._io)

    def remove(self) -> None:
        util.remove(self._path)

    def run(self, command: List[str]) -> None:
        self._builder.run(self._path, command)

    def symlink(self, contents: str) -> None:
        util.remove(self._path)
        self._path.symlink_to(contents)

    def unhardcode_paths(self):
        self._builder.unhardcode_paths(self._path)


class BuildHandle:

    def __init__(self, builder: 'Builder', name: str, version: AnyVersion,
                 distfiles: Dict[str, Distfile],
                 io: Access) -> None:
        self._builder = builder
        self._name = name
        self._version = version
        self._distfiles = distfiles
        self._io = io

    def archive(self, objects):
        return FileHandle(self._builder,
                          self._builder.archive(obj._path for obj in objects),
                          self._io)

    def cc(self) -> str:
        return self._builder.get_cc()

    def cflags(self) -> str:
        return ' '.join(self._builder.get_cflags())

    def cpu(self) -> str:
        return self._builder.get_cpu()

    def cxx(self) -> str:
        return self._builder.get_cxx()

    def cxxflags(self) -> str:
        return ' '.join(self._builder.get_cxxflags())

    @staticmethod
    def endian() -> str:
        # TODO(ed): Extend this once we support big endian CPUs as well.
        return 'little'

    def executable(self, objects: List[FileHandle]) -> FileHandle:
        objs = sorted(str(obj._path) for obj in objects)
        output = self._builder._build_directory.get_new_executable()
        log.info('LD %s', output)
        subprocess = self._io.subprocess
        subprocess.check_call([self._builder.get_cc(), '-o', str(output)] + objs)
        return FileHandle(self._builder, output, self._io)

    def extract(self, name='%(name)s-%(version)s'):
        return FileHandle(
            self._builder,
            self._distfiles[
                name % {'name': self._name, 'version': self._version}
            ].extract(self._builder._build_directory.get_new_directory()),
            self._io
        )

    def gnu_triple(self) -> str:
        return self._builder.get_gnu_triple()

    def host(self):
        return BuildHandle(
            self._builder._host_builder, self._name, self._version,
            self._distfiles, self._io)

    def localbase(self) -> str:
        return self._builder.get_localbase()

    def prefix(self) -> PathExt:
        return self._builder.get_prefix()

    def resource(self, name: str) -> FileHandle:
        root = self._builder._platform(config.DIR_BUILDROOT)
        target = root / 'build' / name
        util.make_parent_dir(target)
        target.open('wb').write(config.RESOURCES[name])
        return FileHandle(self._builder, target, self._io)

    @staticmethod
    def stack_direction():
        # TODO(ed): Don't hardcode this.
        return 'down'


class BuildDirectory:

    def __init__(self, platform: Callable[[object], PathExt]) -> None:
        self._sequence_number = 0
        self._builddir = platform(config.DIR_BUILDROOT) / 'build'

    def get_new_archive(self) -> PathExt:
        path = self._builddir.pathjoin('lib%d.a' % self._sequence_number)
        util.make_parent_dir(path)
        self._sequence_number += 1
        return path

    def get_new_directory(self) -> PathExt:
        path = self._builddir.pathjoin(str(self._sequence_number))
        util.make_dir(path)
        self._sequence_number += 1
        return path

    def get_new_executable(self) -> PathExt:
        path = self._builddir.pathjoin('bin%d' % self._sequence_number)
        util.make_parent_dir(path)
        self._sequence_number += 1
        return path


class Builder:
    def __init__(self, build_directory: BuildDirectory,
                 install_directory: Optional[PathExt], io: Access) -> None:
        self._build_directory = build_directory
        self._install_directory = install_directory
        self._platform = install_directory.platform()
        self._io = io

    def cmake(self, builddir: PathExt, srcdir: PathExt,
              args: List[str]=[]) -> FileHandle: pass

    def run(self, cwd: PathExt, command: List[str]) -> None: pass

    def get_cc(self) -> str: pass

    def get_cflags(self) -> List[str]: pass

    def get_cpu(self) -> str: pass

    def get_cxx(self) -> str: pass

    def get_cxxflags(self) -> List[str]: pass

    def get_gnu_triple(self) -> str: pass

    def get_localbase(self) -> str: pass

    def get_prefix(self) -> PathExt: pass


class HostBuilder(Builder):

    def __init__(self, build_directory: BuildDirectory,
                 install_directory: Optional[PathExt],
                 io: Access) -> None:
        Builder.__init__(self, build_directory, install_directory, io)

        self._cflags = [
            '-O2', '-I' + str(self.get_prefix().pathjoin('include')),
        ]

    def gnu_configure(self, builddir, script, args):
        self.run(builddir, [script, '--prefix=' + self.get_prefix()] + args)

    def cmake(self, builddir, sourcedir, args):
        self.run(builddir, [
            'cmake', sourcedir, '-G', 'Ninja', '-DCMAKE_BUILD_TYPE=Release',
            '-DCMAKE_INSTALL_PREFIX=' + self.get_prefix()] + args)

    def get_cc(self) -> str:
        return str(self._platform(config.HOST_CC))

    def get_cflags(self) -> List[str]:
        return self._cflags

    def get_cxx(self) -> str:
        return str(self._platform(config.HOST_CXX))

    def get_gnu_triple(self):
        # Run config.guess to determine the GNU triple of the system
        # we're running on.
        config_guess = config.DIR_RESOURCES / 'config.guess'
        subprocess = self._io.subprocess
        triple = subprocess.check_output(config_guess)
        return str(triple, encoding='ASCII').strip()

    def get_prefix(self) -> PathExt:
        return config.DIR_BUILDROOT

    def install(self, source: PathExt, target: PathExt) -> None:
        log.info('INSTALL %s->%s', source, target)
        target = self._install_directory / target
        for source_file, target_file in util.walk_files_concurrently(
                source, target):
            # As these are bootstrapping tools, there is no need to
            # preserve any documentation and locales.
            path = str(target_file.relative_to(target))
            if (path != 'lib/charset.alias' and
                not path.startswith('share/doc/') and
                not path.startswith('share/info/') and
                not path.startswith('share/locale/') and
                not path.startswith('share/man/')):
                util.make_parent_dir(target_file)
                util.copy_file(source_file, target_file, False)

    def run(self, cwd: PathExt, command: List[str]) -> None:
        os = self._io
        os.chdir(cwd)
        subprocess = self._io.subprocess
        subprocess.check_call([
            'env',
            'CC=' + str(self.get_cc()),
            'CXX=' + str(self.get_cxx()),
            'CFLAGS=' + ' '.join(self._cflags),
            'CXXFLAGS=' + ' '.join(self._cflags),
            'LDFLAGS=-L' + str(self.get_prefix().pathjoin('lib')),
            'PATH=%s:%s' % (self.get_prefix().pathjoin('bin'),
                            os.getenv('PATH')),
        ] + command)


class TargetBuilder(Builder):

    def __init__(self, build_directory: BuildDirectory,
                 install_directory: PathExt, arch: str,
                 io: Access) -> None:
        Builder.__init__(self, build_directory, install_directory, io)
        self._arch = arch

        # Pick a random prefix directory. That way the build will fail
        # due to nondeterminism in case our piece of software hardcodes
        # the prefix directory.
        platform = install_directory.platform()
        self._prefix = platform(config.RANDOM)

        self._bindir = platform(config.DIR_BUILDROOT) / 'bin'
        self._localbase = platform(config.DIR_BUILDROOT) / self._arch
        self._cflags = [
            '-O2', '-Werror=implicit-function-declaration', '-Werror=date-time',
        ]

        # In case we need to build software for the host system.
        self._host_builder = HostBuilder(build_directory, None, self._io)

    def _tool(self, name) -> str:
        return str(self._bindir.pathjoin('%s-%s' % (self._arch, name)))

    def archive(self, object_files):
        subprocess = self._io.subprocess
        objs = sorted(object_files)
        output = self._build_directory.get_new_archive()
        log.info('AR %s', output)
        subprocess.check_call([self._tool('ar'), 'rcs', output] + objs)
        return output

    def gnu_configure(self, builddir, script, args):
        self.run(builddir, [script, '--host=' + self._arch,
                            '--prefix=' + self.get_prefix()] + args)

    def cmake(self, builddir, sourcedir, args):
        self.run(builddir, [
            'cmake', sourcedir, '-G', 'Ninja',
            '-DCMAKE_AR=' + self._tool('ar'),
            '-DCMAKE_BUILD_TYPE=Release',
            '-DCMAKE_FIND_ROOT_PATH=' + self._localbase,
            '-DCMAKE_FIND_ROOT_PATH_MODE_INCLUDE=ONLY',
            '-DCMAKE_FIND_ROOT_PATH_MODE_LIBRARY=ONLY',
            '-DCMAKE_FIND_ROOT_PATH_MODE_PROGRAM=NEVER',
            '-DCMAKE_INSTALL_PREFIX=' + self.get_prefix(),
            '-DCMAKE_PREFIX_PATH=' + self._localbase,
            '-DCMAKE_RANLIB=' + self._tool('ranlib'),
            '-DCMAKE_SYSTEM_NAME=Generic',
            '-DCMAKE_SYSTEM_PROCESSOR=' + self._arch.split('-')[0],
            '-DUNIX=YES'] + args)

    def get_cc(self) -> str:
        return self._tool('cc')

    def get_cflags(self) -> List[str]:
        return self._cflags

    def get_cpu(self) -> str:
        return self._arch.split('-', 1)[0]

    def get_cxx(self) -> str:
        return self._tool('c++')

    def get_cxxflags(self) -> List[str]:
        return self._cflags

    def get_gnu_triple(self) -> str:
        return self._arch

    def get_localbase(self) -> str:
        return str(self._localbase)

    def get_prefix(self) -> PathExt:
        return self._prefix

    def _unhardcode(self, source: PathExt, target: PathExt) -> None:
        assert not source.is_symlink()
        with source.open('r') as f:
            contents = f.read()
        contents = (contents
                    .replace(self.get_prefix(), '%%PREFIX%%')
                    .replace(self._localbase, '%%PREFIX%%'))
        with target.open('w') as f:
            f.write(contents)

    def unhardcode_paths(self, path):
        self._unhardcode(path, path + '.template')
        path.copymode(path + '.template')
        path.unlink()

    def install(self, source: PathExt, target: PathExt) -> None:
        log.info('INSTALL %s->%s', source, target)
        target = self._install_directory / target
        for source_file, target_file in util.walk_files_concurrently(
                source, target):
            util.make_parent_dir(target_file)
            relpath = str(target_file.relative_to(self._install_directory))
            ext = source_file.suffix
            if ext in {'.la', '.pc'} and not source_file.is_symlink():
                # Remove references to the installation prefix and the
                # localbase directory from libtool archives and
                # pkg-config files.
                self._unhardcode(source_file, target_file + '.template')
            elif ext == '.pyc':
                # Don't install precompiled Python sources. These
                # contain metadata that is non-deterministic.
                pass
            elif relpath.startswith('share/man/') and ext != '.gz':
                # Compress manual pages.
                util.gzip_file(source_file, target_file + '.gz')
            elif relpath == 'share/info/dir':
                # Don't install the GNU Info directory, as it should be
                # regenerated based on the set of packages installed.
                pass
            else:
                # Copy other files literally.
                util.copy_file(source_file, target_file, False)

    def run(self, cwd, command):
        self._io.chdir(cwd)
        subprocess = self._io.subprocess
        subprocess.check_call([
            'env', '-i',
            'AR=' + self._tool('ar'),
            'CC=' + self._tool('cc'),
            'CC_FOR_BUILD=' + self._host_builder.get_cc(),
            'CFLAGS=' + ' '.join(self._cflags),
            'CXX=' + self._tool('c++'),
            'CXXFLAGS=' + ' '.join(self._cflags),
            'CXX_FOR_BUILD=' + self._host_builder.get_cxx(),
            'NM=' + self._tool('nm'),
            'OBJDUMP=' + self._tool('objdump'),
             # List tools directory twice, as certain tools and scripts
             # get confused if PATH contains no colon.
            'PATH=%s:%s' % (self._bindir, self._bindir),
            'PERL=' + str(self._platform(config.PERL)),
            'PKG_CONFIG=' + self._tool('pkg-config'),
            'RANLIB=' + self._tool('ranlib'),
            'STRIP=' + self._tool('strip'),
        ] + command)
