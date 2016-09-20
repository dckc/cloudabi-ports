# Copyright (c) 2015 Nuxi, https://nuxi.nl/
#
# This file is distributed under a 2-clause BSD license.
# See the LICENSE file for details.

from typing import Callable, Dict, NamedTuple, Optional, Set, cast
import logging

from . import config
from . import util
from .builder import BuildDirectory, BuildHandle, HostBuilder, TargetBuilder
from .builder import Access
from .distfile import Distfile
from .util import PathExt
from .version import AnyVersion

log = logging.getLogger(__name__)


class HostPackage:

    def __init__(self, install_directory: PathExt,
                 io: Access,
                 name: str, version: AnyVersion,
                 homepage :str, maintainer: str,
                 build_depends: Set[HostPackage],
                 lib_depends: Set[HostPackage],
                 distfiles: Dict[str, Distfile],
                 build_cmd: Callable[[BuildHandle], None]) -> None:
        self._install_directory = install_directory
        self._io = io
        self._name = name
        self._version = version
        self._distfiles = distfiles
        self._build_cmd = build_cmd

        self._build_depends = set()  # type: Set[HostPackage]
        self._lib_depends = set()    # type: Set[HostPackage]

        # Compute the set of transitive build dependencies.
        for dep in build_depends:
            self._build_depends.add(dep)
            self._build_depends |= dep._lib_depends

        # Compute the set of transitive library dependencies.
        for dep in lib_depends:
            self._lib_depends.add(dep)
            self._lib_depends |= dep._lib_depends

    def _initialize_buildroot(self) -> None:
        # Ensure that all dependencies have been built.
        deps = self._build_depends | self._lib_depends
        for dep in deps:
            dep.build()

        # Install dependencies into an empty buildroot.
        platform = self._install_directory.platform()
        util.remove_and_make_dir(platform(config.DIR_BUILDROOT))
        for dep in deps:
            dep.extract()

    def build(self) -> None:
        # Skip this package if it has been built already.
        if self._install_directory.is_dir():
            return

        # Perform the build inside an empty buildroot.
        self._initialize_buildroot()
        log.info('BUILD %s', self._name)
        platform = self._install_directory.platform()
        self._build_cmd(
            BuildHandle(
                HostBuilder(BuildDirectory(platform), self._install_directory,
                            self._io),
                self._name, self._version, self._distfiles, self._io))

    def extract(self):
        # Copy files literally.
        platform = self._install_directory.platform()
        for source_file, target_file in util.walk_files_concurrently(
                self._install_directory, platform(config.DIR_BUILDROOT)):
            util.make_parent_dir(target_file)
            util.copy_file(source_file, target_file, False)


class TargetPackage:

    def __init__(self, install_directory: PathExt,
                 io: Access,
                 arch: str, name: str, version: AnyVersion, homepage: str,
                 maintainer: str,
                 host_packages: Dict[str, HostPackage],
                 lib_depends: Set[TargetPackage],
                 build_cmd: Optional[Callable[[BuildHandle], None]],
                 distfiles: Dict[str, Distfile]) -> None:
        self._install_directory = install_directory
        self._io = io
        self._arch = arch
        self._name = name
        self._version = version
        self._homepage = homepage
        self._maintainer = maintainer
        self._host_packages = host_packages
        self._build_cmd = build_cmd
        self._distfiles = distfiles

        # Compute the set of transitive library dependencies.
        self._lib_depends = set()  # type: Set[TargetPackage]
        for dep in lib_depends:
            if dep._build_cmd:
                self._lib_depends.add(dep)
            self._lib_depends |= dep._lib_depends

    def __str__(self):
        return '%s %s' % (self.get_freebsd_name(), self._version)

    def build(self) -> None:
        # Skip this package if it has been built already.
        if not self._build_cmd or self._install_directory.is_dir():
            return

        # Perform the build inside a buildroot with its dependencies
        # installed in place.
        self.initialize_buildroot({
            'autoconf', 'automake', 'bash', 'bison', 'cmake',
            'coreutils', 'diffutils', 'findutils', 'flex', 'gawk',
            'gettext', 'grep', 'help2man', 'libarchive', 'libtool',
            'llvm', 'm4', 'make', 'ninja', 'pkgconf', 'sed', 'texinfo',
        }, self._lib_depends)
        log.info('BUILD %s %s', self._name, self._arch)
        platform = self._install_directory.platform()
        self._build_cmd(
            BuildHandle(
                TargetBuilder(BuildDirectory(platform),
                              self._install_directory, self._arch, self._io),
                self._name, self._version, self._distfiles, self._io))

    def clean(self) -> None:
        util.remove(self._install_directory)

    def extract(self, path: PathExt, expandpath: str) -> None:
        for source_file, target_file in util.walk_files_concurrently(
                self._install_directory, path):
            util.make_parent_dir(target_file)
            if target_file.suffix == '.template':
                # File is a template. Expand %%PREFIX%% tags.
                target_file = target_file.with_name(target_file.name[:-9])
                with source_file.open('r') as f:
                    contents = f.read()
                contents = contents.replace('%%PREFIX%%', expandpath)
                with target_file.open('w') as f:
                    f.write(contents)
                source_file.copymode(target_file)
            else:
                # Regular file. Copy it over literally.
                util.copy_file(source_file, target_file, False)

    def get_arch(self):
        return self._arch

    def get_archlinux_name(self):
        return '%s-%s' % (self._arch, self._name)

    def get_cygwin_name(self):
        return '%s-%s' % (self._arch, self._name)

    def get_debian_name(self):
        return '%s-%s' % (self._arch.replace('_', '-'), self._name)

    def get_freebsd_name(self):
        return '%s-%s' % (self._arch, self._name)

    def get_homebrew_name(self):
        return '%s-%s' % (self._arch, self._name)

    def get_netbsd_name(self):
        return '%s-%s' % (self._arch, self._name)

    def get_openbsd_name(self):
        return '%s-%s' % (self._arch, self._name)

    def get_redhat_name(self):
        return '%s-%s' % (self._arch, self._name)

    def get_homepage(self):
        return self._homepage

    def get_lib_depends(self) -> Set[TargetPackage]:
        return self._lib_depends

    def get_maintainer(self):
        return self._maintainer

    def get_name(self):
        return self._name

    def get_version(self):
        return self._version

    def initialize_buildroot(self, host_depends: Set[str],
                             lib_depends: Set[TargetPackage]=set()) -> None:
        # Ensure that all dependencies have been built.
        host_deps = set()
        for dep_name in host_depends:
            package = self._host_packages[dep_name]
            host_deps.add(package)
            for depdep in package._lib_depends:
                host_deps.add(depdep)
        for dep in host_deps:
            dep.build()
        for ldep in lib_depends:
            ldep.build()

        # Install dependencies into an empty buildroot.
        platform = self._install_directory.platform()
        util.remove_and_make_dir(platform(config.DIR_BUILDROOT))
        for dep in host_deps:
            dep.extract()
        prefix = platform(config.DIR_BUILDROOT) / self._arch
        for ldep in lib_depends:
            ldep.extract(prefix, str(prefix))
